"""Tests for app.core.engine.stub_engine — Sprint 1.5 5 规则 stub engine.

Q1 决策：Sprint 1.5 自带 5 条规则的内存 stub engine，让性能基线可被真测试。
Sprint 2 在此基础上加 YAML pack loader + 25+ 规则。

5 条规则：
  R001 line_spacing           paragraph.line_spacing 必须 1.5
  R002 font_size              run.font_size_pt 必须 12.0
  R003 first_line_indent      paragraph.first_line_indent_cm 必须 0.74
  R004 font_family            run.font_eastasia 必须 "宋体"
  R005 paragraph_spacing      paragraph.space_before_pt/space_after_pt 必须 0

TDD：先写测试，RED（找不到 validate_docx），再实现，GREEN。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.ir import (
    Document,
    DocumentMetadata,
    Finding,
    Paragraph,
    Run,
)
from app.core.engine.stub_engine import validate_docx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_run(
    text: str = "正文",
    font_eastasia: str | None = "宋体",
    font_ascii: str | None = "Times New Roman",
    font_size_pt: float | None = 12.0,
) -> Run:
    return Run(
        text=text,
        font_eastasia=font_eastasia,
        font_ascii=font_ascii,
        font_size_pt=font_size_pt,
    )


def _make_paragraph(
    line_spacing: float = 1.5,
    first_line_indent_cm: float = 0.74,
    space_before_pt: float = 0.0,
    space_after_pt: float = 0.0,
    runs: tuple[Run, ...] | None = None,
    text: str = "本文研究的主要内容...",
) -> Paragraph:
    if runs is None:
        runs = (_make_run(text=text),)
    return Paragraph(
        style_id="Body Text",
        text=text,
        level=None,
        runs=runs,
        line_spacing=line_spacing,
        space_before_pt=space_before_pt,
        space_after_pt=space_after_pt,
        first_line_indent_cm=first_line_indent_cm,
    )


def _make_doc(paragraphs: list[Paragraph]) -> Document:
    return Document(
        sections=(),
        paragraphs=tuple(paragraphs),
        metadata=DocumentMetadata(title="示例论文", author="测试"),
    )


def _findings_for_rule(findings: list[Finding], rule_id: str) -> list[Finding]:
    """Helper: filter findings by rule_id."""
    return [f for f in findings if f.rule_id == rule_id]


# ---------------------------------------------------------------------------
# R001 — line_spacing 必须 1.5
# ---------------------------------------------------------------------------

class TestR001LineSpacing(unittest.TestCase):
    def test_R001_line_spacing_1_5_passes(self):
        doc = _make_doc([_make_paragraph(line_spacing=1.5)])
        findings = validate_docx(doc)
        self.assertEqual(_findings_for_rule(findings, "R001"), [],
                         "line_spacing==1.5 不应产生 R001 违规")

    def test_R001_line_spacing_1_0_fails(self):
        doc = _make_doc([_make_paragraph(line_spacing=1.0)])
        findings = validate_docx(doc)
        r001 = _findings_for_rule(findings, "R001")
        self.assertEqual(len(r001), 1, "line_spacing==1.0 必须产生 1 条 R001")
        self.assertIsNotNone(r001[0].fix, "Finding 必须附带 Fix 以便 live_mutator 应用")


# ---------------------------------------------------------------------------
# R002 — font_size 必须 12.0pt
# ---------------------------------------------------------------------------

class TestR002FontSize(unittest.TestCase):
    def test_R002_font_size_12pt_passes(self):
        doc = _make_doc([_make_paragraph(
            runs=(_make_run(font_size_pt=12.0),),
        )])
        findings = validate_docx(doc)
        self.assertEqual(_findings_for_rule(findings, "R002"), [],
                         "font_size_pt==12.0 不应产生 R002 违规")

    def test_R002_font_size_14pt_fails(self):
        doc = _make_doc([_make_paragraph(
            runs=(_make_run(font_size_pt=14.0),),
        )])
        findings = validate_docx(doc)
        r002 = _findings_for_rule(findings, "R002")
        self.assertEqual(len(r002), 1, "font_size_pt==14.0 必须产生 1 条 R002")
        self.assertIsNotNone(r002[0].fix, "Finding 必须附带 Fix")


# ---------------------------------------------------------------------------
# R003 — first_line_indent 必须 0.74cm
# ---------------------------------------------------------------------------

class TestR003FirstLineIndent(unittest.TestCase):
    def test_R003_first_line_indent_0_74cm_passes(self):
        doc = _make_doc([_make_paragraph(first_line_indent_cm=0.74)])
        findings = validate_docx(doc)
        self.assertEqual(_findings_for_rule(findings, "R003"), [],
                         "first_line_indent_cm==0.74 不应产生 R003 违规")

    def test_R003_first_line_indent_0_fails(self):
        doc = _make_doc([_make_paragraph(first_line_indent_cm=0.0)])
        findings = validate_docx(doc)
        r003 = _findings_for_rule(findings, "R003")
        self.assertEqual(len(r003), 1, "first_line_indent_cm==0.0 必须产生 1 条 R003")
        self.assertIsNotNone(r003[0].fix, "Finding 必须附带 Fix")


# ---------------------------------------------------------------------------
# R004 — font_family.eastAsia 必须 "宋体"
# ---------------------------------------------------------------------------

class TestR004FontFamily(unittest.TestCase):
    def test_R004_font_family_songti_passes(self):
        doc = _make_doc([_make_paragraph(
            runs=(_make_run(font_eastasia="宋体"),),
        )])
        findings = validate_docx(doc)
        self.assertEqual(_findings_for_rule(findings, "R004"), [],
                         'font_eastasia=="宋体" 不应产生 R004 违规')

    def test_R004_font_family_arial_fails(self):
        doc = _make_doc([_make_paragraph(
            runs=(_make_run(font_eastasia="Arial"),),
        )])
        findings = validate_docx(doc)
        r004 = _findings_for_rule(findings, "R004")
        self.assertEqual(len(r004), 1, 'font_eastasia=="Arial" 必须产生 1 条 R004')
        self.assertIsNotNone(r004[0].fix, "Finding 必须附带 Fix")


# ---------------------------------------------------------------------------
# R005 — paragraph_spacing (space_before/space_after) 必须 0pt
# ---------------------------------------------------------------------------

class TestR005ParagraphSpacing(unittest.TestCase):
    def test_R005_paragraph_spacing_0pt_passes(self):
        doc = _make_doc([_make_paragraph(
            space_before_pt=0.0,
            space_after_pt=0.0,
        )])
        findings = validate_docx(doc)
        self.assertEqual(_findings_for_rule(findings, "R005"), [],
                         "space_before/after==0 不应产生 R005 违规")

    def test_R005_paragraph_spacing_6pt_fails(self):
        doc = _make_doc([_make_paragraph(
            space_before_pt=6.0,
            space_after_pt=0.0,
        )])
        findings = validate_docx(doc)
        r005 = _findings_for_rule(findings, "R005")
        self.assertEqual(len(r005), 1, "space_before==6 必须产生 1 条 R005")
        self.assertIsNotNone(r005[0].fix, "Finding 必须附带 Fix")


if __name__ == "__main__":
    unittest.main()
