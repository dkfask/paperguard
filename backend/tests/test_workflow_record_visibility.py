"""Older student workflow records must not disappear behind other users' records."""

import pytest
from fastapi import HTTPException

from app.api import projects, workflow
from app.db import Store
from app.models.project import WritingProject


@pytest.fixture
def older_workflow(tmp_path):
    store = Store(tmp_path / "workflow.db")
    student = {"id": "student-a", "role": "student", "status": "active"}
    teacher = {"id": "teacher-a", "role": "teacher", "status": "active"}
    other = {"id": "student-b", "role": "student", "status": "active"}
    project = WritingProject(id="project-a", template_id="template-a", owner_id=student["id"])
    store.save_project(project)

    records = {
        "cohort": {"id": "cohort-a", "name": "试点", "status": "active", "organization_id": "school-a"},
        "stage": {"id": "stage-a", "name": "终稿", "key": "final", "cohort_id": "cohort-a"},
        "supervision": {"id": "supervision-a", "cohort_id": "cohort-a", "student_id": "student-a", "teacher_id": "teacher-a"},
        "participant": {"id": "participant-a", "cohort_id": "cohort-a", "stage_id": "stage-a", "student_id": "student-a", "project_id": "project-a", "state": "archived", "current_submission_id": "submission-a"},
        "submission": {"id": "submission-a", "participant_id": "participant-a", "project_version_id": "version-a", "submission_number": "SUB-A", "state": "approved"},
        "review": {"id": "review-a", "submission_id": "submission-a", "decision": "returned", "reason": "请补充实验结果"},
        "project_version": {"id": "version-a", "project_id": "project-a"},
        "archive": {"id": "archive-a", "participant_id": "participant-a", "submission_id": "submission-a", "status": "succeeded"},
    }
    with store.workflow_transaction() as conn:
        for kind, record in records.items():
            store.save_workflow_record_conn(conn, kind, record)
        for index in range(501):
            for kind, payload in (
                ("participant", {"cohort_id": "other", "stage_id": "other", "student_id": "other", "project_id": "other", "state": "in_progress"}),
                ("submission", {"participant_id": "missing"}),
                ("review", {"submission_id": "missing"}),
                ("project_version", {"project_id": "other"}),
                ("archive", {"participant_id": "missing"}),
            ):
                store.save_workflow_record_conn(conn, kind, {"id": f"noise-{kind}-{index}", **payload})
        for kind in ("participant", "submission", "review", "project_version", "archive"):
            conn.execute("UPDATE workflow_records SET updated_at = ? WHERE kind = ? AND id = ?",
                         ("2000-01-01T00:00:00Z", kind, records[kind]["id"]))
    return store, project, student, teacher, other


def test_old_task_submission_and_archive_remain_visible_only_to_assigned_people(older_workflow):
    store, _, student, teacher, other = older_workflow
    for user in (student, teacher):
        assert [item["id"] for item in workflow.my_tasks(store, user)] == ["participant-a"]
        assert [item["id"] for item in workflow.list_submissions(store, user)] == ["submission-a"]
        assert [item["id"] for item in workflow.list_archives(store, user)] == ["archive-a"]
    assert workflow.my_tasks(store, other) == []
    assert workflow.list_submissions(store, other) == []
    assert workflow.list_archives(store, other) == []


def test_old_submitted_version_remains_visible_and_cannot_be_deleted(older_workflow):
    store, project, student, _, _ = older_workflow
    assert [item["id"] for item in workflow.list_project_versions(project.id, store, student)] == ["version-a"]
    participant = store.get_workflow_record("participant", "participant-a")
    participant["state"] = "needs_revision"
    store.save_workflow_record("participant", participant, expected_revision=participant["revision"])
    with pytest.raises(HTTPException) as exc:
        projects.delete_project(project.id, store, student)
    assert exc.value.status_code == 409
    assert not store.get_project(project.id).deleted_at


def test_old_archived_task_still_blocks_student_editing(older_workflow):
    store, project, student, _, _ = older_workflow
    with pytest.raises(HTTPException) as exc:
        projects._ensure_student_can_write(project, student, store)
    assert exc.value.status_code == 409


def test_old_approved_task_satisfies_next_stage_prerequisite(older_workflow):
    store, _, student, _, _ = older_workflow
    assert workflow._prerequisites_satisfied(
        store, {"student_id": student["id"]}, {"prerequisite_stage_ids": ["stage-a"]},
    )


def test_legacy_submission_still_shows_its_review_reason(older_workflow):
    store, _, _, _, _ = older_workflow
    review = workflow._latest_review(store, store.get_workflow_record("submission", "submission-a"))
    assert review["reason"] == "请补充实验结果"


def test_transfer_updates_old_open_task_and_notifies_new_teacher(older_workflow, monkeypatch):
    store, _, _, _, _ = older_workflow
    participant = store.get_workflow_record("participant", "participant-a")
    participant.update(state="needs_revision", teacher_id="teacher-a")
    store.save_workflow_record("participant", participant, expected_revision=participant["revision"])
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE workflow_records SET updated_at = ? WHERE kind = ? AND id = ?",
                     ("2000-01-01T00:00:00Z", "participant", "participant-a"))
    monkeypatch.setattr(workflow, "_ensure_cohort_access", lambda _store, _user, cohort_id: store.get_workflow_record("cohort", cohort_id))
    monkeypatch.setattr(workflow, "_ensure_active_person", lambda _store, user_id, role, _label: {"id": user_id, "role": role})
    workflow.upsert_member(
        "cohort-a", workflow.MemberUpsert(student_id="student-a", teacher_id="teacher-b"),
        store, {"id": "admin-a", "role": "admin", "status": "active"},
    )
    assert store.get_workflow_record("participant", "participant-a")["teacher_id"] == "teacher-b"
    assert workflow.my_tasks(store, {"id": "teacher-a", "role": "teacher", "status": "active"}) == []
    assert [item["id"] for item in workflow.my_tasks(
        store, {"id": "teacher-b", "role": "teacher", "status": "active"},
    )] == ["participant-a"]
    with store._connect() as conn:
        recipients = {row["recipient_id"] for row in conn.execute(
            "SELECT recipient_id FROM workflow_events WHERE event_type = ? AND resource_id = ?",
            ("supervision.transferred", "participant-a"),
        )}
    assert recipients == {"teacher-a", "teacher-b"}
