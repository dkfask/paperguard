from __future__ import annotations

import json
import zipfile

from click.testing import CliRunner
from fastapi.testclient import TestClient

from app.cli import cli
from app.auth import seed_admin
from app.db import Store
from app.main import app
from app.api.workflow import recover_pending_archives
from app.services.workflow_jobs import consume_in_app_outbox
from app.services.backup_restore import create_backup, restore_backup
from app.db import new_id


def test_backup_contains_database_and_persisted_assets_and_restores(tmp_path):
    db_path = tmp_path / "paperguard.db"
    data_dir = tmp_path / "data"
    data_dir.joinpath("uploads").mkdir(parents=True)
    data_dir.joinpath("uploads", "project-image.png").write_bytes(b"image")
    store = Store(db_path)
    store.set_system_setting("retention_policy", json.dumps({"draft_days": 30}))

    backup_path = tmp_path / "backup.zip"
    manifest = create_backup(backup_path, db_path=db_path, data_dir=data_dir)
    assert manifest["format"] == "paperguard-backup"
    with zipfile.ZipFile(backup_path) as bundle:
        assert "database/paperguard.db" in bundle.namelist()
        assert "data/uploads/project-image.png" in bundle.namelist()
        assert json.loads(bundle.read("backup-manifest.json"))["files"] == manifest["files"]

    data_dir.joinpath("uploads", "project-image.png").write_bytes(b"changed")
    store.set_system_setting("retention_policy", None)
    result = restore_backup(backup_path, db_path=db_path, data_dir=data_dir)
    assert result["rollback_data"] and result["rollback_db"]
    assert data_dir.joinpath("uploads", "project-image.png").read_bytes() == b"image"
    assert Store(db_path).get_system_setting("retention_policy") == json.dumps({"draft_days": 30})


def test_restore_when_database_is_inside_data_root(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "paperguard.db"
    store = Store(db_path)
    store.set_system_setting("retention_policy", json.dumps({"archive_days": 1825}))
    data_dir.joinpath("uploads").mkdir()
    data_dir.joinpath("uploads", "source.txt").write_text("original", encoding="utf-8")

    backup_path = tmp_path / "inside-root-backup.zip"
    create_backup(backup_path, db_path=db_path, data_dir=data_dir)
    store.set_system_setting("retention_policy", None)
    data_dir.joinpath("uploads", "source.txt").write_text("changed", encoding="utf-8")

    result = restore_backup(backup_path, db_path=db_path, data_dir=data_dir)

    assert result["rollback_data"]
    assert result["rollback_db"] == ""
    assert data_dir.joinpath("uploads", "source.txt").read_text(encoding="utf-8") == "original"
    assert Store(db_path).get_system_setting("retention_policy") == json.dumps({"archive_days": 1825})


def test_restore_rejects_backup_without_database_manifest(tmp_path):
    backup_path = tmp_path / "missing-db.zip"
    with zipfile.ZipFile(backup_path, "w") as bundle:
        bundle.writestr("backup-manifest.json", json.dumps({
            "format": "paperguard-backup", "format_version": 1,
            "database": "database/paperguard.db", "data_root": "data/", "files": [],
        }))

    try:
        restore_backup(backup_path, db_path=tmp_path / "paperguard.db", data_dir=tmp_path / "data")
    except ValueError as exc:
        assert "数据库快照" in str(exc)
    else:
        raise AssertionError("缺少数据库快照的备份不应被恢复")


def test_backup_cli_requires_explicit_restore_confirmation(tmp_path, monkeypatch):
    db_path = tmp_path / "paperguard.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PAPERGUARD_DB_PATH", str(db_path))
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(data_dir))
    Store(db_path)
    backup_path = tmp_path / "cli-backup.zip"
    runner = CliRunner()
    created = runner.invoke(cli, ["backup", str(backup_path)])
    assert created.exit_code == 0, created.output
    refused = runner.invoke(cli, ["restore", str(backup_path)])
    assert refused.exit_code != 0 and "--yes" in refused.output


def test_migrate_cli_is_repeatable_and_reports_schema_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_DB_PATH", str(tmp_path / "migrations.db"))
    runner = CliRunner()
    first = runner.invoke(cli, ["migrate"])
    second = runner.invoke(cli, ["migrate"])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_payload = json.loads(first.output)
    second_payload = json.loads(second.output)
    assert first_payload["status"] == second_payload["status"] == "ok"
    assert [item["version"] for item in first_payload["migrations"]] == [
        "0001_runtime_schema", "0002_durable_jobs", "0003_audit_ledger", "0004_document_packages", "0005_document_projects",
    ]
    assert [item["version"] for item in first_payload["migrations"]] == [
        item["version"] for item in second_payload["migrations"]
    ]


def test_operations_api_configures_retention_and_downloads_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    current = client.get("/api/operations/retention", headers=headers)
    assert current.status_code == 200 and current.json()["configured"] is False
    configured = client.put("/api/operations/retention", headers=headers, json={
        "draft_days": 30, "attachment_days": 365, "submission_days": 730,
        "archive_days": 3650, "audit_days": 3650, "local_cache_days": 7,
        "timezone": "Asia/Shanghai",
    })
    assert configured.status_code == 200 and configured.json()["configured"] is True
    backup = client.post("/api/operations/backup", headers=headers)
    assert backup.status_code == 200 and backup.content[:2] == b"PK"
    assert backup.headers["x-paperguard-backup-sha256"]


def test_worker_consumes_in_app_outbox_with_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_ADMIN_PASSWORD", "WorkerAdminPassword123")
    store = Store(tmp_path / "worker.db")
    seed_admin(store)
    admin = store.get_user_by_username("admin")
    assert admin
    with store.workflow_transaction() as conn:
        assert store.save_workflow_event_conn(
            conn, event_type="test.event", recipient_id=admin["id"], resource_type="test",
            resource_id="resource-1", dedupe_key="test:event:1", data={"title": "测试通知"},
        )
    result = consume_in_app_outbox(store, consumer_id="test-worker")
    assert result["claimed"] == 1 and len(result["delivered"]) == 1
    assert store.list_pending_workflow_events() == []


def test_durable_job_lease_retry_and_completion(tmp_path):
    store = Store(tmp_path / "jobs.db")
    job_id = new_id("job")
    with store.workflow_transaction() as conn:
        created = store.create_job_conn(
            conn, job_id=job_id, job_type="test.job", resource_type="test",
            resource_id="resource-1", max_attempts=3,
        )
    assert created["state"] == "queued"
    claimed = store.claim_jobs(job_type="test.job", consumer_id="worker-a", lease_seconds=30)
    assert len(claimed) == 1 and claimed[0]["id"] == job_id
    failed = store.fail_job(job_id, error="temporary failure", lease_token=claimed[0]["lease_token"], retry_delay_seconds=0)
    assert failed and failed["state"] == "queued" and failed["attempt_count"] == 1
    claimed_again = store.claim_jobs(job_type="test.job", consumer_id="worker-b", lease_seconds=30)
    assert len(claimed_again) == 1
    completed = store.complete_job(job_id, lease_token=claimed_again[0]["lease_token"])
    assert completed and completed["state"] == "succeeded" and completed["completed_at"]
    assert store.claim_jobs(job_type="test.job", consumer_id="worker-c") == []


def test_archive_job_recovery_requeues_running_record_after_restart(tmp_path):
    store = Store(tmp_path / "recovery.db")
    archive = {
        "id": "archive-recovery", "submission_id": "submission-recovery",
        "participant_id": "participant-recovery", "status": "running", "revision": 0,
        "created_at": "2026-09-11T00:00:00+00:00",
    }
    store.save_workflow_record("archive", archive)
    recover_pending_archives(store)
    recovered = store.get_workflow_record("archive", archive["id"])
    assert recovered and recovered["status"] == "queued" and recovered["job_id"]
    jobs = store.list_jobs(resource_type="archive", resource_id=archive["id"])
    assert len(jobs) == 1 and jobs[0]["state"] == "queued"