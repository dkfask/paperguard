"""Independent, fail-closed checks over generated DOCX bytes.

This module intentionally reads the emitted OOXML through python-docx. The
project/template models describe expectations only; they are never treated as
evidence that the generated file has those properties.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, Twips
from lxml import etree

from app.core.extract.xml_utils import safe_xml_parser
from app.models.project import WritingProject
from app.models.template import PaperTemplate, SectionType, TextStyle
from app.services.document_assets import resolve_document_image_path
from app.services.project_versions import project_content_hash
from app.services.references import (ReferenceInlineMappingError, reference_block_text, reference_inline_runs,
                                     reference_number_for, uses_sequential_reference_numbering)
from app.services.upload_security import validate_docx_bytes


INSPECTOR_VERSION = "docx-output-check/7"
MASTER_INSPECTOR_VERSION = "docx-output-check/10-master"
_PREVIOUS_INSPECTOR_VERSIONS = {
    INSPECTOR_VERSION: frozenset({"docx-output-check/5"}),
    MASTER_INSPECTOR_VERSION: frozenset({"docx-output-check/6-master", "docx-output-check/9-master"}),
}
_CHART_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "cx": "http://schemas.microsoft.com/office/drawing/2014/chartex",
}
_PT_TWIPS = 20.0
_CM_TWIPS = 1440.0 / 2.54
_FALLBACK_PAGE_LAYOUT_CM = {
    "page_width_cm": 21.0,
    "page_height_cm": 29.7,
    "margin_top_cm": 2.54,
    "margin_bottom_cm": 2.54,
    "margin_left_cm": 3.17,
    "margin_right_cm": 3.17,
}
TEXT_STYLE_COVERAGE = {
    "font_zh": "format.font_east_asia",
    "font_en": "format.font_ascii",
    "size_pt": "format.font_size",
    "bold": "format.bold",
    "italic": "format.italic",
    "underline": "format.underline",
    "align": "format.alignment",
    "line_spacing": "format.line_spacing",
    "line_spacing_rule": "format.line_spacing",
    "space_before_pt": "format.space_before",
    "space_after_pt": "format.space_after",
    "first_line_indent_chars": "format.indent",
    "hanging_indent_cm": "format.indent",
    "color": "format.font_color",
    "highlight": "format.highlight",
    "vert_align": "format.vert_align",
    "page_break_before": "format.page_break_before",
}


class DocxOutputCheckError(ValueError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__("DOCX 输出检查未通过")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def docx_chart_parts(data: bytes) -> list[str]:
    """Return chart package parts or XML markers that the checker cannot verify."""
    found: set[str] = set()
    validate_docx_bytes(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        found.update(name for name in names if name.startswith("word/charts/"))
        for name in sorted(name for name in names if name.startswith("word/") and name.endswith(".xml")):
            root = etree.fromstring(archive.read(name), safe_xml_parser())
            if root.getroottree().docinfo.doctype:
                raise ValueError("DOCX XML parts must not declare a DTD")
            if root.xpath(
                ".//c:chart | .//cx:chart | .//a:graphicData[contains(@uri, '/chart')]",
                namespaces=_CHART_NS,
            ):
                found.add(name)
    return sorted(found)


def template_output_hash(template: PaperTemplate) -> str:
    # The new empty default must not change identities frozen before this field
    # existed. Nonempty provenance remains part of the template identity.
    excluded = {"admission_provenance"} if not template.admission_provenance else set()
    return _sha256(template.model_dump_json(exclude=excluded).encode("utf-8"))


def output_check_identity(report: dict[str, Any] | None) -> dict[str, Any]:
    report = report if isinstance(report, dict) else {}
    return {
        key: report.get(key)
        for key in (
            "inspector_version", "project_id", "project_revision", "project_content_hash",
            "template_id", "template_version", "template_hash", "source_template_sha256",
        )
    }


def expected_output_check_identity(
    project: WritingProject,
    template: PaperTemplate,
    source_data: bytes | None,
) -> dict[str, Any]:
    return {
        "inspector_version": MASTER_INSPECTOR_VERSION if template.source_execution else INSPECTOR_VERSION,
        "project_id": project.id,
        "project_revision": project.revision,
        "project_content_hash": project_content_hash(project),
        "template_id": template.id,
        "template_version": template.version,
        "template_hash": template_output_hash(template),
        "source_template_sha256": _sha256(source_data) if source_data is not None else "",
    }


def verify_report_identity(report: dict[str, Any] | None, expected: dict[str, Any], *,
                           allow_previous_inspector: bool = False) -> bool:
    if not report or not expected:
        return False
    if ("source_profile_sha256" in expected
            and report.get("source_profile_sha256") != expected.get("source_profile_sha256")):
        return False
    actual = output_check_identity(report)
    wanted = {key: expected.get(key) for key in actual}
    if (allow_previous_inspector
            and actual["inspector_version"] in _PREVIOUS_INSPECTOR_VERSIONS.get(wanted["inspector_version"], ())):
        wanted["inspector_version"] = actual["inspector_version"]
    return actual == wanted


def _style_for(project_section, template: PaperTemplate, block_index: int) -> TextStyle:
    node = next((item for item in _flatten(template.sections) if item.key == project_section.key), None)
    kind = str(getattr(project_section.type, "value", project_section.type))
    if block_index == -1 and node and node.style:
        return node.style
    # Section titles with special semantic roles use heading1 formatting;
    # paragraphs inside heading sections use body formatting.
    if block_index == -1 and kind in {"references", "acknowledgement", "appendix"}:
        return template.styles.get("heading1") or TextStyle()
    block_kind = str(getattr(project_section.paragraphs[block_index].type, "value", project_section.paragraphs[block_index].type)) if block_index >= 0 else ""
    if block_kind == "paragraph" and block_index > 0:
        previous_kind = str(getattr(project_section.paragraphs[block_index - 1].type, "value", project_section.paragraphs[block_index - 1].type))
        caption_label = "图" if previous_kind == "image" else "表" if previous_kind == "table" else ""
        if caption_label and _is_caption_text(project_section.paragraphs[block_index].text, caption_label):
            key = "figure_caption" if caption_label == "图" else "table_caption"
            style = template.styles.get(key) or template.styles.get("body") or TextStyle()
            return style
    if block_kind == "image":
        key = "figure_caption"
    elif block_kind == "table":
        key = "table_caption"
    elif block_index >= 0 and kind in {"heading1", "heading2", "heading3"}:
        key = "body"
    elif block_index >= 0 and kind == "abstract":
        key = "abstract_body"
    elif block_index >= 0 and kind == "english_abstract":
        key = "english_abstract_body"
    else:
        key = kind
    style = template.styles.get(key) or template.styles.get("body") or TextStyle()
    # In the legacy exporter, pagination for bibliography/acknowledgement/
    # appendix sections belongs to the section heading, not every body entry.
    if block_index >= 0 and kind in {"references", "acknowledgement", "appendix"} and style.page_break_before:
        style = style.model_copy(update={"page_break_before": False})
    return style


def _flatten(nodes):
    for node in nodes:
        yield node
        yield from _flatten(node.children)


def _expected_text(project: WritingProject) -> list[tuple[str, str, int]]:
    """Return required heading/text paragraphs in source order."""
    expected: list[tuple[str, str, int]] = []
    chapter = 0
    figure_number = 0
    table_number = 0
    for section in project.sections:
        kind = str(getattr(section.type, "value", section.type))
        if kind in {"cover", "toc"}:
            continue  # generated/template material has separate diagnostics below
        if kind == "heading1":
            chapter += 1
            figure_number = 0
            table_number = 0
        if section.title and kind not in {"keywords", "english_keywords"}:
            expected.append((section.title, section.key, -1))
        reference_number = 0
        for index, block in enumerate(section.paragraphs):
            block_kind = str(getattr(block.type, "value", block.type))
            if block_kind == "paragraph":
                has_reference_content = bool(reference_block_text(block).strip())
                if kind == "references" and has_reference_content:
                    reference_number += 1
                text = reference_block_text(block, reference_number_for(block, reference_number) if kind == "references" and has_reference_content else None)
                if kind in {"keywords", "english_keywords"} and text.strip():
                    prefix = "关键词：" if kind == "keywords" else "Key Words: "
                    if not text.strip().startswith(("关键词", "Key Words", "Keywords")):
                        text = prefix + text.strip()
                if text.strip():
                    expected.append((text, section.key, index))
            elif block_kind in {"image", "table"}:
                details = getattr(block, block_kind, None)
                label = "图" if block_kind == "image" else "表"
                if block_kind == "image":
                    figure_number += 1
                    number = figure_number
                else:
                    table_number += 1
                    number = table_number
                if not _has_explicit_neighbor_caption(section.paragraphs, index, label):
                    caption = _strip_caption_number(str(getattr(details, "caption", "") or "")) if details else ""
                    expected.append((f"{label} {chapter}.{number}" + (f" {caption}" if caption else ""), section.key, index))
            # Table cells and image geometry are checked from emitted objects below.
    return expected


_CAPTION_NUMBER_RE = re.compile(r"^(?P<kind>[图表])\s*\d+(?:\.\d+)*(?:-\d+)?\s*(?P<title>\S.*)$")


def _strip_caption_number(value: str) -> str:
    match = _CAPTION_NUMBER_RE.match(value or "")
    return match.group("title").strip() if match else (value or "").strip()


def _is_caption_text(value: str, label: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 100:
        return False
    match = _CAPTION_NUMBER_RE.match(text)
    if not match or match.group("kind") != label:
        return False
    title = match.group("title").strip()
    if not title or title.startswith(("所示", "如下", "可见", "可知", "可以看出")):
        return False
    return not title.endswith(("。", "！", "？", "；"))


def _has_explicit_neighbor_caption(blocks, index: int, label: str) -> bool:
    return index + 1 < len(blocks) and str(getattr(blocks[index + 1].type, "value", blocks[index + 1].type)) == "paragraph" and _is_caption_text(blocks[index + 1].text, label)


def _field_records(element) -> list[dict[str, Any]]:
    """Read simple and complete complex field instructions from OOXML."""
    fields: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for node in element.iter():
        if node.tag == qn("w:fldSimple"):
            fields.append({
                "instruction": str(node.get(qn("w:instr")) or "").strip(),
                "complete": True,
                "dirty": False,
                "has_result": any(item.tag == qn("w:t") and bool(item.text) for item in node.iter()),
            })
            continue
        if node.tag == qn("w:fldChar"):
            kind = str(node.get(qn("w:fldCharType")) or "").lower()
            if kind == "begin":
                stack.append({"instruction_parts": [], "separated": False, "dirty": node.get(qn("w:dirty")), "has_result": False})
            elif kind == "separate" and stack:
                stack[-1]["separated"] = True
            elif kind == "end" and stack:
                item = stack.pop()
                instruction = "".join(item["instruction_parts"]).strip()
                fields.append({
                    "instruction": instruction,
                    "complete": bool(instruction and item["separated"]),
                    "dirty": str(item["dirty"] or "").lower() in {"true", "1", "on"},
                    "has_result": item["has_result"],
                })
                if stack and instruction:
                    stack[-1]["instruction_parts"].append(instruction)
            continue
        if node.tag == qn("w:instrText") and stack:
            stack[-1]["instruction_parts"].append(node.text or "")
        elif node.tag == qn("w:t") and stack and stack[-1]["separated"] and node.text:
            stack[-1]["has_result"] = True
    return fields


def _cover_expectations(template: PaperTemplate, project: WritingProject) -> tuple[list[dict[str, Any]], list[str]]:
    expected: list[dict[str, Any]] = []
    unsupported: list[str] = []
    if template.cover_blocks:
        for block_index, block in enumerate(template.cover_blocks):
            if block.kind == "text":
                expected.append({"key": f"block:{block_index}", "text": block.text, "required": True, "missing_value": False, "paragraph_style": block.style or TextStyle(), "run_styles": None})
            elif block.kind == "field" and len(block.fields) == 1:
                field = block.fields[0]
                value = project.meta.get(field.key, "") if field.key else field.value
                value_style = field.value_style or TextStyle()
                expected.append({
                    "key": field.key or f"block:{block_index}",
                    "text": f"{field.label}{value}",
                    "required": bool(field.key and next((item.required for item in template.meta_fields if item.key == field.key), True)),
                    "missing_value": bool(field.key and not str(value).strip()),
                    "paragraph_style": value_style,
                    "run_styles": ([{"text": field.label, "style": field.label_style or value_style}] if field.label else []) + [{"text": str(value), "style": value_style}],
                })
            elif block.kind == "spacer":
                continue
            else:
                unsupported.append("封面表格或多字段封面块尚未纳入本切片的单段字段检查")
        return expected, unsupported
    for field in template.meta_fields:
        value = str(project.meta.get(field.key, "") or "")
        expected.append({
            "key": field.key,
            "text": f"{field.label}：{value}",
            "required": bool(field.required),
            "missing_value": bool(field.required and not value.strip()),
            "paragraph_style": template.styles.get("cover_meta") or TextStyle(),
            "run_styles": None,
        })
    return expected, unsupported


def _check_cover_content(doc, template: PaperTemplate, project: WritingProject, check) -> set[int]:
    expected, unsupported = _cover_expectations(template, project)
    for item in unsupported:
        check("capability.cover_layout", "unverifiable", obj="cover", expected="single paragraph text/field cover", actual=item, evidence="cover tables and compound field blocks remain outside this slice")
    paragraphs = _content_paragraphs(doc)
    matched: set[int] = set()
    cursor = 0
    for item in expected:
        found = next((index for index in range(cursor, len(paragraphs)) if paragraphs[index].text == item["text"]), None)
        status = "pass" if found is not None and not item["missing_value"] else "fail"
        check(
            "cover.field_content" if item["key"] else "cover.static_content",
            status,
            obj=f"cover:{item['key']}",
            expected={"text": item["text"], "required": item["required"]},
            actual=paragraphs[found].text if found is not None else None,
            evidence="match generated cover paragraph to the frozen metadata/template field",
        )
        if found is not None:
            matched.add(found)
            cursor = found + 1
            style = item.get("paragraph_style") or TextStyle()
            _check_paragraph_style(check, paragraphs[found], style, f"cover:{item['key']}", unsupported)
            runs = [run for run in paragraphs[found].runs if run.text]
            run_specs = item.get("run_styles")
            if run_specs is None:
                run_specs = [{"text": None, "style": style} for _ in runs]
            if len(run_specs) != len(runs):
                unsupported.append(f"封面字段 run 无法按模板边界关联样式: {item['key']}")
                check("capability.cover_run_mapping", "unverifiable", obj=f"cover:{item['key']}", expected=len(run_specs), actual=len(runs), evidence="cover run boundaries do not match the declared label/value styles")
            for run_index, run in enumerate(runs):
                spec = run_specs[run_index] if run_index < len(run_specs) else {"style": style}
                _check_run_style(check, run, paragraphs[found], spec["style"], f"cover:{item['key']}/run:{run_index}", unsupported=unsupported)
        if item["missing_value"]:
            check("cover.required_field_value", "fail", obj=f"cover:{item['key']}", expected="non-empty required metadata", actual="empty", evidence="required cover value is missing from the project snapshot")
    if template.cover_blocks:
        paragraph_index = 0
        for block_index, block in enumerate(template.cover_blocks):
            if block.kind == "spacer":
                if paragraph_index >= len(doc.paragraphs):
                    check("structure.cover_spacer", "fail", obj=f"cover:block:{block_index}", expected="empty paragraph spacer", actual="missing", evidence="cover spacer must exist in output order")
                    continue
                paragraph = doc.paragraphs[paragraph_index]
                is_empty = not paragraph.text.strip()
                check("structure.cover_spacer", "pass" if is_empty else "fail", obj=f"cover:block:{block_index}", expected="empty paragraph spacer", actual=paragraph.text, evidence="match spacer position against the emitted cover paragraph sequence")
                style = block.style or TextStyle()
                _check_paragraph_style(check, paragraph, style, f"cover:block:{block_index}", unsupported)
                _check_page_break_before(doc, paragraph, style, f"cover:block:{block_index}", check)
                paragraph_index += 1
            elif block.kind in {"text", "field"}:
                wanted = block.text if block.kind == "text" else f"{block.fields[0].label}{project.meta.get(block.fields[0].key, '') if block.fields[0].key else block.fields[0].value}" if block.fields else ""
                match_index = next((index for index in range(paragraph_index, len(doc.paragraphs)) if doc.paragraphs[index].text == wanted), None)
                if match_index is not None:
                    paragraph_index = match_index + 1
            # Cover tables are already reported as unsupported above and do not
            # create top-level paragraphs in python-docx's document.paragraphs.
    return matched


def _section_layout_cm(section) -> dict[str, float | None]:
    attrs = {
        "page_width_cm": "page_width",
        "page_height_cm": "page_height",
        "margin_top_cm": "top_margin",
        "margin_bottom_cm": "bottom_margin",
        "margin_left_cm": "left_margin",
        "margin_right_cm": "right_margin",
    }
    return {
        key: float(getattr(section, attr).cm) if getattr(section, attr) is not None else None
        for key, attr in attrs.items()
    }


def _expected_section_page_formats(template: PaperTemplate, project: WritingProject) -> tuple[dict[int, str | None] | None, bool]:
    cover_expected = bool(template.cover_blocks or template.meta_fields)
    front_types = {"abstract", "english_abstract", "keywords", "english_keywords", "toc"}
    body_types = {"heading1", "heading2", "heading3", "body", "references", "acknowledgement", "appendix"}
    kinds = [str(getattr(section.type, "value", section.type)) for section in project.sections]
    has_front = any(kind in front_types for kind in kinds) or any(
        str(getattr(node.type, "value", node.type)) == "toc" for node in _flatten(template.sections)
    )
    has_body = any(kind in body_types for kind in kinds)
    if cover_expected and has_front and has_body:
        return {0: None, 1: "upperRoman", 2: "decimal"}, True
    if not cover_expected and not has_front and has_body:
        return {0: "decimal"}, True
    if not cover_expected and not has_front and not has_body and len(project.sections) == 0:
        return {0: "decimal"}, True
    return None, False


def _check_sections_and_page_fields(doc, template: PaperTemplate, project: WritingProject, source_template_bytes: bytes | None, check) -> None:
    expected_formats, recognized_groups = _expected_section_page_formats(template, project)
    source_sections = None
    if source_template_bytes:
        try:
            source_doc = Document(io.BytesIO(source_template_bytes))
            source_sections = list(source_doc.sections)
        except Exception:
            source_sections = None
    source_has_page_field = bool(source_sections) and any(
        field["instruction"].split()[0:1] == ["PAGE"]
        for section in source_sections
        for field in _field_records(section.footer._element)
    )
    if expected_formats is None:
        check("capability.section_numbering", "unverifiable", obj="sections", expected="recognized cover/front/body grouping", actual=len(doc.sections), evidence="page-number section grouping is outside this supported template profile")
    else:
        expected_count = max(expected_formats) + 1
        check("section.count", "pass" if len(doc.sections) == expected_count else "fail", obj="document", expected=expected_count, actual=len(doc.sections), evidence="read emitted Word section properties")
        if len(doc.sections) == expected_count:
            for index, expected_format in expected_formats.items():
                sect_pr = doc.sections[index]._sectPr
                pg_num = sect_pr.find(qn("w:pgNumType"))
                actual_format = pg_num.get(qn("w:fmt")) if pg_num is not None else None
                actual_start = pg_num.get(qn("w:start")) if pg_num is not None else None
                if expected_format is None:
                    ok = pg_num is None
                    expected = {"format": None, "has_page_field": False}
                else:
                    ok = (actual_format or "decimal") == expected_format and (actual_start in {None, "1"})
                    expected = {"format": expected_format, "start": 1}
                check("section.page_number_format", "pass" if ok else "fail", obj=f"section:{index}", expected=expected, actual={"format": actual_format, "start": actual_start}, evidence="read w:pgNumType from emitted section properties")

            for index, section in enumerate(doc.sections):
                footer_fields = _field_records(section.footer._element)
                page_fields = [field for field in footer_fields if field["instruction"].split()[0:1] == ["PAGE"]]
                expected_page_field = expected_formats[index] is not None and (
                    source_template_bytes is None or source_has_page_field
                )
                field_ok = (len(page_fields) == 1 and page_fields[0]["complete"] and page_fields[0]["has_result"]) if expected_page_field else not page_fields
                check("section.page_field", "pass" if field_ok else "fail", obj=f"section:{index}/footer", expected="one complete PAGE field" if expected_page_field else "no PAGE field", actual=[field["instruction"] for field in footer_fields], evidence="read page-number field instructions from the section footer part")

                section_type = section._sectPr.find(qn("w:type"))
                actual_type = section_type.get(qn("w:val")) if section_type is not None else "nextPage"
                expected_type = "nextPage" if index < expected_count - 1 else "nextPage"
                check("section.break_type", "pass" if actual_type == expected_type else "fail", obj=f"section:{index}", expected=expected_type, actual=actual_type, evidence="read emitted section break type; this does not assert rendered pagination")

    layout_expectations = dict(template.page_layout)
    if not layout_expectations:
        if not template.source_docx:
            layout_expectations = dict(_FALLBACK_PAGE_LAYOUT_CM)
        elif source_sections and len(source_sections) == 1:
            layout_expectations = _section_layout_cm(source_sections[0])
        elif source_sections:
            check("capability.source_section_layout", "unverifiable", obj="template", expected="single source section or explicit page_layout", actual=len(source_sections), evidence="source template has multiple independent section layouts")
    elif template.source_docx and source_sections and len(source_sections) > 1:
        check("capability.source_section_layout", "unverifiable", obj="template", expected="single source section or resolved section mapping", actual=len(source_sections), evidence="source template section layout mapping is not defined")
    if layout_expectations:
        for index, section in enumerate(doc.sections):
            actual_values = _section_layout_cm(section)
            for key, expected_value in layout_expectations.items():
                if expected_value is None or key not in actual_values:
                    continue
                actual_value = actual_values[key]
                ok = actual_value is not None and math.isclose(actual_value, float(expected_value), abs_tol=0.03)
                check(f"page.{key}", "pass" if ok else "fail", obj=f"section:{index}", expected=float(expected_value), actual=actual_value, evidence="read emitted section page geometry and margins")
    if not recognized_groups and len(doc.sections) > 1:
        check("capability.section_group_mapping", "unverifiable", obj="sections", expected="known semantic group per section", actual=len(doc.sections), evidence="section-to-paper-role mapping is not defined for this template")


def _check_toc_field(doc, template: PaperTemplate, check, unsupported: list[str] | None = None) -> set[int]:
    toc_nodes = [node for node in _flatten(template.sections) if str(getattr(node.type, "value", node.type)) == "toc"]
    if not toc_nodes:
        return set()
    titles = {node.title for node in toc_nodes}
    paragraphs = _content_paragraphs(doc)
    title_matches = [index for index, paragraph in enumerate(paragraphs) if paragraph.text in titles]
    fields = [field for field in _field_records(doc.element.body) if field["instruction"].split()[0:1] == ["TOC"]]
    unknown_profile = not fields and template.id != "default-gb7713"
    title_status = "pass" if len(title_matches) == len(toc_nodes) else "unverifiable" if unknown_profile else "fail"
    check("structure.toc_title", title_status, obj="toc", expected=len(toc_nodes), actual=len(title_matches), evidence="match each configured directory title to an emitted paragraph")
    if unknown_profile:
        reason = "非内置论文模板的目录缓存/域与更新策略未被映射，需先定义其目录输出约定"
        if unsupported is not None:
            unsupported.append(reason)
        check("capability.toc_profile", "unverifiable", obj="toc", expected="known TOC field profile", actual="no emitted TOC field", evidence=reason)
        return set(title_matches)
    valid_field = (
        len(fields) == 1
        and fields[0]["complete"]
        and fields[0]["has_result"]
        and fields[0]["dirty"]
        and '\\o "1-3"' in fields[0]["instruction"]
    )
    check("structure.toc_field", "pass" if valid_field else "fail", obj="toc", expected='one complete TOC field with \\o "1-3"', actual=fields, evidence="read the actual TOC field instruction and begin/separate/end structure")
    update_fields = doc.settings._element.find(qn("w:updateFields"))
    update_value = str(update_fields.get(qn("w:val")) or "") if update_fields is not None else ""
    update_ok = update_value.lower() in {"true", "1", "on"}
    check("structure.toc_update_on_open", "pass" if update_ok else "fail", obj="settings:updateFields", expected=True, actual=update_value or None, evidence="read Word field-refresh setting; no claim is made that Word has refreshed pagination")
    generated_indices = set(title_matches)
    for index, paragraph in enumerate(paragraphs):
        if any(field["instruction"].split()[0:1] == ["TOC"] for field in _field_records(paragraph._p)):
            generated_indices.add(index)
    return generated_indices


def _style_chain(style):
    while style is not None:
        yield style
        style = style.base_style


def _doc_default_rpr(run):
    styles = run.part.styles.element
    defaults = styles.find(qn("w:docDefaults"))
    rpr_default = defaults.find(qn("w:rPrDefault")) if defaults is not None else None
    return rpr_default.find(qn("w:rPr")) if rpr_default is not None else None


def _doc_default_run_property(run, name: str):
    rpr = _doc_default_rpr(run)
    tags = {"bold": "w:b", "italic": "w:i", "underline": "w:u", "size": "w:sz"}
    node = rpr.find(qn(tags[name])) if rpr is not None and name in tags else None
    if node is None:
        return None
    value = node.get(qn("w:val"))
    if name == "size":
        return Pt(float(value) / 2) if value is not None else None
    if name == "underline":
        return str(value or "single").lower() not in {"none", "0", "false", "off"}
    if name in {"bold", "italic"}:
        return str(value or "1").lower() not in {"0", "false", "off", "no"}
    return None


def _effective_run_property(run, name: str, paragraph=None):
    value = getattr(run.font, name)
    if value is not None:
        return value
    for style in (*_style_chain(run.style), *_style_chain(paragraph.style if paragraph is not None else None)):
        value = getattr(style.font, name, None)
        if value is not None:
            return value
    return _doc_default_run_property(run, name)


def _font_name(run, east_asia: bool, paragraph=None) -> str | None:
    node = run._element.rPr
    rfonts = node.find(qn("w:rFonts")) if node is not None else None
    attrs = (qn("w:eastAsia"),) if east_asia else (qn("w:ascii"), qn("w:hAnsi"))
    if rfonts is not None:
        for attr in attrs:
            if rfonts.get(attr):
                return rfonts.get(attr)
    for style in (*_style_chain(run.style), *_style_chain(paragraph.style if paragraph is not None else None)):
        style_rpr = style._element.rPr
        style_fonts = style_rpr.find(qn("w:rFonts")) if style_rpr is not None else None
        if style_fonts is not None:
            for attr in attrs:
                if style_fonts.get(attr):
                    return style_fonts.get(attr)
    defaults = _doc_default_rpr(run)
    default_fonts = defaults.find(qn("w:rFonts")) if defaults is not None else None
    if default_fonts is not None:
        for attr in attrs:
            if default_fonts.get(attr):
                return default_fonts.get(attr)
    return _effective_run_property(run, "name", paragraph)


def _paragraph_format_value(paragraph, name: str):
    value = getattr(paragraph.paragraph_format, name)
    if value is not None:
        return value
    for style in _style_chain(paragraph.style):
        value = getattr(style.paragraph_format, name)
        if value is not None:
            return value
    defaults = paragraph.part.styles.element.find(qn("w:docDefaults"))
    ppr_default = defaults.find(qn("w:pPrDefault")) if defaults is not None else None
    ppr = ppr_default.find(qn("w:pPr")) if ppr_default is not None else None
    if ppr is not None:
        tags = {"space_before": "before", "space_after": "after", "first_line_indent": "firstLine", "left_indent": "left"}
        spacing_or_indent = ppr.find(qn("w:spacing")) if name in {"space_before", "space_after"} else ppr.find(qn("w:ind"))
        if spacing_or_indent is not None and name in tags:
            raw = spacing_or_indent.get(qn(f"w:{tags[name]}"))
            if raw is not None:
                return Pt(float(raw) / 20) if name in {"space_before", "space_after"} else Twips(float(raw))
    return None


def _paragraph_line_rule(paragraph) -> str | None:
    nodes = []
    direct_ppr = paragraph._p.pPr
    if direct_ppr is not None:
        nodes.append(direct_ppr.find(qn("w:spacing")))
    for style in _style_chain(paragraph.style):
        ppr = style._element.pPr
        nodes.append(ppr.find(qn("w:spacing")) if ppr is not None else None)
    defaults = paragraph.part.styles.element.find(qn("w:docDefaults"))
    ppr_default = defaults.find(qn("w:pPrDefault")) if defaults is not None else None
    default_ppr = ppr_default.find(qn("w:pPr")) if ppr_default is not None else None
    nodes.append(default_ppr.find(qn("w:spacing")) if default_ppr is not None else None)
    for node in nodes:
        if node is not None and node.get(qn("w:line")) is not None:
            rule = node.get(qn("w:lineRule"))
            return rule or "auto"
    return None


_HIGHLIGHT_XML = {
    "yellow": "yellow", "green": "green", "cyan": "cyan", "magenta": "magenta",
    "blue": "blue", "darkblue": "darkBlue", "darkcyan": "darkCyan", "darkgreen": "darkGreen",
    "darkmagenta": "darkMagenta", "darkred": "darkRed", "darkyellow": "darkYellow",
    "lightgray": "lightGray", "darkgray": "darkGray", "black": "black",
}


def style_capability_issues(style: TextStyle) -> list[dict[str, Any]]:
    """Return explicit TextStyle values that this exporter/checker cannot verify."""
    issues: list[dict[str, Any]] = []
    for key in sorted(style.model_extra or {}):
        issues.append({
            "code": "unsupported_style_property",
            "property": key,
            "expected": sorted(TEXT_STYLE_COVERAGE),
            "actual": style.model_extra[key],
            "message": f"unsupported explicit TextStyle property {key!r}",
        })
    if style.line_spacing_rule not in {"auto", "exact", "atLeast"}:
        issues.append({"code": "style_line_spacing_rule", "property": "line_spacing_rule", "expected": ["auto", "exact", "atLeast"], "actual": style.line_spacing_rule, "message": "unsupported explicit line_spacing_rule"})
    color = (style.color or "").strip().lstrip("#")
    if color and not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        issues.append({"code": "style_color", "property": "color", "expected": "six-digit RRGGBB or empty", "actual": style.color, "message": "invalid explicit TextStyle.color"})
    if style.highlight is not None and style.highlight.lower() not in _HIGHLIGHT_XML:
        issues.append({"code": "style_highlight", "property": "highlight", "expected": sorted(_HIGHLIGHT_XML), "actual": style.highlight, "message": "unsupported explicit TextStyle.highlight"})
    if style.vert_align not in {None, "superscript", "subscript"}:
        issues.append({"code": "style_vert_align", "property": "vert_align", "expected": [None, "superscript", "subscript"], "actual": style.vert_align, "message": "unsupported explicit TextStyle.vert_align"})
    return issues


def _style_capabilities(style: TextStyle, obj: str, check, unsupported: list[str]) -> None:
    for issue in style_capability_issues(style):
        reason = f"{issue['message']} at {obj}"
        unsupported.append(reason)
        check(
            f"capability.{issue['code']}", "unverifiable", obj=obj,
            expected=issue["expected"], actual=issue["actual"], evidence=reason,
        )


def _effective_rpr_value(run, paragraph, tag: str, attr: str = "w:val") -> str | None:
    candidates = [run._element.rPr]
    candidates.extend(style._element.rPr for style in _style_chain(run.style))
    candidates.extend(style._element.rPr for style in _style_chain(paragraph.style))
    for rpr in candidates:
        node = rpr.find(qn(tag)) if rpr is not None else None
        if node is not None:
            return node.get(qn(attr)) or ("1" if tag in {"w:b", "w:i"} else None)
    return None


def _check_run_style(check, run, paragraph, style: TextStyle, obj: str, *, bold_extra: bool = False, italic_extra: bool = False, underline_extra: bool = False, highlight_override: str | None = None, vert_align_override: str | None = None, unsupported: list[str] | None = None) -> None:
    if unsupported is None:
        unsupported = []
    _style_capabilities(style, obj, check, unsupported)
    for prop, expected in (("bold", bool(style.bold or bold_extra)), ("italic", bool(style.italic or italic_extra)), ("underline", bool(style.underline or underline_extra))):
        actual_value = _effective_run_property(run, prop, paragraph)
        actual = bool(actual_value) if actual_value is not None else False
        check(f"format.{prop}", "pass" if actual == expected else "fail", obj=obj, expected=expected, actual=actual, evidence="read effective character property from the emitted run")
    size = _effective_run_property(run, "size", paragraph)
    size_pt = float(size.pt) if size is not None else None
    check("format.font_size", "pass" if size_pt is not None and abs(size_pt - style.size_pt) <= 0.05 else "unverifiable" if size_pt is None else "fail", obj=obj, expected=style.size_pt, actual=size_pt, evidence="read emitted run size with character-style inheritance")
    for label, actual_font, expected_font in (("east_asia", _font_name(run, True, paragraph), style.font_zh), ("ascii", _font_name(run, False, paragraph), style.font_en)):
        normalized = actual_font.casefold() if actual_font else None
        check(f"format.font_{label}", "pass" if normalized == expected_font.casefold() else "unverifiable" if not actual_font else "fail", obj=obj, expected=expected_font, actual=actual_font, evidence="read emitted run font declaration and character-style inheritance")

    color_node_value = _effective_rpr_value(run, paragraph, "w:color")
    has_theme_color = any(
        rpr is not None and (node := rpr.find(qn("w:color"))) is not None and node.get(qn("w:themeColor"))
        for rpr in [run._element.rPr, *(style._element.rPr for style in _style_chain(run.style)), *(style._element.rPr for style in _style_chain(paragraph.style))]
    )
    if run.font.color.rgb is not None:
        actual_color = str(run.font.color.rgb)
    elif has_theme_color:
        actual_color = None
    elif color_node_value in {None, "auto"}:
        actual_color = "000000"
    else:
        actual_color = color_node_value
    expected_color = ((style.color or "").strip().lstrip("#") or "000000").upper()
    check("format.font_color", "pass" if actual_color and actual_color.upper() == expected_color else "unverifiable" if actual_color is None else "fail", obj=obj, expected=expected_color, actual=actual_color, evidence="read emitted RGB run color; empty template color exports as explicit black")

    expected_highlight_value = highlight_override if highlight_override is not None else style.highlight
    if expected_highlight_value is not None and expected_highlight_value.lower() not in _HIGHLIGHT_XML and unsupported is not None:
        reason = f"unsupported explicit highlight override={expected_highlight_value!r} at {obj}"
        unsupported.append(reason)
        check("capability.inline_highlight", "unverifiable", obj=obj, expected=sorted(_HIGHLIGHT_XML), actual=expected_highlight_value, evidence=reason)
    expected_highlight = _HIGHLIGHT_XML.get(expected_highlight_value.lower()) if expected_highlight_value else None
    actual_highlight = _effective_rpr_value(run, paragraph, "w:highlight")
    check("format.highlight", "pass" if actual_highlight == expected_highlight else "fail", obj=obj, expected=expected_highlight, actual=actual_highlight, evidence="read emitted highlight value from run properties")

    expected_vert_align = vert_align_override if vert_align_override is not None else style.vert_align
    if expected_vert_align not in {None, "superscript", "subscript"} and unsupported is not None:
        reason = f"unsupported explicit vertical alignment override={expected_vert_align!r} at {obj}"
        unsupported.append(reason)
        check("capability.inline_vert_align", "unverifiable", obj=obj, expected=[None, "superscript", "subscript"], actual=expected_vert_align, evidence=reason)
    actual_vert_align = _effective_rpr_value(run, paragraph, "w:vertAlign")
    expected_vert_align = expected_vert_align if expected_vert_align in {"superscript", "subscript"} else None
    check("format.vert_align", "pass" if actual_vert_align == expected_vert_align else "fail", obj=obj, expected=expected_vert_align, actual=actual_vert_align, evidence="read emitted superscript/subscript run property")


def _has_page_break_before(doc, paragraph) -> bool:
    ppr = paragraph._p.pPr
    direct = ppr.find(qn("w:pageBreakBefore")) if ppr is not None else None
    if direct is not None:
        value = direct.get(qn("w:val"))
        if value is None or value.lower() not in {"0", "false", "off", "no"}:
            return True
    for style in _style_chain(paragraph.style):
        style_ppr = style._element.pPr
        node = style_ppr.find(qn("w:pageBreakBefore")) if style_ppr is not None else None
        if node is not None:
            value = node.get(qn("w:val"))
            if value is None or value.lower() not in {"0", "false", "off", "no"}:
                return True
    paragraphs = list(doc.paragraphs)
    current = next((index for index, item in enumerate(paragraphs) if item._p is paragraph._p), None)
    if current is None:
        return False
    for previous in reversed(paragraphs[:current]):
        ppr = previous._p.pPr
        sect_pr = ppr.find(qn("w:sectPr")) if ppr is not None else None
        if sect_pr is not None:
            section_type = sect_pr.find(qn("w:type"))
            section_value = section_type.get(qn("w:val")) if section_type is not None else "nextPage"
            if section_value in {None, "nextPage"}:
                return True
        if previous._p.xpath(".//w:br[@w:type='page']"):
            return True
        if previous.text.strip():
            break
    return False


def _check_page_break_before(doc, paragraph, style: TextStyle, obj: str, check) -> None:
    if style.page_break_before:
        actual = _has_page_break_before(doc, paragraph)
        check("format.page_break_before", "pass" if actual else "fail", obj=obj, expected=True, actual=actual, evidence="check an emitted pageBreakBefore property, explicit page break, or next-page section boundary before this element")
    else:
        ppr = paragraph._p.pPr
        direct = ppr.find(qn("w:pageBreakBefore")) if ppr is not None else None
        actual = None
        if direct is not None:
            value = direct.get(qn("w:val"))
            actual = value is None or value.lower() not in {"0", "false", "off", "no"}
        else:
            for para_style in _style_chain(paragraph.style):
                style_ppr = para_style._element.pPr
                node = style_ppr.find(qn("w:pageBreakBefore")) if style_ppr is not None else None
                if node is not None:
                    value = node.get(qn("w:val"))
                    actual = value is None or value.lower() not in {"0", "false", "off", "no"}
                    break
        normalized = bool(actual) if actual is not None else False
        check("format.page_break_before", "pass" if not normalized else "fail", obj=obj, expected=False, actual=normalized, evidence="inspect the emitted paragraph and inherited paragraph style pageBreakBefore setting")


def _check_paragraph_style(check, paragraph, style: TextStyle, obj: str, unsupported: list[str]) -> None:
    _style_capabilities(style, obj, check, unsupported)
    actual_alignment = _paragraph_format_value(paragraph, "alignment")
    align_map = {0: "left", 1: "center", 2: "right", 3: "justify", 4: "distribute"}
    alignment_name = align_map.get(int(actual_alignment)) if actual_alignment is not None else None
    check("format.alignment", "pass" if alignment_name == style.align.value else "fail", obj=obj, expected=style.align.value, actual=alignment_name, evidence="read effective paragraph alignment")
    line_spacing = _paragraph_format_value(paragraph, "line_spacing")
    line_rule = _paragraph_line_rule(paragraph)
    line_value = float(line_spacing.pt) if hasattr(line_spacing, "pt") else float(line_spacing) if line_spacing is not None else None
    if style.line_spacing_rule in {"exact", "atLeast"}:
        line_ok = line_rule == style.line_spacing_rule and line_value is not None and abs(line_value - style.line_spacing) <= 0.05
    else:
        line_ok = line_rule in (None, "auto") and line_value is not None and abs(line_value - style.line_spacing) <= 0.02
    check("format.line_spacing", "pass" if line_ok else "fail", obj=obj, expected={"rule": style.line_spacing_rule, "value": style.line_spacing}, actual={"rule": line_rule, "value": line_value}, evidence="read effective paragraph spacing and OOXML lineRule")
    for prop, expected_pt in (("space_before", style.space_before_pt), ("space_after", style.space_after_pt)):
        value = _paragraph_format_value(paragraph, prop)
        actual_pt = float(value.pt) if hasattr(value, "pt") else float(value) if value is not None else 0.0
        check(f"format.{prop}", "pass" if abs(actual_pt - expected_pt) <= 0.05 else "fail", obj=obj, expected=expected_pt, actual=actual_pt, evidence="read effective paragraph spacing in points")
    first = _paragraph_format_value(paragraph, "first_line_indent")
    actual_twips = float(first.twips) if first is not None else 0.0
    ind = paragraph._p.pPr.find(qn("w:ind")) if paragraph._p.pPr is not None else None
    first_chars = ind.get(qn("w:firstLineChars")) if ind is not None else None
    hanging_chars = ind.get(qn("w:hangingChars")) if ind is not None else None
    expected_twips = style.first_line_indent_chars * style.size_pt * _PT_TWIPS if style.first_line_indent_chars else -style.hanging_indent_cm * _CM_TWIPS if style.hanging_indent_cm else 0
    if first_chars is not None and hanging_chars is None:
        indent_ok = style.hanging_indent_cm == 0 and abs(float(first_chars) / 100 - style.first_line_indent_chars) <= 0.01
        actual_indent: Any = {"first_line_chars": float(first_chars) / 100}
    elif hanging_chars is not None and first_chars is None:
        expected_hanging_chars = style.hanging_indent_cm * _CM_TWIPS / (style.size_pt * _PT_TWIPS)
        indent_ok = style.first_line_indent_chars == 0 and abs(float(hanging_chars) / 100 - expected_hanging_chars) <= 0.01
        actual_indent = {"hanging_chars": float(hanging_chars) / 100}
    elif first_chars is not None or hanging_chars is not None:
        indent_ok = False
        actual_indent = {"first_line_chars": first_chars, "hanging_chars": hanging_chars}
    else:
        indent_ok = abs(actual_twips - expected_twips) <= 2
        actual_indent = {"first_line_twips": actual_twips}
    check("format.indent", "pass" if indent_ok else "fail", obj=obj, expected={"first_line_indent_chars": style.first_line_indent_chars, "hanging_indent_cm": style.hanging_indent_cm}, actual=actual_indent, evidence="read effective first-line or hanging indentation")


def _content_paragraphs(doc) -> list:
    # Tables are structural blocks and their cell text is checked as content,
    # but they are not mixed into paragraph-to-section style matching.
    return [paragraph for paragraph in doc.paragraphs if paragraph.text.strip()]


def _check_table_and_image_outputs(doc, project: WritingProject, template: PaperTemplate, check, unsupported: list[str]) -> None:
    table_blocks = []
    image_blocks = []
    for section in project.sections:
        for index, block in enumerate(section.paragraphs):
            kind = str(getattr(block.type, "value", block.type))
            if kind == "table" and block.table:
                table_blocks.append((section, index, block.table))
                if block.table.source_xml:
                    unsupported.append(f"source_xml complex table is outside the supported table profile: {section.key}/{index}")
            elif kind == "image" and block.image:
                image_blocks.append((section, index, block.image))

    simple_tables = [(section, index, table) for section, index, table in table_blocks if not table.source_xml]
    if len(doc.tables) != len(simple_tables):
        check("structure.table_count", "fail", obj="document", expected=len(simple_tables), actual=len(doc.tables), evidence="compare emitted top-level tables with structured table blocks")
    for table_index, (section, block_index, expected) in enumerate(simple_tables[:len(doc.tables)]):
        actual = doc.tables[table_index]
        actual_rows, actual_cols = len(actual.rows), len(actual.columns)
        check("structure.table_dimensions", "pass" if actual_rows == expected.rows and actual_cols == expected.cols else "fail", obj=f"table:{table_index}", expected={"rows": expected.rows, "cols": expected.cols}, actual={"rows": actual_rows, "cols": actual_cols}, evidence="read emitted table row and grid column counts")
        grid_cols = actual._tbl.tblGrid.gridCol_lst if actual._tbl.tblGrid is not None else []
        grid_widths = [int(col.get(qn("w:w"), "0")) for col in grid_cols]
        has_merge = bool(actual._tbl.xpath(".//w:gridSpan | .//w:vMerge"))
        grid_ok = len(grid_cols) == expected.cols and all(width > 0 for width in grid_widths) and not has_merge
        check("structure.table_grid", "pass" if grid_ok else "fail", obj=f"table:{table_index}", expected={"grid_columns": expected.cols, "positive_widths": True, "merged_cells": False}, actual={"grid_columns": len(grid_cols), "widths_twips": grid_widths, "merged_cells": has_merge}, evidence="inspect emitted table grid widths and merge markers")
        expected_cells = expected.cells or []
        for row_index in range(min(actual_rows, expected.rows)):
            for col_index in range(min(actual_cols, expected.cols)):
                wanted = str(expected_cells[row_index][col_index] if row_index < len(expected_cells) and col_index < len(expected_cells[row_index]) else "")
                cell = actual.cell(row_index, col_index)
                actual_text = cell.text
                check("content.table_cell", "pass" if actual_text == wanted else "fail", obj=f"table:{table_index}/row:{row_index}/cell:{col_index}", expected=wanted, actual=actual_text, evidence="compare emitted cell text to the project snapshot")
                expected_paragraph_count = 1
                check("structure.table_cell_paragraphs", "pass" if len(cell.paragraphs) == expected_paragraph_count else "fail", obj=f"table:{table_index}/row:{row_index}/cell:{col_index}", expected=expected_paragraph_count, actual=len(cell.paragraphs), evidence="structured table cells render as one paragraph")
                cell_style = template.styles.get("table_cell") or TextStyle()
                for paragraph_index, paragraph in enumerate(cell.paragraphs):
                    obj = f"table:{table_index}/row:{row_index}/cell:{col_index}/paragraph:{paragraph_index}"
                    _check_paragraph_style(check, paragraph, cell_style, obj, unsupported)
                    _check_page_break_before(doc, paragraph, cell_style, obj, check)
                    cell_runs = [run for run in paragraph.runs if run.text]
                    if not cell_runs and wanted:
                        unsupported.append(f"table cell text cannot be mapped to a formatted run: {obj}")
                        check("capability.table_cell_run", "unverifiable", obj=obj, expected="text run for non-empty cell", actual=0, evidence="cell run style must be checked")
                    for run_index, run in enumerate(cell_runs):
                        _check_run_style(check, run, paragraph, cell_style, f"{obj}/run:{run_index}", unsupported=unsupported)

    if len(doc.inline_shapes) != len(image_blocks):
        check("structure.image_count", "fail", obj="document", expected=len(image_blocks), actual=len(doc.inline_shapes), evidence="compare emitted inline pictures with project image blocks")
    for image_index, (section, block_index, expected) in enumerate(image_blocks[:len(doc.inline_shapes)]):
        shape = doc.inline_shapes[image_index]
        asset_path = resolve_document_image_path(project.id, expected.url)
        expected_digest = hashlib.sha256(asset_path.read_bytes()).hexdigest() if asset_path and asset_path.is_file() else None
        blips = shape._inline.xpath(".//a:blip")
        rel_id = blips[0].get(qn("r:embed")) if blips else None
        image_part = doc.part.related_parts.get(rel_id) if rel_id else None
        actual_digest = hashlib.sha256(image_part.blob).hexdigest() if image_part is not None else None
        digest_ok = expected_digest is not None and actual_digest == expected_digest
        check("content.image_asset", "pass" if digest_ok else "fail" if expected_digest else "unverifiable", obj=f"image:{image_index}", expected=expected_digest, actual=actual_digest, evidence="compare emitted image bytes with the project-owned uploaded asset")
        actual_width_cm, actual_height_cm = float(shape.width.cm), float(shape.height.cm)
        size_ok = actual_width_cm > 0 and actual_height_cm > 0
        if expected.width_cm is not None:
            size_ok = size_ok and abs(actual_width_cm - expected.width_cm) <= 0.02
        else:
            size_ok = size_ok and actual_width_cm <= 14.02
        if expected.height_cm is not None:
            size_ok = size_ok and abs(actual_height_cm - expected.height_cm) <= 0.02
        else:
            size_ok = size_ok and actual_height_cm <= 13.52
        check("format.image_dimensions", "pass" if size_ok else "fail", obj=f"image:{image_index}", expected={"width_cm": expected.width_cm, "height_cm": expected.height_cm, "default_max_width_cm": 14.0, "default_max_height_cm": 13.5}, actual={"width_cm": actual_width_cm, "height_cm": actual_height_cm}, evidence="read emitted inline shape extents in centimeters")
        paragraph_xml = shape._inline.getparent().getparent().getparent()
        paragraph = next((item for item in doc.paragraphs if item._p is paragraph_xml), None)
        actual_alignment = _paragraph_format_value(paragraph, "alignment") if paragraph is not None else None
        align_map = {0: "left", 1: "center", 2: "right", 3: "justify", 4: "distribute"}
        expected_alignment = expected.alignment or "center"
        actual_alignment_name = align_map.get(int(actual_alignment)) if actual_alignment is not None else None
        check("format.image_alignment", "pass" if actual_alignment_name == expected_alignment else "fail", obj=f"image:{image_index}", expected=expected_alignment, actual=actual_alignment_name, evidence="read paragraph alignment containing emitted inline image")


def inspect_docx_output(
    data: bytes,
    project: WritingProject,
    template: PaperTemplate,
    *,
    source_template_bytes: bytes | None = None,
    context_kind: str = "paper",
) -> dict[str, Any]:
    """Inspect the actual DOCX and return an identity-bound result contract."""
    source_hash = _sha256(source_template_bytes) if source_template_bytes is not None else ""
    project_hash = project_content_hash(project)
    result: dict[str, Any] = {
        "contract_version": 1,
        "inspector_version": MASTER_INSPECTOR_VERSION if template.source_execution else INSPECTOR_VERSION,
        "context_kind": context_kind,
        "project_id": project.id,
        "project_revision": project.revision,
        "project_content_hash": project_hash,
        "template_id": template.id,
        "template_version": template.version,
        "template_hash": template_output_hash(template),
        "source_template_sha256": source_hash,
        "output_sha256": _sha256(data),
        "status": "unverifiable",
        "checks": [],
        "unsupported": [],
        "check_count": 0,
        "passed_count": 0,
    }

    if template.source_execution:
        from app.services.master_docx_check import inspect_master_output
        return inspect_master_output(data, project, template, source_template_bytes, result)

    def check(rule: str, status: str, *, obj: str, expected=None, actual=None, evidence: str):
        result["checks"].append({
            "rule": rule, "status": status, "object": obj,
            "expected": expected, "actual": actual, "evidence": evidence,
        })

    if not data:
        check("docx.open", "unverifiable", obj="document", expected="non-empty DOCX", actual="empty", evidence="no bytes")
        result["check_count"] = len(result["checks"])
        return result
    try:
        chart_parts = docx_chart_parts(data)
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        check("docx.open", "unverifiable", obj="document", expected="valid DOCX", actual=type(exc).__name__, evidence="DOCX package limits, safe XML parsing or document loading failed")
        result["check_count"] = len(result["checks"])
        return result
    check("docx.open", "pass", obj="document", expected="valid DOCX", actual="opened", evidence="parsed emitted DOCX bytes")

    unsupported: list[str] = []
    if chart_parts:
        unsupported.append("Office 图表对象的数据、语义与视觉结果尚未纳入成品检查")
        check("capability.chart_objects", "unverifiable", obj="document", expected="no unsupported Office chart objects", actual=chart_parts, evidence="chart parts or DrawingML chart markers are present in the emitted DOCX")
    if source_template_bytes is not None:
        try:
            source_charts = docx_chart_parts(source_template_bytes)
        except Exception:
            source_charts = []
            unsupported.append("模板源 DOCX 的 OOXML 部件无法完整解析")
            check("capability.source_package", "unverifiable", obj="template", expected="readable source DOCX parts", actual="unreadable", evidence="source template package could not be safely inspected")
        if source_charts:
            unsupported.append("源模板中的 Office 图表对象尚未映射到成品检查规则")
            check("capability.source_chart_objects", "unverifiable", obj="template", expected="no unsupported Office chart objects", actual=source_charts, evidence="source template contains chart parts or DrawingML chart markers")
    expected = _expected_text(project)
    actual_paragraphs = _content_paragraphs(doc)
    actual_text = [paragraph.text for paragraph in actual_paragraphs]
    cursor = 0
    matched: list[tuple[Any, str, int]] = []
    matched_indices = _check_cover_content(doc, template, project, check)
    matched_indices.update(_check_toc_field(doc, template, check, unsupported))
    project_sections = {section.key: section for section in project.sections}
    for node in _flatten(template.sections):
        node_kind = str(getattr(node.type, "value", node.type))
        if not node.required or node_kind in {"cover", "toc"}:
            continue
        section = project_sections.get(node.key)
        if section is None or str(getattr(section.type, "value", section.type)) != node_kind:
            check("structure.required_section", "fail", obj=f"section:{node.key}", expected={"type": node_kind, "present": True}, actual=None if section is None else str(getattr(section.type, "value", section.type)), evidence="compare required template section to the emitted project snapshot")
        else:
            check("structure.required_section", "pass", obj=f"section:{node.key}", expected={"type": node_kind, "present": True}, actual={"type": node_kind, "title": section.title}, evidence="required section identity and type resolved by stable section key")
            if not section.title.strip():
                check("structure.required_title", "fail", obj=f"section:{node.key}", expected="non-empty section title", actual=section.title, evidence="required title is empty in the project snapshot")
            if node_kind in {"abstract", "english_abstract", "keywords", "english_keywords", "body", "references", "acknowledgement", "appendix"}:
                has_content = any(
                    (
                        str(getattr(block.type, "value", block.type)) == "paragraph"
                        and bool(reference_block_text(block).strip())
                    )
                    or str(getattr(block.type, "value", block.type)) in {"image", "table", "equation"}
                    for block in section.paragraphs
                )
                if not has_content:
                    check("content.required_section_body", "fail", obj=f"section:{node.key}", expected="at least one non-empty content block", actual=0, evidence="required section has no body content in project snapshot")
    for text, section_key, block_index in expected:
        found = next((index for index in range(cursor, len(actual_text)) if actual_text[index] == text), None)
        if found is None:
            check("content.order_and_presence", "fail", obj=f"section:{section_key}/block:{block_index}", expected=text, actual=actual_text[cursor:cursor + 3], evidence="exact paragraph match not found in output document order")
            continue
        check("content.order_and_presence", "pass", obj=f"section:{section_key}/block:{block_index}", expected=text, actual=actual_text[found], evidence=f"emitted paragraph index {found}")
        if found > cursor:
            skipped = actual_text[cursor:found]
            duplicates = [item for item in skipped if item in {entry[0] for entry in expected}]
            if duplicates:
                check("content.no_duplicates", "fail", obj=f"section:{section_key}/block:{block_index}", expected="no repeated required paragraphs", actual=duplicates, evidence="duplicate project text appears before the next required paragraph")
            elif len(matched) > 0:
                generated_titles = {node.title for node in _flatten(template.sections) if str(getattr(node.type, "value", node.type)) == "toc"}
                unexpected = [item for item in skipped if item not in generated_titles and item != "请右键更新目录"]
                if unexpected:
                    check("content.no_unexpected_body", "fail", obj="document", expected="no extra paragraphs within project content", actual=unexpected, evidence="unmatched text occurs between required project paragraphs")
        matched.append((actual_paragraphs[found], section_key, block_index))
        matched_indices.add(found)
        cursor = found + 1
    if uses_sequential_reference_numbering(project):
        blocks = [(section.key, index) for section in project.sections
                  if str(getattr(section.type, "value", section.type)) == "references" or section.key == "references"
                  for index, block in enumerate(section.paragraphs)
                  if str(getattr(block.type, "value", block.type)) == "paragraph" and reference_block_text(block).strip()]
        observed = [paragraph.text for paragraph, section_key, index in matched
                    if (section_key, index) in blocks]
        numbers = [re.match(r"^\[(\d+)\](?:\s|$)", text) for text in observed]
        actual_numbers = [int(match.group(1)) for match in numbers if match]
        good = len(observed) == len(blocks) and len(actual_numbers) == len(blocks) and actual_numbers == list(range(1, len(blocks) + 1))
        check("references.sequential_numbers", "pass" if good else "fail", obj="references",
              expected=list(range(1, len(blocks) + 1)), actual=actual_numbers,
              evidence="number prefixes read independently from actual emitted reference paragraphs")
    if cursor < len(actual_text):
        extras = actual_text[cursor:]
        # Generated title/TOC cached rows are not inferred as paper body content.
        generated = {node.title for node in _flatten(template.sections) if str(getattr(node.type, "value", node.type)) == "toc"}
        extras = [text for text in extras if text not in generated and text != "请右键更新目录"]
        if extras:
            check("content.no_unexpected_body", "fail", obj="document", expected="no unexpected body paragraphs", actual=extras[:8], evidence="unmatched paragraphs remain after required project text")
    if not expected:
        unsupported.append("文档没有可验证的正文/标题文字，内容检查数量为零")

    # Map expected text back to source semantic style, then inspect every run.
    section_by_key = {section.key: section for section in project.sections}
    for paragraph, section_key, block_index in matched:
        section = section_by_key.get(section_key)
        if section is None:
            unsupported.append(f"无法关联章节语义: {section_key}")
            continue
        style = _style_for(section, template, block_index)
        par_index = actual_paragraphs.index(paragraph)
        _style_capabilities(style, f"paragraph:{par_index}", check, unsupported)
        _check_page_break_before(doc, paragraph, style, f"paragraph:{par_index}", check)
        if block_index == -1:
            kind = str(getattr(section.type, "value", section.type))
            expected_heading_level = {"heading1": "Heading 1", "heading2": "Heading 2", "heading3": "Heading 3"}.get(kind)
            if kind in {"references", "acknowledgement", "appendix"}:
                expected_heading_level = "Heading 1"
            if expected_heading_level:
                actual_style_name = paragraph.style.name if paragraph.style else None
                check("structure.heading_semantics", "pass" if actual_style_name == expected_heading_level else "fail", obj=f"paragraph:{par_index}", expected=expected_heading_level, actual=actual_style_name, evidence="read actual Word paragraph style for heading level")
        align_actual = _paragraph_format_value(paragraph, "alignment")
        align_map = {0: "left", 1: "center", 2: "right", 3: "justify", 4: "distribute"}
        align_name = align_map.get(int(align_actual), None) if align_actual is not None else None
        check("format.alignment", "pass" if align_name == style.align.value else "fail", obj=f"paragraph:{par_index}", expected=style.align.value, actual=align_name, evidence="read effective paragraph alignment from emitted DOCX")

        line_spacing = _paragraph_format_value(paragraph, "line_spacing")
        line_rule = _paragraph_line_rule(paragraph)
        line_ok = False
        line_value = float(line_spacing.pt) if hasattr(line_spacing, "pt") else float(line_spacing) if line_spacing is not None else None
        line_actual: dict[str, Any] = {"rule": line_rule, "value": line_value}
        if line_spacing is not None:
            if style.line_spacing_rule in {"exact", "atLeast"}:
                line_ok = line_rule == ("exact" if style.line_spacing_rule == "exact" else "atLeast") and line_value is not None and abs(line_value - style.line_spacing) <= 0.05
            else:
                line_ok = line_rule in (None, "auto") and line_value is not None and abs(line_value - style.line_spacing) <= 0.02
        check("format.line_spacing", "pass" if line_ok else "fail", obj=f"paragraph:{par_index}", expected={"rule": style.line_spacing_rule, "value": style.line_spacing}, actual=line_actual, evidence="read effective paragraph line spacing and OOXML lineRule")

        for prop, expected_points in (("space_before", style.space_before_pt), ("space_after", style.space_after_pt)):
            actual_points = _paragraph_format_value(paragraph, prop)
            actual_pt = float(actual_points.pt) if hasattr(actual_points, "pt") else float(actual_points) if actual_points is not None else 0.0
            ok = abs(actual_pt - expected_points) <= 0.05
            check(f"format.{prop}", "pass" if ok else "fail", obj=f"paragraph:{par_index}", expected=expected_points, actual=actual_pt, evidence="read effective paragraph spacing in points")

        first_indent = _paragraph_format_value(paragraph, "first_line_indent")
        left_indent = _paragraph_format_value(paragraph, "left_indent")
        expected_indent = (style.first_line_indent_chars * style.size_pt * 20.0) if style.first_line_indent_chars else (-style.hanging_indent_cm * _CM_TWIPS if style.hanging_indent_cm else 0)
        actual_indent = float(first_indent.twips) if first_indent is not None else 0.0
        ind_node = paragraph._p.pPr.find(qn("w:ind")) if paragraph._p.pPr is not None else None
        first_chars = ind_node.get(qn("w:firstLineChars")) if ind_node is not None else None
        hanging_chars = ind_node.get(qn("w:hangingChars")) if ind_node is not None else None
        if first_chars is not None and hanging_chars is not None:
            indent_ok = False
            indent_actual = {"first_line_chars": first_chars, "hanging_chars": hanging_chars, "first_line_twips": actual_indent}
        elif first_chars is not None:
            indent_ok = style.hanging_indent_cm == 0 and abs(float(first_chars) / 100 - style.first_line_indent_chars) <= 0.01
            indent_actual = {"first_line_chars": float(first_chars) / 100, "first_line_twips_ignored_by_word": actual_indent}
        elif hanging_chars is not None:
            expected_hanging_chars = style.hanging_indent_cm * _CM_TWIPS / (style.size_pt * _PT_TWIPS)
            indent_ok = style.first_line_indent_chars == 0 and abs(float(hanging_chars) / 100 - expected_hanging_chars) <= 0.01
            indent_actual = {"hanging_chars": float(hanging_chars) / 100, "hanging_twips_ignored_by_word": actual_indent}
        else:
            indent_ok = abs(actual_indent - expected_indent) <= 2
            indent_actual = {"first_line_twips": actual_indent}
        check("format.indent", "pass" if indent_ok else "fail", obj=f"paragraph:{par_index}", expected={"twips": expected_indent, "chars": style.first_line_indent_chars, "hanging_cm": style.hanging_indent_cm}, actual=indent_actual | {"left_twips": float(left_indent.twips) if left_indent else 0.0}, evidence="respect firstLineChars/hangingChars precedence; otherwise read emitted length indentation in twips")

        runs = [run for run in paragraph.runs if run.text]
        if not runs:
            check("format.runs_present", "unverifiable", obj=f"paragraph:{par_index}", expected="text runs", actual=0, evidence="paragraph text has no readable runs")
            continue
        source_block = section.paragraphs[block_index] if block_index >= 0 else None
        inline_specs = []
        if source_block is not None and source_block.inline_runs:
            if "".join(item.text for item in source_block.inline_runs) != source_block.text:
                unsupported.append(f"行内 run 文本与段落文本不一致，无法核验: {section_key}/{block_index}")
                check("capability.inline_run_mapping", "unverifiable", obj=f"paragraph:{par_index}", expected=source_block.text, actual="".join(item.text for item in source_block.inline_runs), evidence="exporter would discard inline formatting when source run text does not match the paragraph")
            else:
                try:
                    is_reference = str(getattr(section.type, "value", section.type)) == "references"
                    marker = re.match(r"^\[(\d+)\] ", paragraph.text) if is_reference else None
                    inline_specs = reference_inline_runs(source_block, int(marker.group(1))) if marker else [item for item in source_block.inline_runs if item.text]
                except ReferenceInlineMappingError:
                    unsupported.append(f"参考文献行内格式无法与输出文字关联: {section_key}/{block_index}")
                    check("capability.inline_run_mapping", "unverifiable", obj=f"paragraph:{par_index}", expected="reference text mapped to formatting", actual="unmappable reference", evidence="structured reference body differs from source text")
                    inline_specs = []
            if inline_specs and "".join(run.text for run in runs) != "".join(item.text for item in inline_specs):
                unsupported.append(f"输出文字无法与行内格式逐字关联: {section_key}/{block_index}")
                check("capability.inline_run_mapping", "unverifiable", obj=f"paragraph:{par_index}", expected="all paragraph text mapped to runs", actual="run text differs", evidence="text outside readable runs cannot be matched to source formatting")
                inline_specs = []
        inline_index = 0
        inline_remaining = len(inline_specs[0].text) if inline_specs else 0
        for run_index, run in enumerate(runs):
            obj = f"paragraph:{par_index}/run:{run_index}"
            mapped = []
            remaining = len(run.text)
            # Word can split or merge runs without changing their appearance.
            while remaining and inline_index < len(inline_specs):
                inline = inline_specs[inline_index]
                mapped.append(inline)
                consumed = min(remaining, inline_remaining)
                remaining -= consumed
                inline_remaining -= consumed
                if not inline_remaining:
                    inline_index += 1
                    if inline_index < len(inline_specs):
                        inline_remaining = len(inline_specs[inline_index].text)
            for inline in mapped or [None]:
                _check_run_style(
                    check, run, paragraph, style, obj,
                    bold_extra=bool(source_block and source_block.bold) or bool(inline and inline.bold),
                    italic_extra=bool(source_block and source_block.italic) or bool(inline and inline.italic),
                    underline_extra=bool(source_block and source_block.underline) or bool(inline and inline.underline),
                    highlight_override=(inline.highlight if inline and inline.highlight is not None else source_block.highlight if source_block and source_block.highlight is not None else None),
                    vert_align_override=(inline.vert_align if inline and inline.vert_align is not None else source_block.vert_align if source_block and source_block.vert_align is not None else None),
                    unsupported=unsupported,
                )
    for paragraph_index, paragraph in enumerate(actual_paragraphs):
        if paragraph_index in matched_indices:
            continue
        if not paragraph.text.strip():
            continue
        unsupported.append(f"输出段落无法可靠关联到模板语义: paragraph:{paragraph_index}")
        for run_index, run in enumerate(item for item in paragraph.runs if item.text):
            check("semantics.unmapped_output_run", "unverifiable", obj=f"paragraph:{paragraph_index}/run:{run_index}", expected="section-bound text style", actual=run.text[:80], evidence="output paragraph has no reliable source section mapping")
    for section in project.sections:
        for index, block in enumerate(section.paragraphs):
            kind = str(getattr(block.type, "value", block.type))
            if kind == "equation":
                unsupported.append(f"公式内容块的 OMML 语义与几何尚未纳入本检查器: {section.key}/{index}")
    story_parts = [
        (f"section:{index}/header", section.header)
        for index, section in enumerate(doc.sections)
    ] + [
        (f"section:{index}/footer", section.footer)
        for index, section in enumerate(doc.sections)
    ]
    for story_name, story in story_parts:
        story_runs = [run for paragraph in story.paragraphs for run in paragraph.runs if run.text]
        story_fields = _field_records(story._element)
        if story_name.endswith("/header"):
            if story_runs or story_fields:
                unsupported.append("页眉内容与域尚未纳入本切片的静态结构检查")
                check("semantics.unmapped_header", "unverifiable", obj=story_name, expected="empty header", actual="text or fields present", evidence="header rendering and field semantics are outside this supported profile")
            continue
        non_page_fields = [field for field in story_fields if field["instruction"].split()[0:1] != ["PAGE"]]
        footer_text = "".join(paragraph.text for paragraph in story.paragraphs)
        known_footer_text = all(character in "第页 0123456789" for character in footer_text)
        if non_page_fields or (story_runs and not (story_fields and known_footer_text)):
            unsupported.append("页脚中的非页码文本或其他域尚未纳入本切片的静态结构检查")
            check("semantics.unmapped_footer", "unverifiable", obj=story_name, expected="known PAGE field wrapper or empty footer", actual="unrecognized text or field", evidence="only the generated PAGE field and its fixed label are supported")
    _check_table_and_image_outputs(doc, project, template, check, unsupported)
    if template.source_docx and source_template_bytes is None:
        unsupported.append("模板声明了源 DOCX，但检查时无法读取其实际源文件摘要")
    _check_sections_and_page_fields(doc, template, project, source_template_bytes, check)
    result["unsupported"] = unsupported
    if unsupported:
        for item in unsupported:
            check("capability.supported", "unverifiable", obj="document", expected="supported output semantics", actual=item, evidence="capability boundary")
    result["check_count"] = len(result["checks"])
    result["passed_count"] = sum(1 for item in result["checks"] if item["status"] == "pass")
    statuses = {item["status"] for item in result["checks"]}
    result["status"] = "pass" if result["check_count"] > 0 and statuses == {"pass"} else "fail" if "fail" in statuses else "unverifiable"
    return result


def require_docx_output_pass(*args, **kwargs) -> dict[str, Any]:
    report = inspect_docx_output(*args, **kwargs)
    return require_output_check_report(report)


def require_output_check_report(report: dict[str, Any]) -> dict[str, Any]:
    from app.services.output_acceptance import output_report_is_acceptable

    checks = report.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or report.get("check_count") != len(checks)
        or not output_report_is_acceptable(report)
    ):
        raise DocxOutputCheckError(report)
    return report


def safe_output_check_diagnostics(report: dict[str, Any] | None) -> dict[str, Any]:
    """Project check failures without exposing manuscript text or expected/actual content."""
    from app.services.output_acceptance import output_report_is_acceptable

    report = report if isinstance(report, dict) else {}
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    findings = []
    for item in checks:
        if not isinstance(item, dict) or item.get("status") == "pass":
            continue
        findings.append({
            "rule": str(item.get("rule") or "output_check.unknown"),
            "status": str(item.get("status") or "unverifiable"),
            "object": str(item.get("object") or "document"),
            "evidence": str(item.get("evidence") or ""),
        })
        if len(findings) == 50:
            break
    invalid_passing_report = report.get("status") == "pass" and not output_report_is_acceptable(report) and (
        not checks
        or report.get("check_count") != len(checks)
        or not output_report_is_acceptable(report)
    )
    if not findings and (not checks or report.get("status") != "pass" or invalid_passing_report):
        findings.append({
            "rule": "output_check.invalid_report",
            "status": "unverifiable",
            "object": "document",
            "evidence": "检查器异常或报告为空、无效",
        })
    return {
        "status": "unverifiable" if invalid_passing_report else report.get("status") if report.get("status") in {"fail", "unverifiable"} else "unverifiable",
        "check_count": int(report.get("check_count", len(checks)) or 0),
        "findings": findings,
    }


def verify_report_binding(report: dict[str, Any] | None, data: bytes, *,
                          allow_previous_inspector: bool = False) -> bool:
    from app.services.output_acceptance import output_report_is_acceptable

    if not isinstance(report, dict):
        return False
    checks = report.get("checks")
    master_report = "source_profile_sha256" in report
    expected_version = MASTER_INSPECTOR_VERSION if master_report else INSPECTOR_VERSION
    accepted_versions = {expected_version}
    previous_versions = _PREVIOUS_INSPECTOR_VERSIONS[expected_version]
    if allow_previous_inspector:
        accepted_versions.update(previous_versions)
    if master_report and report.get("inspector_version") in previous_versions:
        # This only establishes that a historical report points at these bytes.
        # Delivery callers using this compatibility path must re-run the current checker.
        return bool(
            allow_previous_inspector
            and report.get("contract_version") == 1
            and re.fullmatch(r"[0-9a-f]{64}", str(report.get("source_profile_sha256") or ""))
            and isinstance(checks, list) and checks
            and report.get("check_count") == len(checks)
            and report.get("passed_count") == len(checks)
            and not report.get("unsupported")
            and all(isinstance(item, dict) and item.get("status") == "pass" for item in checks)
            and report.get("output_sha256") == _sha256(data)
        )
    return bool(
        report.get("contract_version") == 1
        and report.get("inspector_version") in accepted_versions
        and (not master_report or re.fullmatch(r"[0-9a-f]{64}", str(report.get("source_profile_sha256") or "")))
        and output_report_is_acceptable(report)
        and isinstance(checks, list)
        and len(checks) > 0
        and report.get("check_count") == len(checks)
        and report.get("output_sha256") == _sha256(data)
    )


def verify_source_template_binding(report: dict[str, Any] | None, source_data: bytes | None) -> bool:
    expected = str((report or {}).get("source_template_sha256") or "")
    actual = _sha256(source_data) if source_data is not None else ""
    return bool(report and expected == actual)


def source_template_bytes(path: str | Path | None) -> bytes | None:
    if not path:
        return None
    candidate = Path(path)
    return candidate.read_bytes() if candidate.is_file() else None
