"""Tests for app.cli.extract — Sprint 3 commit #15.

2 integration tests for `paperguard extract` Click subcommand.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli
from app.core.extract import extract_template
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "templates" / "minimal.docx"


class TestCLIExtract(unittest.TestCase):

    def test_extract_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["extract", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--out", result.output)
        self.assertIn("--json-out", result.output)

    def test_extract_returns_requirements_via_engine(self):
        """engine-level: extract_template on fixture yields Requirements"""
        if not FIXTURE.exists():
            self.skipTest(f"Fixture missing: {FIXTURE}")
        reqs = extract_template(FIXTURE)
        self.assertGreater(len(reqs), 0)


if __name__ == "__main__":
    unittest.main()
