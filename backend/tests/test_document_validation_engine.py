"""Intrinsic generic-template validation tests."""

import pytest

from app.domain.documents.schemas import ContentBlock, DocumentProject, TemplateDefinition, TemplateField, TemplateSection
from app.domain.documents.specs import SpecRule, SpecVersion
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


def test_required_checkbox_is_missing_when_false() -> None:
    project = DocumentProject(
        id="project-2",
        package_id="package-1",
        document_type_id="application",
        template_version_id="template-1@1",
        spec_version_id="spec-1@1",
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
        owner_id="user-1",
        field_values={"approved": False},
    )
    definition = TemplateDefinition(fields=[TemplateField(key="approved", label="Approved", type="checkbox", required=True)])
    spec = SpecVersion(id="spec-1@1", spec_set_id="spec-1", version="1", status="published")

    run = validate_document_project(
        project,
        definition,
        spec,
        run_id="validation-2",
        performed_by="user-1",
        created_at="2026-09-12T00:00:00+00:00",
        rule_engine_version="generic-check-v1",
    )

    assert run.summary["hard_failures"] == 1
    assert run.findings[0].rule_id == "template.required.approved"


@pytest.mark.parametrize(
    ("kind", "data", "expected_failures"),
    [
        ("paragraph", {"kind": "table", "rows": 1, "cols": 1, "cells": [["hidden"]]}, 1),
        ("table", {}, 1),
        ("grid", {"rows": 1, "cols": 1, "cells": [[""]]}, 1),
        ("table", {"rows": 2, "cols": 2, "cells": [["测试项", "结果"], ["连接测试", "通过"]]}, 0),
        ("table", {"rows": 51, "cols": 1, "cells": [["越界"]]}, 1),
    ],
)
def test_table_required_only_accepts_non_empty_renderable_table(kind, data, expected_failures) -> None:
    project = DocumentProject(
        id="project-table",
        package_id="package-1",
        document_type_id="report",
        template_version_id="template-1@1",
        spec_version_id="spec-1@1",
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
        owner_id="user-1",
        sections={"testing": [ContentBlock(id="block-1", kind=kind, data=data)]},
    )
    definition = TemplateDefinition(sections=[TemplateSection(key="testing", title="测试结果")])
    spec = SpecVersion(
        id="spec-1@1",
        spec_set_id="spec-1",
        version="1",
        status="published",
        rules=[SpecRule(
            id="testing-table-required",
            kind="auto",
            target="testing",
            checker="section.table_required",
            params={"section_key": "testing", "min": 1},
            severity="error",
            enforce="hard",
            description="测试结果必须用表格表示",
        )],
    )

    run = validate_document_project(
        project,
        definition,
        spec,
        run_id="validation-table",
        performed_by="user-1",
        created_at="2026-09-12T00:00:00+00:00",
        rule_engine_version="generic-check-v1",
    )

    assert run.summary["hard_failures"] == expected_failures
    if expected_failures:
        assert run.findings[0].rule_id == "testing-table-required"
        assert run.findings[0].actual == 0
