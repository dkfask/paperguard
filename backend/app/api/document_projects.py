"""Dynamic generic document project API."""

from __future__ import annotations

from datetime import datetime, timezone
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from app.auth import can_manage, current_user
from app.core.feature_flags import require_general_document_platform
from app.db import Store, new_id
from app.domain.documents.schemas import ContentBlock, DocumentProject, ValidationRun
from app.services.document_project_engine import (
    DocumentProjectError,
    editor_schema,
    instantiate_document_project,
    validate_field_values,
    validate_section_content,
)
from app.services.document_validation_engine import validate_document_project
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
    document_context_visible,
    public_document_bundle,
)
from app.services.generic_docx_renderer import GenericDocxRenderError, render_document_project_to_docx
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


def get_store() -> Store:
    return Store()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _package_visible(store: Store, bundle: dict[str, Any], user: dict) -> bool:
    context = bundle["package"].metadata.get("organization_context") or {}
    return document_context_visible(store, user, context)


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
) -> FileResponse:
    """Render a generic project through the existing DOCX export adapter."""
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    _ensure_force_override(user, force)
    bundle = _project_package(store, project)
    _ensure_export_preflight(bundle, project)
    run = _create_validation_run(store, user, project, bundle)
    if run.summary.get("hard_failures") and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "存在硬性规范问题, 无法导出",
                "run_id": run.id,
                "summary": run.summary,
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
    _audit(store, user, action="document.project.rendered", project_id=project.id, reason=f"run:{run.id}")
    safe_title = (project.title or "document").replace("/", "_").replace("\\", "_").strip()[:120] or "document"
    return FileResponse(
        output,
        filename=f"{safe_title}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        background=BackgroundTask(shutil.rmtree, temp_dir, True),
    )


def _render_job_for_project(store: Store, project: DocumentProject, render_job_id: str) -> dict[str, Any]:
    record = get_document_render_job(store, render_job_id)
    if not record or record.get("project_id") != project.id:
        raise HTTPException(status_code=404, detail="渲染任务不存在")
    return record


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
    _ensure_force_override(user, force)
    bundle = _project_package(store, project)
    _ensure_export_preflight(bundle, project)
    run = _create_validation_run(store, user, project, bundle)
    if run.summary.get("hard_failures") and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "存在硬性规范问题, 无法排队导出",
                "run_id": run.id,
                "summary": run.summary,
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
) -> FileResponse:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(store, project, user)
    record = _render_job_for_project(store, project, render_job_id)
    if record.get("status") != "succeeded" or not record.get("artifact_id"):
        raise HTTPException(status_code=409, detail="渲染产物尚未完成")
    artifact = get_document_artifact(store, record["artifact_id"])
    if artifact is None or artifact.project_id != project.id or artifact.render_job_id != render_job_id:
        raise HTTPException(status_code=404, detail="渲染产物不存在")
    path = resolve_document_artifact(artifact)
    if path is not None:
        return FileResponse(path, filename=artifact.filename, media_type=artifact.content_type)
    data = read_document_artifact(artifact)
    if data is None:
        raise HTTPException(status_code=404, detail="渲染产物文件不存在")
    return Response(
        content=data,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
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
