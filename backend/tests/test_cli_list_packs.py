"""Integration tests for paperguard list-packs."""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli


class TestCLIListPacks(unittest.TestCase):
    def test_list_packs_text(self):
        result = CliRunner().invoke(cli, ["list-packs"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("GB-T-7713-1-2006", result.output)
        self.assertIn("SIM-UNIV-generic", result.output)

    def test_list_packs_json(self):
        result = CliRunner().invoke(cli, ["list-packs", "--format", "json"])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertIsInstance(payload, list)
        self.assertIn("id", payload[0])
        self.assertIn("path", payload[0])


if __name__ == "__main__":
    unittest.main()
