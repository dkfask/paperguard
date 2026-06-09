"""Tests for app.core.extract.run_props — Sprint 2.5 run 属性抽取.

8 RED tests：
- 字体 4 字段（ascii / eastAsia / hAnsi / cs）
- 字号 half_points
- bold / italic
- 无 rPr 返空 dict
- el 为 None 安全
- 过滤 None 值（不输出 w:rFonts w:cs=""）

**关键陷阱**（Context7 调研）：python-docx `style.font.name` 不暴露
`w:eastAsia` 中文字体名，必须 lxml 走 `<w:rFonts w:eastAsia>`。
本模块用 lxml 直拆。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree

from app.core.extract.run_props import run_props
from app.core.extract.xml_utils import NS


def _w(tag: str) -> str:
    return f"{{{NS['w']}}}{tag}"


class TestRunProps(unittest.TestCase):
    def _el(self, xml: str):
        return etree.fromstring(xml.encode("utf-8"))

    def test_fonts_eastasia(self):
        """中文字体（关键）：w:rFonts w:eastAsia='宋体'"""
        xml = (
            f'<w:rPr xmlns:w="{NS["w"]}">'
            f'<w:rFonts w:eastAsia="SimSun"/>'
            f'</w:rPr>'
        )
        d = run_props(self._el(xml))
        self.assertEqual(d.get("font.eastAsia"), "SimSun")

    def test_fonts_ascii(self):
        xml = (
            f'<w:rPr xmlns:w="{NS["w"]}">'
            f'<w:rFonts w:ascii="Times New Roman"/>'
            f'</w:rPr>'
        )
        d = run_props(self._el(xml))
        self.assertEqual(d.get("font.ascii"), "Times New Roman")

    def test_fonts_hAnsi(self):
        xml = (
            f'<w:rPr xmlns:w="{NS["w"]}">'
            f'<w:rFonts w:hAnsi="Arial"/>'
            f'</w:rPr>'
        )
        d = run_props(self._el(xml))
        self.assertEqual(d.get("font.hAnsi"), "Arial")

    def test_fonts_cs(self):
        xml = (
            f'<w:rPr xmlns:w="{NS["w"]}">'
            f'<w:rFonts w:cs="Symbol"/>'
            f'</w:rPr>'
        )
        d = run_props(self._el(xml))
        self.assertEqual(d.get("font.cs"), "Symbol")

    def test_size_half_points(self):
        """OOXML 字号存为半磅（24 = 12pt）"""
        xml = (
            f'<w:rPr xmlns:w="{NS["w"]}">'
            f'<w:sz w:val="24"/>'
            f'</w:rPr>'
        )
        d = run_props(self._el(xml))
        self.assertEqual(d.get("font.size_half_points"), "24")

    def test_bold(self):
        xml = f'<w:rPr xmlns:w="{NS["w"]}"><w:b/></w:rPr>'
        d = run_props(self._el(xml))
        self.assertEqual(d.get("font.bold"), "true")

    def test_no_rpr_returns_empty_dict(self):
        self.assertEqual(run_props(None), {})

    def test_empty_rpr_returns_empty_dict(self):
        xml = f'<w:rPr xmlns:w="{NS["w"]}"/>'
        self.assertEqual(run_props(self._el(xml)), {})


if __name__ == "__main__":
    unittest.main()
