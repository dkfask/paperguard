"""Tests for app.core.extract.section_props — Sprint 2.5 节属性抽取.

6 RED tests：
- page width / height（从 w:pgSz）
- margin 4 方向（top / bottom / left / right，从 w:pgMar）
- orientation（w:pgSz w:orient）
- 无 sect 返空 dict
- el 为 None 安全
- 多个属性同时存在
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree

from app.core.extract.section_props import section_props
from app.core.extract.xml_utils import NS, parse_xml


class TestSectionProps(unittest.TestCase):
    def _el(self, xml: str):
        return parse_xml(xml.encode("utf-8"))

    def test_page_size_width(self):
        """页宽 11906 twips = A4 横向"""
        xml = (
            f'<w:sectPr xmlns:w="{NS["w"]}">'
            f'<w:pgSz w:w="11906" w:h="16838"/>'
            f'</w:sectPr>'
        )
        d = section_props(self._el(xml))
        self.assertEqual(d.get("section.page.width"), "11906")

    def test_page_size_height(self):
        xml = (
            f'<w:sectPr xmlns:w="{NS["w"]}">'
            f'<w:pgSz w:w="11906" w:h="16838"/>'
            f'</w:sectPr>'
        )
        d = section_props(self._el(xml))
        self.assertEqual(d.get("section.page.height"), "16838")

    def test_margin_top(self):
        """页边距 twips：1440 = 1 inch = 2.54cm"""
        xml = (
            f'<w:sectPr xmlns:w="{NS["w"]}">'
            f'<w:pgMar w:top="1440"/>'
            f'</w:sectPr>'
        )
        d = section_props(self._el(xml))
        self.assertEqual(d.get("section.margin.top"), "1440")

    def test_orientation(self):
        xml = (
            f'<w:sectPr xmlns:w="{NS["w"]}">'
            f'<w:pgSz w:w="11906" w:h="16838" w:orient="landscape"/>'
            f'</w:sectPr>'
        )
        d = section_props(self._el(xml))
        self.assertEqual(d.get("section.orientation"), "landscape")

    def test_no_sect_returns_empty_dict(self):
        self.assertEqual(section_props(None), {})

    def test_multiple_margins_at_once(self):
        xml = (
            f'<w:sectPr xmlns:w="{NS["w"]}">'
            f'<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800"/>'
            f'</w:sectPr>'
        )
        d = section_props(self._el(xml))
        self.assertEqual(d.get("section.margin.top"), "1440")
        self.assertEqual(d.get("section.margin.right"), "1800")
        self.assertEqual(d.get("section.margin.bottom"), "1440")
        self.assertEqual(d.get("section.margin.left"), "1800")


if __name__ == "__main__":
    unittest.main()
