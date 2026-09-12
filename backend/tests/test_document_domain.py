"""Phase 1 domain contract and legacy adapter tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db import Store
from app.domain.documents.adapters import (
    LegacyStoreProjectSnapshotRepository,
    LegacyStoreSpecRepository,
    LegacyStoreTemplateRepository,
)
from app.domain.documents.hashing import canonical_hash
from app.domain.documents.mappers import domain_to_paper_template, paper_template_to_domain
from app.domain.documents.schemas import (
    DocumentPackage,
    TemplateDefinition,
    TemplateField,
    TemplateSection,
    TemplateVersion,
)
from app.domain.documents.snapshots import ProjectSnapshot
from app.domain.documents.specs import requirement_set_to_spec
from app.models.policy import RequirementItem, RequirementSet, RequirementType
from app.models.project import WritingProject
from app.models.template import (
    CoverBlock,
    MetaField,
    PaperTemplate,
    SectionType,
    TemplateSection as LegacyTemplateSection,
    TextStyle,
)


def _template() -> PaperTemplate:
    return PaperTemplate(
        id="tpl-domain-test",
        name="Domain test template",
        description="round-trip",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
        sections=[
            LegacyTemplateSection(
                key="cover",
                type=SectionType.COVER,
                title="Cover",
                children=[
                    LegacyTemplateSection(
                        key="body",
                        type=SectionType.BODY,
                        title="Body",
                        style=TextStyle(font_zh="Arial", size_pt=11),
                    )
                ],
            )
        ],
        styles={"body": TextStyle(font_zh="Arial", size_pt=11)},
        page_layout={"margin_top_cm": 2.0},
        meta_fields=[MetaField(key="title", label="Title", required=True)],
        cover_blocks=[CoverBlock(kind="text", text="Document")],
        source_docx="source.docx",
        allow_structure_edit=False,
        version="7",
        rule_packs=["SIM-UNIV-generic"],
        requirement_set_ids=["req-1"],
        organization_context={"school_id": "school-1"},
    )


def test_thesis_template_round_trip_preserves_legacy_data() -> None:
    original = _template()
    logical, version = paper_template_to_domain(original)

    assert logical.document_type_id == "thesis"
    assert version.id == "tpl-domain-test@7"
    assert version.definition_hash == canonical_hash(version.definition)
    assert version.source_asset_id == "tpl-domain-test:source:source.docx"

    restored = domain_to_paper_template(logical, version)
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")


def test_published_template_version_is_immutable() -> None:
    _, version = paper_template_to_domain(_template())

    with pytest.raises(ValidationError):
        version.version = "8"  # type: ignore[misc]


def test_generic_sections_and_fields_are_structured_without_thesis_enum() -> None:
    definition = TemplateDefinition(
        sections=[
            TemplateSection(
                key="approval-table",
                title="Approval table",
                kind="table",
                content_schema={"columns": 4},
            )
        ],
        fields=[TemplateField(key="budget", label="Budget", type="number", required=True)],
    )
    version = TemplateVersion(
        id="generic@1",
        template_id="generic",
        version="1",
        definition=definition,
        definition_hash=canonical_hash(definition),
    )

    payload = version.model_dump(mode="json")
    assert payload["definition"]["sections"][0]["kind"] == "table"
    assert payload["definition"]["fields"][0]["type"] == "number"


def test_requirement_set_mapping_is_stable_and_preserves_provenance() -> None:
    requirement_set = RequirementSet(
        id="req-v2",
        family_id="req-family",
        name="Generic rules",
        version="2",
        items=[
            RequirementItem(
                id="rule-1",
                type=RequirementType.MANUAL,
                target="section.body",
                description="Confirm the evidence.",
                source="ai:candidate",
            )
        ],
    )

    first_set, first_version = requirement_set_to_spec(requirement_set)
    second_set, second_version = requirement_set_to_spec(requirement_set)
    assert first_set.model_dump(mode="json") == second_set.model_dump(mode="json")
    assert first_version.model_dump(mode="json") == second_version.model_dump(mode="json")
    assert first_version.id == "req-family@2"
    assert first_version.content_hash == canonical_hash(
        {key: value for key, value in first_version.model_dump(mode="json").items() if key != "content_hash"}
    )
    assert first_version.rules[0].provenance["ai_generated"] is True


def test_project_snapshot_freezes_legacy_template_policy_and_engine_versions() -> None:
    project = WritingProject(
        id="project-1",
        template_id="tpl-domain-test",
        template_snapshot={"id": "tpl-domain-test", "version": "7"},
        template_source_snapshot={"id": "tpl-domain-test", "version": "7"},
        requirement_snapshot={"content_hash": "policy-hash"},
        owner_id="student-1",
        revision=3,
        sections=[],
    )

    snapshot = ProjectSnapshot.from_legacy_project(
        project,
        renderer_version="docx-renderer@1",
        rule_engine_version="rules@1",
    )
    assert snapshot.template_version_id == "tpl-domain-test@7"
    assert snapshot.spec_version_id == "legacy-spec@policy-hash"
    assert snapshot.renderer_version == "docx-renderer@1"
    assert snapshot.rule_engine_version == "rules@1"
    assert snapshot.project_data == project.model_dump(mode="json")

    with pytest.raises(ValidationError):
        snapshot.renderer_version = "docx-renderer@2"  # type: ignore[misc]


def test_store_adapters_read_existing_models_without_new_tables(tmp_path) -> None:
    store = Store(tmp_path / "domain.db")
    template = _template()
    requirement_set = RequirementSet(id="req-1", name="Rules")
    project = WritingProject(id="project-1", template_id=template.id, sections=[])
    store.save_template(template)
    store.save_requirement_set(requirement_set)
    store.save_project(project)

    template_aggregate = LegacyStoreTemplateRepository(store).get(template.id)
    spec_aggregate = LegacyStoreSpecRepository(store).get(requirement_set.id)
    snapshot = LegacyStoreProjectSnapshotRepository(store).get(project.id)

    assert template_aggregate is not None
    assert template_aggregate[0].id == template.id
    assert spec_aggregate is not None
    assert spec_aggregate[1].spec_set_id == requirement_set.id
    assert snapshot is not None
    assert snapshot.project_id == project.id


def test_package_contract_references_exact_execution_versions() -> None:
    package = DocumentPackage(
        id="package-1",
        document_type_id="generic",
        template_version_id="template@3",
        spec_version_id="spec@4",
        renderer_version="docx@2",
        rule_engine_version="rules@5",
    )
    assert package.template_version_id == "template@3"
    assert package.spec_version_id == "spec@4"
    assert package.renderer_version == "docx@2"
    assert package.rule_engine_version == "rules@5"
