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
