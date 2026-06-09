"""Tests for app.core.rules.packs.GB-T-7714-1-2015 — Sprint 3.

12 RED tests for GB/T 7714-1-2015 参考文献格式 pack.

设计契约（plan §3.5 + ADR-3 A）：
- 10 核心规则覆盖参考文献格式（字号/字体/行距/缩进/对齐/段落间距/无下划线）
- scope: paragraphs[?style_id == 'Bibliography' || 'References' || '参考文献']
- 全部 PG-REF- 前缀
- severity 全 warning
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine import validate_docx
from app.core.engine.loader import load_pack
from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
    Section,
    Severity,
)

PACK_ID = "GB-T-7714-1-2015"


def _ref_para(
    style_id: str = "Bibliography",
    line_spacing: float = 1.5,
    first_line_indent_cm: float = 0.0,
    space_before_pt: float = 0.0,
    space_after_pt: float = 0.0,
    align: str = "left",
    font_size: float = 10.5,
    font_eastasia: str = "宋体",
    font_ascii: str = "Times New Roman",
    underline: str | None = None,
) -> Paragraph:
    """构造一个参考文献条目段落（默认全部合 GB/T 7714 规范）"""
    return Paragraph(
        style_id=style_id,
        text="[1] 张三. 论文标题[J]. 期刊名, 2024, 10(1): 1-5.",
        level=None,
        runs=(Run(
            text="[1] 张三. 论文标题[J]. 期刊名, 2024, 10(1): 1-5.",
            font_eastasia=font_eastasia,
            font_ascii=font_ascii,
            font_size_pt=font_size,
            underline=underline,
        ),),
        line_spacing=line_spacing,
        space_before_pt=space_before_pt,
        space_after_pt=space_after_pt,
        first_line_indent_cm=first_line_indent_cm,
    )


def _doc(paragraphs: tuple[Paragraph, ...] = ()) -> Document:
    return Document(
        sections=(Section(),),
        paragraphs=paragraphs,
        metadata=DocumentMetadata(title="t"),
    )


def _has_finding(findings, rule_id: str) -> bool:
    return any(f.rule_id == rule_id for f in findings)


class TestGB7714PackRules(unittest.TestCase):
    """10 条规则各 1 个 positive case（违规时 finding 出来）"""

    def test_PG_REF_FONT_SIZE_violated_by_12pt(self):
        doc = _doc((_ref_para(font_size=12.0),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-FONT-SIZE"))

    def test_PG_REF_FONT_EASTASIA_violated_by_arial(self):
        doc = _doc((_ref_para(font_eastasia="Arial"),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-FONT-EASTASIA"))

    def test_PG_REF_FONT_ASCII_violated_by_helvetica(self):
        doc = _doc((_ref_para(font_ascii="Helvetica"),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-FONT-ASCII"))

    def test_PG_REF_LINE_SPACING_violated_by_1_0(self):
        doc = _doc((_ref_para(line_spacing=1.0),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-LINE-SPACING"))

    def test_PG_REF_FIRST_LINE_INDENT_violated_by_074(self):
        doc = _doc((_ref_para(first_line_indent_cm=0.74),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-FIRST-LINE-INDENT"))

    def test_PG_REF_ALIGN_violated_by_center(self):
        doc = _doc((_ref_para(align="center"),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-ALIGN"))

    def test_PG_REF_SPACE_BEFORE_violated_by_6pt(self):
        doc = _doc((_ref_para(space_before_pt=6.0),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-SPACE-BEFORE"))

    def test_PG_REF_SPACE_AFTER_violated_by_6pt(self):
        doc = _doc((_ref_para(space_after_pt=6.0),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-SPACE-AFTER"))

    def test_PG_REF_NO_UNDERLINE_violated_by_single(self):
        doc = _doc((_ref_para(underline="single"),))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-NO-UNDERLINE"))

    def test_PG_REF_NO_BOLD_violated_by_bold(self):
        """bonus 第 10 条 — 参考文献不应加粗"""
        para = _ref_para()
        para = Paragraph(
            style_id=para.style_id, text=para.text, level=para.level,
            runs=(Run(text=para.runs[0].text, font_eastasia="宋体",
                      font_size_pt=10.5, bold=True),),
            line_spacing=para.line_spacing, space_before_pt=para.space_before_pt,
            space_after_pt=para.space_after_pt, first_line_indent_cm=para.first_line_indent_cm,
        )
        doc = _doc((para,))
        self.assertTrue(_has_finding(validate_docx(doc, PACK_ID), "PG-REF-NO-BOLD"))


class TestGB7714PackIntegration(unittest.TestCase):
    """集成 + rule_id 命名约定"""

    def test_load_pack_with_10_rules(self):
        pack = load_pack(PACK_ID)
        self.assertEqual(pack.id, PACK_ID)
        self.assertEqual(len(pack.rules), 10,
                         f"GB-T-7714 应有 10 条规则，实际 {len(pack.rules)}")

    def test_all_rule_ids_use_pg_ref_prefix(self):
        pack = load_pack(PACK_ID)
        for r in pack.rules:
            self.assertTrue(r.id.startswith("PG-REF-"),
                            f"rule_id '{r.id}' 不以 PG-REF- 开头")


if __name__ == "__main__":
    unittest.main()
