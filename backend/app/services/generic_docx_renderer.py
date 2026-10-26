"""Render generic document projects through the existing DOCX exporter."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.image.image import Image as DocxImage
from lxml import etree
from app.core.extract.xml_utils import safe_xml_parser
from app.core.export.docx_exporter import export_project_to_docx
from app.domain.documents.hashing import canonical_hash, document_project_content_hash
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
from app.services.docx_output_check import INSPECTOR_VERSION, docx_chart_parts, inspect_docx_output, require_output_check_report, source_template_bytes


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
    if definition.extensions.get("source_execution"):
        return definition.model_copy(update={"sections":[_normalize_section(n) for n in definition.sections]})
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
            image=ImageBlock(url=url, caption=str(data.get("caption") or ""),width_cm=data.get("width_cm"),height_cm=data.get("height_cm"),alignment=data.get("alignment")),
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
        if definition.extensions.get("source_execution") and node.kind in {"cover","toc"}:
            if blocks and any(block.text.strip() or block.kind not in {"paragraph","text"} for block in blocks):
                raise GenericDocxRenderError("封面与目录由母版管理，不能作为自由正文编辑")
            continue
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

    strategy = template_version.definition.export_strategy
    has_profile = bool(template_version.definition.extensions.get("source_execution"))
    if strategy == "source_template" and not template_version.definition.anchors and not has_profile:
        raise GenericDocxRenderError("source-template export requires stable DOCX anchors")

    # Explicit anchors are the high-fidelity path for new generic templates:
    # mutate only declared locations and preserve the original OOXML package.
    # Templates without anchors retain the existing thesis-compatible adapter.
    if strategy != "legacy_adapter" and template_version.definition.anchors and not has_profile:
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


def inspect_document_project_docx_output(
    data: bytes,
    project: DocumentProject,
    package: DocumentPackage,
    template: DocumentTemplate,
    template_version: TemplateVersion,
    spec_version: SpecVersion | None,
    *,
    context_kind: str,
) -> dict[str, Any]:
    """Check emitted bytes against the pinned adapter or explicit-anchor profile."""
    definition = template_version.definition
    source_path = resolve_template_source(definition.source_docx)
    has_profile = bool(definition.extensions.get("source_execution"))
    if definition.export_strategy != "legacy_adapter" and definition.anchors and not has_profile:
        report = _inspect_anchored_output(data, project, definition, source_path)
        source_asset = template_version.extensions.get("source_asset") or {}
        if (source_asset.get("checksum") and source_asset["checksum"] != report.get("source_template_sha256")) or (source_asset.get("storage_key") and source_asset["storage_key"] != definition.source_docx):
            report["checks"].append({"rule": "template.source_checksum", "status": "fail", "object": "template", "expected": source_asset.get("checksum"), "actual": report.get("source_template_sha256"), "evidence": "compare actual source bytes with the immutable published template asset"})
            report["check_count"] = len(report["checks"])
            report["status"] = "fail"
        policy_issues = []
        if spec_version is not None and (spec_version.format_overrides or spec_version.layout_overrides):
            policy_issues.append("源模板锚点导出尚不支持规范中的字体、段落或页面覆盖；需先在 Word 源模板中落实约束并重新导入")
        if any(node.style is not None or node.style_ref for node in _flatten_sections(definition)):
            policy_issues.append("源模板锚点导出尚不支持章节的额外样式覆盖；当前以固定源模板段落和样式为准")
        if source_path is not None and definition.styles:
            from app.services.import_template import import_template_styles
            source_styles = import_template_styles(source_path).get("styles") or {}
            for key, style in definition.styles.items():
                source_style = source_styles.get(key)
                if source_style is None or style.model_dump(mode="json") != StyleDefinition.model_validate(source_style.model_dump(mode="json")).model_dump(mode="json"):
                    policy_issues.append(f"样式 {key} 与源模板导入结果不同；锚点导出不能把未应用的样式覆盖标记为通过")
        if policy_issues:
            report["unsupported"].extend(policy_issues)
            report["checks"].extend({"rule": "capability.anchor_format_overrides", "status": "unverifiable", "object": "template", "expected": "source template formatting without unapplied overrides", "actual": reason, "evidence": "source-preserving renderer capability boundary"} for reason in policy_issues)
            report["check_count"] = len(report["checks"])
            if report["status"] == "pass":
                report["status"] = "unverifiable"
        report.update({
            "contract_version": 1,
            "inspector_version": INSPECTOR_VERSION,
            "context_kind": context_kind,
            "project_id": project.id,
            "project_revision": project.revision,
            "project_content_hash": document_project_content_hash(project),
            "template_id": template.id,
            "template_version": template_version.version,
            "template_hash": canonical_hash({
                "package": package.model_dump(mode="json"),
                "template": template.model_dump(mode="json"),
                "template_version": template_version.model_dump(mode="json"),
                "spec_version": spec_version.model_dump(mode="json") if spec_version is not None else None,
            }),
            "output_sha256": hashlib.sha256(data).hexdigest(),
        })
        return report
    legacy_definition = _legacy_template_definition(definition)
    legacy_version = template_version.model_copy(update={"definition": legacy_definition})
    legacy_template = domain_to_paper_template(template, legacy_version)
    if spec_version is not None:
        legacy_template.page_layout.update(spec_version.layout_overrides)
    legacy_project = _legacy_project(project, legacy_definition)
    report = inspect_docx_output(
        data,
        legacy_project,
        legacy_template,
        source_template_bytes=source_template_bytes(source_path),
        context_kind=context_kind,
    )
    if not has_profile and (definition.export_strategy != "legacy_adapter" or definition.anchors):
        message = "锚点型或自动策略模板的源文档版式映射尚未纳入成品检查能力"
        report["unsupported"].append(message)
        report["checks"].append({
            "rule": "capability.generic_template_strategy",
            "status": "unverifiable",
            "object": "template",
            "expected": "legacy_adapter without anchors",
            "actual": definition.export_strategy,
            "evidence": message,
        })
        report["check_count"] = len(report["checks"])
        report["passed_count"] = sum(item["status"] == "pass" for item in report["checks"])
        if report["status"] == "pass":
            report["status"] = "unverifiable"
    if has_profile:
        asset = template_version.extensions.get("source_asset") or {}
        if (asset.get("checksum") and asset["checksum"] != report.get("source_template_sha256")) or (asset.get("storage_key") and asset["storage_key"] != definition.source_docx) or (spec_version is not None and (spec_version.format_overrides or spec_version.layout_overrides)):
            report["checks"].append({"rule":"master.published_basis","status":"fail","object":"template/spec","expected":"approved source and compiled rules","actual":"asset or unapplied overrides mismatch","evidence":"immutable published asset/spec binding"})
            report["check_count"] = len(report["checks"])
            report["status"] = "fail"
        report.update({"project_content_hash":document_project_content_hash(project),"template_id":template.id,"template_version":template_version.version,
                       "template_hash":canonical_hash({"package":package.model_dump(mode="json"),"template":template.model_dump(mode="json"),"template_version":template_version.model_dump(mode="json"),"spec_version":spec_version.model_dump(mode="json") if spec_version is not None else None})})
    return report


def _check_anchor_story_structure(source_parts, output_parts, project, definition, check, unsupported) -> None:
    """Check actual positions, static structure and inherited source formatting.

    This is a postcondition walk over source/output XML, independent of the
    renderer. A value occurring elsewhere in the story is not sufficient.
    """
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
    NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main", "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships", "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture"}

    def signature(node, *, mask_text=False):
        if node is None:
            return None
        attrs = tuple(sorted((key, value) for key, value in node.attrib.items() if key != XML_SPACE))
        text = "" if mask_text and node.tag == f"{W}t" else node.text or ""
        return (node.tag, attrs, text, tuple(signature(child, mask_text=mask_text) for child in node))

    def visible(node):
        return "".join(item.text or "" for item in node.iter(f"{W}t"))

    def paragraph(actual, reference, text, obj, rule="content.anchor_position"):
        right_kind = actual is not None and actual.tag == f"{W}p"
        matches = right_kind and visible(actual) == text
        check(rule, "pass" if matches else "fail", obj, text, visible(actual) if actual is not None else None, "compare text at its exact source-derived paragraph position")
        style_ok = right_kind and signature(actual, mask_text=True) == signature(reference, mask_text=True)
        check("format.anchor_source_formatting", "pass" if style_ok else "fail", obj, "source paragraph properties and every run format preserved", "preserved" if style_ok else "changed", "compare source/output paragraph and run XML; shared style/default parts are checked separately")

    for part in sorted({item.part for item in definition.anchors}):
        if part not in source_parts or part not in output_parts:
            continue
        source_root = etree.fromstring(source_parts[part], safe_xml_parser())
        output_root = etree.fromstring(output_parts[part], safe_xml_parser())
        anchors = [item for item in definition.anchors if item.part == part]
        unsupported_kinds = {item.kind for item in anchors} - {"placeholder", "section_placeholder"}
        if unsupported_kinds:
            unsupported.append("锚点位置与格式复核暂只覆盖文本占位符和整段章节占位符：" + ", ".join(sorted(unsupported_kinds)))
            continue
        fields = {item.selector: "" if project.field_values.get(item.key) is None else str(project.field_values[item.key]) for item in anchors if item.kind == "placeholder"}
        sections = {item.selector: item.section_key or item.key for item in anchors if item.kind == "section_placeholder"}
        field_pattern = re.compile("|".join(re.escape(value) for value in sorted(fields, key=len, reverse=True))) if fields else None
        for anchor in anchors:
            found = any(anchor.selector in visible(node) for node in source_root.iter(f"{W}p"))
            check("template.anchor_source_location", "pass" if found else "fail", f"{part}/anchor:{anchor.key}", "declared placeholder present in source", found, "read declared source paragraph anchors independently")
        rels_part = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
        rels_xml = output_parts.get(rels_part)
        relations = {item.get("Id"): item for item in etree.fromstring(rels_xml, safe_xml_parser())} if rels_xml else {}

        def image_checks(actual, block, block_data, obj):
            blips = actual.xpath(".//a:blip", namespaces=NS)
            inline = actual.xpath(".//wp:inline", namespaces=NS)
            relation = relations.get(blips[0].get(f"{{{NS['r']}}}embed")) if len(blips) == 1 else None
            target = posixpath.normpath(posixpath.join(posixpath.dirname(part), relation.get("Target", ""))) if relation is not None and relation.get("TargetMode") != "External" else ""
            path = resolve_document_image_path(project.id, str(block_data.get("url") or block_data.get("path") or ""))
            expected = path.read_bytes() if path and path.is_file() else None
            actual_bytes = output_parts.get(target)
            asset_ok = expected is not None and expected == actual_bytes
            check("content.anchor_image_asset", "pass" if asset_ok else "fail", obj, hashlib.sha256(expected).hexdigest() if expected else None, hashlib.sha256(actual_bytes).hexdigest() if actual_bytes else None, "resolve the picture relationship at this exact anchor position to project-owned image bytes")
            expected_size = None
            if expected is not None:
                image = DocxImage.from_blob(expected)
                cx = min(int(6.5 * 914400), max(1, image.px_width * 9525))
                expected_size = (cx, max(1, int(image.px_height * cx / image.px_width)))
            extent = inline[0].find(f"{{{NS['wp']}}}extent") if len(inline) == 1 else None
            actual_size = (int(extent.get("cx", "0")), int(extent.get("cy", "0"))) if extent is not None else None
            shape_sizes = actual.xpath(".//pic:spPr/a:xfrm/a:ext", namespaces=NS)
            shape_size = (int(shape_sizes[0].get("cx", "0")), int(shape_sizes[0].get("cy", "0"))) if len(shape_sizes) == 1 else None
            size_ok = expected_size is not None and actual_size == expected_size == shape_size
            check("format.anchor_image_dimensions", "pass" if size_ok else "fail", obj, expected_size, {"inline": actual_size, "shape": shape_size}, "compare actual inline and drawing dimensions against image pixels, aspect ratio and the 6.5 inch width limit")

        def walk(source, output, obj):
            if source.tag != output.tag or source.attrib != output.attrib:
                check("template.anchor_story_structure", "fail", obj, "source element and attributes preserved", "changed", "compare the story tree outside dynamic text")
                return
            source_children, output_children = list(source), list(output)
            cursor = 0
            for index, child in enumerate(source_children):
                child_obj = f"{obj}/{index}"
                source_text = visible(child) if child.tag == f"{W}p" else ""
                section_key = sections.get(source_text.strip())
                if child.tag == f"{W}p" and section_key is not None:
                    blocks = sorted(project.sections.get(section_key, []), key=lambda block: (block.order, block.id))
                    for block in blocks:
                        block_data = block.data if isinstance(block.data, dict) else {}
                        kind = str(block.kind or "paragraph").lower()
                        descriptors = []
                        if kind in {"image", "figure"}:
                            descriptors.append(("image", None))
                            if block_data.get("caption"):
                                descriptors.append(("text", str(block_data["caption"])))
                        elif kind in {"table", "grid"}:
                            if block_data.get("caption"):
                                descriptors.append(("text", str(block_data["caption"])))
                            descriptors.append(("table", None))
                        elif kind in {"paragraph", "text"}:
                            if block.text:
                                descriptors.append(("text", block.text))
                        else:
                            unsupported.append(f"锚点内容块类型尚不能独立核验：{section_key}/{block.id}/{kind}")
                        for block_kind, text in descriptors:
                            actual = output_children[cursor] if cursor < len(output_children) else None
                            block_obj = f"{part}/section:{section_key}/block:{block.id}/{cursor}"
                            if block_kind == "text":
                                paragraph(actual, child, text, block_obj)
                            else:
                                required_tag = f"{W}p" if block_kind == "image" else f"{W}tbl"
                                right_kind = actual is not None and actual.tag == required_tag
                                check("structure.anchor_block_order", "pass" if right_kind else "fail", block_obj, block_kind, actual.tag if actual is not None else None, "read each rich block at its source-derived sibling position")
                                if right_kind and block_kind == "image":
                                    source_ppr = signature(child.find(f"{W}pPr"))
                                    output_ppr = signature(actual.find(f"{W}pPr"))
                                    check("format.anchor_image_paragraph", "pass" if source_ppr == output_ppr else "fail", block_obj, "source paragraph formatting", "preserved" if source_ppr == output_ppr else "changed", "compare image paragraph properties with the replaced source paragraph")
                                    image_checks(actual, block, block_data, block_obj)
                                if right_kind and block_kind == "table":
                                    cells = block_data.get("cells") or []
                                    actual_cells = [[visible(cell) for cell in row.findall(f"{W}tc")] for row in actual.findall(f"{W}tr")]
                                    rows, cols = int(block_data.get("rows") or 1), int(block_data.get("cols") or 1)
                                    expected_cells = [[str(cells[r][c] or "") if r < len(cells) and c < len(cells[r]) else "" for c in range(cols)] for r in range(rows)]
                                    check("content.anchor_positioned_table", "pass" if actual_cells == expected_cells else "fail", block_obj, expected_cells, actual_cells, "compare the table at this anchor position rather than all tables in the story")
                                    dimensions = [len(row) for row in actual_cells]
                                    check("structure.anchor_table_dimensions", "pass" if len(actual_cells) == rows and all(size == cols for size in dimensions) else "fail", block_obj, {"rows": rows, "cols": cols}, dimensions, "read positioned table row and cell counts")
                                    style = actual.find(f"{W}tblPr/{W}tblStyle")
                                    grid = [item.get(f"{W}w") for item in actual.findall(f"{W}tblGrid/{W}gridCol")]
                                    format_ok = style is not None and style.get(f"{W}val") == "TableGrid" and grid == [str(max(1, 9000 // cols))] * cols
                                    for row in actual.findall(f"{W}tr"):
                                        format_ok = format_ok and all(item.tag == f"{W}tc" for item in row)
                                        for cell in row.findall(f"{W}tc"):
                                            width = cell.find(f"{W}tcPr/{W}tcW")
                                            format_ok = format_ok and width is not None and dict(width.attrib) == {f"{W}type": "dxa", f"{W}w": str(max(1, 9000 // cols))}
                                            format_ok = format_ok and not cell.xpath(".//w:rPr | .//w:pPr | .//w:vMerge | .//w:gridSpan", namespaces={"w": W[1:-1]}) and len(cell.findall(f"{W}p")) == 1
                                    check("format.anchor_table_grid", "pass" if format_ok else "fail", block_obj, "TableGrid with equal declared column widths", {"style": style.get(f"{W}val") if style is not None else None, "grid": grid}, "compare generated table style and grid with the supported anchor table profile")
                            cursor += 1
                    continue
                actual = output_children[cursor] if cursor < len(output_children) else None
                if actual is None:
                    check("template.anchor_story_structure", "fail", child_obj, "source element present", "missing", "source/output tree alignment")
                elif child.tag == f"{W}p":
                    expected_text = field_pattern.sub(lambda match: fields[match.group(0)], source_text) if field_pattern is not None else source_text
                    if expected_text != source_text:
                        paragraph(actual, child, expected_text, child_obj, rule="content.anchor_field")
                    else:
                        ok = signature(child) == signature(actual)
                        check("template.static_story_structure", "pass" if ok else "fail", child_obj, "unchanged static paragraph", "preserved" if ok else "changed", "compare complete static text, order, paragraph properties and run formatting")
                else:
                    walk(child, actual, child_obj)
                cursor += 1
            check("template.anchor_story_child_count", "pass" if cursor == len(output_children) else "fail", obj, cursor, len(output_children), "reject extra or duplicated output elements after accounting for declared anchor expansions")

        walk(source_root, output_root, part)


def _inspect_anchored_output(data: bytes, project: DocumentProject, definition: TemplateDefinition, source_path: Path | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    unsupported: list[str] = []

    def check(rule: str, status: str, obj: str, expected, actual, evidence: str) -> None:
        checks.append({"rule": rule, "status": status, "object": obj, "expected": expected, "actual": actual, "evidence": evidence})

    if not data:
        check("docx.open", "unverifiable", "document", "non-empty DOCX", "empty", "no output bytes")
        return _anchor_report(checks, unsupported, None)
    try:
        output_charts = docx_chart_parts(data)
        Document(io.BytesIO(data))
        with zipfile.ZipFile(io.BytesIO(data), "r") as output_zip:
            output_parts = {name: output_zip.read(name) for name in output_zip.namelist()}
    except Exception as exc:
        check("docx.open", "unverifiable", "document", "valid DOCX", type(exc).__name__, "python-docx or ZIP parser could not open emitted bytes")
        return _anchor_report(checks, unsupported, None)
    check("docx.open", "pass", "document", "valid DOCX", "opened", "parsed emitted DOCX bytes")
    drawing_ids = [node.get("id") for name, raw in output_parts.items() if name.startswith("word/") and name.endswith(".xml") for node in etree.fromstring(raw, safe_xml_parser()).iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr")]
    ids_ok = all(str(value).isdigit() and int(value) > 0 for value in drawing_ids) and len(drawing_ids) == len(set(drawing_ids))
    check("structure.anchor_picture_ids", "pass" if ids_ok else "fail", "document", "positive unique drawing object ids", drawing_ids, "inspect emitted picture identifiers across document stories")
    if output_charts:
        unsupported.append("Office 图表对象尚不能由锚点成品检查证明")
        check("capability.chart_objects", "unverifiable", "document", "no unsupported chart objects", output_charts, "inspect actual output package chart markers and parts")
    if source_path is None or not source_path.is_file():
        unsupported.append("锚点模板源 DOCX 不可读取，无法核对模板固定部件与字段来源")
        return _anchor_report(checks, unsupported, hashlib.sha256(data).hexdigest())
    try:
        source_charts = docx_chart_parts(source_path.read_bytes())
        with zipfile.ZipFile(source_path, "r") as source_zip:
            source_parts = {name: source_zip.read(name) for name in source_zip.namelist()}
    except Exception as exc:
        check("template.source_open", "fail", "template", "valid source DOCX", type(exc).__name__, "pinned source package could not be parsed")
        return _anchor_report(checks, unsupported, hashlib.sha256(data).hexdigest())
    check("template.source_open", "pass", "template", "valid source DOCX", hashlib.sha256(source_path.read_bytes()).hexdigest(), "read the pinned source template package")
    if source_charts:
        unsupported.append("源模板的 Office 图表对象尚未映射到锚点成品规则")
        check("capability.source_chart_objects", "unverifiable", "template", "no unsupported chart objects", source_charts, "inspect pinned source chart markers and parts")

    # Anchored rendering may rewrite declared story XML, its relationships,
    # package content types and added media. Other source package parts must
    # remain byte-identical.
    story_parts = {anchor.part for anchor in definition.anchors}
    mutable = {"[Content_Types].xml"}
    mutable.update(story_parts)
    mutable.update(str(Path(part).parent / "_rels" / f"{Path(part).name}.rels").replace("\\", "/") for part in story_parts)
    retained = [name for name in source_parts if name not in mutable]
    changed_static = [name for name in retained if output_parts.get(name) != source_parts[name]]
    missing_static = [name for name in retained if name not in output_parts]
    package_ok = not changed_static and not missing_static
    check("template.static_package_preserved", "pass" if package_ok else "fail", "template", "all non-anchor package parts unchanged", {"changed": changed_static[:8], "missing": missing_static[:8]}, "compare source and output ZIP members outside declared story parts and renderer-owned package additions")
    try:
        for part in story_parts:
            rels_part = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
            source_rels = etree.fromstring(source_parts[rels_part], safe_xml_parser()) if rels_part in source_parts else []
            output_rels = etree.fromstring(output_parts[rels_part], safe_xml_parser()) if rels_part in output_parts else []
            source_map = {item.get("Id"): dict(item.attrib) for item in source_rels}
            output_map = {item.get("Id"): dict(item.attrib) for item in output_rels}
            preserved = all(output_map.get(key) == value for key, value in source_map.items())
            additions = [value for key, value in output_map.items() if key not in source_map]
            valid_additions = all(value.get("Type", "").endswith("/image") and value.get("TargetMode") != "External" and posixpath.normpath(posixpath.join(posixpath.dirname(part), value.get("Target", ""))) in output_parts for value in additions)
            rels_ok = preserved and valid_additions and len(output_map) == len(output_rels)
            check("template.anchor_relationships_preserved", "pass" if rels_ok else "fail", rels_part, "source relationships unchanged; only internal image additions", rels_ok, "compare every original relationship by Id and inspect actual added picture targets")
        source_types = etree.fromstring(source_parts["[Content_Types].xml"], safe_xml_parser())
        output_types = etree.fromstring(output_parts["[Content_Types].xml"], safe_xml_parser())
        def type_map(root):
            return {(item.tag, item.get("Extension") or item.get("PartName")): dict(item.attrib) for item in root}
        source_map, output_map = type_map(source_types), type_map(output_types)
        types_ok = all(output_map.get(key) == value for key, value in source_map.items()) and len(output_map) == len(output_types)
        check("template.anchor_content_types_preserved", "pass" if types_ok else "fail", "[Content_Types].xml", "original content types unchanged", types_ok, "compare pinned package content types while allowing added image declarations")
        _check_anchor_story_structure(source_parts, output_parts, project, definition, check, unsupported)
    except Exception as exc:
        check("template.anchor_inspection", "unverifiable", "document", "readable supported output structure", type(exc).__name__, "output properties could not be independently verified")

    report_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return _anchor_report(checks, unsupported, report_hash)


def _anchor_report(checks: list[dict[str, Any]], unsupported: list[str], source_hash: str | None) -> dict[str, Any]:
    if unsupported:
        for reason in unsupported:
            checks.append({"rule": "capability.supported", "status": "unverifiable", "object": "document", "expected": "supported explicit-anchor profile", "actual": reason, "evidence": "capability boundary"})
    statuses = {item["status"] for item in checks}
    status = "pass" if checks and statuses == {"pass"} else "fail" if "fail" in statuses else "unverifiable"
    return {
        "inspector_version": INSPECTOR_VERSION,
        "context_kind": "generic-anchor",
        "status": status,
        "checks": checks,
        "unsupported": unsupported,
        "check_count": len(checks),
        "passed_count": sum(item["status"] == "pass" for item in checks),
        "source_template_sha256": source_hash or "",
    }


def require_document_project_docx_output(*args, **kwargs) -> dict[str, Any]:
    return require_output_check_report(inspect_document_project_docx_output(*args, **kwargs))


__all__ = ["GenericDocxRenderError", "inspect_document_project_docx_output", "require_document_project_docx_output", "render_document_project_to_docx"]
