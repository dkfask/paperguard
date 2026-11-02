"""M1 permits checked teacher/student workflow while other pilot boundaries remain closed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.templates import get_store
from app.auth import current_user
from app.db import Store
from app.main import app
from app.models.project import WritingProject
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
