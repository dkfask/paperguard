"""Phase 5 dynamic document project API tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "minimal.docx"
SPEC = """---
name: 通用实验报告规范
version: '1'
rules:
  - id: body-min
    type: auto
    target: body
    checker: word_count
    params: {min: 10}
    description: 正文不少于 10 字
---
# 通用实验报告规范
"""


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _published_package(client: TestClient, headers: dict[str, str]) -> dict:
    template = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("minimal.docx", FIXTURE.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert template.status_code == 200, template.text
    template_confirmed = client.post("/api/templates/parse/confirm", headers=headers, json=template.json())
    assert template_confirmed.status_code == 200, template_confirmed.text
    spec = client.post("/api/specs/parse", headers=headers, files={"file": ("spec.md", SPEC.encode(), "text/markdown")})
    assert spec.status_code == 200, spec.text
    spec_confirmed = client.post("/api/specs/parse/confirm", headers=headers, json=spec.json())
    assert spec_confirmed.status_code == 200, spec_confirmed.text
    package = client.post(
        "/api/document-packages",
        headers=headers,
        json={
            "template_candidate": template_confirmed.json(),
            "spec_candidate": spec_confirmed.json(),
            "document_type_id": "lab-report",
        },
    )
    assert package.status_code == 201, package.text
    return package.json()["package"]


def test_dynamic_project_schema_autosave_and_revision_conflict() -> None:
    client = TestClient(app)
    headers = _admin_headers(client)
    package = _published_package(client, headers)

    created = client.post(
        "/api/document-projects",
        headers=headers,
        json={"package_id": package["id"], "title": "第一次实验报告"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["package_id"] == package["id"]
    assert project["revision"] == 0

    editor = client.get(f"/api/document-projects/{project['id']}/editor", headers=headers)
    assert editor.status_code == 200, editor.text
    editor_body = editor.json()
    assert editor_body["editor_schema"]["sections"]
    assert editor_body["editor_schema"]["fields"] == []
    assert set(editor_body["project"]["sections"]) == {
        section["key"] for section in editor_body["editor_schema"]["sections"]
        if section["kind"] not in {"cover", "toc"}
    }

    sections = editor_body["project"]["sections"]
    first_key = next(iter(sections))
    sections[first_key][0]["text"] = "已自动保存的正文内容"
    updated = client.put(
        f"/api/document-projects/{project['id']}",
        headers=headers,
        json={"sections": sections, "base_revision": 0},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 1
    assert updated.json()["sections"][first_key][0]["text"] == "已自动保存的正文内容"

    stale = client.put(
        f"/api/document-projects/{project['id']}",
        headers=headers,
        json={"title": "旧客户端覆盖", "base_revision": 0},
    )
    assert stale.status_code == 409, stale.text

    unknown_field = client.post(
        "/api/document-projects",
        headers=headers,
        json={"package_id": package["id"], "field_values": {"not_in_template": "x"}},
    )
    assert unknown_field.status_code == 422, unknown_field.text


def test_document_project_is_owner_scoped_and_package_reference_is_frozen() -> None:
    client = TestClient(app)
    headers = _admin_headers(client)
    package = _published_package(client, headers)
    created = client.post("/api/document-projects", headers=headers, json={"package_id": package["id"]})
    assert created.status_code == 201
    project = created.json()

    unauthenticated = client.get(f"/api/document-projects/{project['id']}")
    assert unauthenticated.status_code == 401

    listed = client.get("/api/document-projects", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == project["id"] for item in listed.json())
    assert project["template_version_id"] == package["template_version_id"]
    assert project["spec_version_id"] == package["spec_version_id"]
    assert project["renderer_version"] == package["renderer_version"]
    assert project["rule_engine_version"] == package["rule_engine_version"]


def test_document_project_validation_is_deterministic_and_persisted() -> None:
    client = TestClient(app)
    headers = _admin_headers(client)
    package = _published_package(client, headers)
    created = client.post(
        "/api/document-projects",
        headers=headers,
        json={"package_id": package["id"], "title": "待检查报告"},
    )
    assert created.status_code == 201, created.text
    project = created.json()

    first = client.post(f"/api/document-projects/{project['id']}/validate", headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["project_revision"] == 0
    assert first_body["summary"]["total"] == 1
    assert first_body["findings"][0]["rule_id"] == "body-min"

    sections = project["sections"]
    first_key = next(iter(sections))
    sections[first_key][0]["text"] = "这是一段满足最小长度要求的正文内容"
    updated = client.put(
        f"/api/document-projects/{project['id']}",
        headers=headers,
        json={"sections": sections, "base_revision": 0},
    )
    assert updated.status_code == 200, updated.text

    second = client.post(f"/api/document-projects/{project['id']}/validate", headers=headers)
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["project_revision"] == 1
    assert second_body["summary"]["total"] == 0
    assert second_body["content_hash"] != first_body["content_hash"]

    history = client.get(f"/api/document-projects/{project['id']}/validations", headers=headers)
    assert history.status_code == 200, history.text
    assert {item["id"] for item in history.json()} == {second_body["id"], first_body["id"]}
    detail = client.get(
        f"/api/document-projects/{project['id']}/validations/{first_body['id']}",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text


def test_document_project_render_returns_docx_artifact() -> None:
    client = TestClient(app)
    headers = _admin_headers(client)
    package = _published_package(client, headers)
    created = client.post(
        "/api/document-projects",
        headers=headers,
        json={"package_id": package["id"], "title": "导出实验报告"},
    )
    assert created.status_code == 201, created.text

    response = client.post(f"/api/document-projects/{created.json()['id']}/render", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert response.content[:2] == b"PK"
