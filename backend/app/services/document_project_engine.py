"""Instantiate and validate generic document projects from package snapshots."""

from __future__ import annotations

from typing import Any

from app.domain.documents.schemas import ContentBlock, DocumentPackage, DocumentProject, TemplateDefinition
from app.services.document_assets import resolve_document_image_path


class DocumentProjectError(ValueError):
    """Raised when project content does not match its pinned template schema."""


def flatten_template_sections(definition: TemplateDefinition) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(nodes) -> None:
        for node in nodes:
            result.append({
                "key": node.key,
                "title": node.title,
                "kind": node.kind,
                "required": node.required,
                "style_ref": node.style_ref,
                "content_schema": node.content_schema,
                "parent_key": node.parent_key,
            })
            walk(node.children)

    walk(definition.sections)
    return result


def validate_field_values(definition: TemplateDefinition, values: dict[str, Any]) -> None:
    fields = {field.key: field for field in definition.fields}
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise DocumentProjectError(f"字段不属于当前模板: {', '.join(unknown)}")
    for key, field in fields.items():
        value = values.get(key)
        if field.required and (value is None or not str(value).strip()):
            continue
        max_length = field.constraints.get("max_length") if isinstance(field.constraints, dict) else None
        if max_length is not None:
            try:
                if len(str(value)) > int(max_length):
                    raise DocumentProjectError(f"字段超过长度限制: {key}")
            except (TypeError, ValueError):
                raise DocumentProjectError(f"字段长度约束无效: {key}") from None


def validate_section_content(
    definition: TemplateDefinition,
    sections: dict[str, list[ContentBlock]],
    project_id: str | None = None,
) -> None:
    known_keys = {item["key"] for item in flatten_template_sections(definition)}
    unknown = sorted(set(sections) - known_keys)
    if unknown:
        raise DocumentProjectError(f"章节不属于当前模板: {', '.join(unknown)}")
    encoded_size = sum(len(block.model_dump_json()) for blocks in sections.values() for block in blocks)
    if encoded_size > 2_000_000:
        raise DocumentProjectError("项目内容超过 2 MB 单次保存上限")
    for key, blocks in sections.items():
        for index, block in enumerate(blocks):
            if len(block.text) > 200_000:
                raise DocumentProjectError(f"内容块过大: {key}[{index}]")
            kind = str(block.kind or "paragraph").lower()
            if kind in {"image", "figure"}:
                data = block.data if isinstance(block.data, dict) else {}
                url = str(data.get("url") or data.get("path") or "")
                if project_id is not None and resolve_document_image_path(project_id, url) is None:
                    raise DocumentProjectError(f"图片引用无效或不属于当前项目: {key}[{index}]")


def instantiate_document_project(
    package: DocumentPackage,
    definition: TemplateDefinition,
    *,
    project_id: str,
    owner_id: str,
    title: str,
    field_values: dict[str, Any],
    organization_context: dict[str, str],
) -> DocumentProject:
    validate_field_values(definition, field_values)
    sections = {
        section["key"]: [ContentBlock(id=f"{project_id}:{section['key']}:0", order=0)]
        for section in flatten_template_sections(definition)
        if section["kind"] not in {"cover", "toc"}
    }
    project = DocumentProject(
        id=project_id,
        package_id=package.id,
        document_type_id=package.document_type_id,
        template_version_id=package.template_version_id,
        spec_version_id=package.spec_version_id,
        renderer_version=package.renderer_version,
        rule_engine_version=package.rule_engine_version,
        owner_id=owner_id,
        title=title,
        field_values=dict(field_values),
        sections=sections,
        organization_context=dict(organization_context),
        metadata={"source": "document_package", "package_id": package.id},
    )
    validate_section_content(definition, project.sections, project.id)
    return project


def editor_schema(definition: TemplateDefinition) -> dict[str, Any]:
    return {
        "fields": [field.model_dump(mode="json") for field in definition.fields],
        "sections": flatten_template_sections(definition),
        "styles": {key: style.model_dump(mode="json") for key, style in definition.styles.items()},
        "page_layout": dict(definition.page_layout),
        "allow_structure_edit": definition.allow_structure_edit,
    }


__all__ = [
    "DocumentProjectError",
    "editor_schema",
    "flatten_template_sections",
    "instantiate_document_project",
    "validate_field_values",
    "validate_section_content",
]
