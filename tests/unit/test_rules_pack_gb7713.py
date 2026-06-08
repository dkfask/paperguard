"""Tests for GB-T-7713-1-2006.yaml pack — Sprint 2 commit #12.

27 tests = 25 negative（每条规则各 1 个违规 case）+ 2 integration（多违规组合 + 全合规）。

每个 negative test 构造一个会触发对应规则的 IR Document，
跑 validate_docx(doc, pack="GB-T-7713-1-2006") → 断言结果含该 rule_id。

YAML pack 加载用默认 packs_dir（即 app/core/rules/packs/）。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine import validate_docx
from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
)


PACK_ID = "GB-T-7713-1-2006"


# ---------------------------------------------------------------------------
# Helpers — 合规 body/heading 段落工厂
# ---------------------------------------------------------------------------

def _body_run(**override) -> Run:
    defaults = dict(
        text="正文",
        font_eastasia="宋体",
        font_ascii="Times New Roman",
        font_hansi="Times New Roman",
        font_cs=None,
        font_size_pt=12.0,
        bold=False,
        italic=False,
        underline=None,
        color_hex="000000",
        highlight=None,
        vert_align=None,
    )
    defaults.update(override)
    return Run(**defaults)


def _body_para(runs=None, **override) -> Paragraph:
    defaults = dict(
        style_id="Body Text",
        text="正文段",
        level=None,
        runs=runs if runs is not None else (_body_run(),),
        line_spacing=1.5,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.74,
        is_page_break_before=False,
    )
    defaults.update(override)
    return Paragraph(**defaults)


def _h_run(eastasia="黑体", size=18.0, **override) -> Run:
    return _body_run(font_eastasia=eastasia, font_size_pt=size, **override)


def _h1_para(runs=None, **override) -> Paragraph:
    defaults = dict(
        style_id="Heading 1",
        text="一级标题",
        level=1,
        runs=runs if runs is not None else (_h_run(size=18.0),),
        line_spacing=1.5,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.0,
        is_page_break_before=True,
    )
    defaults.update(override)
    return Paragraph(**defaults)


def _h2_para(runs=None, **override) -> Paragraph:
    defaults = dict(
        style_id="Heading 2",
        text="二级标题",
        level=2,
        runs=runs if runs is not None else (_h_run(size=15.0),),
        line_spacing=1.5,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.0,
    )
    defaults.update(override)
    return Paragraph(**defaults)


def _h3_para(runs=None, **override) -> Paragraph:
    defaults = dict(
        style_id="Heading 3",
        text="三级标题",
        level=3,
        runs=runs if runs is not None else (_h_run(size=14.0),),
        line_spacing=1.5,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.0,
    )
    defaults.update(override)
    return Paragraph(**defaults)


def _doc(paragraphs) -> Document:
    return Document(
        sections=(),
        paragraphs=tuple(paragraphs),
        metadata=DocumentMetadata(title="test"),
    )


def _has_rule(findings, rule_id: str) -> bool:
    return any(f.rule_id == rule_id for f in findings)


# ===========================================================================
# Negative tests — 每条规则各 1 个违规 case（25 tests）
# ===========================================================================

class TestBodyRules(unittest.TestCase):
    """正文 10 条规则。"""

    def test_PG_BODY_LINE_SPACING_violated_by_single_spacing(self):
        doc = _doc([_body_para(line_spacing=1.0)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-LINE-SPACING"))

    def test_PG_BODY_FONT_SIZE_violated_by_14pt(self):
        doc = _doc([_body_para(runs=(_body_run(font_size_pt=14.0),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-FONT-SIZE"))

    def test_PG_BODY_FIRST_LINE_INDENT_violated_by_zero(self):
        doc = _doc([_body_para(first_line_indent_cm=0.0)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-FIRST-LINE-INDENT"))

    def test_PG_BODY_FONT_EASTASIA_violated_by_arial(self):
        doc = _doc([_body_para(runs=(_body_run(font_eastasia="Arial"),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-FONT-EASTASIA"))

    def test_PG_BODY_PARAGRAPH_SPACING_violated_by_6pt_before(self):
        doc = _doc([_body_para(space_before_pt=6.0)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-PARAGRAPH-SPACING"))

    def test_PG_BODY_FONT_ASCII_violated_by_arial(self):
        doc = _doc([_body_para(runs=(_body_run(font_ascii="Arial"),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-FONT-ASCII"))

    def test_PG_BODY_FONT_HANSI_violated_by_arial(self):
        doc = _doc([_body_para(runs=(_body_run(font_hansi="Arial"),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-FONT-HANSI"))

    def test_PG_BODY_COLOR_BLACK_violated_by_red(self):
        doc = _doc([_body_para(runs=(_body_run(color_hex="FF0000"),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-COLOR-BLACK"))

    def test_PG_BODY_NO_UNDERLINE_violated_by_single_underline(self):
        doc = _doc([_body_para(runs=(_body_run(underline="single"),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-NO-UNDERLINE"))

    def test_PG_BODY_NO_BOLD_violated_by_bold(self):
        doc = _doc([_body_para(runs=(_body_run(bold=True),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-NO-BOLD"))


class TestHeading1Rules(unittest.TestCase):
    """一级标题 4 条规则。"""

    def test_PG_H1_LINE_SPACING_violated(self):
        doc = _doc([_h1_para(line_spacing=1.0)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H1-LINE-SPACING"))

    def test_PG_H1_FONT_EASTASIA_violated_by_songti(self):
        doc = _doc([_h1_para(runs=(_h_run(eastasia="宋体", size=18.0),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H1-FONT-EASTASIA"))

    def test_PG_H1_FONT_SIZE_violated_by_14pt(self):
        doc = _doc([_h1_para(runs=(_h_run(size=14.0),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H1-FONT-SIZE"))

    def test_PG_H1_LEVEL_violated_by_none(self):
        doc = _doc([_h1_para(level=None)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H1-LEVEL"))


class TestHeading2Rules(unittest.TestCase):
    """二级标题 4 条规则。"""

    def test_PG_H2_LINE_SPACING_violated(self):
        doc = _doc([_h2_para(line_spacing=1.0)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H2-LINE-SPACING"))

    def test_PG_H2_FONT_EASTASIA_violated(self):
        doc = _doc([_h2_para(runs=(_h_run(eastasia="宋体", size=15.0),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H2-FONT-EASTASIA"))

    def test_PG_H2_FONT_SIZE_violated_by_12pt(self):
        doc = _doc([_h2_para(runs=(_h_run(size=12.0),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H2-FONT-SIZE"))

    def test_PG_H2_LEVEL_violated(self):
        doc = _doc([_h2_para(level=1)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H2-LEVEL"))


class TestHeading3Rules(unittest.TestCase):
    """三级标题 4 条规则。"""

    def test_PG_H3_LINE_SPACING_violated(self):
        doc = _doc([_h3_para(line_spacing=2.0)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H3-LINE-SPACING"))

    def test_PG_H3_FONT_EASTASIA_violated(self):
        doc = _doc([_h3_para(runs=(_h_run(eastasia="宋体", size=14.0),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H3-FONT-EASTASIA"))

    def test_PG_H3_FONT_SIZE_violated(self):
        doc = _doc([_h3_para(runs=(_h_run(size=12.0),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H3-FONT-SIZE"))

    def test_PG_H3_LEVEL_violated(self):
        doc = _doc([_h3_para(level=2)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H3-LEVEL"))


class TestChapterAndFormatRules(unittest.TestCase):
    """章节级 + 正文格式 3 条规则。"""

    def test_PG_H1_PAGE_BREAK_violated_by_no_break(self):
        doc = _doc([_h1_para(is_page_break_before=False)])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-H1-PAGE-BREAK"))

    def test_PG_BODY_NO_ITALIC_violated_by_italic(self):
        doc = _doc([_body_para(runs=(_body_run(italic=True),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-NO-ITALIC"))

    def test_PG_BODY_NO_HIGHLIGHT_violated_by_yellow(self):
        doc = _doc([_body_para(runs=(_body_run(highlight="yellow"),))])
        self.assertTrue(_has_rule(validate_docx(doc, PACK_ID), "PG-BODY-NO-HIGHLIGHT"))


# ===========================================================================
# Integration tests — 多违规组合 + 全合规
# ===========================================================================

class TestIntegrationMultipleViolations(unittest.TestCase):
    """多种违规并存：验证 findings 排序 + 多 rule 同段命中。"""

    def test_multiple_violations_in_same_doc(self):
        doc = _doc([
            _body_para(line_spacing=1.0),                                 # idx 0: 违规 PG-BODY-LINE-SPACING
            _body_para(runs=(_body_run(font_size_pt=14.0, bold=True),)),  # idx 1: 违规 PG-BODY-FONT-SIZE + PG-BODY-NO-BOLD
            _h1_para(line_spacing=1.0),                                   # idx 2: 违规 PG-H1-LINE-SPACING
        ])
        findings = validate_docx(doc, PACK_ID)
        # 至少触发了 4 条规则
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("PG-BODY-LINE-SPACING", rule_ids)
        self.assertIn("PG-BODY-FONT-SIZE", rule_ids)
        self.assertIn("PG-BODY-NO-BOLD", rule_ids)
        self.assertIn("PG-H1-LINE-SPACING", rule_ids)
        # 排序检查：按 (paragraph_index, rule_id)
        indices = [f.location.paragraph_index for f in findings]
        self.assertEqual(indices, sorted(indices), "findings 应按 paragraph_index 升序")


class TestIntegrationFullyCompliant(unittest.TestCase):
    """完全合规 doc 不产生任何 findings。"""

    def test_fully_compliant_document_produces_no_findings(self):
        doc = _doc([
            _h1_para(),
            _body_para(),
            _body_para(),
            _h2_para(),
            _body_para(),
            _h3_para(),
            _body_para(),
        ])
        findings = validate_docx(doc, PACK_ID)
        self.assertEqual(findings, [],
                         f"合规 doc 不应产生 finding；实际产生 {len(findings)} 条")


if __name__ == "__main__":
    unittest.main()
