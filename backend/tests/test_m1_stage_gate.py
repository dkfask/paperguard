"""M1 permits checked teacher/student workflow while other pilot boundaries remain closed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.templates import get_store
from app.auth import current_user
from app.db import Store
from app.main import app
from app.models.project import InlineTextRun, ParagraphBlock, ProjectSection, WritingProject
from app.models.template import PaperTemplate
from app.services.export_jobs import create_export_job, run_project_export
from app.services.workflow_jobs import consume_archive_jobs, consume_project_export_jobs


@pytest.fixture
def pilot_store(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERGUARD_DELIVERY_STAGE", raising=False)
    store = Store(tmp_path / "pilot.db")
    for role in ("admin", "teacher", "student"):
        store.save_user({
            "id": role, "username": role, "password_hash": "unused", "salt": "unused",
            "role": role, "display_name": role, "status": "active", "created_at": "2026-10-30",
        })
    template = PaperTemplate(id="pilot-template", name="试点模板")
    store.save_template(template)
    projects = {}
    for role in ("teacher", "student"):
        project = WritingProject(id=f"{role}-project", owner_id=role, template_id=template.id,
                                 template_snapshot=template.model_dump(mode="json"))
        projects[role] = store.save_project(project)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: store.get_user("admin")
    yield store, template, projects
    app.dependency_overrides.pop(get_store, None)
    app.dependency_overrides.pop(current_user, None)


def test_unknown_stage_closes_workflow_actions_and_old_archive_links(pilot_store, monkeypatch):
    store, _, projects = pilot_store
    monkeypatch.setenv("PAPERGUARD_DELIVERY_STAGE", "unrecognized")
    client = TestClient(app)
    assert client.get("/api/workflow-capabilities").json()["m2_workflow_enabled"] is False
    paths = [
        ("post", "/api/submission-candidates", {"participant_id": "existing"}),
        ("get", "/api/submission-candidates/existing", None),
        ("post", "/api/submissions", {"candidate_id": "existing", "idempotency_key": "test-key-123"}),
        ("get", "/api/submissions/existing/materials/existing/download", None),
        ("get", "/api/submissions/existing/document", None),
        ("post", "/api/submissions/existing/withdraw", {}),
        ("post", "/api/submissions/existing/reviews", {"decision": "approved"}),
        ("post", "/api/submissions/existing/comments", {"body": "批注"}),
        ("post", "/api/comments/existing/response", {"body": "回复"}),
        ("post", "/api/comments/existing/status", {"status": "resolved"}),
        ("post", "/api/stage-participants/existing/reopen", {"reason": "重开"}),
        ("post", "/api/archives", {"submission_id": "existing"}),
        ("post", "/api/archives/existing/retry", {"reason": "重试"}),
        ("get", "/api/archives/existing/download", None),
        ("get", "/api/archives/existing/review-candidate", None),
        ("post", "/api/archives/existing/manual-review", {"output_sha256": "a" * 64, "confirmed_check_ids": ["a"]}),
    ]
    for method, path, body in paths:
        response = client.request(method, path, json=body)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"]["code"] == "m1_workflow_unavailable"
    for path, body in [
        (f"/api/projects/{projects['student'].id}/template", {"template_id": "pilot-template"}),
        (f"/api/projects/{projects['student'].id}/refresh-template", None),
        ("/api/stages/existing/versions/existing/publish", {"participant_ids": ["existing"], "reason": "迁移"}),
    ]:
        response = client.post(path, json=body)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"]["code"] == "m1_template_migration_unavailable"
    assert consume_archive_jobs(store, consumer_id="m1")["claimed"] == 0


def test_m1_workflow_is_advertised_without_opening_other_delivery_surfaces(pilot_store, monkeypatch):
    store, _, projects = pilot_store
    monkeypatch.setenv("PAPERGUARD_DELIVERY_STAGE", "m1")
    client = TestClient(app)
    assert client.get("/api/workflow-capabilities").json()["m2_workflow_enabled"] is True
    assert client.get(f"/api/projects/{projects['teacher'].id}/capabilities").json()["formal_delivery_enabled"] is False
    response = client.post(f"/api/projects/{projects['student'].id}/template", json={"template_id": "pilot-template"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "m1_template_migration_unavailable"


def test_m1_student_draft_remains_editable_but_staff_delivery_is_closed(pilot_store, monkeypatch):
    store, template, projects = pilot_store
    queued = create_export_job(store, projects["teacher"], template, force=True, actor_id="teacher")
    monkeypatch.setenv("PAPERGUARD_DELIVERY_STAGE", "m1")
    client = TestClient(app)
    teacher_path = f"/api/projects/{projects['teacher'].id}"
    capabilities = client.get(teacher_path + "/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["formal_delivery_enabled"] is False
    assert capabilities.json()["template_migration_enabled"] is False
    paths = [
        ("post", "/export?force=true", None),
        ("post", "/export-preview?force=true", None),
        ("post", "/export-jobs?force=true", None),
        ("post", f"/export-jobs/{queued['id']}/retry", None),
        ("get", f"/export-jobs/{queued['id']}/download", None),
        ("get", f"/export-jobs/{queued['id']}/review-candidate", None),
        ("post", f"/export-jobs/{queued['id']}/manual-review", {"output_sha256": "a" * 64, "confirmed_check_ids": ["a"]}),
    ]
    for method, path, body in paths:
        response = client.request(method, teacher_path + path, json=body)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"]["code"] == "m1_personal_delivery_unavailable"

    app.dependency_overrides[current_user] = lambda: store.get_user("student")
    student_path = f"/api/projects/{projects['student'].id}"
    assert client.get(student_path + "/capabilities").json()["formal_delivery_enabled"] is True
    saved = client.put(student_path + "/meta", json={"base_revision": projects["student"].revision, "meta": {"title": "草稿"}})
    assert saved.status_code == 200, saved.text


@pytest.mark.parametrize(("state", "cohort_status"), [
    ("approved", "open"), ("pending_archive", "open"), ("archived", "open"), ("in_progress", "closed"),
])
def test_read_only_student_can_resolve_delivery_capabilities_but_not_write(pilot_store, monkeypatch, state, cohort_status):
    store, _, projects = pilot_store
    monkeypatch.setattr("app.core.feature_flags.manuscript_import_enabled", lambda: True)
    project = projects["student"]
    store.save_workflow_record("cohort", {"id": "cohort", "status": cohort_status})
    store.save_workflow_record("participant", {"id": "participant", "project_id": project.id,
        "student_id": "student", "cohort_id": "cohort", "state": state})
    app.dependency_overrides[current_user] = lambda: store.get_user("student")
    client = TestClient(app)
    path = f"/api/projects/{project.id}"
    capabilities = client.get(path + "/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    assert capabilities.json()["formal_delivery_enabled"] is True
    assert capabilities.json()["manuscript_import_enabled"] is False
    assert capabilities.json()["template_migration_enabled"] is False
    assert client.put(path + "/meta", json={"base_revision": project.revision, "meta": {"title": "禁止修改"}}).status_code == 409
    assert client.get(path + "/export-jobs").status_code == 200
    app.dependency_overrides[current_user] = lambda: store.get_user("teacher")
    assert client.get(path + "/capabilities").status_code == 403
    assert client.get(path + "/export-jobs").status_code == 404


def test_reference_inline_edit_saves_and_reloads_with_formatting(pilot_store):
    store, _, projects = pilot_store
    project = projects["student"]
    project.sections = [ProjectSection(key="references", type="references", title="参考文献", paragraphs=[
        ParagraphBlock(id="ref", text="作者. 学报[J]", inline_runs=[InlineTextRun(text="作者. "),
            InlineTextRun(text="学报", italic=True), InlineTextRun(text="[J]")]),
    ])]
    store.save_project(project)
    app.dependency_overrides[current_user] = lambda: store.get_user("student")
    client = TestClient(app)
    block = project.sections[0].paragraphs[0].model_dump(mode="json")
    block["text"] = "作者甲. 学报[J]"
    block["inline_runs"][0]["text"] = "作者甲. "
    saved = client.put(f"/api/projects/{project.id}/section/references", json={
        "base_revision": project.revision, "paragraphs": [block]})
    assert saved.status_code == 200, saved.text
    current = store.get_project(project.id).sections[0].paragraphs[0]
    assert current.text == "作者甲. 学报[J]"
    assert "".join(run.text for run in current.inline_runs) == current.text
    assert current.inline_runs[1].text == "学报" and current.inline_runs[1].italic is True


def test_m1_pauses_old_staff_jobs_without_spending_attempt_or_starving_students(pilot_store, monkeypatch, tmp_path):
    store, template, projects = pilot_store
    from app.services import export_jobs

    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path / "exports")
    staff = create_export_job(store, projects["teacher"], template, force=False, actor_id="teacher")
    student = create_export_job(store, projects["student"], template, force=False, actor_id="student")
    monkeypatch.setenv("PAPERGUARD_DELIVERY_STAGE", "m1")
    assert run_project_export(store, staff["id"])["status"] == "paused"
    first = consume_project_export_jobs(store, consumer_id="m1", limit=1)
    assert first["paused"] == [staff["job_id"]]
    job = store.get_job(staff["job_id"])
    assert job["state"] == "queued" and job["attempt_count"] == 0
    assert store.get_workflow_record("project_export", staff["id"])["status"] == "queued"
    second = consume_project_export_jobs(store, consumer_id="m1", limit=1)
    assert second["claimed"] == 1
    assert store.get_job(student["job_id"])["attempt_count"] == 1
