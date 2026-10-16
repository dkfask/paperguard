"""生产 UAT 的报表口径和课题变更回归，仅使用临时测试库。"""

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app
from tests.test_workflow_api import _create_people, _login, _prepare_project, client


def _cohort(admin, members=None):
    suffix = uuid.uuid4().hex[:10]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"uat-fix-{suffix}", "name": "回归学院", "type": "school",
    }).json()
    response = client.post("/api/cohorts", headers=admin, json={
        "name": f"回归批次-{suffix}", "organization_id": organization["id"],
    })
    assert response.status_code == 201, response.text
    cohort = response.json()
    if members is not None:
        cohort["member_ids"] = members
        Store().save_workflow_record("cohort", cohort)
    return cohort


def test_report_counts_unique_eligible_stage_tasks_not_students():
    admin = _login("admin", "TestAdminPassword123")
    cohort = _cohort(admin, ["member-active", "member-exited"])
    store = Store()
    now = datetime.now(timezone.utc)
    prefix = cohort["id"]
    for key, student, state in [
        ("one", "member-active", "archived"),
        ("two", "member-active", "pending_archive"),
        ("exit", "member-exited", "exited"),
        ("withdraw", "member-active", "withdrawn"),
    ]:
        participant_id = f"{prefix}-{key}"
        store.save_workflow_record("participant", {
            "id": participant_id, "cohort_id": prefix, "student_id": student,
            "state": state, "initial_deadline_at": (now + timedelta(days=1)).isoformat(),
            "current_submission_id": f"{participant_id}-submission",
        })
        store.save_workflow_record("submission", {
            "id": f"{participant_id}-submission", "participant_id": participant_id,
            "state": "approved", "review_cycle": 1, "submitted_at": now.isoformat(),
        })
        if key != "two":
            for attempt in range(2):
                store.save_workflow_record("archive", {
                    "id": f"{participant_id}-archive-{attempt}", "participant_id": participant_id,
                    "submission_id": f"{participant_id}-submission", "status": "succeeded",
                })
    # Duplicate first-cycle facts and archive retries must not count twice.
    store.save_workflow_record("submission", {
        "id": f"{prefix}-duplicate", "participant_id": f"{prefix}-one",
        "state": "approved", "review_cycle": 1, "submitted_at": now.isoformat(),
    })
    response = client.get(f"/api/reports/cohorts/{prefix}", headers=admin)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["member_count"] == 1
    assert report["participant_count"] == 4
    assert report["metrics"]["first_submission_count"] == 2
    assert report["metrics"]["on_time_first_submission_rate"] == 1.0
    assert report["archive_count"] == 1
    assert report["metrics"]["archive_completion_rate"] == 0.5
    assert report["metrics"]["denominator"]["eligible_participant_count"] == 2


def test_report_without_eligible_tasks_has_no_percentage():
    admin = _login("admin", "TestAdminPassword123")
    cohort = _cohort(admin, ["member-without-task"])
    report = client.get(f"/api/reports/cohorts/{cohort['id']}", headers=admin).json()
    assert report["member_count"] == 1
    assert report["metrics"]["on_time_first_submission_rate"] is None
    assert report["metrics"]["archive_completion_rate"] is None


def _topic_fixture(new_topic="审批后的新题目"):
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    cohort = _cohort(admin)
    member = client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    })
    assert member.status_code == 201, member.text
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "topic", "name": "课题变更回归", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = next(p for p in client.get("/api/my-tasks", headers=student).json() if p["stage_id"] == stage["id"])
    project_id = _prepare_project(student, participant["id"])
    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    record = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "topic_change", "data": {
            "original_topic": project["title"], "new_topic": new_topic, "reason": "合成回归申请",
        },
    })
    assert record.status_code == 201, record.text
    return {"admin": admin, "teacher": teacher, "student": student, "participant": participant,
            "project": project, "record_id": record.json()["id"], "new_topic": new_topic}


def _submit_and_teacher_approve(fixture):
    record_id = fixture["record_id"]
    submitted = client.post(f"/api/process-records/{record_id}/submit", headers=fixture["student"])
    assert submitted.status_code == 200, submitted.text
    forbidden = client.post(f"/api/process-records/{record_id}/decisions", headers=fixture["student"], json={"decision": "approved"})
    assert forbidden.status_code == 403
    first = client.post(f"/api/process-records/{record_id}/decisions", headers=fixture["teacher"], json={"decision": "approved"})
    assert first.status_code == 200 and first.json()["state"] == "submitted", first.text
    assert client.get(f"/api/projects/{fixture['project']['id']}", headers=fixture["student"]).json() == fixture["project"]


def _final_approval(fixture, session=client):
    return session.post(f"/api/process-records/{fixture['record_id']}/decisions", headers=fixture["admin"], json={"decision": "approved"})


def test_student_cannot_mutate_teacher_midterm_draft_but_can_edit_writable_material():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    cohort = _cohort(admin)
    member = client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    })
    assert member.status_code == 201, member.text
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "midterm", "name": "中期材料权限回归",
        "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    })
    assert stage.status_code == 201, stage.text
    assert client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin).status_code == 200
    participant = next(
        task for task in client.get("/api/my-tasks", headers=student).json()
        if task["stage_id"] == stage.json()["id"]
    )
    original_data = {
        "progress": "合成中期进展", "problems": "合成问题", "next_plan": "合成计划",
        "advisor_comment": "仅教师可修改的合成意见", "score": 82,
    }
    teacher_draft = client.post(
        f"/api/stage-participants/{participant['id']}/process-records", headers=teacher,
        json={"artifact_type": "midterm_check", "data": original_data},
    )
    assert teacher_draft.status_code == 201, teacher_draft.text
    record = teacher_draft.json()

    forbidden_update = client.put(f"/api/process-records/{record['id']}", headers=student, json={
        "base_revision": record["revision"], "data": {
            **original_data, "score": 99, "advisor_comment": "学生越权改写",
        },
    })
    assert forbidden_update.status_code == 403, forbidden_update.text
    forbidden_attachment = client.post(
        f"/api/process-records/{record['id']}/attachments", headers=student,
        files={"file": ("student-note.txt", b"synthetic attachment", "text/plain")},
    )
    assert forbidden_attachment.status_code == 403, forbidden_attachment.text
    unchanged = client.get(f"/api/process-records/{record['id']}", headers=teacher).json()
    assert unchanged["data"] == original_data
    assert unchanged["revision"] == record["revision"]
    assert unchanged["version"] == record["version"]
    assert unchanged["attachments"] == []

    student_draft = client.post(
        f"/api/stage-participants/{participant['id']}/process-records", headers=student,
        json={"artifact_type": "task_book", "data": {"title": "学生自有任务书"}},
    )
    assert student_draft.status_code == 201, student_draft.text
    editable = student_draft.json()
    updated = client.put(f"/api/process-records/{editable['id']}", headers=student, json={
        "base_revision": editable["revision"], "data": {"title": "学生更新后的任务书"},
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["title"] == "学生更新后的任务书"


def test_final_topic_approval_syncs_cover_and_export_preserving_snapshot():
    f = _topic_fixture()
    student, project = f["student"], f["project"]
    assert client.post(f"/api/stage-participants/{f['participant']['id']}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": f["participant"]["id"]})
    assert candidate.status_code == 201, candidate.text
    store = Store()
    version_id = candidate.json()["project_version_id"]
    snapshot_before = store.get_workflow_record("project_version", version_id)
    # Candidate checks append check-run metadata without editing the manuscript.
    f["project"] = project = client.get(f"/api/projects/{project['id']}", headers=student).json()
    _submit_and_teacher_approve(f)
    final = _final_approval(f)
    assert final.status_code == 200 and final.json()["state"] == "approved", final.text
    changed = client.get(f"/api/projects/{project['id']}", headers=student).json()
    assert changed["title"] == f["new_topic"]
    assert changed["meta"]["title"] == f["new_topic"]
    assert changed["revision"] == project["revision"] + 1
    assert changed["sections"] == project["sections"]
    assert store.get_workflow_record("project_version", version_id) == snapshot_before
    stale_candidate = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"topic-stale-{uuid.uuid4().hex}",
        "warning_acknowledged": True,
    })
    assert stale_candidate.status_code == 409 and stale_candidate.json()["detail"]["code"] == "CHECK_STALE"
    exported = client.post(f"/api/projects/{project['id']}/export", headers=student)
    assert exported.status_code == 200, exported.text
    assert f["new_topic"] in "\n".join(p.text for p in Document(io.BytesIO(exported.content)).paragraphs)
    after_export = client.get(f"/api/projects/{project['id']}", headers=student).json()
    stale = client.put(f"/api/projects/{project['id']}/meta", headers=student,
                       json={"base_revision": project["revision"], "meta": {"title": "旧窗口标题"}})
    assert stale.status_code == 409
    assert _final_approval(f).status_code == 409
    assert client.get(f"/api/projects/{project['id']}", headers=student).json() == after_export


@pytest.mark.parametrize("new_topic", ["", "  ", None, 123])
def test_topic_change_rejects_missing_or_invalid_title(new_topic):
    f = _topic_fixture(new_topic)
    response = client.post(f"/api/process-records/{f['record_id']}/submit", headers=f["student"])
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("decision", ["returned", "rejected"])
def test_unapproved_topic_change_does_not_modify_project(decision):
    f = _topic_fixture()
    assert client.post(f"/api/process-records/{f['record_id']}/submit", headers=f["student"]).status_code == 200
    response = client.post(f"/api/process-records/{f['record_id']}/decisions", headers=f["teacher"], json={"decision": decision, "reason": "合成退回原因"})
    assert response.status_code == 200
    assert client.get(f"/api/projects/{f['project']['id']}", headers=f["student"]).json() == f["project"]


def test_topic_change_and_approval_roll_back_together(monkeypatch):
    from app.api import process_records

    f = _topic_fixture()
    _submit_and_teacher_approve(f)
    before = Store().get_workflow_record("process_record", f["record_id"])
    def fail_notification(*args, **kwargs):
        raise RuntimeError("synthetic transaction failure")
    monkeypatch.setattr(process_records, "_notify_record_owner", fail_notification)
    response = _final_approval(f, TestClient(app, raise_server_exceptions=False))
    assert response.status_code == 500
    assert Store().get_workflow_record("process_record", f["record_id"]) == before
    assert client.get(f"/api/projects/{f['project']['id']}", headers=f["student"]).json() == f["project"]


@pytest.mark.parametrize("invalid", ["missing", "deleted", "owner", "cohort"])
def test_topic_change_fails_closed_for_invalid_project_binding(invalid):
    f = _topic_fixture()
    _submit_and_teacher_approve(f)
    store = Store()
    participant = store.get_workflow_record("participant", f["participant"]["id"])
    project = store.get_project(f["project"]["id"])
    if invalid == "missing":
        participant["project_id"] = "missing-project"
        store.save_workflow_record("participant", participant)
    else:
        if invalid == "deleted": project.deleted_at = datetime.now(timezone.utc).isoformat()
        if invalid == "owner": project.owner_id = "unrelated-student"
        if invalid == "cohort": project.organization_context["batch_id"] = "unrelated-cohort"
        store.save_project(project)
    before_project = store.get_project(project.id).model_dump()
    before_record = store.get_workflow_record("process_record", f["record_id"])
    response = _final_approval(f)
    assert response.status_code == 409, response.text
    assert store.get_workflow_record("process_record", f["record_id"]) == before_record
    assert store.get_project(project.id).model_dump() == before_project


def test_topic_change_rolls_back_on_project_revision_conflict(monkeypatch):
    f = _topic_fixture()
    _submit_and_teacher_approve(f)
    before = Store().get_workflow_record("process_record", f["record_id"])
    monkeypatch.setattr(Store, "update_project_if_revision_conn", lambda *args, **kwargs: None, raising=False)
    response = _final_approval(f)
    assert response.status_code == 409, response.text
    assert Store().get_workflow_record("process_record", f["record_id"]) == before


def test_cohort_report_does_not_truncate_at_global_record_limit(monkeypatch, tmp_path):
    from app import db
    from app.api.workflow import cohort_report
    from app.models.organization import OrganizationUnit

    store = Store(tmp_path / "report.db")
    store.save_organization_unit(OrganizationUnit(id="report-school", name="回归学校", type="school"))
    cohort = {"id": "report-cohort", "name": "报表截断回归", "member_ids": ["report-member"], "organization_id": "report-school"}
    store.save_workflow_record("cohort", cohort)
    participant_id = f"{cohort['id']}-participant"
    store.save_workflow_record("participant", {
        "id": participant_id, "cohort_id": cohort["id"], "student_id": "report-member",
        "state": "archived", "current_submission_id": "report-submission",
        "initial_deadline_at": "2026-12-01T00:00:00+00:00",
    })
    store.save_workflow_record("submission", {"id": "report-submission", "participant_id": participant_id,
                                             "review_cycle": 1, "submitted_at": "2026-11-01T00:00:00+00:00"})
    store.save_workflow_record("archive", {"id": "report-archive", "submission_id": "report-submission", "status": "succeeded"})
    # Newer unrelated records used to evict this cohort's facts from each global list.
    monkeypatch.setattr(db, "_now", lambda: "2099-01-01T00:00:00+00:00")
    with store.workflow_transaction() as conn:
        for index in range(501):
            for kind in ["participant", "submission", "archive", "review"]:
                store.save_workflow_record_conn(conn, kind, {"id": f"noise-{kind}-{index}", "cohort_id": "unrelated-cohort"})
    report = cohort_report(cohort["id"], store=store, user={"role": "admin", "status": "active", "id": "report-admin"})
    assert report["participant_count"] == 1
    assert report["metrics"]["on_time_first_submission_rate"] == 1
    assert report["metrics"]["archive_completion_rate"] == 1
