"""Durable project DOCX export jobs executed by the standalone worker."""

from __future__ import annotations

import re
import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.export.docx_exporter import export_project_to_docx
from app.core.feature_flags import project_delivery_enabled, require_project_delivery
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
from app.services.manual_output_review import review_required, review_view
from app.services.export_time_budget import (
    EXPORT_JOB_LEASE_SECONDS, ExportBudgetExceeded, InvalidExportBudget, check_export_budget,
    configured_export_request_limit, configured_export_total_seconds, export_budget, new_export_deadline,
    parse_export_time, recorded_export_deadline,
)


EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "exports"
EXPORT_JOB_TYPE = "project.export"
EXPORT_ID_RE = re.compile(r"^export-[0-9a-f]{12}$")


class ExportRequestLimitExceeded(RuntimeError):
    """Repeated failed requests for the same frozen content need support."""


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
    hidden = {"file_path", "project_snapshot", "template_snapshot", "output_check", "requirement_manual_checks"}
    result = {key: value for key, value in record.items() if key not in hidden}
    report = record.get("output_check") or {}
    requires_automation = review_required(report)
    result["output_check_status"] = "unverifiable" if requires_automation else report.get("status", "unverified")
    result["output_check_count"] = report.get("check_count", 0)
    result["manual_review"] = review_view(report, None if requires_automation else record.get("manual_review"))
    if record.get("output_check_diagnostic"):
        result["output_check_diagnostic"] = record["output_check_diagnostic"]
    if record.get("status") == "succeeded":
        if not requires_automation:
            result["download_url"] = f"/api/projects/{record['project_id']}/export-jobs/{record['id']}/download"
    return result


def bind_requirement_manual_checks(report: dict, checks: list[str] | None) -> dict:
    """Bind unautomatable hard requirements to the artifact's Office review receipt."""
    additions = [str(item).strip() for item in (checks or []) if str(item).strip()]
    if not additions:
        return report
    result = dict(report)
    existing = [str(item).strip() for item in result.get("manual_checks", []) if str(item).strip()]
    result["manual_checks"] = list(dict.fromkeys([*existing, *additions]))
    result["office_review_required"] = True
    return result


def create_export_job(
    store: Store,
    project: WritingProject,
    template: PaperTemplate,
    *,
    force: bool,
    actor_id: str,
    requirement_manual_checks: list[str] | None = None,
) -> dict:
    """Create a version-frozen export record and durable queue item atomically."""
    require_project_delivery(store, project.owner_id)
    if project.tenant_id or project.membership_id:
        raise PermissionError("tenant project export isolation is not implemented")
    now = _now()
    total_seconds = configured_export_total_seconds()
    request_limit = configured_export_request_limit()
    park_expired_project_exports(store)
    source_path = resolve_template_source(template.source_docx) if template.source_docx else None
    source = source_template_bytes(source_path)
    record = {
        "id": new_id("export"),
        "project_id": project.id,
        "project_revision": project.revision,
        "content_hash": project_content_hash(project),
        "force": bool(force),
        "requirement_manual_checks": [
            str(item).strip() for item in (requirement_manual_checks or []) if str(item).strip()
        ],
        "status": "queued",
        "filename": _safe_filename(project.title),
        "file_path": "",
        "error_code": "",
        "job_id": new_id("job"),
        "project_snapshot": project.model_dump(mode="json"),
        "template_snapshot": template.model_dump(mode="json"),
        "source_template_sha256": hashlib.sha256(source).hexdigest() if source is not None else "",
        "revision": 0,
        "created_at": now,
        "deadline_at": new_export_deadline(now, total_seconds),
        "total_timeout_seconds": total_seconds,
    }
    # Resolve audit metadata before a large frozen snapshot can spill the write cache.
    organization_id = _organization_id(store, project)
    repository = WorkflowRepository(store)
    with store.workflow_transaction() as conn:
        rows = conn.execute(
            "SELECT data FROM workflow_records WHERE kind = 'project_export' "
            "AND json_extract(data, '$.project_id') = ? "
            "AND json_extract(data, '$.project_revision') = ? ORDER BY rowid DESC",
            (project.id, project.revision),
        ).fetchall()
        same_revision = [item for row in rows if (item := json.loads(row["data"]))
                         and item.get("content_hash") == record["content_hash"]]
        existing = []
        for item in same_revision:
            status = item.get("status")
            if status not in {"queued", "running", "failed"}:
                continue
            job = repository.get_job_in_transaction(conn, item.get("job_id", ""))
            matches = bool(job and job["job_type"] == EXPORT_JOB_TYPE
                           and job["resource_type"] == "project_export"
                           and job["resource_id"] == item["id"])
            error = "PROJECT_EXPORT_QUEUE_STATE_MISSING" if not matches else ""
            if matches:
                attempts_left = job["attempt_count"] < job["max_attempts"]
                if job["state"] == "running":
                    try:
                        lease_is_live = parse_export_time(job["lease_until"]) > parse_export_time(now)
                    except InvalidExportBudget:
                        error = "PROJECT_EXPORT_QUEUE_STATE_INVALID"
                    else:
                        if not lease_is_live and not attempts_left:
                            error = "PROJECT_EXPORT_ATTEMPTS_EXHAUSTED"
                elif job["state"] in {"queued", "failed"}:
                    if not attempts_left:
                        error = "PROJECT_EXPORT_ATTEMPTS_EXHAUSTED"
                else:
                    error = "PROJECT_EXPORT_QUEUE_STATE_INVALID"
            if status in {"queued", "running"} and error:
                # Preserve the frozen input and count an unrecoverable queue
                # record as a failed request. Never touch a different job.
                item["status"] = transition_state("project_export", status, "failed")
                item.update(error_code=error, recovery_reason="queue_state_invalid", file_path="", failed_at=now)
                saved = repository.save_in_transaction(
                    conn, "project_export", item, expected_revision=item.get("revision", 0),
                )
                if saved is None:
                    raise RuntimeError("export changed while recovering its queue state")
                item.update(saved)
                if matches and job["state"] in {"queued", "running", "failed"}:
                    queue_state = "failed" if error == "PROJECT_EXPORT_ATTEMPTS_EXHAUSTED" else "cancelled"
                    conn.execute(
                        "UPDATE jobs SET state = ?, lease_until = NULL, lease_token = NULL, leased_by = NULL, "
                        "last_error = ?, updated_at = ? WHERE id = ?",
                        (queue_state, error, now, job["id"]),
                    )
                store.save_audit_event_conn(conn, {
                    "id": new_id("audit"), "actor_id": "system", "action": "project.export.queue_state_invalid",
                    "resource_type": "project_export", "resource_id": item["id"], "result": "failure",
                    "reason": error, "created_at": now,
                })
                continue
            if not error:
                existing.append(item)
        if existing:
            return existing[0]
        failed = []
        for item in same_revision:
            if item.get("status") == "succeeded":
                break
            if item.get("status") == "failed":
                failed.append(item)
        limit_reached = len(failed) >= request_limit
        if not limit_reached:
            if failed:
                record["previous_export_id"] = failed[0]["id"]
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
    # Keep any orphan diagnosis even if this request exceeds the limit.
    if limit_reached:
        raise ExportRequestLimitExceeded("当前论文版本多次导出失败，请联系系统团队处理")
    return record


def recover_pending_exports(store: Store) -> None:
    """Recover interrupted records without recreating a lost attempt budget."""
    repository = WorkflowRepository(store)
    park_expired_project_exports(store)
    park_exhausted_project_exports(store)
    for record in store.list_workflow_records("project_export", limit=None):
        if record.get("status") not in {"queued", "failed"}:
            if record.get("status") != "running":
                continue
        with store.workflow_transaction() as conn:
            job = repository.get_job_in_transaction(conn, record.get("job_id", ""))
            if (job is None or job["job_type"] != EXPORT_JOB_TYPE or job["resource_type"] != "project_export"
                    or job["resource_id"] != record["id"]):
                if record["status"] != "failed":
                    record["status"] = transition_state("project_export", record["status"], "failed")
                record["error_code"] = "PROJECT_EXPORT_QUEUE_STATE_MISSING"
                record["recovery_reason"] = "queue_state_missing"
                record["file_path"] = ""
                record["failed_at"] = _now()
                repository.save_in_transaction(conn, "project_export", record, expected_revision=record.get("revision", 0))
                continue
            if record["status"] == "running":
                if job["state"] == "running" and job["lease_until"] and job["lease_until"] > _now():
                    continue
                record["status"] = transition_state("project_export", "running", "queued")
                record["recovery_reason"] = "process_restart"
                repository.save_in_transaction(conn, "project_export", record, expected_revision=record.get("revision", 0))


def park_expired_project_exports(store: Store) -> None:
    """Stop overdue exports only after their worker lease is no longer active."""
    repository = WorkflowRepository(store)
    now = _now()
    now_value = parse_export_time(now)
    with store.workflow_transaction() as conn:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE job_type = ? AND resource_type = 'project_export' "
            "AND (state IN ('queued', 'failed') OR "
            "(state = 'running' AND lease_until IS NOT NULL AND lease_until <= ?))",
            (EXPORT_JOB_TYPE, now),
        ).fetchall()
        for job in jobs:
            if job["state"] == "failed" and job["attempt_count"] >= job["max_attempts"]:
                continue
            row = conn.execute(
                "SELECT data FROM workflow_records WHERE kind = 'project_export' AND id = ?",
                (job["resource_id"],),
            ).fetchone()
            if row is None:
                continue
            record = json.loads(row["data"])
            if record.get("job_id") != job["id"] or record.get("status") not in {"queued", "running", "failed"}:
                continue
            try:
                deadline, derived = recorded_export_deadline(record)
                expired = parse_export_time(deadline) <= now_value
                error = "PROJECT_EXPORT_DEADLINE_EXCEEDED"
            except InvalidExportBudget:
                deadline, derived, expired = "", False, True
                error = "PROJECT_EXPORT_DEADLINE_INVALID"
            if derived:
                record["deadline_at"] = deadline
                record["deadline_origin"] = "legacy_created_at"
            if not expired:
                if derived:
                    saved = repository.save_in_transaction(
                        conn, "project_export", record, expected_revision=record.get("revision", 0),
                    )
                    if saved is None:
                        raise RuntimeError("export changed while recording its deadline")
                continue
            if record["status"] != "failed":
                record["status"] = transition_state("project_export", record["status"], "failed")
            record.update(error_code=error, file_path="", failed_at=now)
            saved = repository.save_in_transaction(
                conn, "project_export", record, expected_revision=record.get("revision", 0),
            )
            if saved is None:
                raise RuntimeError("export changed while parking an expired job")
            conn.execute(
                "UPDATE jobs SET state = 'cancelled', lease_until = NULL, lease_token = NULL, leased_by = NULL, "
                "last_error = ?, updated_at = ? WHERE id = ?",
                (error, now, job["id"]),
            )
            store.save_audit_event_conn(conn, {
                "id": new_id("audit"), "actor_id": "system", "action": "project.export.deadline_expired",
                "resource_type": "project_export", "resource_id": record["id"], "result": "failure",
                "reason": error, "created_at": now,
            })


def park_exhausted_project_exports(store: Store) -> None:
    """Stop exhausted expired jobs while retaining their frozen export inputs."""
    repository = WorkflowRepository(store)
    now = _now()
    with store.workflow_transaction() as conn:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE job_type = ? AND resource_type = 'project_export' "
            "AND attempt_count >= max_attempts AND (state IN ('queued', 'failed') OR "
            "(state = 'running' AND lease_until IS NOT NULL AND lease_until <= ?))",
            (EXPORT_JOB_TYPE, now),
        ).fetchall()
        for job in jobs:
            if job["state"] != "failed":
                conn.execute(
                    "UPDATE jobs SET state = 'failed', lease_until = NULL, lease_token = NULL, leased_by = NULL, "
                    "last_error = ?, updated_at = ? WHERE id = ?",
                    ("PROJECT_EXPORT_ATTEMPTS_EXHAUSTED", now, job["id"]),
                )
            row = conn.execute(
                "SELECT data FROM workflow_records WHERE kind = 'project_export' AND id = ?",
                (job["resource_id"],),
            ).fetchone()
            if row is None:
                continue
            record = json.loads(row["data"])
            if record.get("job_id") != job["id"] or record.get("status") not in {"queued", "running"}:
                continue
            record["status"] = transition_state("project_export", record["status"], "failed")
            record["error_code"] = "PROJECT_EXPORT_ATTEMPTS_EXHAUSTED"
            record["file_path"] = ""
            record["failed_at"] = now
            saved = repository.save_in_transaction(conn, "project_export", record, expected_revision=record.get("revision", 0))
            if saved is None:
                raise RuntimeError("export changed while parking an exhausted job")
            store.save_audit_event_conn(conn, {
                "id": new_id("audit"), "actor_id": "system", "action": "project.export.attempts_exhausted",
                "resource_type": "project_export", "resource_id": record["id"], "result": "failure",
                "reason": f"attempts:{job['attempt_count']}/{job['max_attempts']}", "created_at": now,
            })


def _now() -> str:
    from app.api.workflow import _now as workflow_now

    return workflow_now()


class _ExportLeaseLost(Exception):
    pass


class _ExportRecordConflict(Exception):
    pass


def _export_lease(conn, record: dict, lease_token: str):
    job = Store.get_job_conn(conn, record.get("job_id", ""))
    if (not job or job["job_type"] != EXPORT_JOB_TYPE or job["resource_type"] != "project_export"
            or job["resource_id"] != record["id"] or job["state"] != "running"
            or not lease_token or job["lease_token"] != lease_token
            or not job["lease_until"] or job["lease_until"] <= _now()):
        raise _ExportLeaseLost
    return job


def _finish_export_job(conn, job, lease_token: str, *, error: str = "", retry_delay_seconds: int = 0,
                       terminal: bool = False) -> str:
    now = datetime.now(timezone.utc)
    now_text = now.isoformat(timespec="seconds")
    if error:
        state = "cancelled" if terminal else ("failed" if job["attempt_count"] >= job["max_attempts"] else "queued")
        available_at = (now + timedelta(seconds=max(0, int(retry_delay_seconds)))).isoformat(timespec="seconds")
        completed_at = None
    else:
        state, available_at, completed_at = "succeeded", job["available_at"], now_text
    updated = conn.execute(
        "UPDATE jobs SET state = ?, available_at = ?, lease_until = NULL, lease_token = NULL, "
        "leased_by = NULL, last_error = ?, updated_at = ?, completed_at = ? "
        "WHERE id = ? AND state = 'running' AND lease_token = ? AND lease_until > ?",
        (state, available_at, error[:2000] or None, now_text, completed_at, job["id"], lease_token, now_text),
    )
    if updated.rowcount != 1:
        raise _ExportLeaseLost
    return state


def fail_unstarted_export_job(
    store: Store, job_id: str, resource_id: str, lease_token: str, error: str, *, retry_delay_seconds: int,
) -> str | None:
    """Return an unstarted queue item to retry only while its claim is still valid."""
    try:
        with store.workflow_transaction() as conn:
            job = Store.get_job_conn(conn, job_id)
            if (not job or job["job_type"] != EXPORT_JOB_TYPE or job["resource_type"] != "project_export"
                    or job["resource_id"] != resource_id or job["state"] != "running"
                    or not lease_token or job["lease_token"] != lease_token):
                raise _ExportLeaseLost
            return _finish_export_job(conn, job, lease_token, error=error, retry_delay_seconds=retry_delay_seconds)
    except _ExportLeaseLost:
        return None


def run_project_export(
    store: Store, export_id: str, actor_id: str = "system", *, lease_token: str | None = None,
    retry_delay_seconds: int = 0,
) -> dict[str, Any]:
    """Render one frozen snapshot and publish only while holding its queue lease."""
    if not EXPORT_ID_RE.fullmatch(str(export_id)):
        return {"status": "failed", "error_code": "EXPORT_RECORD_NOT_FOUND"}
    try:
        with store.workflow_transaction() as conn:
            row = conn.execute(
                "SELECT data FROM workflow_records WHERE kind = 'project_export' AND id = ?", (export_id,),
            ).fetchone()
            if row is None:
                return {"status": "failed", "error_code": "EXPORT_RECORD_NOT_FOUND"}
            record = json.loads(row["data"])
            snapshot = record.get("project_snapshot") or {}
            if snapshot.get("tenant_id") or snapshot.get("membership_id"):
                return {"status": "failed", "error_code": "PROJECT_ASYNC_ISOLATION_PENDING"}
            if not project_delivery_enabled(store, str(snapshot.get("owner_id") or "")):
                return {"status": "paused", "error_code": "M1_PERSONAL_DELIVERY_UNAVAILABLE"}

            # Direct callers also consume an attempt; they may not bypass an active lease.
            if lease_token is None:
                if record.get("status") == "succeeded" and record.get("file_path"):
                    return record
                job = Store.get_job_conn(conn, record.get("job_id", ""))
                if (not job or job["job_type"] != EXPORT_JOB_TYPE or job["resource_type"] != "project_export"
                        or job["resource_id"] != export_id or job["state"] not in {"queued", "failed"}
                        or job["attempt_count"] >= job["max_attempts"]):
                    raise _ExportLeaseLost
                now = datetime.now(timezone.utc)
                lease_token = new_id("lease")
                conn.execute(
                    "UPDATE jobs SET state = 'running', attempt_count = attempt_count + 1, lease_until = ?, "
                    "lease_token = ?, leased_by = ?, last_error = NULL, updated_at = ? WHERE id = ?",
                    ((now + timedelta(seconds=EXPORT_JOB_LEASE_SECONDS)).isoformat(timespec="seconds"), lease_token,
                     "inline-export", now.isoformat(timespec="seconds"), job["id"]),
                )
            job = _export_lease(conn, record, lease_token)
            if record.get("status") == "succeeded" and record.get("file_path"):
                _finish_export_job(conn, job, lease_token)
                return record
            try:
                deadline_at, derived = recorded_export_deadline(record)
            except InvalidExportBudget:
                deadline_at, derived = "", False
            if derived:
                record["deadline_at"] = deadline_at
                record["deadline_origin"] = "legacy_created_at"
            lease_until = job["lease_until"]
            current = str(record.get("status") or "")
            if current not in {"queued", "failed", "running"}:
                raise _ExportRecordConflict
            if current == "failed":
                current = transition_state("project_export", current, "queued")
            if current != "running":
                record["status"] = transition_state("project_export", current, "running")
            record["attempt"] = int(record.get("attempt", 0)) + 1
            record["file_path"] = ""
            record.pop("output_check", None)
            record.pop("output_check_diagnostic", None)
            record["error_code"] = ""
            saved = store.save_workflow_record_conn(
                conn, "project_export", record, expected_revision=record.get("revision", 0),
            )
            if saved is None:
                raise _ExportRecordConflict
            record = saved
    except _ExportLeaseLost:
        return {"status": "failed", "error_code": "PROJECT_EXPORT_LEASE_LOST"}
    except _ExportRecordConflict:
        return {"status": "failed", "error_code": "EXPORT_RECORD_CONFLICT"}

    final_path = EXPORT_DIR / f"{export_id}.docx"
    temp_dir: Path | None = None
    published = False
    try:
        with export_budget(deadline_at, lease_until=lease_until):
            project = WritingProject.model_validate(record["project_snapshot"])
            template = PaperTemplate.model_validate(record["template_snapshot"])
            from app.services.project_validation import validate_heading_hierarchy
            if validate_heading_hierarchy(project.sections):
                raise ValueError("frozen project has invalid heading hierarchy")
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
            check_export_budget()
            record["output_check"] = require_docx_output_pass(
                temp_path.read_bytes(), project, template,
                source_template_bytes=source,
                context_kind="paper-async",
            )
            record["output_check"] = bind_requirement_manual_checks(
                record["output_check"], record.get("requirement_manual_checks")
            )
            if record["output_check"]["source_template_sha256"] != str(record.get("source_template_sha256") or "") or source_template_bytes(source_path) != source:
                raise ValueError("source template changed during export")
            check_export_budget()
            record["status"] = transition_state("project_export", "running", "succeeded")
            record["file_path"] = str(final_path)
            record["completed_at"] = _now()
            record["completed_by"] = actor_id
            record["error_code"] = ""
            organization_id = _organization_id(store, project)
            with store.workflow_transaction() as conn:
                job = _export_lease(conn, record, lease_token)
                check_export_budget()
                _finish_export_job(conn, job, lease_token)
                saved = store.save_workflow_record_conn(
                    conn, "project_export", record, expected_revision=record.get("revision", 0),
                )
                if saved is None:
                    raise _ExportRecordConflict
                store.save_audit_event_conn(conn, {
                    "id": f"audit-{export_id}-attempt-{record.get('attempt', 0)}-succeeded",
                    "actor_id": actor_id,
                    "action": "project.export.succeeded",
                    "resource_type": "project_export",
                    "resource_id": export_id,
                    "organization_id": organization_id,
                    "result": "success",
                    "created_at": record["completed_at"],
                })
                check_export_budget()
                temp_path.replace(final_path)
                published = True
            return saved
    except _ExportLeaseLost:
        return {"status": "failed", "error_code": "PROJECT_EXPORT_LEASE_LOST"}
    except _ExportRecordConflict:
        return {"status": "failed", "error_code": "EXPORT_RECORD_CONFLICT"}
    except Exception as exc:
        record["status"] = transition_state("project_export", "running", "failed")
        record.pop("completed_at", None)
        record.pop("completed_by", None)
        if isinstance(exc, ExportBudgetExceeded):
            record.pop("output_check", None)
            record["error_code"] = ("PROJECT_EXPORT_DEADLINE_EXCEEDED" if exc.reason == "total"
                                    else "PROJECT_EXPORT_ATTEMPT_TIMEOUT")
        elif isinstance(exc, InvalidExportBudget):
            record.pop("output_check", None)
            record["error_code"] = "PROJECT_EXPORT_DEADLINE_INVALID"
        elif isinstance(exc, DocxOutputCheckError):
            record["output_check"] = exc.report
            record["output_check_diagnostic"] = safe_output_check_diagnostics(exc.report)
            record["error_code"] = "PROJECT_EXPORT_OUTPUT_CHECK_FAILED"
        else:
            record.pop("output_check", None)
            record["error_code"] = "PROJECT_EXPORT_FAILED"
        record["file_path"] = ""
        record["failed_at"] = _now()
        project = None
        try:
            project = WritingProject.model_validate(record.get("project_snapshot") or {})
        except Exception:
            pass
        organization_id = _organization_id(store, project) if project else ""
        try:
            with store.workflow_transaction() as conn:
                job = _export_lease(conn, record, lease_token)
                queue_state = _finish_export_job(
                    conn, job, lease_token, error=record["error_code"], retry_delay_seconds=retry_delay_seconds,
                    terminal=record["error_code"] in {"PROJECT_EXPORT_DEADLINE_EXCEEDED", "PROJECT_EXPORT_DEADLINE_INVALID"},
                )
                saved = store.save_workflow_record_conn(
                    conn, "project_export", record, expected_revision=record.get("revision", 0),
                )
                if saved is None:
                    raise _ExportRecordConflict
                store.save_audit_event_conn(conn, {
                    "id": f"audit-{export_id}-attempt-{record.get('attempt', 0)}-failed",
                    "actor_id": actor_id,
                    "action": "project.export.failed",
                    "resource_type": "project_export",
                    "resource_id": export_id,
                    "organization_id": organization_id,
                    "result": "failure",
                    "reason": record["error_code"],
                    "created_at": record["failed_at"],
                })
                if published:
                    final_path.unlink(missing_ok=True)
            return {**saved, "_queue_state": queue_state}
        except _ExportLeaseLost:
            return {"status": "failed", "error_code": "PROJECT_EXPORT_LEASE_LOST"}
        except _ExportRecordConflict:
            return {"status": "failed", "error_code": "EXPORT_RECORD_CONFLICT"}
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
