"""Tests for app.cli.init — Sprint 3 commit #15.

2 integration tests for `paperguard init` Click subcommand.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli


class TestCLIInit(unittest.TestCase):

    def test_init_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--school", result.output)
        self.assertIn("--out", result.output)

    def test_init_with_run_argv(self):
        """engine-level: subprocess 跑 init --out /tmp/skeleton.yaml"""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "skel.yaml"
            runner = CliRunner()
            result = runner.invoke(cli, ["init", "--school", "TestUniv",
                                         "--out", str(out)])
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("TestUniv", content)
            self.assertIn("rules", content)


if __name__ == "__main__":
    unittest.main()
