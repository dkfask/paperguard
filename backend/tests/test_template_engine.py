"""Phase 2 DOCX template candidate tests."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.domain.documents.schemas import TemplateAnchor, TemplateAsset, TemplateDefinition, TemplateField, TemplateSection
from app.db import Store
from app.main import app
from app.models.template import PaperTemplate
from app.services.document_candidates import TEMPLATE_CANDIDATE_KIND
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
    from app.services.template_source import save_template_source

    source = (FIXTURES / "templates" / "admissible.docx").read_bytes()
    storage_key = save_template_source(source)
    candidate = parse_docx_template(
        FIXTURES / "templates" / "admissible.docx",
        candidate_id="candidate-2",
        template_id="template-2",
        filename="admissible.docx",
        source_asset=TemplateAsset(
            id="candidate:source",
            storage_key=storage_key,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=len(source),
            checksum=hashlib.sha256(source).hexdigest(),
            purpose="template-source",
            scan_status="validated",
        ),
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
        files={"file": ("admissible.docx", (FIXTURES / "templates" / "admissible.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert unauthenticated.status_code == 401

    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("admissible.docx", (FIXTURES / "templates" / "admissible.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    assert body["status"] == "needs_confirmation"
    assert body["source_asset"]["checksum"] == hashlib.sha256((FIXTURES / "templates" / "admissible.docx").read_bytes()).hexdigest()
    original_assessment = body["admission_assessment"]
    assert original_assessment["status"] == "office_review_required"
    assert original_assessment["can_claim_compliant_delivery"] is False

    changed_candidate = {**body, "version": "2"}
    reassessed = client.post("/api/templates/parse/assess", headers=headers, json=changed_candidate)
    assert reassessed.status_code == 200, reassessed.text
    assert reassessed.json()["admission_assessment"]["assessment_hash"] != original_assessment["assessment_hash"]
    unchanged = client.get(f"/api/templates/parse/candidates/{body['candidate_id']}", headers=headers)
    assert unchanged.status_code == 200
    assert unchanged.json()["admission_assessment"]["assessment_hash"] == original_assessment["assessment_hash"]

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


@pytest.mark.parametrize("interruption", ["template_insert", "publication_finalize"])
def test_candidate_publish_requires_non_preview_trials_and_binds_project_snapshot(monkeypatch, interruption) -> None:
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    source = (FIXTURES / "templates" / "admissible.docx").read_bytes()
    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("admissible.docx", source, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 200, parsed.text
    candidate = parsed.json()
    specification = candidate["definition"]["extensions"]["writing_specification"]
    confirmation = {
        "expected_specification_sha256": specification["specification_sha256"],
        "accept_recommended": True,
        "remove_template_instructions": True,
        "accept_new_object_defaults": True,
    }

    preview = client.post("/api/templates/parse/writing-profile", headers=headers, json={
        "candidate": candidate,
        "confirmation": confirmation,
        "preview_only": True,
    })
    assert preview.status_code == 200, preview.text
    assert all(report["status"] == "pass" for report in preview.json()["trials"].values())
    preview_candidate = client.post(
        "/api/templates/parse/confirm", headers=headers, json=preview.json()["candidate"]
    )
    assert preview_candidate.status_code == 422, preview_candidate.text
    assert "source_profile_invalid" in preview_candidate.text

    parsed_again = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("admissible.docx", source, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed_again.status_code == 200, parsed_again.text
    candidate = parsed_again.json()
    candidate["definition"]["organization_context"] = {"school_id": "client-spoofed-school"}
    confirmation["expected_specification_sha256"] = candidate["definition"]["extensions"]["writing_specification"]["specification_sha256"]
    prepared = client.post("/api/templates/parse/writing-profile", headers=headers, json={
        "candidate": candidate,
        "confirmation": confirmation,
        "preview_only": False,
    })
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["candidate"]["definition"]["organization_context"] == {}
    assert set(prepared.json()["trials"]) == {"normal", "long"}
    assert all(report["status"] == "pass" for report in prepared.json()["trials"].values())
    # JSON.stringify in browsers emits integral floats as integers. The
    # server-owned prepared profile must remain valid after that round trip.
    browser_candidate = json.loads(
        json.dumps(prepared.json()["candidate"]),
        parse_float=lambda value: int(float(value)) if float(value).is_integer() else float(value),
    )
    assert json.dumps(browser_candidate) != json.dumps(prepared.json()["candidate"])
    confirmed = client.post(
        "/api/templates/parse/confirm", headers=headers, json=browser_candidate
    )
    assert confirmed.status_code == 200, confirmed.text

    store = Store()
    store.save_template(PaperTemplate(id=candidate["template_id"], name="预先占用的模板"))
    collision = client.post(
        "/api/templates/parse/publish", headers=headers,
        json={"candidate_id": candidate["candidate_id"]},
    )
    assert collision.status_code == 409, collision.text
    candidate_record = store.get_workflow_record(TEMPLATE_CANDIDATE_KIND, candidate["candidate_id"])
    assert candidate_record is not None
    assert candidate_record.get("publication") is None
    store.delete_template(candidate["template_id"])

    original_insert = Store.insert_template_if_absent
    with monkeypatch.context() as patch:
        if interruption == "template_insert":
            def interrupt_insert(self, template):
                if template.id == candidate["template_id"]:
                    raise RuntimeError("simulated interruption before template insert")
                return original_insert(self, template)

            patch.setattr(Store, "insert_template_if_absent", interrupt_insert)
        else:
            original_save_record = Store.save_workflow_record

            def interrupt_finalize(self, kind, record, *, expected_revision=None):
                publication = record.get("publication") or {}
                if kind == TEMPLATE_CANDIDATE_KIND and publication.get("status") == "published":
                    return None
                return original_save_record(self, kind, record, expected_revision=expected_revision)

            patch.setattr(Store, "save_workflow_record", interrupt_finalize)
        with TestClient(app, raise_server_exceptions=False) as interrupted_client:
            interrupted = interrupted_client.post(
                "/api/templates/parse/publish", headers=headers,
                json={"candidate_id": candidate["candidate_id"]},
            )
    assert interrupted.status_code == (500 if interruption == "template_insert" else 409)
    pending_record = store.get_workflow_record(TEMPLATE_CANDIDATE_KIND, candidate["candidate_id"])
    assert pending_record["publication"]["status"] == "publishing"
    assert pending_record["publication"]["template_sha256"]
    if interruption == "publication_finalize":
        assert store.get_template(candidate["template_id"]) is not None

    published = client.post(
        "/api/templates/parse/publish", headers=headers,
        json={"candidate_id": candidate["candidate_id"]},
    )
    assert published.status_code == 200, published.text
    template = published.json()["template"]
    provenance = template["admission_provenance"]
    assert template["id"] == candidate["template_id"]
    assert template["organization_context"] == {}
    assert provenance["visibility"] == "global"
    assert provenance["candidate_id"] == candidate["candidate_id"]
    assert provenance["source_docx_sha256"] == hashlib.sha256(source).hexdigest()
    assert provenance["trial_evidence_sha256"]
    assert published.json()["admission"]["can_use_for_draft"] is True
    assert published.json()["admission"]["can_claim_compliant_delivery"] is False

    browser_template = json.loads(
        json.dumps(template),
        parse_float=lambda value: int(float(value)) if float(value).is_integer() else float(value),
    )
    assessed = client.post('/api/templates/admission/assess', headers=headers, json=browser_template)
    assert assessed.status_code == 200, assessed.text
    assert assessed.json()['can_use_for_draft'] is True
    browser_template['source_execution']['source_sha256'] = '0' * 64
    changed_assessment = client.post('/api/templates/admission/assess', headers=headers, json=browser_template)
    assert changed_assessment.status_code == 200, changed_assessment.text
    assert any(item['code'] == 'source_profile_invalid' for item in changed_assessment.json()['blockers'])

    changed_definition = {**template, "name": "未经重新准入的改写"}
    update = client.put(f"/api/templates/{template['id']}", headers=headers, json=changed_definition)
    assert update.status_code == 409, update.text
    assert client.get(f"/api/templates/{template['id']}", headers=headers).json()["admission_provenance"] == provenance
    assert client.delete(f"/api/templates/{template['id']}", headers=headers).status_code == 409
    monkeypatch.setattr(
        "app.api.templates.default_template",
        lambda: PaperTemplate.model_validate(template),
    )
    reset = client.post("/api/templates/default", headers=headers)
    assert reset.status_code == 409, reset.text
    assert client.get(f"/api/templates/{template['id']}", headers=headers).json()["admission_provenance"] == provenance
    assert client.post("/api/templates/default", headers=headers).status_code == 409

    repeated = client.post(
        "/api/templates/parse/publish", headers=headers,
        json={"candidate_id": candidate["candidate_id"]},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["template"]["admission_provenance"] == provenance

    project = client.post("/api/projects", headers=headers, json={
        "template_id": template["id"], "title": "候选发布快照验证",
    })
    assert project.status_code == 201, project.text
    assert project.json()["template_snapshot"]["admission_provenance"] == provenance


def test_insert_template_if_absent_never_replaces_an_existing_id(tmp_path) -> None:
    store = Store(tmp_path / "templates.db")
    existing = PaperTemplate(id="same-id", name="existing")
    replacement = PaperTemplate(id="same-id", name="replacement")

    assert store.insert_template_if_absent(existing) is True
    assert store.insert_template_if_absent(replacement) is False
    assert store.get_template("same-id").name == "existing"


def test_template_create_reports_atomic_id_collision_without_replacing_template(monkeypatch) -> None:
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    existing = PaperTemplate(id=f"concurrent-{uuid.uuid4().hex}", name="并发先创建")
    store = Store()
    original_insert = Store.insert_template_if_absent

    def insert_competing_template(self, template):
        if template.id == existing.id:
            assert original_insert(self, existing)
        return original_insert(self, template)

    monkeypatch.setattr(Store, "insert_template_if_absent", insert_competing_template)
    response = client.post("/api/templates", headers=headers, json={
        **PaperTemplate(id=existing.id, name="后到模板").model_dump(mode="json"),
    })

    assert response.status_code == 409, response.text
    assert store.get_template(existing.id).name == "并发先创建"


def test_candidate_publish_rejects_definition_changed_after_trial() -> None:
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    source = (FIXTURES / "templates" / "admissible.docx").read_bytes()
    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("admissible.docx", source, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 200, parsed.text
    candidate = parsed.json()
    confirmation = {
        "expected_specification_sha256": candidate["definition"]["extensions"]["writing_specification"]["specification_sha256"],
        "accept_recommended": True,
        "remove_template_instructions": True,
        "accept_new_object_defaults": True,
    }
    prepared = client.post("/api/templates/parse/writing-profile", headers=headers, json={
        "candidate": candidate,
        "confirmation": confirmation,
        "preview_only": False,
    })
    assert prepared.status_code == 200, prepared.text
    changed = prepared.json()["candidate"]
    changed["definition"]["extensions"]["post_trial_change"] = "changed"
    confirmed = client.post("/api/templates/parse/confirm", headers=headers, json=changed)
    assert confirmed.status_code == 200, confirmed.text
    published = client.post("/api/templates/parse/publish", headers=headers, json={
        "candidate_id": candidate["candidate_id"],
    })
    assert published.status_code == 409
    assert "试排后发生变化" in published.text


def test_template_parse_blocks_an_unmapped_header_before_confirmation() -> None:
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("minimal.docx", (FIXTURES / "templates" / "minimal.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["admission_assessment"]["status"] == "blocked"
    rejected = client.post("/api/templates/parse/confirm", headers=headers, json=parsed.json())
    assert rejected.status_code == 422, rejected.text
    assert any(item["code"] == "unmapped_header" for item in rejected.json()["detail"]["admission"]["blockers"])


def test_template_candidate_nested_style_is_blocked_before_confirmation():
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("admissible.docx", (FIXTURES / "templates" / "admissible.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 200, parsed.text
    candidate = parsed.json()
    candidate["definition"]["sections"][0]["style"] = {"color": "NOTRGB"}

    assessed = client.post("/api/templates/parse/assess", headers=headers, json=candidate)
    assert assessed.status_code == 200, assessed.text
    report = assessed.json()["admission_assessment"]
    assert report["status"] == "blocked"
    assert any(item["code"] == "style_color" for item in report["blockers"])

    rejected = client.post("/api/templates/parse/confirm", headers=headers, json=candidate)
    assert rejected.status_code == 422, rejected.text
    assert any(item["code"] == "style_color" for item in rejected.json()["detail"]["admission"]["blockers"])


def test_closed_college_admin_cannot_review_generic_template_candidate(closed_role_session) -> None:
    client = TestClient(app)
    admin_login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert admin_login.status_code == 200, admin_login.text
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['token']}"}
    username = f"generic_college_admin_{uuid.uuid4().hex[:8]}"
    password = f"college-admin-{uuid.uuid4().hex}-A1"
    registered = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "display_name": "学院管理员", "role": "teacher"},
    )
    assert registered.status_code == 201, registered.text
    headers = closed_role_session(registered.json()["id"])

    parsed = client.post(
        "/api/templates/parse",
        headers=headers,
        files={"file": ("minimal.docx", (FIXTURES / "templates" / "minimal.docx").read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert parsed.status_code == 403, parsed.text
    listed = client.get("/api/templates/parse/candidates", headers=headers)
    assert listed.status_code == 403, listed.text
