"""Integration tests for Sprint 4 fix -> check verification loop."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli


FIXTURE = Path("tests/fixtures/sample_paper.docx")


class TestFixLoop(unittest.TestCase):
    def test_fix_help_prefers_skip_verify_and_keeps_no_verify_hidden(self):
        result = CliRunner().invoke(cli, ["fix", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--skip-verify", result.output)
        self.assertNotIn("--no-verify", result.output)

    def test_fix_skip_verify_does_not_write_changes_md(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "fixed.docx"
            result = runner.invoke(
                cli,
                ["fix", str(FIXTURE), "--output", str(out), "--skip-verify"],
            )

            self.assertIn(result.exit_code, {0, 1})
            self.assertTrue(out.exists())
            self.assertFalse((Path(tmp) / "changes.md").exists())

    def test_fix_default_writes_changes_md_with_summary(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "fixed.docx"
            result = runner.invoke(cli, ["fix", str(FIXTURE), "--output", str(out)])

            self.assertIn(result.exit_code, {0, 1})
            changes = Path(tmp) / "changes.md"
            self.assertTrue(changes.exists())
            report = changes.read_text(encoding="utf-8")
            self.assertIn("# PaperGuard Fix Report", report)
            self.assertIn("- Source:", report)
            self.assertIn("- Output:", report)
            self.assertIn("- Pack:", report)
            self.assertIn("| Metric | Count |", report)
            self.assertIn("## Auto-fixed", report)
            self.assertIn("## Remaining", report)


if __name__ == "__main__":
    unittest.main()
