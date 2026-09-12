"""Compatibility mappers between legacy PaperGuard models and domain schemas."""

from __future__ import annotations

from typing import Any

from app.domain.documents.hashing import canonical_hash
from app.domain.documents.schemas import (
    DocumentTemplate,
    StyleDefinition,
    TemplateAsset,
    TemplateDefinition,
    TemplateField,
    TemplateSection,
    TemplateVersion,
    VersionStatus,
)
from app.models.project import WritingProject
from app.models.template import (
    CoverBlock,
    MetaField,
    PaperTemplate,
    SectionType,
    TemplateSection as LegacyTemplateSection,
    TextStyle,
)


def _version_id(template_id: str, version: str) -> str:
    return f"{template_id}@{version}"


def _style_to_domain(style: TextStyle) -> StyleDefinition:
    return StyleDefinition.model_validate(style.model_dump(mode="json"))


def _style_to_legacy(style: StyleDefinition) -> TextStyle:
    return TextStyle.model_validate(style.model_dump(mode="json"))


def _section_to_domain(node: LegacyTemplateSection, order: int = 0) -> TemplateSection:
    children = [
        _section_to_domain(child, child_order)
        for child_order, child in enumerate(node.children)
    ]
    return TemplateSection(
        key=node.key,
        title=node.title,
        kind=node.type.value,
        order=order,
        required=node.required,
        style_ref=node.type.value,
        style=_style_to_domain(node.style) if node.style else None,
        children=children,
        extensions={"legacy_type": node.type.value},
    )


def _section_to_legacy(node: TemplateSection) -> LegacyTemplateSection:
    try:
        section_type = SectionType(node.kind)
    except ValueError as exc:
        raise ValueError(
            f"thesis adapter cannot represent generic section kind: {node.kind}"
        ) from exc
    return LegacyTemplateSection(
        key=node.key,
        type=section_type,
        title=node.title,
        required=node.required,
        style=_style_to_legacy(node.style) if node.style else None,
        children=[_section_to_legacy(child) for child in node.children],
    )


def paper_template_to_domain(
    template: PaperTemplate,
    *,
    document_type_id: str = "thesis",
    renderer_version: str = "legacy-docx-v1",
    rule_engine_version: str = "legacy-check-v1",
) -> tuple[DocumentTemplate, TemplateVersion]:
    """Convert a legacy PaperTemplate into a logical template and immutable version."""
    definition = TemplateDefinition(
        sections=[_section_to_domain(section, order) for order, section in enumerate(template.sections)],
        fields=[
            TemplateField(
                key=field.key,
                label=field.label,
                type="text",
                required=field.required,
                location="metadata",
                source="legacy.meta_fields",
            )
            for field in template.meta_fields
        ],
        styles={key: _style_to_domain(style) for key, style in template.styles.items()},
        page_layout=dict(template.page_layout),
        allow_structure_edit=template.allow_structure_edit,
        source_docx=template.source_docx,
        cover_blocks=[block.model_dump(mode="json") for block in template.cover_blocks],
        rule_packs=list(template.rule_packs),
        requirement_set_ids=list(template.requirement_set_ids),
        organization_context=dict(template.organization_context),
        extensions={"legacy_model": "PaperTemplate"},
    )
    version_id = _version_id(template.id, template.version)
    source_asset_id = ""
    if template.source_docx:
        source_asset_id = f"{template.id}:source:{template.source_docx}"
    version = TemplateVersion(
        id=version_id,
        template_id=template.id,
        version=template.version,
        status=VersionStatus.PUBLISHED,
        definition=definition,
        source_asset_id=source_asset_id,
        definition_hash=canonical_hash(definition),
        renderer_version=renderer_version,
        rule_engine_version=rule_engine_version,
        created_at=template.created_at,
        published_at=template.updated_at or template.created_at,
        extensions={
            "legacy_rule_packs": list(template.rule_packs),
            "legacy_requirement_set_ids": list(template.requirement_set_ids),
        },
    )
    logical = DocumentTemplate(
        id=template.id,
        document_type_id=document_type_id,
        name=template.name,
        description=template.description,
        organization_context=dict(template.organization_context),
        status="active",
        current_version_id=version_id,
        created_at=template.created_at,
        updated_at=template.updated_at,
        metadata={"legacy_model": "PaperTemplate"},
    )
    return logical, version


def domain_to_paper_template(
    logical: DocumentTemplate,
    version: TemplateVersion,
) -> PaperTemplate:
    """Convert a thesis-compatible domain version back to the legacy model."""
    if logical.id != version.template_id:
        raise ValueError("template aggregate identity mismatch")
    definition = version.definition
    return PaperTemplate(
        id=logical.id,
        name=logical.name,
        description=logical.description,
        created_at=logical.created_at,
        updated_at=logical.updated_at,
        sections=[_section_to_legacy(section) for section in definition.sections],
        styles={key: _style_to_legacy(style) for key, style in definition.styles.items()},
        page_layout=dict(definition.page_layout),
        meta_fields=[
            MetaField(key=field.key, label=field.label, required=field.required)
            for field in definition.fields
            if field.location == "metadata"
        ],
        cover_blocks=[CoverBlock.model_validate(block) for block in definition.cover_blocks],
        source_docx=definition.source_docx,
        allow_structure_edit=definition.allow_structure_edit,
        version=version.version,
        rule_packs=list(definition.rule_packs),
        requirement_set_ids=list(definition.requirement_set_ids),
        organization_context=dict(definition.organization_context),
    )


def template_source_asset(
    logical: DocumentTemplate,
    version: TemplateVersion,
) -> TemplateAsset | None:
    """Expose a legacy template source filename as an asset reference."""
    if not version.definition.source_docx or not version.source_asset_id:
        return None
    return TemplateAsset(
        id=version.source_asset_id,
        storage_key=version.definition.source_docx,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        purpose="template-source",
    )


def template_version_id(template: PaperTemplate) -> str:
    return _version_id(template.id, template.version)


def project_template_version_id(project: WritingProject) -> str:
    snapshot = project.template_snapshot or {}
    version = str(snapshot.get("version") or "legacy")
    return _version_id(project.template_id, version)


__all__ = [
    "domain_to_paper_template",
    "paper_template_to_domain",
    "project_template_version_id",
    "template_source_asset",
    "template_version_id",
]
