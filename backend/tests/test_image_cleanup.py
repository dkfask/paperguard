"""Upload cleanup must preserve references held by all saved project versions."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from app.db import Store
from app.domain.documents.schemas import ContentBlock, DocumentProject
from app.models.project import WritingProject
from app.services.document_assets import document_upload_dir
from app.services.image_cleanup import cleanup_orphan_images


def _upload(name: str):
    path = document_upload_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    return path


def test_cleanup_preserves_history_generic_projects_and_tenant_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    store = Store(tmp_path / "test.db")
    history = _upload("history.png")
    snapshot = _upload("snapshot.png")
    generic = _upload("generic.png")
    tenant = _upload("tenant.png")
    orphan = _upload("orphan.png")

    store.save_workflow_record("project_version", {
        "id": "history", "attachment_refs": [{"filename": history.name}],
        "content_snapshot": {"sections": [{"paragraphs": [{"image": {"url": f"/media/uploads/{snapshot.name}"}}]}]},
    })
    store.save_document_project(DocumentProject(
        id="generic", package_id="package", document_type_id="document", template_version_id="template",
        spec_version_id="spec", renderer_version="renderer", rule_engine_version="rules", owner_id="owner",
        sections={"body": [ContentBlock(id="image", kind="image", data={"url": f"/media/uploads/{generic.name}"})]},
    ))
    tenant_project = WritingProject(id="tenant-project", template_id="template", tenant_id="tenant", sections=[{
        "key": "body", "type": "body", "title": "Body",
        "paragraphs": [{"id": "image", "type": "image", "image": {"url": f"/media/uploads/{tenant.name}"}}],
    }])
    with store.workflow_transaction() as conn:
        conn.execute("INSERT INTO projects (id, template_id, data, updated_at) VALUES (?, ?, ?, ?)",
                     (tenant_project.id, tenant_project.template_id, tenant_project.model_dump_json(), "now"))

    assert cleanup_orphan_images(store) == 1
    assert all(path.is_file() for path in (history, snapshot, generic, tenant))
    assert not orphan.exists()


def test_cleanup_fails_closed_when_persisted_references_cannot_be_read(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    store = Store(tmp_path / "test.db")
    image = _upload("unknown-reference.png")
    store.save_workflow_record("project_version", {"id": "damaged", "attachment_refs": "unreadable"})

    assert cleanup_orphan_images(store) == 0
    assert image.is_file()


def test_cleanup_preserves_recent_uploads_during_grace_period(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    store = Store(tmp_path / "test.db")
    recent = _upload("recent.png")
    old = _upload("old.png")
    past = time.time() - 2 * 24 * 60 * 60
    os.utime(old, (past, past))

    assert cleanup_orphan_images(store, grace_seconds=24 * 60 * 60) == 1
    assert recent.is_file()
    assert not old.exists()


@pytest.mark.parametrize(
    ("setting", "should_delete"),
    [(None, False), ("", False), ("false", False), ("unknown", False), ("true", True), ("1", True)],
)
def test_startup_image_cleanup_requires_explicit_opt_in(tmp_path, monkeypatch, caplog, setting, should_delete):
    from app.main import app

    data_dir = tmp_path / "data"
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PAPERGUARD_DB_PATH", str(data_dir / "test.db"))
    if setting is None:
        monkeypatch.delenv("PAPERGUARD_STARTUP_IMAGE_CLEANUP", raising=False)
    else:
        monkeypatch.setenv("PAPERGUARD_STARTUP_IMAGE_CLEANUP", setting)
    old = _upload("old-unreferenced.png")
    past = time.time() - 2 * 24 * 60 * 60
    os.utime(old, (past, past))

    with TestClient(app) as client:
        assert client.get("/api/health/live").status_code == 200
        assert old.is_file() is not should_delete

    assert "启动初始化失败" not in caplog.text
