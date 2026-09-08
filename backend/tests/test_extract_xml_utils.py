"""Tests for app.core.extract.xml_utils — Sprint 2.5 XML 工具 (lxml 集成).

Sprint 2.5 commit #6: 8 RED tests.

借鉴 references/format-thesis-docx/scripts/extract_requirements.py:27-33
（qn + attr 模式）但实现**重写**。NS 字典定义模块级常量。

设计契约（plan §3.1）：
- qn(tag): "w:rFonts" -> Clark notation
- attr(el, name): 安全取属性，None 安全
- load_part(zf, name): 加载 OOXML part，缺失返 None（不抛）
- NS: 字典含 w / w14 / r 三个 namespace
"""

from __future__ import annotations

import os
import unittest
import zipfile
from io import BytesIO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree

from app.core.extract.xml_utils import NS, attr, load_part, qn


class TestNamespaceDict(unittest.TestCase):
    def test_ns_has_wordprocessingml_main(self):
        self.assertEqual(
            NS["w"],
            "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        )

    def test_ns_has_word2010_extension(self):
        self.assertIn("w14", NS)

    def test_ns_has_relationships(self):
        self.assertEqual(
            NS["r"],
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        )


class TestQN(unittest.TestCase):
    def test_qn_w_prefix(self):
        result = qn("w:rFonts")
        self.assertEqual(
            result,
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts",
        )

    def test_qn_w14_prefix(self):
        result = qn("w14:paraId")
        self.assertIn("paraId", result)
        self.assertTrue(result.startswith("{"))

    def test_qn_unknown_prefix_raises(self):
        with self.assertRaises(ValueError):
            qn("xxx:foo")


class TestAttr(unittest.TestCase):
    def test_attr_existing(self):
        xml = '<w:rFonts xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:ascii="Times"/>'
        el = etree.fromstring(xml.encode("utf-8"))
        self.assertEqual(attr(el, "w:ascii"), "Times")

    def test_attr_missing_attribute_returns_none(self):
        xml = '<w:rFonts xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
        el = etree.fromstring(xml.encode("utf-8"))
        self.assertIsNone(attr(el, "w:ascii"))

    def test_attr_none_element_safe(self):
        # 不抛异常，返 None
        self.assertIsNone(attr(None, "w:foo"))


class TestLoadPart(unittest.TestCase):
    def _make_docx(self, parts: dict[str, str]) -> bytes:
        """辅助：构造一个虚拟 docx (zipfile) 内存。"""
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in parts.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def test_load_part_success(self):
        xml = '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>'
        data = self._make_docx({"word/document.xml": xml})
        with zipfile.ZipFile(BytesIO(data)) as zf:
            el = load_part(zf, "word/document.xml")
        self.assertIsNotNone(el)
        self.assertEqual(el.tag, qn("w:document"))

    def test_load_part_missing_returns_none(self):
        data = self._make_docx({})  # 空 docx
        with zipfile.ZipFile(BytesIO(data)) as zf:
            self.assertIsNone(load_part(zf, "word/numbering.xml"))

    def test_load_part_does_not_expand_entities(self):
        """上传的 docx 部件带实体膨胀时不得展开(防内存/CPU 耗尽)。"""
        bomb = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE w:document ['
            '<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            '<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>&d;</w:t></w:r></w:p></w:body></w:document>'
        )
        data = self._make_docx({"word/document.xml": bomb})
        with zipfile.ZipFile(BytesIO(data)) as zf:
            el = load_part(zf, "word/document.xml")
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        self.assertLess(len(text), 100, "实体被展开了: %d 字符" % len(text))

    def test_load_part_keeps_predefined_entities(self):
        """加固解析器不得影响 &amp; 等预定义实体与数字字符引用。"""
        xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>a &amp; b &#23435;</w:t></w:r></w:p></w:body></w:document>'
        )
        data = self._make_docx({"word/document.xml": xml})
        with zipfile.ZipFile(BytesIO(data)) as zf:
            el = load_part(zf, "word/document.xml")
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        self.assertEqual(text, "a & b 宋")


if __name__ == "__main__":
    unittest.main()
