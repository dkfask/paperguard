"""Isolated acceptance: real admin HTTP audit, fresh processes and recoverable assets.

Only synthetic accounts/data are used. Child applications run from a copied source
checkout under tmp_path, so hard-coded asset directories cannot touch real data.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.auth import seed_admin
from app.db import Store, new_id
from app.main import app
from app.models.project import WritingProject
from app.models.template import PaperTemplate
from app.services.backup_restore import create_backup, restore_backup
from app.services.export_jobs import EXPORT_JOB_TYPE, create_export_job


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(data))
    monkeypatch.setenv("PAPERGUARD_DB_PATH", str(data / "runtime.db"))
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "0123456789abcdef" * 4)
    store = Store()
    seed_admin(store)
    return store, data


def test_admin_noop_save_is_visible_in_scoped_audit_http(isolated_store):
    store, _ = isolated_store
    secret = "synthetic-admin-key-not-a-real-credential"
    cfg = {"base_url": "https://audit.vendor.example/v1", "api_key": secret,
           "model": "isolated-admin-audit"}
    store.set_system_ai_config(cfg)
    client = TestClient(app)
    login = client.post("/api/auth/login", json={
        "username": "admin", "password": os.environ["PAPERGUARD_ADMIN_PASSWORD"],
    })
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    response = client.put("/api/ai/admin/config", headers=headers,
                          json={**cfg, "api_key": ""})
    assert response.status_code == 200, response.text
    assert store.get_system_ai_config() == cfg  # blank key preserves the exact previous key
    audit = client.get("/api/audit", headers=headers, params={
        "action": "ai.config.system_saved", "resource_type": "ai_config:system",
    })
    assert audit.status_code == 200, audit.text
    rows = audit.json()
    assert len(rows) == 1 and rows[0]["result"] == "success"
    assert rows[0]["actor_id"] == store.get_user_by_username("admin")["id"]
    assert rows[0]["created_at"]  # request_id is currently optional in the audit contract
    assert "audit.vendor.example" in rows[0]["reason"]
    assert secret not in audit.text and "pgsec1$" not in audit.text


def _child_checkout(tmp_path):
    source = Path(__file__).resolve().parents[1] / "app"
    checkout = tmp_path / "child-backend"
    checkout.mkdir()
    shutil.copytree(source, checkout / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*"))
    return checkout


def _run_process(checkout, code=None):
    env = {**os.environ, "PYTHONPATH": str(checkout), "PAPERGUARD_WORKER_ONCE": "1",
           "PAPERGUARD_WORKER_ID": "isolated-recovery-worker",
           "PAPERGUARD_TEMPLATE_DIR": str(checkout / "data" / "templates"),
           "PAPERGUARD_SMTP_HOST": "", "PAPERGUARD_RESEND_API_KEY": "",
           "PAPERGUARD_AI_API_KEY": "", "PAPERGUARD_WECHAT_APP_SECRET": "",
           "PAPERGUARD_WECHAT_APP_ID": "", "PAPERGUARD_MP_BINDING_SECRET": ""}
    args = [sys.executable, "-c", code] if code else [sys.executable, "-m", "app.worker"]
    run = subprocess.run(args, cwd=checkout, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return run


def test_worker_restart_recovers_expired_outbox_without_duplicate(isolated_store, tmp_path):
    store, _ = isolated_store
    admin = store.get_user_by_username("admin")
    with store.workflow_transaction() as conn:
        assert store.save_workflow_event_conn(
            conn, event_type="recovery.fixture", recipient_id=admin["id"],
            resource_type="synthetic", resource_id="outbox-recovery",
            dedupe_key="isolated-recovery-event", data={"title": "synthetic recovery notification"},
        )
    claimed = store.claim_workflow_events(
        [row["id"] for row in store.list_pending_workflow_events()],
        consumer_id="interrupted-worker", lease_seconds=30,
    )
    assert len(claimed) == 1
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE workflow_events SET lease_until=? WHERE id=?",
                     ("2000-01-01T00:00:00+00:00", claimed[0]["id"]))
    checkout = _child_checkout(tmp_path)
    first = _run_process(checkout)
    assert "worker.iteration.completed" in first.stderr
    assert store.list_pending_workflow_events() == []
    with sqlite3.connect(store._db_path) as conn:
        before = conn.execute("SELECT delivery_status, attempt_count FROM workflow_events").fetchall()
    _run_process(checkout)
    with sqlite3.connect(store._db_path) as conn:
        after = conn.execute("SELECT delivery_status, attempt_count FROM workflow_events").fetchall()
    assert before == after == [("delivered", 2)]


def test_backend_and_worker_restart_recover_interrupted_export_and_fail_closed(isolated_store, tmp_path):
    store, _ = isolated_store
    admin = store.get_user_by_username("admin")
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic recovery template")
    project = WritingProject(id=new_id("proj"), template_id=template.id,
                             title="Synthetic recovery paper", owner_id=admin["id"])
    export = create_export_job(store, project, template, force=True, actor_id=admin["id"])
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="interrupted-worker", lease_seconds=30)
    assert len(claimed) == 1
    export = store.get_workflow_record("project_export", export["id"])
    export["status"] = "running"
    store.save_workflow_record("project_export", export, expected_revision=export["revision"])
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until=? WHERE id=?",
                     ("2000-01-01T00:00:00+00:00", claimed[0]["id"]))
    checkout = _child_checkout(tmp_path)
    _run_process(checkout, "from fastapi.testclient import TestClient; from app.main import app; "
                 "\nwith TestClient(app) as client:\n assert client.get('/api/health/ready').status_code == 200")
    recovered = store.get_workflow_record("project_export", export["id"])
    assert recovered["status"] == "queued" and recovered["recovery_reason"] == "process_restart"
    _run_process(checkout)
    completed = store.get_workflow_record("project_export", export["id"])
    assert completed["status"] == "failed", completed
    assert not completed.get("file_path")
    assert completed.get("output_check_diagnostic", {}).get("findings")
    _run_process(checkout)
    after_restart = store.get_workflow_record("project_export", export["id"])
    assert after_restart["status"] == "failed" and not after_restart.get("file_path")
    jobs = store.list_jobs(resource_type="project_export", resource_id=export["id"])
    assert len(jobs) == 1 and jobs[0]["state"] in {"queued", "failed"}
    assert jobs[0]["state"] != "succeeded" and jobs[0]["attempt_count"] >= 2


def test_expired_final_export_attempt_is_not_reclaimed(isolated_store, monkeypatch):
    from app.services import export_jobs
    from app.services.workflow_jobs import consume_project_export_jobs

    store, _ = isolated_store
    admin = store.get_user_by_username("admin")
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic retry budget template")
    project = WritingProject(id=new_id("proj"), template_id=template.id, owner_id=admin["id"])
    export = create_export_job(store, project, template, force=False, actor_id=admin["id"])
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET max_attempts = 1 WHERE id = ?", (export["job_id"],))
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="interrupted-worker")
    assert len(claimed) == 1
    export["status"] = "running"
    store.save_workflow_record("project_export", export, expected_revision=export["revision"])
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?",
                     ("2000-01-01T00:00:00+00:00", export["job_id"]))
    invoked = []
    monkeypatch.setattr(export_jobs, "run_project_export", lambda *_args, **_kwargs: invoked.append(True))

    consume_project_export_jobs(store, consumer_id="replacement-worker")

    assert not invoked
    job = store.get_job(export["job_id"])
    assert job["state"] == "failed" and job["attempt_count"] == 1
    record = store.get_workflow_record("project_export", export["id"])
    assert record["status"] == "failed" and record["error_code"] == "PROJECT_EXPORT_ATTEMPTS_EXHAUSTED"
    assert record["project_snapshot"] == export["project_snapshot"]
    assert not record.get("file_path")


def test_missing_export_queue_state_does_not_reset_retry_budget(isolated_store):
    from app.services.export_jobs import recover_pending_exports

    store, _ = isolated_store
    admin = store.get_user_by_username("admin")
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic missing queue template")
    project = WritingProject(id=new_id("proj"), template_id=template.id, owner_id=admin["id"])
    export = create_export_job(store, project, template, force=False, actor_id=admin["id"])
    assert store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="interrupted-worker")
    export["status"] = "running"
    store.save_workflow_record("project_export", export, expected_revision=export["revision"])
    with store.workflow_transaction() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (export["job_id"],))

    recover_pending_exports(store)

    assert store.get_job(export["job_id"]) is None
    record = store.get_workflow_record("project_export", export["id"])
    assert record["status"] == "failed" and record["error_code"] == "PROJECT_EXPORT_QUEUE_STATE_MISSING"
    assert record["project_snapshot"] == export["project_snapshot"]
    assert not record.get("file_path")


def test_export_recovery_does_not_interrupt_a_live_lease(isolated_store):
    from app.services.export_jobs import recover_pending_exports

    store, _ = isolated_store
    admin = store.get_user_by_username("admin")
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic live lease template")
    project = WritingProject(id=new_id("proj"), template_id=template.id, owner_id=admin["id"])
    export = create_export_job(store, project, template, force=False, actor_id=admin["id"])
    assert store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="active-worker")
    export["status"] = "running"
    store.save_workflow_record("project_export", export, expected_revision=export["revision"])

    recover_pending_exports(store)

    assert store.get_workflow_record("project_export", export["id"])["status"] == "running"
    assert store.get_job(export["job_id"])["state"] == "running"


@pytest.mark.parametrize("stale_raises", [False, True])
def test_expired_export_worker_cannot_publish_after_replacement(isolated_store, tmp_path, monkeypatch, stale_raises):
    from app.services import export_jobs

    store, _ = isolated_store
    admin = store.get_user_by_username("admin")
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic stale worker template")
    project = WritingProject(id=new_id("proj"), template_id=template.id, owner_id=admin["id"])
    export = create_export_job(store, project, template, force=False, actor_id=admin["id"])
    first = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="worker-a")[0]
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(export_jobs, "require_docx_output_pass", lambda *_args, **_kwargs: {"source_template_sha256": ""})
    calls = []

    def render(_project, _template, output_path, **_kwargs):
        calls.append(output_path)
        if len(calls) == 1:
            with store.workflow_transaction() as conn:
                conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?",
                             ("2000-01-01T00:00:00+00:00", export["job_id"]))
            replacement = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="worker-b")[0]
            result = export_jobs.run_project_export(store, export["id"], lease_token=replacement["lease_token"])
            assert result["status"] == "succeeded"
            Path(output_path).write_bytes(b"stale worker artifact")
            if stale_raises:
                raise RuntimeError("stale worker failed after replacement")
        else:
            Path(output_path).write_bytes(b"replacement worker artifact")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", render)
    stale = export_jobs.run_project_export(store, export["id"], lease_token=first["lease_token"])

    assert stale["status"] == "failed" and stale["error_code"] == "PROJECT_EXPORT_LEASE_LOST"
    saved = store.get_workflow_record("project_export", export["id"])
    assert saved["status"] == "succeeded"
    assert Path(saved["file_path"]).read_bytes() == b"replacement worker artifact"
    assert store.get_job(export["job_id"])["state"] == "succeeded"
    outcomes = [event["action"] for event in store.list_audit_events()
                if event["resource_id"] == export["id"] and event["action"] in {
                    "project.export.succeeded", "project.export.failed",
                }]
    assert outcomes == ["project.export.succeeded"]


def test_expired_final_worker_cannot_publish_after_parking(isolated_store, tmp_path, monkeypatch):
    from app.services import export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic exhausted lease template")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET max_attempts = 1 WHERE id = ?", (export["job_id"],))
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="worker-a")[0]
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(export_jobs, "require_docx_output_pass", lambda *_args, **_kwargs: {"source_template_sha256": ""})
    original = tmp_path / f"{export['id']}.docx"
    original.write_bytes(b"existing artifact")

    def render(_project, _template, output_path, **_kwargs):
        with store.workflow_transaction() as conn:
            conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?",
                         ("2000-01-01T00:00:00+00:00", export["job_id"]))
        export_jobs.park_exhausted_project_exports(store)
        Path(output_path).write_bytes(b"expired artifact")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", render)
    result = export_jobs.run_project_export(store, export["id"], lease_token=claimed["lease_token"])

    assert result["error_code"] == "PROJECT_EXPORT_LEASE_LOST"
    assert original.read_bytes() == b"existing artifact"
    assert store.get_job(export["job_id"])["state"] == "failed"
    saved = store.get_workflow_record("project_export", export["id"])
    assert saved["status"] == "failed" and saved["error_code"] == "PROJECT_EXPORT_ATTEMPTS_EXHAUSTED"
    assert not any(event["resource_id"] == export["id"] and event["action"] in {
        "project.export.succeeded", "project.export.failed",
    } for event in store.list_audit_events())


@pytest.mark.parametrize("render_fails", [False, True])
def test_expired_export_lease_cannot_finish_before_reclaim(isolated_store, tmp_path, monkeypatch, render_fails):
    from app.services import export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic expired lease template")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="worker-a")[0]
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(export_jobs, "require_docx_output_pass", lambda *_args, **_kwargs: {"source_template_sha256": ""})

    def render(_project, _template, output_path, **_kwargs):
        with store.workflow_transaction() as conn:
            conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?",
                         ("2000-01-01T00:00:00+00:00", export["job_id"]))
        if render_fails:
            raise RuntimeError("synthetic failure after lease expiry")
        Path(output_path).write_bytes(b"expired artifact")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", render)
    result = export_jobs.run_project_export(store, export["id"], lease_token=claimed["lease_token"])

    assert result["error_code"] == "PROJECT_EXPORT_LEASE_LOST"
    assert store.get_job(export["job_id"])["state"] == "running"
    assert store.get_workflow_record("project_export", export["id"])["status"] == "running"
    assert not (tmp_path / f"{export['id']}.docx").exists()
    assert not any(event["resource_id"] == export["id"] and event["action"] in {
        "project.export.succeeded", "project.export.failed",
    } for event in store.list_audit_events())


@pytest.mark.parametrize("render_fails", [False, True])
def test_export_record_conflict_rolls_back_queue_and_does_not_publish(isolated_store, tmp_path, monkeypatch, render_fails):
    from app.services import export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic export conflict template")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="worker-a")[0]
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(export_jobs, "require_docx_output_pass", lambda *_args, **_kwargs: {"source_template_sha256": ""})

    def render(_project, _template, output_path, **_kwargs):
        current = store.get_workflow_record("project_export", export["id"])
        current["recovery_reason"] = "concurrent-update"
        assert store.save_workflow_record("project_export", current, expected_revision=current["revision"])
        Path(output_path).write_bytes(b"conflicting artifact")
        if render_fails:
            raise RuntimeError("synthetic render failure after concurrent update")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", render)
    result = export_jobs.run_project_export(store, export["id"], lease_token=claimed["lease_token"])

    assert result["error_code"] == "EXPORT_RECORD_CONFLICT"
    assert store.get_job(export["job_id"])["state"] == "running"
    assert store.get_workflow_record("project_export", export["id"])["recovery_reason"] == "concurrent-update"
    assert not (tmp_path / f"{export['id']}.docx").exists()
    assert not any(event["resource_id"] == export["id"] and event["action"] in {
        "project.export.succeeded", "project.export.failed",
    }
                   for event in store.list_audit_events())


def test_export_success_audit_failure_cannot_publish(isolated_store, tmp_path, monkeypatch):
    from app.services import export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic export audit template")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="worker-a")[0]
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(export_jobs, "require_docx_output_pass", lambda *_args, **_kwargs: {"source_template_sha256": ""})
    monkeypatch.setattr(export_jobs, "export_project_to_docx",
                        lambda _project, _template, output_path, **_kwargs: Path(output_path).write_bytes(b"artifact"))
    save_audit = store.save_audit_event_conn

    def reject_success(conn, event):
        if event["action"] == "project.export.succeeded":
            raise RuntimeError("synthetic audit failure")
        return save_audit(conn, event)

    monkeypatch.setattr(store, "save_audit_event_conn", reject_success)
    result = export_jobs.run_project_export(store, export["id"], lease_token=claimed["lease_token"])

    assert result["status"] == "failed" and result["_queue_state"] == "queued"
    assert store.get_job(export["job_id"])["state"] == "queued"
    saved = store.get_workflow_record("project_export", export["id"])
    assert saved["status"] == "failed"
    assert "completed_at" not in saved and "completed_by" not in saved and "output_check" not in saved
    assert not (tmp_path / f"{export['id']}.docx").exists()
    outcomes = [event["action"] for event in store.list_audit_events()
                if event["resource_id"] == export["id"] and event["action"] in {
                    "project.export.succeeded", "project.export.failed",
                }]
    assert outcomes == ["project.export.failed"]


def test_missing_export_record_returns_claim_to_queue(isolated_store):
    from app.services.workflow_jobs import consume_project_export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic missing record template")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    with store.workflow_transaction() as conn:
        conn.execute("DELETE FROM workflow_records WHERE kind = 'project_export' AND id = ?", (export["id"],))

    result = consume_project_export_jobs(store, consumer_id="worker-a")

    assert result["failed"] == [{"job_id": export["job_id"], "state": "queued", "error": "EXPORT_RECORD_NOT_FOUND"}]
    assert store.get_job(export["job_id"])["attempt_count"] == 1


def test_direct_export_cannot_steal_active_lease_or_reset_attempt_budget(isolated_store, tmp_path, monkeypatch):
    from app.services import export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Synthetic direct export template")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET max_attempts = 1 WHERE id = ?", (export["job_id"],))
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="worker-a")[0]
    assert export_jobs.run_project_export(store, export["id"])["error_code"] == "PROJECT_EXPORT_LEASE_LOST"
    assert store.get_job(export["job_id"])["lease_token"] == claimed["lease_token"]
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)

    def fail_render(*_args, **_kwargs):
        raise RuntimeError("synthetic final attempt failure")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", fail_render)
    result = export_jobs.run_project_export(store, export["id"], lease_token=claimed["lease_token"])
    assert result["status"] == "failed" and result["_queue_state"] == "failed"
    assert export_jobs.run_project_export(store, export["id"])["error_code"] == "PROJECT_EXPORT_LEASE_LOST"
    assert store.get_job(export["job_id"])["attempt_count"] == 1


def test_restore_failure_rolls_back_database_and_uploads_together(isolated_store, tmp_path, monkeypatch):
    store, data = isolated_store
    upload = data / "uploads" / "synthetic.txt"
    upload.parent.mkdir()
    upload.write_text("backup generation")
    store.set_system_setting("recovery.fixture", "backup generation")
    bundle = tmp_path / "backup.zip"
    create_backup(bundle, db_path=store._db_path, data_dir=data)
    upload.write_text("current generation")
    store.set_system_setting("recovery.fixture", "current generation")
    original_copy = shutil.copy2

    def fail_database_copy(source, destination, *args, **kwargs):
        if Path(destination) == store._db_path and "database" in Path(source).parts:
            raise OSError("isolated restore write failure")
        return original_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", fail_database_copy)
    with pytest.raises(OSError, match="isolated restore write failure"):
        restore_backup(bundle, db_path=store._db_path, data_dir=data)
    assert upload.read_text() == "current generation"
    assert Store(store._db_path).get_system_setting("recovery.fixture") == "current generation"
    monkeypatch.setattr(shutil, "copy2", original_copy)
    restored = restore_backup(bundle, db_path=store._db_path, data_dir=data)
    assert restored["rollback_data"]
    assert upload.read_text() == "backup generation"
    assert Store(store._db_path).get_system_setting("recovery.fixture") == "backup generation"


def test_queued_export_deadline_expires_before_claim_and_new_request_is_linked(isolated_store, monkeypatch):
    from app.services import export_jobs
    from app.services.workflow_jobs import consume_project_export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Deadline test")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    original_snapshot = export["project_snapshot"]
    export["deadline_at"] = "2000-01-01T00:00:00+00:00"
    assert store.save_workflow_record("project_export", export, expected_revision=export["revision"])
    monkeypatch.setattr(export_jobs, "export_project_to_docx", lambda *_args, **_kwargs: pytest.fail("expired job rendered"))

    result = consume_project_export_jobs(store, consumer_id="deadline-worker")

    saved = store.get_workflow_record("project_export", export["id"])
    job = store.get_job(export["job_id"])
    assert result["claimed"] == 0
    assert saved["status"] == "failed" and saved["error_code"] == "PROJECT_EXPORT_DEADLINE_EXCEEDED"
    assert saved["project_snapshot"] == original_snapshot and not saved["file_path"]
    assert job["state"] == "cancelled" and job["attempt_count"] == 0
    replacement = create_export_job(store, project, template, force=False, actor_id="system")
    assert replacement["id"] != export["id"] and replacement["previous_export_id"] == export["id"]
    assert store.get_workflow_record("project_export", export["id"])["project_snapshot"] == original_snapshot


def test_expired_export_does_not_interrupt_active_lease(isolated_store, monkeypatch):
    from app.services import export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Active deadline test")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    export["deadline_at"] = "2000-01-01T00:00:00+00:00"
    assert store.save_workflow_record("project_export", export, expected_revision=export["revision"])
    claim = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="active")[0]
    export_jobs.park_expired_project_exports(store)
    assert store.get_job(export["job_id"])["lease_token"] == claim["lease_token"]
    monkeypatch.setattr(export_jobs, "export_project_to_docx", lambda *_args, **_kwargs: pytest.fail("expired job rendered"))

    result = export_jobs.run_project_export(store, export["id"], lease_token=claim["lease_token"])

    assert result["error_code"] == "PROJECT_EXPORT_DEADLINE_EXCEEDED"
    assert store.get_job(export["job_id"])["state"] == "cancelled"
    assert not store.get_workflow_record("project_export", export["id"])["file_path"]


@pytest.mark.parametrize("phase", ["render", "check", "publication"])
def test_export_deadline_at_each_phase_never_publishes(isolated_store, tmp_path, monkeypatch, phase):
    from types import SimpleNamespace
    from app.services import export_jobs, export_time_budget

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Deadline during export")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    clock = {"seconds": 0}
    monkeypatch.setattr(export_time_budget, "time", SimpleNamespace(monotonic=lambda: clock["seconds"]))
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)

    def render(_project, _template, output_path, **_kwargs):
        Path(output_path).write_bytes(b"temporary artifact")
        if phase == "render":
            clock["seconds"] = 1000

    def inspect(*_args, **_kwargs):
        if phase == "check":
            clock["seconds"] = 1000
        return {"source_template_sha256": ""}

    original_audit = store.save_audit_event_conn

    def audit(conn, event):
        result = original_audit(conn, event)
        if phase == "publication" and event["action"] == "project.export.succeeded":
            clock["seconds"] = 1000
        return result

    monkeypatch.setattr(export_jobs, "export_project_to_docx", render)
    monkeypatch.setattr(export_jobs, "require_docx_output_pass", inspect)
    monkeypatch.setattr(store, "save_audit_event_conn", audit)
    result = export_jobs.run_project_export(store, export["id"])

    assert result["error_code"] == "PROJECT_EXPORT_DEADLINE_EXCEEDED"
    assert store.get_job(export["job_id"])["state"] == "cancelled"
    assert not (tmp_path / f"{export['id']}.docx").exists()
    assert not any(event["action"] == "project.export.succeeded" and event["resource_id"] == export["id"]
                   for event in store.list_audit_events())


def test_lease_budget_failure_preserves_total_deadline_for_retry_and_restart(isolated_store, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.services import export_jobs, export_time_budget

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Retry deadline test")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    deadline = export["deadline_at"]
    clock = {"seconds": 0}
    monkeypatch.setattr(export_time_budget, "time", SimpleNamespace(monotonic=lambda: clock["seconds"]))
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)

    def render(_project, _template, output_path, **_kwargs):
        Path(output_path).write_bytes(b"late attempt")
        clock["seconds"] = export_time_budget.EXPORT_JOB_LEASE_SECONDS - 9

    monkeypatch.setattr(export_jobs, "export_project_to_docx", render)
    result = export_jobs.run_project_export(store, export["id"])
    assert result["error_code"] == "PROJECT_EXPORT_ATTEMPT_TIMEOUT"
    assert store.get_job(export["job_id"])["state"] == "queued"
    monkeypatch.setenv("PAPERGUARD_EXPORT_TOTAL_SECONDS", "3600")
    export_jobs.recover_pending_exports(store)
    saved = store.get_workflow_record("project_export", export["id"])
    assert saved["deadline_at"] == deadline
    assert saved["project_snapshot"] == export["project_snapshot"]
    assert not (tmp_path / f"{export['id']}.docx").exists()



def test_worker_export_lease_covers_extended_layout_and_stays_exclusive(isolated_store, monkeypatch):
    from datetime import datetime, timezone
    from app.services import export_jobs, export_time_budget
    from app.services.workflow_jobs import consume_project_export_jobs

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Extended lease test")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    seen = []

    def run_claimed(active_store, export_id, **kwargs):
        job = active_store.get_job(export["job_id"])
        remaining = (datetime.fromisoformat(job["lease_until"]) - datetime.now(timezone.utc)).total_seconds()
        seen.append(remaining)
        assert kwargs["lease_token"] == job["lease_token"]
        assert active_store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="other-worker") == []
        return {"status": "succeeded"}

    monkeypatch.setattr(export_jobs, "run_project_export", run_claimed)
    consume_project_export_jobs(store, consumer_id="first-worker", limit=1)
    assert len(seen) == 1
    assert export_time_budget.EXPORT_JOB_LEASE_SECONDS - 2 <= seen[0] <= export_time_budget.EXPORT_JOB_LEASE_SECONDS


def test_legacy_export_deadline_is_bound_once_from_creation_time(isolated_store, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.services.export_jobs import recover_pending_exports

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Legacy deadline test")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    export.pop("deadline_at")
    export["created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(timespec="seconds")
    assert store.save_workflow_record("project_export", export, expected_revision=export["revision"])
    monkeypatch.setenv("PAPERGUARD_EXPORT_TOTAL_SECONDS", "120")
    recover_pending_exports(store)
    first = store.get_workflow_record("project_export", export["id"])
    assert first["deadline_origin"] == "legacy_created_at"
    monkeypatch.setenv("PAPERGUARD_EXPORT_TOTAL_SECONDS", "3600")
    recover_pending_exports(store)
    assert store.get_workflow_record("project_export", export["id"])["deadline_at"] == first["deadline_at"]


def test_new_export_requests_obey_one_limit_even_when_force_changes(isolated_store, monkeypatch):
    from app.services.export_jobs import ExportRequestLimitExceeded, park_expired_project_exports

    store, _ = isolated_store
    monkeypatch.setenv("PAPERGUARD_EXPORT_REQUEST_LIMIT", "2")
    template = PaperTemplate(id=new_id("tpl"), name="Request budget test")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    attempts = []
    for force in (False, True):
        export = create_export_job(store, project, template, force=force, actor_id="system")
        attempts.append(export)
        export["deadline_at"] = "2000-01-01T00:00:00+00:00"
        assert store.save_workflow_record("project_export", export, expected_revision=export["revision"])
        park_expired_project_exports(store)
    assert attempts[1]["previous_export_id"] == attempts[0]["id"]
    with pytest.raises(ExportRequestLimitExceeded):
        create_export_job(store, project, template, force=False, actor_id="system")


def test_invalid_new_configuration_does_not_corrupt_legacy_export(isolated_store, monkeypatch):
    from app.services.export_jobs import recover_pending_exports
    from app.services.export_time_budget import (
        DEFAULT_EXPORT_TOTAL_SECONDS, InvalidExportBudget, new_export_deadline,
    )

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Legacy export with invalid configuration")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    export.pop("deadline_at")
    assert store.save_workflow_record("project_export", export, expected_revision=export["revision"])
    monkeypatch.setenv("PAPERGUARD_EXPORT_TOTAL_SECONDS", "invalid")

    recover_pending_exports(store)

    saved = store.get_workflow_record("project_export", export["id"])
    assert saved["status"] == "queued"
    assert saved["deadline_at"] == new_export_deadline(export["created_at"], DEFAULT_EXPORT_TOTAL_SECONDS)
    assert store.get_job(export["job_id"])["state"] == "queued"
    with pytest.raises(InvalidExportBudget):
        create_export_job(store, project, template, force=False, actor_id="system")


def test_corrupt_legacy_export_deadline_is_parked(isolated_store):
    from app.services.export_jobs import park_expired_project_exports

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Corrupt deadline")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    export = create_export_job(store, project, template, force=False, actor_id="system")
    export["deadline_at"] = "not-a-time"
    assert store.save_workflow_record("project_export", export, expected_revision=export["revision"])

    park_expired_project_exports(store)

    saved = store.get_workflow_record("project_export", export["id"])
    assert saved["status"] == "failed"
    assert saved["error_code"] == "PROJECT_EXPORT_DEADLINE_INVALID"
    assert store.get_job(export["job_id"])["state"] == "cancelled"


def test_new_request_recovers_missing_export_queue_without_waiting_for_restart(isolated_store):
    store, _ = isolated_store
    admin = store.get_user_by_username("admin")
    template = PaperTemplate(id=new_id("tpl"), name="Missing queue during export")
    project = WritingProject(id=new_id("proj"), template_id=template.id, owner_id=admin["id"])
    original = create_export_job(store, project, template, force=False, actor_id=admin["id"])
    with store.workflow_transaction() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (original["job_id"],))

    replacement = create_export_job(store, project, template, force=False, actor_id=admin["id"])

    old = store.get_workflow_record("project_export", original["id"])
    assert old["status"] == "failed" and old["error_code"] == "PROJECT_EXPORT_QUEUE_STATE_MISSING"
    assert old["project_snapshot"] == original["project_snapshot"] and not old["file_path"]
    assert replacement["id"] != original["id"]
    assert replacement["previous_export_id"] == original["id"]
    assert store.get_job(replacement["job_id"])["state"] == "queued"


def test_missing_queue_failure_is_retained_even_at_request_limit(isolated_store, monkeypatch):
    from app.services.export_jobs import ExportRequestLimitExceeded

    store, _ = isolated_store
    monkeypatch.setenv("PAPERGUARD_EXPORT_REQUEST_LIMIT", "1")
    template = PaperTemplate(id=new_id("tpl"), name="Missing queue at limit")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    original = create_export_job(store, project, template, force=False, actor_id="system")
    with store.workflow_transaction() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (original["job_id"],))

    with pytest.raises(ExportRequestLimitExceeded):
        create_export_job(store, project, template, force=True, actor_id="system")

    saved = store.get_workflow_record("project_export", original["id"])
    assert saved["status"] == "failed" and saved["error_code"] == "PROJECT_EXPORT_QUEUE_STATE_MISSING"
    assert saved["project_snapshot"] == original["project_snapshot"]
    assert len([r for r in store.list_workflow_records("project_export") if r["project_id"] == project.id]) == 1


def test_recovery_does_not_modify_another_exports_live_queue(isolated_store):
    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Queue ownership")
    other = WritingProject(id=new_id("proj"), template_id=template.id)
    other_export = create_export_job(store, other, template, force=False, actor_id="system")
    own = WritingProject(id=new_id("proj"), template_id=template.id)
    original = create_export_job(store, own, template, force=False, actor_id="system")
    claimed = store.claim_jobs(job_type=EXPORT_JOB_TYPE, consumer_id="other-worker", limit=1)[0]
    assert claimed["id"] == other_export["job_id"]
    original["job_id"] = other_export["job_id"]
    assert store.save_workflow_record("project_export", original, expected_revision=original["revision"])

    replacement = create_export_job(store, own, template, force=False, actor_id="system")

    assert replacement["previous_export_id"] == original["id"]
    assert store.get_workflow_record("project_export", original["id"])["error_code"] == "PROJECT_EXPORT_QUEUE_STATE_MISSING"
    other_job = store.get_job(other_export["job_id"])
    assert other_job["state"] == "running" and other_job["lease_token"] == claimed["lease_token"]


@pytest.mark.parametrize(("queue_state", "lease", "attempts", "expected_error", "expected_queue_state"), [
    ("cancelled", None, 0, "PROJECT_EXPORT_QUEUE_STATE_INVALID", "cancelled"),
    ("running", None, 1, "PROJECT_EXPORT_QUEUE_STATE_INVALID", "cancelled"),
    ("queued", None, 5, "PROJECT_EXPORT_ATTEMPTS_EXHAUSTED", "failed"),
])
def test_unrecoverable_queue_does_not_reset_request_limit(
        isolated_store, monkeypatch, queue_state, lease, attempts, expected_error, expected_queue_state):
    from app.services.export_jobs import ExportRequestLimitExceeded

    store, _ = isolated_store
    monkeypatch.setenv("PAPERGUARD_EXPORT_REQUEST_LIMIT", "1")
    template = PaperTemplate(id=new_id("tpl"), name="Unrecoverable queue")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    original = create_export_job(store, project, template, force=False, actor_id="system")
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET state = ?, lease_until = ?, attempt_count = ?, max_attempts = 5 WHERE id = ?",
                     (queue_state, lease, attempts, original["job_id"]))

    with pytest.raises(ExportRequestLimitExceeded):
        create_export_job(store, project, template, force=True, actor_id="system")

    saved = store.get_workflow_record("project_export", original["id"])
    assert saved["status"] == "failed" and saved["error_code"] == expected_error
    assert store.get_job(original["job_id"])["state"] == expected_queue_state
    assert saved["project_snapshot"] == original["project_snapshot"]


def test_final_attempt_with_live_lease_is_not_replaced(isolated_store):
    from datetime import datetime, timedelta, timezone

    store, _ = isolated_store
    template = PaperTemplate(id=new_id("tpl"), name="Live final attempt")
    project = WritingProject(id=new_id("proj"), template_id=template.id)
    original = create_export_job(store, project, template, force=False, actor_id="system")
    lease_until = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(timespec="seconds")
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET state = 'running', lease_until = ?, lease_token = 'live', "
                     "attempt_count = 5, max_attempts = 5 WHERE id = ?", (lease_until, original["job_id"]))

    existing = create_export_job(store, project, template, force=True, actor_id="system")

    assert existing["id"] == original["id"]
    job = store.get_job(original["job_id"])
    assert job["state"] == "running" and job["lease_token"] == "live"
