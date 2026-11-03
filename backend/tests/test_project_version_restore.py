"""Restoring an import version preserves its template basis and revision history."""

import pytest
from fastapi.testclient import TestClient

from app.api import workflow
from app.api.templates import get_store
from app.auth import current_user
from app.db import Store
from app.main import app
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.services.project_versions import project_content_hash, project_snapshot


@pytest.fixture
def restore_api(tmp_path):
    store = Store(tmp_path / "restore.db")
    actor = {"id": "admin", "role": "admin", "username": "admin"}
    current = WritingProject(
        id="paper", template_id="tpl", owner_id="student", revision=4,
        title="当前稿", meta={"title": "当前稿"}, organization_context={"school_id": "school"},
        template_snapshot={"name": "当前模板"}, template_source_snapshot={"name": "当前源"},
        layout_template_snapshot={"name": "当前版式"}, requirement_snapshot={"name": "当前规则"},
        reference_number_owners={"2": "current-ref", "3": "deleted-current"},
        sections=[ProjectSection(key="references", type="references", title="参考文献", paragraphs=[
            ParagraphBlock(id="current-ref", text="[2] 当前文献", reference_number=2),
        ])],
    )
    store.save_project(current)
    historical = current.model_copy(deep=True)
    historical.title = "历史稿"
    historical.meta = {"title": "历史稿"}
    historical.template_snapshot = {"name": "历史模板"}
    historical.template_source_snapshot = {"name": "历史源"}
    historical.layout_template_snapshot = {"name": "历史版式"}
    historical.requirement_snapshot = {"name": "历史规则"}
    historical.reference_number_owners = {"1": "old-ref", "4": "deleted-old"}
    historical.sections[0].paragraphs = [ParagraphBlock(id="old-ref", text="[1] 历史文献", reference_number=1)]
    version = {
        "id": "old-version", "project_id": current.id, "content_snapshot": project_snapshot(historical),
        "content_hash": project_content_hash(historical), "created_reason": "before_docx_import",
        "immutable": True,
    }
    store.save_workflow_record("project_version", version)
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: actor
    try:
        yield TestClient(app, raise_server_exceptions=False), store, current, version
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def _restore(client):
    return client.post("/api/projects/paper/restore-version", json={"version_id": "old-version", "base_revision": 4})


def test_restore_atomically_preserves_both_version_bases_and_reference_reservations(restore_api):
    client, store, current, _ = restore_api
    response = _restore(client)
    assert response.status_code == 200, response.text
    restored = store.get_project("paper")
    assert restored.title == "历史稿"
    assert restored.meta == {"title": "历史稿"}
    assert restored.template_snapshot == {"name": "历史模板"}
    assert restored.template_source_snapshot == {"name": "历史源"}
    assert restored.layout_template_snapshot == {"name": "历史版式"}
    assert restored.requirement_snapshot == {"name": "历史规则"}
    assert restored.owner_id == current.owner_id
    assert restored.organization_context == current.organization_context
    assert restored.reference_number_owners == {
        "1": "old-ref", "2": "current-ref", "3": "deleted-current", "4": "deleted-old",
    }
    versions = store.list_workflow_records("project_version", limit=None)
    before = next(item for item in versions if item.get("created_reason") == "before_restore_version")
    after = next(item for item in versions if item.get("created_reason") == "restore_version")
    assert before["content_snapshot"]["title"] == "当前稿"
    assert after["content_hash"] == project_content_hash(restored)
    assert after["restored_from_version_id"] == "old-version"


def test_restore_rejects_conflicting_reference_ownership_without_writing(restore_api):
    client, store, current, version = restore_api
    version["content_snapshot"]["reference_number_owners"] = {"2": "old-ref"}
    version["content_snapshot"]["sections"][0]["paragraphs"][0].update({"text": "[2] 历史文献", "reference_number": 2})
    version["content_hash"] = project_content_hash(version["content_snapshot"])
    store.save_workflow_record("project_version", version)
    response = _restore(client)
    assert response.status_code == 409, response.text
    assert store.get_project("paper").model_dump() == current.model_dump()
    assert len(store.list_workflow_records("project_version", limit=None)) == 1


@pytest.mark.parametrize("damage", ["hash", "snapshot"])
def test_restore_rejects_unverifiable_history(restore_api, damage):
    client, store, current, version = restore_api
    if damage == "hash":
        version["content_hash"] = "tampered"
    else:
        del version["content_snapshot"]["template_source_snapshot"]
        version["content_hash"] = project_content_hash(version["content_snapshot"])
    store.save_workflow_record("project_version", version)
    response = _restore(client)
    assert response.status_code == 409, response.text
    assert store.get_project("paper").model_dump() == current.model_dump()
    assert len(store.list_workflow_records("project_version", limit=None)) == 1


def test_restore_rolls_back_project_and_versions_when_audit_fails(restore_api, monkeypatch):
    client, store, current, _ = restore_api
    monkeypatch.setattr(store, "save_audit_event_conn", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))
    assert _restore(client).status_code == 500
    assert store.get_project("paper").model_dump() == current.model_dump()
    assert len(store.list_workflow_records("project_version", limit=None)) == 1


def test_restore_detects_same_revision_content_change(restore_api, monkeypatch):
    client, store, _, _ = restore_api
    ensure_access = workflow._ensure_project_workflow_access

    def mutate_after_access(*args):
        stale = ensure_access(*args)
        concurrent = store.get_project("paper")
        concurrent.meta["title"] = "另一个窗口的修改"
        store.save_project(concurrent)
        return stale

    monkeypatch.setattr(workflow, "_ensure_project_workflow_access", mutate_after_access)
    assert _restore(client).status_code == 409
    assert store.get_project("paper").meta["title"] == "另一个窗口的修改"
    assert len(store.list_workflow_records("project_version", limit=None)) == 1
