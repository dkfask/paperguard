"""Role rollout tests use disposable accounts and real authentication guards."""

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest

from app import auth
from app.db import Store
from app.main import app


client = TestClient(app)
PASSWORD = "PilotFixturePassword123"


def account(role="student", **fields):
    store = Store()
    user_id = "pilot-" + uuid.uuid4().hex
    password_hash, salt = auth.hash_password(PASSWORD)
    user = store.save_user({
        "id": user_id, "username": user_id, "role": role, "status": "active",
        "password_hash": password_hash, "salt": salt, "display_name": "Pilot fixture",
        "created_at": datetime.now(timezone.utc).isoformat(), **fields,
    })
    return store, user


def headers(user):
    login = client.post("/api/auth/login", json={"username": user["username"], "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["token"]}


def error_code(response):
    payload = response.json()
    return (payload.get("error") or payload.get("detail"))["code"]


@pytest.mark.parametrize("role", ["college_admin", "reviewer", "platform_admin", "unknown"])
def test_closed_roles_cannot_login_or_keep_using_old_sessions(role):
    store, user = account()
    old_session = headers(user)
    store.update_user_role_and_status(user["id"], role, "active")
    login = client.post("/api/auth/login", json={"username": user["username"], "password": PASSWORD})
    assert login.status_code == 403 and login.json()["detail"]["code"] == "ROLE_NOT_OPEN"
    for path in ["/api/auth/me", "/api/projects", "/api/organizations", "/api/audit",
                 "/api/document-projects", "/api/document-packages", "/api/process-records/catalog",
                 "/api/saas/v1/context", "/api/operations/retention", "/media/uploads/unknown.png"]:
        response = client.get(path, headers=old_session)
        assert response.status_code == 403, (path, response.text)
        assert error_code(response) == "ROLE_NOT_OPEN", path
    write = client.post("/api/projects", headers=old_session, json={"template_id": "default-gb7713"})
    assert write.status_code == 403
    assert store.get_user(user["id"])["role"] == role


@pytest.mark.parametrize("state", ["unselected", "pending", "rejected", "approved"])
def test_social_and_uncertified_users_are_limited_to_certification(state):
    _, user = account("user", role_application_status=state)
    session = headers(user)
    assert client.get("/api/auth/me", headers=session).status_code == 200
    assert client.get("/api/auth/role-certification", headers=session).status_code == 200
    for role in ["user", "college_admin", "admin"]:
        assert client.post("/api/auth/role-certification", headers=session, json={"role": role}).status_code == 422
    for path in ["/api/projects", "/api/templates", "/api/document-projects", "/api/document-packages",
                 "/api/saas/v1/context", "/api/ai/config"]:
        response = client.get(path, headers=session)
        assert response.status_code == 403, (path, response.text)
        assert error_code(response) == "ROLE_CERTIFICATION_REQUIRED"
    assert client.post("/api/auth/logout", headers=session).status_code == 200


@pytest.mark.parametrize("role", ["admin", "teacher", "student"])
def test_open_roles_retain_thesis_and_generic_document_access(role):
    _, user = account(role)
    session = headers(user)
    for path in ["/api/auth/me", "/api/projects", "/api/templates", "/api/document-projects", "/api/document-packages"]:
        response = client.get(path, headers=session)
        assert response.status_code == 200, (path, response.text)


def test_admin_cannot_assign_closed_roles_but_can_reopen_an_existing_account(closed_role_session):
    _, administrator = account("admin")
    admin = headers(administrator)
    store, user = account()
    old_session = closed_role_session(user["id"])
    for role in ["college_admin", "user", "platform_admin", "unknown"]:
        response = client.put(f"/api/auth/admin/users/{user['id']}/role-status", headers=admin,
                              json={"role": role, "status": "active"})
        assert response.status_code == 422
        assert store.get_user(user["id"])["role"] == "college_admin"
    response = client.put(f"/api/auth/admin/users/{user['id']}/role-status", headers=admin,
                          json={"role": "teacher", "status": "active"})
    assert response.status_code == 200
    assert client.get("/api/projects", headers=old_session).status_code == 200


@pytest.mark.parametrize("role", ["college_admin", "user"])
def test_closed_membership_role_cannot_bypass_global_role_admission(role):
    store, user = account()
    session = headers(user)
    tenant_id = "pilot-tenant-" + uuid.uuid4().hex
    store.create_tenant({"id": tenant_id, "slug": tenant_id, "name": "Local pilot"})
    store.save_membership({"id": "member-" + uuid.uuid4().hex, "tenant_id": tenant_id,
                           "user_id": user["id"], "role": role, "capabilities": ["project:read"]})
    response = client.get("/api/saas/v1/context", headers=session)
    assert response.status_code == 403


def test_college_admin_cannot_create_a_mini_program_binding(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "LocalPilotBindingFixture123")
    store, user = account("college_admin")
    ticket = auth.mini_program_login(store, "local-pilot-openid")["bind_ticket"]
    with pytest.raises(HTTPException) as error:
        auth.mini_program_bind(store, ticket, user["username"], PASSWORD)
    assert error.value.status_code == 403
    assert store.get_mini_program_binding_by_openid(auth._mini_program_openid_hash("local-pilot-openid")) is None
