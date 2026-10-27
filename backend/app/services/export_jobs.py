"""Durable project DOCX export jobs executed by the standalone worker."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.core.export.docx_exporter import export_project_to_docx
from app.db import Store, new_id
from app.domain.workflow_states import transition_state
from app.models.project import WritingProject
from app.models.template import PaperTemplate
from app.repositories.workflow_repository import WorkflowRepository
from app.services.project_versions import project_content_hash
from app.services.template_source import resolve_template_source
from app.services.docx_output_check import (
    DocxOutputCheckError,
    require_docx_output_pass,
    safe_output_check_diagnostics,
    source_template_bytes,
)
from app.services.manual_output_review import review_matches, review_view


EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "exports"
EXPORT_JOB_TYPE = "project.export"
EXPORT_ID_RE = re.compile(r"^export-[0-9a-f]{12}$")


def _safe_filename(title: str) -> str:
    clean = "".join(char for char in str(title or "paper") if char not in '<>:"/\\|?*').strip()
    return f"{clean[:120] or 'paper'}.docx"


def _template_snapshot_sha256(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def export_identity(project: WritingProject, template: PaperTemplate) -> dict[str, Any]:
    source_path = resolve_template_source(template.source_docx) if template.source_docx else None
    source = source_template_bytes(source_path)
    return {
        "project_revision": project.revision,
        "content_hash": project_content_hash(project),
        "template_snapshot_sha256": _template_snapshot_sha256(template.model_dump(mode="json")),
        "source_template_sha256": hashlib.sha256(source).hexdigest() if source is not None else "",
    }


def _confirmed_artifact_matches(record: dict, identity: dict[str, Any], force: bool) -> bool:
    if record.get("status") != "succeeded" or bool(record.get("force")) != bool(force):
        return False
    if not export_job_is_current(record, identity):
        return False
    report = record.get("output_check") or {}
    receipt = record.get("manual_review")
    if not review_matches(report, receipt):
        return False
    output = Path(str(record.get("file_path") or "")).resolve()
    root = EXPORT_DIR.resolve()
    if output.parent != root or not output.is_file():
        return False
    expected_sha = str((receipt or {}).get("output_sha256") or report.get("output_sha256") or "")
    return bool(expected_sha and hashlib.sha256(output.read_bytes()).hexdigest() == expected_sha)


def export_job_is_current(record: dict, identity: dict[str, Any]) -> bool:
    frozen_template = record.get("template_snapshot") or {}
    stored_template_hash = record.get("template_snapshot_sha256") or _template_snapshot_sha256(frozen_template)
    return all(
        record.get(key) == value
        for key, value in identity.items()
        if key != "template_snapshot_sha256"
    ) and stored_template_hash == identity["template_snapshot_sha256"]


def _organization_id(store: Store, project: WritingProject) -> str:
    context = project.organization_context or {}
    batch_id = context.get("batch_id", "")
    cohort = store.get_workflow_record("cohort", batch_id) if batch_id else None
    return str((cohort or {}).get("organization_id") or context.get("college_id") or context.get("school_id") or "")


def export_job_view(record: dict, *, current_identity: dict[str, Any] | None = None) -> dict:
    """Return a public task projection without content snapshots or paths."""
    hidden = {"file_path", "project_snapshot", "template_snapshot", "template_snapshot_sha256", "output_check"}
    result = {key: value for key, value in record.items() if key not in hidden}
    report = record.get("output_check") or {}
    result["output_check_status"] = report.get("status", "unverified")
    result["output_check_count"] = report.get("check_count", 0)
    result["manual_review"] = review_view(report, record.get("manual_review"))
    if current_identity is not None:
        result["is_current_for_project"] = export_job_is_current(record, current_identity)
    if record.get("output_check_diagnostic"):
        result["output_check_diagnostic"] = record["output_check_diagnostic"]
    if record.get("status") == "succeeded":
        if result.get("is_current_for_project") is False:
            return result
        if result["manual_review"]["status"] == "required":
            result["review_candidate_url"] = f"/api/projects/{record['project_id']}/export-jobs/{record['id']}/review-candidate"
        else:
            result["download_url"] = f"/api/projects/{record['project_id']}/export-jobs/{record['id']}/download"
    return result


def create_export_job(
    store: Store,
    project: WritingProject,
    template: PaperTemplate,
    *,
    force: bool,
    actor_id: str,
) -> dict:
    """Create a version-frozen export record and durable queue item atomically."""
    if project.tenant_id or project.membership_id:
        raise PermissionError("tenant project export isolation is not implemented")
    now = _now()
    source_path = resolve_template_source(template.source_docx) if template.source_docx else None
    source = source_template_bytes(source_path)
    identity = {
        "project_revision": project.revision,
        "content_hash": project_content_hash(project),
        "template_snapshot_sha256": _template_snapshot_sha256(template.model_dump(mode="json")),
        "source_template_sha256": hashlib.sha256(source).hexdigest() if source is not None else "",
    }
    record = {
        "id": new_id("export"),
        "project_id": project.id,
        **identity,
        "force": bool(force),
        "status": "queued",
        "filename": _safe_filename(project.title),
        "file_path": "",
        "error_code": "",
        "job_id": new_id("job"),
        "project_snapshot": project.model_dump(mode="json"),
        "template_snapshot": template.model_dump(mode="json"),
        "revision": 0,
        "created_at": now,
    }
    # Resolve audit metadata before a large frozen snapshot can spill the write cache.
    organization_id = _organization_id(store, project)
    repository = WorkflowRepository(store)
    with store.workflow_transaction() as conn:
        records = repository.list("project_export")
        existing = [
            item for item in records
            if item.get("project_id") == project.id
            and _confirmed_artifact_matches(item, identity, force)
        ]
        if existing:
            return existing[0]
        active = [
            item for item in records
            if item.get("project_id") == project.id
            and item.get("project_revision") == identity["project_revision"]
            and item.get("content_hash") == identity["content_hash"]
            and item.get("template_snapshot_sha256", _template_snapshot_sha256(item.get("template_snapshot") or {})) == identity["template_snapshot_sha256"]
            and item.get("source_template_sha256", "") == identity["source_template_sha256"]
            and bool(item.get("force")) == bool(force)
            and item.get("status") in {"queued", "running"}
        ]
        if active:
            return active[0]
        repository.save_in_transaction(conn, "project_export", record)
        repository.create_job_in_transaction(
            conn,
            job_id=record["job_id"],
            job_type=EXPORT_JOB_TYPE,
            resource_type="project_export",
            resource_id=record["id"],
            max_attempts=5,
        )
        store.save_audit_event_conn(conn, {
            "id": f"audit-{record['id']}-queued",
            "actor_id": actor_id,
            "action": "project.export.queued",
            "resource_type": "project_export",
            "resource_id": record["id"],
            "organization_id": organization_id,
            "result": "success",
            "created_at": now,
        })
    return record


def recover_pending_exports(store: Store) -> None:
    """Repair running export records and recreate missing queue rows at startup."""
    repository = WorkflowRepository(store)
    for record in repository.list("project_export"):
        if record.get("status") == "running":
            record["status"] = transition_state("project_export", "running", "queued")
            record["recovery_reason"] = "process_restart"
            saved = repository.save("project_export", record, expected_revision=record.get("revision", 0))
            if saved is None:
                continue
            record = saved
        if record.get("status") not in {"queued", "failed"}:
            continue
        job_id = record.get("job_id") or new_id("job")
        with store.workflow_transaction() as conn:
            if not record.get("job_id"):
                record["job_id"] = job_id
                repository.save_in_transaction(conn, "project_export", record, expected_revision=record.get("revision", 0))
            if repository.get_job_in_transaction(conn, job_id) is None:
                repository.create_job_in_transaction(
                    conn, job_id=job_id, job_type=EXPORT_JOB_TYPE,
                    resource_type="project_export", resource_id=record["id"], max_attempts=5,
                )


def _now() -> str:
    from app.api.workflow import _now as workflow_now

    return workflow_now()


def run_project_export(store: Store, export_id: str, actor_id: str = "system") -> dict[str, Any]:
    """Render one frozen project snapshot, leaving only a controlled final artifact."""
    if not EXPORT_ID_RE.fullmatch(str(export_id)):
        return {"status": "failed", "error_code": "EXPORT_RECORD_NOT_FOUND"}
    record = store.get_workflow_record("project_export", export_id)
    if not record:
        return {"status": "failed", "error_code": "EXPORT_RECORD_NOT_FOUND"}
    snapshot = record.get("project_snapshot") or {}
    if snapshot.get("tenant_id") or snapshot.get("membership_id"):
        return {"status": "failed", "error_code": "PROJECT_ASYNC_ISOLATION_PENDING"}
    if record.get("status") == "succeeded" and record.get("file_path"):
        return record
    current = str(record.get("status") or "")
    if current not in {"queued", "failed"}:
        return record
    if current == "failed":
        current = transition_state("project_export", current, "queued")
    record["status"] = transition_state("project_export", current, "running")
    record["attempt"] = int(record.get("attempt", 0)) + 1
    final_path = EXPORT_DIR / f"{export_id}.docx"
    record["file_path"] = ""
    record.pop("output_check", None)
    record.pop("output_check_diagnostic", None)
    record["error_code"] = ""
    saved = store.save_workflow_record("project_export", record, expected_revision=record.get("revision", 0))
    if saved is None:
        return {"status": "failed", "error_code": "EXPORT_RECORD_CONFLICT"}
    record = saved
    temp_dir: Path | None = None
    try:
        final_path.unlink(missing_ok=True)
        project = WritingProject.model_validate(record["project_snapshot"])
        template = PaperTemplate.model_validate(record["template_snapshot"])
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(str(record.get("filename") or "paper.docx"))
        if "/" in filename or "\\" in filename or set(filename) <= {"."}:
            raise ValueError("导出文件名不合法")
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{export_id}-", dir=str(EXPORT_DIR))).resolve()
        temp_path = (temp_dir / filename).resolve()
        if temp_path.parent != temp_dir:
            raise ValueError("导出文件名解析越界")
        source_path = resolve_template_source(template.source_docx) if template.source_docx else None
        source = source_template_bytes(source_path)
        source_hash = hashlib.sha256(source).hexdigest() if source is not None else ""
        if source_hash != str(record.get("source_template_sha256") or "") or (template.source_docx and not source_hash):
            raise ValueError("source template differs from the frozen export request")
        export_project_to_docx(
            project,
            template,
            str(temp_path),
            template_path=source_path,
        )
        record["output_check"] = require_docx_output_pass(
            temp_path.read_bytes(), project, template,
            source_template_bytes=source,
            context_kind="paper-async",
        )
        if record["output_check"]["source_template_sha256"] != str(record.get("source_template_sha256") or "") or source_template_bytes(source_path) != source:
            raise ValueError("source template changed during export")
        temp_path.replace(final_path)
        record["status"] = transition_state("project_export", "running", "succeeded")
        record["file_path"] = str(final_path)
        record["completed_at"] = _now()
        record["completed_by"] = actor_id
        record["error_code"] = ""
        saved = store.save_workflow_record("project_export", record, expected_revision=record.get("revision", 0))
        store.save_audit_event({
            "id": f"audit-{export_id}-attempt-{record.get('attempt', 0)}-succeeded",
            "actor_id": actor_id,
            "action": "project.export.succeeded",
            "resource_type": "project_export",
            "resource_id": export_id,
            "organization_id": _organization_id(store, project),
            "result": "success",
            "created_at": record["completed_at"],
        })
        return saved or record
    except Exception as exc:
        try:
            final_path.unlink(missing_ok=True)
        except OSError:
            pass
        record["status"] = transition_state("project_export", "running", "failed")
        if isinstance(exc, DocxOutputCheckError):
            record["output_check"] = exc.report
            record["output_check_diagnostic"] = safe_output_check_diagnostics(exc.report)
            record["error_code"] = "PROJECT_EXPORT_OUTPUT_CHECK_FAILED"
        else:
            record["error_code"] = "PROJECT_EXPORT_FAILED"
        record["file_path"] = ""
        record["failed_at"] = _now()
        saved = store.save_workflow_record("project_export", record, expected_revision=record.get("revision", 0))
        project = None
        try:
            project = WritingProject.model_validate(record.get("project_snapshot") or {})
        except Exception:
            pass
        store.save_audit_event({
            "id": f"audit-{export_id}-attempt-{record.get('attempt', 0)}-failed",
            "actor_id": actor_id,
            "action": "project.export.failed",
            "resource_type": "project_export",
            "resource_id": export_id,
            "organization_id": _organization_id(store, project) if project else "",
            "result": "failure",
            "reason": record["error_code"],
            "created_at": record["failed_at"],
        })
        return saved or record
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
