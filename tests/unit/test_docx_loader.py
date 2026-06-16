"""Tests for app.core.loaders.docx_loader.

策略：用 python-docx 在内存里构造一个**最小 docx**（不读真文件），
跑 load_docx() 验证 IR 字段正确性。

TDD: 任务 1.5 写测试 RED，任务 1.6 写实现 GREEN。
"""

import io
import unittest
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.style import WD_STYLE_TYPE
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
    vert_align = rpr.find(qn("w:vertAlign"))
    if vert_align is None:
        vert_align = OxmlElement("w:vertAlign")
        rpr.append(vert_align)
    vert_align.set(qn("w:val"), "superscript")

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

    def test_body_paragraph_alignment(self):
        doc = load_docx(self.path)
        p1 = doc.paragraphs[1]
        self.assertEqual(p1.alignment, "justify")

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
        self.assertEqual(run.vert_align, "superscript")

    def test_metadata_default(self):
        doc = load_docx(self.path)
        self.assertIsInstance(doc.metadata, DocumentMetadata)


class TestLoadDocxErrors(unittest.TestCase):
    def test_nonexistent_file(self):
        with self.assertRaises((FileNotFoundError, ValueError)):
            load_docx("nonexistent_zzz.docx")


class TestLoadDocxHeaderFooterParagraphs(unittest.TestCase):
    def test_loads_header_and_footer_paragraphs_separately_from_body(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "header_footer.docx"
            docx = DocxDocument()
            docx.sections[0].header.paragraphs[0].text = "论文题名"
            docx.sections[0].footer.paragraphs[0].text = "第 1 页"
            docx.add_paragraph("正文内容")
            docx.save(path)

            loaded = load_docx(path)

        self.assertEqual([p.text for p in loaded.paragraphs], ["正文内容"])
        self.assertEqual([p.text for p in loaded.header_paragraphs], ["论文题名"])
        self.assertEqual([p.text for p in loaded.footer_paragraphs], ["第 1 页"])


class TestLoadDocxParagraphStyleInheritance(unittest.TestCase):
    def test_reads_alignment_from_paragraph_style_when_paragraph_has_no_direct_alignment(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_alignment.docx"
            docx = DocxDocument()
            style = docx.styles.add_style("PG Body Style", WD_STYLE_TYPE.PARAGRAPH)
            style.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
            paragraph = docx.add_paragraph("styled body")
            paragraph.style = style
            self.assertIsNone(paragraph.alignment)
            docx.save(path)

            loaded = load_docx(path)

        self.assertEqual(loaded.paragraphs[0].alignment, "justify")

    def test_reads_word_justification_variants_as_justify(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "justify_variant.docx"
            docx = DocxDocument()
            paragraph = docx.add_paragraph("variant justify")
            paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY_MED
            docx.save(path)

            loaded = load_docx(path)

        self.assertEqual(loaded.paragraphs[0].alignment, "justify")

    def test_reads_spacing_from_paragraph_style_when_paragraph_has_no_direct_spacing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_spacing.docx"
            docx = DocxDocument()
            style = docx.styles.add_style("PG Spacing Style", WD_STYLE_TYPE.PARAGRAPH)
            style.paragraph_format.line_spacing = 1.5
            style.paragraph_format.space_before = Pt(6)
            style.paragraph_format.space_after = Pt(12)
            paragraph = docx.add_paragraph("styled spacing")
            paragraph.style = style
            self.assertIsNone(paragraph.paragraph_format.line_spacing)
            self.assertIsNone(paragraph.paragraph_format.space_before)
            self.assertIsNone(paragraph.paragraph_format.space_after)
            docx.save(path)

            loaded = load_docx(path)

        self.assertAlmostEqual(loaded.paragraphs[0].line_spacing, 1.5, places=2)
        self.assertEqual(loaded.paragraphs[0].space_before_pt, 6.0)
        self.assertEqual(loaded.paragraphs[0].space_after_pt, 12.0)

    def test_reads_indent_and_page_break_from_paragraph_style_when_no_direct_format(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_indent_page_break.docx"
            docx = DocxDocument()
            style = docx.styles.add_style("PG Indent Style", WD_STYLE_TYPE.PARAGRAPH)
            style.paragraph_format.first_line_indent = Cm(0.74)
            style.paragraph_format.page_break_before = True
            paragraph = docx.add_paragraph("styled indent")
            paragraph.style = style
            self.assertIsNone(paragraph.paragraph_format.first_line_indent)
            self.assertIsNone(paragraph.paragraph_format.page_break_before)
            docx.save(path)

            loaded = load_docx(path)

        self.assertAlmostEqual(loaded.paragraphs[0].first_line_indent_cm, 0.74, places=2)
        self.assertTrue(loaded.paragraphs[0].is_page_break_before)

    def test_reads_direct_hanging_indent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hanging_indent.docx"
            docx = DocxDocument()
            paragraph = docx.add_paragraph("reference item")
            paragraph.paragraph_format.first_line_indent = Cm(-0.74)
            docx.save(path)

            loaded = load_docx(path)

        self.assertAlmostEqual(loaded.paragraphs[0].hanging_indent_cm, 0.74, places=2)
        self.assertAlmostEqual(loaded.paragraphs[0].first_line_indent_cm, 0.0, places=2)

    def test_reads_run_font_from_paragraph_style_when_run_has_no_direct_format(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_run_font.docx"
            docx = DocxDocument()
            style = docx.styles.add_style("PG Run Style", WD_STYLE_TYPE.PARAGRAPH)
            style.font.name = "Times New Roman"
            style.font.size = Pt(12)
            rpr = style.element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = OxmlElement("w:rFonts")
                rpr.append(rfonts)
            rfonts.set(qn("w:eastAsia"), "宋体")
            rfonts.set(qn("w:ascii"), "Times New Roman")
            rfonts.set(qn("w:hAnsi"), "Times New Roman")
            paragraph = docx.add_paragraph("styled run font")
            paragraph.style = style
            self.assertIsNone(paragraph.runs[0].font.size)
            docx.save(path)

            loaded = load_docx(path)

        run = loaded.paragraphs[0].runs[0]
        self.assertEqual(run.font_eastasia, "宋体")
        self.assertEqual(run.font_ascii, "Times New Roman")
        self.assertEqual(run.font_hansi, "Times New Roman")
        self.assertEqual(run.font_size_pt, 12.0)

    def test_direct_run_font_overrides_only_present_style_inherited_font_parts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "partial_direct_run_font.docx"
            docx = DocxDocument()
            style = docx.styles.add_style("PG Partial Run Style", WD_STYLE_TYPE.PARAGRAPH)
            style.font.size = Pt(12)
            style_rpr = style.element.get_or_add_rPr()
            style_rfonts = style_rpr.find(qn("w:rFonts"))
            if style_rfonts is None:
                style_rfonts = OxmlElement("w:rFonts")
                style_rpr.append(style_rfonts)
            style_rfonts.set(qn("w:eastAsia"), "宋体")
            style_rfonts.set(qn("w:hAnsi"), "Times New Roman")
            paragraph = docx.add_paragraph()
            paragraph.style = style
            run = paragraph.add_run("partial direct font")
            run_rpr = run._r.get_or_add_rPr()
            run_rfonts = OxmlElement("w:rFonts")
            run_rpr.append(run_rfonts)
            run_rfonts.set(qn("w:ascii"), "Arial")
            docx.save(path)

            loaded = load_docx(path)

        loaded_run = loaded.paragraphs[0].runs[0]
        self.assertEqual(loaded_run.font_eastasia, "宋体")
        self.assertEqual(loaded_run.font_ascii, "Arial")
        self.assertEqual(loaded_run.font_hansi, "Times New Roman")
        self.assertEqual(loaded_run.font_size_pt, 12.0)

    def test_reads_run_decoration_from_paragraph_style_when_run_has_no_direct_format(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_run_decoration.docx"
            docx = DocxDocument()
            style = docx.styles.add_style("PG Decoration Style", WD_STYLE_TYPE.PARAGRAPH)
            rpr = style.element.get_or_add_rPr()
            for tag in ("w:b", "w:i"):
                if rpr.find(qn(tag)) is None:
                    rpr.append(OxmlElement(tag))
            underline = OxmlElement("w:u")
            underline.set(qn("w:val"), "single")
            rpr.append(underline)
            color = OxmlElement("w:color")
            color.set(qn("w:val"), "FF0000")
            rpr.append(color)
            highlight = OxmlElement("w:highlight")
            highlight.set(qn("w:val"), "yellow")
            rpr.append(highlight)
            vert_align = OxmlElement("w:vertAlign")
            vert_align.set(qn("w:val"), "superscript")
            rpr.append(vert_align)
            paragraph = docx.add_paragraph("styled decoration")
            paragraph.style = style
            docx.save(path)

            loaded = load_docx(path)

        loaded_run = loaded.paragraphs[0].runs[0]
        self.assertTrue(loaded_run.bold)
        self.assertTrue(loaded_run.italic)
        self.assertEqual(loaded_run.underline, "single")
        self.assertEqual(loaded_run.color_hex, "FF0000")
        self.assertEqual(loaded_run.highlight, "yellow")
        self.assertEqual(loaded_run.vert_align, "superscript")

    def test_reads_paragraph_and_run_formatting_from_base_style_chain(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "base_style_chain.docx"
            docx = DocxDocument()
            base = docx.styles.add_style("PG Base Style", WD_STYLE_TYPE.PARAGRAPH)
            base.paragraph_format.line_spacing = 1.5
            base.paragraph_format.space_before = Pt(3)
            base.paragraph_format.space_after = Pt(6)
            base.paragraph_format.first_line_indent = Cm(0.74)
            base.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
            base.font.size = Pt(12)
            base_rpr = base.element.get_or_add_rPr()
            base_rfonts = base_rpr.find(qn("w:rFonts"))
            if base_rfonts is None:
                base_rfonts = OxmlElement("w:rFonts")
                base_rpr.append(base_rfonts)
            base_rfonts.set(qn("w:eastAsia"), "宋体")
            base_rfonts.set(qn("w:ascii"), "Times New Roman")
            base_rfonts.set(qn("w:hAnsi"), "Times New Roman")
            child = docx.styles.add_style("PG Child Style", WD_STYLE_TYPE.PARAGRAPH)
            child.base_style = base
            paragraph = docx.add_paragraph("base inherited text")
            paragraph.style = child
            docx.save(path)

            loaded = load_docx(path)

        paragraph = loaded.paragraphs[0]
        self.assertEqual(paragraph.alignment, "justify")
        self.assertAlmostEqual(paragraph.line_spacing, 1.5, places=2)
        self.assertEqual(paragraph.space_before_pt, 3.0)
        self.assertEqual(paragraph.space_after_pt, 6.0)
        self.assertAlmostEqual(paragraph.first_line_indent_cm, 0.74, places=2)
        self.assertEqual(paragraph.runs[0].font_eastasia, "宋体")
        self.assertEqual(paragraph.runs[0].font_ascii, "Times New Roman")
        self.assertEqual(paragraph.runs[0].font_hansi, "Times New Roman")
        self.assertEqual(paragraph.runs[0].font_size_pt, 12.0)


class TestLoadDocxTableParagraphs(unittest.TestCase):
    def test_reads_table_cell_paragraphs_in_document_order(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table_paragraphs.docx"
            docx = DocxDocument()
            docx.add_paragraph("before table")
            table = docx.add_table(rows=1, cols=2)
            table.cell(0, 0).paragraphs[0].text = "left cell"
            table.cell(0, 1).paragraphs[0].text = "right cell"
            docx.add_paragraph("after table")
            docx.save(path)

            loaded = load_docx(path)

        self.assertEqual(
            [paragraph.text for paragraph in loaded.paragraphs],
            ["before table", "left cell", "right cell", "after table"],
        )

    def test_reads_table_cell_location_for_cell_paragraphs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table_cell_location.docx"
            docx = DocxDocument()
            docx.add_paragraph("before table")
            table = docx.add_table(rows=2, cols=2)
            table.cell(0, 0).paragraphs[0].text = "r0c0"
            table.cell(0, 1).paragraphs[0].text = "r0c1"
            table.cell(1, 0).paragraphs[0].text = "r1c0"
            table.cell(1, 1).paragraphs[0].text = "r1c1"
            docx.add_paragraph("after table")
            docx.save(path)

            loaded = load_docx(path)

        locations = [
            (p.text, p.table_index, p.table_row_index, p.table_cell_index)
            for p in loaded.paragraphs
        ]
        self.assertEqual(
            locations,
            [
                ("before table", None, None, None),
                ("r0c0", 0, 0, 0),
                ("r0c1", 0, 0, 1),
                ("r1c0", 0, 1, 0),
                ("r1c1", 0, 1, 1),
                ("after table", None, None, None),
            ],
        )

    def test_reads_merged_table_cell_paragraph_once(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "merged_table_cell.docx"
            docx = DocxDocument()
            table = docx.add_table(rows=1, cols=2)
            merged = table.cell(0, 0).merge(table.cell(0, 1))
            merged.paragraphs[0].text = "merged cell"
            docx.save(path)

            loaded = load_docx(path)

        self.assertEqual([paragraph.text for paragraph in loaded.paragraphs], ["merged cell"])


if __name__ == "__main__":
    unittest.main()
