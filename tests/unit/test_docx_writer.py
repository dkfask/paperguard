"""Tests for app.core.writers.docx_writer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import docx
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn

from app.core.ir import Document, DocumentMetadata, Paragraph, Run, Section
from app.core.loaders.docx_loader import load_docx
from app.core.writers.docx_writer import write_ir_to_docx


def _write_and_open(ir: Document):
    tmpdir = tempfile.TemporaryDirectory()
    path = Path(tmpdir.name) / "out.docx"
    write_ir_to_docx(ir, str(path))
    return tmpdir, docx.Document(str(path))


class TestDocxWriterFormatting(unittest.TestCase):
    def test_writes_paragraph_spacing_indent_and_page_break(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="正文",
                    level=None,
                    runs=(Run(text="正文"),),
                    line_spacing=1.5,
                    space_before_pt=6.0,
                    space_after_pt=12.0,
                    first_line_indent_cm=0.74,
                    alignment="center",
                    is_page_break_before=True,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        tmpdir, written = _write_and_open(ir)
        self.addCleanup(tmpdir.cleanup)
        fmt = written.paragraphs[0].paragraph_format

        self.assertEqual(fmt.line_spacing, 1.5)
        self.assertAlmostEqual(fmt.space_before.pt, 6.0, places=1)
        self.assertAlmostEqual(fmt.space_after.pt, 12.0, places=1)
        self.assertAlmostEqual(fmt.first_line_indent.cm, 0.74, places=2)
        self.assertEqual(written.paragraphs[0].alignment, WD_PARAGRAPH_ALIGNMENT.CENTER)
        self.assertTrue(fmt.page_break_before)

    def test_writes_run_font_variants_and_styles(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="正文",
                    level=None,
                    runs=(
                        Run(
                            text="正文",
                            font_eastasia="宋体",
                            font_ascii="Times New Roman",
                            font_hansi="Times New Roman",
                            font_size_pt=12.0,
                            bold=True,
                            italic=True,
                            underline="single",
                            color_hex="FF0000",
                            highlight="yellow",
                            vert_align="superscript",
                        ),
                    ),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.74,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        tmpdir, written = _write_and_open(ir)
        self.addCleanup(tmpdir.cleanup)
        run = written.paragraphs[0].runs[0]
        rfonts = run._r.get_or_add_rPr().find(qn("w:rFonts"))

        self.assertEqual(rfonts.get(qn("w:eastAsia")), "宋体")
        self.assertEqual(rfonts.get(qn("w:ascii")), "Times New Roman")
        self.assertEqual(rfonts.get(qn("w:hAnsi")), "Times New Roman")
        self.assertAlmostEqual(run.font.size.pt, 12.0, places=1)
        self.assertTrue(run.bold)
        self.assertTrue(run.italic)
        self.assertTrue(run.underline)
        self.assertEqual(str(run.font.color.rgb), "FF0000")
        self.assertEqual(str(run.font.highlight_color).split(" ")[0], "YELLOW")
        self.assertTrue(run.font.superscript)

    def test_written_docx_roundtrips_supported_formatting_through_loader(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="正文",
                    level=None,
                    runs=(
                        Run(
                            text="正文",
                            font_eastasia="宋体",
                            font_ascii="Times New Roman",
                            font_hansi="Times New Roman",
                            font_size_pt=12.0,
                            bold=True,
                            italic=True,
                            underline="single",
                            color_hex="FF0000",
                            highlight="yellow",
                            vert_align="subscript",
                        ),
                    ),
                    line_spacing=1.5,
                    space_before_pt=6.0,
                    space_after_pt=12.0,
                    first_line_indent_cm=0.74,
                    alignment="center",
                    is_page_break_before=True,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roundtrip.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        para = loaded.paragraphs[0]
        run = para.runs[0]
        self.assertEqual(para.line_spacing, 1.5)
        self.assertAlmostEqual(para.space_before_pt, 6.0, places=1)
        self.assertAlmostEqual(para.space_after_pt, 12.0, places=1)
        self.assertAlmostEqual(para.first_line_indent_cm, 0.74, places=2)
        self.assertEqual(para.alignment, "center")
        self.assertTrue(para.is_page_break_before)
        self.assertEqual(run.font_eastasia, "宋体")
        self.assertEqual(run.font_ascii, "Times New Roman")
        self.assertEqual(run.font_hansi, "Times New Roman")
        self.assertEqual(run.font_size_pt, 12.0)
        self.assertTrue(run.bold)
        self.assertTrue(run.italic)
        self.assertEqual(run.underline, "single")
        self.assertEqual(run.color_hex, "FF0000")
        self.assertEqual(run.highlight, "yellow")
        self.assertEqual(run.vert_align, "subscript")

    def test_written_docx_roundtrips_heading_level_through_loader(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="1.1 背景",
                    level=2,
                    runs=(Run(text="1.1 背景"),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heading.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        self.assertEqual(loaded.paragraphs[0].style_id, "Heading 2")
        self.assertEqual(loaded.paragraphs[0].level, 2)


if __name__ == "__main__":
    unittest.main()
