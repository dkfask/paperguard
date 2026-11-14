"""An expired worker cannot release a lease acquired by another worker."""

import sqlite3

import pytest

from app.db import Store


@pytest.mark.parametrize("kind", ["job", "event"])
def test_failure_does_not_release_a_reclaimed_lease(tmp_path, monkeypatch, kind):
    store = Store(tmp_path / "race.db")
    other = Store(tmp_path / "race.db")
    with store.workflow_transaction() as conn:
        if kind == "job":
            store.create_job_conn(conn, job_id="job-1", job_type="race", resource_type="test", resource_id="1")
        else:
            store.save_workflow_event_conn(conn, event_type="test", recipient_id="user-1", resource_type="test", resource_id="1", dedupe_key="race", data={})

    if kind == "job":
        first = store.claim_jobs(job_type="race", consumer_id="worker-a")[0]
        item_id = first["id"]
        table = "jobs"
    else:
        item_id = store.list_workflow_events("user-1")[0]["id"]
        first = store.claim_workflow_events([item_id], consumer_id="worker-a")[0]
        table = "workflow_events"

    with store._connect() as conn:
        conn.execute(f"UPDATE {table} SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", item_id))

    reclaimed = []

    class InterleavingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith(f"UPDATE {table} SET") and not reclaimed:
                assert not self.in_transaction
                if kind == "job":
                    reclaimed.extend(other.claim_jobs(job_type="race", consumer_id="worker-b"))
                else:
                    reclaimed.extend(other.claim_workflow_events([item_id], consumer_id="worker-b"))
                assert len(reclaimed) == 1
            return super().execute(sql, parameters)

    def interleaving_connect():
        conn = sqlite3.connect(store._db_path, timeout=10, factory=InterleavingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(store, "_connect", interleaving_connect)
    if kind == "job":
        result = store.fail_job(item_id, error="stale", lease_token=first["lease_token"])
        current = other.get_job(item_id)
        status_key, running = "state", "running"
    else:
        result = store.fail_workflow_event(item_id, error="stale", lease_token=first["lease_token"])
        current = other.list_workflow_events("user-1")[0]
        status_key, running = "delivery_status", "processing"
    assert result is None
    assert current[status_key] == running
    assert current["lease_token"] == reclaimed[0]["lease_token"]
    assert current["leased_by"] == "worker-b"
    assert current["attempt_count"] == 2
    if kind == "job":
        assert other.complete_job(item_id, lease_token=reclaimed[0]["lease_token"])["state"] == "succeeded"
    else:
        assert other.mark_workflow_event_delivered_with_lease(item_id, reclaimed[0]["lease_token"])


def test_unleased_event_cannot_be_failed(tmp_path):
    store = Store(tmp_path / "unleased.db")
    with store.workflow_transaction() as conn:
        store.save_workflow_event_conn(conn, event_type="test", recipient_id="user-1", resource_type="test", resource_id="1", dedupe_key="unleased", data={})
    event = store.list_workflow_events("user-1")[0]
    assert store.fail_workflow_event(event["id"], error="no lease") is None
    assert store.list_workflow_events("user-1")[0]["delivery_status"] == "pending"


def test_unleased_running_job_cannot_be_failed(tmp_path):
    store = Store(tmp_path / "unleased-job.db")
    with store.workflow_transaction() as conn:
        store.create_job_conn(conn, job_id="job-1", job_type="race", resource_type="test", resource_id="1")
        conn.execute("UPDATE jobs SET state = 'running', lease_token = '' WHERE id = ?", ("job-1",))
    assert store.fail_job("job-1", error="no lease") is None
    assert store.get_job("job-1")["state"] == "running"


@pytest.mark.parametrize("kind", ["job", "event"])
def test_current_lease_holder_can_park_at_retry_limit(tmp_path, kind):
    store = Store(tmp_path / "retry.db")
    with store.workflow_transaction() as conn:
        if kind == "job":
            store.create_job_conn(conn, job_id="job-1", job_type="race", resource_type="test", resource_id="1", max_attempts=1)
        else:
            store.save_workflow_event_conn(conn, event_type="test", recipient_id="user-1", resource_type="test", resource_id="1", dedupe_key="retry", data={})
    if kind == "job":
        claimed = store.claim_jobs(job_type="race", consumer_id="worker")[0]
        failed = store.fail_job(claimed["id"], error="failed", lease_token=claimed["lease_token"])
        assert failed["state"] == "failed"
    else:
        event_id = store.list_workflow_events("user-1")[0]["id"]
        claimed = store.claim_workflow_events([event_id], consumer_id="worker")[0]
        failed = store.fail_workflow_event(event_id, error="failed", lease_token=claimed["lease_token"], max_attempts=1)
        assert failed["delivery_status"] == "failed"
    assert failed["attempt_count"] == 1
    assert failed["lease_token"] is None
