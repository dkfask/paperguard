"""Standalone scheduler and in-app outbox consumer for production deployments."""

from __future__ import annotations

import logging
import os
import time

from app.db import Store
from app.core.context import RequestContext
from app.core.structured_logging import log_event
from app.services.workflow_jobs import (
    consume_archive_jobs,
    consume_document_render_jobs,
    consume_in_app_outbox,
    consume_project_export_jobs,
    create_system_due_reminders,
)


LOGGER = logging.getLogger("paperguard.worker")


def run_once(store: Store | None = None) -> dict:
    store = store or Store()
    consumer_id = os.environ.get("PAPERGUARD_WORKER_ID", "paperguard-worker")
    reminders = create_system_due_reminders(store)
    archives = consume_archive_jobs(store, consumer_id=consumer_id)
    exports = consume_project_export_jobs(store, consumer_id=consumer_id)
    document_renders = consume_document_render_jobs(store, consumer_id=consumer_id)
    outbox = consume_in_app_outbox(store, consumer_id=consumer_id)
    return {
        "reminders": reminders,
        "archives": archives,
        "exports": exports,
        "document_renders": document_renders,
        "outbox": outbox,
    }


def main() -> None:
    logging.basicConfig(level=os.environ.get("PAPERGUARD_LOG_LEVEL", "INFO"))
    interval = max(5, int(os.environ.get("PAPERGUARD_WORKER_INTERVAL_SECONDS", "30")))
    once = os.environ.get("PAPERGUARD_WORKER_ONCE", "0") == "1"
    consumer_id = os.environ.get("PAPERGUARD_WORKER_ID", "paperguard-worker")
    while True:
        try:
            result = run_once()
            log_event(
                LOGGER, "worker.iteration.completed",
                context=RequestContext(actor_id="system", role="system", request_id=f"worker-{consumer_id}"),
                consumer_id=consumer_id,
                reminders_created=len(result["reminders"]["created"]),
                archives_claimed=result["archives"]["claimed"],
                archives_succeeded=len(result["archives"]["succeeded"]),
                exports_claimed=result["exports"]["claimed"],
                exports_succeeded=len(result["exports"]["succeeded"]),
                document_renders_claimed=result["document_renders"]["claimed"],
                document_renders_succeeded=len(result["document_renders"]["succeeded"]),
                outbox_claimed=result["outbox"]["claimed"],
                outbox_delivered=len(result["outbox"]["delivered"]),
            )
        except Exception:
            log_event(
                LOGGER, "worker.iteration.failed",
                context=RequestContext(actor_id="system", role="system", request_id=f"worker-{consumer_id}"),
                consumer_id=consumer_id,
            )
            LOGGER.exception("workflow worker iteration failed")
        if once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
