"""写作项目 API: 基于模板创建、保存章节内容、导出 docx。"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import datetime, timezone
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
from app.models.policy import (
    DisputeStatus,
    RequirementAcknowledgement,
    RequirementDispute,
    RequirementUpgrade,
    Waiver,
    WaiverStatus,
)
from app.models.template import PaperTemplate, SectionType
from app.services.image_cleanup import cleanup_orphan_images, remove_project_images
from app.services.project_validation import validate_project_completeness
from app.services.template_compare import compare_project_to_template
from app.services.projects import create_project_from_template
from app.services.policy_resolver import build_requirement_snapshot
from app.services.template_source import resolve_template_source
from app.services.upload_security import IMAGE_MAX_BYTES, validate_image

router = APIRouter(prefix="/api/projects")

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"


class CreateProjectRequest(BaseModel):
    template_id: str
    title: str = ""
    organization_context: dict[str, str] = {}
    stage: str = ""


def _project_writing_metrics(project: WritingProject) -> dict[str, object]:
    """Return lightweight writing progress for the manuscript list view."""
    sections = [section for section in project.sections if section.type not in ("cover", "toc")]

    def block_has_content(block) -> bool:
        block_type = getattr(block.type, "value", block.type)
        if block_type == "image":
            return bool(block.image and block.image.url)
        if block_type == "table":
            return bool(block.table and any(str(cell).strip() for row in block.table.cells for cell in row))
        return bool((block.text or "").strip())

    completed = [section for section in sections if any(block_has_content(block) for block in section.paragraphs)]
    total_words = sum(
        len((block.text or "").strip())
        for section in project.sections
        for block in section.paragraphs
        if getattr(block.type, "value", block.type) == "paragraph"
    )
    last_written = next((section.title for section in reversed(sections) if any(block_has_content(block) for block in section.paragraphs)), "")
    progress = round(len(completed) / len(sections) * 100) if sections else 0
    return {
        "section_count": len(sections),
        "completed_sections": len(completed),
        "total_words": total_words,
        "progress_pct": progress,
        "last_written_section": last_written,
    }


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
            **_project_writing_metrics(p),
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
    owner = user["id"]
    teacher_id = user.get("teacher_id")
    organization_context = dict(user.get("organization_context") or {})
    if can_manage(user):
        organization_context.update(req.organization_context)
    proj = create_project_from_template(
        store,
        tpl,
        owner_id=owner,
        teacher_id=teacher_id,
        organization_context=organization_context,
        stage=req.stage,
    )
    if req.title:
        proj.title = req.title
    return store.save_project(proj)


class UpdateSectionRequest(BaseModel):
    title: str | None = None
    paragraphs: list[dict] | None = None   # [{id, text, bold, italic, underline}]
    base_revision: int | None = None


class ProjectFixRequest(BaseModel):
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


def _ensure_requirement_snapshot(proj: WritingProject, tpl: PaperTemplate, store: Store) -> dict:
    """为旧项目补建规范快照；新项目始终在创建时生成。"""
    if proj.requirement_snapshot:
        return proj.requirement_snapshot
    owner = store.get_user(proj.owner_id) if proj.owner_id else None
    proj.requirement_snapshot = build_requirement_snapshot(
        store,
        tpl,
        teacher_id=owner.get("teacher_id") if owner else None,
        student_id=proj.owner_id,
        organization_context=proj.organization_context,
        project_id=proj.id,
        stage=proj.stage,
    )
    store.save_project(proj)
    return proj.requirement_snapshot


class SwitchTemplateRequest(BaseModel):
    template_id: str


def _flatten_template_nodes(nodes) -> list:
    out = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten_template_nodes(node.children))
    return out


def _rebind_template(proj: WritingProject, tpl: PaperTemplate) -> WritingProject:
    """把项目换成 tpl: 只换排版规范与封面定义。

    学生已有的章节结构、顺序、标题原样保留(写作界面看到的顺序就是导出顺序);
    新模板里有、项目里还没有的章节, 按模板中的位置插到相邻章节之后。
    封面与目录不建项目章节(封面来自 meta, 目录由导出自动生成)。
    """
    result = list(proj.sections)
    present = {s.key for s in result}
    flat = _flatten_template_nodes(tpl.sections)

    for index, node in enumerate(flat):
        if node.type in (SectionType.COVER, SectionType.TOC) or node.key in present:
            continue
        # 插到模板中该节点之前、最近一个已存在章节的后面
        insert_at = len(result)
        for previous in reversed(flat[:index]):
            position = next((i for i, s in enumerate(result) if s.key == previous.key), None)
            if position is not None:
                insert_at = position + 1
                break
        result.insert(insert_at, ProjectSection(
            key=node.key, type=node.type.value, title=node.title,
            required=node.required,
            paragraphs=[ParagraphBlock(id=f"{node.key}-p1", text="")],
        ))
        present.add(node.key)

    proj.sections = result
    proj.template_id = tpl.id
    proj.template_name = tpl.name
    proj.template_snapshot = tpl.model_dump(mode="json")
    # 补齐新模板的封面字段, 已填写的值保留
    for field in tpl.meta_fields:
        proj.meta.setdefault(field.key, "")
    return proj


@router.post("/{proj_id}/template")
def switch_project_template(proj_id: str, req: SwitchTemplateRequest,
                            store: Store = Depends(get_store),
                            user: dict = Depends(current_user)) -> WritingProject:
    """把项目换成指定模板(模板重新导入/修订后使用)。

    只换排版规范与封面字段定义, 已写内容按章节 key 保留。
    """
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="只有管理员/教师可以更换模板")
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = store.get_template(req.template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="目标模板不存在")
    _rebind_template(proj, tpl)
    owner = store.get_user(proj.owner_id) if proj.owner_id else None
    proj.requirement_snapshot = build_requirement_snapshot(
        store, tpl,
        teacher_id=owner.get("teacher_id") if owner else None,
        student_id=proj.owner_id,
        organization_context=proj.organization_context,
        project_id=proj.id,
        stage=proj.stage,
    )
    store.save_project(proj)
    return proj


@router.post("/{proj_id}/refresh-template")
def refresh_project_template(proj_id: str,
                             store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> WritingProject:
    """重新套用项目当前绑定的模板(等价于换成同一个模板)。"""
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="只有管理员/教师可以重新套用模板")
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = store.get_template(proj.template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="项目绑定的模板不存在")
    _rebind_template(proj, tpl)
    owner = store.get_user(proj.owner_id) if proj.owner_id else None
    proj.requirement_snapshot = build_requirement_snapshot(
        store, tpl,
        teacher_id=owner.get("teacher_id") if owner else None,
        student_id=proj.owner_id,
        organization_context=proj.organization_context,
        project_id=proj.id,
        stage=proj.stage,
    )
    store.save_project(proj)
    return proj


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
def check_project(proj_id: str, pack: str | None = None,
                  store: Store = Depends(get_store),
                  user: dict = Depends(current_user)) -> dict:
    """对写作项目实时跑格式规则, 返回问题列表(带章节定位)。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    from app.services.project_check import check_project as run_check
    return run_check(proj, tpl, pack=pack, store=store, performed_by=user["id"])


@router.post("/{proj_id}/fix")
def fix_project(proj_id: str, req: ProjectFixRequest,
                store: Store = Depends(get_store),
                user: dict = Depends(current_user)) -> dict:
    """在写作工作台修复检查结果中可自动处理的警告/错误并复检。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)

    expected_revision = proj.revision if req.base_revision is None else req.base_revision
    if req.base_revision is not None and req.base_revision != proj.revision:
        raise HTTPException(
            status_code=409,
            detail={"message": "项目内容已被更新, 请刷新后重试", "revision": proj.revision},
        )

    from app.services.project_check import apply_project_fixes, check_project as run_check
    report = run_check(proj, tpl, store=None, performed_by=user["id"])
    fixed_count, fixed_rule_ids = apply_project_fixes(proj, report, template=tpl)

    if fixed_count:
        # Formatting fixes can update the project-bound template rather than a
        # content block; persist both through the same revision-checked write.
        proj.template_snapshot = tpl.model_dump(mode="json")
        saved = store.update_project_if_revision(proj, expected_revision)
        if saved is None:
            raise HTTPException(
                status_code=409,
                detail={"message": "项目内容已被更新, 请刷新后重试"},
            )
        proj = saved
        report = run_check(proj, tpl, store=None, performed_by=user["id"])

    return {
        "project": proj.model_dump(mode="json"),
        "report": report,
        "fixed_count": fixed_count,
        "fixed_rule_ids": fixed_rule_ids,
    }


class WaiverRequest(BaseModel):
    reason: str
    effective_from: str = ""
    effective_to: str = ""


class WaiverDecisionRequest(BaseModel):
    status: WaiverStatus
    review_note: str = ""
    effective_from: str = ""
    effective_to: str = ""


class DisputeRequest(BaseModel):
    reason: str


class DisputeDecisionRequest(BaseModel):
    status: DisputeStatus
    review_note: str = ""


class RequirementUpgradeRequest(BaseModel):
    reason: str = ""
    expected_snapshot_hash: str = ""
    confirm: bool = False


class RequirementAcknowledgementRequest(BaseModel):
    snapshot_hash: str = ""


class RequirementAssistRequest(BaseModel):
    question: str = ""


def _current_requirement_snapshot(proj: WritingProject, tpl: PaperTemplate, store: Store) -> dict:
    owner = store.get_user(proj.owner_id) if proj.owner_id else None
    return build_requirement_snapshot(
        store,
        tpl,
        teacher_id=owner.get("teacher_id") if owner else None,
        student_id=proj.owner_id,
        organization_context=proj.organization_context,
        project_id=proj.id,
        stage=proj.stage,
    )


def _snapshot_diff(old: dict, new: dict) -> dict:
    old_sets = {item.get("id"): item for item in old.get("policy_sets", [])}
    new_sets = {item.get("id"): item for item in new.get("policy_sets", [])}
    added = [new_sets[key] for key in new_sets.keys() - old_sets.keys()]
    removed = [old_sets[key] for key in old_sets.keys() - new_sets.keys()]
    changed = [
        {"before": old_sets[key], "after": new_sets[key]}
        for key in old_sets.keys() & new_sets.keys()
        if old_sets[key].get("content_hash") != new_sets[key].get("content_hash")
    ]
    old_items = {item.get("id"): item for item in old.get("resolved_items", [])}
    new_items = {item.get("id"): item for item in new.get("resolved_items", [])}
    return {
        "changed": bool(added or removed or changed or old.get("content_hash") != new.get("content_hash")),
        "added_sets": added,
        "removed_sets": removed,
        "changed_sets": changed,
        "added_items": [new_items[key] for key in new_items.keys() - old_items.keys()],
        "removed_items": [old_items[key] for key in old_items.keys() - new_items.keys()],
    }


def _can_review_waiver(proj: WritingProject, user: dict, store: Store) -> bool:
    if user["role"] in ("admin", "college_admin"):
        return True
    owner = store.get_user(proj.owner_id) if proj.owner_id else None
    return user["role"] == "teacher" and owner is not None and owner.get("teacher_id") == user["id"]


def _ensure_requirement_item(proj: WritingProject, item_id: str) -> None:
    item_ids = {item.get("id") for item in (proj.requirement_snapshot or {}).get("resolved_items", [])}
    if item_id not in item_ids:
        raise HTTPException(status_code=404, detail="项目当前规范中不存在该要求")


@router.get("/{proj_id}/requirements/waivers")
def list_requirement_waivers(proj_id: str, store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> list[dict]:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    return proj.waivers


@router.post("/{proj_id}/requirements/{item_id}/waiver", status_code=201)
def request_requirement_waiver(proj_id: str, item_id: str, req: WaiverRequest,
                               store: Store = Depends(get_store),
                               user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    if not req.reason.strip():
        raise HTTPException(status_code=422, detail="申请豁免必须填写理由")
    _ensure_requirement_item(proj, item_id)
    pending = next((item for item in proj.waivers if item.get("requirement_item_id") == item_id and item.get("status") == "pending"), None)
    if pending:
        raise HTTPException(status_code=409, detail="该要求已有待审核的豁免申请")
    waiver = Waiver(
        id=new_id("waiver"),
        requirement_item_id=item_id,
        reason=req.reason.strip(),
        requested_by=user["id"],
        requested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        effective_from=req.effective_from,
        effective_to=req.effective_to,
    )
    proj.waivers.append(waiver.model_dump(mode="json"))
    store.save_project(proj)
    return waiver.model_dump(mode="json")


@router.post("/{proj_id}/requirements/waivers/{waiver_id}/decision")
def decide_requirement_waiver(proj_id: str, waiver_id: str, req: WaiverDecisionRequest,
                              store: Store = Depends(get_store),
                              user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    if not _can_review_waiver(proj, user, store):
        raise HTTPException(status_code=403, detail="只有指导教师或管理员可以审核豁免")
    waiver = next((item for item in proj.waivers if item.get("id") == waiver_id), None)
    if waiver is None:
        raise HTTPException(status_code=404, detail="豁免申请不存在")
    if waiver.get("status") != WaiverStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="该豁免申请已经处理")
    if req.status not in (WaiverStatus.APPROVED, WaiverStatus.REJECTED):
        raise HTTPException(status_code=422, detail="审核结果只能是 approved 或 rejected")
    waiver.update({
        "status": req.status.value,
        "reviewed_by": user["id"],
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_note": req.review_note,
        "effective_from": req.effective_from or waiver.get("effective_from", ""),
        "effective_to": req.effective_to or waiver.get("effective_to", ""),
    })
    store.save_project(proj)
    return waiver


@router.get("/{proj_id}/requirements/disputes")
def list_requirement_disputes(proj_id: str, store: Store = Depends(get_store),
                              user: dict = Depends(current_user)) -> list[dict]:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    return proj.requirement_disputes


@router.post("/{proj_id}/requirements/{item_id}/dispute", status_code=201)
def request_requirement_dispute(proj_id: str, item_id: str, req: DisputeRequest,
                                store: Store = Depends(get_store),
                                user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    if not req.reason.strip():
        raise HTTPException(status_code=422, detail="申诉必须填写理由")
    _ensure_requirement_item(proj, item_id)
    pending = next((item for item in proj.requirement_disputes
                    if item.get("requirement_item_id") == item_id and item.get("status") == DisputeStatus.PENDING.value), None)
    if pending:
        raise HTTPException(status_code=409, detail="该要求已有待处理的申诉")
    dispute = RequirementDispute(
        id=new_id("dispute"),
        requirement_item_id=item_id,
        reason=req.reason.strip(),
        requested_by=user["id"],
        requested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    proj.requirement_disputes.append(dispute.model_dump(mode="json"))
    store.save_project(proj)
    return dispute.model_dump(mode="json")


@router.post("/{proj_id}/requirements/disputes/{dispute_id}/decision")
def decide_requirement_dispute(proj_id: str, dispute_id: str, req: DisputeDecisionRequest,
                               store: Store = Depends(get_store),
                               user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    if not _can_review_waiver(proj, user, store):
        raise HTTPException(status_code=403, detail="只有指导教师或管理员可以审核申诉")
    dispute = next((item for item in proj.requirement_disputes if item.get("id") == dispute_id), None)
    if dispute is None:
        raise HTTPException(status_code=404, detail="申诉记录不存在")
    if dispute.get("status") != DisputeStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="该申诉已经处理")
    if req.status not in (DisputeStatus.ACCEPTED, DisputeStatus.REJECTED):
        raise HTTPException(status_code=422, detail="审核结果只能是 accepted 或 rejected")
    dispute.update({
        "status": req.status.value,
        "reviewed_by": user["id"],
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_note": req.review_note,
    })
    store.save_project(proj)
    return dispute


@router.post("/{proj_id}/requirements/acknowledge")
def acknowledge_requirements(proj_id: str, req: RequirementAcknowledgementRequest,
                             store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    current_hash = (proj.requirement_snapshot or {}).get("content_hash", "")
    if req.snapshot_hash and req.snapshot_hash != current_hash:
        raise HTTPException(status_code=409, detail="确认的规范快照不是项目当前版本，请刷新后重试")
    snapshot_hash = current_hash
    if not snapshot_hash:
        raise HTTPException(status_code=409, detail="项目尚未生成规范快照")
    item = RequirementAcknowledgement(
        id=new_id("ack"),
        user_id=user["id"],
        snapshot_hash=snapshot_hash,
        acknowledged_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    proj.requirement_acknowledgements = [
        value for value in proj.requirement_acknowledgements
        if value.get("user_id") != user["id"]
    ] + [item.model_dump(mode="json")]
    store.save_project(proj)
    return item.model_dump(mode="json")


@router.post("/{proj_id}/requirements/{item_id}/assist")
def assist_requirement_check(proj_id: str, item_id: str, req: RequirementAssistRequest,
                             store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> dict:
    """对 manual 要求提供非判定性的 AI 检查建议，不改变合规结果。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    _ensure_requirement_item(proj, item_id)
    item = next(item for item in proj.requirement_snapshot.get("resolved_items", []) if item.get("id") == item_id)
    if item.get("type") != "manual":
        raise HTTPException(status_code=422, detail="只有人工检查项可以请求 AI 辅助")
    text_parts = []
    for section in proj.sections:
        for paragraph in section.paragraphs:
            if paragraph.type.value == "paragraph" and paragraph.text.strip():
                text_parts.append(f"【{section.title}】\n{paragraph.text.strip()}")
    content = "\n\n".join(text_parts)
    if not content:
        raise HTTPException(status_code=422, detail="项目正文为空，无法辅助检查")
    from app.api.ai import _chat_completion, _resolve_cfg
    resolved = _resolve_cfg(store, user)
    if not resolved:
        raise HTTPException(status_code=503, detail="AI 功能未启用")
    cfg, _source = resolved
    question = req.question.strip() or item.get("description", "请按要求检查论文")
    messages = [
        {"role": "system", "content": "你是论文人工检查辅助工具。只给出基于提供文本的核对建议，不作最终合规判定，不编造事实。输出三部分：已观察到的证据、疑点或缺口、建议人工确认的问题。"},
        {"role": "user", "content": f"检查要求：{question}\n\n论文内容：\n{content[:11000]}"},
    ]
    suggestion = _chat_completion(cfg, messages, 0.2)
    return {
        "requirement_item_id": item_id,
        "suggestion": suggestion,
        "is_authoritative": False,
        "message": "AI 结果仅供人工参考，不会自动改变合规判定。",
    }


@router.get("/{proj_id}/requirements/upgrade-preview")
def preview_requirement_upgrade(proj_id: str, store: Store = Depends(get_store),
                                 user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    new_snapshot = _current_requirement_snapshot(proj, tpl, store)
    return {
        "from_snapshot_hash": proj.requirement_snapshot.get("content_hash", ""),
        "to_snapshot_hash": new_snapshot.get("content_hash", ""),
        "diff": _snapshot_diff(proj.requirement_snapshot, new_snapshot),
        "snapshot": new_snapshot,
    }


@router.post("/{proj_id}/requirements/upgrade")
def upgrade_requirements(proj_id: str, req: RequirementUpgradeRequest,
                         store: Store = Depends(get_store),
                         user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="只有教师或管理员可以升级项目规范")
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    if req.expected_snapshot_hash and req.expected_snapshot_hash != proj.requirement_snapshot.get("content_hash"):
        raise HTTPException(status_code=409, detail="项目规范已发生变化，请重新预览")
    new_snapshot = _current_requirement_snapshot(proj, tpl, store)
    diff = _snapshot_diff(proj.requirement_snapshot, new_snapshot)
    if not req.confirm:
        raise HTTPException(status_code=428, detail={"message": "请确认应用规范升级", "diff": diff})
    old_hash = proj.requirement_snapshot.get("content_hash", "")
    if diff["changed"]:
        upgrade = RequirementUpgrade(
            id=new_id("upgrade"),
            from_snapshot_hash=old_hash,
            to_snapshot_hash=new_snapshot.get("content_hash", ""),
            changed_policy_ids=[item.get("id", "") for item in diff["added_sets"]]
            + [item.get("id", "") for item in diff["removed_sets"]]
            + [item["after"].get("id", "") for item in diff["changed_sets"]],
            reason=req.reason,
            operated_by=user["id"],
            operated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        proj.requirement_upgrades.append(upgrade.model_dump(mode="json"))
        proj.requirement_snapshot = new_snapshot
        store.save_project(proj)
    return {"project": proj, "diff": diff}


@router.get("/{proj_id}/check-runs")
def list_project_check_runs(proj_id: str, store: Store = Depends(get_store),
                            user: dict = Depends(current_user)) -> list[dict]:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    return [item.model_dump(mode="json") for item in store.list_check_runs(proj_id)]


@router.get("/{proj_id}/check-runs/{run_id}")
def get_project_check_run(proj_id: str, run_id: str, store: Store = Depends(get_store),
                          user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    run = store.get_check_run(run_id)
    if run is None or run.project_id != proj_id:
        raise HTTPException(status_code=404, detail="检查记录不存在")
    return run.model_dump(mode="json")


@router.get("/{proj_id}/compliance-report")
def compliance_report(proj_id: str, store: Store = Depends(get_store),
                      user: dict = Depends(current_user)) -> dict:
    """生成绑定当前模板/规范快照的合规报告，供送审或归档使用。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    from app.services.project_check import check_project as run_check
    report = run_check(proj, tpl, pack=None, store=store, performed_by=user["id"])
    report["report_type"] = "compliance"
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report


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
    _ensure_requirement_snapshot(proj, tpl, store)

    if not force:
        missing = validate_project_completeness(proj, tpl)
        from app.services.project_check import check_project as run_check
        report = run_check(proj, tpl, pack=None, store=store, performed_by=user["id"])
        missing.extend(
            f"{finding.get('source', '规范要求')}: {finding['message']}"
            for finding in report.get("findings", [])
            if finding.get("source_type") == "requirement"
            and finding.get("enforce") == "hard"
            and finding.get("severity") == "error"
        )
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"message": "以下必填项未完成, 无法导出", "missing": missing},
            )

    out_dir = Path(tempfile.mkdtemp(prefix="paperguard-export-"))
    out = out_dir / "paper.docx"
    try:
        export_project_to_docx(proj, tpl, str(out),
                               template_path=resolve_template_source(tpl.source_docx))
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
