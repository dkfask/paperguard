"""Phase 5 dynamic document project API tests."""

from __future__ import annotations

import base64
from pathlib import Path
import uuid

from fastapi.testclient import TestClient

from app.db import Store
from app.domain.documents.schemas import DocumentPackage, TemplateDefinition, TemplateSection
from app.main import app
from app.services.document_assets import document_upload_dir
from app.services.document_project_engine import instantiate_document_project
from tests._helpers import register_fixture


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "admissible.docx"
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

HARD_SPEC = SPEC.replace(
    "    description: 正文不少于 10 字",
    "    severity: error\n    enforce: hard\n    description: 正文不少于 10 字",
)

ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _random_password(prefix: str) -> str:
    """随机生成符合策略(≥12 位、含字母数字)的测试口令, 避免静态凭据。"""
    return f"{prefix}-{uuid.uuid4().hex}-A1"


def _published_package(client: TestClient, headers: dict[str, str], spec_content: str = SPEC) -> dict:
    template = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("minimal.docx", FIXTURE.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert template.status_code == 200, template.text
    template_confirmed = client.post("/api/templates/parse/confirm", headers=headers, json=template.json())
    assert template_confirmed.status_code == 200, template_confirmed.text
    spec = client.post("/api/specs/parse", headers=headers, files={"file": ("spec.md", spec_content.encode(), "text/markdown")})
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


def test_form_table_metadata_becomes_editable_table_blocks() -> None:
    definition = TemplateDefinition(
        sections=[TemplateSection(key="form", title="一、基本信息")],
        extensions={
            "source_document": {
                "tables": [{
                    "index": 1,
                    "rows": 2,
                    "cols": 2,
                    "section_title": "一、基本信息",
                    "cells": [["课题名称", ""], ["负责人", ""]],
                    "has_merged_cells": True,
                }],
            },
        },
    )
    package = DocumentPackage(
        id="package:form-table",
        document_type_id="application-form",
        template_version_id="template:form@1",
        spec_version_id="no-spec:form@1",
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
    )

    project = instantiate_document_project(
        package,
        definition,
        project_id="project:form-table",
        owner_id="user-admin",
        title="表格申报书",
        field_values={},
        organization_context={},
    )

    block = project.sections["form"][0]
    assert block.kind == "table"
    assert block.data["cells"][0][0] == "课题名称"
    assert block.data["has_merged_cells"] is True


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
    assert response.status_code == 409, response.text
    diagnostics = response.json()["detail"]["output_check"]
    assert diagnostics["status"] in {"fail", "unverifiable"}
    assert diagnostics["findings"]
    assert b"PK" not in response.content


def test_m1_generic_document_keeps_drafts_but_blocks_delivery_and_queued_work(monkeypatch) -> None:
    from app.services.document_render_jobs import run_document_render_job
    from app.services.workflow_jobs import consume_document_render_jobs

    client = TestClient(app)
    headers = _admin_headers(client)
    package = _published_package(client, headers)
    created = client.post('/api/document-projects', headers=headers,
        json={'package_id': package['id'], 'title': '试点草稿'})
    assert created.status_code == 201, created.text
    project = created.json()
    base = f"/api/document-projects/{project['id']}"
    queued = client.post(base + '/render-jobs', headers=headers)
    assert queued.status_code == 202, queued.text
    job = queued.json()

    monkeypatch.setenv('PAPERGUARD_DELIVERY_STAGE', 'm1')
    editor = client.get(base + '/editor', headers=headers)
    assert editor.status_code == 200
    assert editor.json()['capabilities']['formal_delivery_enabled'] is False
    saved = client.put(base, headers=headers, json={'title': '试点草稿已保存', 'base_revision': project['revision']})
    assert saved.status_code == 200, saved.text

    paths = [
        ('post', '/render?force=true', None),
        ('post', '/render-jobs?force=true', None),
        ('post', f"/render-jobs/{job['id']}/retry", None),
        ('get', f"/render-jobs/{job['id']}/artifact", None),
        ('get', f"/render-jobs/{job['id']}/review-candidate", None),
        ('post', f"/render-jobs/{job['id']}/manual-review", {'output_sha256': 'a' * 64, 'confirmed_check_ids': ['a']}),
    ]
    for method, path, payload in paths:
        response = client.request(method, base + path, headers=headers, json=payload)
        assert response.status_code == 403, (path, response.text)
        assert response.json()['detail']['code'] == 'm1_generic_delivery_unavailable'
    direct = run_document_render_job(Store(), job['id'])
    assert direct['error_code'] == 'M1_GENERIC_DELIVERY_UNAVAILABLE'
    consumed = consume_document_render_jobs(Store(), consumer_id='test-m1')
    assert consumed['claimed'] == 0
    assert Store().get_workflow_record('document_render_job', job['id'])['status'] == 'queued'


def test_document_project_image_upload_is_owned_but_unverified_render_is_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    headers = _admin_headers(client)
    package = _published_package(client, headers)
    created = client.post(
        "/api/document-projects",
        headers=headers,
        json={"package_id": package["id"], "title": "带图片的报告"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    uploaded = client.post(
        f"/api/document-projects/{project_id}/images",
        headers=headers,
        files={"file": ("figure.png", ONE_PIXEL_PNG, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    image_url = uploaded.json()["url"]
    image_path = document_upload_dir() / uploaded.json()["filename"]
    assert image_url.startswith(f"/media/uploads/{project_id}-")
    assert image_path.is_file()
    assert client.get(image_url, headers=headers).status_code == 200

    sections = created.json()["sections"]
    first_key = next(iter(sections))
    sections[first_key][0].update({
        "kind": "image",
        "data": {"url": image_url, "caption": "示例图片"},
    })
    updated = client.put(
        f"/api/document-projects/{project_id}",
        headers=headers,
        json={"sections": sections, "base_revision": 0},
    )
    assert updated.status_code == 200, updated.text
    rendered = client.post(f"/api/document-projects/{project_id}/render", headers=headers)
    assert rendered.status_code == 409, rendered.text
    assert rendered.json()["detail"]["output_check"]["status"] != "pass"
    assert rendered.json()["detail"]["output_check"]["findings"]

    foreign = sections[first_key][0].copy()
    foreign["data"] = {"url": "/media/uploads/docproj-other-figure.png"}
    rejected = client.put(
        f"/api/document-projects/{project_id}",
        headers=headers,
        json={
            "sections": {first_key: [foreign]},
            "base_revision": updated.json()["revision"],
        },
    )
    assert rejected.status_code == 422, rejected.text


def test_document_project_hard_findings_block_force_sync_and_async_render() -> None:
    client = TestClient(app)
    headers = _admin_headers(client)
    package = _published_package(client, headers, HARD_SPEC)
    created = client.post(
        "/api/document-projects",
        headers=headers,
        json={"package_id": package["id"], "title": "异步实验报告"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    blocked = client.post(f"/api/document-projects/{project_id}/render-jobs", headers=headers)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["run_id"]

    for endpoint in ("render", "render-jobs"):
        forced = client.post(f"/api/document-projects/{project_id}/{endpoint}?force=true", headers=headers)
        assert forced.status_code == 409, forced.text
        assert "force 不能绕过" in forced.json()["detail"]["message"]
        assert forced.json()["detail"]["summary"]["hard_failures"] == 1

    assert client.get(f"/api/document-projects/{project_id}/render-jobs", headers=headers).json() == []


def test_document_project_render_job_can_be_cancelled_and_force_is_role_scoped() -> None:
    from app.worker import run_once

    client = TestClient(app)
    admin_headers = _admin_headers(client)
    package = _published_package(client, admin_headers)
    created = client.post(
        "/api/document-projects",
        headers=admin_headers,
        json={"package_id": package["id"], "title": "可取消报告"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    queued = client.post(f"/api/document-projects/{project_id}/render-jobs", headers=admin_headers)
    assert queued.status_code == 202, queued.text
    task = queued.json()

    cancelled = client.post(
        f"/api/document-projects/{project_id}/render-jobs/{task['id']}/cancel",
        headers=admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    run_result = run_once()
    assert task["job_id"] not in run_result["document_renders"]["succeeded"]
    current = client.get(
        f"/api/document-projects/{project_id}/render-jobs/{task['id']}",
        headers=admin_headers,
    )
    assert current.status_code == 200
    assert current.json()["status"] == "cancelled"

    username = f"generic_student_{uuid.uuid4().hex[:8]}"
    student_password = _random_password("student")
    registered = register_fixture(client, username, student_password, display_name="普通学生")
    login = client.post("/api/auth/login", json={"username": username, "password": student_password})
    assert login.status_code == 200, login.text
    student_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    student_project = client.post(
        "/api/document-projects",
        headers=student_headers,
        json={"package_id": package["id"], "title": "学生报告"},
    )
    assert student_project.status_code == 201, student_project.text
    force = client.post(
        f"/api/document-projects/{student_project.json()['id']}/render-jobs?force=true",
        headers=student_headers,
    )
    assert force.status_code == 403, force.text

    audit = client.get("/api/audit", headers=admin_headers, params={"resource_type": "document_render_job"})
    assert audit.status_code == 200, audit.text
    actions = {item["action"] for item in audit.json() if item["resource_id"] == task["id"]}
    assert "document.project.render.cancelled" in actions
