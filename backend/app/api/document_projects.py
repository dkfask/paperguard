"""Dynamic generic document project API."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth import can_manage, current_user
from app.core.feature_flags import m1_pilot_active, require_general_document_platform, require_generic_delivery
from app.db import Store, new_id
from app.domain.documents.hashing import canonical_hash, document_project_content_hash
from app.domain.documents.schemas import Artifact, ContentBlock, DocumentPackage, DocumentProject, DocumentTemplate, TemplateVersion, ValidationRun
from app.domain.documents.specs import SpecVersion
from app.services.document_project_engine import (
    DocumentProjectError,
    editor_schema,
    instantiate_document_project,
    validate_field_values,
    validate_section_content,
)
from app.services.document_validation_engine import required_delivery_issues, validate_document_project
from app.services.document_render_jobs import (
    create_document_render_job,
    get_document_artifact,
    get_document_render_job,
    render_job_view,
    read_document_artifact,
    resolve_document_artifact,
)
from app.services.document_access import (
    audit_document_access_denied,
    can_access_document_project,
    document_package_visible,
    public_document_bundle,
)
from app.services.generic_docx_renderer import (
    GenericDocxRenderError,
    require_document_project_docx_output,
    render_document_project_to_docx,
)
from app.services.docx_output_check import (
    DocxOutputCheckError,
    safe_output_check_diagnostics,
    source_template_bytes,
    verify_report_binding,
    verify_report_identity,
    verify_source_template_binding,
)
from app.services.manual_output_review import confirm_review, review_identity, review_matches, review_required, review_view
from app.services.template_source import resolve_template_source
from app.services.document_assets import document_upload_dir
from app.services.document_export_preflight import run_document_export_preflight
from app.services.upload_security import IMAGE_MAX_BYTES, read_limited, validate_image
from app.repositories.workflow_repository import WorkflowRepository
from app.domain.workflow_states import transition_state

router = APIRouter(
    prefix="/api/document-projects",
    tags=["document-projects"],
    dependencies=[Depends(require_general_document_platform)],
)


class CreateDocumentProjectRequest(BaseModel):
    package_id: str = Field(min_length=1)
    title: str = Field(default="", max_length=300)
    field_values: dict[str, Any] = Field(default_factory=dict)


class UpdateDocumentProjectRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    field_values: dict[str, Any] | None = None
    sections: dict[str, list[ContentBlock]] | None = None
    base_revision: int | None = Field(default=None, ge=0)


class ManualOutputReviewRequest(BaseModel):
    output_sha256: str = Field(min_length=64, max_length=64)
    confirmed_check_ids: list[str] = Field(min_length=1)


def get_store() -> Store:
    return Store()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _package_visible(store: Store, bundle: dict[str, Any], user: dict) -> bool:
    return document_package_visible(store, user, bundle["package"])


def _ensure_project_access(store: Store, project: DocumentProject, user: dict) -> None:
    if not can_access_document_project(store, user, project):
        audit_document_access_denied(
            store,
            user,
            resource_type="document_project",
            resource_id=project.id,
            context=project.organization_context,
        )
        raise HTTPException(status_code=404, detail="项目不存在或无权访问")


def _project_package(store: Store, project: DocumentProject) -> dict[str, Any]:
    bundle = store.get_document_package_bundle(project.package_id)
    if bundle is None:
        raise HTTPException(status_code=409, detail="项目引用的文档包已失效")
    package = bundle["package"]
    references = (
        package.template_version_id,
        package.spec_version_id,
        package.renderer_version,
        package.rule_engine_version,
    )
    pinned = (
        project.template_version_id,
        project.spec_version_id,
        project.renderer_version,
        project.rule_engine_version,
    )
    if references != pinned:
        raise HTTPException(status_code=409, detail="项目版本快照与文档包不一致")
    return bundle


def _audit(store: Store, user: dict, *, action: str, project_id: str, result: str = "success", reason: str = "") -> None:
    context = user.get("organization_context") or {}
    store.save_audit_event({
        "id": new_id("audit"),
        "actor_id": str(user.get("id") or ""),
        "action": action,
        "resource_type": "document_project",
        "resource_id": project_id,
        "organization_id": str(context.get("school_id") or ""),
        "result": result,
        "reason": reason,
        "created_at": _now(),
    })


def _summary(project: DocumentProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "package_id": project.package_id,
        "document_type_id": project.document_type_id,
        "title": project.title,
        "status": project.status,
        "revision": project.revision,
        "updated_at": project.updated_at,
        "section_count": len(project.sections),
    }


def _create_validation_run(store: Store, user: dict, project: DocumentProject, bundle: dict[str, Any]) -> ValidationRun:
    run = validate_document_project(
        project,
        bundle["template_version"].definition,
        bundle["spec_version"],
        run_id=new_id("validation"),
        performed_by=user["id"],
        created_at=_now(),
        rule_engine_version=project.rule_engine_version,
    )
    try:
        saved = store.save_validation_run(run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="检查记录已存在, 请重试") from exc
    _audit(store, user, action="document.project.validated", project_id=project.id, reason=f"run:{saved.id}")
    return saved


def _ensure_export_preflight(bundle: dict[str, Any], project: DocumentProject) -> dict[str, Any]:
    result = run_document_export_preflight(
        project,
        bundle["package"],
        bundle["template"],
        bundle["template_version"],
    )
    if not result["ok"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "导出前检查未通过",
                "errors": result["errors"],
                "warnings": result["warnings"],
            },
        )
    return result


def _ensure_force_override(user: dict[str, Any], force: bool) -> None:
    if force and not can_manage(user):
        raise HTTPException(status_code=403, detail="只有教师或管理员可以使用导出覆盖")


@router.get("")
def list_document_projects(
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    scoped_listing = user.get("role") in {"admin", "college_admin"}
    projects = store.list_document_projects(owner_id="" if scoped_listing else user["id"])
    return [
        _summary(project)
        for project in projects
        if can_access_document_project(store, user, project)
    ]


@router.post("", status_code=201)
def create_document_project(
    request: CreateDocumentProjectRequest,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> DocumentProject:
    bundle = store.get_document_package_bundle(request.package_id)
    if bundle is None or bundle["package"].status.value != "published" or not _package_visible(store, bundle, user):
        raise HTTPException(status_code=404, detail="文档包不存在或无权使用")
    definition = bundle["template_version"].definition
    try:
        project = instantiate_document_project(
            bundle["package"],
            definition,
            project_id=new_id("docproj"),
            owner_id=user["id"],
            title=request.title.strip() or bundle["template"].name,
            field_values=request.field_values,
            organization_context=dict(user.get("organization_context") or {}),
        )
        saved = store.save_document_project(project)
    except DocumentProjectError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(store, user, action="document.project.created", project_id=saved.id)
    return saved


@router.get("/{project_id}")
def get_document_project(
    project_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> DocumentProject:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    return project


@router.get("/{project_id}/editor")
def get_document_project_editor(
    project_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    bundle = _project_package(store, project)
    public_bundle = public_document_bundle(bundle)
    return {
        "project": project,
        "package": public_bundle["package"],
        "template": public_bundle["template"],
        "template_version": public_bundle["template_version"],
        "spec_version": public_bundle["spec_version"],
        "editor_schema": editor_schema(bundle["template_version"].definition),
        "capabilities": {"formal_delivery_enabled": not m1_pilot_active()},
    }


@router.post("/{project_id}/images", status_code=201)
async def upload_document_image(
    project_id: str,
    file: UploadFile = File(...),
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, str]:
    """Upload a project-owned raster image for a generic document block."""
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    data = read_limited(file, IMAGE_MAX_BYTES)
    kind = validate_image(data, file.filename, require_docx_compatible=True)
    # python-docx accepts PNG/JPEG/GIF for embedded pictures. Keep this path
    # explicit so an unsupported format cannot become a mapping error.
    extension = {"jpeg": ".jpg", "png": ".png", "gif": ".gif"}.get(kind)
    if extension is None:
        raise HTTPException(status_code=400, detail="通用 DOCX 图片仅支持 PNG/JPEG/GIF")
    filename = f"{project.id}-{uuid.uuid4().hex[:12]}{extension}"
    destination = document_upload_dir() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.write_bytes(data)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="图片保存失败") from exc
    url = f"/media/uploads/{filename}"
    _audit(store, user, action="document.project.image_uploaded", project_id=project.id)
    return {"url": url, "filename": filename}


@router.put("/{project_id}")
def update_document_project(
    project_id: str,
    request: UpdateDocumentProjectRequest,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> DocumentProject:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    expected_revision = project.revision if request.base_revision is None else request.base_revision
    if expected_revision != project.revision:
        raise HTTPException(status_code=409, detail={"message": "项目内容已被更新, 请刷新后重试", "revision": project.revision})
    bundle = _project_package(store, project)
    definition = bundle["template_version"].definition
    try:
        if request.field_values is not None:
            validate_field_values(definition, request.field_values)
            project.field_values = dict(request.field_values)
        if request.sections is not None:
            validate_section_content(definition, request.sections, project.id)
            project.sections = {key: list(blocks) for key, blocks in request.sections.items()}
        if request.title is not None:
            project.title = request.title.strip()
        saved = store.update_document_project_if_revision(project, expected_revision)
    except DocumentProjectError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=409, detail={"message": "项目内容已被更新, 请刷新后重试"})
    _audit(store, user, action="document.project.updated", project_id=saved.id)
    return saved


@router.post("/{project_id}/validate")
def validate_project(
    project_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> ValidationRun:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    bundle = _project_package(store, project)
    return _create_validation_run(store, user, project, bundle)


@router.post("/{project_id}/render")
def render_project(
    project_id: str,
    force: bool = False,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> Response:
    """Render a generic project through the existing DOCX export adapter."""
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    require_generic_delivery()
    _ensure_force_override(user, force)
    bundle = _project_package(store, project)
    _ensure_export_preflight(bundle, project)
    run = _create_validation_run(store, user, project, bundle)
    required_issues = required_delivery_issues(run)
    if required_issues:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "存在必需规范问题或待核查项，force 不能绕过成品交付门禁",
                "run_id": run.id,
                "summary": run.summary,
                "missing": required_issues,
            },
        )

    if force:
        _audit(store, user, action="document.project.export.override", project_id=project.id, reason=f"run:{run.id}")

    temp_dir = Path(tempfile.mkdtemp(prefix=f"paperguard-generic-{project.id}-"))
    output = temp_dir / "document.docx"
    try:
        render_document_project_to_docx(
            project,
            bundle["package"],
            bundle["template"],
            bundle["template_version"],
            bundle["spec_version"],
            output,
        )
    except GenericDocxRenderError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    try:
        output_data = output.read_bytes()
        output_check = require_document_project_docx_output(
            output_data, project, bundle["package"], bundle["template"],
            bundle["template_version"], bundle["spec_version"], context_kind="document-sync",
        )
    except DocxOutputCheckError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=409, detail={"message": "生成文件未通过必需的 DOCX 成品检查", "output_check": safe_output_check_diagnostics(exc.report)}) from exc
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=409, detail={"message": "DOCX 成品检查无法完成，未提供文件", "output_check": safe_output_check_diagnostics(None)}) from exc
    if review_view(output_check, None)["status"] == "required":
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "成品需要 Word/WPS 人工核查；请使用异步导出生成核查候选并确认后下载。",
                "manual_review": review_view(output_check, None),
            },
        )
    _audit(store, user, action="document.project.rendered", project_id=project.id, reason=f"run:{run.id}")
    safe_title = (project.title or "document").replace("/", "_").replace("\\", "_").strip()[:120] or "document"
    shutil.rmtree(temp_dir, ignore_errors=True)
    from urllib.parse import quote
    return Response(
        content=output_data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'{safe_title}.docx')}",
            "X-PaperGuard-Output-Check": output_check["status"],
            "X-PaperGuard-Output-Check-Count": str(output_check["check_count"]),
            "X-PaperGuard-Output-SHA256": output_check["output_sha256"],
            "X-PaperGuard-Inspector-Version": output_check["inspector_version"],
            "X-PaperGuard-Project-Content-SHA256": output_check["project_content_hash"],
            "X-PaperGuard-Template-SHA256": output_check["template_hash"],
            "X-PaperGuard-Source-Template-SHA256": output_check["source_template_sha256"] or "none",
        },
    )


def _render_job_for_project(store: Store, project: DocumentProject, render_job_id: str) -> dict[str, Any]:
    record = get_document_render_job(store, render_job_id)
    if not record or record.get("project_id") != project.id:
        raise HTTPException(status_code=404, detail="渲染任务不存在")
    return record


def _verified_render_output(store: Store, project_id: str, record: dict) -> tuple[Artifact, bytes, dict]:
    """Recheck output against the same frozen inputs at every delivery boundary."""
    if record.get("status") != "succeeded" or not record.get("artifact_id"):
        raise HTTPException(status_code=409, detail="渲染产物尚未完成")
    artifact = get_document_artifact(store, record["artifact_id"])
    if artifact is None or artifact.project_id != project_id or artifact.render_job_id != record.get("id"):
        raise HTTPException(status_code=404, detail="渲染产物不存在")
    data = read_document_artifact(artifact)
    if data is None or artifact.checksum != artifact.output_check.get("output_sha256") or not verify_report_binding(artifact.output_check, data, allow_previous_inspector=True):
        raise HTTPException(status_code=409, detail="核查候选与成品摘要不一致")
    snapshot = record.get("snapshot") or {}
    if record.get("snapshot_hash") != canonical_hash(snapshot):
        raise HTTPException(status_code=409, detail="渲染任务冻结快照校验失败")
    try:
        snapshot_project = DocumentProject.model_validate(snapshot["project"])
        snapshot_package = DocumentPackage.model_validate(snapshot["package"])
        snapshot_template = DocumentTemplate.model_validate(snapshot["template"])
        snapshot_template_version = TemplateVersion.model_validate(snapshot["template_version"])
        snapshot_spec_version = SpecVersion.model_validate(snapshot["spec_version"])
        if snapshot_project.id != project_id or record.get("project_id") != project_id:
            raise ValueError("project identity mismatch")
        current_project = store.get_document_project(project_id)
        if current_project is None:
            raise ValueError("current project is missing")
        if (
            current_project.revision != snapshot_project.revision
            or document_project_content_hash(current_project)
            != document_project_content_hash(snapshot_project)
        ):
            raise ValueError("rendered output is stale for the current project")
        if (
            snapshot_project.package_id != snapshot_package.id
            or snapshot_project.template_version_id != snapshot_package.template_version_id
            or snapshot_project.spec_version_id != snapshot_package.spec_version_id
            or snapshot_template_version.id != snapshot_package.template_version_id
            or snapshot_spec_version.id != snapshot_package.spec_version_id
            or snapshot_template_version.template_id != snapshot_template.id
            or snapshot_project.renderer_version != snapshot_package.renderer_version
            or snapshot_project.rule_engine_version != snapshot_package.rule_engine_version
        ):
            raise ValueError("frozen package version references mismatch")
        fresh_report = require_document_project_docx_output(
            data, snapshot_project, snapshot_package, snapshot_template,
            snapshot_template_version, snapshot_spec_version, context_kind="document-async",
        )
        if not verify_report_identity(artifact.output_check, fresh_report,
                                      allow_previous_inspector=True):
            raise ValueError("output report identity mismatch")
        source_ref = snapshot_template_version.definition.source_docx or ""
        source_path = resolve_template_source(source_ref) if source_ref else None
        source = source_template_bytes(source_path)
        if snapshot.get("source_template_sha256") != (hashlib.sha256(source).hexdigest() if source is not None else ""):
            raise ValueError("source template hash mismatch")
        if not verify_source_template_binding(artifact.output_check, source):
            raise ValueError("source template binding mismatch")
    except Exception as exc:
        raise HTTPException(status_code=409, detail="核查候选未通过冻结快照和源模板复核") from exc
    if review_identity(artifact.output_check) != review_identity(fresh_report):
        raise HTTPException(status_code=409, detail="人工核查清单与独立成品复核不一致，请重新导出")
    return artifact, data, fresh_report


@router.post("/{project_id}/render-jobs", status_code=202)
def create_render_job(
    project_id: str,
    force: bool = False,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Queue a version-frozen generic DOCX render request."""
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    require_generic_delivery()
    _ensure_force_override(user, force)
    bundle = _project_package(store, project)
    _ensure_export_preflight(bundle, project)
    run = _create_validation_run(store, user, project, bundle)
    required_issues = required_delivery_issues(run)
    if required_issues:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "存在必需规范问题或待核查项，force 不能绕过成品交付门禁",
                "run_id": run.id,
                "summary": run.summary,
                "missing": required_issues,
            },
        )

    if force:
        _audit(store, user, action="document.project.export.override", project_id=project.id, reason=f"run:{run.id}")
    record = create_document_render_job(
        store,
        project,
        bundle,
        force=force,
        actor_id=user["id"],
    )
    artifact = get_document_artifact(store, record.get("artifact_id", "")) if record.get("artifact_id") else None
    return render_job_view(record, artifact)


@router.get("/{project_id}/render-jobs")
def list_render_jobs(
    project_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    records = [
        item for item in store.list_workflow_records("document_render_job")
        if item.get("project_id") == project.id
    ]
    return [
        render_job_view(
            item,
            get_document_artifact(store, item.get("artifact_id", "")) if item.get("artifact_id") else None,
        )
        for item in records
    ]


@router.get("/{project_id}/render-jobs/{render_job_id}")
def get_render_job(
    project_id: str,
    render_job_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    record = _render_job_for_project(store, project, render_job_id)
    artifact = get_document_artifact(store, record.get("artifact_id", "")) if record.get("artifact_id") else None
    return render_job_view(record, artifact)


@router.post("/{project_id}/render-jobs/{render_job_id}/cancel")
def cancel_render_job(
    project_id: str,
    render_job_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Cancel a queued or in-flight render while retaining its evidence."""
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    record = _render_job_for_project(store, project, render_job_id)
    if record.get("status") not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="当前渲染任务不可取消")
    repository = WorkflowRepository(store)
    now = _now()
    record["status"] = transition_state("document_render_job", record["status"], "cancelled")
    record["error_code"] = "DOCUMENT_RENDER_CANCELLED"
    record["last_error"] = "用户取消渲染任务"
    record["cancelled_at"] = now
    with store.workflow_transaction() as conn:
        saved = repository.save_in_transaction(
            conn,
            "document_render_job",
            record,
            expected_revision=record.get("revision", 0),
        )
        if saved is None:
            raise HTTPException(status_code=409, detail="渲染任务已被其他操作更新, 请刷新后重试")
        repository.cancel_job_in_transaction(conn, record.get("job_id", ""), "通用文档渲染已取消")
        store.save_audit_event_conn(conn, {
            "id": f"audit-{render_job_id}-cancelled",
            "actor_id": user["id"],
            "action": "document.project.render.cancelled",
            "resource_type": "document_render_job",
            "resource_id": render_job_id,
            "organization_id": str((project.organization_context or {}).get("school_id") or ""),
            "result": "success",
            "created_at": now,
        })
        record = saved
    return render_job_view(record)


@router.post("/{project_id}/render-jobs/{render_job_id}/retry")
def retry_render_job(
    project_id: str,
    render_job_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="仅教师或管理员可以重试渲染任务")
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    require_generic_delivery()
    record = _render_job_for_project(store, project, render_job_id)
    if record.get("status") not in {"failed", "queued"}:
        raise HTTPException(status_code=409, detail="当前渲染任务不可重试")
    repository = WorkflowRepository(store)
    with store.workflow_transaction() as conn:
        if record.get("status") == "failed":
            record["status"] = transition_state("document_render_job", "failed", "queued")
            record["error_code"] = ""
            record["last_error"] = ""
            record["artifact_id"] = ""
            saved = repository.save_in_transaction(
                conn,
                "document_render_job",
                record,
                expected_revision=record.get("revision", 0),
            )
            if saved is None:
                raise HTTPException(status_code=409, detail="渲染任务已被其他操作更新, 请刷新后重试")
            record = saved
        job = repository.retry_job_conn(conn, record.get("job_id", ""), "管理员手动重试通用文档渲染")
        if job is None:
            if repository.get_job_in_transaction(conn, record.get("job_id", "")) is not None:
                raise HTTPException(status_code=409, detail="渲染任务队列状态不允许重试")
            repository.create_job_in_transaction(
                conn,
                job_id=record.get("job_id") or new_id("job"),
                job_type="document.render",
                resource_type="document_render_job",
                resource_id=record["id"],
                max_attempts=5,
            )
    artifact = get_document_artifact(store, record.get("artifact_id", "")) if record.get("artifact_id") else None
    return render_job_view(record, artifact)


@router.get("/{project_id}/render-jobs/{render_job_id}/artifact")
def download_render_artifact(
    project_id: str,
    render_job_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> Response:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    require_generic_delivery()
    record = _render_job_for_project(store, project, render_job_id)
    artifact, data, report = _verified_render_output(store, project_id, record)
    if not review_matches(report, artifact.manual_review):
        raise HTTPException(
            status_code=409,
            detail={"message": "该成品尚未完成绑定当前文件的 Word/WPS 人工核查", "manual_review": review_view(report, artifact.manual_review)},
        )
    from urllib.parse import quote
    return Response(
        content=data,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.filename)}"},
    )


@router.post("/{project_id}/render-jobs/{render_job_id}/manual-review")
def confirm_render_manual_review(
    project_id: str,
    render_job_id: str,
    request: ManualOutputReviewRequest,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    require_generic_delivery()
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="仅教师或管理员可以确认 Word/WPS 成品核查")
    record = _render_job_for_project(store, project, render_job_id)
    artifact, data, report = _verified_render_output(store, project_id, record)
    try:
        receipt = confirm_review(
            report,
            output_sha256=request.output_sha256,
            confirmed_check_ids=request.confirmed_check_ids,
            reviewer_id=user["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = artifact.model_copy(update={"manual_review": receipt})
    stored_artifact = store.get_workflow_record("document_artifact", artifact.id)
    repository = WorkflowRepository(store)
    saved = repository.save("document_artifact", updated.model_dump(mode="json"), expected_revision=stored_artifact.get("revision", 0))
    if saved is None:
        raise HTTPException(status_code=409, detail="成品核查记录已更新，请刷新后重试")
    _audit(store, user, action="document.project.output.manual_reviewed", project_id=project_id, reason=f"artifact:{artifact.id};sha256:{receipt['output_sha256']}")
    return render_job_view(record, updated)


@router.get("/{project_id}/render-jobs/{render_job_id}/review-candidate")
def download_render_review_candidate(
    project_id: str,
    render_job_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> Response:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    require_generic_delivery()
    if not can_manage(user):
        raise HTTPException(status_code=403, detail="仅教师或管理员可以下载核查候选")
    record = _render_job_for_project(store, project, render_job_id)
    artifact, data, report = _verified_render_output(store, project_id, record)
    if not review_required(report):
        raise HTTPException(status_code=409, detail="当前成品没有待确认的 Office 人工核查项")
    from urllib.parse import quote
    return Response(
        content=data,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.filename.rsplit('.', 1)[0] + '.review-candidate.docx')}",
            "X-PaperGuard-Review-Candidate": "true",
            "X-PaperGuard-Output-SHA256": report.get("output_sha256", ""),
        },
    )


@router.get("/{project_id}/validations")
def list_project_validations(
    project_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[ValidationRun]:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    return store.list_validation_runs(project_id)


@router.get("/{project_id}/validations/{run_id}")
def get_project_validation(
    project_id: str,
    run_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> ValidationRun:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    run = store.get_validation_run(run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="检查记录不存在")
    return run


__all__ = ["router"]
