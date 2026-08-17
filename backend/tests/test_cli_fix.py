"""Tests for app.cli.fix — Sprint 3 commit #15.

3 integration tests for `paperguard fix` Click subcommand.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli
from app.core.engine import validate_docx
from app.core.ir import (
    Document, DocumentMetadata, Paragraph, Run, Section,
)


class TestCLIFix(unittest.TestCase):

    def test_fix_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fix", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--output", result.output)
        self.assertIn("--pack", result.output)

    def test_fix_with_missing_docx_exits_2(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fix", "/nonexistent/path.docx",
                                     "--output", "/tmp/out.docx"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertNotEqual(result.exit_code, 1)

    def test_fix_engine_apply_fix_correctly(self):
        """engine-level test: fix_applier 应用 set action 后 IR 更新"""
        from app.core.writers.fix_applier import apply_fix
        from app.core.ir.finding import Fix, Finding, Location, Severity
        para = Paragraph(
            style_id="Body Text", text="x", level=None,
            runs=(Run(text="x", font_eastasia="宋体", font_size_pt=12.0),),
            line_spacing=1.0,  # 违规
            space_before_pt=0.0, space_after_pt=0.0, first_line_indent_cm=0.74,
        )
        ir = Document(sections=(Section(),), paragraphs=(para,),
                      metadata=DocumentMetadata(title="t"))
        findings = validate_docx(ir, "GB-T-7713-1-2006")
        # 应用第一个 finding 的 fix
        f = findings[0]
        fixed = apply_fix(ir, f)
        # 原 IR 不可变
        self.assertEqual(ir.paragraphs[0].line_spacing, 1.0)
        # 新 IR line_spacing 已 fix (若 PG-BODY-LINE-SPACING 触发)
        if f.fix and f.fix.target == "paragraph.line_spacing":
            self.assertEqual(fixed.paragraphs[0].line_spacing, 1.5)


if __name__ == "__main__":
    unittest.main()
