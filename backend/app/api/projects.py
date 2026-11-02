"""写作项目 API: 基于模板创建、保存章节内容、导出 docx。"""

from __future__ import annotations

import shutil
import hashlib
import json
import re
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from lxml import etree
from pydantic import BaseModel, Field, ValidationError

from app.api.templates import ensure_template_admitted_for_writing, get_store
from app.auth import can_manage, current_user
from app.core.export.docx_exporter import export_project_to_docx
from app.core.extract.xml_utils import parse_xml, qn
from app.core import feature_flags
from app.db import Store, new_id
from app.models.project import BlockType, InlineTextRun, ParagraphBlock, ProjectSection, ReferenceEntry, WritingProject
from app.models.policy import (
    DisputeStatus,
    RequirementAcknowledgement,
    RequirementDispute,
    RequirementUpgrade,
    Waiver,
    WaiverStatus,
)
from app.models.template import PaperTemplate, SectionType
from app.services.image_cleanup import remove_project_images
from app.services.project_validation import validate_heading_hierarchy, validate_project_completeness
from app.services.project_versions import project_attachment_refs, project_content_hash, project_snapshot
from app.services.access_service import visible_organization_ids
from app.services.resource_access import assert_resource_read
from app.services.template_compare import compare_project_to_template
from app.services.projects import create_project_from_template
from app.services.policy_resolver import (
    apply_requirement_format_overrides,
    build_requirement_snapshot,
    hydrate_snapshot_format_overrides,
)
from app.services.template_source import resolve_template_source, template_source_dir, delete_template_source
from app.services.source_template_profile import ProfileError
from app.services.docx_output_check import (
    DocxOutputCheckError,
    expected_output_check_identity,
    output_check_identity,
    require_docx_output_pass,
    safe_output_check_diagnostics,
    source_template_bytes,
    verify_report_binding,
    verify_report_identity,
    verify_source_template_binding,
)
from app.services.docx_import_candidates import read_candidate_source, write_candidate_source
from app.services.docx_import_inventory import (
    INVENTORY_VERSION,
    inventory_docx_source,
    inventory_sha256,
    reconcile_applied_docx_import,
    reconcile_docx_import,
)
from app.services.docx_project_import import (
    IMPORT_CONFIGURATION_VERSION,
    import_candidate_sha256,
    import_docx_project,
    public_import_sections,
)
from app.services.document_assets import document_upload_dir
from app.services.manual_output_review import review_identity, review_required, review_view
from app.services.references import reconcile_reference_language, reconcile_reference_year, reference_block_text
from app.services.upload_security import IMPORT_MAX_BYTES, IMAGE_MAX_BYTES, read_limited, validate_docx_bytes, validate_image

router = APIRouter(prefix="/api/projects")

class CreateProjectRequest(BaseModel):
    template_id: str
    title: str = ""
    organization_context: dict[str, str] = {}
    stage: str = ""


class ManualOutputReviewRequest(BaseModel):
    output_sha256: str = Field(min_length=64, max_length=64)
    confirmed_check_ids: list[str] = Field(min_length=1)


def _project_writing_metrics(project: WritingProject) -> dict[str, object]:
    """Return lightweight writing progress for the manuscript list view."""
    sections = [section for section in project.sections if section.type not in ("cover", "toc")]

    def block_has_content(block) -> bool:
        block_type = getattr(block.type, "value", block.type)
        if block_type == "image":
            return bool(block.image and block.image.url)
        if block_type == "table":
            return bool(block.table and any(str(cell).strip() for row in block.table.cells for cell in row))
        return bool(reference_block_text(block).strip())

    completed = [section for section in sections if any(block_has_content(block) for block in section.paragraphs)]
    total_words = sum(
        len(reference_block_text(block).strip())
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
        item for item in store.list_workflow_records("participant", limit=None)
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


@router.get("/{proj_id}/capabilities")
def project_capabilities(proj_id: str, store: Store = Depends(get_store),
                         user: dict = Depends(current_user)) -> dict:
    from app.services.word_optional_pdf import word_pdf_available
    proj = store.get_project(proj_id)
    if proj is None or proj.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user, store, write=True)
    return {
        "manuscript_import_enabled": feature_flags.manuscript_import_enabled(),
        "template_migration_enabled": not feature_flags.m1_pilot_active(),
        "formal_delivery_enabled": feature_flags.project_delivery_enabled(store, proj.owner_id),
        "optional_pdf_enabled": word_pdf_available(),
    }


@router.post("", status_code=201)
def create_project(req: CreateProjectRequest, store: Store = Depends(get_store),
                   user: dict = Depends(current_user)) -> WritingProject:
    """基于模板创建写作项目(实例化章节树), 归属当前用户。"""
    tpl = store.get_template(req.template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    ensure_template_admitted_for_writing(tpl)
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
                if not proj.allow_structure_edit and req.title != sec.title:
                    raise HTTPException(status_code=409,detail="当前母版的章节标题已冻结，请由教师修改模板并重新发布")
                sec.title = req.title
            if req.paragraphs is not None:
                existing = {block.id: block for block in sec.paragraphs}
                ids = [str(block.get("id", "")) for block in req.paragraphs]
                if len(ids) != len(set(ids)):
                    raise HTTPException(status_code=422, detail="章节内容块 ID 存在重复")
                parsed = [
                    _parse_par(
                        p, proj.id, existing.get(str(p.get("id", ""))), actor_id=user["id"],
                        reference_section=sec.type == SectionType.REFERENCES.value or sec.key == "references",
                    )
                    for p in req.paragraphs
                ]
                if (proj.template_snapshot or {}).get("source_execution") and sec.type in {"keywords","english_keywords"} and (len(parsed)!=1 or parsed[0].type.value!="paragraph" or '\n' in parsed[0].text or parsed[0].inline_runs):
                    raise HTTPException(status_code=422,detail="关键词区域只接受一段关键词文字")
                sec.paragraphs = parsed
            saved = store.update_project_if_revision(proj, expected_revision)
            if saved is None:
                raise HTTPException(
                    status_code=409,
                    detail={"message": "项目内容已被更新, 请刷新后重试"},
                )
            return saved
    raise HTTPException(status_code=404, detail="章节不存在")


def _plain_table_can_be_rebuilt(source_xml: str) -> bool:
    """Only rebuild source tables whose cell content can be represented as plain text."""
    try:
        raw = source_xml.encode("utf-8")
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            return False
        root = parse_xml(raw)
        children = {
            "tbl": {"tblPr", "tblGrid", "tr"},
            "tblPr": {"tblW", "tblStyle", "tblLayout", "tblLook"},
            "tblGrid": {"gridCol"},
            "tr": {"tc"},
            "tc": {"tcPr", "p"},
            "tcPr": {"tcW"},
            "p": {"r"},
            "r": {"t"},
        }
        allowed_children = {
            qn(f"w:{parent}"): {qn(f"w:{child}") for child in names}
            for parent, names in children.items()
        }
        if root.tag != qn("w:tbl"):
            return False
        for element in root.iter():
            if element.tag != qn("w:t") and (element.text or "").strip():
                return False
            if (element.tail or "").strip():
                return False
            if any(child.tag not in allowed_children.get(element.tag, set()) for child in element):
                return False
            if element.tag == qn("w:tc") and len(element.findall(qn("w:p"))) != 1:
                return False
        return True
    except (ValueError, TypeError, etree.XMLSyntaxError):
        return False


def _saved_reference_source(existing: ParagraphBlock | None, *, include_legacy: bool) -> str | None:
    if existing is None:
        return None
    if existing.reference and existing.reference.original_pasted_text is not None:
        return existing.reference.original_pasted_text
    if not include_legacy or existing.type != BlockType.PARAGRAPH:
        return None
    source = (existing.reference.unformatted_text if existing.reference else "") or existing.text
    return source if source.strip() else None


def _parse_par(data: dict, project_id: str = "", existing: ParagraphBlock | None = None, *,
               actor_id: str = "", reference_section: bool = False):
    """把前端传来的内容块解析为 ParagraphBlock, 并限制图片 URL 属于当前项目。"""
    block_type = data.get("type", "paragraph")
    if (existing and existing.reference and existing.reference.original_pasted_text is not None
            and block_type != "paragraph"):
        raise HTTPException(status_code=422, detail="请删除该条文献后再更换内容类型")
    if block_type == "image":
        image = data.get("image", {})
        url = str(image.get("url", ""))
        filename = Path(url).name
        if url and (not url.startswith("/media/uploads/") or not filename.startswith(project_id + "-")):
            raise HTTPException(status_code=400, detail="图片不属于当前项目")
        try:
            return ParagraphBlock(
                id=str(data.get("id", "")),
                type="image",
                image={"url": url, "caption": str(image.get("caption", "")),
                       "width_cm": image.get("width_cm"), "height_cm": image.get("height_cm"),
                       "alignment": image.get("alignment")},
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="图片尺寸或对齐方式无效") from exc
    if block_type == "table":
        t = data.get("table", {})
        try:
            result = ParagraphBlock(
                id=str(data.get("id", "")),
                type="table",
                table={"rows": t.get("rows", 2), "cols": t.get("cols", 2),
                       "cells": t.get("cells", []), "caption": str(t.get("caption", ""))},
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="表格尺寸或内容无效") from exc
        previous = existing.table if existing and existing.type == BlockType.TABLE else None
        if previous:
            result.table.borders = previous.borders
        if previous and previous.source_xml:
            unchanged = (result.table.rows, result.table.cols, result.table.cells) == (
                previous.rows, previous.cols, previous.cells,
            )
            if unchanged:
                result.table.source_xml = previous.source_xml
            elif not _plain_table_can_be_rebuilt(previous.source_xml):
                raise HTTPException(status_code=409, detail="该表格含有当前编辑器不能保留的结构，无法保存修改")
        return result
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
    if raw_inline_runs is not None:
        if not isinstance(raw_inline_runs, list) or len(raw_inline_runs) > 500:
            raise HTTPException(status_code=422, detail="段落行内格式无效，请检查后重试")
        parsed_runs = []
        for raw_run in raw_inline_runs:
            if not isinstance(raw_run, dict) or not isinstance(raw_run.get("text"), str):
                raise HTTPException(status_code=422, detail="段落行内格式无效，请检查后重试")
            vert_align = raw_run.get("vert_align")
            highlight = raw_run.get("highlight")
            if (vert_align is not None and vert_align not in ("superscript", "subscript")) or (highlight is not None and not isinstance(highlight, str)):
                raise HTTPException(status_code=422, detail="段落行内格式无效，请检查后重试")
            parsed_runs.append(InlineTextRun(
                text=raw_run["text"],
                bold=bool(raw_run.get("bold", False)),
                italic=bool(raw_run.get("italic", False)),
                underline=bool(raw_run.get("underline", False)),
                highlight=(highlight.strip() or None) if isinstance(highlight, str) else None,
                vert_align=vert_align,
            ))
        if parsed_runs and "".join(run.text for run in parsed_runs) != text:
            raise HTTPException(status_code=422, detail="段落文字与行内格式不一致，请检查后重试")
        inline_runs = parsed_runs
    reference = data.get("reference")
    if existing and existing.reference and existing.reference.original_pasted_text is not None:
        if not isinstance(reference, dict):
            raise HTTPException(status_code=422, detail="请删除该条目后再移除其粘贴原文")
    original = _saved_reference_source(existing, include_legacy=reference_section)
    if original is not None and isinstance(reference, dict):
        reference = {**reference, "original_pasted_text": original}
    try:
        parsed = ParagraphBlock(
            id=str(data.get("id", "")),
            text=text,
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            underline=bool(data.get("underline", False)),
            highlight=(str(data["highlight"]).strip() or None) if data.get("highlight") is not None else None,
            vert_align=(str(data["vert_align"]).strip() or None) if data.get("vert_align") is not None else None,
            inline_runs=inline_runs,
            reference=reference,
            reference_number=data.get("reference_number"),
        )
        if parsed.reference is not None:
            reconcile_reference_language(
                parsed.reference, existing.reference if existing else None,
                provided=isinstance(reference, dict) and "language_fact" in reference,
                confirm=isinstance(reference, dict) and reference.get("language_confirmation_requested") is True,
                actor_id=actor_id,
            )
            reconcile_reference_year(
                parsed.reference, existing.reference if existing else None,
                provided=isinstance(reference, dict) and "year_fact" in reference,
                confirm=isinstance(reference, dict) and reference.get("year_confirmation_requested") is True,
                actor_id=actor_id,
            )
        return parsed
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="参考文献或段落内容无效，请检查后重试") from exc


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


def _apply_imported_docx(proj: WritingProject, imported: dict, created_paths: list[Path] | None = None) -> dict:
    """Merge an imported manuscript in document order while retaining required gaps."""
    heading_types = {SectionType.HEADING1.value, SectionType.HEADING2.value, SectionType.HEADING3.value}
    master_bound = bool((proj.template_snapshot or {}).get("source_execution"))

    def normalized_title(title: str) -> str:
        value = unicodedata.normalize("NFKC", title or "")
        value = re.sub(r"[（(].*?[）)]\s*$", "", value)
        value = re.sub(r"^第(?:\d+|[一二三四五六七八九十]+)章[：:、.．]?\s*", "", value)
        value = re.sub(r"^\d+(?:\.\d+)*[、.．]?\s*", "", value)
        value = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", value)
        return re.sub(r"\s+", "", value).casefold()

    def section_number(title: str) -> str:
        value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", title or ""))
        digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

        def chinese_integer(token: str) -> str:
            if "十" not in token:
                return str(digits.get(token, 0))
            tens, _, ones = token.partition("十")
            return str((digits.get(tens, 1) if tens else 1) * 10 + digits.get(ones, 0))

        chapter = re.match(r"^第(\d+|[一二三四五六七八九十]+)章", value)
        if chapter:
            token = chapter.group(1)
            return str(int(token)) if token.isdigit() else chinese_integer(token)
        numbered = re.match(r"^(\d+(?:\.\d+)*)(?:[.．、]|$)", value)
        if numbered:
            return numbered.group(1)
        numbered = re.match(r"^(\d+(?:\.\d+)*)(?=[^\d.]|$)", value)
        if numbered:
            return numbered.group(1).rstrip(".")
        chinese = re.match(r"^([一二三四五六七八九十]+)[、.．]", value)
        return chinese_integer(chinese.group(1)) if chinese else ""

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
    if master_bound and (proj.template_snapshot or {}).get("sections"):
        template = PaperTemplate.model_validate(proj.template_snapshot)
        existing_by_key: dict[str, ProjectSection] = {}
        for section in existing_sections:
            existing_by_key.setdefault(section.key, section)
        frozen_sections: list[ProjectSection] = []

        def restore_frozen_nodes(nodes) -> None:
            for node in nodes:
                node_type = getattr(node.type, "value", node.type)
                if node_type in {SectionType.COVER.value, SectionType.TOC.value}:
                    restore_frozen_nodes(node.children)
                    continue
                section = existing_by_key.get(node.key)
                if section is None:
                    section = ProjectSection(
                        key=node.key, type=node_type, title=node.title, required=node.required,
                        paragraphs=[ParagraphBlock(id=f"{node.key}-p1", text="")],
                    )
                else:
                    section = section.model_copy(deep=True)
                    section.type = node_type
                    section.title = node.title
                    section.required = node.required
                frozen_sections.append(section)
                restore_frozen_nodes(node.children)

        restore_frozen_nodes(template.sections)
        if not frozen_sections:
            raise ValueError("冻结母版没有可写章节，不能应用论文内容")
        existing_sections = frozen_sections
    candidates = [section for section in existing_sections if section.type not in {"cover", "toc"}]
    used: set[str] = set()
    imported_in_order: list[ProjectSection] = []
    added_section_count = 0
    flattened_section_titles: list[str] = []
    normalized_table_count = 0
    unplaced_section_titles: list[str] = []
    pending_master_blocks: list[ParagraphBlock] = []
    pending_master_titles: list[str] = []
    source_section_mapping: list[dict] = []
    first_body = next((section for section in candidates if section.type == SectionType.BODY.value), None)
    current_heading1: ProjectSection | None = None
    source_sections = imported.get("sections", [])

    def map_title(source: dict, *, section: ProjectSection | None = None, block_id: str | None = None) -> None:
        if source.get("_source_location"):
            source_section_mapping.append({
                "source_location": source["_source_location"],
                "target_section_key": section.key if section else None,
                "target_block_id": block_id,
                "target_title": section.title if section else source.get("title"),
                "transformation": "heading_as_paragraph" if block_id else (
                    "template_heading" if section and section.title != source.get("title") else "preserved"),
            })

    def append_flattened(target: ProjectSection, source: dict, blocks: list[ParagraphBlock]) -> None:
        """Keep source content inside the frozen master section without changing its tree."""
        title = str(source.get("title", "")).strip()
        if title:
            heading = ParagraphBlock(
                id=f"import-heading-{uuid.uuid4().hex[:10]}", text=title, bold=True,
            )
            target.paragraphs.append(heading)
            map_title(source, block_id=heading.id)
        target.paragraphs.extend(blocks)
        if title:
            flattened_section_titles.append(title)

    for source_index, imported_section in enumerate(source_sections):
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
            title_matches = [section for section in candidates
                             if section.key not in used
                             and section.type == imported_type
                             and normalized_title(section.title) == normalized]
            if len(title_matches) == 1:
                target = title_matches[0]
        if target is None and imported_type in heading_types:
            imported_number = section_number(title)
            if imported_number:
                number_matches = [section for section in candidates
                                  if section.key not in used and section.type == imported_type
                                  and section_number(section.title) == imported_number]
                if len(number_matches) == 1:
                    target = number_matches[0]
        if target is None and imported_section.get("key") == "import-body":
            if first_body is not None and first_body.key not in used:
                target = first_body

        blocks = []
        for raw_block in imported_section.get("paragraphs", []):
            block = dict(raw_block)
            if master_bound and block.get("_border_error"):
                raise ValueError(block["_border_error"])
            if master_bound and block.get("type") == "table" and isinstance(block.get("table"), dict) and block["table"].get("source_xml"):
                # The source table XML belongs to the uploaded document package.
                # A frozen master can safely preserve its cell text, but cannot
                # carry source relationships/styles into the master's package.
                table = dict(block["table"])
                source_xml = str(table.get("source_xml") or "")
                if (re.search(r"<w:(?:gridSpan|vMerge|drawing|pict)\b", source_xml)
                        or len(re.findall(r"<w:tbl(?:\s|>)", source_xml)) > 1):
                    raise ValueError("冻结母版暂不支持带合并单元格、嵌套表格或单元格图片的源表格，请先拆分为普通文本表格")
                table["source_xml"] = ""
                block["table"] = table
                normalized_table_count += 1
            if block.get("type") == "image":
                image_data = block.pop("_image_data", None)
                extension = block.pop("_image_extension", ".png")
                if image_data:
                    kind = validate_image(image_data)
                    extension = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}[kind]
                    filename = f"{proj.id}-{uuid.uuid4().hex[:8]}{extension}"
                    destination = document_upload_dir() / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("xb") as target_file:
                        if created_paths is not None:
                            created_paths.append(destination)
                        target_file.write(image_data)
                    # The confirmed parser result remains immutable evidence.
                    block["image"] = dict(block.get("image") or {})
                    block["image"]["url"] = f"/media/uploads/{filename}"
            blocks.append(ParagraphBlock.model_validate(block))
        if master_bound and imported_type in {SectionType.ABSTRACT.value, SectionType.ENGLISH_ABSTRACT.value}:
            keyword_type = SectionType.KEYWORDS.value if imported_type == SectionType.ABSTRACT.value else SectionType.ENGLISH_KEYWORDS.value
            keyword_target = next((section for section in candidates if section.key not in used and section.type == keyword_type), None)
            keyword_blocks: list[ParagraphBlock] = []
            retained_blocks: list[ParagraphBlock] = []
            prefix_re = re.compile(r"^\s*(?:关键词|key\s*words?)\s*[：:]\s*", re.IGNORECASE)
            for block in blocks:
                if block.type != BlockType.PARAGRAPH:
                    retained_blocks.append(block)
                    continue
                text = block.text or ""
                match = prefix_re.match(text) or re.search(r"(?:关键词|key\s*words?)\s*[：:]\s*", text, re.IGNORECASE)
                if not match:
                    retained_blocks.append(block)
                    continue
                before = text[:match.start()].strip()
                after = text[match.end():].strip()
                if before:
                    retained_blocks.append(block.model_copy(update={"text": before, "inline_runs": []}))
                if after:
                    keyword_blocks.append(block.model_copy(update={"text": after, "inline_runs": []}))
            if keyword_target is not None and keyword_blocks:
                keyword_target.paragraphs = keyword_blocks
                used.add(keyword_target.key)
                imported_in_order.append(keyword_target)
                blocks = retained_blocks
        if target is not None and target.key not in used:
            if master_bound and imported_type == SectionType.HEADING1.value and pending_master_blocks:
                blocks = pending_master_blocks + blocks
                pending_master_blocks.clear()
            target.paragraphs = blocks
            if imported_section.get("key") != "import-body" and title:
                if not master_bound and (section_number(title) or not section_number(target.title)):
                    target.title = title
                if not master_bound and (imported_type in heading_types or imported_type in {
                    SectionType.ABSTRACT.value, SectionType.ENGLISH_ABSTRACT.value,
                    SectionType.REFERENCES.value, SectionType.ACKNOWLEDGEMENT.value,
                    SectionType.APPENDIX.value,
                }):
                    target.type = imported_type
            used.add(target.key)
            if imported_type == SectionType.HEADING1.value:
                current_heading1 = target
            map_title(imported_section, section=target)
            imported_in_order.append(target)
            continue
        if target is None and master_bound:
            # A published source profile is a fixed layout, but manuscript headings
            # commonly differ from its examples or contain deeper levels. Preserve
            # their text and blocks in the current chapter rather than creating
            # sections the master renderer cannot place.
            # Do not consume a future frozen heading slot for an unmatched
            # source heading. Keep it with the current matched chapter, or
            # with the body fallback before the first matched chapter.
            fallback = current_heading1 or first_body
            if imported_type == SectionType.HEADING1.value:
                current_number_raw = section_number(current_heading1.title) if current_heading1 else ""
                current_number = int(current_number_raw) if current_number_raw.isdigit() else 0
                next_number = next((section_number(str(item.get("title", "")))
                                    for item in source_sections[source_index + 1:]
                                    if item.get("type") == SectionType.HEADING1.value
                                    and section_number(str(item.get("title", "")))), "")
                next_number_int = int(next_number) if next_number.isdigit() else 0
                wanted_number = current_number + 1
                if next_number_int == wanted_number + 1:
                    gaps = [section for section in candidates
                            if section.key not in used and section.type == SectionType.HEADING1.value
                            and section_number(section.title) == str(wanted_number)]
                    if len(gaps) == 1:
                        fallback = gaps[0]
                elif current_heading1 is None and first_body is None and next_number_int > 1:
                    first_chapter = [section for section in candidates
                                     if section.key not in used and section.type == SectionType.HEADING1.value
                                     and section_number(section.title) == "1"]
                    if len(first_chapter) == 1:
                        fallback = first_chapter[0]
            if fallback is not None:
                if fallback.key not in used:
                    used.add(fallback.key)
                    imported_in_order.append(fallback)
                append_flattened(fallback, imported_section, blocks)
                if fallback.type == SectionType.HEADING1.value:
                    current_heading1 = fallback
                continue
            if title:
                heading = ParagraphBlock(
                    id=f"import-heading-{uuid.uuid4().hex[:10]}", text=title, bold=True,
                )
                pending_master_blocks.append(heading)
                map_title(imported_section, block_id=heading.id)
                pending_master_titles.append(title)
                flattened_section_titles.append(title)
            pending_master_blocks.extend(blocks)
            continue
        added = ProjectSection(
            key=f"import-{uuid.uuid4().hex[:10]}", type=imported_type,
            title=title or "导入章节", required=False, paragraphs=blocks,
        )
        imported_in_order.append(added)
        map_title(imported_section, section=added)
        added_section_count += 1

    if pending_master_blocks:
        fallback = current_heading1 or first_body or next(
            (section for section in candidates if section.type == SectionType.HEADING1.value), None,
        )
        if fallback is None:
            unplaced_section_titles.extend(pending_master_titles or ["未命名章节"])
        else:
            fallback.paragraphs = pending_master_blocks + fallback.paragraphs

    structural = [section for section in existing_sections if section.type in {"cover", "toc"}]
    retained_existing = [
        section for section in existing_sections
        if section.type not in {"cover", "toc"} and section.key not in used
        and (section.required or section_has_content(section))
    ]
    proj.sections = existing_sections if master_bound else structural + imported_in_order + retained_existing
    for key, value in (imported.get("metadata") or {}).items():
        normalized = str(value or "").strip()
        if normalized and not str(proj.meta.get(key, "") or "").strip():
            proj.meta[key] = normalized
    return {
        "matched_section_keys": sorted(used),
        "added_section_count": added_section_count,
        "flattened_section_count": len(flattened_section_titles),
        "flattened_section_titles": flattened_section_titles,
        "normalized_source_table_count": normalized_table_count,
        "preserved_section_order": master_bound,
        "unplaced_section_titles": unplaced_section_titles,
        "source_section_mapping": source_section_mapping,
    }


def _import_candidate(store: Store, proj: WritingProject, actor_id: str, candidate_id: str) -> dict:
    candidate = store.get_workflow_record("docx_import_candidate", candidate_id)
    if not candidate or candidate.get("project_id") != proj.id or candidate.get("created_by") != actor_id:
        raise HTTPException(status_code=404, detail="导入候选不存在")
    if candidate.get("status") != "preview":
        raise HTTPException(status_code=409, detail="该导入候选已失效或应用，请重新预览")
    if (candidate.get("project_revision") != proj.revision
            or candidate.get("project_content_sha256") != project_content_hash(proj)):
        raise HTTPException(status_code=409, detail={"message": "项目已被更新，请重新预览后应用", "revision": proj.revision})
    if candidate.get("configuration_version") != IMPORT_CONFIGURATION_VERSION:
        raise HTTPException(status_code=409, detail="导入规则已更新，请重新预览")
    return candidate


def _parse_import_candidate(candidate: dict) -> tuple[bytes, dict]:
    try:
        data = read_candidate_source(candidate["source_name"], candidate["source_sha256"])
        imported = import_docx_project(data, candidate.get("heading_decisions") or {})
        if import_candidate_sha256(imported) != candidate["candidate_sha256"]:
            raise ValueError("导入候选内容已变化，请重新预览")
        inventory = inventory_docx_source(data)
        reconciliation = reconcile_docx_import(inventory, imported)
        if (candidate.get("inventory_version") != INVENTORY_VERSION
                or candidate.get("inventory_sha256") != inventory_sha256(inventory)
                or candidate.get("reconciliation") != reconciliation
                or candidate.get("reconciliation_sha256") != reconciliation["sha256"]
                or candidate.get("unresolved") != reconciliation["unresolved"]):
            raise ValueError("导入内容对账凭据已变化或缺失，请重新上传并预览")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return data, imported


def _import_preview(candidate: dict, imported: dict, proj: WritingProject) -> dict:
    return {
        "candidate_id": candidate["id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "source_sha256": candidate["source_sha256"],
        "configuration_version": candidate["configuration_version"],
        "filename": candidate["filename"],
        "metadata": imported["metadata"],
        "stats": imported["stats"],
        "warnings": imported["warnings"],
        "heading_candidates": imported["heading_candidates"],
        "sections": public_import_sections(imported["sections"]),
        "applied": False,
        "project_revision": candidate["project_revision"],
        "structure_mode": "manuscript" if ((proj.template_snapshot or {}).get("source_execution") or {}).get("preserve_manuscript_structure") else "template",
        "integrity": {
            "status": candidate["reconciliation"]["status"],
            "unresolved_count": len(candidate["unresolved"]),
            "counts": candidate["reconciliation"]["counts"],
            "sha256": candidate["reconciliation_sha256"],
        },
        "reconciliation": candidate["reconciliation"],
    }


def _import_version(proj: WritingProject, actor_id: str, reason: str, candidate_id: str) -> dict:
    return {
        "id": new_id("version"), "project_id": proj.id, "source_revision": proj.revision,
        "content_snapshot": project_snapshot(proj), "attachment_refs": project_attachment_refs(proj),
        "content_hash": project_content_hash(proj), "created_by": actor_id,
        "created_reason": reason, "import_candidate_id": candidate_id, "immutable": True, "revision": 0,
    }


@router.get("/{proj_id}/import-docx/candidate")
def latest_project_docx_candidate(proj_id: str, store: Store = Depends(get_store),
                                  user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user, store, write=True)
    feature_flags.require_manuscript_import()
    candidates = (item for item in store.list_workflow_records("docx_import_candidate", limit=None)
                  if item.get("project_id") == proj_id and item.get("created_by") == user["id"]
                  and item.get("status") == "preview")
    candidate = next(candidates, None)
    if candidate is None:
        raise HTTPException(status_code=404, detail="没有可恢复的导入候选")
    candidate = _import_candidate(store, proj, user["id"], candidate["id"])
    _, imported = _parse_import_candidate(candidate)
    return _import_preview(candidate, imported, proj)


@router.post("/{proj_id}/import-docx")
def import_project_docx(proj_id: str, file: UploadFile | None = File(None), apply: bool = False,
                        base_revision: int | None = None, candidate_id: str | None = None,
                        candidate_sha256: str | None = None, heading_decisions: str | None = Form(None),
                        store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    """Create a private preview candidate or apply exactly that confirmed candidate."""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_access(proj, user, store, write=True)
    feature_flags.require_manuscript_import()

    if not apply:
        if bool(file) == bool(candidate_id):
            raise HTTPException(status_code=422, detail="请选择 Word 文件或已有候选重新预览")
        previous = None
        if file:
            if not file.filename or not file.filename.lower().endswith(".docx"):
                raise HTTPException(status_code=400, detail="只支持 .docx 文件")
            data = read_limited(file, IMPORT_MAX_BYTES)
            validate_docx_bytes(data)
            filename = Path(file.filename.replace("\\", "/")).name
        else:
            previous = _import_candidate(store, proj, user["id"], candidate_id or "")
            data, _ = _parse_import_candidate(previous)
            filename = previous["filename"]
        try:
            decisions = json.loads(heading_decisions) if heading_decisions else {}
            imported = import_docx_project(data, decisions)
            inventory = inventory_docx_source(data)
            reconciliation = reconcile_docx_import(inventory, imported)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"论文正文导入失败: {exc}") from exc
        new_candidate = {
            "id": new_id("import_candidate"), "project_id": proj_id, "created_by": user["id"],
            "project_revision": proj.revision, "project_content_sha256": project_content_hash(proj),
            "source_name": f"candidate-{uuid.uuid4().hex}.docx", "filename": filename,
            "source_sha256": imported["source_sha256"], "configuration_version": IMPORT_CONFIGURATION_VERSION,
            "heading_decisions": decisions, "candidate_sha256": import_candidate_sha256(imported),
            "inventory_version": INVENTORY_VERSION, "inventory_sha256": inventory_sha256(inventory),
            "reconciliation": reconciliation, "reconciliation_sha256": reconciliation["sha256"],
            "unresolved": reconciliation["unresolved"],
            "status": "preview", "revision": 0,
        }
        source_path = write_candidate_source(new_candidate["source_name"], data)
        try:
            with store.workflow_transaction() as conn:
                row = conn.execute("SELECT data FROM projects WHERE id = ?", (proj_id,)).fetchone()
                current = WritingProject.model_validate_json(row["data"]) if row else None
                if not current or current.revision != proj.revision or project_content_hash(current) != new_candidate["project_content_sha256"]:
                    raise HTTPException(status_code=409, detail="项目已被更新，请重新预览")
                rows = conn.execute(
                    "SELECT data FROM workflow_records WHERE kind = 'docx_import_candidate' "
                    "AND json_extract(data, '$.project_id') = ? AND json_extract(data, '$.created_by') = ? "
                    "AND json_extract(data, '$.status') = 'preview'", (proj_id, user["id"]),
                ).fetchall()
                active = [json.loads(row["data"]) for row in rows]
                if previous and not any(item["id"] == previous["id"] for item in active):
                    raise HTTPException(status_code=409, detail="导入候选已失效，请重新预览")
                for item in active:
                    item["status"] = "superseded"
                    store.save_workflow_record_conn(conn, "docx_import_candidate", item, expected_revision=item["revision"])
                store.save_workflow_record_conn(conn, "docx_import_candidate", new_candidate)
        except Exception:
            source_path.unlink(missing_ok=True)
            raise
        return _import_preview(new_candidate, imported, proj)

    if not candidate_id or not candidate_sha256 or file is not None or heading_decisions is not None:
        raise HTTPException(status_code=422, detail="请先预览并确认导入候选，再应用到草稿")
    candidate = _import_candidate(store, proj, user["id"], candidate_id)
    if candidate_sha256 != candidate.get("candidate_sha256"):
        raise HTTPException(status_code=409, detail="导入候选摘要不匹配，请重新预览")
    if base_revision is not None and base_revision != candidate["project_revision"]:
        raise HTTPException(status_code=409, detail="草稿版本与预览不一致，请重新预览")
    _, imported = _parse_import_candidate(candidate)
    if any(item.get("decision") is None for item in imported["heading_candidates"]):
        raise HTTPException(status_code=409, detail="请先确认全部疑似标题，再重新预览")
    if candidate["reconciliation"]["status"] != "complete":
        raise HTTPException(status_code=409, detail={
            "message": "源文档还有未对账的内容，未应用到草稿", "reconciliation": candidate["reconciliation"],
        })
    preview = _import_preview(candidate, imported, proj)
    original = proj.model_copy(deep=True)
    created_paths: list[Path] = []
    try:
        if preview["structure_mode"] == "manuscript":
            from app.services.manuscript_template import derive_manuscript_template
            base = proj.layout_template_snapshot or proj.template_source_snapshot or proj.template_snapshot
            layout = PaperTemplate.model_validate(base)
            source_path = resolve_template_source(layout.source_docx)
            if source_path is None:
                raise ProfileError("原版式模板源文件缺失，未应用导入内容")
            derived, payload = derive_manuscript_template(layout, source_path, imported, actor=user["id"])
            requirement_snapshot = proj.requirement_snapshot
            if not requirement_snapshot:
                owner = store.get_user(proj.owner_id) if proj.owner_id else None
                requirement_snapshot = build_requirement_snapshot(
                    store, layout,
                    teacher_id=owner.get("teacher_id") if owner else None,
                    student_id=proj.owner_id,
                    organization_context=proj.organization_context,
                    project_id=proj.id,
                    stage=proj.stage,
                )
            from app.services.manuscript_template import remap_requirement_snapshot
            requirement_snapshot = remap_requirement_snapshot(
                requirement_snapshot,
                derived.source_execution.get("manuscript_section_key_map"),
                derived.source_execution.get("manuscript_source_section_keys", []),
            )
            layout_instance = derived
            derived_path = template_source_dir() / layout_instance.source_docx
            with derived_path.open("xb") as target:
                created_paths.append(derived_path)
                target.write(payload)
            derived = apply_requirement_format_overrides(layout_instance, requirement_snapshot)
            proj.layout_template_snapshot = layout.model_dump(mode="json")
            proj.template_source_snapshot = layout_instance.model_dump(mode="json")
            proj.template_snapshot = derived.model_dump(mode="json")
            proj.requirement_snapshot = requirement_snapshot
        merge = _apply_imported_docx(proj, imported, created_paths)
        if merge.get("unplaced_section_titles"):
            raise HTTPException(status_code=422, detail={
                "message": "冻结母版没有可承载的固定章节，未应用导入内容",
                "unplaced_section_titles": merge["unplaced_section_titles"],
            })
        applied_reconciliation = reconcile_applied_docx_import(imported, proj, merge.get("source_section_mapping", []))
        if (applied_reconciliation["status"] != "complete"
                or applied_reconciliation["candidate_sha256"] != candidate["candidate_sha256"]):
            raise HTTPException(status_code=409, detail={
                "message": "导入内容未完整应用，已保留原草稿", "reconciliation": applied_reconciliation,
            })
        with store.workflow_transaction() as conn:
            row = conn.execute("SELECT data FROM projects WHERE id = ?", (proj_id,)).fetchone()
            current = WritingProject.model_validate_json(row["data"]) if row else None
            row = conn.execute("SELECT data FROM workflow_records WHERE kind = 'docx_import_candidate' AND id = ?", (candidate_id,)).fetchone()
            locked_candidate = json.loads(row["data"]) if row else None
            if (not current or current.revision != candidate["project_revision"]
                    or project_content_hash(current) != candidate["project_content_sha256"]
                    or not locked_candidate or locked_candidate.get("status") != "preview"
                    or locked_candidate.get("candidate_sha256") != candidate_sha256
                    or locked_candidate.get("reconciliation") != candidate["reconciliation"]
                    or locked_candidate.get("reconciliation_sha256") != candidate["reconciliation_sha256"]
                    or locked_candidate.get("inventory_version") != candidate["inventory_version"]
                    or locked_candidate.get("inventory_sha256") != candidate["inventory_sha256"]
                    or locked_candidate.get("unresolved") != candidate["unresolved"]
                    or locked_candidate.get("revision") != candidate.get("revision")):
                raise HTTPException(status_code=409, detail="草稿或导入候选已变化，请重新预览")
            before_version = _import_version(original, user["id"], "before_docx_import", candidate_id)
            store.save_workflow_record_conn(conn, "project_version", before_version)
            saved = store.update_project_if_revision_conn(conn, proj, candidate["project_revision"])
            if saved is None:
                raise HTTPException(status_code=409, detail="草稿已变化，请重新预览")
            applied_reconciliation = reconcile_applied_docx_import(imported, saved, merge.get("source_section_mapping", []))
            if (applied_reconciliation["status"] != "complete"
                    or applied_reconciliation["candidate_sha256"] != candidate["candidate_sha256"]):
                raise HTTPException(status_code=409, detail={
                    "message": "保存后的导入内容对账失败，已回滚原草稿", "reconciliation": applied_reconciliation,
                })
            after_version = _import_version(saved, user["id"], "after_docx_import", candidate_id)
            store.save_workflow_record_conn(conn, "project_version", after_version)
            locked_candidate["status"] = "applied"
            locked_candidate["before_version_id"] = before_version["id"]
            locked_candidate["after_version_id"] = after_version["id"]
            locked_candidate["applied_reconciliation"] = applied_reconciliation
            locked_candidate["source_mapping"] = applied_reconciliation["mappings"]
            if store.save_workflow_record_conn(conn, "docx_import_candidate", locked_candidate,
                                               expected_revision=locked_candidate["revision"]) is None:
                raise HTTPException(status_code=409, detail="导入候选已变化，请重新预览")
    except Exception as exc:
        for path in reversed(created_paths):
            path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=422, detail=f"论文正文应用失败: {exc}") from exc
    applied_preview = dict(preview)
    warnings = list(applied_preview.get("warnings") or [])
    if merge.get("flattened_section_count"):
        warnings.append(
            f"{merge['flattened_section_count']} 个源文档标题未在冻结母版中找到，已保留在对应章节正文中："
            + "、".join(merge.get("flattened_section_titles") or [])
        )
    if merge.get("normalized_source_table_count"):
        warnings.append("源文档表格已按母版列宽和文字样式重建，原文档表格 XML 样式与关系未直接带入")
    applied_preview["warnings"] = list(dict.fromkeys(warnings))
    return applied_preview | {"applied": True, "project": saved, "merge": merge,
                              "applied_reconciliation": applied_reconciliation,
                              "before_version_id": before_version["id"], "after_version_id": after_version["id"]}


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
    originals = {}
    old_blocks = {(section.key, block.id): block for section in current.sections for block in section.paragraphs}
    for section in current.sections:
        for block in section.paragraphs:
            if block.reference and block.reference.original_pasted_text is not None:
                if block.id in originals:
                    raise HTTPException(status_code=422, detail="现有文献内容块 ID 重复，无法安全恢复草稿")
                originals[block.id] = block.reference.original_pasted_text
    restored_originals = set()
    for section, raw_section in zip(sections, raw_sections):
        for block, raw_block in zip(section.paragraphs, raw_section.get("paragraphs", [])):
            prior = old_blocks.get((section.key, block.id))
            if block.id in originals:
                if block.id in restored_originals:
                    raise HTTPException(status_code=422, detail="本地草稿中的文献内容块 ID 重复")
                restored_originals.add(block.id)
                if block.type != BlockType.PARAGRAPH or block.reference is None:
                    raise HTTPException(status_code=422, detail="请删除该条文献后再移除其粘贴原文或更换内容类型")
                block.reference.original_pasted_text = originals[block.id]
            elif block.reference is not None:
                original = _saved_reference_source(
                    prior, include_legacy=section.type == SectionType.REFERENCES.value or section.key == "references",
                )
                if original is not None:
                    try:
                        block.reference = ReferenceEntry.model_validate({
                            **block.reference.model_dump(), "original_pasted_text": original,
                        })
                    except ValidationError as exc:
                        raise HTTPException(status_code=422, detail="原有文献原文过长，无法转换为结构化文献") from exc
            if block.reference is not None:
                raw_reference = raw_block.get("reference") if isinstance(raw_block, dict) else None
                reconcile_reference_language(
                    block.reference, prior.reference if prior else None,
                    provided=isinstance(raw_reference, dict) and "language_fact" in raw_reference,
                )
                reconcile_reference_year(
                    block.reference, prior.reference if prior else None,
                    provided=isinstance(raw_reference, dict) and "year_fact" in raw_reference,
                )
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
    referenced_versions = [item for item in store.list_workflow_records("project_version", limit=None) if item.get("project_id") == proj_id]
    submitted_version_ids = {
        item.get("project_version_id") for item in store.list_workflow_records("submission", limit=None)
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

    expected_hash = project_content_hash(proj)
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
        _save_project_snapshots(proj, store, expected_hash)
    return effective_template


def _ensure_requirement_snapshot(proj: WritingProject, tpl: PaperTemplate, store: Store) -> dict:
    """为旧项目补建规范快照；新项目始终在创建时生成。"""
    expected_hash = project_content_hash(proj)
    if proj.requirement_snapshot:
        hydrated = hydrate_snapshot_format_overrides(proj.requirement_snapshot)
        if hydrated != proj.requirement_snapshot:
            proj.requirement_snapshot = hydrated
            _save_project_snapshots(proj, store, expected_hash)
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
    _save_project_snapshots(proj, store, expected_hash)
    return proj.requirement_snapshot


def _save_project_snapshots(proj: WritingProject, store: Store, expected_hash: str) -> None:
    saved = store.update_project_snapshots(
        proj.id, proj.revision, expected_hash,
        template_snapshot=proj.template_snapshot,
        requirement_snapshot=proj.requirement_snapshot,
    )
    if saved is None:
        raise HTTPException(status_code=409, detail="论文或规范已变化，请刷新后重试")
    proj.updated_at = saved.updated_at
    proj.check_run_ids = saved.check_run_ids


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
    proj.layout_template_snapshot = None
    # 补齐新模板的封面字段, 已填写的值保留
    for field in tpl.meta_fields:
        proj.meta.setdefault(field.key, "")
    return proj


@router.post("/{proj_id}/template", dependencies=[Depends(feature_flags.require_template_migration)])
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
    ensure_template_admitted_for_writing(tpl)
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
    saved = store.update_project_if_revision(proj, proj.revision)
    if saved is None:
        raise HTTPException(status_code=409, detail="项目已被更新，请刷新后重新更换模板")
    return saved


@router.post("/{proj_id}/refresh-template", dependencies=[Depends(feature_flags.require_template_migration)])
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
    ensure_template_admitted_for_writing(tpl)
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
    saved = store.update_project_if_revision(proj, proj.revision)
    if saved is None:
        raise HTTPException(status_code=409, detail="项目已被更新，请刷新后重新套用模板")
    return saved


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


def _export_preflight(
    proj: WritingProject, tpl: PaperTemplate, store: Store, actor_id: str,
) -> tuple[list[str], list[str]]:
    """Separate actionable blockers from hard rules that require artifact review."""
    from app.services.references import validate_reference_numbers
    validate_reference_numbers(proj)
    blocking = validate_project_completeness(proj, tpl)
    from app.services.project_check import check_project as run_check
    report = run_check(proj, tpl, pack=None, store=store, performed_by=actor_id)
    blocking.extend(
        f"{finding.get('source', '规范要求')}: {finding['message']}"
        for finding in report.get("findings", [])
        if finding.get("source_type") == "requirement"
        and finding.get("enforce") == "hard"
        and finding.get("severity") == "error"
    )
    manual_checks = [
        f"{item.get('source', '规范要求')}: {item.get('description') or '必需规范无法自动核验'}"
        for item in report.get("not_checked", [])
        if item.get("enforce") == "hard"
    ]
    return blocking, manual_checks


@router.post("/{proj_id}/export-jobs", status_code=202)
def create_project_export_job(proj_id: str, force: bool = False,
                              store: Store = Depends(get_store),
                              user: dict = Depends(current_user)) -> dict:
    """Queue a version-frozen DOCX export for the standalone worker."""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    feature_flags.require_project_delivery(store, proj.owner_id)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    tpl = _project_template(proj, store)
    force = force and can_manage(user)
    missing, manual_checks = _export_preflight(proj, tpl, store, user["id"])
    if missing:
        raise HTTPException(status_code=409, detail={"message": "请先补全必填内容或修复自动检查失败项", "missing": missing})
    from app.services.export_jobs import ExportRequestLimitExceeded, create_export_job, export_job_view
    from app.services.export_time_budget import InvalidExportBudget
    try:
        return export_job_view(create_export_job(
            store, proj, tpl, force=force, actor_id=user["id"],
            requirement_manual_checks=manual_checks,
        ))
    except ExportRequestLimitExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidExportBudget as exc:
        raise HTTPException(status_code=503, detail="导出服务配置暂不可用，请联系系统团队") from exc


@router.get("/{proj_id}/export-jobs")
def list_project_export_jobs(proj_id: str, store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> list[dict]:
    """List export attempts for a project without exposing content snapshots."""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    from app.services.export_jobs import export_identity, export_job_view
    current_identity = export_identity(proj)
    return [
        export_job_view(item, current_identity=current_identity, check_current=True)
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
    from app.services.export_jobs import export_identity, export_job_view
    return export_job_view(record, current_identity=export_identity(proj), check_current=True)


@router.post("/{proj_id}/export-jobs/{export_id}/retry")
def retry_project_export_job(proj_id: str, export_id: str, store: Store = Depends(get_store),
                             user: dict = Depends(current_user)) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    feature_flags.require_project_delivery(store, proj.owner_id)
    # 导出失败时作者需要自救；教师与管理员保留原有的跨项目重试权限。
    if not can_manage(user) and proj.owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="仅项目所有者或教师、管理员可以重试导出任务")
    from app.services.export_jobs import park_expired_project_exports
    park_expired_project_exports(store)
    record = store.get_workflow_record("project_export", export_id)
    if not record or record.get("project_id") != proj_id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    if record.get("error_code") in {"PROJECT_EXPORT_DEADLINE_EXCEEDED", "PROJECT_EXPORT_DEADLINE_INVALID"}:
        raise HTTPException(status_code=409, detail="该导出任务已超过时限或时间记录无效，请重新发起导出")
    if record.get("status") not in {"failed", "queued"}:
        raise HTTPException(status_code=409, detail="当前导出任务不可重试")
    _assert_current_export_project(record, proj)
    from app.domain.workflow_states import transition_state
    from app.repositories.workflow_repository import WorkflowRepository
    repository = WorkflowRepository(store)
    with store.workflow_transaction() as conn:
        queue_job = repository.get_job_in_transaction(conn, record.get("job_id", ""))
        if (queue_job is None or queue_job["job_type"] != "project.export"
                or queue_job["resource_type"] != "project_export" or queue_job["resource_id"] != export_id
                or queue_job["state"] not in {"failed", "queued"}):
            raise HTTPException(status_code=409, detail="导出任务队列状态不允许重试")
        if queue_job["attempt_count"] >= queue_job["max_attempts"]:
            raise HTTPException(status_code=409, detail="该导出任务已达到重试次数上限，请联系管理员处理")
        from app.services.export_time_budget import InvalidExportBudget, parse_export_time, recorded_export_deadline
        try:
            deadline_at, derived = recorded_export_deadline(record)
            if parse_export_time(deadline_at) <= datetime.now(timezone.utc):
                raise HTTPException(status_code=409, detail="该导出任务已超过时限，请重新发起导出")
        except InvalidExportBudget as exc:
            raise HTTPException(status_code=409, detail="该导出任务的时间记录无效，请重新发起导出") from exc
        if derived:
            record["deadline_at"] = deadline_at
            record["deadline_origin"] = "legacy_created_at"
        if record.get("status") == "failed":
            record["status"] = transition_state("project_export", "failed", "queued")
            record["error_code"] = ""
            saved = repository.save_in_transaction(
                conn, "project_export", record, expected_revision=record.get("revision", 0)
            )
            if saved is None:
                raise HTTPException(status_code=409, detail="导出任务已被其他操作更新，请刷新后重试")
            record = saved
        elif derived:
            saved = repository.save_in_transaction(
                conn, "project_export", record, expected_revision=record.get("revision", 0)
            )
            if saved is None:
                raise HTTPException(status_code=409, detail="导出任务已被其他操作更新，请刷新后重试")
            record = saved
        job = repository.store.retry_job_conn(
            conn, record.get("job_id", ""), "用户手动重试项目导出", preserve_attempts=True,
        )
        if job is None:
            raise HTTPException(status_code=409, detail="导出任务队列状态不允许重试")
        store.save_audit_event_conn(conn, {
            "id": new_id("audit"),
            "actor_id": user["id"],
            "action": "project.export.retry_requested",
            "resource_type": "project_export",
            "resource_id": export_id,
            "result": "success",
            "reason": f"attempts:{job['attempt_count']}/{job['max_attempts']}",
        })
    from app.services.export_jobs import export_job_view
    return export_job_view(record)


def _assert_current_export_project(record: dict, project: WritingProject | None) -> None:
    from app.services.export_jobs import export_identity, export_job_is_current

    if project is None or not export_job_is_current(record, export_identity(project)):
        raise HTTPException(status_code=409, detail="论文内容已变化或模板已变化，该导出文件已失效，请重新导出并核查")


def _verified_project_export_output(record: dict, proj_id: str, project: WritingProject) -> tuple[bytes, dict]:
    """Use one independent artifact check for review and final download."""
    if record.get("status") != "succeeded" or not record.get("file_path"):
        raise HTTPException(status_code=409, detail="导出尚未完成")
    from app.services.export_jobs import EXPORT_DIR
    path = Path(record["file_path"])
    if path.parent != EXPORT_DIR or not path.is_file():
        raise HTTPException(status_code=404, detail="导出制品不存在")
    data = path.read_bytes()
    if not verify_report_binding(record.get("output_check"), data):
        raise HTTPException(status_code=409, detail="导出制品检查凭据缺失或与当前文件不匹配，请重新导出")
    try:
        snapshot_project = WritingProject.model_validate(record.get("project_snapshot") or {})
        snapshot_template = PaperTemplate.model_validate(record.get("template_snapshot") or {})
    except Exception as exc:
        raise HTTPException(status_code=409, detail="导出任务冻结快照缺失或无效，请重新导出") from exc
    if (snapshot_project.id != proj_id or record.get("project_id") != proj_id
            or snapshot_project.template_id != snapshot_template.id
            or record.get("content_hash") != project_content_hash(snapshot_project)
            or record.get("project_revision") != snapshot_project.revision):
        raise HTTPException(status_code=409, detail="导出任务与冻结项目身份不匹配，请重新导出")
    _assert_current_export_project(record, project)
    heading_issues = validate_heading_hierarchy(snapshot_project.sections)
    if heading_issues:
        raise HTTPException(status_code=409, detail={"message": "标题层级有误，请修复后重新导出", "missing": heading_issues})
    snapshot_source = resolve_template_source(snapshot_template.source_docx) if snapshot_template.source_docx else None
    snapshot_source_bytes = source_template_bytes(snapshot_source)
    if str(record.get("source_template_sha256") or "") != str((record.get("output_check") or {}).get("source_template_sha256") or ""):
        raise HTTPException(status_code=409, detail="导出报告与任务发起时的源模板摘要不匹配，请重新导出")
    expected_identity = expected_output_check_identity(snapshot_project, snapshot_template, snapshot_source_bytes)
    if not verify_report_identity(record.get("output_check"), expected_identity):
        raise HTTPException(status_code=409, detail="导出报告与任务冻结项目/模板快照不匹配，请重新导出")
    if not verify_source_template_binding(record.get("output_check"), snapshot_source_bytes):
        raise HTTPException(status_code=409, detail="导出模板源文件已变化或检查凭据无效，请重新导出")
    try:
        fresh_report = require_docx_output_pass(
            data, snapshot_project, snapshot_template,
            source_template_bytes=snapshot_source_bytes,
            context_kind="paper-async",
        )
        from app.services.export_jobs import bind_requirement_manual_checks
        fresh_report = bind_requirement_manual_checks(
            fresh_report, record.get("requirement_manual_checks")
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail="导出制品未通过冻结快照复核，请重新导出") from exc
    if output_check_identity(record.get("output_check")) != output_check_identity(fresh_report):
        raise HTTPException(status_code=409, detail="导出检查报告与冻结快照复核不一致，请重新导出")
    if review_identity(record.get("output_check") or {}) != review_identity(fresh_report):
        raise HTTPException(status_code=409, detail="人工核查清单与独立成品复核不一致，请重新导出")
    return data, fresh_report


@router.get("/{proj_id}/export-jobs/{export_id}/download")
def download_project_export(proj_id: str, export_id: str, format: Literal["docx", "pdf"] = "docx",
                            store: Store = Depends(get_store),
                            user: dict = Depends(current_user)) -> Response:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    feature_flags.require_project_delivery(store, proj.owner_id)
    record = store.get_workflow_record("project_export", export_id)
    if not record or record.get("project_id") != proj_id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    data, report = _verified_project_export_output(record, proj_id, proj)
    _assert_current_export_project(record, store.get_project(proj_id))
    if review_required(report):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OUTPUT_CHECK_AUTOMATION_REQUIRED",
                "message": "该模板的自动版面检查尚未完成，请联系系统团队适配后重新导出。",
                "manual_review": review_view(report, None),
            },
        )
    filename = record.get("filename") or "paper.docx"
    if format == "pdf":
        from app.services.word_optional_pdf import WordPdfError, create_optional_pdf, word_pdf_available
        if not word_pdf_available():
            raise HTTPException(status_code=503, detail="PDF 转换暂不可用，Word 文件仍可下载")
        try:
            pdf, evidence = create_optional_pdf(data)
        except (WordPdfError, ProfileError) as exc:
            raise HTTPException(status_code=503, detail="PDF 生成或检查失败，Word 文件仍可下载") from exc
        current_project = store.get_project(proj_id)
        if current_project is None:
            raise HTTPException(status_code=409, detail="论文已变化，请重新导出")
        assert_resource_read(store, user, "project", current_project)
        feature_flags.require_project_delivery(store, current_project.owner_id)
        current_record = store.get_workflow_record("project_export", export_id)
        if current_record != record:
            raise HTTPException(status_code=409, detail="导出任务已变化，请重新导出")
        current_data, current_report = _verified_project_export_output(current_record, proj_id, current_project)
        if (current_data != data or review_required(current_report)
                or evidence.get("source_sha256") != hashlib.sha256(data).hexdigest()
                or evidence.get("pdf_sha256") != hashlib.sha256(pdf).hexdigest()):
            raise HTTPException(status_code=409, detail="PDF 与 Word 成品不匹配，请重新导出")
        from urllib.parse import quote
        return Response(
            content=pdf, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(Path(filename).stem + '.pdf')}",
                     "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                     "X-PaperGuard-Source-SHA256": evidence["source_sha256"],
                     "X-PaperGuard-PDF-SHA256": evidence["pdf_sha256"]},
        )
    from urllib.parse import quote
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/{proj_id}/export-jobs/{export_id}/manual-review")
def confirm_project_export_manual_review(
    proj_id: str,
    export_id: str,
    request: ManualOutputReviewRequest,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    feature_flags.require_project_delivery(store, proj.owner_id)
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="仅教师或管理员可以确认 Word/WPS 成品核查")
    record = store.get_workflow_record("project_export", export_id)
    if not record or record.get("project_id") != proj_id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    _verified_project_export_output(record, proj_id, proj)
    raise HTTPException(status_code=409, detail={
        "code": "OUTPUT_CHECK_AUTOMATION_REQUIRED",
        "message": "人工确认不能替代自动版面检查，请联系系统团队适配该模板。",
    })


@router.get("/{proj_id}/export-jobs/{export_id}/review-candidate")
def download_project_export_review_candidate(
    proj_id: str,
    export_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> Response:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    feature_flags.require_project_delivery(store, proj.owner_id)
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="仅教师或管理员可以下载 Word/WPS 人工核查候选")
    record = store.get_workflow_record("project_export", export_id)
    if not record or record.get("project_id") != proj_id or record.get("status") != "succeeded":
        raise HTTPException(status_code=404, detail="导出核查候选不存在")
    _verified_project_export_output(record, proj_id, proj)
    _assert_current_export_project(record, store.get_project(proj_id))
    raise HTTPException(status_code=409, detail={
        "code": "OUTPUT_CHECK_AUTOMATION_REQUIRED",
        "message": "该模板的自动版面检查尚未完成，请联系系统团队适配后重新导出。",
    })


@router.post("/{proj_id}/export")
def export_docx(proj_id: str, force: bool = False,
                store: Store = Depends(get_store),
                user: dict = Depends(current_user)):
    """按模板导出为 .docx, 下载新文件。

    导出前校验必填章节与封面字段; 未完成时返回 409 与缺失清单,
    force 仅记录教师/管理员的试验请求，不绕过任何成品交付门禁。
    """
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_resource_read(store, user, "project", proj)
    feature_flags.require_project_delivery(store, proj.owner_id)
    tpl = _project_template(proj, store)
    _ensure_requirement_snapshot(proj, tpl, store)
    tpl = _project_template(proj, store)

    force = force and can_manage(user)
    missing, manual_checks = _export_preflight(proj, tpl, store, user["id"])
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"message": "存在必需项未完成或待核查，无法交付 DOCX", "missing": missing},
        )
    if manual_checks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OUTPUT_CHECK_AUTOMATION_REQUIRED",
                "message": "该模板的自动规范检查尚未完成，请联系系统团队适配后重新导出。",
                "manual_checks": manual_checks,
            },
        )

    out_dir = Path(tempfile.mkdtemp(prefix="paperguard-export-"))
    out = out_dir / "paper.docx"
    try:
        source_path = resolve_template_source(tpl.source_docx) if tpl.source_docx else None
        if tpl.source_docx and source_path is None:
            raise HTTPException(status_code=409, detail="冻结母版源文件缺失，请重新导入并确认模板")
        export_project_to_docx(proj, tpl, str(out), template_path=source_path)
    except HTTPException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    except ProfileError as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(status_code=409, detail=f"冻结母版无法导出：{exc}") from exc
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    try:
        output_data = out.read_bytes()
        report = require_docx_output_pass(
            output_data, proj, tpl,
            source_template_bytes=source_template_bytes(source_path),
            context_kind="paper-sync",
        )
    except DocxOutputCheckError as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(status_code=409, detail={"message": "生成文件未通过必需的 DOCX 成品检查", "output_check": safe_output_check_diagnostics(exc.report)}) from exc
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(status_code=409, detail={"message": "DOCX 成品检查无法完成，未提供文件", "output_check": safe_output_check_diagnostics(None)}) from exc
    if review_view(report, None)["status"] == "required":
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OUTPUT_CHECK_AUTOMATION_REQUIRED",
                "message": "该模板的自动版面检查尚未完成，请联系系统团队适配后重新导出。",
                "manual_review": review_view(report, None),
            },
        )
    safe_title = (proj.title or "paper").replace("/", "_").replace("\\", "_")
    # 中文文件名用 RFC 5987 filename* 编码, 避免 header 编码错误
    from urllib.parse import quote
    filename_ascii = "paper.docx"
    shutil.rmtree(out_dir, ignore_errors=True)
    return Response(
        content=output_data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename_ascii}"; filename*=UTF-8\'\'{quote(f"{safe_title}.docx")}',
            "X-PaperGuard-Output-Check": report["status"],
            "X-PaperGuard-Output-Check-Count": str(report["check_count"]),
            "X-PaperGuard-Output-SHA256": report["output_sha256"],
            "X-PaperGuard-Inspector-Version": report["inspector_version"],
            "X-PaperGuard-Project-Revision": str(report["project_revision"]),
            "X-PaperGuard-Project-Content-SHA256": report["project_content_hash"],
            "X-PaperGuard-Template-SHA256": report["template_hash"],
            "X-PaperGuard-Source-Template-SHA256": report["source_template_sha256"] or "none",
        },
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
    feature_flags.require_project_delivery(store, proj.owner_id)
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
        "can_export": not missing, "force": force,
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
