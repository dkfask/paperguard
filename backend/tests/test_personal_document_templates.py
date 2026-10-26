"""Personal writing is separate from student thesis-template publication."""

from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient

from app.db import Store
from app.main import app
from app.services.document_access import document_package_visible

FIXTURE = Path(__file__).parent / "fixtures/templates/admissible.docx"
SPEC = """---
name: Personal writing standard
version: '1'
rules:
  - id: body-min
    type: auto
    target: body
    checker: word_count
    params: {min: 1}
    description: Body is required
---
# Personal writing standard
"""


def account(client, admin, role, context):
    username = "personal-" + uuid.uuid4().hex
    password = "Local-" + uuid.uuid4().hex + "-A1"
    registered = client.post("/api/auth/register", json={"username": username, "password": password, "role": "student"})
    assert registered.status_code == 201
    user_id = registered.json()["id"]
    activated = client.put(f"/api/auth/admin/users/{user_id}/role-status", headers=admin, json={"role": role, "status": "active"})
    assert activated.status_code == 200
    store = Store()
    user = store.get_user(user_id)
    user["organization_context"] = context
    store.save_user(user)
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    return {"Authorization": "Bearer " + login.json()["token"]}, user_id


def test_teacher_personal_template_and_candidates_are_owner_only(closed_role_session):
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    admin = {"Authorization": "Bearer " + login.json()["token"]}
    context = {"school_id": "personal-" + uuid.uuid4().hex}
    owner, owner_id = account(client, admin, "teacher", context)
    parsed = client.post("/api/templates/parse", headers=owner, files={"file": ("personal.docx", FIXTURE.read_bytes())})
    assert parsed.status_code == 200
    confirmed = client.post("/api/templates/parse/confirm", headers=owner, json=parsed.json())
    assert confirmed.status_code == 200
    spec = client.post("/api/specs/parse", headers=owner, files={"file": ("personal.md", SPEC.encode(), "text/markdown")})
    assert spec.status_code == 200
    confirmed_spec = client.post("/api/specs/parse/confirm", headers=owner, json=spec.json())
    assert confirmed_spec.status_code == 200
    saved = client.post("/api/document-packages", headers=owner, json={
        "template_candidate": confirmed.json(), "spec_candidate": confirmed_spec.json(), "document_type_id": "grant-application",
        "usage": "shared", "metadata": {"usage": "shared", "owner_id": "forged", "organization_context": {}},
    })
    assert saved.status_code == 201
    package = saved.json()["package"]
    assert package["metadata"]["usage"] == "personal"
    assert package["published_by"] == owner_id
    assert package["metadata"]["organization_context"] == context
    package_id = package["id"]
    assert client.get(f"/api/document-packages/{package_id}", headers=owner).status_code == 200
    created = client.post("/api/document-projects", headers=owner, json={"package_id": package_id, "title": "本人申报书"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert client.get(f"/api/document-projects/{project_id}/editor", headers=owner).status_code == 200

    for role in ["student", "teacher", "college_admin", "admin"]:
        other, other_id = account(client, admin, "student" if role == "college_admin" else role, context)
        if role == "college_admin":
            other = closed_role_session(other_id)
            assert client.get("/api/document-packages", headers=other).status_code == 403
            assert client.get(f"/api/document-packages/{package_id}", headers=other).status_code == 403
            assert client.post("/api/document-projects", headers=other, json={"package_id": package_id}).status_code == 403
            continue
        listed = client.get("/api/document-packages", headers=other)
        assert listed.status_code == 200
        assert all(item["package"]["id"] != package_id for item in listed.json())
        assert client.get(f"/api/document-packages/{package_id}", headers=other).status_code == 404
        assert client.post("/api/document-projects", headers=other, json={"package_id": package_id}).status_code == 404
        if role == "teacher":
            assert client.get(f"/api/document-projects/{project_id}/editor", headers=other).status_code == 404
        if role != "student":
            candidate_id = parsed.json()["candidate_id"]
            candidates = client.get("/api/templates/parse/candidates", headers=other)
            assert candidates.status_code == 200
            assert all(item["candidate_id"] != candidate_id for item in candidates.json())
            assert client.get(f"/api/templates/parse/candidates/{candidate_id}", headers=other).status_code == 404
            assert client.post("/api/templates/parse/confirm", headers=other, json=confirmed.json()).status_code == 404
            spec_id = spec.json()["candidate_id"]
            specs = client.get("/api/specs/parse/candidates", headers=other)
            assert specs.status_code == 200
            assert all(item["candidate_id"] != spec_id for item in specs.json())
            assert client.get(f"/api/specs/parse/candidates/{spec_id}", headers=other).status_code == 404
            assert client.post("/api/specs/parse/confirm", headers=other, json=confirmed_spec.json()).status_code == 404
    assert client.get(f"/api/document-packages/{package_id}").status_code == 401
    assert client.get("/api/document-packages/unknown", headers=owner).status_code == 404


def test_existing_shared_packages_keep_their_scope_and_private_owner_is_not_client_metadata(tmp_path):
    store = Store(tmp_path / "access.db")
    shared = SimpleNamespace(metadata={"organization_context": {"school_id": "school-1"}}, published_by="teacher-1")
    student = {"id": "student-1", "role": "student", "organization_context": {"school_id": "school-1"}}
    assert document_package_visible(store, student, shared)
    assert not document_package_visible(store, {**student, "organization_context": {"school_id": "school-2"}}, shared)
    personal = SimpleNamespace(metadata={"usage": "personal", "owner_id": "student-1"}, published_by="teacher-1")
    assert not document_package_visible(store, student, personal)
    assert document_package_visible(store, {"id": "teacher-1", "role": "teacher"}, personal)
    personal.published_by = ""
    assert not document_package_visible(store, {"id": "", "role": "admin"}, personal)
