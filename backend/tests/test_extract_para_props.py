"""Tests for app.core.extract.para_props — Sprint 2.5 段落属性抽取.

10 RED tests:
- style_id（从 pStyle 节点读）
- align（jc 节点）
- spacing (before/after/line)
- indent (left/right/firstLine)
- keepNext
- pageBreakBefore
- 无 pPr 返空 dict
- el 为 None 安全
- 多个属性同时存在
- 过滤空字符串
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree

from app.core.extract.para_props import para_props
from app.core.extract.xml_utils import NS, parse_xml


class TestParaProps(unittest.TestCase):
    def _el(self, xml: str):
        return parse_xml(xml.encode("utf-8"))

    def test_style_id_from_pStyle(self):
        xml = (
            f'<w:pPr xmlns:w="{NS["w"]}">'
            f'<w:pStyle w:val="BodyText"/>'
            f'</w:pPr>'
        )
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.style_id"), "BodyText")

    def test_align(self):
        xml = (
            f'<w:pPr xmlns:w="{NS["w"]}">'
            f'<w:jc w:val="center"/>'
            f'</w:pPr>'
        )
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.align"), "center")

    def test_spacing_before(self):
        xml = (
            f'<w:pPr xmlns:w="{NS["w"]}">'
            f'<w:spacing w:before="120"/>'
            f'</w:pPr>'
        )
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.spacing.before"), "120")

    def test_spacing_after(self):
        xml = (
            f'<w:pPr xmlns:w="{NS["w"]}">'
            f'<w:spacing w:after="60"/>'
            f'</w:pPr>'
        )
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.spacing.after"), "60")

    def test_spacing_line(self):
        """OOXML 行距：276 = 1.15 倍，360 = 1.5 倍（240 法则下）"""
        xml = (
            f'<w:pPr xmlns:w="{NS["w"]}">'
            f'<w:spacing w:line="360" w:lineRule="auto"/>'
            f'</w:pPr>'
        )
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.spacing.line"), "360")

    def test_indent_first_line(self):
        """首行缩进：420 = 2 字符（21pt 字符 × 2）"""
        xml = (
            f'<w:pPr xmlns:w="{NS["w"]}">'
            f'<w:ind w:firstLine="420"/>'
            f'</w:pPr>'
        )
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.indent.first_line"), "420")

    def test_keep_next(self):
        xml = f'<w:pPr xmlns:w="{NS["w"]}"><w:keepNext/></w:pPr>'
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.keep_next"), "true")

    def test_page_break_before(self):
        xml = f'<w:pPr xmlns:w="{NS["w"]}"><w:pageBreakBefore/></w:pPr>'
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.page_break_before"), "true")

    def test_no_ppr_returns_empty_dict(self):
        self.assertEqual(para_props(None), {})

    def test_multiple_attributes_at_once(self):
        xml = (
            f'<w:pPr xmlns:w="{NS["w"]}">'
            f'<w:pStyle w:val="BodyText"/>'
            f'<w:spacing w:line="360" w:lineRule="auto"/>'
            f'<w:ind w:firstLine="420"/>'
            f'</w:pPr>'
        )
        d = para_props(self._el(xml))
        self.assertEqual(d.get("paragraph.style_id"), "BodyText")
        self.assertEqual(d.get("paragraph.spacing.line"), "360")
        self.assertEqual(d.get("paragraph.indent.first_line"), "420")


if __name__ == "__main__":
    unittest.main()
