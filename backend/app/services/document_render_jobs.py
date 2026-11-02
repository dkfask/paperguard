"""Durable generic DOCX render jobs and controlled artifact storage."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.core.feature_flags import m1_pilot_active
from app.db import Store, new_id
from app.domain.documents.hashing import document_project_content_hash
from app.domain.documents.hashing import canonical_hash
from app.domain.documents.schemas import (
    Artifact,
    DocumentPackage,
    DocumentProject,
    DocumentTemplate,
    RenderJob,
    TemplateVersion,
)
from app.domain.documents.specs import SpecVersion
from app.domain.workflow_states import transition_state
from app.repositories.workflow_repository import WorkflowRepository
from app.services.generic_docx_renderer import require_document_project_docx_output, render_document_project_to_docx
from app.services.docx_output_check import DocxOutputCheckError, safe_output_check_diagnostics, source_template_bytes
from app.services.manual_output_review import review_view
from app.services.document_validation_engine import required_delivery_issues, validate_document_project
from app.services.template_source import resolve_template_source
from app.services.storage import LocalStorageAdapter, configured_storage
from app.services.upload_security import DOCX_MAX_BYTES, validate_docx_bytes


RENDER_JOB_KIND = "document_render_job"
ARTIFACT_KIND = "document_artifact"
RENDER_JOB_TYPE = "document.render"
_DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "artifacts"
_SAFE_STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.docx$")


class DocumentRenderCancelled(RuntimeError):
    """Raised when an in-flight render is cancelled before publication."""


class DocumentDeliveryGateError(ValueError):
    """A frozen generic project no longer satisfies required delivery rules."""


def artifact_dir() -> Path:
    base = Path(os.environ.get("PAPERGUARD_ARTIFACT_DIR", str(_DEFAULT_ARTIFACT_DIR)))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_filename(title: str) -> str:
    clean = "".join(char for char in str(title or "document") if char not in '<>:"/\\|?*')
    clean = " ".join(clean.split()).strip()
    return f"{clean[:120] or 'document'}.docx"


def _artifact_path(storage_key: str) -> Path | None:
    if not isinstance(storage_key, str) or not _SAFE_STORAGE_KEY_RE.fullmatch(storage_key):
        return None
    base = artifact_dir().resolve()
    candidate = (base / storage_key).resolve()
    if candidate.parent != base or candidate.suffix.lower() != ".docx":
        return None
    return candidate


def artifact_storage() -> LocalStorageAdapter | Any:
    """Return the configured artifact store while retaining local temp space."""
    return configured_storage(artifact_dir())


def _artifact_view(artifact: Artifact | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    payload = artifact.model_dump(mode="json", exclude={"storage_key", "output_check"})
    payload["output_check_status"] = artifact.output_check.get("status", "unverified")
    payload["output_check_count"] = artifact.output_check.get("check_count", 0)
    return payload


def render_job_view(record: dict[str, Any], artifact: Artifact | None = None) -> dict[str, Any]:
    """Return a public job projection without snapshots, paths, or error text."""
    job = RenderJob.model_validate(record)
    payload = job.model_dump(mode="json", exclude={"snapshot", "last_error", "recovery_reason"})
    if artifact is not None:
        payload["artifact"] = _artifact_view(artifact)
        payload["manual_review"] = review_view(artifact.output_check, artifact.manual_review)
    return payload


def _snapshot(project: DocumentProject, bundle: dict[str, Any]) -> dict[str, Any]:
    source = source_template_bytes(resolve_template_source(bundle["template_version"].definition.source_docx))
    return {
        "source_template_sha256": hashlib.sha256(source).hexdigest() if source is not None else "",
        "project": project.model_dump(mode="json"),
        "package": bundle["package"].model_dump(mode="json"),
        "template": bundle["template"].model_dump(mode="json"),
        "template_version": bundle["template_version"].model_dump(mode="json"),
        "spec_version": bundle["spec_version"].model_dump(mode="json"),
    }


def create_document_render_job(
    store: Store,
    project: DocumentProject,
    bundle: dict[str, Any],
    *,
    force: bool,
    actor_id: str,
) -> dict[str, Any]:
    """Persist one version-frozen generic render request and queue item."""
    if m1_pilot_active():
        raise DocumentDeliveryGateError("试点期间通用文档仅支持草稿")
    content_hash = document_project_content_hash(project)
    repository = WorkflowRepository(store)
    for existing in repository.list(RENDER_JOB_KIND):
        if (
            existing.get("project_id") == project.id
            and int(existing.get("project_revision", -1)) == project.revision
            and existing.get("content_hash") == content_hash
            and bool(existing.get("force")) == bool(force)
            and existing.get("status") in {"queued", "running"}
        ):
            return existing

    now = _now()
    render_job = RenderJob(
        id=new_id("render"),
        project_id=project.id,
        package_id=project.package_id,
        project_revision=project.revision,
        content_hash=content_hash,
        template_version_id=project.template_version_id,
        spec_version_id=project.spec_version_id,
        renderer_version=project.renderer_version,
        rule_engine_version=project.rule_engine_version,
        force=force,
        status="queued",
        job_id=new_id("job"),
        requested_by=actor_id,
        snapshot=_snapshot(project, bundle),
        created_at=now,
        updated_at=now,
    )
    render_job.snapshot_hash = canonical_hash(render_job.snapshot)
    with store.workflow_transaction() as conn:
        repository.save_in_transaction(conn, RENDER_JOB_KIND, render_job.model_dump(mode="json"))
        repository.create_job_in_transaction(
            conn,
            job_id=render_job.job_id,
            job_type=RENDER_JOB_TYPE,
            resource_type=RENDER_JOB_KIND,
            resource_id=render_job.id,
            max_attempts=5,
        )
        store.save_audit_event_conn(conn, {
            "id": f"audit-{render_job.id}-queued",
            "actor_id": actor_id,
            "action": "document.project.render.queued",
            "resource_type": RENDER_JOB_KIND,
            "resource_id": render_job.id,
            "organization_id": str((project.organization_context or {}).get("school_id") or ""),
            "result": "success",
            "created_at": now,
        })
    return render_job.model_dump(mode="json")


def get_document_render_job(store: Store, render_job_id: str) -> dict[str, Any] | None:
    return store.get_workflow_record(RENDER_JOB_KIND, render_job_id)


def get_document_artifact(store: Store, artifact_id: str) -> Artifact | None:
    record = store.get_workflow_record(ARTIFACT_KIND, artifact_id)
    if not record:
        return None
    payload = dict(record)
    payload.pop("revision", None)
    payload.pop("updated_at", None)
    return Artifact.model_validate(payload)


def resolve_document_artifact(artifact: Artifact) -> Path | None:
    storage = artifact_storage()
    if isinstance(storage, LocalStorageAdapter):
        return storage.resolve_path(artifact.storage_key)
    return None


def read_document_artifact(artifact: Artifact) -> bytes | None:
    return artifact_storage().get_bytes(artifact.storage_key)


def recover_pending_document_render_jobs(store: Store) -> None:
    """Requeue generic render records after a process restart."""
    repository = WorkflowRepository(store)
    for raw in repository.list(RENDER_JOB_KIND):
        record = dict(raw)
        if record.get("status") == "running":
            record["status"] = transition_state(RENDER_JOB_KIND, "running", "queued")
            record["recovery_reason"] = "process_restart"
            saved = repository.save(RENDER_JOB_KIND, record, expected_revision=record.get("revision", 0))
            if saved is None:
                continue
            record = saved
        if record.get("status") not in {"queued", "failed"}:
            continue
        job_id = record.get("job_id") or new_id("job")
        with store.workflow_transaction() as conn:
            if not record.get("job_id"):
                record["job_id"] = job_id
                saved = repository.save_in_transaction(
                    conn, RENDER_JOB_KIND, record, expected_revision=record.get("revision", 0)
                )
                if saved is None:
                    continue
                record = saved
            if repository.get_job_in_transaction(conn, job_id) is None:
                repository.create_job_in_transaction(
                    conn,
                    job_id=job_id,
                    job_type=RENDER_JOB_TYPE,
                    resource_type=RENDER_JOB_KIND,
                    resource_id=record["id"],
                    max_attempts=5,
                )


def run_document_render_job(store: Store, render_job_id: str, actor_id: str = "system") -> dict[str, Any]:
    """Render the immutable job snapshot and publish one controlled artifact."""
    if m1_pilot_active():
        return {"status": "blocked", "error_code": "M1_GENERIC_DELIVERY_UNAVAILABLE"}
    raw = get_document_render_job(store, render_job_id)
    if not raw:
        return {"status": "failed", "error_code": "DOCUMENT_RENDER_JOB_NOT_FOUND"}
    current = str(raw.get("status") or "")
    if current == "succeeded" and raw.get("artifact_id"):
        return raw
    if current not in {"queued", "failed"}:
        return raw

    repository = WorkflowRepository(store)
    if current == "failed":
        current = transition_state(RENDER_JOB_KIND, current, "queued")
    raw["status"] = transition_state(RENDER_JOB_KIND, current, "running")
    raw["attempt_count"] = int(raw.get("attempt_count", 0)) + 1
    raw.pop("artifact_id", None)
    raw.pop("output_check_diagnostic", None)
    raw["error_code"] = ""
    running = repository.save(RENDER_JOB_KIND, raw, expected_revision=raw.get("revision", 0))
    if running is None:
        return {"status": "failed", "error_code": "DOCUMENT_RENDER_JOB_CONFLICT"}
    raw = running
    temp_dir: Path | None = None
    final_path: Path | None = None
    storage: LocalStorageAdapter | Any | None = None
    stored_key = ""
    output_check_started = False
    try:
        snapshot = raw.get("snapshot") or {}
        actual_snapshot_hash = canonical_hash(snapshot)
        stored_snapshot_hash = str(raw.get("snapshot_hash") or "")
        if stored_snapshot_hash and stored_snapshot_hash != actual_snapshot_hash:
            raise ValueError("render job snapshot integrity check failed")
        raw["snapshot_hash"] = actual_snapshot_hash
        project = DocumentProject.model_validate(snapshot["project"])
        package = DocumentPackage.model_validate(snapshot["package"])
        template = DocumentTemplate.model_validate(snapshot["template"])
        template_version = TemplateVersion.model_validate(snapshot["template_version"])
        spec_version = SpecVersion.model_validate(snapshot["spec_version"])
        validation = validate_document_project(
            project,
            template_version.definition,
            spec_version,
            run_id=f"worker-check:{render_job_id}",
            performed_by=actor_id,
            created_at=_now(),
            rule_engine_version=project.rule_engine_version,
        )
        required_issues = required_delivery_issues(validation)
        if required_issues:
            raise DocumentDeliveryGateError("；".join(required_issues[:20]))
        source = source_template_bytes(resolve_template_source(template_version.definition.source_docx))
        expected_source = str(snapshot.get("source_template_sha256") or "")
        if source is None or not expected_source or hashlib.sha256(source).hexdigest() != expected_source:
            raise ValueError("source template differs from the frozen render request")

        artifact_id = new_id("artifact")
        artifact = Artifact(
            id=artifact_id,
            project_id=project.id,
            render_job_id=raw["id"],
            storage_key=f"{artifact_id}.docx",
            filename=_safe_filename(project.title),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            renderer_version=project.renderer_version,
            created_at=_now(),
        )
        base = artifact_dir()
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{raw['id']}-", dir=str(base))).resolve()
        output = temp_dir / "document.docx"
        render_document_project_to_docx(project, package, template, template_version, spec_version, output)
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError("renderer produced no artifact")
        if output.stat().st_size > DOCX_MAX_BYTES:
            raise ValueError("renderer artifact exceeds the DOCX size limit")
        validate_docx_bytes(output.read_bytes())
        output_check_started = True
        output_check = require_document_project_docx_output(
            output.read_bytes(), project, package, template, template_version,
            spec_version, context_kind="document-async",
        )
        if output_check["source_template_sha256"] != expected_source:
            raise ValueError("checked source template differs from the frozen render request")
        artifact = artifact.model_copy(update={"output_check": output_check})
        latest = get_document_render_job(store, render_job_id)
        if latest and latest.get("status") == "cancelled":
            raise DocumentRenderCancelled()
        if m1_pilot_active():
            raise DocumentDeliveryGateError("试点期间通用文档仅支持草稿")
        storage = artifact_storage()
        stored = storage.put_bytes(
            artifact.storage_key,
            output.read_bytes(),
            artifact.content_type,
        )
        stored_key = artifact.storage_key
        if isinstance(storage, LocalStorageAdapter):
            final_path = storage.resolve_path(artifact.storage_key)
            if final_path is None:
                raise ValueError("artifact storage write did not produce a file")
        artifact = artifact.model_copy(update={
            "size_bytes": stored["size_bytes"],
            "checksum": stored["checksum"],
        })
        if artifact.checksum != output_check["output_sha256"]:
            raise ValueError("stored artifact checksum differs from checked DOCX bytes")
        raw["status"] = transition_state(RENDER_JOB_KIND, "running", "succeeded")
        raw["artifact_id"] = artifact.id
        raw.pop("output_check_diagnostic", None)
        raw["error_code"] = ""
        raw["last_error"] = ""
        raw["completed_at"] = _now()
        with store.workflow_transaction() as conn:
            saved = repository.save_in_transaction(
                conn, RENDER_JOB_KIND, raw, expected_revision=raw.get("revision", 0)
            )
            if saved is None:
                raise RuntimeError("render job changed while publishing artifact")
            repository.save_in_transaction(conn, ARTIFACT_KIND, artifact.model_dump(mode="json"))
            store.save_audit_event_conn(conn, {
                "id": f"audit-{raw['id']}-succeeded",
                "actor_id": actor_id,
                "action": "document.project.render.succeeded",
                "resource_type": RENDER_JOB_KIND,
                "resource_id": raw["id"],
                "organization_id": str((project.organization_context or {}).get("school_id") or ""),
                "result": "success",
                "reason": f"artifact:{artifact.id}",
                "created_at": raw["completed_at"],
            })
        return saved
    except DocumentRenderCancelled:
        if storage is not None and stored_key:
            storage.delete(stored_key)
        return get_document_render_job(store, render_job_id) or raw
    except Exception as exc:
        if storage is not None and stored_key:
            storage.delete(stored_key)
        elif final_path is not None:
            final_path.unlink(missing_ok=True)
        raw["status"] = transition_state(RENDER_JOB_KIND, "running", "failed")
        if isinstance(exc, DocxOutputCheckError):
            raw["output_check_diagnostic"] = safe_output_check_diagnostics(exc.report)
            raw["error_code"] = "DOCUMENT_OUTPUT_CHECK_FAILED"
            raw["last_error"] = "DOCX output check rejected the rendered artifact"
        elif output_check_started:
            raw["output_check_diagnostic"] = safe_output_check_diagnostics(None)
            raw["error_code"] = "DOCUMENT_OUTPUT_CHECK_FAILED"
            raw["last_error"] = "DOCX output check could not complete"
        elif isinstance(exc, DocumentDeliveryGateError):
            raw["error_code"] = "DOCUMENT_DELIVERY_GATE_FAILED"
            raw["last_error"] = str(exc)[:2000]
        else:
            raw["error_code"] = "DOCUMENT_RENDER_FAILED"
            raw["last_error"] = str(exc)[:2000]
        raw["failed_at"] = _now()
        saved = repository.save(RENDER_JOB_KIND, raw, expected_revision=raw.get("revision", 0))
        store.save_audit_event({
            "id": f"audit-{raw['id']}-failed-{raw.get('attempt_count', 0)}",
            "actor_id": actor_id,
            "action": "document.project.render.failed",
            "resource_type": RENDER_JOB_KIND,
            "resource_id": raw["id"],
            "result": "failure",
            "reason": raw["error_code"],
            "created_at": raw["failed_at"],
        })
        return saved or raw
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


__all__ = [
    "ARTIFACT_KIND",
    "DocumentRenderCancelled",
    "DocumentDeliveryGateError",
    "RENDER_JOB_KIND",
    "RENDER_JOB_TYPE",
    "artifact_dir",
    "artifact_storage",
    "create_document_render_job",
    "get_document_artifact",
    "get_document_render_job",
    "recover_pending_document_render_jobs",
    "render_job_view",
    "read_document_artifact",
    "resolve_document_artifact",
    "run_document_render_job",
]
