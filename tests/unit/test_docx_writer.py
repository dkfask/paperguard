"""Tests for app.core.writers.docx_writer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import docx
from docx.enum.section import WD_ORIENT
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

    def test_writes_section_orientation_and_header_footer_distances(self):
        ir = Document(
            sections=(
                Section(
                    page_width_cm=29.7,
                    page_height_cm=21.0,
                    margin_top_cm=2.0,
                    margin_bottom_cm=2.2,
                    margin_left_cm=2.4,
                    margin_right_cm=2.6,
                    header_distance_cm=1.4,
                    footer_distance_cm=1.6,
                    orientation="landscape",
                ),
            ),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="body",
                    level=None,
                    runs=(Run(text="body"),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        tmpdir, written = _write_and_open(ir)
        self.addCleanup(tmpdir.cleanup)
        section = written.sections[0]

        self.assertEqual(section.orientation, WD_ORIENT.LANDSCAPE)
        self.assertAlmostEqual(section.header_distance.cm, 1.4, places=1)
        self.assertAlmostEqual(section.footer_distance.cm, 1.6, places=1)

    def test_written_docx_roundtrips_section_orientation_and_header_footer_distances(self):
        ir = Document(
            sections=(
                Section(
                    page_width_cm=29.7,
                    page_height_cm=21.0,
                    header_distance_cm=1.4,
                    footer_distance_cm=1.6,
                    orientation="landscape",
                ),
            ),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="body",
                    level=None,
                    runs=(Run(text="body"),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "section.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        section = loaded.sections[0]
        self.assertEqual(section.orientation, "landscape")
        self.assertAlmostEqual(section.header_distance_cm, 1.4, places=1)
        self.assertAlmostEqual(section.footer_distance_cm, 1.6, places=1)

    def test_written_docx_roundtrips_multiple_sections(self):
        ir = Document(
            sections=(
                Section(
                    page_width_cm=21.0,
                    page_height_cm=29.7,
                    margin_top_cm=2.0,
                    margin_bottom_cm=2.0,
                    margin_left_cm=2.5,
                    margin_right_cm=2.5,
                    header_distance_cm=1.2,
                    footer_distance_cm=1.3,
                    orientation="portrait",
                ),
                Section(
                    page_width_cm=29.7,
                    page_height_cm=21.0,
                    margin_top_cm=1.5,
                    margin_bottom_cm=1.6,
                    margin_left_cm=1.7,
                    margin_right_cm=1.8,
                    header_distance_cm=1.4,
                    footer_distance_cm=1.5,
                    orientation="landscape",
                ),
            ),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="body",
                    level=None,
                    runs=(Run(text="body"),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sections.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        self.assertEqual(len(loaded.sections), 2)
        first, second = loaded.sections
        self.assertEqual(first.orientation, "portrait")
        self.assertAlmostEqual(first.header_distance_cm, 1.2, places=1)
        self.assertAlmostEqual(first.footer_distance_cm, 1.3, places=1)
        self.assertEqual(second.orientation, "landscape")
        self.assertAlmostEqual(second.margin_left_cm, 1.7, places=1)
        self.assertAlmostEqual(second.margin_right_cm, 1.8, places=1)
        self.assertAlmostEqual(second.header_distance_cm, 1.4, places=1)
        self.assertAlmostEqual(second.footer_distance_cm, 1.5, places=1)

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

    def test_written_docx_preserves_paragraph_text_when_runs_are_empty(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="plain paragraph",
                    level=None,
                    runs=(),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        self.assertEqual(loaded.paragraphs[0].text, "plain paragraph")
        self.assertEqual(loaded.paragraphs[0].runs[0].text, "plain paragraph")

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

    def test_written_docx_roundtrips_custom_paragraph_style_id(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Bibliography",
                    text="[1] Paper.",
                    level=None,
                    runs=(Run(text="[1] Paper."),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom_style.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        self.assertEqual(loaded.paragraphs[0].style_id, "Bibliography")

    def test_written_docx_roundtrips_hanging_indent(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Bibliography",
                    text="[1] Paper.",
                    level=None,
                    runs=(Run(text="[1] Paper."),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                    hanging_indent_cm=0.74,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hanging_indent.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        self.assertAlmostEqual(loaded.paragraphs[0].hanging_indent_cm, 0.74, places=2)
        self.assertAlmostEqual(loaded.paragraphs[0].first_line_indent_cm, 0.0, places=2)

    def test_written_docx_roundtrips_header_and_footer_paragraphs(self):
        header = Paragraph(
            style_id="Header",
            text="论文题名",
            level=None,
            runs=(Run(text="论文题名"),),
            line_spacing=1.0,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        footer = Paragraph(
            style_id="Footer",
            text="第 1 页",
            level=None,
            runs=(Run(text="第 1 页"),),
            line_spacing=1.0,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="正文",
                    level=None,
                    runs=(Run(text="正文"),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.74,
                ),
            ),
            metadata=DocumentMetadata(title="t"),
            header_paragraphs=(header,),
            footer_paragraphs=(footer,),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "header_footer.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        self.assertEqual([p.text for p in loaded.header_paragraphs], ["论文题名"])
        self.assertEqual([p.text for p in loaded.footer_paragraphs], ["第 1 页"])

    def test_written_docx_preserves_header_footer_text_when_runs_are_empty(self):
        header = Paragraph(
            style_id="Header",
            text="plain header",
            level=None,
            runs=(),
            line_spacing=1.0,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        footer = Paragraph(
            style_id="Footer",
            text="plain footer",
            level=None,
            runs=(),
            line_spacing=1.0,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        ir = Document(
            sections=(Section(),),
            paragraphs=(),
            metadata=DocumentMetadata(title="t"),
            header_paragraphs=(header,),
            footer_paragraphs=(footer,),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "header_footer_plain.docx"
            write_ir_to_docx(ir, str(path))
            loaded = load_docx(path)

        self.assertEqual(loaded.header_paragraphs[0].text, "plain header")
        self.assertEqual(loaded.footer_paragraphs[0].text, "plain footer")

    def test_written_docx_roundtrips_table_cell_paragraphs(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="before",
                    level=None,
                    runs=(Run(text="before"),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
                Paragraph(
                    style_id="Table Text",
                    text="r0c0",
                    level=None,
                    runs=(Run(text="r0c0", font_size_pt=10.5),),
                    line_spacing=1.0,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                    table_index=0,
                    table_row_index=0,
                    table_cell_index=0,
                ),
                Paragraph(
                    style_id="Table Text",
                    text="r0c1",
                    level=None,
                    runs=(Run(text="r0c1", font_size_pt=10.5),),
                    line_spacing=1.0,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                    table_index=0,
                    table_row_index=0,
                    table_cell_index=1,
                ),
                Paragraph(
                    style_id="Body Text",
                    text="after",
                    level=None,
                    runs=(Run(text="after"),),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.0,
                ),
            ),
            metadata=DocumentMetadata(title="table"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.docx"
            write_ir_to_docx(ir, str(path))
            written = docx.Document(str(path))
            loaded = load_docx(path)

        self.assertEqual(len(written.tables), 1)
        self.assertEqual(written.tables[0].cell(0, 0).text, "r0c0")
        self.assertEqual(written.tables[0].cell(0, 1).text, "r0c1")
        self.assertEqual(
            [
                (p.text, p.table_index, p.table_row_index, p.table_cell_index)
                for p in loaded.paragraphs
            ],
            [
                ("before", None, None, None),
                ("r0c0", 0, 0, 0),
                ("r0c1", 0, 0, 1),
                ("after", None, None, None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
