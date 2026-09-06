"""Compare a paper DOCX against a user-provided PaperTemplate."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.ir import Document, Finding, Location, Severity
from app.models.project import WritingProject
from app.models.template import PaperTemplate, TemplateSection, TextStyle

_TOLERANCE = 0.05
_SYSTEM_GENERATED_SECTION_TYPES = {"cover", "toc"}


@dataclass(frozen=True)
class TemplateCheckContext:
    section_count: int
    style_count: int


def compare_document_to_template(
    document: Document,
    template: PaperTemplate,
) -> tuple[list[Finding], TemplateCheckContext]:
    """Return typed findings for supported template structure and paragraph styles."""
    findings: list[Finding] = []
    section_nodes = _flatten_sections(template.sections)
    expected_heading_count = sum(1 for node in section_nodes if node.type.value.startswith("heading"))
    actual_heading_count = sum(1 for paragraph in document.paragraphs if paragraph.level is not None)
    if actual_heading_count < expected_heading_count:
        findings.append(Finding(
            rule_id=f"TPL.{template.id}.structure.heading-count",
            severity=Severity.ERROR,
            message=f"论文标题章节数量不足: 模板要求至少 {expected_heading_count} 个, 实际 {actual_heading_count} 个",
            location=Location(),
            actual=actual_heading_count,
            expected=expected_heading_count,
        ))

    for index, paragraph in enumerate(document.paragraphs):
        expected_style = _expected_style_for_paragraph(paragraph, template)
        if expected_style is None:
            continue
        section_key = _section_key_for_paragraph(paragraph, section_nodes, index)
        _compare_paragraph(findings, paragraph, index, section_key, expected_style, template.id)

    return findings, TemplateCheckContext(
        section_count=len(section_nodes),
        style_count=len(template.styles),
    )


def compare_project_to_template(
    project: WritingProject,
    template: PaperTemplate,
) -> tuple[list[Finding], TemplateCheckContext]:
    """Check project content against its template without injecting template styles."""
    findings: list[Finding] = []
    project_sections = {section.key: section for section in project.sections}
    section_nodes = _flatten_sections(template.sections)
    for node in section_nodes:
        if node.type.value in _SYSTEM_GENERATED_SECTION_TYPES:
            continue
        section = project_sections.get(node.key)
        if section is None:
            if node.required:
                findings.append(Finding(
                    rule_id=f"TPL.{template.id}.section.{node.key}.missing",
                    severity=Severity.ERROR,
                    message=f"缺少模板要求的章节: {node.title}",
                    location=Location(section_key=node.key),
                    actual=False,
                    expected=True,
                ))
            continue
        if node.required and not _project_section_has_content(section):
            findings.append(Finding(
                rule_id=f"TPL.{template.id}.section.{node.key}.empty",
                severity=Severity.ERROR,
                message=f"模板要求的章节尚未填写: {node.title}",
                location=Location(section_key=node.key),
                actual=False,
                expected=True,
            ))
        for block_index, block in enumerate(section.paragraphs):
            if block.type.value != "paragraph":
                continue
            if block.bold and not _style_for_node(node, template).bold:
                findings.append(_project_inline_finding(template, node, section.key, block_index, "bold", True, False, "加粗"))
            if block.italic and not _style_for_node(node, template).italic:
                findings.append(_project_inline_finding(template, node, section.key, block_index, "italic", True, False, "斜体"))
            if block.underline and not _style_for_node(node, template).underline:
                findings.append(_project_inline_finding(template, node, section.key, block_index, "underline", True, False, "下划线"))
    return findings, TemplateCheckContext(len(section_nodes), len(template.styles))


def _project_section_has_content(section) -> bool:
    return bool((section.title or "").strip() or any(
        (block.text or "").strip() or block.type.value in {"image", "table"}
        for block in section.paragraphs
    ))


def _style_for_node(node: TemplateSection, template: PaperTemplate) -> TextStyle:
    return node.style or template.styles.get(node.type.value) or template.styles.get("body") or TextStyle()


def _project_inline_finding(template, node, section_key, block_index, prop, actual, expected, label):
    return Finding(
        rule_id=f"TPL.{template.id}.section.{section_key}.paragraph.{prop}",
        severity=Severity.WARNING,
        message=f"第 {block_index + 1} 个段落的{label}不符合模板规范",
        location=Location(section_key=section_key, paragraph_index=block_index),
        actual=actual,
        expected=expected,
    )
def _flatten_sections(nodes: list[TemplateSection]) -> list[TemplateSection]:
    result: list[TemplateSection] = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten_sections(node.children))
    return result


def _expected_style_for_paragraph(paragraph, template: PaperTemplate) -> TextStyle | None:
    if paragraph.level is not None:
        key = f"heading{paragraph.level}"
    elif paragraph.style_id in {"AbstractBody", "AbstractContent", "英文摘要", "EnglishAbstract"}:
        key = "english_abstract"
    elif paragraph.style_id in {"Keywords", "关键词"}:
        key = "keywords"
    elif paragraph.style_id in {"Bibliography", "References", "参考文献"}:
        key = "references"
    else:
        key = "body"
    return template.styles.get(key) or template.styles.get("body")


def _section_key_for_paragraph(paragraph, sections: list[TemplateSection], index: int) -> str | None:
    if paragraph.level is not None:
        candidates = [node for node in sections if node.type.value == f"heading{paragraph.level}"]
        if candidates:
            heading_positions = [i for i, node in enumerate(sections) if node.type.value == f"heading{paragraph.level}"]
            nearest = min(range(len(heading_positions)), key=lambda i: abs(heading_positions[i] - index))
            return candidates[nearest].key
    return None


def _compare_paragraph(findings, paragraph, index, section_key, style, template_id):
    location = Location(
        paragraph_index=index,
        text_snippet=paragraph.text[:120] if paragraph.text else None,
        section_key=section_key,
    )
    checks = [
        ("alignment", paragraph.alignment, style.align.value, "对齐方式"),
        ("line_spacing", paragraph.line_spacing, style.line_spacing, "行距"),
        ("space_before_pt", paragraph.space_before_pt, style.space_before_pt, "段前间距"),
        ("space_after_pt", paragraph.space_after_pt, style.space_after_pt, "段后间距"),
        ("first_line_indent_cm", paragraph.first_line_indent_cm, style.first_line_indent_chars * 0.37, "首行缩进"),
    ]
    for prop, actual, expected, label in checks:
        if actual is None or _equal(actual, expected):
            continue
        findings.append(Finding(
            rule_id=f"TPL.{template_id}.paragraph.{prop}",
            severity=Severity.WARNING,
            message=f"{label}不符合上传模板规范",
            location=location,
            actual=actual,
            expected=expected,
        ))

    first_run = paragraph.runs[0] if paragraph.runs else None
    if first_run is None:
        return
    run_checks = [
        ("font_eastasia", first_run.font_eastasia, style.font_zh, "中文字体"),
        ("font_ascii", first_run.font_ascii, style.font_en, "英文字体"),
        ("font_size_pt", first_run.font_size_pt, style.size_pt, "字号"),
        ("bold", first_run.bold, style.bold, "加粗状态"),
        ("italic", first_run.italic, style.italic, "斜体状态"),
        ("underline", first_run.underline is not None, style.underline, "下划线状态"),
    ]
    for prop, actual, expected, label in run_checks:
        if actual is None or _equal(actual, expected):
            continue
        findings.append(Finding(
            rule_id=f"TPL.{template_id}.run.{prop}",
            severity=Severity.WARNING,
            message=f"{label}不符合上传模板规范",
            location=location,
            actual=actual,
            expected=expected,
        ))


def _equal(actual, expected) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), abs_tol=_TOLERANCE)
    return actual == expected


__all__ = ["TemplateCheckContext", "compare_document_to_template"]
