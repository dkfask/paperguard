"""Phase 4 package persistence and publication tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Store
from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentTemplate,
    TemplateDefinition,
    TemplateVersion,
    VersionStatus,
)
from app.domain.documents.specs import SpecRule, SpecSet, SpecVersion
from app.main import app


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "minimal.docx"
SPEC = """---
name: 实验报告规范
version: '1'
rules:
  - id: body-min
    type: auto
    target: body
    checker: word_count
    params: {min: 10}
    description: 正文不少于 10 字
---
# 实验报告规范
"""


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_store_round_trips_package_snapshot_and_rejects_duplicate_composition(tmp_path) -> None:
    store = Store(tmp_path / "packages.db")
    template = DocumentTemplate(
        id="template-1",
        document_type_id="generic",
        name="Template",
        current_version_id="template-1@1",
    )
    template_version = TemplateVersion(
        id="template-1@1",
        template_id=template.id,
        version="1",
        status=VersionStatus.PUBLISHED,
        definition=TemplateDefinition(sections=[]),
    )
    spec_set = SpecSet(id="spec-1", document_type_id="generic", name="Spec", current_version_id="spec-1@1")
    spec_version = SpecVersion(
        id="spec-1@1",
        spec_set_id=spec_set.id,
        version="1",
        status="published",
        rules=[SpecRule(id="manual", kind="manual")],
    )
    package = DocumentPackage(
        id="package-1",
        document_type_id="generic",
        template_version_id=template_version.id,
        spec_version_id=spec_version.id,
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
        status=VersionStatus.PUBLISHED,
    )

    store.save_document_package(package, template, template_version, spec_set, spec_version)
    bundle = Store(tmp_path / "packages.db").get_document_package_bundle(package.id)
    assert bundle is not None
    assert bundle["package"].status == VersionStatus.PUBLISHED
    assert bundle["template_version"].definition == template_version.definition
    assert bundle["spec_version"].rules[0].id == "manual"

    try:
        store.save_document_package(package, template, template_version, spec_set, spec_version)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate package composition was accepted")


def test_publish_package_from_confirmed_candidates_is_persisted_and_readable() -> None:
    client = TestClient(app)
    headers = _admin_headers(client)
    template_content = FIXTURE.read_bytes()
    parsed_template = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("minimal.docx", template_content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed_template.status_code == 200, parsed_template.text
    confirmed_template = client.post("/api/templates/parse/confirm", headers=headers, json=parsed_template.json())
    assert confirmed_template.status_code == 200, confirmed_template.text

    parsed_spec = client.post(
        "/api/specs/parse",
        headers=headers,
        files={"file": ("lab-spec.md", SPEC.encode(), "text/markdown")},
    )
    assert parsed_spec.status_code == 200, parsed_spec.text
    confirmed_spec = client.post("/api/specs/parse/confirm", headers=headers, json=parsed_spec.json())
    assert confirmed_spec.status_code == 200, confirmed_spec.text

    request = {
        "template_candidate": confirmed_template.json(),
        "spec_candidate": confirmed_spec.json(),
        "document_type_id": "lab-report",
    }
    published = client.post("/api/document-packages", headers=headers, json=request)
    assert published.status_code == 201, published.text
    body = published.json()
    assert body["package"]["status"] == "published"
    assert body["package"]["template_version_id"].endswith("@1")
    assert body["package"]["spec_version_id"].endswith("@1")
    assert body["template_version"]["renderer_version"] == "generic-docx-v1"
    assert body["spec_version"]["rules"][0]["checker"] == "section.word_count"

    assert Store().get_workflow_record(
        "document_template_candidate",
        parsed_template.json()["candidate_id"],
    )["status"] == "confirmed"
    assert Store().get_workflow_record(
        "document_spec_candidate",
        parsed_spec.json()["candidate_id"],
    )["status"] == "confirmed"

    loaded = client.get(f"/api/document-packages/{body['package']['id']}", headers=headers)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["package"] == body["package"]
    listed = client.get("/api/document-packages", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(item["package"]["id"] == body["package"]["id"] for item in listed.json())

    duplicate = client.post("/api/document-packages", headers=headers, json=request)
    assert duplicate.status_code == 409, duplicate.text

    tampered = deepcopy(request)
    tampered["renderer_version"] = "generic-docx-v2"
    tampered["template_candidate"]["name"] = "attacker-controlled name"
    tampered["template_candidate"]["definition"]["source_docx"] = "attacker.docx"
    republished = client.post("/api/document-packages", headers=headers, json=tampered)
    assert republished.status_code == 201, republished.text
    assert republished.json()["template"]["name"] == body["template"]["name"]
    assert republished.json()["template_version"]["definition"]["source_docx"] == ""
    assert republished.json()["package"]["metadata"]["organization_context"] == {}

    missing = deepcopy(confirmed_template.json())
    missing["candidate_id"] = "does-not-exist"
    rejected_confirmation = client.post(
        "/api/templates/parse/confirm",
        headers=headers,
        json=missing,
    )
    assert rejected_confirmation.status_code == 404, rejected_confirmation.text

    audit = client.get(
        "/api/audit",
        headers=headers,
        params={"resource_type": "document_package", "actor_id": "user-admin"},
    )
    assert audit.status_code == 200, audit.text
    actions = {event["action"] for event in audit.json() if event["resource_id"] == body["package"]["id"]}
    assert "document.package.published" in actions
    assert "lab-spec" not in json.dumps(audit.json(), ensure_ascii=False)
