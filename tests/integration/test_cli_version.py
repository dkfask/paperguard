"""Integration tests for paperguard version."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli


class TestCLIVersion(unittest.TestCase):
    def test_top_level_version_still_works(self):
        result = CliRunner().invoke(cli, ["--version"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("0.1.0", result.output)

    def test_version_subcommand_reports_local_version(self):
        result = CliRunner().invoke(cli, ["version"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("PaperGuard", result.output)
        self.assertIn("0.1.0", result.output)

    def test_version_help_includes_check_update(self):
        result = CliRunner().invoke(cli, ["version", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--check-update", result.output)


if __name__ == "__main__":
    unittest.main()
