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


if __name__ == "__main__":
    unittest.main()
