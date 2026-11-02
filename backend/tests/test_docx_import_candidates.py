"""Preview-bound manuscript import and heading confirmation regressions."""

from io import BytesIO
import json

import pytest
from docx import Document
from docx.shared import Pt
from fastapi.testclient import TestClient
from PIL import Image

from app.api.templates import get_store
from app.auth import current_user
from app.core import feature_flags
from app.db import Store
from app.main import app
from app.models.project import WritingProject
from app.services.document_assets import document_upload_dir
from app.services.image_cleanup import cleanup_orphan_images


@pytest.fixture
def import_api(tmp_path, monkeypatch):
    # Exercise the implementation in isolation; the product entry remains closed.
    monkeypatch.setattr(feature_flags, "manuscript_import_enabled", lambda: True)
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    store = Store(tmp_path / "test.db")
    actor = {"id": "owner", "role": "student", "username": "owner"}
    project = WritingProject(id="import-project", owner_id=actor["id"], template_id="test")
    store.save_project(project)
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: actor
    try:
        yield TestClient(app), store, actor, project.id
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def _manuscript(*, title="手工编号标题", image=False):
    document = Document()
    document.add_heading("第5章 系统实现", level=1)
    document.add_heading("5.2 前一节", level=2)
    paragraph = document.add_paragraph()
    run = paragraph.add_run(f"5.3 {title}")
    run.font.name = "黑体"
    run.font.size = Pt(15)
    document.add_paragraph("这一节的正文。")
    if image:
        picture = BytesIO()
        Image.new("RGB", (8, 8), color="red").save(picture, format="PNG")
        document.add_picture(BytesIO(picture.getvalue()))
    document.add_heading("5.4 后一节", level=2)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _preview(client, url, data=None, *, candidate_id=None, decisions=None):
    params = {"candidate_id": candidate_id} if candidate_id else {}
    fields = {"heading_decisions": json.dumps(decisions)} if decisions is not None else {}
    files = {"file": ("paper.docx", data)} if data is not None else None
    return client.post(url, params=params, data=fields, files=files)


def _confirm(client, url, preview, value="heading2"):
    decisions = {item["id"]: value for item in preview["heading_candidates"]}
    response = _preview(client, url, candidate_id=preview["candidate_id"], decisions=decisions)
    assert response.status_code == 200, response.text
    return response.json()


def _apply(client, url, preview, **extra):
    return client.post(url, params={
        "apply": True, "candidate_id": preview["candidate_id"],
        "candidate_sha256": preview["candidate_sha256"], **extra,
    })


def test_import_entry_is_closed_until_separately_approved(import_api, monkeypatch):
    client, store, _, project_id = import_api
    monkeypatch.setattr(feature_flags, "manuscript_import_enabled", lambda: False)
    url = f"/api/projects/{project_id}/import-docx"
    revision = store.get_project(project_id).revision

    capabilities = client.get(f"/api/projects/{project_id}/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["manuscript_import_enabled"] is False
    requests = [
        client.post(url, files={"file": ("paper.docx", _manuscript())}),
        client.get(f"{url}/candidate"),
        client.post(url, params={"candidate_id": "existing"}),
        client.post(url, params={"apply": True, "candidate_id": "existing", "candidate_sha256": "digest"}),
    ]
    assert all(response.status_code == 403 for response in requests)
    assert all(response.json()["detail"]["code"] == "manuscript_import_unavailable" for response in requests)
    assert store.get_project(project_id).revision == revision
    assert not store.list_workflow_records("docx_import_candidate", limit=None)


def test_preview_identifies_manual_heading_and_requires_confirmation(import_api):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    preview = client.post(url, files={"file": ("paper.docx", _manuscript())})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["candidate_id"]
    assert result["candidate_sha256"]
    assert result["source_sha256"]
    assert result["configuration_version"]
    candidate = next(item for item in result["heading_candidates"] if item["text"] == "5.3 手工编号标题")
    assert candidate["suggested"] == "heading2"
    assert candidate["decision"] is None

    blocked = client.post(url, params={
        "apply": True,
        "candidate_id": result["candidate_id"],
        "candidate_sha256": result["candidate_sha256"],
    })
    assert blocked.status_code in (409, 422), blocked.text
    assert store.get_project(project_id).revision == result["project_revision"]


def test_confirmed_candidate_applies_once_and_preserves_prior_revision(import_api):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    before = store.get_project(project_id)
    before.title = "原有草稿"
    store.save_project(before)
    initial = _preview(client, url, _manuscript()).json()
    confirmed = _confirm(client, url, initial)
    assert confirmed["candidate_id"] != initial["candidate_id"]
    assert confirmed["source_sha256"] == initial["source_sha256"]
    assert _apply(client, url, initial).status_code == 409

    result = _apply(client, url, confirmed)
    assert result.status_code == 200, result.text
    assert any(section["title"] == "5.3 手工编号标题" and section["type"] == "heading2"
               for section in result.json()["project"]["sections"])
    versions = store.list_workflow_records("project_version", limit=None)
    assert {item["created_reason"] for item in versions} >= {"before_docx_import", "after_docx_import"}
    assert any(item["content_snapshot"]["title"] == "原有草稿"
               and item["created_reason"] == "before_docx_import" for item in versions)
    assert _apply(client, url, confirmed).status_code == 409


def test_new_upload_or_changed_decision_invalidates_old_candidate(import_api):
    client, _, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    first = _confirm(client, url, _preview(client, url, _manuscript()).json())
    second = _preview(client, url, _manuscript()).json()
    assert second["candidate_id"] != first["candidate_id"]
    assert _apply(client, url, first).status_code == 409
    second = _confirm(client, url, second, "body")
    changed = _confirm(client, url, second, "heading3")
    assert changed["candidate_id"] != second["candidate_id"]
    assert changed["candidate_sha256"] != second["candidate_sha256"]
    assert _apply(client, url, second).status_code == 409
    third = _preview(client, url, _manuscript(title="新稿")).json()
    assert third["source_sha256"] != changed["source_sha256"]
    assert _apply(client, url, changed).status_code == 409


def test_candidate_rejects_changed_project_hash_and_missing_or_tampered_source(import_api):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    candidate = _confirm(client, url, _preview(client, url, _manuscript()).json())
    project = store.get_project(project_id)
    project.meta["title"] = "另一个草稿"
    store.save_project(project)  # A same-revision mutation must also invalidate a preview.
    assert _apply(client, url, candidate).status_code == 409
    fresh = _confirm(client, url, _preview(client, url, _manuscript()).json())
    record = store.get_workflow_record("docx_import_candidate", fresh["candidate_id"])
    source = document_upload_dir().parent / "import_candidates" / record["source_name"]
    source.write_bytes(b"changed")
    assert _apply(client, url, fresh).status_code == 409


def test_candidate_can_be_resumed_and_is_bound_to_actor(import_api):
    client, _, actor, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    candidate = _confirm(client, url, _preview(client, url, _manuscript()).json())
    resumed = client.get(url + "/candidate")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["candidate_id"] == candidate["candidate_id"]
    assert resumed.json()["heading_candidates"][0]["decision"] == "heading2"
    actor["id"] = "another-user"
    assert client.get(url + "/candidate").status_code in (403, 404)
    assert _apply(client, url, candidate).status_code in (403, 404)


def test_failed_apply_rolls_back_versions_and_only_new_images(import_api, monkeypatch):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    first = _confirm(client, url, _preview(client, url, _manuscript(image=True)).json())
    assert _apply(client, url, first).status_code == 200
    previous = store.get_project(project_id)
    old_image = next(block.image.url for section in previous.sections for block in section.paragraphs
                     if block.type.value == "image")
    old_path = document_upload_dir() / old_image.rsplit("/", 1)[-1]
    candidate = _confirm(client, url, _preview(client, url, _manuscript(image=True)).json())
    original = store.save_workflow_record_conn

    def fail_after_update(conn, kind, record, **kwargs):
        if kind == "project_version" and record.get("created_reason") == "after_docx_import":
            raise RuntimeError("simulated transaction failure")
        return original(conn, kind, record, **kwargs)

    monkeypatch.setattr(store, "save_workflow_record_conn", fail_after_update)
    response = _apply(client, url, candidate)
    assert response.status_code in (409, 422, 500), response.text
    assert store.get_project(project_id).model_dump() == previous.model_dump()
    assert old_path.is_file()
    assert list(document_upload_dir().iterdir()) == [old_path]


def test_orphan_cleanup_keeps_images_referenced_by_history(import_api):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    first = _confirm(client, url, _preview(client, url, _manuscript(image=True)).json())
    assert _apply(client, url, first).status_code == 200
    old_image = next(block.image.url for section in store.get_project(project_id).sections
                     for block in section.paragraphs if block.type.value == "image")
    old_path = document_upload_dir() / old_image.rsplit("/", 1)[-1]
    second = _confirm(client, url, _preview(client, url, _manuscript()).json())
    assert _apply(client, url, second).status_code == 200
    cleanup_orphan_images(store)
    assert old_path.is_file()


def _inventory_manuscript():
    document = Document()
    document.add_heading("第一章 研究", level=1)
    document.add_paragraph("必须保留的事实正文")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "第一格事实"
    table.cell(0, 1).text = "第二格事实"
    picture = BytesIO()
    Image.new("RGB", (8, 8), color="red").save(picture, format="PNG")
    document.add_picture(BytesIO(picture.getvalue()))
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_candidate_persists_and_restores_independent_integrity_report(import_api):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    response = _preview(client, url, _inventory_manuscript())
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["integrity"]["status"] == "complete"
    assert preview["reconciliation"]["unresolved"] == []
    assert preview["reconciliation"]["counts"]["table_cell"] == 2
    assert preview["reconciliation"]["counts"]["image"] == 1
    record = store.get_workflow_record("docx_import_candidate", preview["candidate_id"])
    assert record["inventory_version"] == preview["reconciliation"]["version"]
    assert record["reconciliation_sha256"] == preview["reconciliation"]["sha256"]
    assert record["inventory_sha256"]
    resumed = client.get(url + "/candidate")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["reconciliation"] == preview["reconciliation"]
    applied = _apply(client, url, preview)
    assert applied.status_code == 200, applied.text
    record = store.get_workflow_record("docx_import_candidate", preview["candidate_id"])
    report = record["applied_reconciliation"]
    assert report["status"] == "complete"
    assert report["candidate_sha256"] == preview["candidate_sha256"]
    assert report["source_sha256"] == preview["source_sha256"]
    assert report["project_revision"] == applied.json()["project"]["revision"]
    mappings = report["mappings"]
    assert {item["kind"] for item in mappings} >= {"title", "paragraph", "table", "image"}
    assert all(item["source_location"].startswith("word/document.xml#body/") for item in mappings)
    assert all(item["targets"] for item in mappings)


@pytest.mark.parametrize("missing_kind", ["paragraph", "table_cell", "image"])
def test_parser_content_loss_is_visible_and_cannot_be_applied(import_api, monkeypatch, missing_kind):
    import app.api.projects as projects_api

    client, store, _, project_id = import_api
    original_parser = projects_api.import_docx_project

    def losing_parser(*args, **kwargs):
        imported = original_parser(*args, **kwargs)
        blocks = imported["sections"][0]["paragraphs"]
        if missing_kind == "table_cell":
            next(block for block in blocks if block.get("type") == "table")["table"]["cells"][0][1] = ""
        else:
            blocks[:] = [block for block in blocks if block.get("type", "paragraph") != missing_kind]
        return imported

    monkeypatch.setattr(projects_api, "import_docx_project", losing_parser)
    before = store.get_project(project_id).model_dump()
    url = f"/api/projects/{project_id}/import-docx"
    response = _preview(client, url, _inventory_manuscript())
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["integrity"]["status"] == "incomplete"
    assert any(item["kind"] == missing_kind and item["location"] and item["reason"]
               for item in preview["reconciliation"]["unresolved"])
    assert client.get(url + "/candidate").json()["integrity"]["status"] == "incomplete"
    blocked = _apply(client, url, preview, force=True)
    assert blocked.status_code == 409, blocked.text
    assert store.get_project(project_id).model_dump() == before
    assert not store.list_workflow_records("project_version", limit=None)


@pytest.mark.parametrize("mutation", ["report", "report_sha256", "inventory_sha256", "inventory_version", "unresolved"])
def test_tampered_integrity_evidence_cannot_be_restored_or_applied(import_api, mutation):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    preview = _preview(client, url, _inventory_manuscript()).json()
    record = store.get_workflow_record("docx_import_candidate", preview["candidate_id"])
    if mutation == "report":
        record["reconciliation"]["counts"]["image"] = 99
    elif mutation == "report_sha256":
        record["reconciliation_sha256"] = "0" * 64
    elif mutation == "inventory_sha256":
        record["inventory_sha256"] = "0" * 64
    elif mutation == "inventory_version":
        record["inventory_version"] = "obsolete"
    else:
        record["unresolved"] = [{"kind": "paragraph", "location": "missing", "reason": "changed"}]
    store.save_workflow_record("docx_import_candidate", record)
    assert client.get(url + "/candidate").status_code == 409
    assert _apply(client, url, preview, force=True).status_code == 409
    assert not store.list_workflow_records("project_version", limit=None)


@pytest.mark.parametrize("missing_kind", ["paragraph", "table_cell", "image", "order"])
def test_apply_content_loss_rolls_back_project_versions_and_images(import_api, monkeypatch, missing_kind):
    import app.api.projects as projects_api

    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    preview = _preview(client, url, _inventory_manuscript()).json()
    before = store.get_project(project_id).model_dump()
    original_apply = projects_api._apply_imported_docx

    def losing_apply(project, *args, **kwargs):
        merge = original_apply(project, *args, **kwargs)
        blocks = project.sections[0].paragraphs
        if missing_kind == "table_cell":
            next(block for block in blocks if block.type.value == "table").table.cells[0][1] = ""
        elif missing_kind == "order":
            blocks.reverse()
        else:
            blocks[:] = [block for block in blocks if block.type.value != missing_kind]
        return merge

    monkeypatch.setattr(projects_api, "_apply_imported_docx", losing_apply)
    blocked = _apply(client, url, preview)
    assert blocked.status_code in (409, 422), blocked.text
    assert store.get_project(project_id).model_dump() == before
    assert not store.list_workflow_records("project_version", limit=None)
    assert not list(document_upload_dir().glob(f"{project_id}-*"))


def test_persisted_project_is_reconciled_before_versions_are_committed(import_api, monkeypatch):
    client, store, _, project_id = import_api
    url = f"/api/projects/{project_id}/import-docx"
    preview = _preview(client, url, _inventory_manuscript()).json()
    before = store.get_project(project_id).model_dump()
    original_save = store.update_project_if_revision_conn

    def corrupt_saved_project(*args, **kwargs):
        saved = original_save(*args, **kwargs)
        saved.sections[0].paragraphs[0].text = "保存阶段丢失事实"
        return saved

    monkeypatch.setattr(store, "update_project_if_revision_conn", corrupt_saved_project)
    blocked = _apply(client, url, preview)
    assert blocked.status_code == 409, blocked.text
    assert store.get_project(project_id).model_dump() == before
    assert not store.list_workflow_records("project_version", limit=None)
    assert not list(document_upload_dir().glob(f"{project_id}-*"))
