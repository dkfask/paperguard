"""Tests for app.cli.validate_pack — Sprint 3 commit #15.

2 integration tests for `paperguard validate-pack` Click subcommand.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from click.testing import CliRunner

from app.cli import cli


VALID_YAML = """\
id: TEST-VALID-PACK
name: "测试"
version: 1.0.0
rules:
  - id: PG-TEST
    severity: warning
    scope: "paragraphs[*]"
    when:
      "!=":
        - var: "line_spacing"
        - 1.5
    message: test
"""

INVALID_YAML = """\
id: TEST-INVALID
name: bad
version: 1.0.0
rules:
  - id: PG-BAD
    severity: not_a_severity
    scope: 123
"""


class TestCLIValidatePack(unittest.TestCase):

    def test_validate_pack_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["validate-pack", "--help"])
        self.assertEqual(result.exit_code, 0)

    def test_validate_pack_valid_yaml_exits_0(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "valid.yaml"
            p.write_text(VALID_YAML, encoding="utf-8")
            runner = CliRunner()
            result = runner.invoke(cli, ["validate-pack", str(p)])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("Valid", result.output)

    def test_validate_pack_invalid_yaml_exits_2(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "invalid.yaml"
            p.write_text(INVALID_YAML, encoding="utf-8")
            runner = CliRunner()
            result = runner.invoke(cli, ["validate-pack", str(p)])
            self.assertEqual(result.exit_code, 2)


if __name__ == "__main__":
    unittest.main()
