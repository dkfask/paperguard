"""完整业务闭环 API 回归：发布、提交、审核、归档和对象级授权。"""

from __future__ import annotations

import io
import base64
import hashlib
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from docx import Document

from app.main import app
from app.db import Store
from app.worker import run_once
from app.services.docx_output_check import DocxOutputCheckError
from tests._helpers import register_fixture_response


client = TestClient(app)


@pytest.mark.parametrize("workflow", [False, True])
def test_workflow_manager_guard_rejects_closed_college_admin(workflow):
    from fastapi import HTTPException
    from app.api.workflow import _MANAGER_ROLES, _WORKFLOW_MANAGER_ROLES, _ensure_manager
    from app.api.process_records import _role_can_approve
    from app.auth import can_manage

    assert _MANAGER_ROLES == {"admin"}
    assert _WORKFLOW_MANAGER_ROLES == {"admin", "teacher"}
    assert not can_manage({"role": "college_admin", "status": "active"})
    for required_role in ("college_admin", "admin", "teacher", "defense_group"):
        assert not _role_can_approve({"id": "closed-user", "role": "college_admin"}, required_role, None)
    assert _role_can_approve({"id": "admin-user", "role": "admin"}, "college_admin", None)
    with pytest.raises(HTTPException) as error:
        _ensure_manager({"role": "college_admin", "status": "active"}, workflow=workflow)
    assert error.value.status_code == 403
    assert error.value.detail == (
        "仅教师或系统管理员可配置工作流" if workflow else "仅系统管理员可执行此操作"
    )


def test_workflow_visibility_helpers_reject_closed_and_inactive_roles(tmp_path):
    from app.api.workflow import _cohort_access, _participant_visible, _visible_supervisions

    store = Store(tmp_path / "workflow-visibility.db")
    cohort = {"id": "visibility-cohort", "organization_id": "visibility-org"}
    participant = {"cohort_id": cohort["id"], "student_id": "visibility-student"}
    store.save_workflow_record("cohort", cohort)

    for user in (
        {"id": "closed", "role": "college_admin", "status": "active"},
        {"id": "pending", "role": "teacher", "status": "pending"},
    ):
        assert _cohort_access(store, user, cohort) is False
        assert _visible_supervisions(store, user, cohort["id"]) == []
        assert _participant_visible(store, user, participant) is False


@pytest.mark.parametrize("ready_submission_candidate", ["m1"], indirect=True)
@pytest.mark.parametrize("role", ["college_admin", "reviewer", "platform_admin", "unknown"])
def test_closed_role_cannot_access_real_submission_or_archive(
    closed_role_session, ready_submission_candidate, role, tmp_path, monkeypatch,
):
    fixture = ready_submission_candidate
    store = Store()
    monkeypatch.setenv("PAPERGUARD_PROCESS_FILE_DIR", str(tmp_path / "process-files"))
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    stage = store.get_workflow_record("stage", participant["stage_id"])
    config_id = participant.get("config_version_id") or stage["config_version_id"]
    config = store.get_workflow_record("stage_config", config_id)
    config["required_materials"] = ["task_book"]
    assert store.save_workflow_record("stage_config", config, expected_revision=config["revision"])
    record = client.post(f"/api/stage-participants/{participant['id']}/process-records",
                         headers=fixture["student"], json={
                             "artifact_type": "task_book", "data": {"title": "真实材料", "requirements": "闭环权限测试"},
                         })
    assert record.status_code == 201, record.text
    material_id = record.json()["id"]
    material = client.post(f"/api/process-records/{material_id}/attachments", headers=fixture["student"], files={
        "file": ("task.txt", b"frozen workflow material", "text/plain"),
    })
    assert material.status_code == 201, material.text
    file_id = material.json()["id"]
    assert client.post(f"/api/process-records/{material_id}/submit", headers=fixture["student"]).status_code == 200
    assert client.post(f"/api/process-records/{material_id}/decisions", headers=fixture["teacher"],
                       json={"decision": "approved"}).status_code == 200

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )
    project_id = participant["project_id"]
    project = client.get(f"/api/projects/{project_id}", headers=fixture["student"])
    uploaded = client.post(f"/api/projects/{project_id}/images", headers=fixture["student"], files={
        "file": ("figure.png", png, "image/png"),
    })
    assert uploaded.status_code == 201, uploaded.text
    section = next(item for item in project.json()["sections"] if item["key"] == "ch1")
    saved = client.put(f"/api/projects/{project_id}/section/ch1", headers=fixture["student"], json={
        "base_revision": project.json()["revision"],
        "paragraphs": section["paragraphs"] + [{
            "id": "closed-role-real-image", "type": "image",
            "image": {"url": uploaded.json()["url"], "caption": "图 1.1 权限测试"},
        }],
    })
    assert saved.status_code == 200, saved.text
    candidate = client.post("/api/submission-candidates", headers=fixture["student"], json={
        "participant_id": fixture["participant_id"],
    })
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": candidate.json()["id"],
        "idempotency_key": f"closed-role-resource-{role}-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    materials = client.get(f"/api/submissions/{submission_id}", headers=fixture["teacher"])
    assert materials.status_code == 200 and materials.json()["submitted_materials_available"] is True
    image_download = client.get(f"/api/submissions/{submission_id}/image", headers=fixture["teacher"],
                                params={"block_id": "closed-role-real-image"})
    assert image_download.status_code == 200 and image_download.content == png
    material_download = client.get(
        f"/api/submissions/{submission_id}/materials/{file_id}/download", headers=fixture["teacher"],
    )
    assert material_download.status_code == 200 and material_download.content == b"frozen workflow material"
    approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"], json={
        "decision": "approved",
    })
    assert approved.status_code == 200, approved.text
    archive_id = approved.json()["archive"]["id"]
    from app.worker import run_once

    run_once()
    archive_download = client.get(f"/api/archives/{archive_id}/download", headers=fixture["student"])
    assert archive_download.status_code == 200, archive_download.text
    assert client.get(f"/api/submissions/{submission_id}", headers=fixture["student"]).status_code == 200
    assert client.get(f"/api/submissions/{submission_id}/document", headers=fixture["student"]).status_code == 200
    assert client.get(f"/api/submissions/{submission_id}/image", headers=fixture["student"],
                      params={"block_id": "closed-role-real-image"}).status_code == 200
    assert client.get(f"/api/submissions/{submission_id}/materials/{file_id}/download",
                      headers=fixture["student"]).status_code == 200
    assert client.get(f"/api/archives/{archive_id}", headers=fixture["teacher"]).status_code == 200
    assert client.get(f"/api/archives/{archive_id}/download", headers=fixture["teacher"]).status_code == 200
    other_teacher, other_student, _, _ = _create_people(fixture["admin"])
    for headers in (other_teacher, other_student):
        assert client.get(f"/api/submissions/{submission_id}", headers=headers).status_code in (403, 404)
        assert client.get(f"/api/archives/{archive_id}/download", headers=headers).status_code in (403, 404)
    assert client.get(f"/api/archives/{archive_id}", headers=fixture["admin"]).status_code == 200
    assert client.get(f"/api/archives/{archive_id}/download", headers=fixture["admin"]).status_code == 200
    legacy = register_fixture_response(
        client, f"closed_resource_{uuid.uuid4().hex[:10]}", _password("ClosedResource"),
        display_name="关闭角色资源测试",
    )
    closed_session = closed_role_session(legacy.json()["id"], role=role)
    requests = [
        ("get", f"/api/cohorts/{fixture['cohort_id']}", None),
        ("get", f"/api/stage-participants/{fixture['participant_id']}", None),
        ("get", f"/api/submission-candidates/{candidate.json()['id']}", None),
        ("get", f"/api/submissions/{submission_id}", None),
        ("get", f"/api/submissions/{submission_id}/document", None),
        ("get", f"/api/submissions/{submission_id}/image", {"block_id": "closed-role-real-image"}),
        ("get", f"/api/submissions/{submission_id}/materials/{file_id}/download", None),
        ("get", f"/api/process-files/{file_id}/download", None),
        ("post", f"/api/submissions/{submission_id}/comments", {"body": "越权批注"}),
        ("post", f"/api/submissions/{submission_id}/reviews", {"decision": "returned", "reason": "越权退回"}),
        ("get", f"/api/archives/{archive_id}", None),
        ("get", f"/api/archives/{archive_id}/download", None),
        ("get", f"/api/archives/{archive_id}/review-candidate", None),
        ("post", f"/api/archives/{archive_id}/retry", {"reason": "越权重试"}),
        ("post", f"/api/archives/{archive_id}/manual-review", {
            "output_sha256": "0" * 64, "confirmed_check_ids": [],
        }),
    ]
    for method, path, body in requests:
        response = client.request(method.upper(), path, headers=closed_session,
                                  params=body if method == "get" else None,
                                  json=body if method == "post" else None)
        assert response.status_code == 403, (method, path, response.text)
        payload = response.json()
        assert (payload.get("error") or payload.get("detail"))["code"] == "ROLE_NOT_OPEN"


def test_comment_lookup_is_not_limited_by_other_submissions(tmp_path):
    from app.api.workflow import _comment_records

    store = Store(tmp_path / "comments.db")
    with store.workflow_transaction() as conn:
        store.save_workflow_record_conn(conn, "comment", {
            "id": "target-comment", "submission_id": "target-submission", "status": "open",
        })
        conn.execute(
            "UPDATE workflow_records SET updated_at = ? WHERE kind = 'comment' AND id = ?",
            ("2000-01-01T00:00:00+00:00", "target-comment"),
        )
        for index in range(500):
            store.save_workflow_record_conn(conn, "comment", {
                "id": f"other-comment-{index}", "submission_id": "other-submission", "status": "open",
            })

    assert [item["id"] for item in _comment_records(store, "target-submission")] == ["target-comment"]


def test_prior_feedback_follows_revision_chain_and_rejects_invalid_history(tmp_path):
    from fastapi import HTTPException
    from app.api.workflow import _prior_unresolved_comments

    store = Store(tmp_path / "feedback.db")
    store.save_workflow_record("project_version", {
        "id": "old-version", "content_snapshot": {"sections": [
            {"key": "intro", "title": "绪论", "paragraphs": [{"id": "p1", "text": "原稿内容"}]},
        ]},
    })
    store.save_workflow_record("submission", {
        "id": "first", "participant_id": "student-task", "project_version_id": "old-version",
        "submission_number": "SUB-OLD",
    })
    store.save_workflow_record("submission", {
        "id": "second", "participant_id": "student-task", "prior_submission_id": "first",
    })
    store.save_workflow_record("comment", {
        "id": "old-open", "submission_id": "first", "block_id": "p1",
        "status": "addressed", "severity": "blocking", "body": "补充背景",
    })
    store.save_workflow_record("comment", {
        "id": "already-resolved", "submission_id": "second", "status": "resolved", "severity": "blocking",
    })
    current = {"id": "third", "participant_id": "student-task", "prior_submission_id": "second"}

    feedback = _prior_unresolved_comments(store, current)
    assert [item["id"] for item in feedback] == ["old-open"]
    assert feedback[0]["source_submission_number"] == "SUB-OLD"
    assert feedback[0]["source_section_title"] == "绪论"
    assert feedback[0]["source_text"] == "原稿内容"
    with store.workflow_transaction() as conn:
        assert _prior_unresolved_comments(store, current, conn=conn) == feedback

    with pytest.raises(HTTPException) as missing:
        _prior_unresolved_comments(store, {**current, "prior_submission_id": "missing"})
    assert missing.value.status_code == 409
    with pytest.raises(HTTPException) as other_task:
        _prior_unresolved_comments(store, {**current, "participant_id": "other-task"})
    assert other_task.value.status_code == 409
    with pytest.raises(HTTPException) as cycle:
        _prior_unresolved_comments(store, {**current, "prior_submission_id": "third"})
    assert cycle.value.status_code == 409


def test_archive_worker_rejects_source_changed_after_request(tmp_path, monkeypatch):
    from app.api import workflow as workflow_api
    from app.models.project import WritingProject
    from app.models.template import PaperTemplate
    source = tmp_path / "archive-source.docx"
    Document().save(source)
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))
    monkeypatch.setattr(workflow_api, "_archive_dir", lambda: tmp_path / "archives")
    template = PaperTemplate(id="archive-source-template", name="归档源冻结测试", source_docx=source.name)
    project = WritingProject(id="archive-source-project", template_id=template.id, template_snapshot=template.model_dump(mode="json"))
    store = Store()
    store.save_workflow_record("project_version", {"id": "archive-source-version", "content_snapshot": project.model_dump(mode="json")})
    store.save_workflow_record("submission", {
        "id": "archive-source-submission",
        "project_version_id": "archive-source-version",
        "participant_id": "archive-source-participant",
    })
    store.save_workflow_record("participant", {"id": "archive-source-participant"})
    store.save_workflow_record("archive", {"id": "archive-source-change", "job_id": "archive-source-job", "submission_id": "archive-source-submission", "participant_id": "archive-source-participant", "status": "queued", "source_template_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    with store.workflow_transaction() as conn:
        store.create_job_conn(conn, job_id="archive-source-job", job_type="archive.generate", resource_type="archive", resource_id="archive-source-change")
    claimed = store.claim_jobs(job_type="archive.generate", consumer_id="source-test", limit=50)
    job = next(item for item in claimed if item["id"] == "archive-source-job")
    changed = Document(source)
    changed.core_properties.author = "changed after archive request"
    changed.save(source)
    invoked = []
    monkeypatch.setattr(workflow_api, "export_project_to_docx", lambda *_args, **_kwargs: invoked.append(True))
    result = workflow_api._run_archive(store, "archive-source-change", lease_token=job["lease_token"])
    assert result["status"] == "failed"
    assert not result.get("file_path")
    assert not invoked


def _password(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}-8a"


def _make_legacy_manual_archive(archive_id: str, message: str, monkeypatch, *, pending: bool = False):
    """Construct a pre-existing archive for backward-compatibility checks."""
    from app.api import workflow as workflow_api

    store = Store()
    archive = store.get_workflow_record("archive", archive_id)
    path = Path(archive["file_path"])
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["output_check"]["office_review_required"] = True
    manifest["output_check"]["manual_checks"] = [message]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    path.write_bytes(result.getvalue())
    archive["manifest"] = manifest
    archive["manifest_hash"] = workflow_api._hash(manifest)
    assert store.save_workflow_record("archive", archive, expected_revision=archive["revision"])
    if pending:
        participant = store.get_workflow_record("participant", archive["participant_id"])
        participant["state"] = "pending_archive"
        assert store.save_workflow_record("participant", participant, expected_revision=participant["revision"])

    original_check = workflow_api.require_docx_output_pass

    def check_legacy_archive(*args, **kwargs):
        report = original_check(*args, **kwargs)
        if kwargs.get("context_kind") == "workflow-archive-download":
            report["office_review_required"] = True
            report["manual_checks"] = [message]
        return report

    monkeypatch.setattr(workflow_api, "require_docx_output_pass", check_legacy_archive)


def _login(username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _create_people(admin_headers: dict[str, str]) -> tuple[dict[str, str], dict[str, str], str, str]:
    suffix = uuid.uuid4().hex[:8]
    teacher_name = f"workflow_teacher_{suffix}"
    teacher_password = _password("Teacher")
    teacher = register_fixture_response(client, teacher_name, teacher_password, role="teacher", display_name="流程教师")
    teacher_id = teacher.json()["id"]
    approved = client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=admin_headers,
                          json={"role": "teacher", "status": "active"})
    assert approved.status_code == 200, approved.text

    student_name = f"workflow_student_{suffix}"
    student_password = _password("Student")
    student = register_fixture_response(client, student_name, student_password, display_name="流程学生")
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


@pytest.fixture
def ready_submission_candidate(request, monkeypatch):
    if hasattr(request, "param"):
        stage = request.param
        if stage == "m1-pdf-disabled":
            monkeypatch.setenv("PAPERGUARD_WORD_PDF_ENABLED", "false")
            from app.services import word_optional_pdf

            def reject_pdf_conversion(*_args, **_kwargs):
                pytest.fail("Word submission and archive must not invoke the optional PDF converter")

            monkeypatch.setattr(word_optional_pdf, "create_optional_pdf", reject_pdf_conversion)
            stage = "m1"
        monkeypatch.setenv("PAPERGUARD_DELIVERY_STAGE", stage)
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"submission-boundary-school-{suffix}", "name": "提交边界测试学校", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    scope = client.put(f"/api/organizations/users/{teacher_id}/context", headers=admin, json={
        "organization_context": {"school_id": organization.json()["id"]},
    })
    assert scope.status_code == 200, scope.text
    cohort = client.post("/api/cohorts", headers=teacher, json={
        "name": "提交边界测试批次", "organization_id": organization.json()["id"],
    })
    assert cohort.status_code == 201, cohort.text
    cohort_id = cohort.json()["id"]
    assert client.post(f"/api/cohorts/{cohort_id}/members", headers=teacher, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort_id}/stages", headers=teacher, json={
        "key": "final", "name": "终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
    })
    assert stage.status_code == 201, stage.text
    assert client.post(f"/api/stages/{stage.json()['id']}/publish", headers=teacher).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    return {"admin": admin, "teacher": teacher, "student": student,
            "teacher_id": teacher_id, "student_id": student_id,
            "cohort_id": cohort_id, "participant_id": participant["id"],
            "candidate_id": candidate.json()["id"], "now": now}


def test_comment_text_range_is_bound_to_the_submitted_snapshot(ready_submission_candidate):
    fixture = ready_submission_candidate
    student, teacher = fixture["student"], fixture["teacher"]
    candidate = Store().get_workflow_record("candidate", fixture["candidate_id"])
    project_id = candidate["project_id"]
    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    section = next(item for item in project["sections"] if item["key"] == "ch1")
    section["paragraphs"][0]["text"] = "结果😀结果\n实验"
    saved = client.put(f"/api/projects/{project_id}/section/ch1", headers=student, json={
        "base_revision": project["revision"], "paragraphs": section["paragraphs"],
    })
    assert saved.status_code == 200, saved.text
    latest = client.post("/api/submission-candidates", headers=student, json={"participant_id": fixture["participant_id"]})
    assert latest.status_code == 201 and latest.json()["status"] == "ready", latest.text
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": latest.json()["id"], "idempotency_key": f"text-range-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    endpoint = f"/api/submissions/{submission_id}/comments"
    base = {"body": "请核实", "block_id": "ch1-p1"}

    for fields in (
        {"quoted_text": "结果", "text_start": 3},
        {"quoted_text": "结果", "text_end": 5},
        {"quoted_text": "", "text_start": 3, "text_end": 5},
        {"quoted_text": "结果", "text_start": 3, "text_end": 3},
        {"quoted_text": "结果", "text_start": 3, "text_end": 99},
        {"quoted_text": "结果", "text_start": 2, "text_end": 4},
        {"quoted_text": "实验", "text_start": 3, "text_end": 5},
        {"block_id": "", "quoted_text": "结果", "text_start": 3, "text_end": 5},
    ):
        response = client.post(endpoint, headers=teacher, json={**base, **fields})
        assert response.status_code == 422, (fields, response.text)

    for quote, start, end in (("结果", 3, 5), ("😀", 2, 3)):
        response = client.post(endpoint, headers=teacher, json={
            **base, "quoted_text": quote, "text_start": start, "text_end": end,
        })
        assert response.status_code == 201, response.text
        assert (response.json()["quoted_text"], response.json()["text_start"], response.json()["text_end"]) == (quote, start, end)
    legacy = client.post(endpoint, headers=teacher, json={**base, "quoted_text": "结果"})
    assert legacy.status_code == 201, legacy.text
    detail = client.get(f"/api/submissions/{submission_id}", headers=student)
    assert detail.status_code == 200, detail.text
    assert any(item.get("quoted_text") == "结果" and item.get("text_start") == 3
               and item.get("text_end") == 5 for item in detail.json()["comments"])


@pytest.mark.parametrize("ready_submission_candidate", ["legacy", "m1", "m1-pdf-disabled"], indirect=True)
def test_student_teacher_revision_and_archive_closed_loop(ready_submission_candidate):
    fixture = ready_submission_candidate
    student, teacher = fixture["student"], fixture["teacher"]
    first = client.post("/api/submissions", headers=student, json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": "closed-loop-first",
    })
    assert first.status_code == 200, first.text
    first_id = first.json()["submission"]["id"]
    project_id = first.json()["participant"]["project_id"]
    first_detail = client.get(f"/api/submissions/{first_id}", headers=teacher)
    assert first_detail.status_code == 200, first_detail.text
    first_version = first_detail.json()["version"]
    original_section = next(section for section in first_version["content_snapshot"]["sections"] if section["key"] == "ch1")
    original_text = original_section["paragraphs"][0]["text"]

    comment = client.post(f"/api/submissions/{first_id}/comments", headers=teacher, json={
        "body": "补充实验结果", "severity": "blocking", "block_id": "ch1-p1",
    })
    assert comment.status_code == 201, comment.text
    comment_id = comment.json()["id"]
    returned = client.post(f"/api/submissions/{first_id}/reviews", headers=teacher, json={
        "decision": "returned", "reason": "请补充实验结果",
    })
    assert returned.status_code == 200 and returned.json()["participant"]["state"] == "needs_revision", returned.text
    student_feedback = client.get(f"/api/submissions/{first_id}", headers=student)
    assert student_feedback.json()["review"]["reason"] == "请补充实验结果"

    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    section = next(section for section in project["sections"] if section["key"] == "ch1")
    paragraphs = section["paragraphs"]
    paragraphs[0]["text"] = original_text + " 已补充实验结果。"
    saved = client.put(f"/api/projects/{project_id}/section/ch1", headers=student, json={
        "base_revision": project["revision"], "paragraphs": paragraphs,
    })
    assert saved.status_code == 200, saved.text
    reply = client.post(f"/api/comments/{comment_id}/response", headers=student, json={"body": "已补充实验结果"})
    assert reply.status_code == 201, reply.text
    second_candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": fixture["participant_id"]})
    assert second_candidate.status_code == 201 and second_candidate.json()["status"] == "ready", second_candidate.text
    second = client.post("/api/submissions", headers=student, json={
        "candidate_id": second_candidate.json()["id"], "idempotency_key": "closed-loop-second",
    })
    assert second.status_code == 200, second.text
    second_id = second.json()["submission"]["id"]
    second_detail = client.get(f"/api/submissions/{second_id}", headers=teacher)
    assert second_detail.status_code == 200, second_detail.text
    new_version = second_detail.json()["version"]
    new_section = next(section for section in new_version["content_snapshot"]["sections"] if section["key"] == "ch1")
    assert new_section["paragraphs"][0]["text"] == original_text + " 已补充实验结果。"
    assert new_version["content_hash"] != first_version["content_hash"]
    assert second_detail.json()["prior_unresolved_comments"][0]["responses"][0]["body"] == "已补充实验结果"
    original_again = client.get(f"/api/submissions/{first_id}", headers=teacher).json()
    assert next(section for section in original_again["version"]["content_snapshot"]["sections"] if section["key"] == "ch1")["paragraphs"][0]["text"] == original_text

    resolved = client.post(f"/api/comments/{comment_id}/status", headers=teacher, json={"status": "resolved"})
    assert resolved.status_code == 200, resolved.text
    approved = client.post(f"/api/submissions/{second_id}/reviews", headers=teacher, json={"decision": "approved"})
    assert approved.status_code == 200 and approved.json()["participant"]["state"] == "pending_archive", approved.text
    archive_id = approved.json()["archive"]["id"]
    run_once()
    download = client.get(f"/api/archives/{archive_id}/download", headers=student)
    assert download.status_code == 200, download.text
    with zipfile.ZipFile(io.BytesIO(download.content)) as bundle:
        assert not any(name.lower().endswith('.pdf') for name in bundle.namelist())
        document = bundle.read("final.docx")
    assert hashlib.sha256(document).hexdigest() == second_candidate.json()["artifact"]["docx_sha256"]
    with zipfile.ZipFile(io.BytesIO(document)) as docx:
        assert "已补充实验结果" in docx.read("word/document.xml").decode("utf-8")


def test_pending_teacher_cannot_read_submission_or_archive(ready_submission_candidate):
    """A pending teacher session cannot retain read access to workflow artifacts."""
    from app.auth import _issue_session

    fixture = ready_submission_candidate
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": f"pending-reader-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]

    # The active teacher can read and download the frozen submission before the
    # account status changes.
    assert client.get(f"/api/submissions/{submission_id}", headers=fixture["teacher"]).status_code == 200
    assert client.get(f"/api/submissions/{submission_id}/document", headers=fixture["teacher"]).status_code == 200
    approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"], json={
        "decision": "approved",
    })
    assert approved.status_code == 200, approved.text
    archive_id = approved.json()["archive"]["id"]
    run_once()
    assert client.get(f"/api/archives/{archive_id}", headers=fixture["teacher"]).status_code == 200
    assert client.get(f"/api/archives/{archive_id}/download", headers=fixture["teacher"]).status_code == 200

    store = Store()
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    teacher_id = participant["teacher_id"]
    changed = client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=fixture["admin"], json={
        "role": "teacher", "status": "pending",
    })
    assert changed.status_code == 200, changed.text

    # The status change revokes the old session. Issue a fresh pending session
    # to exercise route-level read predicates rather than authentication.
    pending_user = store.get_user(teacher_id)
    pending_session = _issue_session(store, pending_user)
    pending = {"Authorization": f"Bearer {pending_session['token']}"}
    assert client.get("/api/submissions", headers=pending).json() == []
    assert client.get(f"/api/submissions/{submission_id}", headers=pending).status_code == 404
    assert client.get(f"/api/submissions/{submission_id}/document", headers=pending).status_code == 404
    assert client.get(f"/api/archives/{archive_id}", headers=pending).status_code == 404
    assert client.get(f"/api/archives/{archive_id}/download", headers=pending).status_code == 404


@pytest.mark.parametrize(("requirement_type", "enforce", "allowed"), [
    ("manual", "hard", False),
    ("doc", "hard", False),
    ("manual", "soft", True),
])
def test_submission_requires_hard_rules_to_be_checked(
    ready_submission_candidate, monkeypatch, requirement_type, enforce, allowed,
):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    original_check = workflow_api.check_project

    def unchecked_rule(*args, **kwargs):
        report = original_check(*args, **kwargs)
        report["not_checked"].append({
            "id": "unchecked-rule", "description": "待核验规则",
            "type": requirement_type, "enforce": enforce,
        })
        return report

    monkeypatch.setattr(workflow_api, "check_project", unchecked_rule)
    response = client.post("/api/submission-candidates", headers=fixture["student"], json={
        "participant_id": fixture["participant_id"],
    })
    assert response.status_code == 201, response.text
    candidate = response.json()
    assert candidate["status"] == ("ready" if allowed else "blocked")
    if not allowed:
        assert any(item["key"] == "automatic_unverified" for item in candidate["gates"])
        # A stale candidate cannot bypass the gate by retaining an old ready state.
        stored = Store().get_workflow_record("candidate", candidate["id"])
        stored["status"] = "ready"
        stored["gates"] = [item for item in stored["gates"] if item["key"] != "automatic_unverified"]
        Store().save_workflow_record("candidate", stored, expected_revision=stored["revision"])
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": candidate["id"], "idempotency_key": f"unchecked-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == (200 if allowed else 409), submitted.text
    if not allowed:
        assert any(item["key"] == "automatic_unverified" for item in submitted.json()["detail"]["gates"])


@pytest.mark.parametrize("already_archived", [False, True])
@pytest.mark.parametrize("rule_source", ["item", "legacy_text", "legacy_attachment", "body_attachment", "compound_format", "legacy_auto_compound", "legacy_manual_duplicate"])
def test_historical_workflow_artifact_rechecks_hard_rules_after_checker_update(
    ready_submission_candidate, monkeypatch, tmp_path, already_archived, rule_source,
):
    from app.services import project_check, requirement_check
    from app.models.project import ImageBlock, ParagraphBlock

    fixture = ready_submission_candidate
    store = Store()
    previous = store.get_workflow_record("candidate", fixture["candidate_id"])
    project = store.get_project(previous["project_id"])
    rule = {
        "id": "image-font", "type": "manual", "target": "document", "checker": "",
        "params": {"format_override": True}, "severity": "error", "enforce": "hard",
        "description": "图中标注符号和文字的字号不大于图题字号，建议五号",
    }
    if rule_source in {"legacy_text", "legacy_attachment", "body_attachment"}:
        if rule_source != "body_attachment":
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
            )
            uploaded = client.post(f"/api/projects/{project.id}/images", headers=fixture["student"], files={
                "file": ("figure.png", png, "image/png"),
            })
            assert uploaded.status_code == 201, uploaded.text
            section = next(section for section in project.sections if section.key == "ch1")
            section.paragraphs.append(ParagraphBlock(id="legacy-image", type="image",
                image=ImageBlock(url=uploaded.json()["url"], caption="系统结构")))
        project.requirement_snapshot = {
            "policy_sets": [{"id": "legacy", "version": "1", "name": "学校规范", "items": [],
                             "markdown_content": "图中标注符号文字字号不大于图题的字号，建议五号字体。"}],
            "resolved_items": [], "rule_pack_selection_version": 1,
        }
        if rule_source in {"legacy_attachment", "body_attachment"}:
            import hashlib
            from app.api import policies

            monkeypatch.setattr(policies, "_DOCUMENT_DIR", tmp_path / "documents")
            directory = policies._DOCUMENT_DIR / "legacy"
            directory.mkdir(parents=True)
            source_text = project.requirement_snapshot["policy_sets"][0].pop("markdown_content")
            data = ("正文必须使用黑体。" if rule_source == "body_attachment" else source_text).encode()
            (directory / "guide.txt").write_bytes(data)
            project.requirement_snapshot["policy_sets"][0]["documents"] = [{
                "id": "guide", "storage_key": "guide.txt", "filename": "guide.txt",
                "checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
            }]
    elif rule_source == "legacy_auto_compound":
        plain = {"id": "plain", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
                 "params": {"min": 1}, "severity": "error", "enforce": "hard", "description": "摘要不少于1字"}
        hidden = {**plain, "id": "hidden", "description": "摘要不少于1字，正文使用黑体"}
        project.requirement_snapshot = {
            "policy_sets": [{"items": [plain, hidden]}], "resolved_items": [plain],
            "format_item_ids": ["hidden"], "rule_pack_selection_version": 1,
        }
    elif rule_source == "legacy_manual_duplicate":
        font = {"id": "format", "type": "manual", "target": "body", "checker": "",
                "params": {"format_override": True}, "severity": "error", "enforce": "hard",
                "description": "正文中文字体为黑体"}
        border = {**font, "target": "table", "description": "表格边框必须为黑色"}
        project.requirement_snapshot = {
            "policy_sets": [{"name": "学校", "items": [font]}, {"name": "专业", "items": [border]}],
            "resolved_items": [font], "format_item_ids": ["format"], "rule_pack_selection_version": 1,
        }
    else:
        if rule_source == "compound_format":
            rule = {**rule, "id": "table-border", "params": {},
                    "description": "表格边框必须为黑色，字号遵守规范"}
        project.requirement_snapshot = {
            "policy_sets": [{"items": [rule]}], "resolved_items": [rule],
            "format_item_ids": [rule["id"]], "rule_pack_selection_version": 1,
        }
    store.save_project(project)
    acknowledged = client.post(f"/api/stage-participants/{fixture['participant_id']}/acknowledge",
                               headers=fixture["student"], json={})
    assert acknowledged.status_code == 200, acknowledged.text
    current_requirements = project_check.check_requirements
    with monkeypatch.context() as old_checker:
        old_checker.setattr(project_check, "is_image_internal_typography", lambda _item: False)
        old_checker.setattr(requirement_check, "is_image_internal_typography", lambda _item: False)
        old_checker.setattr(requirement_check, "_verified_simple_body_font", lambda *_args: True)
        old_checker.setattr(requirement_check, "extract_image_font_size_limit", lambda _text: "")
        if rule_source == "legacy_auto_compound":
            old_checker.setattr(requirement_check, "is_format_requirement", lambda _text: False)
        if rule_source == "legacy_manual_duplicate":
            def old_requirements(*args, **kwargs):
                findings, unchecked = current_requirements(*args, **kwargs)
                return findings, [item for item in unchecked if not item["id"].startswith("merged-format-")]
            old_checker.setattr(project_check, "check_requirements", old_requirements)
        if rule_source == "body_attachment":
            old_checker.setattr(requirement_check, "_frozen_attachment_text", lambda *_args: "")
        candidate = client.post("/api/submission-candidates", headers=fixture["student"], json={
            "participant_id": fixture["participant_id"],
        })
        assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
        candidate_id = candidate.json()["id"]
        if already_archived:
            submission = client.post("/api/submissions", headers=fixture["student"], json={
                "candidate_id": candidate_id, "idempotency_key": f"old-checker-{uuid.uuid4().hex}",
            })
            assert submission.status_code == 200, submission.text
            submission_id = submission.json()["submission"]["id"]
            approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"],
                                   json={"decision": "approved"})
            assert approved.status_code == 200, approved.text
            archive_id = approved.json()["archive"]["id"]
            run_once()
            assert client.get(f"/api/submissions/{submission_id}/document", headers=fixture["teacher"]).status_code == 200
            assert client.get(f"/api/archives/{archive_id}/download", headers=fixture["student"]).status_code == 200

    if already_archived:
        document = client.get(f"/api/submissions/{submission_id}/document", headers=fixture["teacher"])
        archive = client.get(f"/api/archives/{archive_id}/download", headers=fixture["student"])
        assert (document.status_code, archive.status_code) == (409, 409)
        record = store.get_workflow_record("archive", archive_id)
        assert record["status"] == "succeeded"
        archive_view = client.get(f"/api/archives/{archive_id}", headers=fixture["student"]).json()
        assert archive_view["requirement_recheck"] == "unverifiable"
        assert "download_url" not in archive_view
        archives = client.get("/api/archives", headers=fixture["student"]).json()
        assert "download_url" not in next(item for item in archives if item["id"] == archive_id)
        job = client.get(f"/api/jobs/{record['job_id']}", headers=fixture["student"]).json()
        assert "download_url" not in job["archive"]
    else:
        candidate_view = client.get(f"/api/submission-candidates/{candidate_id}", headers=fixture["student"])
        assert candidate_view.status_code == 200 and candidate_view.json()["status"] == "blocked"
        assert any(gate["key"] == "automatic_unverified" and gate["blocking"]
                   for gate in candidate_view.json()["gates"])
        response = client.post("/api/submissions", headers=fixture["student"], json={
            "candidate_id": candidate_id, "idempotency_key": f"new-checker-{uuid.uuid4().hex}",
        })
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "SUBMISSION_ARTIFACT_INVALID"


def test_candidate_artifact_failure_logs_location_without_document_data(
    ready_submission_candidate, monkeypatch, caplog,
):
    from app.api import workflow as workflow_api

    sensitive_content = "论文正文和文件路径不得出现在日志"

    def fail_to_freeze(*_args, **_kwargs):
        try:
            raise OSError(sensitive_content)
        except OSError as cause:
            raise ValueError(sensitive_content) from cause

    monkeypatch.setattr(workflow_api, "_freeze_candidate_artifact", fail_to_freeze)
    with caplog.at_level("WARNING", logger="paperguard.workflow"):
        response = client.post("/api/submission-candidates", headers=ready_submission_candidate["student"], json={
            "participant_id": ready_submission_candidate["participant_id"],
        })

    assert response.status_code == 201, response.text
    candidate = response.json()
    assert candidate["status"] == "blocked"
    assert "artifact" not in candidate
    gate = next(item for item in candidate["gates"] if item["key"] == "output_artifact")
    assert gate["blocking"] and not gate["passed"]
    assert candidate["id"] in caplog.text
    assert "ValueError@fail_to_freeze:" in caplog.text
    assert "OSError@fail_to_freeze:" in caplog.text
    assert sensitive_content not in caplog.text
    assert sensitive_content not in response.text


@pytest.mark.parametrize("ready_submission_candidate", ["legacy", "m1"], indirect=True)
@pytest.mark.parametrize("archive_check", ["candidate", "archive"])
def test_submission_and_archive_keep_manual_review_as_non_blocking_todo(
    ready_submission_candidate, monkeypatch, archive_check,
):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    check_output = workflow_api.require_docx_output_pass
    def pending_output(*args, **kwargs):
        return {**check_output(*args, **kwargs), "office_review_required": True}

    with monkeypatch.context() as patch:
        patch.setattr(workflow_api, "require_docx_output_pass", pending_output)
        candidate_response = client.post("/api/submission-candidates", headers=fixture["student"], json={
            "participant_id": fixture["participant_id"],
        })
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()
    assert candidate["status"] == "ready"
    assert next(item for item in candidate["gates"] if item["key"] == "output_artifact")["blocking"] is False
    office_todo = next(item for item in candidate["gates"] if item["key"] == "office_review_todo")
    assert office_todo["blocking"] is False
    assert candidate["manual_review"]["status"] == "required"
    candidate_id = candidate["id"]

    submit_request = {"candidate_id": candidate_id, "idempotency_key": f"automatic-check-required-{archive_check}"}
    with monkeypatch.context() as patch:
        patch.setattr(workflow_api, "require_docx_output_pass", pending_output)
        submitted = client.post("/api/submissions", headers=fixture["student"], json=submit_request)
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]

    with monkeypatch.context() as patch:
        patch.setattr(workflow_api, "require_docx_output_pass", pending_output)
        approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"],
                               json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    archive_id = approved.json()["archive"]["id"]

    with monkeypatch.context() as patch:
        if archive_check == "candidate":
            patch.setattr(workflow_api, "require_docx_output_pass", pending_output)
        else:
            def pending_archive_output(*args, **kwargs):
                report = check_output(*args, **kwargs)
                report["office_review_required"] = True
                return report

            patch.setattr(workflow_api, "require_docx_output_pass", pending_archive_output)
        worker_result = run_once()

    assert approved.json()["archive"]["job_id"] in worker_result["archives"]["succeeded"], worker_result
    archive = client.get(f"/api/archives/{archive_id}", headers=fixture["student"])
    assert archive.status_code == 200, archive.text
    archive_payload = archive.json()
    assert archive_payload["status"] == "succeeded"
    assert archive_payload["manual_review"]["status"] == "required"
    assert archive_payload["download_url"].endswith(f"/api/archives/{archive_id}/download")
    with monkeypatch.context() as patch:
        if archive_check == "candidate":
            patch.setattr(workflow_api, "require_docx_output_pass", pending_output)
        else:
            patch.setattr(workflow_api, "require_docx_output_pass", pending_archive_output)
        assert client.get(f"/api/archives/{archive_id}/download", headers=fixture["student"]).status_code == 200
    assert Store().get_workflow_record("participant", fixture["participant_id"])["state"] == "archived"


def test_submitted_review_cannot_switch_stage_configuration(ready_submission_candidate):
    fixture = ready_submission_candidate
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": f"frozen-stage-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    participant_id = fixture["participant_id"]
    stage_id = submitted.json()["participant"]["stage_id"]
    original_config_id = submitted.json()["submission"]["stage_config_id"]
    draft = client.post(f"/api/stages/{stage_id}/versions", headers=fixture["admin"], json={
        "is_final": False, "reason": "后续阶段配置调整",
    })
    assert draft.status_code == 201, draft.text
    new_config_id = draft.json()["new_config"]["id"]

    for expected_state in ("submitted", "pending_archive", "archived"):
        current = client.get(f"/api/stage-participants/{participant_id}", headers=fixture["student"])
        assert current.json()["participant"]["state"] == expected_state
        result = client.post(f"/api/stages/{stage_id}/versions/{new_config_id}/publish", headers=fixture["admin"], json={
            "participant_ids": [participant_id], "reason": "尝试切换审核中的阶段配置",
        })
        assert result.status_code == 409, result.text
        detail = client.get(f"/api/submissions/{submission_id}", headers=fixture["teacher"])
        assert detail.json()["config"]["id"] == original_config_id
        if expected_state == "submitted":
            decision = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"], json={
                "decision": "approved",
            })
            assert decision.status_code == 200, decision.text
            assert decision.json()["archive"] is not None
        elif expected_state == "pending_archive":
            run_once()


def test_submission_history_retains_its_frozen_stage_configuration(ready_submission_candidate):
    fixture = ready_submission_candidate
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": f"history-stage-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission = submitted.json()["submission"]
    participant_id = fixture["participant_id"]
    stage_id = submitted.json()["participant"]["stage_id"]
    assert client.post(f"/api/submissions/{submission['id']}/withdraw", headers=fixture["student"]).status_code == 200
    draft = client.post(f"/api/stages/{stage_id}/versions", headers=fixture["admin"], json={
        "revision_days": 21, "reason": "调整后续修订要求",
    })
    assert draft.status_code == 201, draft.text
    new_config_id = draft.json()["new_config"]["id"]
    published = client.post(f"/api/stages/{stage_id}/versions/{new_config_id}/publish", headers=fixture["admin"], json={
        "reason": "发布后续阶段配置",
    })
    assert published.status_code == 200, published.text
    store = Store()
    participant = store.get_workflow_record("participant", participant_id)
    participant["config_version_id"] = new_config_id
    assert store.save_workflow_record("participant", participant, expected_revision=participant["revision"])
    current = client.get(f"/api/stage-participants/{participant_id}", headers=fixture["student"])
    assert current.json()["config"]["id"] == new_config_id
    history = client.get(f"/api/submissions/{submission['id']}", headers=fixture["teacher"])
    assert history.status_code == 200, history.text
    assert history.json()["config"] == submission["stage_config_snapshot"]


@pytest.mark.parametrize("field,value", [
    ("id", "stage-config-other"), ("stage_id", "stage-other"), ("revision_days", 99),
])
def test_review_rejects_invalid_frozen_stage_configuration(ready_submission_candidate, field, value):
    fixture = ready_submission_candidate
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": f"invalid-stage-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    store = Store()
    record = store.get_workflow_record("submission", submission_id)
    record["stage_config_snapshot"][field] = value
    assert store.save_workflow_record("submission", record, expected_revision=record["revision"])

    detail = client.get(f"/api/submissions/{submission_id}", headers=fixture["teacher"])
    assert detail.status_code == 409 and detail.json()["detail"]["code"] == "SUBMISSION_CONFIG_INVALID"
    decision = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"], json={
        "decision": "approved",
    })
    assert decision.status_code == 409 and decision.json()["detail"]["code"] == "SUBMISSION_CONFIG_INVALID"
    assert store.get_workflow_record("participant", fixture["participant_id"])["state"] == "submitted"


def test_stage_configuration_switch_loses_to_concurrent_submission(ready_submission_candidate, monkeypatch):
    fixture = ready_submission_candidate
    store = Store()
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    stage_id = participant["stage_id"]
    original_config_id = participant["config_version_id"]
    draft = client.post(f"/api/stages/{stage_id}/versions", headers=fixture["admin"], json={
        "is_final": False, "reason": "调整后续阶段配置",
    })
    assert draft.status_code == 201, draft.text
    new_config_id = draft.json()["new_config"]["id"]
    waiting, proceed = Event(), Event()
    original_transaction = Store.workflow_transaction

    @contextmanager
    def delay_first_transaction(current_store):
        if not waiting.is_set():
            waiting.set()
            assert proceed.wait(timeout=10)
        with original_transaction(current_store) as conn:
            yield conn

    with monkeypatch.context() as patch:
        patch.setattr(Store, "workflow_transaction", delay_first_transaction)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, f"/api/stages/{stage_id}/versions/{new_config_id}/publish",
                                      headers=fixture["admin"], json={
                                          "participant_ids": [participant["id"]], "reason": "更新可编辑任务配置",
                                      })
            try:
                assert waiting.wait(timeout=10)
                submitted = client.post("/api/submissions", headers=fixture["student"], json={
                    "candidate_id": fixture["candidate_id"], "idempotency_key": f"stage-race-{uuid.uuid4().hex}",
                })
            finally:
                proceed.set()
            switched = pending.result(timeout=20)
    assert submitted.status_code == 200, submitted.text
    assert switched.status_code == 409, switched.text
    current = store.get_workflow_record("participant", participant["id"])
    assert current["state"] == "submitted" and current["config_version_id"] == original_config_id
    assert store.get_workflow_record("stage", stage_id)["config_version_id"] == original_config_id
    assert store.get_workflow_record("stage_config", new_config_id)["status"] == "draft"


def test_submitted_word_download_is_bound_to_the_submission_and_guidance(ready_submission_candidate, monkeypatch):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    student, teacher = fixture["student"], fixture["teacher"]
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": "submitted-document-download",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    project_id = submitted.json()["participant"]["project_id"]
    path = f"/api/submissions/{submission_id}/document"
    candidate = Store().get_workflow_record("candidate", fixture["candidate_id"])
    artifact = workflow_api._candidate_artifact_path(candidate["id"])
    original = artifact.read_bytes()
    for headers in (student, teacher):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert response.content == original
        assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert response.headers["cache-control"] == "private, no-store"
        assert hashlib.sha256(response.content).hexdigest() == candidate["artifact"]["docx_sha256"]

    other_teacher, other_student, _, _ = _create_people(fixture["admin"])
    for headers in (other_teacher, other_student):
        assert client.get(path, headers=headers).status_code in (403, 404)
    assert client.get(path).status_code == 401

    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    section = next(item for item in project["sections"] if item["key"] == "ch1")
    paragraphs = section["paragraphs"]
    paragraphs[0]["text"] += " 修改后的草稿。"
    saved = client.put(f"/api/projects/{project_id}/section/ch1", headers=student, json={
        "base_revision": project["revision"], "paragraphs": paragraphs,
    })
    assert saved.status_code == 200, saved.text
    assert client.get(path, headers=teacher).content == original

    try:
        artifact.write_bytes(b"altered submission")
        unavailable = client.get(path, headers=teacher)
        assert unavailable.status_code == 409
        assert unavailable.json()["detail"]["code"] == "SUBMISSION_ARTIFACT_INVALID"
    finally:
        artifact.write_bytes(original)

    verify = workflow_api._verified_candidate_artifact

    def require_office_review(*args, **kwargs):
        data, report = verify(*args, **kwargs)
        return data, {**report, "office_review_required": True, "manual_checks": ["页面尚未核验"]}

    from app.services.manual_output_review import confirm_review, review_checklist
    version = Store().get_workflow_record("project_version", submitted.json()["submission"]["project_version_id"])
    _, pending_report = require_office_review(Store(), candidate, version)
    candidate["manual_review"] = confirm_review(
        pending_report, output_sha256=pending_report["output_sha256"],
        confirmed_check_ids=[item["id"] for item in review_checklist(pending_report)], reviewer_id="legacy-reviewer",
    )
    assert Store().save_workflow_record("candidate", candidate, expected_revision=candidate["revision"])
    monkeypatch.setattr(workflow_api, "_verified_candidate_artifact", require_office_review)
    for headers in (student, teacher):
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.content == original



def test_previous_candidate_report_is_rechecked_for_submission_download_and_new_archive(
        ready_submission_candidate, monkeypatch):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    store = Store()
    candidate = store.get_workflow_record("candidate", fixture["candidate_id"])
    candidate["artifact"]["output_check"]["inspector_version"] = "docx-output-check/5"
    candidate = store.save_workflow_record("candidate", candidate, expected_revision=candidate["revision"])
    assert candidate
    original = workflow_api._candidate_artifact_path(candidate["id"]).read_bytes()
    calls = []
    check = workflow_api.require_docx_output_pass

    def current_check(*args, **kwargs):
        calls.append(kwargs.get("context_kind"))
        return check(*args, **kwargs)

    monkeypatch.setattr(workflow_api, "require_docx_output_pass", current_check)
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": candidate["id"], "idempotency_key": "previous-inspector-candidate",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    document_url = f"/api/submissions/{submission_id}/document"
    downloaded = client.get(document_url, headers=fixture["teacher"])
    assert downloaded.status_code == 200 and downloaded.content == original
    assert calls.count("workflow-candidate") >= 2

    approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"],
                           json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    archive_id = approved.json()["archive"]["id"]
    run_once()
    archive_url = f"/api/archives/{archive_id}/download"
    archived = client.get(archive_url, headers=fixture["student"])
    assert archived.status_code == 200, archived.text
    with zipfile.ZipFile(io.BytesIO(archived.content)) as bundle:
        assert bundle.read("final.docx") == original
        manifest = json.loads(bundle.read("manifest.json"))
    assert manifest["output_check"]["inspector_version"] == "docx-output-check/9"
    assert store.get_workflow_record("candidate", candidate["id"])["artifact"]["output_check"]["inspector_version"] == "docx-output-check/5"
    assert "workflow-archive-download" in calls

    candidate = store.get_workflow_record("candidate", candidate["id"])
    candidate["artifact"]["output_check"]["inspector_version"] = "docx-output-check/4"
    assert store.save_workflow_record("candidate", candidate, expected_revision=candidate["revision"])
    assert client.get(document_url, headers=fixture["teacher"]).status_code == 409
    assert client.get(archive_url, headers=fixture["student"]).status_code == 409
    candidate = store.get_workflow_record("candidate", candidate["id"])
    candidate["artifact"]["output_check"]["inspector_version"] = "docx-output-check/5"
    assert store.save_workflow_record("candidate", candidate, expected_revision=candidate["revision"])

    def reject_current_check(*_args, **_kwargs):
        raise DocxOutputCheckError({"status": "fail", "checks": []})

    monkeypatch.setattr(workflow_api, "require_docx_output_pass", reject_current_check)
    assert client.get(document_url, headers=fixture["teacher"]).status_code == 409
    assert client.get(archive_url, headers=fixture["student"]).status_code == 409

def test_optional_submission_pdf_requires_the_checked_submission_and_never_blocks_word(ready_submission_candidate, monkeypatch):
    from app.api import workflow as workflow_api
    from app.services import word_optional_pdf

    fixture = ready_submission_candidate
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": "optional-submission-pdf",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    path = f"/api/submissions/{submission_id}/document"
    artifact = workflow_api._candidate_artifact_path(fixture["candidate_id"])
    original = artifact.read_bytes()
    pdf = b"%PDF-1.4 test conversion result"
    used = []

    def convert(data):
        used.append(data)
        return pdf, {"source_sha256": hashlib.sha256(data).hexdigest(), "pdf_sha256": hashlib.sha256(pdf).hexdigest()}

    monkeypatch.setattr(word_optional_pdf, "word_pdf_available", lambda: True)
    monkeypatch.setattr(word_optional_pdf, "create_optional_pdf", convert)
    assert client.get(path, headers=fixture["student"]).content == original
    assert not used
    for headers in (fixture["student"], fixture["teacher"]):
        response = client.get(path + "?format=pdf", headers=headers)
        assert response.status_code == 200, response.text
        assert response.content == pdf and response.headers["content-type"] == "application/pdf"
        assert response.headers["x-paperguard-source-sha256"] == hashlib.sha256(original).hexdigest()
        assert response.headers["x-paperguard-pdf-sha256"] == hashlib.sha256(pdf).hexdigest()
    assert used == [original, original]
    other_teacher, other_student, _, _ = _create_people(fixture["admin"])
    for headers in (other_teacher, other_student):
        assert client.get(path + "?format=pdf", headers=headers).status_code in (403, 404)
    assert client.get(path + "?format=pdf").status_code == 401

    def fail(_data):
        raise word_optional_pdf.WordPdfError("conversion failed")

    monkeypatch.setattr(word_optional_pdf, "create_optional_pdf", fail)
    assert client.get(path + "?format=pdf", headers=fixture["student"]).status_code == 503
    assert client.get(path, headers=fixture["student"]).content == original

    def mutate(data):
        artifact.write_bytes(b"changed during conversion")
        return convert(data)

    monkeypatch.setattr(word_optional_pdf, "create_optional_pdf", mutate)
    try:
        assert client.get(path + "?format=pdf", headers=fixture["student"]).status_code == 409
    finally:
        artifact.write_bytes(original)
    assert client.get(path, headers=fixture["student"]).content == original


def test_content_advisory_follows_submission_and_does_not_block_review(ready_submission_candidate, monkeypatch):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    original_check = workflow_api.check_project
    advisory = {
        "rule_id": "recent-references", "checker": "references.recent_language_summary",
        "message": "近三年文献数量待教师审核", "severity": "info", "enforce": "soft",
    }

    def check_with_advisory(*args, **kwargs):
        report = original_check(*args, **kwargs)
        report.setdefault("quality_advisories", []).append(advisory)
        return report

    monkeypatch.setattr(workflow_api, "check_project", check_with_advisory)
    candidate = client.post("/api/submission-candidates", headers=fixture["student"], json={
        "participant_id": fixture["participant_id"],
    })
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    assert advisory in candidate.json()["check_report"]["quality_advisories"]

    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"quality-advisory-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]

    rechecked = []

    def check_without_frozen_advisory(*args, **kwargs):
        rechecked.append(True)
        return original_check(*args, **kwargs)

    monkeypatch.setattr(workflow_api, "check_project", check_without_frozen_advisory)
    detail = client.get(f"/api/submissions/{submission_id}", headers=fixture["teacher"])
    assert detail.status_code == 200, detail.text
    assert advisory in detail.json()["candidate"]["check_report"]["quality_advisories"]
    approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"], json={
        "decision": "approved",
    })
    assert approved.status_code == 200, approved.text
    assert rechecked, "delivery must recheck hard rules against the frozen project"


def test_required_material_is_bound_to_each_submission(ready_submission_candidate, tmp_path, monkeypatch):
    fixture = ready_submission_candidate
    store = Store()
    monkeypatch.setenv("PAPERGUARD_PROCESS_FILE_DIR", str(tmp_path / "process-files"))
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    stage = store.get_workflow_record("stage", participant["stage_id"])
    config_id = participant.get("config_version_id") or stage["config_version_id"]
    config = store.get_workflow_record("stage_config", config_id)
    config["required_materials"] = ["task_book"]
    assert store.save_workflow_record("stage_config", config, expected_revision=config["revision"])

    record = client.post(f"/api/stage-participants/{participant['id']}/process-records", headers=fixture["student"], json={
        "artifact_type": "task_book", "data": {"title": "原任务书", "requirements": "完成实验"},
    })
    assert record.status_code == 201, record.text
    record_id = record.json()["id"]
    original = b"submitted task material"
    attachment = client.post(f"/api/process-records/{record_id}/attachments", headers=fixture["student"], files={
        "file": ("task.txt", original, "text/plain"),
    })
    assert attachment.status_code == 201, attachment.text
    attachment_id = attachment.json()["id"]
    assert client.post(f"/api/process-records/{record_id}/submit", headers=fixture["student"]).status_code == 200

    stale = client.post("/api/submission-candidates", headers=fixture["student"], json={"participant_id": participant["id"]})
    assert stale.status_code == 201 and stale.json()["status"] == "ready", stale.text
    assert client.post(f"/api/process-records/{record_id}/decisions", headers=fixture["teacher"], json={"decision": "approved"}).status_code == 200
    rejected = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": stale.json()["id"], "idempotency_key": f"stale-material-{uuid.uuid4().hex}",
    })
    assert rejected.status_code == 409, rejected.text

    candidate = client.post("/api/submission-candidates", headers=fixture["student"], json={"participant_id": participant["id"]})
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"frozen-material-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    live = store.get_workflow_record("process_record", record_id)
    Path(live["attachments"][0]["storage_path"]).write_bytes(b"changed after submission")
    live["data"]["title"] = "后来修改"
    assert store.save_workflow_record("process_record", live, expected_revision=live["revision"])

    detail = client.get(f"/api/submissions/{submission_id}", headers=fixture["teacher"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["submitted_materials_available"] is True
    frozen = detail.json()["submitted_materials"]
    assert len(frozen) == 1 and frozen[0]["data"]["title"] == "原任务书"
    assert "storage_path" not in frozen[0]["attachments"][0]
    download_url = frozen[0]["attachments"][0]["download_url"]
    assert client.get(download_url, headers=fixture["teacher"]).content == original
    other_teacher, other_student, _, _ = _create_people(fixture["admin"])
    for user in (other_teacher, other_student):
        assert client.get(download_url, headers=user).status_code in (403, 404)

    from app.api.workflow import _candidate_material_dir
    frozen_file = _candidate_material_dir(candidate.json()["id"]) / attachment_id
    frozen_file.write_bytes(b"tampered frozen material")
    unavailable = client.get(f"/api/submissions/{submission_id}", headers=fixture["teacher"])
    assert unavailable.json()["submitted_materials_available"] is False
    assert client.get(download_url, headers=fixture["teacher"]).status_code == 409
    assert client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"], json={"decision": "approved"}).status_code == 409
    frozen_file.write_bytes(original)

    approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"], json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    archive_id = approved.json()["archive"]["id"]
    run_once()
    archive = client.get(f"/api/archives/{archive_id}/download", headers=fixture["student"])
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        frozen_records = json.loads(bundle.read("process-records.json"))
        assert next(item for item in frozen_records if item["id"] == record_id)["data"]["title"] == "原任务书"
        assert bundle.read(f"materials/{attachment_id}.txt") == original


def _approved_archive_from_candidate(fixture):
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": f"archive-recovery-{uuid.uuid4().hex}",
    })
    assert submitted.status_code == 200, submitted.text
    approved = client.post(f"/api/submissions/{submitted.json()['submission']['id']}/reviews", headers=fixture["teacher"], json={
        "decision": "approved",
    })
    assert approved.status_code == 200, approved.text
    return approved.json()["archive"]


def test_older_student_material_stays_available_through_review_and_archive(
    ready_submission_candidate, tmp_path, monkeypatch,
):
    from app.api.process_records import material_requirement_state
    from app.services.workflow_jobs import consume_archive_jobs

    fixture = ready_submission_candidate
    store = Store()
    suffix = uuid.uuid4().hex[:12]
    record_id = f"process-old-{suffix}"
    attachment_id = f"file-old-{suffix}"
    attachment = tmp_path / f"{attachment_id}.txt"
    contents = b"older student material"
    attachment.write_bytes(contents)
    monkeypatch.setenv("PAPERGUARD_PROCESS_FILE_DIR", str(tmp_path))
    with store.workflow_transaction() as conn:
        store.save_workflow_record_conn(conn, "process_record", {
            "id": record_id, "scope_type": "participant", "scope_id": fixture["participant_id"],
            "artifact_type": "task_book", "state": "submitted", "approval_history": [],
            "attachments": [{
                "id": attachment_id, "name": "task-book.txt", "storage_path": str(attachment),
                "media_type": "text/plain", "sha256": hashlib.sha256(contents).hexdigest(),
            }],
        })
        conn.execute("UPDATE workflow_records SET updated_at = ? WHERE kind = ? AND id = ?",
                     ("2000-01-01T00:00:00Z", "process_record", record_id))
        for index in range(501):
            store.save_workflow_record_conn(conn, "process_record", {
                "id": f"process-noise-{suffix}-{index}", "scope_type": "participant",
                "scope_id": f"other-{suffix}", "state": "draft", "attachments": [],
            })

    assert not any(item["id"] == record_id for item in store.list_workflow_records("process_record"))
    assert material_requirement_state(store, fixture["participant_id"], "task_book")["record_ids"] == [record_id]
    for user in (fixture["student"], fixture["teacher"]):
        records = client.get(f"/api/stage-participants/{fixture['participant_id']}/process-records", headers=user)
        assert records.status_code == 200 and any(item["id"] == record_id for item in records.json())
        downloaded = client.get(f"/api/process-files/{attachment_id}/download", headers=user)
        assert downloaded.status_code == 200 and downloaded.content == contents
    inbox = client.get("/api/process-records/inbox", headers=fixture["teacher"])
    assert inbox.status_code == 200 and any(item["id"] == record_id for item in inbox.json())
    other_teacher, other_student, _, _ = _create_people(fixture["admin"])
    for user in (other_teacher, other_student):
        denied = client.get(f"/api/process-files/{attachment_id}/download", headers=user)
        assert denied.status_code in (403, 404)

    archive = _approved_archive_from_candidate(fixture)
    result = consume_archive_jobs(store, consumer_id=f"material-test-{suffix}", limit=50)
    assert archive["job_id"] in result["succeeded"], result
    downloaded_archive = client.get(f"/api/archives/{archive['id']}/download", headers=fixture["student"])
    assert downloaded_archive.status_code == 200, downloaded_archive.text
    with zipfile.ZipFile(io.BytesIO(downloaded_archive.content)) as bundle:
        records = json.loads(bundle.read("process-records.json"))
        assert any(item["id"] == record_id for item in records)
        assert bundle.read(f"materials/{attachment_id}.txt") == contents


def test_archive_recovery_keeps_active_worker_running(ready_submission_candidate):
    from app.api.workflow import recover_pending_archives
    from app.services.workflow_jobs import consume_archive_jobs

    archive = _approved_archive_from_candidate(ready_submission_candidate)
    store = Store()
    claimed = store.claim_jobs(job_type="archive.generate", consumer_id="active-worker", limit=50)
    assert any(job["id"] == archive["job_id"] for job in claimed)
    record = store.get_workflow_record("archive", archive["id"])
    record["status"] = "running"
    assert store.save_workflow_record("archive", record, expected_revision=record["revision"])

    recover_pending_archives(store)

    assert store.get_workflow_record("archive", archive["id"])["status"] == "running"
    assert store.get_job(archive["job_id"])["state"] == "running"
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", archive["job_id"]))
    assert archive["job_id"] in consume_archive_jobs(store, consumer_id="cleanup-worker", limit=50)["succeeded"]


def test_archive_worker_recovers_after_interrupted_lease(ready_submission_candidate):
    from app.services.workflow_jobs import consume_archive_jobs

    archive = _approved_archive_from_candidate(ready_submission_candidate)
    store = Store()
    claimed = store.claim_jobs(job_type="archive.generate", consumer_id="interrupted-worker", limit=50)
    assert any(job["id"] == archive["job_id"] for job in claimed)
    record = store.get_workflow_record("archive", archive["id"])
    record["status"] = "running"
    assert store.save_workflow_record("archive", record, expected_revision=record["revision"])
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", archive["job_id"]))

    result = consume_archive_jobs(store, consumer_id="replacement-worker", limit=50)

    assert archive["job_id"] in result["succeeded"], result
    assert store.get_workflow_record("archive", archive["id"])["status"] == "succeeded"
    assert store.get_job(archive["job_id"])["state"] == "succeeded"
    assert client.get(f"/api/archives/{archive['id']}/download", headers=ready_submission_candidate["student"]).status_code == 200


@pytest.mark.parametrize("stale_fails", [False, True])
def test_interrupted_archive_worker_cannot_overwrite_replacement(ready_submission_candidate, monkeypatch, stale_fails):
    from app.api import workflow as workflow_api
    from app.services.workflow_jobs import consume_archive_jobs

    archive = _approved_archive_from_candidate(ready_submission_candidate)
    store = Store()
    reached_check, release_old = Event(), Event()
    original = workflow_api.require_docx_output_pass

    def pause_first_archive_check(*args, **kwargs):
        if kwargs.get("context_kind") == "workflow-archive" and not reached_check.is_set():
            reached_check.set()
            assert release_old.wait(30)
            if stale_fails:
                raise RuntimeError("interrupted worker failed after replacement")
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow_api, "require_docx_output_pass", pause_first_archive_check)
    with ThreadPoolExecutor(max_workers=1) as executor:
        old = executor.submit(consume_archive_jobs, store, consumer_id="old-worker", limit=50)
        try:
            assert reached_check.wait(30)
            with store.workflow_transaction() as conn:
                conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", archive["job_id"]))
            replacement = consume_archive_jobs(store, consumer_id="replacement-worker", limit=50)
            assert archive["job_id"] in replacement["succeeded"], replacement
            before = client.get(f"/api/archives/{archive['id']}/download", headers=ready_submission_candidate["student"])
            assert before.status_code == 200, before.text
        finally:
            release_old.set()
        stale_result = old.result(timeout=30)

    assert archive["job_id"] not in stale_result["succeeded"]
    after = client.get(f"/api/archives/{archive['id']}/download", headers=ready_submission_candidate["student"])
    assert after.status_code == 200 and after.content == before.content
    assert store.get_job(archive["job_id"])["state"] == "succeeded"
    assert store.get_workflow_record("archive", archive["id"])["status"] == "succeeded"


def test_exhausted_archive_job_notifies_both_roles_and_can_retry(ready_submission_candidate, tmp_path):
    from app.services.workflow_jobs import consume_archive_jobs

    fixture = ready_submission_candidate
    archive = _approved_archive_from_candidate(fixture)
    store = Store()
    attachment = tmp_path / "missing-material.txt"
    data = b"synthetic test material"
    store.save_workflow_record("process_record", {
        "id": f"process-{uuid.uuid4().hex[:12]}", "scope_type": "participant", "scope_id": fixture["participant_id"],
        "attachments": [{"id": "attachment", "storage_path": str(attachment), "sha256": hashlib.sha256(data).hexdigest()}],
    })
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET max_attempts = 1 WHERE id = ?", (archive["job_id"],))

    first = consume_archive_jobs(store, consumer_id="failing-worker", limit=50)
    assert any(item["job_id"] == archive["job_id"] and item["state"] == "failed" for item in first["failed"])
    failed = store.get_workflow_record("archive", archive["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "ARCHIVE_GENERATION_FAILED"
    assert client.get(f"/api/archives/{archive['id']}/download", headers=fixture["student"]).status_code == 409
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    supervision = next(item for item in store.list_workflow_records("supervision", limit=None)
                       if item.get("cohort_id") == fixture["cohort_id"] and item.get("student_id") == participant["student_id"])
    for recipient in (participant["student_id"], supervision["teacher_id"]):
        assert any(event["event_type"] == "archive.failed" and event["resource_id"] == archive["id"]
                   for event in store.list_workflow_events(recipient))

    attachment.write_bytes(data)
    retried = client.post(f"/api/archives/{archive['id']}/retry", headers=fixture["teacher"], json={"reason": "归档条件已恢复"})
    assert retried.status_code == 200, retried.text
    recovered = consume_archive_jobs(store, consumer_id="recovered-worker", limit=50)
    assert archive["job_id"] in recovered["succeeded"], recovered
    assert client.get(f"/api/archives/{archive['id']}/download", headers=fixture["student"]).status_code == 200


def test_expired_last_archive_attempt_is_parked(ready_submission_candidate):
    from app.services.workflow_jobs import consume_archive_jobs

    archive = _approved_archive_from_candidate(ready_submission_candidate)
    store = Store()
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET max_attempts = 1 WHERE id = ?", (archive["job_id"],))
    claimed = store.claim_jobs(job_type="archive.generate", consumer_id="interrupted-final-worker", limit=50)
    assert any(job["id"] == archive["job_id"] for job in claimed)
    record = store.get_workflow_record("archive", archive["id"])
    record["status"] = "running"
    assert store.save_workflow_record("archive", record, expected_revision=record["revision"])
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", archive["job_id"]))

    result = consume_archive_jobs(store, consumer_id="replacement-worker", limit=50)

    assert archive["job_id"] not in result["succeeded"]
    assert store.get_job(archive["job_id"])["state"] == "failed"
    failed = store.get_workflow_record("archive", archive["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "ARCHIVE_ATTEMPTS_EXHAUSTED"


def test_retry_archive_rejects_a_mismatched_job(ready_submission_candidate):
    fixture = ready_submission_candidate
    archive = _approved_archive_from_candidate(fixture)
    store = Store()
    record = store.get_workflow_record("archive", archive["id"])
    record["status"] = "failed"
    assert store.save_workflow_record("archive", record, expected_revision=record["revision"])
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET job_type = ? WHERE id = ?", ("project.export", archive["job_id"]))
    original_job = store.get_job(archive["job_id"])

    response = client.post(f"/api/archives/{archive['id']}/retry", headers=fixture["teacher"], json={"reason": "重试"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ARCHIVE_QUEUE_STATE_MISMATCH"
    assert store.get_job(archive["job_id"]) == original_job
    assert store.get_workflow_record("archive", archive["id"])["status"] == "failed"
    assert store.cancel_job(archive["job_id"], reason="test completed")


def test_submission_image_stays_bound_to_its_version_and_guidance(ready_submission_candidate):
    from app.services.project_versions import resolve_project_attachment

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )
    fixture = ready_submission_candidate
    student, teacher = fixture["student"], fixture["teacher"]
    participant = Store().get_workflow_record("participant", fixture["participant_id"])
    project_id = participant["project_id"]
    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    uploaded = client.post(f"/api/projects/{project_id}/images", headers=student, files={
        "file": ("figure.png", png, "image/png"),
    })
    assert uploaded.status_code == 201, uploaded.text
    section = next(item for item in project["sections"] if item["key"] == "ch1")
    saved = client.put(f"/api/projects/{project_id}/section/ch1", headers=student, json={
        "base_revision": project["revision"],
        "paragraphs": section["paragraphs"] + [{
            "id": "submitted-figure", "type": "image",
            "image": {"url": uploaded.json()["url"], "caption": "图 1.1 研究结果"},
        }],
    })
    assert saved.status_code == 200, saved.text
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate.json()["id"], "idempotency_key": "image-version-submit",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    image_path = f"/api/submissions/{submission_id}/image"

    # The student can keep editing, but the teacher must see the submitted image.
    current = client.get(f"/api/projects/{project_id}", headers=student).json()
    section = next(item for item in current["sections"] if item["key"] == "ch1")
    saved = client.put(f"/api/projects/{project_id}/section/ch1", headers=student, json={
        "base_revision": current["revision"],
        "paragraphs": [block for block in section["paragraphs"] if block["id"] != "submitted-figure"],
    })
    assert saved.status_code == 200, saved.text
    for headers in (student, teacher):
        image = client.get(image_path, headers=headers, params={"block_id": "submitted-figure"})
        assert image.status_code == 200 and image.content == png, image.text
        assert image.headers["content-type"] == "image/png"
        assert image.headers["cache-control"] == "private, no-store"
    other_teacher, other_student, _, _ = _create_people(fixture["admin"])
    for headers in (other_teacher, other_student):
        assert client.get(image_path, headers=headers, params={"block_id": "submitted-figure"}).status_code in (403, 404)
    assert client.get(image_path, headers=teacher, params={"block_id": "ch1-p1"}).status_code == 404

    version = Store().get_workflow_record("project_version", candidate.json()["project_version_id"])
    path = resolve_project_attachment(version["attachment_refs"][0])
    path.write_bytes(b"changed image")
    tampered = client.get(image_path, headers=teacher, params={"block_id": "submitted-figure"})
    assert tampered.status_code == 409
    assert tampered.json()["detail"]["code"] == "SUBMISSION_IMAGE_UNAVAILABLE"


def test_student_can_withdraw_pending_submission_after_deadline(ready_submission_candidate):
    fixture = ready_submission_candidate
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": "withdraw-after-deadline",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    store = Store()
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    participant["effective_deadline_at"] = (fixture["now"] - timedelta(seconds=1)).isoformat()
    assert store.save_workflow_record("participant", participant, expected_revision=participant["revision"])

    withdrawn = client.post(f"/api/submissions/{submission_id}/withdraw", headers=fixture["student"])
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["state"] == "withdrawn"
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    assert participant["state"] == "in_progress"
    assert participant["current_submission_id"] == ""
    assert participant["last_submission_id"] == submission_id
    assert any(event["data"].get("event_type") == "submission.withdrawn"
               and event["data"].get("resource_id") == submission_id
               for event in client.get("/api/notifications", headers=fixture["teacher"]).json()["items"])


def test_withdraw_loses_to_concurrent_teacher_decision(ready_submission_candidate, monkeypatch):
    fixture = ready_submission_candidate
    submitted = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": "withdraw-concurrent-review",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    waiting, proceed = Event(), Event()
    original_transaction = Store.workflow_transaction

    @contextmanager
    def delay_first_transaction(store):
        if not waiting.is_set():
            waiting.set()
            assert proceed.wait(timeout=10)
        with original_transaction(store) as conn:
            yield conn

    with monkeypatch.context() as patch:
        patch.setattr(Store, "workflow_transaction", delay_first_transaction)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, f"/api/submissions/{submission_id}/withdraw",
                                      headers=fixture["student"])
            try:
                assert waiting.wait(timeout=10)
                reviewed = client.post(f"/api/submissions/{submission_id}/reviews", headers=fixture["teacher"],
                                       json={"decision": "returned", "reason": "补充研究内容"})
            finally:
                proceed.set()
            withdrawn = pending.result(timeout=20)
    assert reviewed.status_code == 200, reviewed.text
    assert withdrawn.status_code == 409, withdrawn.text
    assert withdrawn.json()["detail"]["code"] == "WITHDRAW_NOT_PENDING"
    submission = Store().get_workflow_record("submission", submission_id)
    participant = Store().get_workflow_record("participant", fixture["participant_id"])
    assert submission["state"] == "returned"
    assert participant["state"] == "needs_revision"
    assert participant["current_submission_id"] == submission_id


def test_submission_rechecks_deadline_after_artifact_verification(ready_submission_candidate, monkeypatch):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    verify = workflow_api._verified_candidate_artifact

    def verify_after_deadline(*args, **kwargs):
        result = verify(*args, **kwargs)

        class ClockAfterDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return (fixture["now"] + timedelta(days=2)).astimezone(tz or timezone.utc)

        monkeypatch.setattr(workflow_api, "datetime", ClockAfterDeadline)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(workflow_api, "_verified_candidate_artifact", verify_after_deadline)
        response = client.post("/api/submissions", headers=fixture["student"], json={
            "candidate_id": fixture["candidate_id"], "idempotency_key": "deadline-during-verification",
        })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "DEADLINE_PASSED"
    assert Store().get_workflow_record("candidate", fixture["candidate_id"])["status"] == "ready"
    assert Store().get_workflow_record("participant", fixture["participant_id"])["state"] == "in_progress"


def test_submission_rolls_back_if_deadline_passes_during_transaction(ready_submission_candidate, monkeypatch):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    notify = workflow_api._notify

    def notify_after_deadline(*args, **kwargs):
        result = notify(*args, **kwargs)

        class ClockAfterDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return (fixture["now"] + timedelta(days=2)).astimezone(tz or timezone.utc)

        monkeypatch.setattr(workflow_api, "datetime", ClockAfterDeadline)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(workflow_api, "_notify", notify_after_deadline)
        response = client.post("/api/submissions", headers=fixture["student"], json={
            "candidate_id": fixture["candidate_id"], "idempotency_key": "deadline-during-transaction",
        })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "DEADLINE_PASSED"
    store = Store()
    assert store.get_workflow_record("candidate", fixture["candidate_id"])["status"] == "ready"
    assert store.get_workflow_record("participant", fixture["participant_id"])["state"] == "in_progress"
    assert not any(item.get("participant_id") == fixture["participant_id"]
                   for item in store.list_workflow_records("submission", limit=None))


def test_submission_rechecks_cohort_after_concurrent_close(ready_submission_candidate, monkeypatch):
    fixture = ready_submission_candidate
    waiting, proceed = Event(), Event()
    original_transaction = Store.workflow_transaction

    @contextmanager
    def delay_first_transaction(store):
        if not waiting.is_set():
            waiting.set()
            assert proceed.wait(timeout=10)
        with original_transaction(store) as conn:
            yield conn

    with monkeypatch.context() as patch:
        patch.setattr(Store, "workflow_transaction", delay_first_transaction)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, "/api/submissions", headers=fixture["student"], json={
                "candidate_id": fixture["candidate_id"], "idempotency_key": "cohort-closed-during-submit",
            })
            try:
                assert waiting.wait(timeout=10)
                closed = client.post(f"/api/cohorts/{fixture['cohort_id']}/close", headers=fixture["admin"],
                                     json={"reason": "结束批次", "force": True})
            finally:
                proceed.set()
            response = pending.result(timeout=20)
    assert closed.status_code == 200, closed.text
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "COHORT_CLOSED"
    assert Store().get_workflow_record("candidate", fixture["candidate_id"])["status"] == "ready"
    assert Store().get_workflow_record("participant", fixture["participant_id"])["state"] == "in_progress"
    reopened = client.post(f"/api/cohorts/{fixture['cohort_id']}/reopen", headers=fixture["admin"],
                           json={"reason": "继续提交"})
    assert reopened.status_code == 200, reopened.text
    retried = client.post("/api/submissions", headers=fixture["student"], json={
        "candidate_id": fixture["candidate_id"], "idempotency_key": "cohort-closed-during-submit",
    })
    assert retried.status_code == 200, retried.text
    assert retried.json()["submission"]["state"] == "submitted"


@pytest.mark.parametrize("delivery_stage", ["legacy", "m1"])
def test_submission_and_archive_use_the_same_checked_document(monkeypatch, delivery_stage):
    monkeypatch.setenv("PAPERGUARD_DELIVERY_STAGE", delivery_stage)
    from app.api import workflow as workflow_api
    from app.api.workflow import _candidate_artifact_path

    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-artifact-school-{suffix}", "name": "提交成品测试学校", "type": "school",
    }).json()
    other_teacher, _, other_teacher_id, _ = _create_people(admin)
    other_teacher_record = Store().get_user(other_teacher_id)
    other_teacher_record["organization_context"] = {"school_id": organization["id"]}
    Store().save_user(other_teacher_record)
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "提交成品测试批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "final", "name": "终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    project_id = _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200

    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    candidate_id = candidate.json()["id"]
    path = _candidate_artifact_path(candidate_id)
    original = path.read_bytes()
    assert candidate.json()["artifact"]["docx_sha256"] == hashlib.sha256(original).hexdigest()
    with monkeypatch.context() as failing_renderer:
        def fail_render(*_args, **_kwargs):
            raise OSError("renderer unavailable")

        failing_renderer.setattr(workflow_api, "export_project_to_docx", fail_render)
        blocked = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
        assert blocked.status_code == 201 and blocked.json()["status"] == "blocked"
        assert any(gate["key"] == "output_artifact" and gate["blocking"] for gate in blocked.json()["gates"])
        assert client.post("/api/submissions", headers=student, json={
            "candidate_id": blocked.json()["id"], "idempotency_key": f"artifact-failure-{suffix}",
        }).status_code == 409

    path.write_bytes(b"altered")
    rejected = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate_id, "idempotency_key": f"artifact-{suffix}",
    })
    assert rejected.status_code == 409
    path.write_bytes(original)
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate_id, "idempotency_key": f"artifact-{suffix}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]

    path.write_bytes(b"altered after submission")
    assert client.post(f"/api/submissions/{submission_id}/reviews", headers=teacher, json={"decision": "approved"}).status_code == 409
    path.write_bytes(original)
    approved = client.post(f"/api/submissions/{submission_id}/reviews", headers=teacher, json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    archive_id = approved.json()["archive"]["id"]
    path.write_bytes(b"altered before archive")
    run_once()
    assert client.get(f"/api/archives/{archive_id}", headers=student).json()["status"] == "failed"
    assert client.get(f"/api/archives/{archive_id}/download", headers=student).status_code == 409
    path.write_bytes(original)
    for unauthorized in (other_teacher, student):
        rejected = client.post(f"/api/archives/{archive_id}/retry", headers=unauthorized, json={"reason": "无权重试"})
        assert rejected.status_code == 403, rejected.text
        assert Store().get_workflow_record("archive", archive_id)["status"] == "failed"
    with monkeypatch.context() as job_busy:
        from app.repositories.workflow_repository import WorkflowRepository

        job_busy.setattr(WorkflowRepository, "retry_job_in_transaction", lambda *_args, **_kwargs: None)
        blocked_retry = client.post(f"/api/archives/{archive_id}/retry", headers=teacher, json={"reason": "任务正忙"})
        assert blocked_retry.status_code == 409
        assert Store().get_workflow_record("archive", archive_id)["status"] == "failed"
    retried = client.post(f"/api/archives/{archive_id}/retry", headers=teacher, json={"reason": "恢复已验证的提交文件"})
    assert retried.status_code == 200
    assert retried.json()["retry_requested_by"] == teacher_id
    run_once()
    download = client.get(f"/api/archives/{archive_id}/download", headers=student)
    assert download.status_code == 200, download.text
    with zipfile.ZipFile(io.BytesIO(download.content)) as bundle:
        assert bundle.read("final.docx") == original


def test_workflow_happy_path_and_idempotency(monkeypatch):
    from app.api import workflow as workflow_api
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
    other = register_fixture_response(client, other_name, other_password, display_name="其他学生")
    other_id = other.json()["id"]
    other_student = _login(other_name, other_password)

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

    original_project_check = workflow_api.check_project

    def unchecked_required_rule(*args, **kwargs):
        report = original_project_check(*args, **kwargs)
        report["not_checked"].append({"id": "required-rule", "description": "必需规则", "type": "auto", "enforce": "hard", "reason": "unsupported_checker"})
        report["summary"]["not_checked"] += 1
        return report

    monkeypatch.setattr(workflow_api, "check_project", unchecked_required_rule)
    blocked = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant_id})
    assert blocked.status_code == 201 and blocked.json()["status"] == "blocked"
    assert any(item["key"] == "automatic_unverified" and item["detail"] == ["必需规则"] for item in blocked.json()["gates"])
    # Existing candidates created before the gate was added must also fail closed.
    old_candidate = Store().get_workflow_record("candidate", blocked.json()["id"])
    old_candidate["status"] = "ready"
    old_candidate["gates"] = [item for item in old_candidate["gates"] if item["key"] != "automatic_unverified"]
    Store().save_workflow_record("candidate", old_candidate, expected_revision=old_candidate["revision"])
    rejected = client.post("/api/submissions", headers=student, json={"candidate_id": old_candidate["id"], "idempotency_key": f"unchecked-{suffix}"})
    assert rejected.status_code == 409
    assert any(item["key"] == "automatic_unverified" for item in rejected.json()["detail"]["gates"])
    monkeypatch.setattr(workflow_api, "check_project", original_project_check)

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

    invalid_anchor = client.post(f"/api/submissions/{submission_id}/comments", headers=teacher, json={
        "body": "请修改", "block_id": "not-in-submitted-version",
    })
    assert invalid_anchor.status_code == 422
    invalid_quote = client.post(f"/api/submissions/{submission_id}/comments", headers=teacher, json={
        "body": "请修改", "block_id": "ch1-p1", "quoted_text": "不属于该版本的文字",
    })
    assert invalid_quote.status_code == 422
    assert client.post(f"/api/submissions/{submission_id}/comments", headers=other_student, json={
        "body": "其他学生试图批注", "block_id": "ch1-p1",
    }).status_code == 403
    comment = client.post(f"/api/submissions/{submission_id}/comments", headers=teacher, json={
        "body": "请补充实验分析", "severity": "blocking", "block_id": "ch1-p1", "quoted_text": "论文正文内容",
    })
    assert comment.status_code == 201, comment.text
    assert comment.json()["block_id"] == "ch1-p1"
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
    late_comment = client.post(f"/api/submissions/{submission_id}/comments", headers=teacher,
                               json={"body": "已退回后新增的批注"})
    assert late_comment.status_code == 409 and late_comment.json()["detail"]["code"] == "COMMENT_NOT_PENDING"
    assert client.post(f"/api/comments/{comment_id}/response", headers=student,
                       json={"body": "已补充实验分析"}).status_code == 201
    assert client.post(f"/api/comments/{comment_id}/response", headers=student,
                       json={"body": "补充了对照结果"}).status_code == 201
    teacher_events = client.get("/api/notifications", headers=teacher).json()["items"]
    replies = [item for item in teacher_events if item["event_type"] == "comment.responded"
               and item["resource_id"] == submission_id]
    assert len(replies) == 2
    assert all(item["resource_type"] == "submission" for item in replies)
    student_events = client.get("/api/notifications", headers=student).json()["items"]
    assert not any(item["event_type"] == "comment.responded" and item["resource_id"] == submission_id
                   for item in student_events)
    candidate2 = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant_id, "modification_note": "按意见修订",
    })
    assert candidate2.status_code == 201 and candidate2.json()["status"] == "ready", candidate2.text
    submitted2 = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate2.json()["id"], "idempotency_key": f"submit-{suffix}-2",
    })
    assert submitted2.status_code == 200, submitted2.text
    submission2_id = submitted2.json()["submission"]["id"]
    prior_feedback = client.get(f"/api/submissions/{submission2_id}", headers=teacher).json()["prior_unresolved_comments"]
    assert [item["id"] for item in prior_feedback] == [comment_id]
    assert prior_feedback[0]["source_submission_number"] == submitted.json()["submission"]["submission_number"]
    assert prior_feedback[0]["status"] == "addressed" and len(prior_feedback[0]["responses"]) == 2
    blocked_approval = client.post(f"/api/submissions/{submission2_id}/reviews", headers=teacher, json={"decision": "approved"})
    assert blocked_approval.status_code == 409 and "导师确认" in blocked_approval.text
    unresolved_approval = client.post(f"/api/submissions/{submission2_id}/reviews", headers=teacher, json={
        "decision": "approved", "manual_confirmations": {"导师确认": True},
    })
    assert unresolved_approval.status_code == 409
    assert comment_id in unresolved_approval.json()["detail"]["open_blocking_comments"]
    assert client.post(f"/api/comments/{comment_id}/status", headers=teacher,
                       json={"status": "resolved", "body": "已复核"}).status_code == 200
    assert client.get(f"/api/submissions/{submission2_id}", headers=teacher).json()["prior_unresolved_comments"] == []

    # A blocking comment committed while approval is waiting for its transaction
    # must be part of the approval decision.
    approval_waiting, allow_approval = Event(), Event()
    original_transaction = Store.workflow_transaction

    @contextmanager
    def delay_first_transaction(store):
        if not approval_waiting.is_set():
            approval_waiting.set()
            assert allow_approval.wait(timeout=10)
        with original_transaction(store) as conn:
            yield conn

    with monkeypatch.context() as race:
        race.setattr(Store, "workflow_transaction", delay_first_transaction)
        with ThreadPoolExecutor(max_workers=1) as executor:
            approval_future = executor.submit(client.post, f"/api/submissions/{submission2_id}/reviews", headers=teacher,
                                              json={"decision": "approved", "manual_confirmations": {"导师确认": True}})
            try:
                assert approval_waiting.wait(timeout=10)
                late_blocking = client.post(f"/api/submissions/{submission2_id}/comments", headers=teacher,
                                            json={"body": "仍需修改", "severity": "blocking"})
            finally:
                allow_approval.set()
            raced_approval = approval_future.result(timeout=10)
    assert late_blocking.status_code == 201, late_blocking.text
    assert raced_approval.status_code == 409
    assert late_blocking.json()["id"] in raced_approval.json()["detail"]["open_blocking_comments"]
    assert client.post(f"/api/comments/{late_blocking.json()['id']}/status", headers=teacher,
                       json={"status": "resolved"}).status_code == 200

    from app.repositories.workflow_repository import WorkflowRepository

    def fail_archive_job(*_args, **_kwargs):
        raise RuntimeError("archive job unavailable")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(WorkflowRepository, "create_job_in_transaction", fail_archive_job)
        with pytest.raises(RuntimeError, match="archive job unavailable"):
            client.post(f"/api/submissions/{submission2_id}/reviews", headers=teacher, json={
                "decision": "approved", "manual_confirmations": {"导师确认": True},
            })
    unchanged = client.get(f"/api/submissions/{submission2_id}", headers=student).json()
    assert unchanged["submission"]["state"] == "submitted"
    assert unchanged["participant"]["state"] == "submitted"
    assert not any(item.get("submission_id") == submission2_id for item in Store().list_workflow_records("archive", limit=None))
    approved = client.post(f"/api/submissions/{submission2_id}/reviews", headers=teacher, json={
        "decision": "approved", "manual_confirmations": {"导师确认": True},
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["participant"]["state"] == "pending_archive"
    automatic_archive = approved.json()["archive"]
    assert automatic_archive["status"] == "queued"
    assert approved.json()["participant"]["archive_id"] == automatic_archive["id"]
    assert client.get(f"/api/jobs/{automatic_archive['job_id']}", headers=admin).json()["state"] == "queued"

    archive = client.post("/api/archives", headers=admin, json={"submission_id": submission2_id})
    assert archive.status_code == 202 and archive.json()["status"] == "queued", archive.text
    assert archive.json()["id"] == automatic_archive["id"]
    assert "file_path" not in archive.json()
    archive_id = archive.json()["id"]
    job_id = archive.json()["job_id"]
    queued_job = client.get(f"/api/jobs/{job_id}", headers=admin)
    assert queued_job.status_code == 200 and queued_job.json()["state"] == "queued"
    assert "lease_token" not in queued_job.json() and "leased_by" not in queued_job.json()
    worker_result = run_once()
    assert job_id in worker_result["archives"]["succeeded"]
    assert not any(item["job_id"] == job_id for item in worker_result["archives"]["failed"])
    normal_download = client.get(f"/api/archives/{archive_id}/download", headers=student)
    assert normal_download.status_code == 200 and normal_download.content[:2] == b"PK"
    _make_legacy_manual_archive(archive_id, "核对 Word/WPS 分页、字体与表格跨页", monkeypatch, pending=True)
    archive_status = client.get(f"/api/archives/{archive_id}", headers=admin)
    assert archive_status.status_code == 200 and archive_status.json()["status"] == "succeeded"
    assert all(item["id"] != archive_id for item in client.get("/api/archives", headers=other_student).json())
    assert client.get(f"/api/archives/{archive_id}", headers=other_student).status_code == 404
    assert client.get(f"/api/archives/{archive_id}/download", headers=other_student).status_code == 404
    assert archive_status.json()["manifest"]["output_check"]["status"] == "pass"
    assert archive_status.json()["manifest"]["output_check"]["check_count"] > 0
    assert archive_status.json()["download_url"].endswith(f"/api/archives/{archive_id}/download")
    assert archive_status.json()["manual_review"]["status"] == "required"
    assert not archive_status.json().get("review_candidate_url")
    completed_job = client.get(f"/api/jobs/{job_id}", headers=admin)
    assert completed_job.status_code == 200 and completed_job.json()["state"] == "succeeded"
    download = client.get(f"/api/archives/{archive_id}/download", headers=student)
    assert download.status_code == 200
    assert client.get(f"/api/archives/{archive_id}/review-candidate", headers=student).status_code == 403
    assert client.post(f"/api/archives/{archive_id}/manual-review", headers=student, json={
        "output_sha256": "0" * 64, "confirmed_check_ids": [],
    }).status_code == 403
    candidate = client.get(f"/api/archives/{archive_id}/review-candidate", headers=teacher)
    assert candidate.status_code == 409
    assert candidate.json()["detail"]["code"] == "OUTPUT_CHECK_AUTOMATION_REQUIRED"
    review = archive_status.json()["manual_review"]
    assert client.post(f"/api/archives/{archive_id}/manual-review", headers=teacher, json={
        "output_sha256": "0" * 64, "confirmed_check_ids": [item["id"] for item in review["checklist"]],
    }).status_code == 409
    assert client.post(f"/api/archives/{archive_id}/manual-review", headers=teacher, json={
        "output_sha256": review["output_sha256"], "confirmed_check_ids": [],
    }).status_code == 409
    confirmed = client.post(f"/api/archives/{archive_id}/manual-review", headers=teacher, json={
        "output_sha256": review["output_sha256"],
        "confirmed_check_ids": [item["id"] for item in review["checklist"]],
    })
    assert confirmed.status_code == 409, confirmed.text
    assert confirmed.json()["detail"]["code"] == "OUTPUT_CHECK_AUTOMATION_REQUIRED"
    assert Store().get_workflow_record("participant", archive_status.json()["participant_id"])["state"] == "pending_archive"

    # A historical receipt cannot turn an incomplete automatic check into a pass.
    from app.services.manual_output_review import confirm_review
    legacy_archive = Store().get_workflow_record("archive", archive_id)
    legacy_archive["manual_review"] = confirm_review(
        legacy_archive["manifest"]["output_check"], output_sha256=review["output_sha256"],
        confirmed_check_ids=[item["id"] for item in review["checklist"]], reviewer_id="legacy-reviewer",
    )
    assert Store().save_workflow_record("archive", legacy_archive, expected_revision=legacy_archive["revision"])
    legacy_view = client.get(f"/api/archives/{archive_id}", headers=teacher).json()
    assert legacy_view["manual_review"]["status"] == "required"
    assert legacy_view["download_url"].endswith(f"/api/archives/{archive_id}/download")
    download = client.get(f"/api/archives/{archive_id}/download", headers=student)
    assert download.status_code == 200
    successful_archive = Store().get_workflow_record("archive", archive_id)
    assert successful_archive.get("file_path")
    assert successful_archive["manifest"]["output_check"]["status"] == "pass"
    assert successful_archive["manual_review"]["status"] == "confirmed"
    from app.api.workflow import _archive_dir
    archive_path = _archive_dir() / f"{archive_id}.zip"
    assert archive_path.exists()
    original_archive = archive_path.read_bytes()
    archive_path.write_bytes(b"not a valid ZIP archive")
    assert client.get(f"/api/archives/{archive_id}/download", headers=student).status_code == 409
    archive_path.write_bytes(original_archive)
    version = Store().get_workflow_record("project_version", submitted2.json()["submission"]["project_version_id"])
    assert version is not None
    snapshot = dict(version["content_snapshot"])
    snapshot["template_snapshot"] = {**snapshot["template_snapshot"], "source_docx": "replaced-after-archive.docx"}
    version["content_snapshot"] = snapshot
    assert Store().save_workflow_record("project_version", version, expected_revision=version["revision"])
    assert client.get(f"/api/archives/{archive_id}/download", headers=student).status_code == 409
    report = client.get(f"/api/reports/cohorts/{cohort_id}", headers=admin)
    assert report.status_code == 200 and report.json()["participant_count"] == 2 and report.json()["state_counts"]["pending_archive"] == 1
    assert client.get(f"/api/reports/cohorts/{cohort_id}", headers=student).status_code == 403
    assert client.get("/api/notifications", headers=student).json()["unread"] >= 1
    assert project_id


def test_teacher_reopens_while_archive_is_generating(monkeypatch):
    from app.api import workflow as workflow_api
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-reopen-school-{suffix}", "name": "重开测试学校", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "重开测试批次", "organization_id": organization["id"], "academic_year": "2027",
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "final", "name": "终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    project_id = _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]}).json()
    submission = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate["id"], "idempotency_key": f"reopen-{suffix}",
    }).json()["submission"]
    approved = client.post(f"/api/submissions/{submission['id']}/reviews", headers=teacher, json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    archive = approved.json()["archive"]
    path = f"/api/stage-participants/{participant['id']}/reopen"
    assert client.post(path, headers=student, json={"reason": "学生不能重开"}).status_code == 403
    assert client.post(path, headers=admin, json={"reason": "管理员不能代替教师重开"}).status_code == 403
    assert client.post(path, headers=teacher, json={"reason": "   "}).status_code == 422

    original_verify = workflow_api._verified_candidate_artifact

    def verify_after_reopen(*args, **kwargs):
        monkeypatch.setattr(workflow_api, "_verified_candidate_artifact", original_verify)
        reopened = client.post(path, headers=teacher, json={"reason": "需要补充实验结果"})
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["state"] == "in_progress"
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(workflow_api, "_verified_candidate_artifact", verify_after_reopen)
    result = run_once()
    assert archive["job_id"] in result["archives"]["succeeded"]
    current = client.get(f"/api/stage-participants/{participant['id']}", headers=student).json()["participant"]
    assert current["state"] == "in_progress"
    assert current["current_submission_id"] == ""
    assert current["archive_id"] == ""
    historical = Store().get_workflow_record("archive", archive["id"])
    assert historical["status"] == "succeeded"
    assert historical["superseded_by_reopen"] is True
    project = client.get(f"/api/projects/{project_id}", headers=student).json()
    updated = client.put(f"/api/projects/{project_id}/meta", headers=student, json={
        "base_revision": project["revision"], "meta": {**project["meta"], "title": "重开后的修订"},
    })
    assert updated.status_code == 200, updated.text
    next_candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert next_candidate.status_code == 201 and next_candidate.json()["status"] == "ready", next_candidate.text
    next_submission = client.post("/api/submissions", headers=student, json={
        "candidate_id": next_candidate.json()["id"], "idempotency_key": f"reopen-next-{suffix}",
    })
    assert next_submission.status_code == 200, next_submission.text
    next_approval = client.post(f"/api/submissions/{next_submission.json()['submission']['id']}/reviews", headers=teacher, json={"decision": "approved"})
    assert next_approval.status_code == 200, next_approval.text
    next_archive = next_approval.json()["archive"]
    _make_legacy_manual_archive(archive["id"], "核对分页", monkeypatch)
    old_review = client.get(f"/api/archives/{archive['id']}", headers=teacher).json()["manual_review"]
    assert old_review["status"] == "required"
    confirmed = client.post(f"/api/archives/{archive['id']}/manual-review", headers=teacher, json={
        "output_sha256": old_review["output_sha256"],
        "confirmed_check_ids": [item["id"] for item in old_review["checklist"]],
    })
    assert confirmed.status_code == 409, confirmed.text
    current = client.get(f"/api/stage-participants/{participant['id']}", headers=student).json()["participant"]
    assert current["state"] == "pending_archive"
    assert current["archive_id"] == next_archive["id"]


def test_archive_output_check_failure_remains_blocked_after_retry(monkeypatch):
    def reject_archive_output(*_args, **_kwargs):
        raise DocxOutputCheckError({
            "status": "fail", "checks": [{"rule": "format.font_size", "status": "fail"}],
            "unsupported": [], "check_count": 1, "passed_count": 0,
        })

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
    monkeypatch.setattr("app.api.workflow.require_docx_output_pass", reject_archive_output)

    archive = client.post("/api/archives", headers=admin, json={"submission_id": submission["id"]})
    assert archive.status_code == 202, archive.text
    archive_id, job_id = archive.json()["id"], archive.json()["job_id"]
    failed = run_once()
    assert any(item["job_id"] == job_id for item in failed["archives"]["failed"])
    failed_archive = client.get(f"/api/archives/{archive_id}", headers=admin)
    assert failed_archive.status_code == 200 and failed_archive.json()["status"] == "failed"
    assert failed_archive.json()["error_code"] == "ARCHIVE_OUTPUT_CHECK_FAILED"
    assert not Store().get_workflow_record("archive", archive_id).get("file_path")
    retried = client.post(f"/api/archives/{archive_id}/retry", headers=admin, json={"reason": "重试不能绕过成品检查"})
    assert retried.status_code == 200 and retried.json()["status"] == "queued"
    recovered = run_once()
    assert any(item["job_id"] == job_id for item in recovered["archives"]["failed"])
    assert job_id not in recovered["archives"]["succeeded"]
    completed = client.get(f"/api/archives/{archive_id}", headers=admin)
    assert completed.status_code == 200 and completed.json()["status"] == "failed"
    assert client.get(f"/api/archives/{archive_id}/download", headers=student).status_code == 409


def test_member_assignment_accepts_business_references_for_admin_flows():
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-ref-school-{suffix}", "name": "业务标识测试学院", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    teacher_name = f"ref_teacher_{suffix}"
    teacher = register_fixture_response(client, teacher_name, _password("Teacher"), role="teacher", display_name=f"导师{suffix}")
    teacher_id = teacher.json()["id"]
    assert client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=admin,
                      json={"role": "teacher", "status": "active"}).status_code == 200
    student_name = f"ref_student_{suffix}"
    student = register_fixture_response(client, student_name, _password("Student"), display_name=f"学生{suffix}")
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


@pytest.mark.parametrize("race_action", ["comment", "comment_status", "review", "extension", "withdraw_comment", "withdraw_review"])
def test_teacher_transfer_updates_open_task_and_resource_access(monkeypatch, race_action):
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    second_name = f"workflow_transfer_teacher_{suffix}"
    second_password = _password("TransferTeacher")
    second = register_fixture_response(client, second_name, second_password, role="teacher", display_name="接手教师")
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
    project_id = _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"transfer-race-{suffix}",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]
    existing_comment = client.post(f"/api/submissions/{submission_id}/comments", headers=teacher, json={
        "body": "转交前的批注", "severity": "info",
    })
    assert existing_comment.status_code == 201, existing_comment.text
    comment_id = existing_comment.json()["id"]
    actions = {
        "comment": (f"/api/submissions/{submission_id}/comments", {"body": "转交后的旧教师批注"}),
        "comment_status": (f"/api/comments/{comment_id}/status", {"status": "resolved"}),
        "review": (f"/api/submissions/{submission_id}/reviews", {"decision": "returned", "reason": "修改"}),
        "extension": (f"/api/stage-participants/{participant['id']}/extension", {
            "effective_deadline_at": (now + timedelta(days=4)).isoformat(), "reason": "延长修订时间",
        }),
    }
    action_path, action_body = actions[race_action.removeprefix("withdraw_")]

    waiting, proceed = Event(), Event()
    original_transaction = Store.workflow_transaction

    @contextmanager
    def delay_first_transaction(store):
        if not waiting.is_set():
            waiting.set()
            assert proceed.wait(timeout=10)
        with original_transaction(store) as conn:
            yield conn

    with monkeypatch.context() as race:
        race.setattr(Store, "workflow_transaction", delay_first_transaction)
        with ThreadPoolExecutor(max_workers=1) as executor:
            method = client.put if race_action == "extension" else client.post
            pending = executor.submit(method, action_path, headers=teacher, json=action_body)
            try:
                assert waiting.wait(timeout=10)
                if race_action.startswith("withdraw_"):
                    changed = client.post(f"/api/submissions/{submission_id}/withdraw", headers=student)
                else:
                    changed = client.post(f"/api/cohorts/{cohort_id}/members", headers=admin, json={
                        "student_id": student_id, "teacher_id": second_id, "reason": "导师工作调整",
                    })
            finally:
                proceed.set()
            stale_action = pending.result(timeout=10)
    if race_action.startswith("withdraw_"):
        assert changed.status_code == 200, changed.text
        assert stale_action.status_code == 409, stale_action.text
        detail = client.get(f"/api/submissions/{submission_id}", headers=student).json()
        assert detail["submission"]["state"] == "withdrawn" and detail["review"] is None
        assert len(detail["comments"]) == 1 and detail["comments"][0]["status"] == "open"
        return

    assert stale_action.status_code == 403, stale_action.text
    assert changed.status_code == 201, changed.text
    assert changed.json()["teacher_id"] == second_id
    updated = client.get(f"/api/stage-participants/{participant['id']}", headers=student)
    assert updated.status_code == 200 and updated.json()["participant"]["teacher_id"] == second_id
    assert client.get(f"/api/stage-participants/{participant['id']}", headers=teacher).status_code == 404
    assert client.get(f"/api/stage-participants/{participant['id']}", headers=second_teacher).status_code == 200
    version_id = submitted.json()["submission"]["project_version_id"]
    for path in (f"/api/projects/{project_id}", f"/api/projects/{project_id}/versions", f"/api/project-versions/{version_id}"):
        assert client.get(path, headers=teacher).status_code in {403, 404}
        assert client.get(path, headers=second_teacher).status_code == 200
    assert all(item["id"] != project_id for item in client.get("/api/projects", headers=teacher).json())
    detail = client.get(f"/api/submissions/{submission_id}", headers=second_teacher).json()
    assert detail["submission"]["state"] == "submitted"
    assert detail["review"] is None
    assert len(detail["comments"]) == 1 and detail["comments"][0]["status"] == "open"
    assert client.post(f"/api/submissions/{submission_id}/comments", headers=second_teacher,
                       json={"body": "接手教师批注"}).status_code == 201
    assert client.post(f"/api/comments/{comment_id}/status", headers=admin,
                       json={"status": "resolved"}).status_code == 200
    messages = client.get("/api/notifications", headers=second_teacher)
    assert messages.status_code == 200
    assert any(item.get("data", {}).get("event_type") == "supervision.transferred" for item in messages.json()["items"])


def test_reopened_feedback_across_rounds_blocks_submission_until_answered():
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"feedback-history-{suffix}", "name": "历史批注测试学校", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "历史批注测试批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "paper", "name": "论文", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(),
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200

    def candidate():
        result = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
        assert result.status_code == 201, result.text
        return result.json()

    def submit(item, key):
        return client.post("/api/submissions", headers=student, json={
            "candidate_id": item["id"], "idempotency_key": f"{key}-{suffix}",
        })

    stale_candidate = candidate()
    first = submit(candidate(), "first")
    assert first.status_code == 200, first.text
    first_id = first.json()["submission"]["id"]
    comment = client.post(f"/api/submissions/{first_id}/comments", headers=teacher, json={
        "body": "补充数据来源", "severity": "blocking",
    })
    assert comment.status_code == 201, comment.text
    comment_id = comment.json()["id"]
    assert client.post(f"/api/submissions/{first_id}/reviews", headers=teacher, json={
        "decision": "returned", "reason": "请修改",
    }).status_code == 200
    assert client.post(f"/api/comments/{comment_id}/response", headers=student, json={"body": "已补充"}).status_code == 201

    second = submit(candidate(), "second")
    assert second.status_code == 200, second.text
    second_id = second.json()["submission"]["id"]
    assert client.post(f"/api/submissions/{second_id}/reviews", headers=teacher, json={
        "decision": "returned", "reason": "请进一步修改",
    }).status_code == 200
    assert client.post(f"/api/comments/{comment_id}/status", headers=teacher, json={"status": "open"}).status_code == 200
    detail = client.get(f"/api/submissions/{second_id}", headers=student).json()
    assert [item["id"] for item in detail["prior_unresolved_comments"]] == [comment_id]

    stale = submit(stale_candidate, "stale")
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "CHECK_STALE"
    blocked = candidate()
    assert blocked["status"] == "blocked"
    gate = next(item for item in blocked["gates"] if item["key"] == "comment_responses")
    assert gate["blocking"] and comment_id in gate["detail"]
    assert client.post(f"/api/comments/{comment_id}/response", headers=student, json={"body": "已补充具体来源"}).status_code == 201
    ready = candidate()
    assert ready["status"] == "ready"

    # Recheck inside the submission transaction in case feedback changes after preview.
    assert client.post(f"/api/comments/{comment_id}/status", headers=teacher, json={"status": "open"}).status_code == 200
    rejected = submit(ready, "third")
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "COMMENT_RESPONSE_REQUIRED"
    assert comment_id in rejected.json()["detail"]["comment_ids"]
    assert client.post(f"/api/comments/{comment_id}/response", headers=student, json={"body": "已再次补充"}).status_code == 201
    third = submit(ready, "third")
    assert third.status_code == 200, third.text


def test_return_preserves_revision_window_and_allows_resubmission(monkeypatch):
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"return-deadline-{suffix}", "name": "退回修订期限测试学校", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "退回修订期限测试批次", "organization_id": organization["id"],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "paper", "name": "论文", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "revision_days": 0,
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200

    def submit(key):
        candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
        assert candidate.status_code == 201 and candidate.json()["status"] == "ready", candidate.text
        result = client.post("/api/submissions", headers=student, json={
            "candidate_id": candidate.json()["id"], "idempotency_key": f"{key}-{suffix}",
        })
        assert result.status_code == 200, result.text
        return result.json()["submission"]

    first = submit("initial")
    store = Store()
    expired = store.get_workflow_record("participant", participant["id"])
    expired["effective_deadline_at"] = (now - timedelta(hours=1)).isoformat()
    assert store.save_workflow_record("participant", expired, expected_revision=expired["revision"])
    review_path = f"/api/submissions/{first['id']}/reviews"
    blocked = client.post(review_path, headers=teacher, json={"decision": "returned", "reason": "需要修改"})
    assert blocked.status_code == 409 and "修订期限" in blocked.text
    assert client.get(f"/api/submissions/{first['id']}", headers=student).json()["submission"]["state"] == "submitted"

    extended = client.put(f"/api/stage-participants/{participant['id']}/extension", headers=teacher, json={
        "effective_deadline_at": (now + timedelta(days=3)).isoformat(), "reason": "安排修订时间",
    })
    assert extended.status_code == 200, extended.text
    revision_deadline = (now + timedelta(days=8)).replace(microsecond=0)
    returned = client.post(review_path, headers=teacher, json={
        "decision": "returned", "reason": "需要修改", "revision_deadline_at": revision_deadline.isoformat(),
    })
    assert returned.status_code == 200, returned.text
    assert datetime.fromisoformat(returned.json()["participant"]["revision_deadline_at"]) == revision_deadline

    second = submit("revision")
    returned_again = client.post(f"/api/submissions/{second['id']}/reviews", headers=teacher, json={
        "decision": "returned", "reason": "继续完善",
    })
    assert returned_again.status_code == 200, returned_again.text
    assert datetime.fromisoformat(returned_again.json()["participant"]["revision_deadline_at"]) == revision_deadline
    third = submit("revision-again")

    waiting, proceed = Event(), Event()
    original_transaction = Store.workflow_transaction

    @contextmanager
    def delay_review_transaction(store):
        if not waiting.is_set():
            waiting.set()
            assert proceed.wait(timeout=10)
        with original_transaction(store) as conn:
            yield conn

    latest_deadline = (now + timedelta(days=10)).replace(microsecond=0)
    with monkeypatch.context() as race:
        race.setattr(Store, "workflow_transaction", delay_review_transaction)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, f"/api/submissions/{third['id']}/reviews", headers=teacher,
                                      json={"decision": "returned", "reason": "进一步修改"})
            try:
                assert waiting.wait(timeout=10)
                extension = client.put(f"/api/stage-participants/{participant['id']}/extension", headers=teacher, json={
                    "effective_deadline_at": latest_deadline.isoformat(), "reason": "延长修订时间",
                })
            finally:
                proceed.set()
            raced_return = pending.result(timeout=10)
    assert extension.status_code == 200, extension.text
    assert raced_return.status_code == 200, raced_return.text
    assert datetime.fromisoformat(raced_return.json()["participant"]["revision_deadline_at"]) == latest_deadline
    submit("revision-after-extension")


@pytest.mark.parametrize("is_final", [False, True])
@pytest.mark.parametrize("revision_days", [0, 7])
def test_transfer_after_approval_reopens_for_current_teacher(monkeypatch, is_final, revision_days):
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    successor, _, successor_id, _ = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"reopen-transfer-{suffix}", "name": "重开转交测试学校", "type": "school",
    }).json()
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "重开转交测试批次", "organization_id": organization["id"],
    }).json()
    member_path = f"/api/cohorts/{cohort['id']}/members"
    assert client.post(member_path, headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort['id']}/stages", headers=admin, json={
        "key": "paper", "name": "论文", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": is_final,
        "revision_days": revision_days,
    }).json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=admin).status_code == 200
    participant = client.get("/api/my-tasks", headers=student).json()[0]
    _prepare_project(student, participant["id"])
    assert client.post(f"/api/stage-participants/{participant['id']}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]}).json()
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate["id"], "idempotency_key": f"initial-{suffix}",
    }).json()["submission"]
    approved = client.post(f"/api/submissions/{submitted['id']}/reviews", headers=teacher, json={"decision": "approved"})
    assert approved.status_code == 200, approved.text
    if is_final:
        assert approved.json()["archive"]["job_id"] in run_once()["archives"]["succeeded"]
    store = Store()
    expired = store.get_workflow_record("participant", participant["id"])
    expired["effective_deadline_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert store.save_workflow_record("participant", expired, expected_revision=expired["revision"])

    waiting, proceed = Event(), Event()
    original_transaction = Store.workflow_transaction

    @contextmanager
    def delay_first_transaction(store):
        if not waiting.is_set():
            waiting.set()
            assert proceed.wait(timeout=10)
        with original_transaction(store) as conn:
            yield conn

    reopen_path = f"/api/stage-participants/{participant['id']}/reopen"
    with monkeypatch.context() as race:
        race.setattr(Store, "workflow_transaction", delay_first_transaction)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, reopen_path, headers=teacher, json={"reason": "需要修订"})
            try:
                assert waiting.wait(timeout=10)
                transferred = client.post(member_path, headers=admin, json={
                    "student_id": student_id, "teacher_id": successor_id, "reason": "导师调整",
                })
            finally:
                proceed.set()
            stale = pending.result(timeout=10)
    assert transferred.status_code == 201, transferred.text
    assert stale.status_code == 403, stale.text

    reopened = client.post(reopen_path, headers=successor, json={"reason": "需要修订"})
    if revision_days == 0:
        assert reopened.status_code == 409
        extended = client.put(f"/api/stage-participants/{participant['id']}/extension", headers=successor, json={
            "effective_deadline_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "reason": "安排修订",
        })
        assert extended.status_code == 200, extended.text
        reopened = client.post(reopen_path, headers=successor, json={"reason": "需要修订"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["teacher_id"] == successor_id
    deadline = datetime.fromisoformat(reopened.json()["revision_deadline_at"])
    assert deadline > datetime.now(timezone.utc)
    extension = deadline + timedelta(days=2)
    extended = client.put(f"/api/stage-participants/{participant['id']}/extension", headers=successor, json={
        "effective_deadline_at": extension.isoformat(), "reason": "增加修订时间",
    })
    assert extended.status_code == 200, extended.text
    assert datetime.fromisoformat(extended.json()["revision_deadline_at"]) == extension
    next_candidate = client.post("/api/submission-candidates", headers=student, json={"participant_id": participant["id"]})
    assert next_candidate.status_code == 201 and next_candidate.json()["status"] == "ready", next_candidate.text
    next_submission = client.post("/api/submissions", headers=student, json={
        "candidate_id": next_candidate.json()["id"], "idempotency_key": f"after-transfer-{suffix}",
    })
    assert next_submission.status_code == 200, next_submission.text
    next_id = next_submission.json()["submission"]["id"]
    events = client.get("/api/notifications", headers=successor).json()["items"]
    assert any(item.get("resource_id") == next_id and item.get("event_type") == "submission.created" for item in events)
    old_events = client.get("/api/notifications", headers=teacher).json()["items"]
    assert not any(item.get("resource_id") == next_id for item in old_events)


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
    other = register_fixture_response(client, other_name, other_password, display_name="版本范围学生")
    other_headers = _login(other_name, other_password)
    second_teacher_name = f"version_scope_teacher_{suffix}"
    second_teacher_password = _password("SecondTeacher")
    second_teacher = register_fixture_response(client, second_teacher_name, second_teacher_password,
                                               role="teacher", display_name="另一位指导教师")
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
    teacher = register_fixture_response(client, teacher_name, teacher_password, role="teacher", display_name="范围教师")
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
    student = register_fixture_response(client, student_name, student_password, display_name="范围学生")
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


def test_teachers_cannot_transfer_another_teachers_student():
    admin = _login("admin", "TestAdminPassword123")
    first, _, first_id, student_id = _create_people(admin)
    second, _, second_id, second_student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    school = client.post("/api/organizations", headers=admin, json={
        "id": f"workflow-teacher-boundary-{suffix}", "name": "教师指导范围测试", "type": "school",
    }).json()
    for teacher_id in (first_id, second_id):
        response = client.put(f"/api/organizations/users/{teacher_id}/context", headers=admin, json={
            "organization_context": {"school_id": school["id"]},
        })
        assert response.status_code == 200, response.text
    cohort = client.post("/api/cohorts", headers=first, json={
        "name": "指导关系测试", "organization_id": school["id"],
    }).json()
    path = f"/api/cohorts/{cohort['id']}/members"
    original = {"student_id": student_id, "teacher_id": first_id}
    assert client.post(path, headers=first, json=original).status_code == 201
    assert client.post(path, headers=first, json=original).status_code == 201

    transfer = {"student_id": student_id, "teacher_id": second_id}
    assert client.post(path, headers=second, json=transfer).status_code == 403
    assert client.post(path, headers=first, json=transfer).status_code == 403
    for dry_run in (True, False):
        result = client.post(path + "/import", headers=second, json={
            "dry_run": dry_run, "rows": [transfer],
        })
        assert result.status_code == 200, result.text
        assert result.json()["summary"]["failed"] == 1
        assert result.json()["summary"]["imported"] == 0
    assert client.get(path, headers=first).json()[0]["teacher_id"] == first_id

    authorized = client.post(path, headers=admin, json={**transfer, "reason": "管理员调整指导关系"})
    assert authorized.status_code == 201, authorized.text
    assert authorized.json()["teacher_id"] == second_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = [
            executor.submit(client.post, path, headers=headers,
                            json={"student_id": second_student_id, "teacher_id": teacher_id})
            for headers, teacher_id in ((first, first_id), (second, second_id))
        ]
        results = [attempt.result() for attempt in attempts]
    assert sorted(result.status_code for result in results) == [201, 403]
    relationships = [item for item in client.get(path, headers=admin).json()
                     if item["student_id"] == second_student_id and item.get("active")]
    assert len(relationships) == 1

    _, _, _, future_student_id = _create_people(admin)
    scheduled = client.post(path, headers=admin, json={
        "student_id": future_student_id, "teacher_id": first_id,
        "effective_from": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    })
    assert scheduled.status_code == 201, scheduled.text
    assert client.post(path, headers=second, json={
        "student_id": future_student_id, "teacher_id": second_id,
    }).status_code == 403


def test_supervising_teacher_can_extend_only_own_student_deadline():
    """指导教师可以给自己学生的阶段任务延期，但延不了别人指导的学生。"""
    admin = _login("admin", "TestAdminPassword123")
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]

    outsider_name = f"extension_outsider_{suffix}"
    outsider_password = _password("ExtensionOutsider")
    outsider_account = register_fixture_response(client, outsider_name, outsider_password, role="teacher", display_name="无关教师")
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


def test_docx_manuscript_import_previews_then_applies_with_revision_guard(tmp_path, monkeypatch):
    from app.core import feature_flags

    monkeypatch.setattr(feature_flags, "manuscript_import_enabled", lambda: True)
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
    rejected = client.post(f"/api/projects/{project['id']}/import-docx", headers=student, params={
        "apply": "true", "base_revision": preview.json()["project_revision"],
        "candidate_id": preview.json()["candidate_id"], "candidate_sha256": preview.json()["candidate_sha256"],
    })
    assert rejected.status_code == 409, rejected.text
    latest = client.get(f"/api/projects/{project['id']}", headers=student).json()
    assert latest["meta"]["title"] == "先保存的修改"
    refreshed = client.post(f"/api/projects/{project['id']}/import-docx", headers=student, files={
        "file": ("manuscript.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["candidate_id"] != preview.json()["candidate_id"]
    assert refreshed.json()["project_revision"] == latest["revision"]
    applied = client.post(f"/api/projects/{project['id']}/import-docx", headers=student, params={
        "apply": "true", "base_revision": refreshed.json()["project_revision"],
        "candidate_id": refreshed.json()["candidate_id"], "candidate_sha256": refreshed.json()["candidate_sha256"],
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
    registered = register_fixture_response(client, college_admin_name, college_admin_password, display_name="学院管理员")
    college_admin_id = registered.json()["id"]
    college_admin = closed_role_session(college_admin_id)

    for index, college_id in enumerate(colleges, start=1):
        teacher_name = f"multi_teacher_{index}_{suffix}"
        teacher_password = _password(f"MultiTeacher{index}")
        teacher = register_fixture_response(client, teacher_name, teacher_password,
                                            role="teacher", display_name=f"学院{index}导师")
        teacher_id = teacher.json()["id"]
        assert client.put(f"/api/auth/admin/users/{teacher_id}/role-status", headers=admin,
                          json={"role": "teacher", "status": "active"}).status_code == 200
        student_name = f"multi_student_{index}_{suffix}"
        student_password = _password(f"MultiStudent{index}")
        student = register_fixture_response(client, student_name, student_password,
                                            display_name=f"学院{index}学生")
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
    outsider = register_fixture_response(client, outsider_name, outsider_password,
                                         role="teacher", display_name="未指派教师")
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


def test_workflow_stage_cannot_select_a_blocked_template():
    from app.models.template import CoverBlock
    from app.services.projects import default_template

    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"admission-school-{suffix}", "name": "准入测试学校", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": f"准入测试批次 {suffix}", "organization_id": organization.json()["id"], "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text

    template = default_template().model_copy(update={
        "id": f"blocked-stage-{suffix}",
        "name": "不能分配给学生的模板",
        "cover_blocks": [CoverBlock(kind="table")],
    })
    Store().save_template(template)
    now = datetime.now(timezone.utc)
    response = client.post(f"/api/cohorts/{cohort.json()['id']}/stages", headers=admin, json={
        "key": "final", "name": "终稿", "template_id": template.id,
        "open_at": (now + timedelta(days=1)).isoformat(),
        "deadline_at": (now + timedelta(days=2)).isoformat(),
        "is_final": True,
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "template_admission_blocked"


def test_workflow_template_scope_uses_cohort_organization_ancestors(tmp_path):
    from fastapi import HTTPException
    from app.api.workflow import _ensure_workflow_template_access
    from app.models.organization import OrganizationUnit
    from app.services.projects import default_template

    store = Store(tmp_path / 'scope-ancestors.db')
    for unit in (
        OrganizationUnit(id='school-a', name='学校 A', type='school'),
        OrganizationUnit(id='college-a', name='学院 A', type='college', parent_id='school-a'),
        OrganizationUnit(id='school-b', name='学校 B', type='school'),
    ):
        store.save_organization_unit(unit)
    template = default_template().model_copy(update={
        'organization_context': {'school_id': 'school-a'},
        'admission_provenance': {'source': 'document_template_candidate'},
    })
    _ensure_workflow_template_access(store, {'id': 'batch-a', 'organization_id': 'college-a'}, template)
    with pytest.raises(HTTPException) as denied:
        _ensure_workflow_template_access(store, {'id': 'batch-b', 'organization_id': 'school-b'}, template)
    assert denied.value.status_code == 404


def test_workflow_template_scope_is_checked_for_stages_versions_and_binding():
    from app.services.projects import default_template

    admin = _login('admin', 'TestAdminPassword123')
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    store = Store()
    schools = []
    for name in ('scope-a', 'scope-b'):
        response = client.post('/api/organizations', headers=admin, json={
            'id': f'{name}-{suffix}', 'name': name, 'type': 'school',
        })
        assert response.status_code == 201, response.text
        schools.append(response.json()['id'])
    for user_id in (teacher_id, student_id):
        person = store.get_user(user_id)
        person['organization_context'] = {'school_id': schools[0]}
        store.save_user(person)
    cohort = client.post('/api/cohorts', headers=admin, json={
        'name': '范围验证批次', 'organization_id': schools[0],
    }).json()
    assert client.post(f"/api/cohorts/{cohort['id']}/members", headers=admin, json={
        'student_id': student_id, 'teacher_id': teacher_id,
    }).status_code == 201
    templates = []
    for index, school in enumerate(schools):
        template = default_template().model_copy(update={
            'id': f'scoped-{index}-{suffix}', 'organization_context': {'school_id': school},
            'admission_provenance': {'source': 'document_template_candidate'},
        })
        store.save_template(template)
        templates.append(template)
    now = datetime.now(timezone.utc)
    stage_data = {'key': 'final', 'name': '终稿', 'open_at': (now - timedelta(minutes=1)).isoformat(),
                  'deadline_at': (now + timedelta(days=1)).isoformat(), 'is_final': True}
    url = f"/api/cohorts/{cohort['id']}/stages"
    denied = client.post(url, headers=teacher, json={**stage_data, 'template_id': templates[1].id})
    assert denied.status_code == 404, denied.text
    denied_admin = client.post(url, headers=admin, json={**stage_data, 'template_id': templates[1].id})
    assert denied_admin.status_code == 404, denied_admin.text
    allowed = client.post(url, headers=teacher, json={**stage_data, 'template_id': templates[0].id})
    assert allowed.status_code == 201, allowed.text
    stage = allowed.json()
    assert client.post(f"/api/stages/{stage['id']}/publish", headers=teacher).status_code == 200
    denied_version = client.post(f"/api/stages/{stage['id']}/versions", headers=teacher, json={
        'reason': '测试模板范围', 'template_id': templates[1].id,
    })
    assert denied_version.status_code == 404, denied_version.text

    # Simulate an older published stage containing a foreign template ID.
    config = store.get_workflow_record('stage_config', stage['config_version_id'])
    config['template_id'] = templates[1].id
    assert store.save_workflow_record('stage_config', config, expected_revision=config['revision'])
    assert client.post(f"/api/stages/{stage['id']}/preview", headers=teacher).status_code == 404
    participant = client.get('/api/my-tasks', headers=student).json()[0]
    denied_bind = client.post(f"/api/stage-participants/{participant['id']}/project",
                              headers=student, json={'title': '范围验证'})
    assert denied_bind.status_code == 404, denied_bind.text
    assert not store.get_workflow_record('participant', participant['id']).get('project_id')
    config = store.get_workflow_record('stage_config', stage['config_version_id'])
    config['template_id'] = templates[0].id
    assert store.save_workflow_record('stage_config', config, expected_revision=config['revision'])
    person = store.get_user(student_id)
    person['organization_context'] = {}
    store.save_user(person)
    allowed_bind = client.post(f"/api/stage-participants/{participant['id']}/project",
                               headers=student, json={'title': '范围验证'})
    assert allowed_bind.status_code == 200, allowed_bind.text


def test_binding_existing_paper_persists_batch_scope_atomically(monkeypatch):
    admin = _login('admin', 'TestAdminPassword123')
    teacher, student, teacher_id, student_id = _create_people(admin)
    suffix = uuid.uuid4().hex[:8]
    organization = client.post('/api/organizations', headers=admin, json={
        'id': 'binding-school-' + suffix, 'name': '绑定测试学校', 'type': 'school',
    }).json()
    cohort = client.post('/api/cohorts', headers=admin, json={
        'name': '既有论文绑定', 'organization_id': organization['id'],
    }).json()
    assert client.post(f'/api/cohorts/{cohort["id"]}/members', headers=admin, json={
        'student_id': student_id, 'teacher_id': teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f'/api/cohorts/{cohort["id"]}/stages', headers=admin, json={
        'key': 'draft', 'name': '初稿', 'open_at': (now - timedelta(minutes=1)).isoformat(),
        'deadline_at': (now + timedelta(days=1)).isoformat(),
    }).json()
    assert client.post(f'/api/stages/{stage["id"]}/publish', headers=admin).status_code == 200
    participant = client.get('/api/my-tasks', headers=student).json()[0]
    project = client.post('/api/projects', headers=student, json={'template_id': 'default-gb7713', 'title': '已有草稿'}).json()
    before = Store().get_project(project['id'])
    # A failure after the project write must roll back both project and binding.
    from app.api import workflow as workflow_api
    original_record = workflow_api._record
    def fail_participant(store, kind, record, **kwargs):
        if kind == 'participant' and record.get('project_id') == project['id']:
            raise RuntimeError('synthetic transaction failure')
        return original_record(store, kind, record, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(workflow_api, '_record', fail_participant)
        failed_client = TestClient(app, raise_server_exceptions=False)
        assert failed_client.post(f'/api/stage-participants/{participant["id"]}/project', headers=student, json={'project_id': project['id']}).status_code == 500
    unchanged = Store().get_project(project['id'])
    assert unchanged.organization_context == before.organization_context
    assert unchanged.revision == before.revision
    assert Store().get_workflow_record('participant', participant['id']).get('project_id', '') == ''
    bound = client.post(f'/api/stage-participants/{participant["id"]}/project', headers=student, json={'project_id': project['id']})
    assert bound.status_code == 200, bound.text
    current = Store().get_project(project['id'])
    assert current.organization_context['batch_id'] == cohort['id']
    assert current.stage == 'draft'
    assert current.revision == before.revision + 1
    assert current.sections == before.sections
    assert client.get(f'/api/projects/{project["id"]}', headers=teacher).status_code == 200
    rebound = client.post(f'/api/stage-participants/{participant["id"]}/project', headers=student, json={'project_id': project['id']})
    assert rebound.status_code == 200
    assert Store().get_project(project['id']).revision == current.revision


def test_submission_candidate_blocks_invalid_heading_hierarchy_and_allows_repair(ready_submission_candidate):
    fixture = ready_submission_candidate
    participant = Store().get_workflow_record('participant', fixture['participant_id'])
    project_id = participant['project_id']
    project = client.get(f'/api/projects/{project_id}', headers=fixture['student']).json()
    sections = [{key: item[key] for key in ('key', 'type', 'title', 'required')}
                for item in project['sections']]
    next(item for item in sections if item['key'] == 'ch1_1')['type'] = 'heading3'
    saved = client.put(f'/api/projects/{project_id}/sections', headers=fixture['student'], json={
        'base_revision': project['revision'], 'sections': sections,
    })
    assert saved.status_code == 200, saved.text
    blocked = client.post('/api/submission-candidates', headers=fixture['student'], json={
        'participant_id': fixture['participant_id'],
    })
    assert blocked.status_code == 201, blocked.text
    assert blocked.json()['status'] == 'blocked'
    completeness = next(gate for gate in blocked.json()['gates'] if gate['key'] == 'completeness')
    assert any('三级标题' in issue for issue in completeness['detail'])
    next(item for item in sections if item['key'] == 'ch1_1')['type'] = 'heading2'
    repaired = client.put(f'/api/projects/{project_id}/sections', headers=fixture['student'], json={
        'base_revision': saved.json()['revision'], 'sections': sections,
    })
    assert repaired.status_code == 200, repaired.text
    ready = client.post('/api/submission-candidates', headers=fixture['student'], json={
        'participant_id': fixture['participant_id'],
    })
    assert ready.status_code == 201 and ready.json()['status'] == 'ready', ready.text


@pytest.mark.parametrize('ready_submission_candidate', ['m1-pdf-disabled'], indirect=True)
def test_old_submission_and_archive_recheck_heading_hierarchy(ready_submission_candidate, monkeypatch):
    from app.api import workflow as workflow_api

    fixture = ready_submission_candidate
    participant = Store().get_workflow_record('participant', fixture['participant_id'])
    project_id = participant['project_id']
    project = client.get(f'/api/projects/{project_id}', headers=fixture['student']).json()
    sections = [{key: item[key] for key in ('key', 'type', 'title', 'required')}
                for item in project['sections']]
    next(item for item in sections if item['key'] == 'ch1_1')['type'] = 'heading3'
    saved = client.put(f'/api/projects/{project_id}/sections', headers=fixture['student'], json={
        'base_revision': project['revision'], 'sections': sections,
    })
    assert saved.status_code == 200, saved.text
    with monkeypatch.context() as patch:
        patch.setattr(workflow_api, 'validate_project_completeness', lambda *_args: [])
        patch.setattr(workflow_api, 'validate_heading_hierarchy', lambda _sections: [])
        candidate = client.post('/api/submission-candidates', headers=fixture['student'], json={
            'participant_id': fixture['participant_id'],
        })
        assert candidate.status_code == 201 and candidate.json()['status'] == 'ready', candidate.text
        submitted = client.post('/api/submissions', headers=fixture['student'], json={
            'candidate_id': candidate.json()['id'], 'idempotency_key': 'old-heading-test',
        })
        assert submitted.status_code == 200, submitted.text
        submission_id = submitted.json()['submission']['id']
        approved = client.post(f'/api/submissions/{submission_id}/reviews', headers=fixture['teacher'], json={
            'decision': 'approved',
        })
        assert approved.status_code == 200, approved.text
        archive_id = approved.json()['archive']['id']
        run_once()
        assert client.get(f'/api/archives/{archive_id}/download', headers=fixture['student']).status_code == 200
    assert client.get(f'/api/submissions/{submission_id}/document', headers=fixture['student']).status_code == 409
    assert client.get(f'/api/archives/{archive_id}/download', headers=fixture['student']).status_code == 409


def test_submission_candidate_checks_stage_and_frozen_template_rule_packs(ready_submission_candidate):
    fixture = ready_submission_candidate
    store = Store()
    participant = store.get_workflow_record('participant', fixture['participant_id'])
    config = store.get_workflow_record('stage_config', participant['config_version_id'])
    config['rule_packs'] = ['SIM-UNIV-generic']
    store.save_workflow_record('stage_config', config, expected_revision=config['revision'])

    response = client.post('/api/submission-candidates', headers=fixture['student'],
                           json={'participant_id': fixture['participant_id']})
    assert response.status_code == 201, response.text
    report = response.json()['check_report']
    packs = {source['id'] for source in report['sources'] if source['source_type'] == 'rule_pack'}
    assert {'GB-T-7713-1-2006', 'SIM-UNIV-generic'} <= packs


def test_run_archive_with_large_manifest_succeeds_in_default_rollback_journal_mode(
    ready_submission_candidate, tmp_path, monkeypatch,
):
    from app.api import workflow as workflow_api
    from app.services.docx_output_check import output_check_identity
    from app.services.workflow_jobs import consume_archive_jobs

    fixture = ready_submission_candidate
    archive = _approved_archive_from_candidate(fixture)
    store = Store()
    with store._connect() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() != "wal"

    large_checks = [
        {
            "rule": "master.effective_format",
            "status": "pass",
            "object": f"block-{i}",
            "unit": f"/w:document/w:body/w:p[{i}]#font.size",
            "expected": {"value": 12.0, "unit": "pt"},
            "actual": {"value": 12.0, "unit": "pt"},
            "padding": "x" * 400,
        }
        for i in range(6000)
    ]

    real_require_docx_output_pass = workflow_api.require_docx_output_pass

    def large_output_check(data, *args, **kwargs):
        report = real_require_docx_output_pass(data, *args, **kwargs)
        if kwargs.get("context_kind") == "workflow-archive":
            report = dict(report)
            report["checks"] = [*report.get("checks", []), *large_checks]
            report["check_count"] = len(report["checks"])
            report["passed_count"] = sum(item.get("status") == "pass" for item in report["checks"])
        return report

    monkeypatch.setattr(workflow_api, "require_docx_output_pass", large_output_check)
    result = consume_archive_jobs(store, consumer_id="non-wal-large-manifest", limit=50)
    assert archive["job_id"] in result["succeeded"], result
    saved_archive = store.get_workflow_record("archive", archive["id"])
    assert saved_archive["status"] == "succeeded"
    assert saved_archive["manifest"]["output_check_identity"] == output_check_identity(
        saved_archive["manifest"]["output_check"]
    )
    participant = store.get_workflow_record("participant", fixture["participant_id"])
    assert participant["state"] == "archived"
    assert participant["archive_id"] == archive["id"]
