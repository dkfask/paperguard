"""写作项目 API: 基于模板创建、保存章节内容、导出 docx。"""

from __future__ import annotations

import shutil
import re
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from app.api.templates import get_store
from app.auth import can_manage, current_user
from app.core.export.docx_exporter import export_project_to_docx
from app.db import Store, new_id
from app.models.project import InlineTextRun, ParagraphBlock, ProjectSection, WritingProject
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
from app.services.project_versions import project_content_hash
from app.services.access_service import visible_organization_ids
from app.services.resource_access import assert_resource_read
from app.services.template_compare import compare_project_to_template
from app.services.projects import create_project_from_template
from app.services.policy_resolver import (
    apply_requirement_format_overrides,
    build_requirement_snapshot,
    hydrate_snapshot_format_overrides,
)
from app.services.template_source import resolve_template_source
from app.services.docx_project_import import import_docx_project, public_import_sections
from app.services.document_assets import document_upload_dir
from app.services.upload_security import IMPORT_MAX_BYTES, IMAGE_MAX_BYTES, read_limited, validate_docx_bytes, validate_image

router = APIRouter(prefix="/api/projects")

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
    """项目列表按角色和批次/指导关系收敛，不因管理角色泄露正文归属。"""
    all_projects = [p for p in store.list_projects() if not p.deleted_at and _project_accessible(p, user, store)]
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


def _visible_organization_ids(store: Store, user: dict) -> set[str]:
    return visible_organization_ids(store, user)


def _project_accessible(proj: WritingProject, user: dict, store: Store) -> bool:
    if user.get("role") == "admin":
        return True
    if user.get("role") == "student":
        return bool(proj.owner_id and proj.owner_id == user["id"])
    if user.get("role") == "teacher":
        if user.get("status") != "active":
            return False
        if proj.owner_id == user["id"] or getattr(proj, "teacher_id", "") == user["id"]:
            return True
        batch_id = (proj.organization_context or {}).get("batch_id")
        if not batch_id or not proj.owner_id:
            return False
        # Runtime import avoids coupling the project API import cycle to workflow.
        from app.api.workflow import _current_supervision
        relation = _current_supervision(store, batch_id, proj.owner_id)
        return bool(relation and relation.get("teacher_id") == user["id"])
    if user.get("role") == "college_admin":
        if user.get("status") != "active":
            return False
        batch_id = (proj.organization_context or {}).get("batch_id")
        if batch_id:
            cohort = store.get_workflow_record("cohort", batch_id)
            return bool(cohort and cohort.get("organization_id") in _visible_organization_ids(store, user))
        return bool(set((proj.organization_context or {}).values()) & _visible_organization_ids(store, user))
    return False


def _record_project_audit(store: Store, project: WritingProject, user: dict, action: str) -> None:
    """Keep trash transitions in the same audit stream as workflow operations."""
    batch_id = (project.organization_context or {}).get("batch_id", "")
    cohort = store.get_workflow_record("cohort", batch_id) if batch_id else None
    store.save_audit_event({
        "id": new_id("audit"), "actor_id": user["id"], "action": action,
        "resource_type": "project", "resource_id": project.id,
        "organization_id": (cohort or {}).get("organization_id", ""),
        "result": "success", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def _candidate_uses_waiver(candidate: dict, waiver_id: str) -> bool:
    return any(
        (finding.get("waiver") or {}).get("id") == waiver_id
        for finding in (candidate.get("check_report") or {}).get("waived", [])
    )


def _ensure_student_can_write(proj: WritingProject, user: dict, store: Store) -> None:
    if user.get("role") != "student":
        return
    participants = [
        item for item in store.list_workflow_records("participant")
        if item.get("project_id") == proj.id and item.get("student_id") == user.get("id")
    ]
    if not participants:
        return
    from app.api.workflow import _parse_time, _prerequisites_satisfied

    now = datetime.now(timezone.utc)
    for participant in participants:
        cohort = store.get_workflow_record("cohort", participant.get("cohort_id", ""))
        if cohort and cohort.get("status") == "closed":
            raise HTTPException(status_code=409, detail="批次已关闭，不能继续编辑论文")
        # 提交采用 project_version 快照；当前项目继续作为学生草稿，后续修改
        # 不会回写教师正在审核的不可变提交版本。
        if participant.get("state") in {"in_progress", "needs_revision", "submitted"}:
            return
        if participant.get("state") != "not_started":
            continue
        stage = store.get_workflow_record("stage", participant.get("stage_id", ""))
        config = store.get_workflow_record(
            "stage_config",
            participant.get("config_version_id") or (stage or {}).get("config_version_id", ""),
        ) if stage else None
        opened = _parse_time((config or {}).get("open_at", ""), "开放时间")
        if config and (config.get("allow_early_write") or (opened and now >= opened and _prerequisites_satisfied(store, participant, config))):
            return
    if any(item.get("state") in {"approved", "pending_archive", "archived"} for item in participants):
        raise HTTPException(status_code=409, detail="当前阶段已通过或归档，请重开后再编辑")
    raise HTTPException(status_code=409, detail="当前阶段尚未开放或前置阶段未完成，暂不能编辑论文")


def _ensure_access(proj: WritingProject, user: dict, store: Store | None = None, *, write: bool = False) -> None:
    """Enforce project scope; workflow-bound projects require current assignment."""
    if proj.deleted_at:
        raise HTTPException(status_code=404, detail="项目已在回收站")
    scope_store = store or Store()
    if _project_accessible(proj, user, scope_store):
        if write:
            _ensure_student_can_write(proj, user, scope_store)
        return
    if not proj.owner_id:
        raise HTTPException(status_code=403, detail="项目归属信息缺失, 请联系管理员迁移")
    raise HTTPException(status_code=403, detail="无权访问该项目")


@router.get("/trash")
def list_deleted_projects(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    """List recoverable projects without exposing their manuscript body."""
    return [
        {
            "id": project.id, "template_name": project.template_name, "title": project.title,
            "deleted_at": project.deleted_at, "updated_at": project.updated_at,
        }
        for project in store.list_projects()
        if project.deleted_at and _project_accessible(project, user, store)
    ]


@router.get("/{proj_id}")
def get_project(proj_id: str, store: Store = Depends(get_store),
                user: dict = Depends(current_user)) -> WritingProject:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if proj.deleted_at:
        raise HTTPException(status_code=404, detail="项目已在回收站")
    _ensure_access(proj, user, store)
    _project_template(proj, store)
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
    paragraphs: list[dict] | None = None   # Paragraph blocks, including validated inline_runs when present.
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
    _ensure_access(proj, user, store, write=True)

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
    _ensure_access(proj, user, store, write=True)
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
    if block_type == "equation":
        equation = data.get("equation", {})
        omml = str(equation.get("omml", ""))
        if not omml.startswith("<m:oMath") or len(omml) > 200_000:
            raise HTTPException(status_code=400, detail="公式内容无效")
        return ParagraphBlock(
            id=str(data.get("id", "")), type="equation",
            equation={"omml": omml, "alt_text": str(equation.get("alt_text", ""))[:4000]},
        )
    text = str(data.get("text", ""))
    inline_runs: list[InlineTextRun] = []
    raw_inline_runs = data.get("inline_runs")
    if isinstance(raw_inline_runs, list) and 0 < len(raw_inline_runs) <= 500:
        parsed_runs = []
        for raw_run in raw_inline_runs:
            if not isinstance(raw_run, dict) or not isinstance(raw_run.get("text"), str):
                parsed_runs = []
                break
            vert_align = raw_run.get("vert_align")
            highlight = raw_run.get("highlight")
            parsed_runs.append(InlineTextRun(
                text=raw_run["text"],
                bold=bool(raw_run.get("bold", False)),
                italic=bool(raw_run.get("italic", False)),
                underline=bool(raw_run.get("underline", False)),
                highlight=(highlight.strip() or None) if isinstance(highlight, str) else None,
                vert_align=vert_align if vert_align in {"superscript", "subscript"} else None,
            ))
        if parsed_runs and "".join(run.text for run in parsed_runs) == text:
            inline_runs = parsed_runs
    return ParagraphBlock(
        id=str(data.get("id", "")),
        text=text,
        bold=bool(data.get("bold", False)),
        italic=bool(data.get("italic", False)),
        underline=bool(data.get("underline", False)),
        highlight=(str(data["highlight"]).strip() or None) if data.get("highlight") is not None else None,
        vert_align=(str(data["vert_align"]).strip() or None) if data.get("vert_align") is not None else None,
        inline_runs=inline_runs,
    )


@router.post("/{proj_id}/images", status_code=201)
async def upload_image(proj_id: str, file: UploadFile = File(...),
                     user: dict = Depends(current_user)) -> dict:
    """上传论文图片, 返回静态资源 URL。"""
    store = get_store()
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user, store, write=True)
    data = file.file.read(IMAGE_MAX_BYTES + 1)
    kind = validate_image(data, file.filename)
    ext = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}[kind]
    fname = f"{proj_id}-{uuid.uuid4().hex[:8]}{ext}"
    dest = document_upload_dir() / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(data)
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="图片保存失败")
    return {"url": f"/media/uploads/{fname}"}


def _apply_imported_docx(proj: WritingProject, imported: dict) -> dict:
    """Merge an imported manuscript in document order while retaining required gaps."""
    heading_types = {SectionType.HEADING1.value, SectionType.HEADING2.value, SectionType.HEADING3.value}

    def normalized_title(title: str) -> str:
        value = unicodedata.normalize("NFKC", title or "")
        value = re.sub(r"[（(].*?[）)]\s*$", "", value)
        value = re.sub(r"^第(?:\d+|[一二三四五六七八九十]+)章\s*", "", value)
        value = re.sub(r"^\d+(?:\.\d+)+\s*", "", value)
        return re.sub(r"\s+", "", value).casefold()

    def section_number(title: str) -> str:
        value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", title or ""))
        chapter = re.match(r"^第(\d+|[一二三四五六七八九十]+)章", value)
        if chapter:
            token = chapter.group(1)
            if token.isdigit():
                return str(int(token))
            digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
            if token == "十":
                return "10"
            if token.startswith("十"):
                return str(10 + digits.get(token[1:], 0))
            if token.endswith("十"):
                return str(digits.get(token[0], 0) * 10)
            return str(digits.get(token, 0))
        numbered = re.match(r"^(\d+(?:\.\d+)+)", value)
        return numbered.group(1) if numbered else ""

    def section_has_content(section: ProjectSection) -> bool:
        for block in section.paragraphs:
            block_type = getattr(block.type, "value", block.type)
            if block_type == "image" and block.image and block.image.url:
                return True
            if block_type == "table" and block.table and any(
                str(cell).strip() for row in block.table.cells for cell in row
            ):
                return True
            if block_type == "equation" and block.equation and (
                block.equation.omml or block.equation.alt_text.strip()
            ):
                return True
            if (block.text or "").strip():
                return True
        return False

    existing_sections = list(proj.sections)
    candidates = [section for section in existing_sections if section.type not in {"cover", "toc"}]
    used: set[str] = set()
    imported_in_order: list[ProjectSection] = []
    added_section_count = 0
    first_body = next((section for section in candidates if section.type == SectionType.BODY.value), None)
    for imported_section in imported.get("sections", []):
        title = str(imported_section.get("title", "")).strip()
        imported_type = str(imported_section.get("type", SectionType.BODY.value))
        target = None
        if imported_type in {SectionType.ABSTRACT.value, SectionType.ENGLISH_ABSTRACT.value,
                             SectionType.REFERENCES.value, SectionType.ACKNOWLEDGEMENT.value,
                             SectionType.APPENDIX.value}:
            target = next((section for section in candidates
                           if section.key not in used and section.type == imported_type), None)
        if target is None and title:
            normalized = normalized_title(title)
            target = next((section for section in candidates
                           if section.key not in used and normalized_title(section.title) == normalized), None)
        if target is None and imported_type in heading_types:
            imported_number = section_number(title)
            if imported_number:
                target = next((section for section in candidates
                               if section.key not in used and section.type in heading_types
                               and section_number(section.title) == imported_number), None)
        if target is None and imported_section.get("key") == "import-body":
            if first_body is not None and first_body.key not in used:
                target = first_body

        blocks = []
        for raw_block in imported_section.get("paragraphs", []):
            block = dict(raw_block)
            if block.get("type") == "image":
                image_data = block.pop("_image_data", None)
                extension = block.pop("_image_extension", ".png")
                if image_data:
                    kind = validate_image(image_data)
                    extension = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}[kind]
                    filename = f"{proj.id}-{uuid.uuid4().hex[:8]}{extension}"
                    destination = document_upload_dir() / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(image_data)
                    block.setdefault("image", {})["url"] = f"/media/uploads/{filename}"
            blocks.append(ParagraphBlock.model_validate(block))
        if target is not None and target.key not in used:
            target.paragraphs = blocks
            if imported_section.get("key") != "import-body" and title:
                if section_number(title) or not section_number(target.title):
                    target.title = title
                if imported_type in heading_types or imported_type in {
                    SectionType.ABSTRACT.value, SectionType.ENGLISH_ABSTRACT.value,
                    SectionType.REFERENCES.value, SectionType.ACKNOWLEDGEMENT.value,
                    SectionType.APPENDIX.value,
                }:
                    target.type = imported_type
            used.add(target.key)
            imported_in_order.append(target)
            continue
        imported_in_order.append(ProjectSection(
            key=f"import-{uuid.uuid4().hex[:10]}", type=imported_type,
            title=title or "导入章节", required=False, paragraphs=blocks,
        ))
        added_section_count += 1

    structural = [section for section in existing_sections if section.type in {"cover", "toc"}]
    retained_existing = [
        section for section in existing_sections
        if section.type not in {"cover", "toc"} and section.key not in used
        and (section.required or section_has_content(section))
    ]
    proj.sections = structural + imported_in_order + retained_existing
    for key, value in (imported.get("metadata") or {}).items():
        normalized = str(value or "").strip()
        if normalized and not str(proj.meta.get(key, "") or "").strip():
            proj.meta[key] = normalized
    return {"matched_section_keys": sorted(used), "added_section_count": added_section_count}


@router.post("/{proj_id}/import-docx")
def import_project_docx(proj_id: str, file: UploadFile = File(...), apply: bool = False,
                        base_revision: int | None = None,
                        store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    """Preview or apply a DOCX manuscript import; apply is guarded by project revision."""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user, store, write=apply)
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="只支持 .docx 文件")
    data = read_limited(file, IMPORT_MAX_BYTES)
    validate_docx_bytes(data)
    try:
        imported = import_docx_project(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"论文正文导入失败: {exc}") from exc
    preview = {
        "filename": file.filename,
        "metadata": imported["metadata"],
        "stats": imported["stats"],
        "warnings": imported["warnings"],
        "sections": public_import_sections(imported["sections"]),
        "applied": False,
        "project_revision": proj.revision,
    }
    if not apply:
        return preview
    expected_revision = proj.revision if base_revision is None else base_revision
    if expected_revision != proj.revision:
        raise HTTPException(status_code=409, detail={"message": "项目已被更新，请重新预览后应用", "revision": proj.revision})
    try:
        merge = _apply_imported_docx(proj, imported)
        saved = store.update_project_if_revision(proj, expected_revision)
    except HTTPException:
        cleanup_orphan_images(store)
        raise
    except Exception as exc:
        cleanup_orphan_images(store)
        raise HTTPException(status_code=422, detail=f"论文正文应用失败: {exc}") from exc
    if saved is None:
        cleanup_orphan_images(store)
        raise HTTPException(status_code=409, detail={"message": "项目已被更新，请重新预览后应用"})
    return preview | {"applied": True, "project": saved, "merge": merge}


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
    _ensure_access(proj, user, store, write=True)
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


class RestoreLocalSnapshotRequest(BaseModel):
    snapshot: dict
    base_revision: int = Field(ge=0)


@router.put("/{proj_id}")
def update_project(proj_id: str, req: UpdateProjectRequest,
                   store: Store = Depends(get_store),
                   user: dict = Depends(current_user)) -> WritingProject:
    """只允许更新项目标题, 不接受客户端篡改归属或模板绑定。"""
    old = store.get_project(proj_id)
    if old is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(old, user, store, write=True)
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


@router.post("/{proj_id}/restore-local")
def restore_local_snapshot(proj_id: str, req: RestoreLocalSnapshotRequest,
                           store: Store = Depends(get_store),
                           user: dict = Depends(current_user)) -> WritingProject:
    """Restore only editable fields from a browser recovery snapshot."""
    current = store.get_project(proj_id)
    if current is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(current, user, store, write=True)
    if req.base_revision != current.revision:
        raise HTTPException(
            status_code=409,
            detail={"message": "服务器版本已变化，请先读取最新版本后合并本地草稿", "revision": current.revision},
        )
    raw_sections = req.snapshot.get("sections")
    raw_meta = req.snapshot.get("meta", {})
    if not isinstance(raw_sections, list) or not isinstance(raw_meta, dict):
        raise HTTPException(status_code=422, detail="本地草稿结构无效")
    try:
        sections = [ProjectSection.model_validate(item) for item in raw_sections]
    except Exception as exc:
        raise HTTPException(status_code=422, detail="本地草稿章节结构无效") from exc
    for section in sections:
        for block in section.paragraphs:
            block_type = getattr(block.type, "value", block.type)
            if block_type == "image" and block.image and block.image.url:
                filename = Path(block.image.url).name
                if not block.image.url.startswith("/media/uploads/") or not filename.startswith(f"{proj_id}-"):
                    raise HTTPException(status_code=422, detail="本地草稿包含无效图片引用")
    current.title = str(req.snapshot.get("title", current.title))
    current.meta = {str(key): str(value or "") for key, value in raw_meta.items()}
    current.sections = sections
    saved = store.update_project_if_revision(current, current.revision)
    if saved is None:
        raise HTTPException(status_code=409, detail={"message": "服务器版本已变化，请刷新后重试"})
    return saved


@router.delete("/{proj_id}", status_code=204)
def delete_project(proj_id: str, store: Store = Depends(get_store),
                  user: dict = Depends(current_user)) -> None:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user, store, write=True)
    referenced_versions = [item for item in store.list_workflow_records("project_version") if item.get("project_id") == proj_id]
    submitted_version_ids = {
        item.get("project_version_id") for item in store.list_workflow_records("submission")
        if item.get("project_version_id")
    }
    if any(item.get("id") in submitted_version_ids for item in referenced_versions):
        raise HTTPException(status_code=409, detail="已有正式提交引用该论文，不能删除；请通过重开或更正流程处理")
    proj.deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if store.update_project_if_revision(proj, proj.revision) is None:
        raise HTTPException(status_code=409, detail="项目已被更新，请刷新后重试")
    _record_project_audit(store, proj, user, "project.soft_delete")


@router.post("/{proj_id}/restore")
def restore_deleted_project(proj_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> WritingProject:
    project = store.get_project(proj_id)
    if project is None or not project.deleted_at:
        raise HTTPException(status_code=404, detail="回收站中不存在该项目")
    if not _project_accessible(project, user, store):
        raise HTTPException(status_code=403, detail="无权恢复该项目")
    project.deleted_at = ""
    saved = store.update_project_if_revision(project, project.revision)
    if saved is None:
        raise HTTPException(status_code=409, detail="项目已被更新，请刷新后重试")
    _record_project_audit(store, saved, user, "project.restore")
    return saved



def _project_template(proj: WritingProject, store: Store) -> PaperTemplate:
    """返回项目当前有效模板：冻结模板为基底，规范排版覆盖其样式。"""
    if proj.template_source_snapshot:
        base_template = PaperTemplate.model_validate(proj.template_source_snapshot)
    elif proj.template_snapshot:
        base_template = PaperTemplate.model_validate(proj.template_snapshot)
    else:
        base_template = store.get_template(proj.template_id)
    if base_template is None:
        raise HTTPException(status_code=404, detail="项目绑定的模板不存在, 请联系管理员迁移")

    snapshot = hydrate_snapshot_format_overrides(proj.requirement_snapshot)
    effective_template = apply_requirement_format_overrides(base_template, snapshot)
    snapshot_changed = snapshot != (proj.requirement_snapshot or {})
    template_payload = effective_template.model_dump(mode="json")
    template_changed = template_payload != (proj.template_snapshot or {})
    if snapshot_changed:
        proj.requirement_snapshot = snapshot
    if template_changed:
        proj.template_snapshot = template_payload
    if snapshot_changed or template_changed:
        store.save_project(proj)
    return effective_template


def _ensure_requirement_snapshot(proj: WritingProject, tpl: PaperTemplate, store: Store) -> dict:
    """为旧项目补建规范快照；新项目始终在创建时生成。"""
    if proj.requirement_snapshot:
        hydrated = hydrate_snapshot_format_overrides(proj.requirement_snapshot)
        if hydrated != proj.requirement_snapshot:
            proj.requirement_snapshot = hydrated
            store.save_project(proj)
        return hydrated
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
    proj.template_snapshot = apply_requirement_format_overrides(tpl, proj.requirement_snapshot).model_dump(mode="json")
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
    proj.template_source_snapshot = tpl.model_dump(mode="json")
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
    proj.template_snapshot = apply_requirement_format_overrides(tpl, proj.requirement_snapshot).model_dump(mode="json")
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
    proj.template_snapshot = apply_requirement_format_overrides(tpl, proj.requirement_snapshot).model_dump(mode="json")
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
    assert_resource_read(store, user, "project", proj)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
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
    assert_resource_read(store, user, "project", proj)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    tpl = _project_template(proj, store)
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
    tpl = _project_template(proj, store)

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
        proj.template_source_snapshot = tpl.model_dump(mode="json")
        saved = store.update_project_if_revision(proj, expected_revision)
        if saved is None:
            raise HTTPException(
                status_code=409,
                detail={"message": "项目内容已被更新, 请刷新后重试"},
            )
        proj = saved
        tpl = _project_template(proj, store)
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


class WaiverRevocationRequest(BaseModel):
    reason: str


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
    if user["role"] == "teacher" and waiver.get("requested_by") == user["id"]:
        raise HTTPException(status_code=403, detail="申请人不能审核自己的豁免")
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


@router.post("/{proj_id}/requirements/waivers/{waiver_id}/revoke")
def revoke_requirement_waiver(proj_id: str, waiver_id: str, req: WaiverRevocationRequest,
                              store: Store = Depends(get_store),
                              user: dict = Depends(current_user)) -> dict:
    """撤销已批准豁免，并把已经使用该豁免的候选版本标记为需复核。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user)
    if not _can_review_waiver(proj, user, store):
        raise HTTPException(status_code=403, detail="只有指导教师或管理员可以撤销豁免")
    waiver = next((item for item in proj.waivers if item.get("id") == waiver_id), None)
    if waiver is None:
        raise HTTPException(status_code=404, detail="豁免申请不存在")
    if waiver.get("status") != WaiverStatus.APPROVED.value:
        raise HTTPException(status_code=409, detail="只有已批准的豁免可以撤销")
    if not req.reason.strip():
        raise HTTPException(status_code=422, detail="撤销豁免必须填写理由")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    waiver.update({
        "status": WaiverStatus.REVOKED.value,
        "revoked_by": user["id"],
        "revoked_at": now,
        "revoke_reason": req.reason.strip(),
        "review_note": req.reason.strip(),
    })
    store.save_project(proj)

    # 豁免变更属于项目规范依据变更；历史候选和审核结论保留，只把仍可影响后续审核的候选标成需复核。
    from app.api.workflow import _audit, _current_supervision, _notify

    affected_ids: list[str] = []
    candidates = [
        item for item in store.list_workflow_records("candidate")
        if item.get("project_id") == proj_id and _candidate_uses_waiver(item, waiver_id)
    ]
    batch_id = (proj.organization_context or {}).get("batch_id", "")
    cohort = store.get_workflow_record("cohort", batch_id) if batch_id else None
    organization_id = (cohort or {}).get("organization_id", "")
    with store.workflow_transaction() as conn:
        for candidate in candidates:
            current = store.get_workflow_record("candidate", candidate["id"])
            if not current or not _candidate_uses_waiver(current, waiver_id):
                continue
            current["recheck_required"] = True
            current["recheck_reason"] = "提交所依据的豁免已撤销，请重新预检"
            current["recheck_waiver_id"] = waiver_id
            saved = store.save_workflow_record_conn(
                conn, "candidate", current, expected_revision=current.get("revision", 0)
            )
            if saved is None:
                raise HTTPException(status_code=409, detail="候选版本正在被更新，请刷新后重试")
            affected_ids.append(current["id"])
            participant = store.get_workflow_record("participant", current.get("participant_id", ""))
            if not participant:
                continue
            _notify(
                store, conn, participant.get("student_id", ""), "requirement.waiver.revoked",
                "project", proj_id, "论文规范豁免已撤销，请重新检查后再提交",
                dedupe_key=f"requirement.waiver.revoked:{waiver_id}:{current['id']}",
            )
            relation = _current_supervision(store, participant.get("cohort_id", ""), participant.get("student_id", ""))
            if relation:
                _notify(
                    store, conn, relation.get("teacher_id", ""), "requirement.waiver.revoked",
                    "project", proj_id, "学生提交所依据的规范豁免已撤销，需要复核",
                    dedupe_key=f"requirement.waiver.revoked:teacher:{waiver_id}:{current['id']}",
                )
        _audit(
            store, conn, actor_id=user["id"], action="requirement.waiver.revoked",
            resource_type="project", resource_id=proj_id, organization_id=organization_id,
            reason=req.reason.strip(),
        )
    return {"waiver": waiver, "recheck_required": True, "affected_candidate_ids": affected_ids}


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
    if user["role"] == "teacher" and dispute.get("requested_by") == user["id"]:
        raise HTTPException(status_code=403, detail="申诉人不能审核自己的申诉")
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
    from app.api.ai import _metered_chat, _resolve_cfg
    resolved = _resolve_cfg(store, user)
    if not resolved:
        raise HTTPException(status_code=503, detail="AI 功能未启用")
    cfg, _source = resolved
    question = req.question.strip() or item.get("description", "请按要求检查论文")
    messages = [
        {"role": "system", "content": "你是论文人工检查辅助工具。只给出基于提供文本的核对建议，不作最终合规判定，不编造事实。输出三部分：已观察到的证据、疑点或缺口、建议人工确认的问题。"},
        {"role": "user", "content": f"检查要求：{question}\n\n论文内容：\n{content[:11000]}"},
    ]
    suggestion = _metered_chat(
        store, user, cfg, messages, 0.2,
        action="requirement_assist", source=_source,
    )
    # AI 请求可能耗时较长, 重新读取项目后再按 revision 保存, 避免把期间的正文编辑覆盖掉。
    saved = None
    for _ in range(2):
        latest = store.get_project(proj_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        _ensure_access(latest, user)
        _ensure_requirement_item(latest, item_id)
        saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record = {
            "id": new_id("assist"),
            "requirement_item_id": item_id,
            "question": question,
            "suggestion": suggestion,
            "requested_by": user["id"],
            "saved_at": saved_at,
        }
        latest.ai_assist_results = [
            item for item in latest.ai_assist_results
            if item.get("requirement_item_id") != item_id
        ] + [record]
        saved = store.update_project_if_revision(latest, latest.revision)
        if saved is not None:
            break
    if saved is None:
        raise HTTPException(status_code=409, detail="项目内容已被更新, AI 结果未保存, 请重试")
    return {
        **record,
        "is_authoritative": False,
        "saved": True,
        "project_revision": saved.revision,
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
    tpl = _project_template(proj, store)
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
    tpl = _project_template(proj, store)
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
        base_template = PaperTemplate.model_validate(
            proj.template_source_snapshot or tpl.model_dump(mode="json")
        )
        proj.template_snapshot = apply_requirement_format_overrides(base_template, new_snapshot).model_dump(mode="json")
        store.save_project(proj)
    return {"project": proj, "diff": diff}


@router.get("/{proj_id}/check-runs")
def list_project_check_runs(proj_id: str, store: Store = Depends(get_store),
                            user: dict = Depends(current_user)) -> list[dict]:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    return [item.model_dump(mode="json") for item in store.list_check_runs(proj_id)]


@router.get("/{proj_id}/check-runs/{run_id}")
def get_project_check_run(proj_id: str, run_id: str, store: Store = Depends(get_store),
                          user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
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
    assert_resource_read(store, user, "project", proj)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    tpl = _project_template(proj, store)
    from app.services.project_check import check_project as run_check
    report = run_check(proj, tpl, pack=None, store=store, performed_by=user["id"])
    report["report_type"] = "compliance"
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report


def _cleanup_export(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)


def _export_missing(proj: WritingProject, tpl: PaperTemplate, store: Store, actor_id: str) -> list[str]:
    """Run the same export gate used by both synchronous and durable exports."""
    missing = validate_project_completeness(proj, tpl)
    from app.services.project_check import check_project as run_check
    report = run_check(proj, tpl, pack=None, store=store, performed_by=actor_id)
    missing.extend(
        f"{finding.get('source', '规范要求')}: {finding['message']}"
        for finding in report.get("findings", [])
        if finding.get("source_type") == "requirement"
        and finding.get("enforce") == "hard"
        and finding.get("severity") == "error"
    )
    return missing


@router.post("/{proj_id}/export-jobs", status_code=202)
def create_project_export_job(proj_id: str, force: bool = False,
                              store: Store = Depends(get_store),
                              user: dict = Depends(current_user)) -> dict:
    """Queue a version-frozen DOCX export for the standalone worker."""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    tpl = _project_template(proj, store)
    force = force and can_manage(user)
    if not force:
        missing = _export_missing(proj, tpl, store, user["id"])
        if missing:
            raise HTTPException(status_code=409, detail={"message": "以下必填项未完成, 无法导出", "missing": missing})
    from app.services.export_jobs import create_export_job, export_job_view
    return export_job_view(create_export_job(store, proj, tpl, force=force, actor_id=user["id"]))


@router.get("/{proj_id}/export-jobs")
def list_project_export_jobs(proj_id: str, store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> list[dict]:
    """List export attempts for a project without exposing content snapshots."""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    from app.services.export_jobs import export_job_view
    return [
        export_job_view(item)
        for item in store.list_workflow_records("project_export")
        if item.get("project_id") == proj_id
    ]


@router.get("/{proj_id}/export-jobs/{export_id}")
def get_project_export_job(proj_id: str, export_id: str, store: Store = Depends(get_store),
                           user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    record = store.get_workflow_record("project_export", export_id)
    if not record or record.get("project_id") != proj_id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    from app.services.export_jobs import export_job_view
    return export_job_view(record)


@router.post("/{proj_id}/export-jobs/{export_id}/retry")
def retry_project_export_job(proj_id: str, export_id: str, store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    # 导出失败时作者需要自救；教师与管理员保留原有的跨项目重试权限。
    if not can_manage(user) and proj.owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="仅项目所有者或教师、管理员可以重试导出任务")
    record = store.get_workflow_record("project_export", export_id)
    if not record or record.get("project_id") != proj_id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    if record.get("status") not in {"failed", "queued"}:
        raise HTTPException(status_code=409, detail="当前导出任务不可重试")
    from app.domain.workflow_states import transition_state
    from app.repositories.workflow_repository import WorkflowRepository
    repository = WorkflowRepository(store)
    with store.workflow_transaction() as conn:
        if record.get("status") == "failed":
            record["status"] = transition_state("project_export", "failed", "queued")
            record["error_code"] = ""
            saved = repository.save_in_transaction(
                conn, "project_export", record, expected_revision=record.get("revision", 0)
            )
            if saved is None:
                raise HTTPException(status_code=409, detail="导出任务已被其他操作更新，请刷新后重试")
            record = saved
        job = repository.store.retry_job_conn(conn, record.get("job_id", ""), "管理员手动重试项目导出")
        if job is None:
            raise HTTPException(status_code=409, detail="导出任务队列状态不允许重试")
    from app.services.export_jobs import export_job_view
    return export_job_view(record)


@router.get("/{proj_id}/export-jobs/{export_id}/download")
def download_project_export(proj_id: str, export_id: str, store: Store = Depends(get_store),
                            user: dict = Depends(current_user)) -> FileResponse:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    record = store.get_workflow_record("project_export", export_id)
    if not record or record.get("project_id") != proj_id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    if record.get("status") != "succeeded" or not record.get("file_path"):
        raise HTTPException(status_code=409, detail="导出尚未完成")
    from app.services.export_jobs import EXPORT_DIR
    path = Path(record["file_path"])
    if path.parent != EXPORT_DIR or not path.is_file():
        raise HTTPException(status_code=404, detail="导出制品不存在")
    return FileResponse(
        path,
        filename=record.get("filename") or "paper.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/{proj_id}/export")
def export_docx(proj_id: str, force: bool = False,
                store: Store = Depends(get_store),
                user: dict = Depends(current_user)):
    """按模板导出为 .docx, 下载新文件。

    导出前校验必填章节与封面字段; 未完成时返回 409 与缺失清单,
    教师/管理员可用 force=true 导出未完成草稿；学生始终经过校验。
    """
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    tpl = _project_template(proj, store)

    force = force and can_manage(user)
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


@router.post("/{proj_id}/export-preview")
def export_preview(proj_id: str, force: bool = False,
                   store: Store = Depends(get_store),
                   user: dict = Depends(current_user)) -> dict:
    """Return a read-only export manifest before creating a downloadable DOCX."""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    tpl = _project_template(proj, store)
    force = force and can_manage(user)
    missing = validate_project_completeness(proj, tpl)
    requirement_findings: list[str] = []
    from app.services.project_check import check_project as run_check
    report = run_check(proj, tpl, pack=None, store=store, performed_by=user["id"])
    requirement_findings.extend(
        f"{finding.get('source', '规范要求')}: {finding['message']}"
        for finding in report.get("findings", [])
        if finding.get("source_type") == "requirement"
        and finding.get("enforce") == "hard"
        and finding.get("severity") == "error"
    )
    missing.extend(requirement_findings)
    blocks = [block for section in proj.sections for block in section.paragraphs]
    block_types = {getattr(block.type, "value", block.type) for block in blocks}
    return {
        "project_id": proj.id, "revision": proj.revision, "title": proj.title,
        "template_id": tpl.id, "template_name": tpl.name,
        "content_hash": project_content_hash(proj),
        "can_export": bool(force or not missing), "force": force,
        "missing": missing, "check_summary": report.get("summary", {}),
        "counts": {
            "sections": len(proj.sections), "blocks": len(blocks),
            "paragraphs": sum(1 for block in blocks if getattr(block.type, "value", block.type) == "paragraph"),
            "tables": sum(1 for block in blocks if getattr(block.type, "value", block.type) == "table"),
            "images": sum(1 for block in blocks if getattr(block.type, "value", block.type) == "image"),
            "equations": sum(1 for block in blocks if getattr(block.type, "value", block.type) == "equation"),
        },
        "sections_preview": [
            {"key": section.key, "title": section.title, "type": section.type,
             "blocks": len(section.paragraphs),
             "characters": sum(len(block.text or "") for block in section.paragraphs
                               if getattr(block.type, "value", block.type) == "paragraph")}
            for section in proj.sections
        ],
    }
