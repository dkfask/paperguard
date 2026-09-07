"""写作项目 API: 基于模板创建、保存章节内容、导出 docx。"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

from app.api.templates import get_store
from app.auth import can_manage, current_user
from app.core.export.docx_exporter import export_project_to_docx
from app.db import Store, new_id
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import PaperTemplate
from app.services.image_cleanup import cleanup_orphan_images, remove_project_images
from app.services.project_validation import validate_project_completeness
from app.services.template_compare import compare_project_to_template
from app.services.projects import create_project_from_template
from app.services.upload_security import IMAGE_MAX_BYTES, validate_image

router = APIRouter(prefix="/api/projects")

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"


class CreateProjectRequest(BaseModel):
    template_id: str
    title: str = ""


@router.get("")
def list_projects(store: Store = Depends(get_store),
                  user: dict = Depends(current_user)) -> list[dict]:
    """项目列表: 管理员/教师可见全部, 学生仅见自己的。"""
    all_projects = store.list_projects()
    if not can_manage(user):
        all_projects = [p for p in all_projects if p.owner_id == user["id"]]
    return [
        {
            "id": p.id,
            "template_id": p.template_id,
            "template_name": p.template_name,
            "title": p.title,
            "updated_at": p.updated_at,
        }
        for p in all_projects
    ]


def _ensure_access(proj: WritingProject, user: dict) -> None:
    """管理员/教师可访问全部; 学生仅自己的项目。"""
    if can_manage(user):
        return
    if not proj.owner_id:
        raise HTTPException(status_code=403, detail="项目归属信息缺失, 请联系管理员迁移")
    if proj.owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该项目")


@router.get("/{proj_id}")
def get_project(proj_id: str, store: Store = Depends(get_store),
                user: dict = Depends(current_user)) -> WritingProject:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    return proj


@router.post("", status_code=201)
def create_project(req: CreateProjectRequest, store: Store = Depends(get_store),
                   user: dict = Depends(current_user)) -> WritingProject:
    """基于模板创建写作项目(实例化章节树), 归属当前用户。"""
    tpl = store.get_template(req.template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    proj = create_project_from_template(store, tpl)
    proj.owner_id = user["id"]
    if req.title:
        proj.title = req.title
    return store.save_project(proj)


class UpdateSectionRequest(BaseModel):
    title: str | None = None
    paragraphs: list[dict] | None = None   # [{id, text, bold, italic, underline}]
    base_revision: int | None = None


class SectionStructureItem(BaseModel):
    key: str
    type: str
    title: str
    required: bool = False


class UpdateSectionsStructureRequest(BaseModel):
    sections: list[SectionStructureItem]
    base_revision: int | None = None


@router.put("/{proj_id}/sections")
def update_project_sections_structure(proj_id: str, req: UpdateSectionsStructureRequest,
                                     store: Store = Depends(get_store),
                                     user: dict = Depends(current_user)) -> WritingProject:
    """更新项目的章节框架（增删、排序），由教师通过模板权限控制是否允许学生调整。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)

    # 权限检查：如果非教师/管理员，且该项目或模板不允许编辑框架，则拦截
    tpl = _project_template(proj, store)
    is_allowed = getattr(proj, "allow_structure_edit", None)
    if is_allowed is None:
        is_allowed = getattr(tpl, "allow_structure_edit", True)

    if not is_allowed and not can_manage(user):
        raise HTTPException(status_code=403, detail="指导教师已锁定论文框架，不可增删章节")

    expected_revision = proj.revision if req.base_revision is None else req.base_revision
    if req.base_revision is not None and req.base_revision != proj.revision:
        raise HTTPException(
            status_code=409,
            detail={"message": "项目内容已被更新, 请刷新后重试", "revision": proj.revision},
        )

    # 必填核心章节保护：cover/abstract/references 不能缺失
    incoming_keys = [s.key for s in req.sections]
    if len(incoming_keys) != len(set(incoming_keys)):
        raise HTTPException(status_code=422, detail="章节 key 存在重复")

    existing_sec_map = {s.key: s for s in proj.sections}
    new_sections: list[ProjectSection] = []

    for item in req.sections:
        if not item.key.strip():
            item.key = f"sec_{uuid.uuid4().hex[:8]}"
        if item.key in existing_sec_map:
            sec = existing_sec_map[item.key]
            sec.title = item.title
            sec.type = item.type
            sec.required = item.required
            new_sections.append(sec)
        else:
            # 新增章节
            new_sections.append(ProjectSection(
                key=item.key,
                type=item.type,
                title=item.title,
                required=item.required,
                paragraphs=[ParagraphBlock(id=f"{item.key}-p1", text="")],
            ))

    proj.sections = new_sections
    saved = store.update_project_if_revision(proj, expected_revision)
    if saved is None:
        raise HTTPException(status_code=409, detail={"message": "项目内容已被更新, 请刷新后重试"})
    return saved


@router.put("/{proj_id}/section/{section_key}")
def update_section(proj_id: str, section_key: str, req: UpdateSectionRequest,
                   store: Store = Depends(get_store),
                   user: dict = Depends(current_user)) -> WritingProject:
    """保存单个章节的内容，并以 revision 防止旧快照覆盖新内容。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    expected_revision = proj.revision if req.base_revision is None else req.base_revision
    if req.base_revision is not None and req.base_revision != proj.revision:
        raise HTTPException(
            status_code=409,
            detail={"message": "项目内容已被更新, 请刷新后重试", "revision": proj.revision},
        )
    for sec in proj.sections:
        if sec.key == section_key:
            if req.title is not None:
                sec.title = req.title
            if req.paragraphs is not None:
                parsed = [_parse_par(p, proj.id) for p in req.paragraphs]
                sec.paragraphs = parsed
            saved = store.update_project_if_revision(proj, expected_revision)
            if saved is None:
                raise HTTPException(
                    status_code=409,
                    detail={"message": "项目内容已被更新, 请刷新后重试"},
                )
            return saved
    raise HTTPException(status_code=404, detail="章节不存在")


def _parse_par(data: dict, project_id: str = ""):
    """把前端传来的内容块解析为 ParagraphBlock, 并限制图片 URL 属于当前项目。"""
    block_type = data.get("type", "paragraph")
    if block_type == "image":
        image = data.get("image", {})
        url = str(image.get("url", ""))
        filename = Path(url).name
        if url and (not url.startswith("/media/uploads/") or not filename.startswith(project_id + "-")):
            raise HTTPException(status_code=400, detail="图片不属于当前项目")
        return ParagraphBlock(
            id=str(data.get("id", "")),
            type="image",
            image={"url": url,
                   "caption": str(image.get("caption", ""))},
        )
    if block_type == "table":
        t = data.get("table", {})
        return ParagraphBlock(
            id=str(data.get("id", "")),
            type="table",
            table={
                "rows": int(t.get("rows", 2)),
                "cols": int(t.get("cols", 2)),
                "cells": t.get("cells", []),
                "caption": str(t.get("caption", "")),
            },
        )
    return ParagraphBlock(
        id=str(data.get("id", "")),
        text=str(data.get("text", "")),
        bold=bool(data.get("bold", False)),
        italic=bool(data.get("italic", False)),
        underline=bool(data.get("underline", False)),
    )


@router.post("/{proj_id}/images", status_code=201)
async def upload_image(proj_id: str, file: UploadFile = File(...),
                     user: dict = Depends(current_user)) -> dict:
    """上传论文图片, 返回静态资源 URL。"""
    store = get_store()
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    data = file.file.read(IMAGE_MAX_BYTES + 1)
    kind = validate_image(data, file.filename)
    ext = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}[kind]
    fname = f"{proj_id}-{uuid.uuid4().hex[:8]}{ext}"
    dest = _UPLOAD_DIR / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(data)
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="图片保存失败")
    return {"url": f"/media/uploads/{fname}"}


class UpdateMetaRequest(BaseModel):
    meta: dict[str, str]
    base_revision: int | None = None


@router.put("/{proj_id}/meta")
def update_meta(proj_id: str, req: UpdateMetaRequest,
                store: Store = Depends(get_store),
                user: dict = Depends(current_user)) -> WritingProject:
    """保存封面元信息，并以 revision 防止旧快照覆盖新内容。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    expected_revision = proj.revision if req.base_revision is None else req.base_revision
    if req.base_revision is not None and req.base_revision != proj.revision:
        raise HTTPException(
            status_code=409,
            detail={"message": "项目内容已被更新, 请刷新后重试", "revision": proj.revision},
        )
    proj.meta.update(req.meta)
    saved = store.update_project_if_revision(proj, expected_revision)
    if saved is None:
        raise HTTPException(
            status_code=409,
            detail={"message": "项目内容已被更新, 请刷新后重试"},
        )
    return saved


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    base_revision: int | None = None


@router.put("/{proj_id}")
def update_project(proj_id: str, req: UpdateProjectRequest,
                   store: Store = Depends(get_store),
                   user: dict = Depends(current_user)) -> WritingProject:
    """只允许更新项目标题, 不接受客户端篡改归属或模板绑定。"""
    old = store.get_project(proj_id)
    if old is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(old, user)
    expected_revision = old.revision if req.base_revision is None else req.base_revision
    if req.base_revision is not None and req.base_revision != old.revision:
        raise HTTPException(
            status_code=409,
            detail={"message": "项目内容已被更新, 请刷新后重试", "revision": old.revision},
        )
    if req.title is not None:
        old.title = req.title
    saved = store.update_project_if_revision(old, expected_revision)
    if saved is None:
        raise HTTPException(status_code=409, detail={"message": "项目内容已被更新"})
    return saved


@router.delete("/{proj_id}", status_code=204)
def delete_project(proj_id: str, store: Store = Depends(get_store),
                  user: dict = Depends(current_user)) -> None:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    # 删除数据库记录后按全库引用回收, 避免误删其他项目仍使用的图片。
    store.delete_project(proj_id)
    cleanup_orphan_images(store)



def _project_template(proj: WritingProject, store: Store) -> PaperTemplate:
    """优先使用项目创建时冻结的模板快照, 兼容旧项目回退到当前模板。"""
    if proj.template_snapshot:
        return PaperTemplate.model_validate(proj.template_snapshot)
    tpl = store.get_template(proj.template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="项目绑定的模板不存在, 请联系管理员迁移")
    proj.template_snapshot = tpl.model_dump(mode="json")
    store.save_project(proj)
    return tpl


@router.get("/{proj_id}/template-check")
def check_project_template(proj_id: str,
                           store: Store = Depends(get_store),
                           user: dict = Depends(current_user)) -> dict:
    """按项目绑定的模板快照检查写作内容。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = _project_template(proj, store)
    findings, context = compare_project_to_template(proj, tpl)
    from app.core.reporters.json_reporter import JSONReporter
    return JSONReporter()._build_payload(findings, {
        "mode": "project-template",
        "project_title": proj.title,
        "template_name": tpl.name,
        "template_section_count": context.section_count,
        "template_style_count": context.style_count,
    })


@router.get("/{proj_id}/check")
def check_project(proj_id: str, pack: str = "GB-T-7713-1-2006",
                  store: Store = Depends(get_store),
                  user: dict = Depends(current_user)) -> dict:
    """对写作项目实时跑格式规则, 返回问题列表(带章节定位)。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = _project_template(proj, store)
    from app.services.project_check import check_project as run_check
    return run_check(proj, tpl, pack=pack)


def _cleanup_export(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)


@router.post("/{proj_id}/export")
def export_docx(proj_id: str, force: bool = False,
                store: Store = Depends(get_store),
                user: dict = Depends(current_user)):
    """按模板导出为 .docx, 下载新文件。

    导出前校验必填章节与封面字段; 未完成时返回 409 与缺失清单,
    除非 force=true 跳过校验。
    """
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = _project_template(proj, store)

    if not force:
        missing = validate_project_completeness(proj, tpl)
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"message": "以下必填项未完成, 无法导出", "missing": missing},
            )

    out_dir = Path(tempfile.mkdtemp(prefix="paperguard-export-"))
    out = out_dir / "paper.docx"
    try:
        export_project_to_docx(proj, tpl, str(out))
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    safe_title = (proj.title or "paper").replace("/", "_").replace("\\", "_")
    # 中文文件名用 RFC 5987 filename* 编码, 避免 header 编码错误
    from urllib.parse import quote
    filename_ascii = "paper.docx"
    return StreamingResponse(
        out.open("rb"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename_ascii}"; filename*=UTF-8\'\'{quote(f"{safe_title}.docx")}'
        },
        background=BackgroundTask(_cleanup_export, out),
    )
