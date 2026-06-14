"""Integration tests for fix -> write docx -> load -> validate loops."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.core.engine import validate_docx
from app.core.ir import Document, DocumentMetadata, Paragraph, Run, Section
from app.core.loaders.docx_loader import load_docx
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes


PACK_ID = "GB-T-7713-1-2006"


class TestFixWriteValidateLoop(unittest.TestCase):
    def test_style_inherited_body_run_decoration_triggers_decoration_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_body_decoration.docx"
            docx = DocxDocument()
            normal = docx.styles["Normal"]
            normal.paragraph_format.line_spacing = 1.5
            normal.paragraph_format.space_before = Pt(0)
            normal.paragraph_format.space_after = Pt(0)
            normal.paragraph_format.first_line_indent = Cm(0.74)
            normal.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
            normal.font.name = "Times New Roman"
            normal.font.size = Pt(12)
            rpr = normal.element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = OxmlElement("w:rFonts")
                rpr.append(rfonts)
            rfonts.set(qn("w:eastAsia"), "宋体")
            rfonts.set(qn("w:ascii"), "Times New Roman")
            rfonts.set(qn("w:hAnsi"), "Times New Roman")
            for tag in ("w:b", "w:i"):
                if rpr.find(qn(tag)) is None:
                    rpr.append(OxmlElement(tag))
            underline = OxmlElement("w:u")
            underline.set(qn("w:val"), "single")
            rpr.append(underline)
            highlight = OxmlElement("w:highlight")
            highlight.set(qn("w:val"), "yellow")
            rpr.append(highlight)
            vert_align = OxmlElement("w:vertAlign")
            vert_align.set(qn("w:val"), "superscript")
            rpr.append(vert_align)
            paragraph = docx.add_paragraph("styled decoration")
            paragraph.style = normal
            docx.save(path)

            loaded = load_docx(path)

        findings = validate_docx(loaded, PACK_ID)
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("PG-BODY-NO-BOLD", rule_ids)
        self.assertIn("PG-BODY-NO-ITALIC", rule_ids)
        self.assertIn("PG-BODY-NO-UNDERLINE", rule_ids)
        self.assertIn("PG-BODY-NO-HIGHLIGHT", rule_ids)
        self.assertIn("PG-BODY-NO-VERT-ALIGN", rule_ids)

    def test_style_inherited_body_run_font_does_not_trigger_font_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_body_font.docx"
            docx = DocxDocument()
            normal = docx.styles["Normal"]
            normal.paragraph_format.line_spacing = 1.5
            normal.paragraph_format.space_before = Pt(0)
            normal.paragraph_format.space_after = Pt(0)
            normal.paragraph_format.first_line_indent = Cm(0.74)
            normal.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
            normal.font.name = "Times New Roman"
            normal.font.size = Pt(12)
            rpr = normal.element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = OxmlElement("w:rFonts")
                rpr.append(rfonts)
            rfonts.set(qn("w:eastAsia"), "宋体")
            rfonts.set(qn("w:ascii"), "Times New Roman")
            rfonts.set(qn("w:hAnsi"), "Times New Roman")
            paragraph = docx.add_paragraph("styled body")
            paragraph.style = normal
            docx.save(path)

            loaded = load_docx(path)

        findings = validate_docx(loaded, PACK_ID)
        rule_ids = {f.rule_id for f in findings}
        self.assertNotIn("PG-BODY-FONT-SIZE", rule_ids)
        self.assertNotIn("PG-BODY-FONT-EASTASIA", rule_ids)
        self.assertNotIn("PG-BODY-FONT-ASCII", rule_ids)
        self.assertNotIn("PG-BODY-FONT-HANSI", rule_ids)

    def test_style_inherited_body_alignment_does_not_trigger_alignment_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "style_alignment.docx"
            docx = DocxDocument()
            docx.styles["Normal"].paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
            paragraph = docx.add_paragraph("styled body")
            paragraph.style = docx.styles["Normal"]
            docx.save(path)

            loaded = load_docx(path)

        findings = validate_docx(loaded, PACK_ID)
        rule_ids = {f.rule_id for f in findings}
        self.assertNotIn("PG-BODY-ALIGNMENT", rule_ids)

    def test_body_alignment_fix_survives_docx_roundtrip(self):
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
                            bold=False,
                            italic=False,
                            underline=None,
                            color_hex="000000",
                            highlight=None,
                        ),
                    ),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.74,
                    alignment="center",
                    is_page_break_before=False,
                ),
            ),
            metadata=DocumentMetadata(title="alignment loop"),
        )

        pre_findings = validate_docx(ir, PACK_ID)
        self.assertIn("PG-BODY-ALIGNMENT", {f.rule_id for f in pre_findings})

        fixed, skipped = apply_all_fixes(ir, pre_findings)
        self.assertEqual(skipped, [])
        self.assertEqual(fixed.paragraphs[0].alignment, "justify")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixed.docx"
            write_ir_to_docx(fixed, str(path))
            loaded = load_docx(path)

        post_findings = validate_docx(loaded, PACK_ID)
        post_rule_ids = {f.rule_id for f in post_findings}
        self.assertNotIn("PG-BODY-ALIGNMENT", post_rule_ids)
        self.assertEqual(post_findings, [])

    def test_body_vert_align_fix_survives_docx_roundtrip(self):
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
                            bold=False,
                            italic=False,
                            underline=None,
                            color_hex="000000",
                            highlight=None,
                            vert_align="superscript",
                        ),
                    ),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.74,
                    alignment="justify",
                    is_page_break_before=False,
                ),
            ),
            metadata=DocumentMetadata(title="vert align loop"),
        )

        pre_findings = validate_docx(ir, PACK_ID)
        self.assertIn("PG-BODY-NO-VERT-ALIGN", {f.rule_id for f in pre_findings})

        fixed, skipped = apply_all_fixes(ir, pre_findings)
        self.assertEqual(skipped, [])
        self.assertIsNone(fixed.paragraphs[0].runs[0].vert_align)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixed.docx"
            write_ir_to_docx(fixed, str(path))
            loaded = load_docx(path)

        post_findings = validate_docx(loaded, PACK_ID)
        post_rule_ids = {f.rule_id for f in post_findings}
        self.assertNotIn("PG-BODY-NO-VERT-ALIGN", post_rule_ids)
        self.assertEqual(post_findings, [])

    def test_body_bold_fix_cleans_all_runs_through_docx_roundtrip(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="正常加粗",
                    level=None,
                    runs=(
                        Run(
                            text="正常",
                            font_eastasia="宋体",
                            font_ascii="Times New Roman",
                            font_hansi="Times New Roman",
                            font_size_pt=12.0,
                            bold=False,
                            italic=False,
                            underline=None,
                            color_hex="000000",
                            highlight=None,
                        ),
                        Run(
                            text="加粗",
                            font_eastasia="宋体",
                            font_ascii="Times New Roman",
                            font_hansi="Times New Roman",
                            font_size_pt=12.0,
                            bold=True,
                            italic=False,
                            underline=None,
                            color_hex="000000",
                            highlight=None,
                        ),
                    ),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.74,
                    alignment="justify",
                    is_page_break_before=False,
                ),
            ),
            metadata=DocumentMetadata(title="multi run bold loop"),
        )

        pre_findings = validate_docx(ir, PACK_ID)
        self.assertIn("PG-BODY-NO-BOLD", {f.rule_id for f in pre_findings})

        fixed, skipped = apply_all_fixes(ir, pre_findings)
        self.assertEqual(skipped, [])
        self.assertEqual([r.bold for r in fixed.paragraphs[0].runs], [False, False])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixed.docx"
            write_ir_to_docx(fixed, str(path))
            loaded = load_docx(path)

        post_findings = validate_docx(loaded, PACK_ID)
        post_rule_ids = {f.rule_id for f in post_findings}
        self.assertNotIn("PG-BODY-NO-BOLD", post_rule_ids)
        self.assertEqual(post_findings, [])

    def test_body_decoration_fixes_clean_all_runs_through_docx_roundtrip(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="正常装饰",
                    level=None,
                    runs=(
                        Run(
                            text="正常",
                            font_eastasia="宋体",
                            font_ascii="Times New Roman",
                            font_hansi="Times New Roman",
                            font_size_pt=12.0,
                            bold=False,
                            italic=False,
                            underline=None,
                            color_hex="000000",
                            highlight=None,
                            vert_align=None,
                        ),
                        Run(
                            text="装饰",
                            font_eastasia="宋体",
                            font_ascii="Times New Roman",
                            font_hansi="Times New Roman",
                            font_size_pt=12.0,
                            bold=False,
                            italic=True,
                            underline="single",
                            color_hex="000000",
                            highlight="yellow",
                            vert_align="superscript",
                        ),
                    ),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.74,
                    alignment="justify",
                    is_page_break_before=False,
                ),
            ),
            metadata=DocumentMetadata(title="multi run decoration loop"),
        )

        pre_findings = validate_docx(ir, PACK_ID)
        pre_rule_ids = {f.rule_id for f in pre_findings}
        self.assertIn("PG-BODY-NO-ITALIC", pre_rule_ids)
        self.assertIn("PG-BODY-NO-UNDERLINE", pre_rule_ids)
        self.assertIn("PG-BODY-NO-HIGHLIGHT", pre_rule_ids)
        self.assertIn("PG-BODY-NO-VERT-ALIGN", pre_rule_ids)

        fixed, skipped = apply_all_fixes(ir, pre_findings)
        self.assertEqual(skipped, [])
        self.assertEqual([r.italic for r in fixed.paragraphs[0].runs], [False, False])
        self.assertEqual([r.underline for r in fixed.paragraphs[0].runs], [None, None])
        self.assertEqual([r.highlight for r in fixed.paragraphs[0].runs], [None, None])
        self.assertEqual([r.vert_align for r in fixed.paragraphs[0].runs], [None, None])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixed.docx"
            write_ir_to_docx(fixed, str(path))
            loaded = load_docx(path)

        post_findings = validate_docx(loaded, PACK_ID)
        self.assertEqual(post_findings, [])

    def test_body_font_fixes_clean_all_runs_through_docx_roundtrip(self):
        ir = Document(
            sections=(Section(),),
            paragraphs=(
                Paragraph(
                    style_id="Body Text",
                    text="正常错字",
                    level=None,
                    runs=(
                        Run(
                            text="正常",
                            font_eastasia="宋体",
                            font_ascii="Times New Roman",
                            font_hansi="Times New Roman",
                            font_size_pt=12.0,
                            bold=False,
                            italic=False,
                            underline=None,
                            color_hex="000000",
                            highlight=None,
                        ),
                        Run(
                            text="错字",
                            font_eastasia="黑体",
                            font_ascii="Arial",
                            font_hansi="Arial",
                            font_size_pt=14.0,
                            bold=False,
                            italic=False,
                            underline=None,
                            color_hex="FF0000",
                            highlight=None,
                        ),
                    ),
                    line_spacing=1.5,
                    space_before_pt=0.0,
                    space_after_pt=0.0,
                    first_line_indent_cm=0.74,
                    alignment="justify",
                    is_page_break_before=False,
                ),
            ),
            metadata=DocumentMetadata(title="multi run font loop"),
        )

        pre_findings = validate_docx(ir, PACK_ID)
        pre_rule_ids = {f.rule_id for f in pre_findings}
        self.assertIn("PG-BODY-FONT-SIZE", pre_rule_ids)
        self.assertIn("PG-BODY-FONT-EASTASIA", pre_rule_ids)
        self.assertIn("PG-BODY-FONT-ASCII", pre_rule_ids)
        self.assertIn("PG-BODY-FONT-HANSI", pre_rule_ids)
        self.assertIn("PG-BODY-COLOR-BLACK", pre_rule_ids)

        fixed, skipped = apply_all_fixes(ir, pre_findings)
        self.assertEqual(skipped, [])
        self.assertEqual([r.font_size_pt for r in fixed.paragraphs[0].runs], [12.0, 12.0])
        self.assertEqual([r.font_eastasia for r in fixed.paragraphs[0].runs], ["宋体", "宋体"])
        self.assertEqual([r.font_ascii for r in fixed.paragraphs[0].runs], ["Times New Roman", "Times New Roman"])
        self.assertEqual([r.font_hansi for r in fixed.paragraphs[0].runs], ["Times New Roman", "Times New Roman"])
        self.assertEqual([r.color_hex for r in fixed.paragraphs[0].runs], ["000000", "000000"])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixed.docx"
            write_ir_to_docx(fixed, str(path))
            loaded = load_docx(path)

        post_findings = validate_docx(loaded, PACK_ID)
        self.assertEqual(post_findings, [])


if __name__ == "__main__":
    unittest.main()
