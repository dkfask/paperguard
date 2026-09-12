"""Intrinsic generic-template validation tests."""

from app.domain.documents.schemas import DocumentProject, TemplateDefinition, TemplateField
from app.domain.documents.specs import SpecVersion
from app.services.document_validation_engine import validate_document_project


def test_required_template_fields_are_hard_findings_before_spec_rules() -> None:
    project = DocumentProject(
        id="project-1",
        package_id="package-1",
        document_type_id="application",
        template_version_id="template-1@1",
        spec_version_id="spec-1@1",
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
        owner_id="user-1",
        field_values={"title": ""},
    )
    definition = TemplateDefinition(fields=[TemplateField(key="title", label="Title", required=True)])
    spec = SpecVersion(id="spec-1@1", spec_set_id="spec-1", version="1", status="published")

    run = validate_document_project(
        project,
        definition,
        spec,
        run_id="validation-1",
        performed_by="user-1",
        created_at="2026-09-12T00:00:00+00:00",
        rule_engine_version="generic-check-v1",
    )

    assert run.summary["total"] == 1
    assert run.summary["hard_failures"] == 1
    assert run.findings[0].rule_id == "template.required.title"
    assert run.findings[0].enforce == "hard"
