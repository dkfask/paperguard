"""审计中心的权限、组织范围和内容边界回归。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api import audit as audit_api
from app.main import app
from app.db import Store


client = TestClient(app)


def _password(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}-8a"


def _login(username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_audit_store_does_not_seed_templates(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_DB_PATH", str(tmp_path / "audit-read.db"))
    store = audit_api.get_store()

    assert store.list_templates() == []


def test_audit_center_filters_and_request_correlation():
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    organization_id = f"audit-school-{suffix}"
    created = client.post("/api/organizations", headers=admin, json={
        "id": organization_id, "name": "审计测试学校", "type": "school",
    })
    assert created.status_code == 201, created.text
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "审计测试批次", "organization_id": organization_id, "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text
    cohort_id = cohort.json()["id"]

    response = client.get("/api/audit", headers=admin, params={
        "resource_type": "cohort", "action": "cohort.create", "organization_id": organization_id,
    })
    assert response.status_code == 200, response.text
    rows = response.json()
    event = next(item for item in rows if item["resource_id"] == cohort_id)
    assert event["source"] == "workflow"
    assert event["actor_id"] == "user-admin"
    assert event["actor_display_name"] == "管理员"
    assert event["request_id"].startswith("req-")
    assert "content" not in event and "body" not in event and "data" not in event

    exported = client.get("/api/audit/export", headers=admin, params={
        "resource_type": "cohort", "action": "cohort.create", "organization_id": organization_id,
    })
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    csv_text = exported.content.decode("utf-8-sig")
    assert cohort_id in csv_text
    assert "content" not in csv_text and "document_body" not in csv_text
    with Store()._connect() as conn:
        assert conn.execute("SELECT 1 FROM audit_events WHERE id = ?", (event["id"],)).fetchone()
        assert conn.execute("SELECT 1 FROM workflow_records WHERE kind = 'audit' AND id = ?", (event["id"],)).fetchone() is None

    student_password = _password("AuditStudent")
    registered = client.post("/api/auth/register", json={
        "username": f"audit_student_{suffix}", "password": student_password, "role": "student",
    })
    assert registered.status_code == 201
    student = _login(registered.json()["username"], student_password)
    denied = client.get("/api/audit", headers=student)
    assert denied.status_code == 403


def test_audit_center_respects_college_admin_scope():
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    school_id = f"audit-scope-school-{suffix}"
    college_ids = [f"audit-scope-college-{index}-{suffix}" for index in (1, 2)]
    assert client.post("/api/organizations", headers=admin, json={
        "id": school_id, "name": "审计范围学校", "type": "school",
    }).status_code == 201
    for index, college_id in enumerate(college_ids, start=1):
        assert client.post("/api/organizations", headers=admin, json={
            "id": college_id, "name": f"审计范围学院{index}", "type": "college", "parent_id": school_id,
        }).status_code == 201

    name = f"audit_college_admin_{suffix}"
    password = _password("AuditCollegeAdmin")
    registered = client.post("/api/auth/register", json={
        "username": name, "password": password, "role": "student", "display_name": "审计学院管理员",
    })
    assert registered.status_code == 201
    user_id = registered.json()["id"]
    assert client.put(f"/api/auth/admin/users/{user_id}/role-status", headers=admin, json={
        "role": "college_admin", "status": "active",
    }).status_code == 200
    assert client.put(f"/api/organizations/users/{user_id}/context", headers=admin, json={
        "organization_context": {"school_id": school_id, "college_id": college_ids[0]},
    }).status_code == 200
    college_admin = _login(name, password)

    for index, college_id in enumerate(college_ids, start=1):
        created = client.post("/api/cohorts", headers=admin, json={
            "name": f"范围审计批次{index}", "organization_id": college_id, "academic_year": "2027",
        })
        assert created.status_code == 201, created.text

    response = client.get("/api/audit", headers=college_admin, params={"resource_type": "cohort"})
    assert response.status_code == 200, response.text
    organization_ids = {item["organization_id"] for item in response.json()}
    assert college_ids[0] in organization_ids
    assert college_ids[1] not in organization_ids
