"""多层级规范 P0 API 与项目快照回归。"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._helpers import register_fixture
from app.services import emailer


client = TestClient(app)


def _random_password(prefix: str) -> str:
    """随机生成符合策略(≥12 位、含字母数字)的测试口令, 避免静态凭据。"""
    return f"{prefix}-{uuid.uuid4().hex}-A1"


def _admin_headers() -> dict[str, str]:
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "TestAdminPassword123"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_policy_publish_snapshot_and_unchecked_items():
    headers = _admin_headers()
    policy_id = f"policy-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/policies",
        headers=headers,
        json={
            "id": policy_id,
            "name": "测试学校论文规范",
            "level": "school",
            "scope_id": f"template-only-{policy_id}",
            "status": "draft",
            "version": "2026.1",
            "items": [
                {
                    "id": "abstract-min",
                    "type": "auto",
                    "target": "abstract",
                    "checker": "abstract.word_count",
                    "params": {"min": 10},
                    "severity": "error",
                    "enforce": "hard",
                    "description": "中文摘要不少于 10 字",
                },
                {
                    "id": "manual-method",
                    "type": "manual",
                    "target": "document",
                    "severity": "warning",
                    "enforce": "soft",
                    "description": "人工确认研究方法是否充分",
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert client.post(f"/api/policies/{policy_id}/publish", headers=headers).status_code == 200

    template = client.get("/api/templates/default-gb7713", headers=headers).json()
    template_id = f"tpl-policy-{uuid.uuid4().hex[:8]}"
    template.update({
        "id": template_id,
        "name": "规范快照测试模板",
        "requirement_set_ids": [policy_id],
    })
    created_template = client.post("/api/templates", headers=headers, json=template)
    assert created_template.status_code == 201, created_template.text

    project = client.post(
        "/api/projects",
        headers=headers,
        json={"template_id": template_id, "title": "规范快照测试论文"},
    )
    assert project.status_code == 201, project.text
    project_data = project.json()
    snapshot = project_data["requirement_snapshot"]
    assert snapshot["policy_sets"][0]["version"] == "2026.1"
    assert snapshot["resolved_items"]

    stale_ack = client.post(
        f"/api/projects/{project_data['id']}/requirements/acknowledge",
        headers=headers, json={"snapshot_hash": "sha256:stale"},
    )
    assert stale_ack.status_code == 409
    acknowledged = client.post(
        f"/api/projects/{project_data['id']}/requirements/acknowledge",
        headers=headers, json={"snapshot_hash": snapshot["content_hash"]},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["snapshot_hash"] == snapshot["content_hash"]

    report = client.get(
        f"/api/projects/{project_data['id']}/check", headers=headers
    )
    assert report.status_code == 200, report.text
    report_data = report.json()
    assert any(item["rule_id"] == "REQ-abstract-min" for item in report_data["findings"])
    assert any(item["id"] == "manual-method" for item in report_data["not_checked"])
    assert report_data["summary"]["hard_failures"] >= 1
    assert report_data["context"]["requirement_snapshot_hash"].startswith("sha256:")

    blocked = client.put(
        f"/api/policies/{policy_id}",
        headers=headers,
        json={
            "id": policy_id,
            "name": "不允许修改已发布版本",
            "level": "school",
            "status": "published",
            "version": "2026.1",
            "items": [],
        },
    )
    assert blocked.status_code == 409


def test_student_cannot_manage_school_policy():
    headers = _admin_headers()
    username = f"policy-student-{uuid.uuid4().hex[:8]}"
    password = _random_password("student")
    register = register_fixture(client, username, password, display_name="规范学生")
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    student_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    denied = client.post(
        "/api/policies",
        headers=student_headers,
        json={"name": "不应创建", "level": "school"},
    )
    assert denied.status_code == 403
    assert client.get("/api/policies", headers=student_headers).status_code == 200


def test_teacher_requirement_is_frozen_for_guided_student(monkeypatch):
    admin_headers = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    teacher_username = f"policy-teacher-{suffix}"
    teacher_password = _random_password("teacher")
    registered = register_fixture(client, teacher_username, teacher_password, role="teacher", display_name="规范导师")
    teacher_id = registered["id"]
    approved = client.put(
        f"/api/auth/admin/users/{teacher_id}/role-status",
        headers=admin_headers,
        json={"role": "teacher", "status": "active"},
    )
    assert approved.status_code == 200, approved.text
    teacher_login = client.post(
        "/api/auth/login",
        json={"username": teacher_username, "password": teacher_password},
    )
    teacher_headers = {"Authorization": f"Bearer {teacher_login.json()['token']}"}

    policy = client.post(
        "/api/policies",
        headers=teacher_headers,
        json={
            "name": "导师额外要求",
            "level": "teacher",
            "status": "published",
            "version": "1",
            "items": [{
                "id": "teacher-abstract-min",
                "type": "auto",
                "target": "abstract",
                "checker": "abstract.word_count",
                "params": {"min": 20},
                "severity": "warning",
                "enforce": "soft",
                "description": "摘要不少于 20 字",
            }],
        },
    )
    assert policy.status_code == 201, policy.text
    assert policy.json()["owner_id"] == teacher_id

    student_username = f"policy-student-{suffix}"
    student_password = _random_password("student")
    imported = client.post(
        "/api/teacher/students/import",
        headers=teacher_headers,
        json={
            "students": [{"username": student_username, "display_name": "被指导学生"}],
            "default_password": student_password,
        },
    )
    assert imported.status_code == 200, imported.text
    student_login = client.post(
        "/api/auth/login",
        json={"username": student_username, "password": student_password},
    )
    student_headers = {"Authorization": f"Bearer {student_login.json()['token']}"}
    email = f"{student_username}@example.edu"
    code_box: dict[str, str] = {}
    monkeypatch.setattr(emailer, "email_configured", lambda: True)
    monkeypatch.setattr(emailer, "send_verification_code", lambda _to, code, _minutes=10: code_box.update(code=code))
    code_response = client.post(
        "/api/auth/email/code",
        headers=student_headers,
        json={"email": email, "purpose": "first_login"},
    )
    assert code_response.status_code == 200, code_response.text
    completed = client.post(
        "/api/auth/first-login/complete",
        headers=student_headers,
        json={
            "email": email,
            "email_code": code_box["code"],
            "new_password": f"Updated-{uuid.uuid4().hex}-A1",
        },
    )
    assert completed.status_code == 200, completed.text
    student_headers = {"Authorization": f"Bearer {completed.json()['token']}"}
    project = client.post(
        "/api/projects",
        headers=student_headers,
        json={"template_id": "default-gb7713", "title": "教师规范论文"},
    )
    assert project.status_code == 201, project.text
    snapshot = project.json()["requirement_snapshot"]
    assert any(item["id"] == policy.json()["id"] for item in snapshot["policy_sets"])
