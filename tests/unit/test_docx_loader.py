"""Tests for app.core.loaders.docx_loader.

策略：用 python-docx 在内存里构造一个**最小 docx**（不读真文件），
跑 load_docx() 验证 IR 字段正确性。

TDD: 任务 1.5 写测试 RED，任务 1.6 写实现 GREEN。
"""

import io
import unittest
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
    Section,
)
from app.core.loaders.docx_loader import load_docx


def _make_minimal_docx() -> bytes:
    """构造一个内存中的最小 docx：
    - 1 节（A4，上下 2.54cm，左右 3.17cm）
    - 3 段：1 个标题（Heading 1）、1 个正文（宋体 12pt 1.5 倍行距 + 首行缩进 0.74cm）、1 个空段

    返回 docx 的二进制（用 BytesIO + Document.save）。
    """
    doc = DocxDocument()

    # 1. 设置节的页面尺寸和边距
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.75)

    # 2. 标题段（Heading 1）
    h = doc.add_heading("1 引言", level=1)
    # 标题默认有"标题 1"样式 + 大字体（主题字体），不强求具体

    # 3. 正文段（手动设置 1.5 倍行距 + 首行缩进）
    p = doc.add_paragraph("本文研究的主要内容是...")
    p.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
    p.paragraph_format.page_break_before = True

    # 1.5 倍行距
    ppr = p._p.get_or_add_pPr()
    spacing = ppr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        ppr.append(spacing)
    spacing.set(qn("w:line"), "360")          # 360 / 240 = 1.5
    spacing.set(qn("w:lineRule"), "auto")

    # 首行缩进 0.74 cm（≈ 2 字符，宋体 12pt 下 1 字符约 0.37 cm）
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        ppr.append(ind)
    # 0.74 cm = 0.74 * 567 ≈ 419 twips（1 cm = 567 twips）
    # 只用 firstLine（twips），不用 firstLineChars——避免 chars 近似换算不准
    ind.set(qn("w:firstLine"), "419")

    # 第一个 run：宋体 12pt
    run = p.runs[0]
    run.font.name = "Times New Roman"
    run.font.size = Pt(12)
    run.font.underline = True
    run.font.color.rgb = RGBColor.from_string("FF0000")
    rpr = run._r.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "宋体")
    rfonts.set(qn("w:ascii"), "Times New Roman")
    rfonts.set(qn("w:hAnsi"), "Times New Roman")
    highlight = rpr.find(qn("w:highlight"))
    if highlight is None:
        highlight = OxmlElement("w:highlight")
        rpr.append(highlight)
    highlight.set(qn("w:val"), "yellow")

    # 4. 空段
    doc.add_paragraph("")

    # 5. 序列化到内存
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _write_minimal_docx(path: Path) -> None:
    """把内存 docx 写到磁盘。"""
    path.write_bytes(_make_minimal_docx())


class TestLoadDocx(unittest.TestCase):
    def setUp(self):
        # 用临时文件
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "minimal.docx"
        _write_minimal_docx(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_returns_document(self):
        doc = load_docx(self.path)
        self.assertIsInstance(doc, Document)

    def test_one_section_a4(self):
        doc = load_docx(self.path)
        self.assertEqual(len(doc.sections), 1)
        s = doc.sections[0]
        self.assertAlmostEqual(s.page_width_cm, 21.0, places=1)
        self.assertAlmostEqual(s.page_height_cm, 29.7, places=1)
        self.assertAlmostEqual(s.margin_top_cm, 2.54, places=1)
        self.assertAlmostEqual(s.margin_bottom_cm, 2.54, places=1)
        self.assertAlmostEqual(s.margin_left_cm, 3.17, places=1)
        self.assertAlmostEqual(s.margin_right_cm, 3.17, places=1)

    def test_orientation(self):
        doc = load_docx(self.path)
        s = doc.sections[0]
        self.assertEqual(s.orientation, "portrait")

    def test_three_paragraphs(self):
        doc = load_docx(self.path)
        self.assertEqual(len(doc.paragraphs), 3)

    def test_heading_paragraph(self):
        doc = load_docx(self.path)
        p0 = doc.paragraphs[0]
        self.assertEqual(p0.style_id, "Heading 1")
        self.assertEqual(p0.level, 1)
        self.assertIn("1 引言", p0.text)

    def test_body_paragraph_line_spacing(self):
        doc = load_docx(self.path)
        p1 = doc.paragraphs[1]
        # 360 / 240 = 1.5
        self.assertAlmostEqual(p1.line_spacing, 1.5, places=2)

    def test_body_paragraph_first_line_indent(self):
        doc = load_docx(self.path)
        p1 = doc.paragraphs[1]
        # 419 twips ≈ 0.74 cm（GB/T 7713 标准：2 字符 ≈ 0.74 cm @ 12pt 宋体）
        self.assertAlmostEqual(p1.first_line_indent_cm, 0.74, places=1)

    def test_body_paragraph_page_break_before(self):
        doc = load_docx(self.path)
        p1 = doc.paragraphs[1]
        self.assertTrue(p1.is_page_break_before)

    def test_body_paragraph_run_font(self):
        doc = load_docx(self.path)
        p1 = doc.paragraphs[1]
        self.assertEqual(len(p1.runs), 1)
        run = p1.runs[0]
        self.assertEqual(run.font_eastasia, "宋体")
        self.assertEqual(run.font_ascii, "Times New Roman")
        self.assertEqual(run.font_size_pt, 12.0)

    def test_body_paragraph_run_decoration(self):
        doc = load_docx(self.path)
        run = doc.paragraphs[1].runs[0]
        self.assertEqual(run.underline, "single")
        self.assertEqual(run.color_hex, "FF0000")
        self.assertEqual(run.highlight, "yellow")

    def test_metadata_default(self):
        doc = load_docx(self.path)
        self.assertIsInstance(doc.metadata, DocumentMetadata)


class TestLoadDocxErrors(unittest.TestCase):
    def test_nonexistent_file(self):
        with self.assertRaises((FileNotFoundError, ValueError)):
            load_docx("nonexistent_zzz.docx")


if __name__ == "__main__":
    unittest.main()
