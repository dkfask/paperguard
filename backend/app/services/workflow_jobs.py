"""Durable workflow jobs used by the standalone worker process."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from app.core.context import RequestContext
from app.core.structured_logging import log_event
from app.db import Store
from app.repositories.workflow_repository import WorkflowRepository


LOGGER = logging.getLogger("paperguard.worker.jobs")
LOGGER.setLevel("INFO")


_OUTBOX_MAX_ATTEMPTS = 5


def consume_archive_jobs(store: Store, *, consumer_id: str, limit: int = 20) -> dict[str, Any]:
    """Claim and execute durable archive jobs outside the web process."""
    from app.api.workflow import _run_archive, _now

    repository = WorkflowRepository(store)
    claimed = repository.claim_jobs(
        job_type="archive.generate", consumer_id=consumer_id,
        limit=max(1, min(limit, 50)), lease_seconds=300,
    )
    succeeded: list[str] = []
    failed: list[dict[str, str]] = []
    for job in claimed:
        job_context = RequestContext(
            actor_id="system", role="system", request_id=f"job-{job['id']}"
        )
        log_event(
            LOGGER, "job.started", context=job_context,
            job_id=job["id"], job_type=job["job_type"],
            resource_type=job["resource_type"], resource_id=job["resource_id"],
            attempt_count=job["attempt_count"], consumer_id=consumer_id,
        )
        archive = store.get_workflow_record("archive", job["resource_id"])
        if not archive:
            result = repository.fail_job(
                job["id"], error="归档任务关联记录不存在",
                lease_token=job.get("lease_token", ""),
            )
            if result:
                failed.append({"job_id": job["id"], "state": result["state"], "error": result.get("last_error") or ""})
                log_event(
                    LOGGER, "job.failed", context=job_context,
                    job_id=job["id"], job_type=job["job_type"],
                    state=result["state"], error=result.get("last_error") or "",
                )
            continue
        result = _run_archive(store, archive["id"], actor_id="system")
        if result.get("status") == "succeeded":
            if repository.complete_job(job["id"], lease_token=job.get("lease_token", "")):
                succeeded.append(job["id"])
                log_event(
                    LOGGER, "job.completed", context=job_context,
                    job_id=job["id"], job_type=job["job_type"], state="succeeded",
                )
            continue
        failed_job = repository.fail_job(
            job["id"], error=result.get("error_code") or "归档生成失败",
            lease_token=job.get("lease_token", ""), retry_delay_seconds=5,
        )
        if failed_job:
            failed.append({"job_id": job["id"], "state": failed_job["state"], "error": failed_job.get("last_error") or ""})
            log_event(
                LOGGER, "job.failed", context=job_context,
                job_id=job["id"], job_type=job["job_type"],
                state=failed_job["state"], error=failed_job.get("last_error") or "",
            )
    return {"claimed": len(claimed), "succeeded": succeeded, "failed": failed, "at": _now()}


def consume_project_export_jobs(store: Store, *, consumer_id: str, limit: int = 20) -> dict[str, Any]:
    """Claim and execute durable project export jobs outside the web process."""
    from app.services.export_jobs import EXPORT_JOB_TYPE, run_project_export
    from app.api.workflow import _now

    repository = WorkflowRepository(store)
    claimed = repository.claim_jobs(
        job_type=EXPORT_JOB_TYPE, consumer_id=consumer_id,
        limit=max(1, min(limit, 50)), lease_seconds=300,
    )
    succeeded: list[str] = []
    failed: list[dict[str, str]] = []
    for job in claimed:
        job_context = RequestContext(actor_id="system", role="system", request_id=f"job-{job['id']}")
        log_event(
            LOGGER, "job.started", context=job_context, job_id=job["id"],
            job_type=job["job_type"], resource_type=job["resource_type"],
            resource_id=job["resource_id"], attempt_count=job["attempt_count"], consumer_id=consumer_id,
        )
        result = run_project_export(store, job["resource_id"], actor_id="system")
        if result.get("status") == "succeeded":
            if repository.complete_job(job["id"], lease_token=job.get("lease_token", "")):
                succeeded.append(job["id"])
                log_event(LOGGER, "job.completed", context=job_context, job_id=job["id"], job_type=job["job_type"], state="succeeded")
            continue
        failed_job = repository.fail_job(
            job["id"], error=result.get("error_code") or "项目导出失败",
            lease_token=job.get("lease_token", ""), retry_delay_seconds=5,
        )
        if failed_job:
            failed.append({"job_id": job["id"], "state": failed_job["state"], "error": failed_job.get("last_error") or ""})
            log_event(LOGGER, "job.failed", context=job_context, job_id=job["id"], job_type=job["job_type"], state=failed_job["state"], error=failed_job.get("last_error") or "")
    return {"claimed": len(claimed), "succeeded": succeeded, "failed": failed, "at": _now()}


def consume_in_app_outbox(store: Store, *, consumer_id: str, limit: int = 100) -> dict[str, Any]:
    """Deliver persisted in-app notifications and acknowledge them under a lease."""
    from app.api.workflow import _now

    pending = store.list_pending_workflow_events(limit=max(1, min(limit, 200)))
    claimed = store.claim_workflow_events(
        [item["id"] for item in pending], consumer_id=consumer_id,
        lease_seconds=120, max_attempts=_OUTBOX_MAX_ATTEMPTS,
    )
    delivered: list[str] = []
    failed: list[dict[str, str]] = []
    for event in claimed:
        recipient = store.get_user(event.get("recipient_id", ""))
        if recipient and recipient.get("status") == "active":
            if store.mark_workflow_event_delivered_with_lease(event["id"], event.get("lease_token", "")):
                delivered.append(event["id"])
            continue
        reason = "通知接收账号不存在或已禁用"
        failed_event = store.fail_workflow_event(
            event["id"], error=reason, lease_token=event.get("lease_token", ""),
            max_attempts=_OUTBOX_MAX_ATTEMPTS,
        )
        if failed_event:
            failed.append({"event_id": event["id"], "status": failed_event["delivery_status"], "error": reason})
    return {"claimed": len(claimed), "delivered": delivered, "failed": failed, "at": _now()}


def create_system_due_reminders(store: Store) -> dict[str, Any]:
    """Create deduplicated deadline reminders for all active participants."""
    from app.api.workflow import _ACTIVE_STUDENT_STATES, _current_supervision, _notify, _now, _parse_time

    created: list[str] = []
    now = datetime.now(timezone.utc)
    for participant in store.list_workflow_records("participant"):
        if participant.get("state") not in _ACTIVE_STUDENT_STATES:
            continue
        cohort = store.get_workflow_record("cohort", participant.get("cohort_id", ""))
        if not cohort:
            continue
        deadline = _parse_time(participant.get("revision_deadline_at") or participant.get("effective_deadline_at", ""), "截止时间")
        if not deadline:
            continue
        remaining = (deadline - now).total_seconds()
        if remaining > 72 * 3600:
            continue
        kind = "overdue" if remaining < 0 else "deadline_soon"
        dedupe_key = f"reminder:{kind}:{participant['id']}:{participant.get('revision', 0)}:{deadline.isoformat()}"
        with store.workflow_transaction() as conn:
            inserted = _notify(
                store, conn, participant["student_id"], f"participant.{kind}", "participant", participant["id"],
                "阶段任务已逾期，请尽快处理" if kind == "overdue" else "阶段任务即将截止，请及时提交",
                dedupe_key=dedupe_key,
            )
            if inserted:
                created.append(participant["id"])
            relation = _current_supervision(store, participant["cohort_id"], participant["student_id"])
            if relation:
                _notify(
                    store, conn, relation["teacher_id"], f"participant.{kind}", "participant", participant["id"],
                    "指导学生任务临近截止" if kind == "deadline_soon" else "指导学生任务已逾期",
                    dedupe_key=f"{dedupe_key}:teacher",
                )
    return {"created": created, "generated_at": _now()}
