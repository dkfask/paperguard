"""Durable project DOCX export jobs executed by the standalone worker."""

from __future__ import annotations

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


EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "exports"
EXPORT_JOB_TYPE = "project.export"
EXPORT_ID_RE = re.compile(r"^export-[0-9a-f]{12}$")


def _safe_filename(title: str) -> str:
    clean = "".join(char for char in str(title or "paper") if char not in '<>:"/\\|?*').strip()
    return f"{clean[:120] or 'paper'}.docx"


def _organization_id(store: Store, project: WritingProject) -> str:
    context = project.organization_context or {}
    batch_id = context.get("batch_id", "")
    cohort = store.get_workflow_record("cohort", batch_id) if batch_id else None
    return str((cohort or {}).get("organization_id") or context.get("college_id") or context.get("school_id") or "")


def export_job_view(record: dict) -> dict:
    """Return a public task projection without content snapshots or paths."""
    hidden = {"file_path", "project_snapshot", "template_snapshot"}
    result = {key: value for key, value in record.items() if key not in hidden}
    if record.get("status") == "succeeded":
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
    now = _now()
    record = {
        "id": new_id("export"),
        "project_id": project.id,
        "project_revision": project.revision,
        "content_hash": project_content_hash(project),
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
    repository = WorkflowRepository(store)
    with store.workflow_transaction() as conn:
        existing = [
            item for item in repository.list("project_export")
            if item.get("project_id") == project.id
            and int(item.get("project_revision", -1)) == project.revision
            and bool(item.get("force")) == bool(force)
            and item.get("status") in {"queued", "running"}
        ]
        if existing:
            return existing[0]
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
            "organization_id": _organization_id(store, project),
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
    if record.get("status") == "succeeded" and record.get("file_path"):
        return record
    current = str(record.get("status") or "")
    if current not in {"queued", "failed"}:
        return record
    record["status"] = transition_state("project_export", current, "running")
    record["attempt"] = int(record.get("attempt", 0)) + 1
    saved = store.save_workflow_record("project_export", record, expected_revision=record.get("revision", 0))
    if saved is None:
        return {"status": "failed", "error_code": "EXPORT_RECORD_CONFLICT"}
    record = saved
    temp_dir: Path | None = None
    try:
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
        export_project_to_docx(
            project,
            template,
            str(temp_path),
            template_path=resolve_template_source(template.source_docx) if template.source_docx else None,
        )
        final_path = EXPORT_DIR / f"{export_id}.docx"
        temp_path.replace(final_path)
        record["status"] = transition_state("project_export", "running", "succeeded")
        record["file_path"] = str(final_path)
        record["completed_at"] = _now()
        record["completed_by"] = actor_id
        record["error_code"] = ""
        saved = store.save_workflow_record("project_export", record, expected_revision=record.get("revision", 0))
        store.save_audit_event({
            "id": f"audit-{export_id}-succeeded",
            "actor_id": actor_id,
            "action": "project.export.succeeded",
            "resource_type": "project_export",
            "resource_id": export_id,
            "organization_id": _organization_id(store, project),
            "result": "success",
            "created_at": record["completed_at"],
        })
        return saved or record
    except Exception:
        record["status"] = transition_state("project_export", "running", "failed")
        record["error_code"] = "PROJECT_EXPORT_FAILED"
        record["failed_at"] = _now()
        saved = store.save_workflow_record("project_export", record, expected_revision=record.get("revision", 0))
        project = None
        try:
            project = WritingProject.model_validate(record.get("project_snapshot") or {})
        except Exception:
            pass
        store.save_audit_event({
            "id": f"audit-{export_id}-failed",
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
