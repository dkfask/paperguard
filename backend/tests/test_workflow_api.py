"""完整业务闭环 API 回归：发布、提交、审核、归档和对象级授权。"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from docx import Document

from app.main import app
from app.db import Store
from app.worker import run_once


client = TestClient(app)


def _password(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}-8a"


def _login(username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _create_people(admin_headers: dict[str, str]) -> tuple[dict[str, str], dict[str, str], str, str]:
    suffix = uuid.uuid4().hex[:8]
    teacher_name = f"workflow_teacher_{suffix}"
    teacher_password = _password("Teacher")
    teacher = client.post("/api/auth/register", json={
        "username": teacher_name, "password": teacher_password,
        "role": "teacher", "display_name": "流程教师",
    })
    assert teacher.status_code == 201, teacher.text
    teacher_id = teacher.json()["id"]
    approved = client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=admin_headers,
                          json={"role": "teacher", "status": "active"})
    assert approved.status_code == 200, approved.text

    student_name = f"workflow_student_{suffix}"
    student_password = _password("Student")
    student = client.post("/api/auth/register", json={
        "username": student_name, "password": student_password,
        "role": "student", "display_name": "流程学生",
    })
    assert student.status_code == 201, student.text
    return _login(teacher_name, teacher_password), _login(student_name, student_password), teacher_id, student.json()["id"]


def _prepare_project(student_headers: dict[str, str], participant_id: str) -> str:
    bound = client.post(f"/api/stage-participants/{participant_id}/project", headers=student_headers,
                        json={"title": "闭环测试终稿"})
    assert bound.status_code == 200, bound.text
    project = bound.json()["project"]
    project_id = project["id"]
    updated = client.put(f"/api/projects/{project_id}/meta", headers=student_headers, json={
        "base_revision": project["revision"],
        "meta": {"school": "测试大学", "title": "闭环测试终稿", "author": "流程学生",
                  "student_id": "S-001", "major": "计算机", "advisor": "流程教师", "date": "2026-09-11"},
    })
    assert updated.status_code == 200, updated.text
    revision = updated.json()["revision"]
    for section in updated.json()["sections"]:
        if not section["required"]:
            continue
        saved = client.put(f"/api/projects/{project_id}/section/{section['key']}", headers=student_headers, json={
            "base_revision": revision,
            "paragraphs": [{"id": f"{section['key']}-p1", "text": "这是用于验证业务闭环的论文正文内容。"}],
        })
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
    return project_id


def test_workflow_happy_path_and_idempotency():
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-school-{suffix}", "name": "闭环测试学院", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    organization_id = organization.json()["id"]
    teacher, student, teacher_id, student_id = _create_people(admin)
    other_name = f"workflow_other_student_{suffix}"
    other_password = _password("OtherStudent")
    other = client.post("/api/auth/register", json={
        "username": other_name, "password": other_password, "role": "student", "display_name": "其他学生",
    })
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]
    _login(other_name, other_password)

    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "2027 届闭环批次", "organization_id": organization_id, "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text
    cohort_id = cohort.json()["id"]
    member = client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    })
    assert member.status_code == 201, member.text
    member2 = client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
        "student_id": other_id, "teacher_id": teacher_id,
    })
    assert member2.status_code == 201, member2.text
    student_cohort = client.get(f"/api/cohorts/{cohort_id}", headers=student)
    assert student_cohort.status_code == 200
    assert student_cohort.json()["member_ids"] == [student_id]
    assert {item["student_id"] for item in student_cohort.json()["members"]} == {student_id}

    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort_id}/stages", headers=admin, json={
        "key": "final", "name": "终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
        "required_manual_checks": ["导师确认"],
    })
    assert stage.status_code == 201, stage.text
    stage_id = stage.json()["id"]
    assert client.post(f"/api/stages/{stage_id}/preview", headers=admin).json()["ok"] is True
    published = client.post(f"/api/stages/{stage_id}/publish", headers=admin)
    assert published.status_code == 200, published.text

    task = client.get("/api/my-tasks", headers=student)
    assert task.status_code == 200 and len(task.json()) == 1
    participant_id = next(item["id"] for item in task.json() if item["student_id"] == student_id)
    project_id = _prepare_project(student, participant_id)
    acknowledged = client.post(f"/api/stage-participants/{participant_id}/acknowledge", headers=student, json={})
    assert acknowledged.status_code == 200, acknowledged.text

    candidate = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant_id, "modification_note": "首次提交",
    })
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    candidate_id = candidate.json()["id"]
    concurrent_candidate = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant_id, "modification_note": "并发候选版本",
    })
    assert concurrent_candidate.status_code == 201 and concurrent_candidate.json()["status"] == "ready", concurrent_candidate.text
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate_id, "idempotency_key": f"submit-{suffix}-1",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    raced = client.post("/api/submissions", headers=student, json={
        "candidate_id": concurrent_candidate.json()["id"], "idempotency_key": f"submit-{suffix}-race",
    })
    assert raced.status_code == 409 and raced.json()["detail"]["code"] == "SUBMISSION_ALREADY_PENDING"
    assert raced.json()["detail"]["submission_id"] == submission_id
    repeated = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate_id, "idempotency_key": f"submit-{suffix}-1",
    })
    assert repeated.status_code == 200 and repeated.json()["submission"]["id"] == submission_id
    listing = client.get("/api/submissions", headers=student)
    assert listing.status_code == 200, listing.text
    listed = next(item for item in listing.json() if item["id"] == submission_id)
    assert listed["modification_note"] == "首次提交"
    assert listed["project_id"] == project_id
    assert listed["open_blocking_count"] == 0
    forbidden_withdraw = client.post(f"/api/submissions/{submission_id}/withdraw", headers=teacher)
    assert forbidden_withdraw.status_code == 403
    withdrawn = client.post(f"/api/submissions/{submission_id}/withdraw", headers=student)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["state"] == "withdrawn"
    after_withdraw = client.get(f"/api/stage-participants/{participant_id}", headers=student)
    assert after_withdraw.status_code == 200, after_withdraw.text
    assert after_withdraw.json()["participant"]["state"] == "in_progress"
    assert after_withdraw.json()["participant"]["current_submission_id"] == ""
    assert after_withdraw.json()["participant"]["current_submission"]["id"] == submission_id
    assert after_withdraw.json()["participant"]["current_submission"]["state"] == "withdrawn"
    teacher_after_withdraw = client.get("/api/my-tasks", headers=teacher)
    assert teacher_after_withdraw.status_code == 200, teacher_after_withdraw.text
    withdrawn_task = next(item for item in teacher_after_withdraw.json() if item["id"] == participant_id)
    assert withdrawn_task["state"] == "in_progress"
    assert withdrawn_task["current_submission"]["id"] == submission_id
    assert withdrawn_task["current_submission"]["state"] == "withdrawn"
    not_pending = client.post(f"/api/submissions/{submission_id}/withdraw", headers=student)
    assert not_pending.status_code == 409 and not_pending.json()["detail"]["code"] == "WITHDRAW_NOT_PENDING"
    resubmitted = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant_id, "modification_note": "撤回后再次提交",
    })
    assert resubmitted.status_code == 201 and resubmitted.json()["status"] == "ready", resubmitted.text
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": resubmitted.json()["id"], "idempotency_key": f"submit-{suffix}-1b",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]

    comment = client.post(f"/api/submissions/{submission_id}/comments", headers=teacher, json={
        "body": "请补充实验分析", "severity": "blocking", "block_id": "ch1-p1",
    })
    assert comment.status_code == 201, comment.text
    comment_id = comment.json()["id"]
    returned = client.post(f"/api/submissions/{submission_id}/reviews", headers=teacher, json={
        "decision": "returned", "reason": "需要补充实验分析",
    })
    assert returned.status_code == 200, returned.text
    assert returned.json()["participant"]["state"] == "needs_revision"
    teacher_after_return = client.get("/api/my-tasks", headers=teacher)
    assert teacher_after_return.status_code == 200, teacher_after_return.text
    returned_task = next(item for item in teacher_after_return.json() if item["id"] == participant_id)
    assert returned_task["state"] == "needs_revision"
    assert returned_task["current_submission"]["id"] == submission_id
    assert returned_task["current_submission"]["state"] == "returned"
    returned_detail = client.get(f"/api/submissions/{submission_id}", headers=student)
    assert returned_detail.status_code == 200, returned_detail.text
    review = returned_detail.json()["review"]
    assert review["decision"] == "returned"
    assert review["reason"] == "需要补充实验分析"
    assert "reviewer_id" not in review
    assert client.post(f"/api/comments/{comment_id}/response", headers=student,
                       json={"body": "已补充实验分析"}).status_code == 201
    assert client.post(f"/api/comments/{comment_id}/status", headers=teacher,
                       json={"status": "resolved", "body": "已复核"}).status_code == 200

    candidate2 = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant_id, "modification_note": "按意见修订",
    })
    assert candidate2.status_code == 201 and candidate2.json()["status"] == "ready", candidate2.text
    submitted2 = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate2.json()["id"], "idempotency_key": f"submit-{suffix}-2",
    })
    assert submitted2.status_code == 200, submitted2.text
    submission2_id = submitted2.json()["submission"]["id"]
    blocked_approval = client.post(f"/api/submissions/{submission2_id}/reviews", headers=teacher, json={"decision": "approved"})
    assert blocked_approval.status_code == 409 and "导师确认" in blocked_approval.text
    approved = client.post(f"/api/submissions/{submission2_id}/reviews", headers=teacher, json={
        "decision": "approved", "manual_confirmations": {"导师确认": True},
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["participant"]["state"] == "pending_archive"

    archive = client.post("/api/archives", headers=admin, json={"submission_id": submission2_id})
    assert archive.status_code == 202 and archive.json()["status"] == "queued", archive.text
    assert "file_path" not in archive.json()
    archive_id = archive.json()["id"]
    job_id = archive.json()["job_id"]
    queued_job = client.get(f"/api/jobs/{job_id}", headers=admin)
    assert queued_job.status_code == 200 and queued_job.json()["state"] == "queued"
    assert "lease_token" not in queued_job.json() and "leased_by" not in queued_job.json()
    worker_result = run_once()
    assert job_id in worker_result["archives"]["succeeded"]
    archive_status = client.get(f"/api/archives/{archive_id}", headers=admin)
    assert archive_status.status_code == 200 and archive_status.json()["status"] == "succeeded"
    completed_job = client.get(f"/api/jobs/{job_id}", headers=admin)
    assert completed_job.status_code == 200 and completed_job.json()["state"] == "succeeded"
    assert archive_status.json()["download_url"].endswith(f"/api/archives/{archive_id}/download")
    download = client.get(f"/api/archives/{archive_id}/download", headers=student)
    assert download.status_code == 200 and download.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(download.content)) as bundle:
        assert {
            "final.docx", "content.json", "check-report.json", "submission.json", "template.json",
            "requirements.json", "reviews.json", "comments.json", "materials.json", "manifest.json",
        }.issubset(bundle.namelist())
        manifest = json.loads(bundle.read("manifest.json"))
        assert all(item["sha256"] and item["size_bytes"] >= 0 for item in manifest["files"])
    report = client.get(f"/api/reports/cohorts/{cohort_id}", headers=admin)
    assert report.status_code == 200 and report.json()["participant_count"] == 2 and report.json()["state_counts"]["archived"] == 1
    assert client.get(f"/api/reports/cohorts/{cohort_id}", headers=student).status_code == 403
    assert client.get("/api/notifications", headers=student).json()["unread"] >= 1
    assert project_id


def test_archive_missing_attachment_fails_then_manual_retry_succeeds():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-missing-asset-school-{suffix}", "name": "缺失附件测试学校", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "缺失附件归档批次", "organization_id": organization.json()["id"], "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text
    cohort_id = cohort.json()["id"]
    assert client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort_id}/stages", headers=admin, json={
        "key": "final", "name": "终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
    })
    assert stage.status_code == 201, stage.text
    assert client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"missing-asset-{suffix}",
    })
    assert submitted.status_code == 200, submitted.text
    submission = submitted.json()["submission"]
    approved = client.post(f"/api/submissions/{submission['id']}/reviews", headers=teacher, json={"decision": "approved"})
    assert approved.status_code == 200 and approved.json()["participant"]["state"] == "pending_archive"

    store = Store()
    version = store.get_workflow_record("project_version", submission["project_version_id"])
    assert version is not None
    version["attachment_refs"] = [{"filename": "missing-figure.png", "sha256": "0" * 64, "size_bytes": 1}]
    version = store.save_workflow_record("project_version", version, expected_revision=version["revision"])
    assert version is not None

    archive = client.post("/api/archives", headers=admin, json={"submission_id": submission["id"]})
    assert archive.status_code == 202, archive.text
    archive_id, job_id = archive.json()["id"], archive.json()["job_id"]
    failed = run_once()
    assert any(item["job_id"] == job_id for item in failed["archives"]["failed"])
    failed_archive = client.get(f"/api/archives/{archive_id}", headers=admin)
    assert failed_archive.status_code == 200 and failed_archive.json()["status"] == "failed"
    retried = client.post(f"/api/archives/{archive_id}/retry", headers=admin, json={"reason": "补齐正文附件后人工恢复"})
    assert retried.status_code == 200 and retried.json()["status"] == "queued"
    version["attachment_refs"] = []
    version = store.save_workflow_record("project_version", version, expected_revision=version["revision"])
    assert version is not None
    recovered = run_once()
    assert job_id in recovered["archives"]["succeeded"]
    completed = client.get(f"/api/archives/{archive_id}", headers=admin)
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"


def test_member_assignment_accepts_business_references_for_admin_flows():
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-ref-school-{suffix}", "name": "业务标识测试学院", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    teacher_name = f"ref_teacher_{suffix}"
    teacher = client.post("/api/auth/register", json={
        "username": teacher_name, "password": _password("Teacher"), "role": "teacher",
        "display_name": f"导师{suffix}",
    })
    assert teacher.status_code == 201, teacher.text
    teacher_id = teacher.json()["id"]
    assert client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=admin,
                      json={"role": "teacher", "status": "active"}).status_code == 200
    student_name = f"ref_student_{suffix}"
    student = client.post("/api/auth/register", json={
        "username": student_name, "password": _password("Student"), "role": "student",
        "display_name": f"学生{suffix}",
    })
    assert student.status_code == 201, student.text
    student_id = student.json()["id"]
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "业务标识导入批次", "organization_id": organization.json()["id"],
    })
    assert cohort.status_code == 201, cohort.text
    cohort_id = cohort.json()["id"]

    direct = client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
        "student_ref": student_name, "teacher_ref": teacher_name, "reason": "按学号/工号分配",
    })
    assert direct.status_code == 201, direct.text
    assert direct.json()["student_id"] == student_id
    assert direct.json()["teacher_id"] == teacher_id

    preview = client.post(f"/api/cohorts/{cohort_id}/members/import", headers=admin, json={
        "dry_run": True,
        "rows": [{"student_ref": student_name, "teacher_ref": teacher_name, "reason": "重复导入预检"}],
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"] == {"total": 1, "imported": 0, "ready": 1, "failed": 0}
    assert preview.json()["results"][0]["payload"]["student_id"] == student_id


def test_teacher_transfer_updates_open_task_and_resource_access():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    second_name = f"workflow_transfer_teacher_{suffix}"
    second_password = _password("TransferTeacher")
    second = client.post("/api/auth/register", json={
        "username": second_name, "password": second_password, "role": "teacher", "display_name": "接手教师",
    })
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert client.put(f"/api/auth/admin/users/{second_id}/role-status", headers=admin,
                      json={"role": "teacher", "status": "active"}).status_code == 200
    second_teacher = _login(second_name, second_password)

    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-transfer-school-{suffix}", "name": "导师转交测试学校", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "导师转交闭环批次", "organization_id": organization.json()["id"], "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text
    cohort_id = cohort.json()["id"]
    member = client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id, "reason": "初始分配",
    })
    assert member.status_code == 201, member.text
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort_id}/stages", headers=admin, json={
        "key": "draft", "name": "初稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    })
    assert stage.status_code == 201, stage.text
    assert client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin).status_code == 200
    task = client.get("/api/my-tasks", headers=student)
    assert task.status_code == 200 and len(task.json()) == 1
    participant = task.json()[0]
    assert participant["teacher_id"] == teacher_id
    assert client.get(f"/api/stage-participants/{participant['id']}", headers=teacher).status_code == 200

    transferred = client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": second_id, "reason": "导师工作调整",
    })
    assert transferred.status_code == 201, transferred.text
    assert transferred.json()["teacher_id"] == second_id
    updated = client.get(f"/api/stage-participants/{participant['id']}", headers=student)
    assert updated.status_code == 200 and updated.json()["participant"]["teacher_id"] == second_id
    assert client.get(f"/api/stage-participants/{participant['id']}", headers=teacher).status_code == 404
    assert client.get(f"/api/stage-participants/{participant['id']}", headers=second_teacher).status_code == 200
    messages = client.get("/api/notifications", headers=second_teacher)
    assert messages.status_code == 200
    assert any(item.get("data", {}).get("event_type") == "supervision.transferred" for item in messages.json()["items"])


def test_workflow_direct_id_denial_and_publish_requires_members():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-school-{suffix}", "name": "阻断测试学院", "type": "school",
    })
    assert organization.status_code == 201
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "缺指导教师批次", "organization_id": organization.json()["id"],
    }).json()
    member = client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    })
    assert member.status_code == 201
    import_preview = client.post(f"/api/cohorts/{cohort['id']}/members/import", headers=admin, json={
        "dry_run": True,
        "rows": [
            {"student_id": student_id, "teacher_id": teacher_id, "reason": "重复导入预检"},
            {"student_id": "missing-student", "teacher_id": teacher_id, "reason": "无效账号"},
        ],
    })
    assert import_preview.status_code == 200
    assert import_preview.json()["summary"] == {"total": 2, "imported": 0, "ready": 1, "failed": 1}
    import_result = client.post(f"/api/cohorts/{cohort['id']}/members/import", headers=admin, json={
        "rows": import_preview.json()["failed_rows"] + [import_preview.json()["results"][0]["payload"]],
    })
    assert import_result.status_code == 200 and import_result.json()["summary"]["imported"] == 1
    assert import_result.json()["summary"]["failed"] == 1
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "draft", "name": "初稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    }).json()
    # 学生不能查看别人的批次，教师也不能绕过关系操作陌生提交 ID。
    assert client.get(f"/api/cohorts/{cohort['id']}", headers=student).status_code == 200
    assert client.get("/api/submission-candidates/unknown", headers=student).status_code == 404
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200

    empty_cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "空批次", "organization_id": organization.json()["id"],
    }).json()
    empty_stage = client.post(f"/api/cohorts/{empty_cohort['id']}/stages", headers=admin, json={
        "key": "final", "name": "空批次终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
    }).json()
    blocked = client.post(f"/api/stages/{empty_stage['id']}/publish", headers=admin)
    assert blocked.status_code == 409 and "成员" in blocked.text


def test_teacher_cannot_read_another_student_project_versions():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    other_name = f"version_scope_student_{suffix}"
    other_password = _password("OtherStudent")
    other = client.post("/api/auth/register", json={
        "username": other_name, "password": other_password, "role": "student", "display_name": "版本范围学生",
    })
    assert other.status_code == 201
    other_headers = _login(other_name, other_password)
    second_teacher_name = f"version_scope_teacher_{suffix}"
    second_teacher_password = _password("SecondTeacher")
    second_teacher = client.post("/api/auth/register", json={
        "username": second_teacher_name, "password": second_teacher_password,
        "role": "teacher", "display_name": "另一位指导教师",
    })
    assert second_teacher.status_code == 201
    second_teacher_id = second_teacher.json()["id"]
    assert client.put(f"/api/auth/admin/users/{second_teacher_id}/role-status", headers=admin,
                      json={"role": "teacher", "status": "active"}).status_code == 200
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"version-scope-school-{suffix}", "name": "版本范围学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "版本范围批次", "organization_id": organization["id"],
    }).json()
    for target_student, target_teacher in ((student_id, teacher_id), (other.json()["id"], second_teacher_id)):
        assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
            "student_id": target_student, "teacher_id": target_teacher,
        }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "draft", "name": "版本范围阶段", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    other_task = next(item for item in client.get("/api/my-tasks", headers=other_headers).json() if item["student_id"] == other.json()["id"])
    other_project = _prepare_project(other_headers, other_task["id"])
    assert client.post(f"/api/stage-participants/{other_task['id']}/acknowledge", headers=other_headers, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=other_headers, json={"participant_id": other_task["id"]})
    assert candidate.status_code == 201
    versions = client.get(f"/api/projects/{other_project}/versions", headers=teacher)
    assert versions.status_code == 403


def test_process_records_attachments_and_sequential_approvals(tmp_path, monkeypatch):
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("PAPERGUARD_PROCESS_FILE_DIR", str(tmp_path / "process-files"))
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"record-school-{suffix}", "name": "材料测试学院", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "材料测试批次", "organization_id": organization.json()["id"],
    }).json()
    member = client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    })
    assert member.status_code == 201, member.text
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "final", "name": "材料终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
        "required_materials": ["task_book"],
    })
    assert stage.status_code == 201, stage.text
    published = client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin)
    assert published.status_code == 200, published.text
    participant = client.get("/api/my-tasks", headers=student).json()[0]

    catalog = client.get("/api/process-records/catalog", headers=student)
    assert catalog.status_code == 200
    assert {item["artifact_type"] for item in catalog.json()["participant"]} >= {"task_book", "similarity_report", "defense_application"}
    assert next(item for item in catalog.json()["participant"] if item["artifact_type"] == "final_report")["source"].startswith("4.")
    assert "midterm_quality_check" in {item["artifact_type"] for item in catalog.json()["cohort"]}
    assert {item["artifact_type"] for item in catalog.json()["reference"]} >= {"academic_integrity_policy", "university_writing_standard", "similarity_notice"}

    record = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "任务书", "data": {"title": "材料测试任务书", "requirements": "完成系统实现"},
    })
    assert record.status_code == 201, record.text
    record_id = record.json()["id"]
    attachment = client.post(f"/api/process-records/{record_id}/attachments", headers=student, files={
        "file": ("task-book.docx", (Path(__file__).parent / "fixtures" / "sample_paper.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert attachment.status_code == 201, attachment.text
    assert attachment.json()["sha256"] and "storage_path" not in attachment.json()
    downloaded = client.get(attachment.json()["download_url"], headers=student)
    assert downloaded.status_code == 200 and downloaded.content[:2] == b"PK"
    submitted = client.post(f"/api/process-records/{record_id}/submit", headers=student)
    assert submitted.status_code == 200 and submitted.json()["state"] == "submitted"
    approved = client.post(f"/api/process-records/{record_id}/decisions", headers=teacher, json={"decision": "approved"})
    assert approved.status_code == 200 and approved.json()["state"] == "approved"

    cover = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "process_cover", "data": {"source_filename": "0-过程性文档封面.doc"},
    })
    assert cover.status_code == 201, cover.text
    cover_attachment = client.post(f"/api/process-records/{cover.json()['id']}/attachments", headers=student, files={
        "file": ("0-过程性文档封面.doc", b"legacy OLE document fixture", "application/msword"),
    })
    assert cover_attachment.status_code == 201, cover_attachment.text
    assert cover_attachment.json()["name"] == "0-过程性文档封面.doc"

    score = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=teacher, json={
        "artifact_type": "advisor_score", "data": {"score": 88, "comment": "完成情况良好"},
    })
    assert score.status_code == 201, score.text
    score_id = score.json()["id"]
    assert client.post(f"/api/process-records/{score_id}/submit", headers=teacher).status_code == 200
    assert client.post(f"/api/process-records/{score_id}/decisions", headers=student, json={"decision": "approved"}).status_code == 403
    assert client.post(f"/api/process-records/{score_id}/decisions", headers=teacher, json={"decision": "approved"}).json()["state"] == "approved"

    extension = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "extension_application", "data": {"effective_deadline_at": (now + timedelta(days=10)).isoformat(), "reason": "实习安排冲突"},
    })
    assert extension.status_code == 201, extension.text
    extension_id = extension.json()["id"]
    assert client.post(f"/api/process-records/{extension_id}/submit", headers=student).status_code == 200
    assert client.post(f"/api/process-records/{extension_id}/decisions", headers=teacher, json={"decision": "approved"}).json()["pending_approvals"] == ["college_admin"]
    assert client.post(f"/api/process-records/{extension_id}/decisions", headers=admin, json={"decision": "approved"}).json()["pending_approvals"] == ["admin"]
    final = client.post(f"/api/process-records/{extension_id}/decisions", headers=admin, json={"decision": "approved"})
    assert final.status_code == 200 and final.json()["state"] == "approved"


def test_active_teacher_can_configure_only_their_scoped_workflow():
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    visible = client.post("/api/organizations", headers=admin, json={
        "id": f"teacher-workflow-visible-{suffix}", "name": "教师可见组织", "type": "school",
    })
    hidden = client.post("/api/organizations", headers=admin, json={
        "id": f"teacher-workflow-hidden-{suffix}", "name": "教师不可见组织", "type": "school",
    })
    assert visible.status_code == 201 and hidden.status_code == 201

    teacher_name = f"scoped_workflow_teacher_{suffix}"
    teacher_password = _password("ScopedTeacher")
    teacher = client.post("/api/auth/register", json={
        "username": teacher_name, "password": teacher_password, "role": "teacher", "display_name": "范围教师",
    })
    assert teacher.status_code == 201
    teacher_id = teacher.json()["id"]
    approved = client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=admin, json={
        "role": "teacher", "status": "active",
    })
    assert approved.status_code == 200
    scoped = client.put(f"/api/organizations/users/{teacher_id}/context", headers=admin, json={
        "organization_context": {"school_id": visible.json()["id"]},
    })
    assert scoped.status_code == 200
    teacher_headers = _login(teacher_name, teacher_password)

    cohort = client.post("/api/cohorts", headers=teacher_headers, json={
        "name": "教师范围批次", "organization_id": visible.json()["id"], "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text
    student_name = f"scoped_workflow_student_{suffix}"
    student_password = _password("ScopedStudent")
    student = client.post("/api/auth/register", json={
        "username": student_name, "password": student_password, "role": "student", "display_name": "范围学生",
    })
    assert student.status_code == 201
    member = client.post(f"/api/cohorts/{cohort.json()['id']}/members", headers=teacher_headers, json={
        "student_id": student.json()["id"], "teacher_id": teacher_id,
    })
    assert member.status_code == 201, member.text
    denied = client.post("/api/cohorts", headers=teacher_headers, json={
        "name": "越权批次", "organization_id": hidden.json()["id"], "academic_year": "2027",
    })
    assert denied.status_code == 403

    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort.json()['id']}/stages", headers=teacher_headers, json={
        "key": "teacher-config", "name": "教师配置阶段", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "allow_early_write": True,
    })
    assert stage.status_code == 201, stage.text
    cohort_detail = client.get(f"/api/cohorts/{cohort.json()['id']}", headers=teacher_headers)
    assert cohort_detail.status_code == 200, cohort_detail.text
    assert cohort_detail.json()["stages"][0]["config"]["open_at"]
    assert cohort_detail.json()["stages"][0]["config"]["deadline_at"]
    assert client.post(f"/api/stages/{stage.json()['id']}/preview", headers=teacher_headers).status_code == 200
    assert client.post(f"/api/stages/{stage.json()['id']}/publish", headers=teacher_headers).status_code == 200
    assert client.get("/api/reports/cohorts/unknown", headers=teacher_headers).status_code == 403


def test_supervising_teacher_can_extend_only_own_student_deadline():
    """指导教师可以给自己学生的阶段任务延期，但延不了别人指导的学生。"""
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]

    outsider_name = f"extension_outsider_{suffix}"
    outsider_password = _password("ExtensionOutsider")
    outsider_account = client.post("/api/auth/register", json={
        "username": outsider_name, "password": outsider_password, "role": "teacher", "display_name": "无关教师",
    })
    assert outsider_account.status_code == 201, outsider_account.text
    outsider_id = outsider_account.json()["id"]
    assert client.put(f"/api/auth/admin/users/{outsider_id}/role-status", headers=admin,
                      json={"role": "teacher", "status": "active"}).status_code == 200
    outsider = _login(outsider_name, outsider_password)

    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"extension-school-{suffix}", "name": "教师延期测试学校", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "教师延期批次", "organization_id": organization.json()["id"],
    })
    assert cohort.status_code == 201, cohort.text
    assert client.post(f"/api/cohorts/{cohort.json()['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201

    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort.json()['id']}/stages", headers=admin, json={
        "key": "extendable", "name": "可延期阶段", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=2)).isoformat(),
    })
    assert stage.status_code == 201, stage.text
    assert client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]

    # 非指导教师的教师看不到该批次，_participant_access 会 fail-closed 返回 404 而不是 403，
    # 以免通过状态码差异泄露批次是否存在。
    denied = client.put(f"/api/stage-participants/{participant['id']}/extension", headers=outsider, json={
        "effective_deadline_at": (now + timedelta(days=5)).isoformat(), "reason": "越权延期",
    })
    assert denied.status_code == 404, denied.text

    # 学生本人也不得给自己延期
    assert client.put(f"/api/stage-participants/{participant['id']}/extension", headers=student, json={
        "effective_deadline_at": (now + timedelta(days=5)).isoformat(), "reason": "自行延期",
    }).status_code == 403

    # 指导教师延期自己的学生应放行
    extended = client.put(f"/api/stage-participants/{participant['id']}/extension", headers=teacher, json={
        "effective_deadline_at": (now + timedelta(days=5)).isoformat(), "reason": "实习安排冲突",
    })
    assert extended.status_code == 200, extended.text
    assert extended.json()["effective_deadline_at"].startswith(
        (now + timedelta(days=5)).isoformat(timespec="seconds")[:16])
    assert extended.json()["extension"]["approved_by"]

    # 早于现有截止时间仍应被拒
    assert client.put(f"/api/stage-participants/{participant['id']}/extension", headers=teacher, json={
        "effective_deadline_at": (now + timedelta(days=1)).isoformat(), "reason": "试图提前",
    }).status_code == 422


def test_process_material_gate_requires_real_record_and_similarity_classification():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"gate-school-{suffix}", "name": "材料门禁学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "材料门禁批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "draft", "name": "材料门禁阶段", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "required_materials": ["similarity_report"],
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    project_id = _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200

    blocked = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert blocked.status_code == 201
    material_gate = next(gate for gate in blocked.json()["gates"] if gate["key"] == "materials")
    assert not material_gate["passed"] and "相似性" in material_gate["detail"][0]

    report = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "similarity_report", "data": {"copy_ratio": 35},
    })
    assert report.status_code == 201
    report_id = report.json()["id"]
    assert client.post(f"/api/process-records/{report_id}/submit", headers=student).status_code == 200
    assert client.post(f"/api/process-records/{report_id}/decisions", headers=teacher, json={"decision": "approved"}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate.status_code == 201
    assert next(gate for gate in candidate.json()["gates"] if gate["key"] == "materials")["passed"]
    assert project_id


def test_candidate_rejects_project_changes_after_precheck():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"stale-school-{suffix}", "name": "快照一致性学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "快照一致性批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "final", "name": "快照终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    project_id = _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200

    candidate = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant["id"], "modification_note": "预检后等待提交",
    })
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready"
    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    changed = client.put(f"/api/projects/{project_id}/meta", headers=student, json={
        "base_revision": project["revision"], "meta": {"title": "预检后发生变化"},
    })
    assert changed.status_code == 200
    second_candidate = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant["id"], "modification_note": "变更后重新预检",
    })
    assert second_candidate.status_code == 201 and second_candidate.json()["status"] == "ready"
    versions = client.get(f"/api/projects/{project_id}/versions", headers=student)
    assert versions.status_code == 200 and len(versions.json()) >= 2
    compare = client.get(
        f"/api/projects/{project_id}/versions/compare",
        headers=student,
        params={"left": candidate.json()["project_version_id"], "right": second_candidate.json()["project_version_id"]},
    )
    assert compare.status_code == 200 and compare.json()["diff"]["changed"] is True
    assert compare.json()["diff"]["meta_changed"] is True
    restore_conflict = client.post(f"/api/projects/{project_id}/restore-local", headers=student, json={
        "base_revision": project["revision"], "snapshot": {
            "title": project["title"], "meta": project["meta"], "sections": project["sections"],
        },
    })
    assert restore_conflict.status_code == 409 and restore_conflict.json()["detail"]["revision"] == changed.json()["revision"]
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"stale-{suffix}-1234",
    })
    assert submitted.status_code == 409 and submitted.json()["detail"]["code"] == "CHECK_STALE"


def test_workflow_project_write_respects_stage_open_time():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"opening-school-{suffix}", "name": "开放时间学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "开放时间批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "future", "name": "未来开放阶段", "open_at": (now + timedelta(hours=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    bound = client.post(f"/api/stage-participants/{participant['id']}/project", headers=student, json={"title": "待开放论文"})
    assert bound.status_code == 200
    project = bound.json()["project"]
    blocked = client.put(f"/api/projects/{project['id']}/meta", headers=student, json={
        "base_revision": project["revision"], "meta": {"title": "不应提前写入"},
    })
    assert blocked.status_code == 409 and "尚未开放" in blocked.text
    candidate_blocked = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate_blocked.status_code == 409 and "不允许准备提交" in candidate_blocked.text
    material_blocked = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "task_book", "data": {"title": "不应提前创建"},
    })
    assert material_blocked.status_code == 409 and "尚未开放" in material_blocked.text
    assert client.get(f"/api/projects/{project['id']}", headers=student).status_code == 200


def test_structured_process_forms_export_scores_and_lifecycle(tmp_path, monkeypatch):
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("PAPERGUARD_PROCESS_FILE_DIR", str(tmp_path / "process-files"))
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"structured-school-{suffix}", "name": "结构化材料学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "结构化材料批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "final", "name": "终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=2)).isoformat(), "is_final": True,
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]

    form = client.get(f"/api/stage-participants/{participant['id']}/process-records/form/task_book", headers=student)
    assert form.status_code == 200 and any(field["key"] == "topic" for field in form.json()["schema"]["fields"])
    incomplete = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "task_book", "structured_form": True, "data": {"student_id": "S1", "student_name": "张三"},
    })
    assert incomplete.status_code == 201
    assert client.post(f"/api/process-records/{incomplete.json()['id']}/submit", headers=student).status_code == 422

    task_book_data = {
        "student_id": "S1", "student_name": "张三", "advisor": "指导教师", "topic": "结构化闭环验证",
        "topic_nature": "实践性课题", "content": "完成需求分析、系统设计、系统实现和系统测试。",
    }
    complete = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "task_book", "structured_form": True, "data": task_book_data,
    })
    assert complete.status_code == 201, complete.text
    complete_id = complete.json()["id"]
    assert client.post(f"/api/process-records/{complete_id}/submit", headers=student).status_code == 200
    assert client.post(f"/api/process-records/{complete_id}/decisions", headers=teacher, json={"decision": "approved"}).status_code == 200
    exported = client.post(f"/api/process-records/{complete_id}/export", headers=student)
    assert exported.status_code == 200 and exported.content[:2] == b"PK"
    management = client.post(f"/api/cohorts/{cohort['id']}/process-records", headers=admin, json={
        "artifact_type": "defense_schedule", "structured_form": True,
        "data": {"defense_date": "2026-06-01", "groups": [{"name": "第一组", "students": ["S1"]}], "secretary": "答辩秘书"},
    })
    assert management.status_code == 201, management.text
    management_export = client.post(f"/api/process-records/{management.json()['id']}/export", headers=admin)
    assert management_export.status_code == 200 and management_export.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    with zipfile.ZipFile(io.BytesIO(management_export.content)) as workbook:
        assert "xl/workbook.xml" in workbook.namelist()

    for artifact_type, score in (("advisor_score", 90), ("reviewer_score", 80), ("defense_score", 70)):
        created = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=admin, json={
            "artifact_type": artifact_type, "data": {"score": score, "comment": "结构化评分"},
        })
        assert created.status_code == 201, created.text
        record_id = created.json()["id"]
        assert client.post(f"/api/process-records/{record_id}/submit", headers=admin).status_code == 200
        assert client.post(f"/api/process-records/{record_id}/decisions", headers=admin, json={"decision": "approved"}).status_code == 200
    score_summary = client.get(f"/api/stage-participants/{participant['id']}/score-summary", headers=student)
    assert score_summary.status_code == 200 and score_summary.json()["total_score"] == 78
    records = client.get(f"/api/stage-participants/{participant['id']}/process-records", headers=admin).json()
    comprehensive = next(item for item in records if item["artifact_type"] == "comprehensive_score")
    assert comprehensive["state"] == "submitted"
    assert client.post(f"/api/process-records/{comprehensive['id']}/decisions", headers=admin, json={"decision": "approved"}).status_code == 200
    assert len([item for item in client.get(f"/api/stage-participants/{participant['id']}/process-records", headers=admin).json() if item["artifact_type"] == "comprehensive_score"]) == 1

    assert client.post(f"/api/cohorts/{cohort['id']}/close", headers=admin, json={"reason": "仍有未完成任务"}).status_code == 409
    assert client.post(f"/api/cohorts/{cohort['id']}/close", headers=admin, json={"reason": "管理员确认收口", "force": True}).status_code == 200
    closed_member = client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    })
    assert closed_member.status_code == 409 and "已关闭" in closed_member.text
    closed_stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "closed-stage", "name": "关闭后阶段",
    })
    assert closed_stage.status_code == 409 and "已关闭" in closed_stage.text
    assert client.post(f"/api/cohorts/{cohort['id']}/reopen", headers=admin, json={"reason": "补充材料"}).status_code == 200
    assert client.post(f"/api/stage-participants/{participant['id']}/exit", headers=admin, json={"reason": "测试退出"}).status_code == 200
    assert client.get(f"/api/stage-participants/{participant['id']}", headers=student).json()["participant"]["state"] == "exited"


def test_stage_config_upgrade_requires_explicit_participant_migration_and_outbox_ack():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"versioned-stage-school-{suffix}", "name": "阶段版本学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "阶段版本批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "versioned", "name": "版本化阶段", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "revision_days": 7,
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    old_config_id = participant["config_version_id"]
    current_config = client.get(f"/api/cohorts/{cohort['id']}/stages", headers=admin).json()[0]["config"]

    draft = client.post(f"/api/stages/{stage['id']}/versions", headers=admin, json={
        "revision_days": 14, "reason": "试点后统一延长修订期限", "base_revision": current_config["revision"],
    })
    assert draft.status_code == 201, draft.text
    assert draft.json()["diff"] == [{"field": "revision_days", "before": 7, "after": 14}]
    new_config_id = draft.json()["new_config"]["id"]
    published = client.post(f"/api/stages/{stage['id']}/versions/{new_config_id}/publish", headers=admin, json={
        "reason": "发布新配置但暂不迁移存量任务",
    })
    assert published.status_code == 200, published.text
    untouched = client.get(f"/api/stage-participants/{participant['id']}", headers=student).json()
    assert untouched["config"]["id"] == old_config_id
    assert untouched["config"]["revision_days"] == 7

    current_config = client.get(f"/api/cohorts/{cohort['id']}/stages", headers=admin).json()[0]["config"]
    second_draft = client.post(f"/api/stages/{stage['id']}/versions", headers=admin, json={
        "revision_days": 21, "deadline_at": (now + timedelta(hours=1)).isoformat(),
        "reason": "再次调整修订期限", "base_revision": current_config["revision"],
    })
    assert second_draft.status_code == 201, second_draft.text
    second_id = second_draft.json()["new_config"]["id"]
    migrated = client.post(f"/api/stages/{stage['id']}/versions/{second_id}/publish", headers=admin, json={
        "participant_ids": [participant["id"]], "reason": "明确迁移该学生任务",
    })
    assert migrated.status_code == 200, migrated.text
    current = client.get(f"/api/stage-participants/{participant['id']}", headers=student).json()
    assert current["config"]["id"] == second_id and current["config"]["revision_days"] == 21
    assert current["participant"]["config_migration"]["from_config_id"] == old_config_id
    assert current["participant"]["acknowledged_snapshot_hash"] == ""
    assert current["participant"]["effective_deadline_at"] >= current["participant"]["initial_deadline_at"]

    outbox = client.get("/api/outbox", headers=admin)
    assert outbox.status_code == 200 and len(outbox.json()) >= 2
    claimed = client.post("/api/outbox/claim", headers=admin, json={
        "limit": 10, "lease_seconds": 60, "consumer_id": "workflow-test-consumer",
    })
    assert claimed.status_code == 200 and len(claimed.json()) >= 2
    first = claimed.json()[0]
    event_id = first["id"]
    missing_token_ack = client.post(f"/api/outbox/{event_id}/ack", headers=admin)
    assert missing_token_ack.status_code == 409
    ack = client.post(f"/api/outbox/{event_id}/ack", headers=admin, json={"lease_token": first["lease_token"]})
    assert ack.status_code == 200 and ack.json()["delivery_status"] == "delivered"
    assert event_id not in {item["id"] for item in client.get("/api/outbox", headers=admin).json()}

    retry_event = claimed.json()[1]
    failed = client.post(f"/api/outbox/{retry_event['id']}/fail", headers=admin, json={
        "error": "模拟通知渠道暂不可用", "lease_token": retry_event["lease_token"],
    })
    assert failed.status_code == 200 and failed.json()["delivery_status"] == "pending"
    for attempt in range(4):
        next_claim = client.post("/api/outbox/claim", headers=admin, json={"limit": 10, "consumer_id": "workflow-test-consumer"})
        assert next_claim.status_code == 200 and next_claim.json()
        retry_event = next_claim.json()[0]
        failed = client.post(f"/api/outbox/{retry_event['id']}/fail", headers=admin, json={
            "error": f"模拟第 {attempt + 2} 次失败", "lease_token": retry_event["lease_token"],
        })
        assert failed.status_code == 200
    assert failed.json()["delivery_status"] == "failed"
    parked = client.post(f"/api/outbox/{retry_event['id']}/retry", headers=admin, json={"reason": "渠道恢复"})
    assert parked.status_code == 200 and parked.json()["attempt_count"] == 0
    final_claim = client.post("/api/outbox/claim", headers=admin, json={"limit": 10, "consumer_id": "workflow-test-consumer"})
    assert final_claim.status_code == 200 and final_claim.json()
    final_event = next(item for item in final_claim.json() if item["id"] == retry_event["id"])
    final_ack = client.post(f"/api/outbox/{final_event['id']}/ack", headers=admin, json={"lease_token": final_event["lease_token"]})
    assert final_ack.status_code == 200


def test_submitted_version_isolated_from_student_draft_edits():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"snapshot-school-{suffix}", "name": "提交快照学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "提交快照批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "snapshot", "name": "快照阶段", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    project_id = _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    confirmed = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"snapshot-{suffix}-submit",
    })
    assert confirmed.status_code == 200, confirmed.text
    submission_id = confirmed.json()["submission"]["id"]
    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    changed = client.put(f"/api/projects/{project_id}/meta", headers=student, json={
        "base_revision": project["revision"], "meta": {"title": "提交后草稿标题"},
    })
    assert changed.status_code == 200, changed.text
    detail = client.get(f"/api/submissions/{submission_id}", headers=teacher)
    assert detail.status_code == 200, detail.text
    snapshot = detail.json()["version"]["content_snapshot"]
    assert snapshot["meta"].get("title") != "提交后草稿标题"
    assert detail.json()["version"]["content_hash"] == candidate.json()["content_hash"]
    assert detail.json()["review"] is None
    participant = detail.json()["participant"]
    assert participant["student_name"]
    assert participant["current_submission"]["id"] == submission_id
    assert participant["current_submission"]["submitted_at"]
    teacher_tasks = client.get("/api/my-tasks", headers=teacher)
    assert teacher_tasks.status_code == 200, teacher_tasks.text
    queued = next(item for item in teacher_tasks.json() if item["current_submission"] and item["current_submission"]["id"] == submission_id)
    assert queued["student_name"]
    assert queued["current_submission"]["submitted_at"]


def test_my_tasks_skips_incomplete_participant_records():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    store = Store()
    store.save_workflow_record("participant", {
        "id": f"p-incomplete-{uuid.uuid4().hex[:8]}",
        "state": "in_progress",
        "student_id": student_id,
        "effective_deadline_at": "2026-09-15T12:00:00+00:00",
        "revision": 0,
    })
    teacher_tasks = client.get("/api/my-tasks", headers=teacher)
    student_tasks = client.get("/api/my-tasks", headers=student)
    assert teacher_tasks.status_code == 200, teacher_tasks.text
    assert student_tasks.status_code == 200, student_tasks.text
    assert all(item.get("cohort_id") and item.get("stage_id") for item in teacher_tasks.json())
    assert all(item.get("cohort_id") and item.get("stage_id") for item in student_tasks.json())


def test_docx_manuscript_import_previews_then_applies_with_revision_guard(tmp_path):
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"docx-import-school-{suffix}", "name": "正文导入学院", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "正文导入批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "import", "name": "正文导入阶段", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    bound = client.post(f"/api/stage-participants/{participant['id']}/project", headers=student, json={"title": "待导入论文"})
    assert bound.status_code == 200
    project = bound.json()["project"]

    manuscript = tmp_path / "manuscript.docx"
    document = Document()
    document.core_properties.title = "外部论文标题"
    document.add_heading("研究背景", level=1)
    document.add_paragraph("这是从 Word 导入的正文段落。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "指标"
    table.cell(0, 1).text = "结果"
    table.cell(1, 0).text = "准确率"
    table.cell(1, 1).text = "98%"
    document.save(manuscript)
    payload = manuscript.read_bytes()
    preview = client.post(f"/api/projects/{project['id']}/import-docx", headers=student, files={
        "file": ("manuscript.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["stats"]["tables"] == 1
    assert preview.json()["stats"]["paragraphs"] >= 2
    assert preview.json()["applied"] is False
    stale = client.put(f"/api/projects/{project['id']}/meta", headers=student, json={
        "base_revision": project["revision"], "meta": {"title": "先保存的修改"},
    })
    assert stale.status_code == 200
    rejected = client.post(f"/api/projects/{project['id']}/import-docx?apply=true&base_revision={project['revision']}", headers=student, files={
        "file": ("manuscript.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert rejected.status_code == 409, rejected.text
    latest = client.get(f"/api/projects/{project['id']}", headers=student).json()
    applied = client.post(f"/api/projects/{project['id']}/import-docx?apply=true&base_revision={latest['revision']}", headers=student, files={
        "file": ("manuscript.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    imported = next(section for section in applied.json()["project"]["sections"] if section["title"] == "研究背景")
    assert "这是从 Word 导入的正文段落。" in imported["paragraphs"][0]["text"]
    assert any(block["type"] == "table" and block["table"]["cells"][1][1] == "98%" for block in imported["paragraphs"])


def test_multi_college_scope_and_two_stage_publish(closed_role_session):
    """Exercise the organization boundary with two colleges and two stages."""
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    school = client.post("/api/organizations", headers=admin, json={
        "id": f"multi-school-{suffix}", "name": "多学院测试学校", "type": "school",
    })
    assert school.status_code == 201, school.text
    school_id = school.json()["id"]
    colleges = []
    for index in (1, 2):
        response = client.post("/api/organizations", headers=admin, json={
            "id": f"multi-college-{index}-{suffix}", "name": f"第{index}学院", "type": "college", "parent_id": school_id,
        })
        assert response.status_code == 201, response.text
        colleges.append(response.json()["id"])

    college_admin_name = f"college_admin_{suffix}"
    college_admin_password = _password("CollegeAdmin")
    registered = client.post("/api/auth/register", json={
        "username": college_admin_name, "password": college_admin_password,
        "role": "student", "display_name": "学院管理员",
    })
    assert registered.status_code == 201, registered.text
    college_admin_id = registered.json()["id"]
    college_admin = closed_role_session(college_admin_id)

    for index, college_id in enumerate(colleges, start=1):
        teacher_name = f"multi_teacher_{index}_{suffix}"
        teacher_password = _password(f"MultiTeacher{index}")
        teacher = client.post("/api/auth/register", json={
            "username": teacher_name, "password": teacher_password,
            "role": "teacher", "display_name": f"学院{index}导师",
        })
        assert teacher.status_code == 201, teacher.text
        teacher_id = teacher.json()["id"]
        assert client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=admin,
                          json={"role": "teacher", "status": "active"}).status_code == 200
        student_name = f"multi_student_{index}_{suffix}"
        student_password = _password(f"MultiStudent{index}")
        student = client.post("/api/auth/register", json={
            "username": student_name, "password": student_password,
            "role": "student", "display_name": f"学院{index}学生",
        })
        assert student.status_code == 201, student.text
        student_id = student.json()["id"]
        cohort = client.post("/api/cohorts", headers=admin, json={
            "name": f"学院{index}多阶段批次", "organization_id": college_id, "academic_year": "2027",
        })
        assert cohort.status_code == 201, cohort.text
        cohort_id = cohort.json()["id"]
        member = client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
            "student_id": student_id, "teacher_id": teacher_id,
        })
        assert member.status_code == 201, member.text
        now = datetime.now(timezone.utc)
        for stage_key, stage_name in (("proposal", "开题阶段"), ("final", "终稿阶段")):
            stage = client.post(f"/api/cohorts/{cohort_id}/stages", headers=admin, json={
                "key": stage_key, "name": stage_name,
                "open_at": (now - timedelta(minutes=1)).isoformat(),
                "deadline_at": (now + timedelta(days=7)).isoformat(),
                "is_final": stage_key == "final",
            })
            assert stage.status_code == 201, stage.text
            published = client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin)
            assert published.status_code == 200, published.text

    all_cohorts = client.get("/api/cohorts", headers=admin)
    assert all_cohorts.status_code == 200
    assert {item["organization_id"] for item in all_cohorts.json()} >= set(colleges)
    scoped_cohorts = client.get("/api/cohorts", headers=college_admin)
    assert scoped_cohorts.status_code == 403
    scoped_organizations = client.get("/api/organizations", headers=college_admin)
    assert scoped_organizations.status_code == 403
    hidden_id = next(item["id"] for item in all_cohorts.json() if item["organization_id"] == colleges[1])
    hidden = client.get(f"/api/cohorts/{hidden_id}", headers=college_admin)
    assert hidden.status_code == 403



def test_midterm_teacher_fields_preserve_student_data_and_require_assigned_teacher():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]

    outsider_name = f"midterm_outsider_{suffix}"
    outsider_password = _password("MidtermOutsider")
    outsider = client.post("/api/auth/register", json={
        "username": outsider_name, "password": outsider_password,
        "role": "teacher", "display_name": "未指派教师",
    })
    assert outsider.status_code == 201, outsider.text
    outsider_id = outsider.json()["id"]
    assert client.put(f"/api/auth/admin/users/{outsider_id}/role-status", headers=admin,
                      json={"role": "teacher", "status": "active"}).status_code == 200
    outsider_headers = _login(outsider_name, outsider_password)

    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"midterm-school-{suffix}", "name": "中期检查字段隔离学院", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "中期检查字段隔离批次", "organization_id": organization.json()["id"], "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text
    member = client.post(f"/api/cohorts/{cohort.json()['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    })
    assert member.status_code == 201, member.text
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort.json()['id']}/stages", headers=admin, json={
        "key": "midterm", "name": "中期检查", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=2)).isoformat(), "required_materials": [],
    })
    assert stage.status_code == 201, stage.text
    published = client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin)
    assert published.status_code == 200, published.text
    participant = client.get("/api/my-tasks", headers=student).json()[0]

    form = client.get(f"/api/stage-participants/{participant['id']}/process-records/form/midterm_check", headers=student)
    assert form.status_code == 200, form.text
    student_field_keys = {field["key"] for field in form.json()["schema"]["fields"]}
    assert {"progress", "problems", "next_plan"} <= student_field_keys
    assert not ({"advisor_comment", "score"} & student_field_keys)

    student_data = {
        "title": "合成中期检查", "student_id": "S-MIDTERM", "student_name": "合成学生",
        "topic": "合成论文题目", "progress": "已完成需求分析和主体实现",
        "problems": "待评估性能边界", "next_plan": "补充压力测试并整理结果",
    }
    forbidden_create = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "midterm_check", "structured_form": True,
        "data": {**student_data, "advisor_comment": "学生试图填写教师意见"},
    })
    assert forbidden_create.status_code == 403, forbidden_create.text
    teacher_create = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=teacher, json={
        "artifact_type": "midterm_check", "structured_form": True, "data": student_data,
    })
    assert teacher_create.status_code == 403, teacher_create.text

    created = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=student, json={
        "artifact_type": "midterm_check", "structured_form": True, "data": student_data,
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]
    forbidden_draft_update = client.put(f"/api/process-records/{record_id}", headers=student, json={
        "base_revision": created.json()["revision"], "structured_form": True,
        "data": {**student_data, "score": 99},
    })
    assert forbidden_draft_update.status_code == 403, forbidden_draft_update.text

    submitted = client.post(f"/api/process-records/{record_id}/submit", headers=student)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["state"] == "submitted"
    inbox = client.get("/api/process-records/inbox", headers=teacher)
    assert inbox.status_code == 200
    assert record_id in {item["id"] for item in inbox.json()}

    unassigned_update = client.patch(f"/api/process-records/{record_id}/teacher-fields", headers=outsider_headers, json={
        "base_revision": submitted.json()["revision"], "data": {"advisor_comment": "越权意见"},
    })
    assert unassigned_update.status_code in {403, 404}, unassigned_update.text
    teacher_student_field_update = client.patch(f"/api/process-records/{record_id}/teacher-fields", headers=teacher, json={
        "base_revision": submitted.json()["revision"],
        "data": {"advisor_comment": "中期进度符合计划", "progress": "被教师覆盖"},
    })
    assert teacher_student_field_update.status_code == 403, teacher_student_field_update.text
    invalid_score = client.patch(f"/api/process-records/{record_id}/teacher-fields", headers=teacher, json={
        "base_revision": submitted.json()["revision"], "data": {"score": 101},
    })
    assert invalid_score.status_code == 422, invalid_score.text

    filled = client.patch(f"/api/process-records/{record_id}/teacher-fields", headers=teacher, json={
        "base_revision": submitted.json()["revision"],
        "data": {"advisor_comment": "按计划完成阶段目标，建议继续验证边界情况。", "score": 88},
    })
    assert filled.status_code == 200, filled.text
    assert filled.json()["state"] == "submitted"
    assert filled.json()["data"] == {
        **student_data, "advisor_comment": "按计划完成阶段目标，建议继续验证边界情况。", "score": 88.0,
    }
    stale_update = client.patch(f"/api/process-records/{record_id}/teacher-fields", headers=teacher, json={
        "base_revision": submitted.json()["revision"], "data": {"score": 90},
    })
    assert stale_update.status_code == 409, stale_update.text
    student_teacher_fields_update = client.patch(f"/api/process-records/{record_id}/teacher-fields", headers=student, json={
        "base_revision": filled.json()["revision"], "data": {"score": 0},
    })
    assert student_teacher_fields_update.status_code == 403, student_teacher_fields_update.text

    approved = client.post(f"/api/process-records/{record_id}/decisions", headers=teacher, json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"
    student_readback = client.get(f"/api/process-records/{record_id}", headers=student)
    assert student_readback.status_code == 200, student_readback.text
    assert student_readback.json()["data"] == filled.json()["data"]
