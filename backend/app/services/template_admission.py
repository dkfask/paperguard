"""Read-only, version-bound template capability assessment.

Admission does not inspect or approve an exported manuscript. It identifies
template constructs the current DOCX checker cannot cover and exposes the
remaining Word/WPS checks before students start writing.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any

from lxml import etree

from app.core.extract.xml_utils import safe_xml_parser
from app.models.template import PaperTemplate, TextStyle
from app.services.docx_output_check import TEXT_STYLE_COVERAGE, style_capability_issues
from app.services.template_source import resolve_template_source
from app.services.upload_security import validate_docx_bytes

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_NS = {
    "w": _W,
    "m": _M,
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "cx": "http://schemas.microsoft.com/office/drawing/2014/chartex",
}
_PAGE_FOOTER_TEXT = set("第页 0123456789")


def _canonical_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _find_source_xml(value: Any, path: str = "definition") -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if key == "source_xml" and item:
                found.append({"code": "source_xml_table", "message": "模板含有原始 OOXML 表格片段，当前准入检查无法逐项核对其结构和样式。", "path": item_path})
            else:
                found.extend(_find_source_xml(item, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_source_xml(item, f"{path}[{index}]"))
    return found


def _part_has_rendered_content(root) -> bool:
    if root.xpath(".//w:t[normalize-space(.)] | .//w:instrText[normalize-space(.)] | .//w:fldSimple | .//w:drawing | .//w:pict | .//w:object | .//w:tbl", namespaces=_NS):
        return True
    return False


def _page_fields(root) -> tuple[int, int]:
    instructions = ["".join(node.itertext()).strip() for node in root.xpath(".//w:instrText", namespaces=_NS)]
    instructions.extend(node.get(f"{{{_W}}}instr", "").strip() for node in root.xpath(".//w:fldSimple", namespaces=_NS))
    page = sum(1 for instruction in instructions if instruction.upper().split()[:1] == ["PAGE"])
    other = len(instructions) - page
    return page, other


def _parse_source_xml(data: bytes):
    root = etree.fromstring(data, safe_xml_parser())
    if root.getroottree().docinfo.doctype:
        raise ValueError("DOCX XML parts must not declare a DTD")
    return root


def _source_findings(source_data: bytes | None, expected_source_sha256: str = "") -> list[dict[str, str]]:
    if source_data is None:
        return []
    findings: list[dict[str, str]] = []
    actual_sha256 = hashlib.sha256(source_data).hexdigest()
    if expected_source_sha256 and actual_sha256 != expected_source_sha256:
        findings.append({"code": "source_checksum_mismatch", "message": "模板源文件摘要与已登记值不一致，需重新导入模板。", "path": "source_docx"})
    try:
        # Apply the exact upload budget before opening/decompressing parts.
        validate_docx_bytes(source_data)
        with zipfile.ZipFile(io.BytesIO(source_data)) as archive:
            names = set(archive.namelist())
            document_name = "word/document.xml"
            if document_name not in names:
                raise ValueError("word/document.xml missing")
            document = _parse_source_xml(archive.read(document_name))
            tables = document.xpath(".//w:tbl", namespaces=_NS)
            if tables:
                findings.append({"code": "source_table_not_mapped", "message": f"Word 源模板包含 {len(tables)} 个原始表格；当前准入规则不能保证封面表格或源表格在学生内容替换后的结构与格式。", "path": "source_docx/word/document.xml"})
            part_names = [name for name in names if name.startswith("word/") and name.endswith(".xml")]
            for name in part_names:
                root = _parse_source_xml(archive.read(name))
                if root.xpath(".//m:oMath | .//m:oMathPara", namespaces=_NS):
                    findings.append({"code": "source_equation_omml", "message": "Word 源模板包含 OMML 公式；公式语义与版式尚不能由当前成品检查器证明。", "path": name})
                chart_nodes = root.xpath(
                    ".//c:chart | .//cx:chart | .//a:graphicData[contains(@uri, '/chart')]",
                    namespaces=_NS,
                )
                if chart_nodes:
                    findings.append({"code": "source_chart_not_mapped", "message": "Word 源模板的 OOXML 部件包含图表对象；当前检查器不能证明图表数据、语义和 Office 渲染结果。", "path": name})
            chart_parts = sorted(name for name in names if name.startswith("word/charts/"))
            if chart_parts:
                findings.append({"code": "source_chart_not_mapped", "message": "Word 源模板包含 chart 部件；当前检查器不能证明图表数据、语义和 Office 渲染结果。", "path": chart_parts[0]})
            for name in sorted(name for name in names if re.fullmatch(r"word/header[^/]*\.xml", name)):
                root = _parse_source_xml(archive.read(name))
                if _part_has_rendered_content(root):
                    findings.append({"code": "unmapped_header", "message": "Word 源模板含有页眉内容或对象；当前检查器不验证页眉内容、域和图形。", "path": name})
            for name in sorted(name for name in names if re.fullmatch(r"word/footer[^/]*\.xml", name)):
                root = _parse_source_xml(archive.read(name))
                page_fields, other_fields = _page_fields(root)
                text = "".join(root.xpath(".//w:t/text()", namespaces=_NS))
                has_table_or_object = bool(root.xpath(".//w:tbl | .//w:drawing | .//w:pict | .//w:object", namespaces=_NS))
                known_page_footer = page_fields == 1 and other_fields == 0 and not has_table_or_object and all(char in _PAGE_FOOTER_TEXT for char in text)
                if _part_has_rendered_content(root) and not known_page_footer:
                    findings.append({"code": "unmapped_footer", "message": "Word 源模板页脚不是受支持的简单页码域；当前检查器无法证明其内容或域结果。", "path": name})
            section_count = len(document.xpath(".//w:sectPr", namespaces=_NS))
            if section_count > 1:
                findings.append({"code": "source_section_layouts", "message": f"Word 源模板包含 {section_count} 个分节布局，当前模板准入没有把它们逐段映射到输出分节。", "path": "source_docx/word/document.xml/sectPr"})
    except Exception:
        findings.append({"code": "source_unreadable", "message": "Word 源文件结构无法读取或不完整，不能评估模板准入。", "path": "source_docx"})
    return findings


def _collect_style_entries(raw_definition: dict[str, Any]) -> tuple[list[tuple[str, Any]], list[dict[str, str]]]:
    """Collect every style object consumed by legacy and generic renderers."""
    entries: list[tuple[str, Any]] = []
    findings: list[dict[str, str]] = []

    def add(path: str, value: Any) -> None:
        if value is None:
            return
        entries.append((path, value))

    styles = raw_definition.get("styles", {})
    if not isinstance(styles, dict):
        findings.append({"code": "styles_unreadable", "message": "模板样式定义无法读取。", "path": "definition.styles"})
    else:
        for style_name, style in styles.items():
            add(f"definition.styles.{style_name}", style)

    def walk_sections(nodes: Any, path: str) -> None:
        if not isinstance(nodes, list):
            findings.append({"code": "style_tree_unreadable", "message": "模板章节样式层级无法读取。", "path": path})
            return
        for index, node in enumerate(nodes):
            node_path = f"{path}[{index}]"
            if not isinstance(node, dict):
                findings.append({"code": "style_tree_unreadable", "message": "模板章节样式层级无法读取。", "path": node_path})
                continue
            add(f"{node_path}.style", node.get("style"))
            if "children" in node:
                walk_sections(node.get("children"), f"{node_path}.children")

    if "sections" in raw_definition:
        walk_sections(raw_definition.get("sections"), "definition.sections")

    cover_blocks = raw_definition.get("cover_blocks", [])
    if cover_blocks is None and "cover_blocks" not in raw_definition:
        cover_blocks = []
    if not isinstance(cover_blocks, list):
        findings.append({"code": "cover_style_tree_unreadable", "message": "模板封面样式层级无法读取。", "path": "definition.cover_blocks"})
    else:
        for block_index, block in enumerate(cover_blocks):
            block_path = f"definition.cover_blocks[{block_index}]"
            if not isinstance(block, dict):
                findings.append({"code": "cover_style_tree_unreadable", "message": "模板封面样式层级无法读取。", "path": block_path})
                continue
            add(f"{block_path}.style", block.get("style"))
            fields = block.get("fields") or []
            if not isinstance(fields, list):
                findings.append({"code": "cover_style_tree_unreadable", "message": "模板封面字段样式层级无法读取。", "path": f"{block_path}.fields"})
                continue
            for field_index, field in enumerate(fields):
                field_path = f"{block_path}.fields[{field_index}]"
                if not isinstance(field, dict):
                    findings.append({"code": "cover_style_tree_unreadable", "message": "模板封面字段样式层级无法读取。", "path": field_path})
                    continue
                add(f"{field_path}.label_style", field.get("label_style"))
                add(f"{field_path}.value_style", field.get("value_style"))
    return entries, findings


def assess_template_definition(
    *,
    template_id: str,
    version: str,
    definition: Any,
    source_data: bytes | None,
    expected_source_sha256: str = "",
) -> dict[str, Any]:
    """Assess a legacy PaperTemplate or generic TemplateDefinition without writes."""
    raw_definition = definition.model_dump(mode="json") if hasattr(definition, "model_dump") else definition
    raw_definition = raw_definition if isinstance(raw_definition, dict) else {}
    blockers: list[dict[str, str]] = []
    blockers.extend(_find_source_xml(raw_definition))
    for index, anchor in enumerate(raw_definition.get("anchors", [])):
        if anchor.get("kind") not in {"placeholder", "section_placeholder"}:
            blockers.append({"code": "anchor_kind_not_verifiable", "message": "该锚点类型尚未具备独立的位置和格式核验；请改用文本占位符或整段章节占位符。", "path": f"definition.anchors[{index}]"})
    if raw_definition.get("source_docx") and source_data is None:
        blockers.append({"code": "source_missing", "message": "模板声明了 Word 源文件，但无法读取对应文件；不能确认准入能力。", "path": "definition.source_docx"})

    cover_blocks = raw_definition.get("cover_blocks", [])
    if isinstance(cover_blocks, list):
        for index, block in enumerate(cover_blocks):
            if not isinstance(block, dict):
                continue
            kind = block.get("kind")
            fields = block.get("fields") or []
            if kind == "table":
                blockers.append({"code": "cover_table", "message": "封面含有表格块；当前准入规则不能保证表格单元格与封面字段映射一致。", "path": f"definition.cover_blocks[{index}]"})
            elif kind == "field" and isinstance(fields, list) and len(fields) > 1:
                blockers.append({"code": "compound_cover_field", "message": "封面字段块包含多个字段；当前准入规则只覆盖单段单字段封面。", "path": f"definition.cover_blocks[{index}]"})

    style_entries, style_tree_findings = _collect_style_entries(raw_definition)
    blockers.extend(style_tree_findings)
    for style_path, raw_style in style_entries:
        if not isinstance(raw_style, dict):
            blockers.append({"code": "style_unreadable", "message": f"样式“{style_path}”无法读取。", "path": style_path})
            continue
        try:
            style = TextStyle.model_validate(raw_style)
        except Exception:
            blockers.append({"code": "style_invalid", "message": f"样式“{style_path}”包含无法核验的显式约束。", "path": style_path})
            continue
        for issue in style_capability_issues(style):
            blockers.append({
                "code": issue["code"],
                "message": f"样式“{style_path}”包含暂不支持的约束：{issue['property']}。",
                "path": f"{style_path}.{issue['property']}",
            })
    blockers.extend(_source_findings(source_data, expected_source_sha256))
    blockers = list({(item["code"], item["path"]): item for item in blockers}.values())
    source_sha256 = hashlib.sha256(source_data).hexdigest() if source_data is not None else ""
    definition_sha256 = _canonical_hash(raw_definition)
    assessment_hash = _canonical_hash({
        "assessment_version": "template-admission/2",
        "template_id": template_id,
        "version": version,
        "definition_sha256": definition_sha256,
        "source_docx_sha256": source_sha256,
    })
    manual_checks = [
        {"code": "office_pagination", "message": "在 Word 或 WPS 中检查最终分页、字体替换、表格跨页和内容裁切。"},
        {"code": "office_toc_refresh", "message": "在 Word 或 WPS 中更新目录与页码域，并核对显示结果。"},
    ]
    report = {
        "status": "blocked" if blockers else "office_review_required",
        "template_id": template_id,
        "version": version,
        "assessment_hash": assessment_hash,
        "definition_sha256": definition_sha256,
        "source_docx_sha256": source_sha256,
        "auto_verifiable_constraints": [
            {"property": field, "rule": rule}
            for field, rule in TEXT_STYLE_COVERAGE.items()
        ],
        "blockers": blockers,
        "manual_checks": manual_checks,
        "can_use_for_draft": not blockers,
        "can_claim_compliant_delivery": False,
    }
    profile = raw_definition.get("extensions",{}).get("source_execution")
    if profile:
        from app.services.source_template_profile import paper_from_definition, verify_profile
        try:
            legacy = paper_from_definition(template_id,version,raw_definition)
            verify_profile(legacy,source_data)
            if expected_source_sha256 and expected_source_sha256 != source_sha256:
                raise ValueError("published source mismatch")
        except Exception:
            report["blockers"].append({"code":"source_profile_invalid","message":"已确认的母版执行配置与当前版本或源文件不一致。","path":"definition.extensions.source_execution"})
            report.update(status="blocked",can_use_for_draft=False)
        else:
            report.update(status="office_review_required",can_use_for_draft=True,blockers=[],source_profile_sha256=profile["profile_sha256"])
    return report


def assess_paper_template(template: PaperTemplate, source_data: bytes | None) -> dict[str, Any]:
    report = assess_template_definition(
        template_id=template.id,
        version=template.version,
        definition=template,
        source_data=source_data,
    )
    if template.source_execution:
        from app.services.source_template_profile import verify_profile
        try:
            profile = verify_profile(template, source_data)
        except Exception:
            report["blockers"].append({"code":"source_profile_invalid","message":"母版执行配置已失效，请重新确认规范与映射。","path":"source_execution"})
            report.update(status="blocked", can_use_for_draft=False)
        else:
            report.update(status="office_review_required", can_use_for_draft=True, blockers=[], source_profile_sha256=profile["profile_sha256"])
    return report


def assess_template_candidate(candidate: Any) -> dict[str, Any]:
    asset = getattr(candidate, "source_asset", None)
    source_path = resolve_template_source(asset.storage_key) if asset else None
    source_data = source_path.read_bytes() if source_path is not None else None
    return assess_template_definition(
        template_id=candidate.template_id,
        version=candidate.version,
        definition=candidate.definition,
        source_data=source_data,
        expected_source_sha256=asset.checksum if asset else "",
    )


__all__ = ["assess_paper_template", "assess_template_candidate", "assess_template_definition"]
