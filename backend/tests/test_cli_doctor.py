"""Integration tests for paperguard doctor."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli


class TestCLIDoctor(unittest.TestCase):
    def test_doctor_reports_core_checks(self):
        result = CliRunner().invoke(cli, ["doctor"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Python", result.output)
        self.assertIn("Dependencies", result.output)
        self.assertIn("Rule packs", result.output)
        self.assertIn("PyQt5", result.output)
        self.assertIn("Network", result.output)

    def test_doctor_does_not_check_network_by_default(self):
        result = CliRunner().invoke(cli, ["doctor"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Network: skipped", result.output)


if __name__ == "__main__":
    unittest.main()
