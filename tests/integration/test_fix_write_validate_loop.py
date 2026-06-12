"""Integration tests for fix -> write docx -> load -> validate loops."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.engine import validate_docx
from app.core.ir import Document, DocumentMetadata, Paragraph, Run, Section
from app.core.loaders.docx_loader import load_docx
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes


PACK_ID = "GB-T-7713-1-2006"


class TestFixWriteValidateLoop(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
