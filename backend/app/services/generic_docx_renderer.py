"""Render generic document projects through the existing DOCX exporter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.export.docx_exporter import export_project_to_docx
from app.domain.documents.mappers import domain_to_paper_template
from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentProject,
    DocumentTemplate,
    StyleDefinition,
    TemplateDefinition,
    TemplateSection,
    TemplateVersion,
)
from app.domain.documents.specs import SpecVersion
from app.models.project import (
    BlockType,
    EquationBlock,
    ImageBlock,
    ParagraphBlock,
    ProjectSection,
    TableBlock,
    WritingProject,
)
from app.models.template import DEFAULT_STYLES, SectionType
from app.services.template_source import resolve_template_source
from app.services.document_assets import resolve_document_image_path
from app.services.docx_anchors import DocxAnchorError, render_anchored_docx


_LEGACY_SECTION_KINDS = {item.value for item in SectionType}


class GenericDocxRenderError(ValueError):
    """Raised when a generic project cannot be represented by the DOCX adapter."""


def _legacy_kind(kind: str) -> str:
    return kind if kind in _LEGACY_SECTION_KINDS else SectionType.BODY.value


def _normalize_section(node: TemplateSection) -> TemplateSection:
    return node.model_copy(update={
        "kind": _legacy_kind(node.kind),
        "children": [_normalize_section(child) for child in node.children],
    })


def _legacy_template_definition(definition: TemplateDefinition) -> TemplateDefinition:
    styles = dict(definition.styles)
    for key, style in DEFAULT_STYLES.items():
        if key not in styles:
            styles[key] = StyleDefinition.model_validate(style.model_dump(mode="json"))
    return definition.model_copy(update={
        "sections": [_normalize_section(node) for node in definition.sections],
        "styles": styles,
    })


def _legacy_block(block: Any, project_id: str) -> ParagraphBlock:
    data = block.data if isinstance(block.data, dict) else {}
    kind = str(block.kind or "paragraph").lower()
    if kind in {"image", "figure"}:
        url = str(data.get("url") or data.get("path") or "")
        if not url or resolve_document_image_path(project_id, url) is None:
            raise GenericDocxRenderError(f"image block {block.id} has no valid project-owned image")
        return ParagraphBlock(
            id=block.id,
            type=BlockType.IMAGE,
            image=ImageBlock(url=url, caption=str(data.get("caption") or "")),
        )
    if kind in {"table", "grid"}:
        try:
            table = TableBlock(
                rows=int(data.get("rows") or 1),
                cols=int(data.get("cols") or 1),
                cells=data.get("cells") or [],
                caption=str(data.get("caption") or ""),
                source_xml=str(data.get("source_xml") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise GenericDocxRenderError(f"table block {block.id} is invalid") from exc
        return ParagraphBlock(id=block.id, type=BlockType.TABLE, table=table)
    if kind in {"equation", "formula"}:
        return ParagraphBlock(
            id=block.id,
            type=BlockType.EQUATION,
            equation=EquationBlock(
                omml=str(data.get("omml") or ""),
                alt_text=str(data.get("alt_text") or block.text or ""),
            ),
        )
    return ParagraphBlock(id=block.id, type=BlockType.PARAGRAPH, text=block.text or "")


def _flatten_sections(definition: TemplateDefinition) -> list[TemplateSection]:
    result: list[TemplateSection] = []

    def walk(nodes) -> None:
        for node in nodes:
            result.append(node)
            walk(node.children)

    walk(definition.sections)
    return result


def _legacy_project(project: DocumentProject, definition: TemplateDefinition) -> WritingProject:
    nodes = {node.key: node for node in _flatten_sections(definition)}
    sections: list[ProjectSection] = []
    for key, node in nodes.items():
        blocks = project.sections.get(key)
        if blocks is None:
            continue
        ordered = sorted(blocks, key=lambda block: (block.order, block.id))
        sections.append(ProjectSection(
            key=key,
            type=_legacy_kind(node.kind),
            title=node.title,
            required=node.required,
            paragraphs=[_legacy_block(block, project.id) for block in ordered],
        ))
    unknown = sorted(set(project.sections) - set(nodes))
    if unknown:
        raise GenericDocxRenderError(f"project contains unknown sections: {', '.join(unknown)}")
    return WritingProject(
        id=project.id,
        template_id=project.template_version_id.split("@", 1)[0],
        template_name="generic-document",
        title=project.title,
        owner_id=project.owner_id,
        revision=project.revision,
        meta={key: "" if value is None else str(value) for key, value in project.field_values.items()},
        sections=sections,
        allow_structure_edit=False,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def render_document_project_to_docx(
    project: DocumentProject,
    package: DocumentPackage,
    template: DocumentTemplate,
    template_version: TemplateVersion,
    spec_version: SpecVersion | None,
    out_path: str | Path,
) -> Path:
    """Render one pinned generic project to a new DOCX artifact."""
    pinned = (
        project.package_id == package.id
        and project.document_type_id == package.document_type_id
        and project.template_version_id == package.template_version_id == template_version.id
        and project.spec_version_id == package.spec_version_id
        and project.renderer_version == package.renderer_version == template_version.renderer_version
        and project.rule_engine_version == package.rule_engine_version == template_version.rule_engine_version
    )
    if not pinned:
        raise GenericDocxRenderError("project/package version snapshot mismatch")
    source_path = resolve_template_source(template_version.definition.source_docx)
    if source_path is None:
        raise GenericDocxRenderError("template source DOCX is missing or unsafe")

    # Explicit anchors are the high-fidelity path for new generic templates:
    # mutate only declared locations and preserve the original OOXML package.
    # Templates without anchors retain the existing thesis-compatible adapter.
    if template_version.definition.anchors:
        image_paths: dict[str, Path] = {}
        for blocks in project.sections.values():
            for block in blocks:
                kind = str(block.kind or "paragraph").lower()
                if kind not in {"image", "figure"}:
                    continue
                data = block.data if isinstance(block.data, dict) else {}
                url = str(data.get("url") or data.get("path") or "")
                image_path = resolve_document_image_path(project.id, url)
                if image_path is None:
                    raise GenericDocxRenderError(f"image block {block.id} has no valid project-owned image")
                image_paths[block.id] = image_path
        try:
            render_anchored_docx(
                source_path,
                out_path,
                template_version.definition.anchors,
                project.field_values,
                project.sections,
                image_paths=image_paths,
            )
        except DocxAnchorError as exc:
            raise GenericDocxRenderError(str(exc)) from exc
        return Path(out_path)

    legacy_definition = _legacy_template_definition(template_version.definition)
    legacy_version = template_version.model_copy(update={"definition": legacy_definition})
    try:
        legacy_template = domain_to_paper_template(template, legacy_version)
        legacy_project = _legacy_project(project, legacy_definition)
        if spec_version is not None:
            legacy_template.page_layout.update(spec_version.layout_overrides)
    except (TypeError, ValueError) as exc:
        raise GenericDocxRenderError(f"generic project cannot be adapted to DOCX: {exc}") from exc
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    export_project_to_docx(legacy_project, legacy_template, str(output), template_path=source_path)
    if not output.is_file() or output.stat().st_size == 0:
        raise GenericDocxRenderError("DOCX exporter did not produce an artifact")
    return output


__all__ = ["GenericDocxRenderError", "render_document_project_to_docx"]
