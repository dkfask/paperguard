"""Phase 2 DOCX template candidate tests."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.domain.documents.schemas import TemplateAnchor, TemplateAsset, TemplateDefinition, TemplateField, TemplateSection
from app.main import app
from app.services.template_engine import (
    TemplateCandidateError,
    TemplateParseCandidate,
    confirm_template_candidate,
    parse_docx_template,
    validate_template_candidate,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _asset() -> TemplateAsset:
    return TemplateAsset(
        id="candidate:source",
        storage_key="candidate.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=10,
        checksum=hashlib.sha256(b"candidate").hexdigest(),
        scan_status="validated",
    )


def test_parse_existing_docx_into_generic_candidate() -> None:
    candidate = parse_docx_template(
        FIXTURES / "templates" / "minimal.docx",
        candidate_id="candidate-1",
        template_id="template-1",
        filename="minimal.docx",
        source_asset=_asset(),
    )

    assert candidate.status == "needs_confirmation"
    assert candidate.definition.sections[0].kind == "heading1"
    assert candidate.definition.sections[0].key == "ch1"
    assert candidate.source_asset is not None
    assert candidate.source_asset.scan_status == "validated"


def test_confirm_candidate_is_review_only_and_does_not_publish() -> None:
    candidate = parse_docx_template(
        FIXTURES / "templates" / "minimal.docx",
        candidate_id="candidate-2",
        template_id="template-2",
        filename="minimal.docx",
        source_asset=_asset(),
    )

    confirmed = confirm_template_candidate(
        candidate,
        confirmed_by="user-admin",
        confirmed_at="2026-09-12T00:00:00+00:00",
    )
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_by == "user-admin"
    assert confirmed.definition == candidate.definition


def test_confirm_rejects_candidate_without_sections_or_source_asset() -> None:
    candidate = TemplateParseCandidate(
        candidate_id="candidate-3",
        template_id="template-3",
        name="invalid",
        definition=TemplateDefinition(sections=[]),
    )

    with pytest.raises(TemplateCandidateError) as error:
        confirm_template_candidate(
            candidate,
            confirmed_by="user-admin",
            confirmed_at="2026-09-12T00:00:00+00:00",
        )
    codes = {issue.code for issue in error.value.issues}
    assert {"sections_required", "source_asset_required"}.issubset(codes)


def test_confirm_rejects_duplicate_generic_section_keys() -> None:
    candidate = TemplateParseCandidate(
        candidate_id="candidate-4",
        template_id="template-4",
        name="duplicate",
        source_asset=_asset(),
        definition=TemplateDefinition(sections=[
            TemplateSection(key="same", title="One"),
            TemplateSection(key="same", title="Two"),
        ]),
    )

    with pytest.raises(TemplateCandidateError) as error:
        confirm_template_candidate(
            candidate,
            confirmed_by="user-admin",
            confirmed_at="2026-09-12T00:00:00+00:00",
        )
    assert any(issue.code == "duplicate_section_key" for issue in error.value.issues)


def test_confirm_preserves_reviewed_mode_section_controls_and_export_strategy() -> None:
    candidate = TemplateParseCandidate(
        candidate_id="candidate-reviewed-definition",
        template_id="template-reviewed-definition",
        name="reviewed",
        source_asset=_asset(),
        definition=TemplateDefinition(
            mode="hybrid",
            export_strategy="source_template",
            styles={"Normal": {"size_pt": 12}},
            sections=[TemplateSection(
                key="body",
                title="Body",
                style_ref="Normal",
                repeatable=True,
                allow_user_add=True,
                allow_user_delete=True,
            )],
            fields=[TemplateField(
                key="category",
                label="Category",
                type="select",
                constraints={"options": ["A", "B"]},
            )],
            anchors=[TemplateAnchor(key="category", kind="placeholder", selector="category")],
        ),
    )

    confirmed = confirm_template_candidate(
        candidate,
        confirmed_by="user-admin",
        confirmed_at="2026-09-12T00:00:00+00:00",
    )

    assert confirmed.definition.mode == "hybrid"
    assert confirmed.definition.export_strategy == "source_template"
    section = confirmed.definition.sections[0]
    assert section.repeatable is True
    assert section.allow_user_add is True
    assert section.allow_user_delete is True
    assert section.style_ref == "Normal"


def test_confirm_rejects_source_template_without_anchor_and_invalid_choice_options() -> None:
    source_candidate = TemplateParseCandidate(
        candidate_id="candidate-no-anchor",
        template_id="template-no-anchor",
        name="no-anchor",
        source_asset=_asset(),
        definition=TemplateDefinition(
            export_strategy="source_template",
            sections=[TemplateSection(key="body", title="Body")],
        ),
    )
    with pytest.raises(TemplateCandidateError) as source_error:
        confirm_template_candidate(
            source_candidate,
            confirmed_by="user-admin",
            confirmed_at="2026-09-12T00:00:00+00:00",
        )
    assert any(issue.code == "source_template_anchor_required" for issue in source_error.value.issues)

    choice_candidate = source_candidate.model_copy(update={
        "candidate_id": "candidate-no-options",
        "template_id": "template-no-options",
        "definition": TemplateDefinition(
            sections=[TemplateSection(key="body", title="Body")],
            fields=[TemplateField(key="category", type="select")],
        ),
    })
    checked = validate_template_candidate(choice_candidate)
    assert any(issue.code == "choice_options_required" for issue in checked.issues)


def test_template_parse_and_confirm_api_requires_manager_and_keeps_candidate_state() -> None:
    client = TestClient(app)
    unauthenticated = client.post(
        "/api/templates/parse",
        files={"file": ("minimal.docx", (FIXTURES / "templates" / "minimal.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert unauthenticated.status_code == 401

    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("minimal.docx", (FIXTURES / "templates" / "minimal.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    assert body["status"] == "needs_confirmation"
    assert body["source_asset"]["checksum"] == hashlib.sha256((FIXTURES / "templates" / "minimal.docx").read_bytes()).hexdigest()

    confirmed = client.post("/api/templates/parse/confirm", headers=headers, json=body)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_by"] == "user-admin"

    listed = client.get("/api/templates/parse/candidates", headers=headers)
    assert listed.status_code == 200, listed.text
    listed_candidate = next(item for item in listed.json() if item["candidate_id"] == body["candidate_id"])
    assert listed_candidate["status"] == "confirmed"
    restored = client.get(f"/api/templates/parse/candidates/{body['candidate_id']}", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "confirmed"
    assert client.get("/api/templates/parse/candidates/unknown", headers=headers).status_code == 404

    audit = client.get(
        "/api/audit",
        headers=headers,
        params={"resource_type": "template_candidate", "actor_id": "user-admin"},
    )
    assert audit.status_code == 200, audit.text
    actions = {event["action"] for event in audit.json() if event["resource_id"] == body["candidate_id"]}
    assert {"template.candidate.parsed", "template.candidate.confirmed"}.issubset(actions)
    serialized = " ".join(str(event) for event in audit.json())
    assert body["source_asset"]["checksum"] not in serialized


def test_college_admin_can_review_generic_template_candidate() -> None:
    client = TestClient(app)
    admin_login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert admin_login.status_code == 200, admin_login.text
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['token']}"}
    username = f"generic_college_admin_{uuid.uuid4().hex[:8]}"
    password = "CollegeAdminPassword123"
    registered = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "display_name": "学院管理员", "role": "teacher"},
    )
    assert registered.status_code == 201, registered.text
    promoted = client.put(
        f"/api/auth/admin/users/{registered.json()['id']}/role-status",
        headers=admin_headers,
        json={"role": "college_admin", "status": "active"},
    )
    assert promoted.status_code == 200, promoted.text
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("minimal.docx", (FIXTURES / "templates" / "minimal.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 200, parsed.text
    listed = client.get("/api/templates/parse/candidates", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(item["candidate_id"] == parsed.json()["candidate_id"] for item in listed.json())
