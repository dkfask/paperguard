"""Repository boundary for versioned workflow records and durable jobs.

The repository intentionally delegates transaction ownership to ``Store`` so
existing SQLite behavior stays unchanged while API modules stop depending on
the storage method names directly.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.db import Store


class WorkflowRepository:
    def __init__(self, store: Store):
        self.store = store

    def get(self, kind: str, record_id: str) -> dict | None:
        return self.store.get_workflow_record(kind, record_id)

    def list(self, kind: str, limit: int = 500) -> list[dict]:
        return self.store.list_workflow_records(kind, limit=limit)

    def save(
        self,
        kind: str,
        record: dict,
        *,
        expected_revision: int | None = None,
    ) -> dict | None:
        return self.store.save_workflow_record(
            kind, record, expected_revision=expected_revision
        )

    def save_in_transaction(
        self,
        conn: sqlite3.Connection,
        kind: str,
        record: dict,
        *,
        expected_revision: int | None = None,
    ) -> dict | None:
        return self.store.save_workflow_record_conn(
            conn, kind, record, expected_revision=expected_revision
        )

    def delete(self, kind: str, record_id: str) -> bool:
        return self.store.delete_workflow_record(kind, record_id)

    def get_job(self, job_id: str) -> dict | None:
        return self.store.get_job(job_id)

    def list_jobs(self, **filters: Any) -> list[dict]:
        return self.store.list_jobs(**filters)

    def claim_jobs(self, **filters: Any) -> list[dict]:
        return self.store.claim_jobs(**filters)

    def complete_job(self, job_id: str, *, lease_token: str = "") -> dict | None:
        return self.store.complete_job(job_id, lease_token=lease_token)

    def fail_job(self, job_id: str, **values: Any) -> dict | None:
        return self.store.fail_job(job_id, **values)

    def get_job_in_transaction(self, conn: sqlite3.Connection, job_id: str):
        return self.store.get_job_conn(conn, job_id)

    def create_job_in_transaction(self, conn: sqlite3.Connection, **values: Any) -> dict:
        return self.store.create_job_conn(conn, **values)

    def retry_job_in_transaction(self, conn: sqlite3.Connection, job_id: str, reason: str = "") -> dict | None:
        return self.store.retry_job_conn(conn, job_id, reason)
