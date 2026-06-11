"""Integration tests for Sprint 4 check HTML/SARIF output."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli


FIXTURE = Path("tests/fixtures/sample_paper.docx")


class TestCLICheckHTMLSARIF(unittest.TestCase):
    def test_check_html_stdout(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["check", str(FIXTURE), "--format", "html"])

        self.assertIn(result.exit_code, {0, 1})
        self.assertIn("<!DOCTYPE html>", result.output)
        self.assertIn("PaperGuard Report", result.output)

    def test_check_sarif_stdout(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["check", str(FIXTURE), "--format", "sarif"])

        self.assertIn(result.exit_code, {0, 1})
        payload = json.loads(result.output)
        self.assertEqual(payload["version"], "2.1.0")

    def test_check_html_output_file(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.html"
            result = runner.invoke(
                cli,
                ["check", str(FIXTURE), "--format", "html", "--output", str(out)],
            )

            self.assertIn(result.exit_code, {0, 1})
            self.assertTrue(out.exists())
            self.assertTrue(out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>"))
            self.assertIn("Report written", result.output)

    def test_check_sarif_output_file(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.sarif"
            result = runner.invoke(
                cli,
                ["check", str(FIXTURE), "--format", "sarif", "--output", str(out)],
            )

            self.assertIn(result.exit_code, {0, 1})
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["runs"][0]["tool"]["driver"]["name"], "PaperGuard")


if __name__ == "__main__":
    unittest.main()
