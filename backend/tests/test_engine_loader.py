"""Tests for app.core.engine.loader — Sprint 2 YAML pack loader.

12 RED tests cover: 加载有效 pack / 错误处理 / pydantic schema 校验 / Fix 点分 target 保持 / unicode。

设计约束：
- ``load_pack(pack_id, packs_dir=None) -> RulePack``：注入 packs_dir 便于测试
- 默认 packs_dir = ``app/core/rules/packs/``
- fail-fast：YAML 语法错 / schema 缺字段 / duplicate rule_id 立刻 raise
- Fix.target 必须保持点分形式（如 "paragraph.line_spacing"）兼容 LiveMutator
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine.loader import (
    Rule,
    RulePack,
    list_packs,
    load_pack,
)
from app.core.ir import Fix, Severity


# ---------------------------------------------------------------------------
# Helpers — 写临时 YAML pack 到 tmpdir
# ---------------------------------------------------------------------------

VALID_PACK_YAML = """\
id: TEST-PACK-001
name: "测试 pack 001"
version: 1.0.0
rules:
  - id: PG-BODY-LINE-SPACING
    severity: warning
    scope: "paragraphs[?style_id == 'Body Text']"
    when:
      "!=":
        - var: "line_spacing"
        - 1.5
    message: "正文行距应为 1.5"
    fix:
      action: set
      target: "paragraph.line_spacing"
      value: 1.5
  - id: PG-BODY-FONT-SIZE
    severity: warning
    scope: "paragraphs[?style_id == 'Body Text'].runs[0]"
    when:
      "!=":
        - var: "font_size_pt"
        - 12.0
    message: "正文字号应为 12pt"
    fix:
      action: set
      target: "run.font_size_pt"
      value: 12.0
"""


def _write_pack(tmpdir: Path, pack_id: str, yaml_text: str) -> Path:
    """把 YAML 文本写到 {tmpdir}/{pack_id}.yaml，返回路径。"""
    path = tmpdir / f"{pack_id}.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. load_valid_pack — 基本加载场景
# ---------------------------------------------------------------------------

class TestLoadValidPack(unittest.TestCase):
    def test_load_valid_pack_returns_rulepack(self):
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "TEST-PACK-001", VALID_PACK_YAML)
            pack = load_pack("TEST-PACK-001", packs_dir=tmpdir)
            self.assertIsInstance(pack, RulePack)
            self.assertEqual(pack.id, "TEST-PACK-001")
            self.assertEqual(pack.name, "测试 pack 001")
            self.assertEqual(pack.version, "1.0.0")
            self.assertEqual(len(pack.rules), 2)
            self.assertIsInstance(pack.rules[0], Rule)

    def test_rule_severity_parsed_as_enum(self):
        """Rule.severity 必须是 Severity 枚举（不是字符串）。"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "TEST-PACK-001", VALID_PACK_YAML)
            pack = load_pack("TEST-PACK-001", packs_dir=tmpdir)
            self.assertEqual(pack.rules[0].severity, Severity.WARNING)


# ---------------------------------------------------------------------------
# 2. missing_pack
# ---------------------------------------------------------------------------

class TestMissingPack(unittest.TestCase):
    def test_missing_pack_raises_filenotfound(self):
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                load_pack("NONEXISTENT-PACK", packs_dir=tmpdir)


# ---------------------------------------------------------------------------
# 3. invalid_yaml_syntax
# ---------------------------------------------------------------------------

class TestInvalidYamlSyntax(unittest.TestCase):
    def test_invalid_yaml_syntax_raises(self):
        import yaml
        broken_yaml = "id: test\n  bad: indent\n - oops"
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "BROKEN", broken_yaml)
            with self.assertRaises((yaml.YAMLError, ValueError)):
                load_pack("BROKEN", packs_dir=tmpdir)


# ---------------------------------------------------------------------------
# 4. duplicate_rule_id
# ---------------------------------------------------------------------------

class TestDuplicateRuleId(unittest.TestCase):
    def test_duplicate_rule_id_raises(self):
        dup_yaml = """\
id: DUP-PACK
name: dup
version: 1.0.0
rules:
  - id: PG-X
    severity: warning
    scope: "paragraphs[*]"
    when: {"==": [1, 1]}
    message: "x"
  - id: PG-X
    severity: error
    scope: "paragraphs[*]"
    when: {"==": [2, 2]}
    message: "y"
"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "DUP-PACK", dup_yaml)
            with self.assertRaises(ValueError) as cm:
                load_pack("DUP-PACK", packs_dir=tmpdir)
            self.assertIn("PG-X", str(cm.exception))


# ---------------------------------------------------------------------------
# 5. missing_required_field
# ---------------------------------------------------------------------------

class TestMissingRequiredField(unittest.TestCase):
    def test_rule_missing_id_raises(self):
        bad_yaml = """\
id: BAD-PACK
name: bad
version: 1.0.0
rules:
  - severity: warning
    scope: "paragraphs[*]"
    when: {"==": [1, 1]}
    message: "no id"
"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "BAD-PACK", bad_yaml)
            with self.assertRaises(Exception):  # pydantic.ValidationError 或 ValueError
                load_pack("BAD-PACK", packs_dir=tmpdir)

    def test_pack_missing_id_raises(self):
        no_pack_id = """\
name: no id
version: 1.0.0
rules: []
"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "NO-PACK-ID", no_pack_id)
            with self.assertRaises(Exception):
                load_pack("NO-PACK-ID", packs_dir=tmpdir)


# ---------------------------------------------------------------------------
# 6. invalid_severity
# ---------------------------------------------------------------------------

class TestInvalidSeverity(unittest.TestCase):
    def test_invalid_severity_raises(self):
        bad_sev = """\
id: BAD-SEV
name: bad
version: 1.0.0
rules:
  - id: PG-X
    severity: critical
    scope: "paragraphs[*]"
    when: {"==": [1, 1]}
    message: "x"
"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "BAD-SEV", bad_sev)
            with self.assertRaises(Exception):
                load_pack("BAD-SEV", packs_dir=tmpdir)


# ---------------------------------------------------------------------------
# 7. unicode_pack
# ---------------------------------------------------------------------------

class TestUnicodePack(unittest.TestCase):
    def test_unicode_message_chinese_preserved(self):
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "TEST-PACK-001", VALID_PACK_YAML)
            pack = load_pack("TEST-PACK-001", packs_dir=tmpdir)
            self.assertEqual(pack.rules[0].message, "正文行距应为 1.5")


# ---------------------------------------------------------------------------
# 8. list_packs
# ---------------------------------------------------------------------------

class TestListPacks(unittest.TestCase):
    def test_list_packs_returns_ids(self):
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "PACK-A", VALID_PACK_YAML.replace("TEST-PACK-001", "PACK-A"))
            _write_pack(tmpdir, "PACK-B", VALID_PACK_YAML.replace("TEST-PACK-001", "PACK-B"))
            packs = list_packs(packs_dir=tmpdir)
            self.assertIn("PACK-A", packs)
            self.assertIn("PACK-B", packs)
            self.assertEqual(len(packs), 2)


# ---------------------------------------------------------------------------
# 9. fix.target dotted form preserved
# ---------------------------------------------------------------------------

class TestFixTargetDottedForm(unittest.TestCase):
    def test_fix_target_dotted_preserved(self):
        """Fix.target 必须保持点分形式（LiveMutator 已硬编码）。"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "TEST-PACK-001", VALID_PACK_YAML)
            pack = load_pack("TEST-PACK-001", packs_dir=tmpdir)
            r1 = pack.rules[0]
            self.assertIsInstance(r1.fix, Fix)
            self.assertEqual(r1.fix.target, "paragraph.line_spacing")
            self.assertEqual(r1.fix.action, "set")
            self.assertEqual(r1.fix.value, 1.5)
            r2 = pack.rules[1]
            self.assertEqual(r2.fix.target, "run.font_size_pt")


# ---------------------------------------------------------------------------
# 10. empty rules
# ---------------------------------------------------------------------------

class TestEmptyRules(unittest.TestCase):
    def test_empty_rules_list_allowed(self):
        empty = """\
id: EMPTY-PACK
name: empty
version: 1.0.0
rules: []
"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "EMPTY-PACK", empty)
            pack = load_pack("EMPTY-PACK", packs_dir=tmpdir)
            self.assertEqual(len(pack.rules), 0)


# ---------------------------------------------------------------------------
# 11. when expression preserved as dict
# ---------------------------------------------------------------------------

class TestWhenExpressionPreserved(unittest.TestCase):
    def test_when_expression_kept_as_dict(self):
        """``when`` 是 JSONLogic dict，必须原样保留（evaluator 直接调 jsonLogic(dict, data)）。"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "TEST-PACK-001", VALID_PACK_YAML)
            pack = load_pack("TEST-PACK-001", packs_dir=tmpdir)
            when = pack.rules[0].when
            self.assertIsInstance(when, dict)
            self.assertIn("!=", when)
            args = when["!="]
            self.assertEqual(args[0], {"var": "line_spacing"})
            self.assertEqual(args[1], 1.5)


# ---------------------------------------------------------------------------
# 12. rule without fix
# ---------------------------------------------------------------------------

class TestRuleWithoutFix(unittest.TestCase):
    def test_rule_without_fix_field(self):
        """fix 字段可选 — 某些规则只报告不修复。"""
        no_fix = """\
id: NO-FIX-PACK
name: no fix
version: 1.0.0
rules:
  - id: PG-INFO-ONLY
    severity: info
    scope: "paragraphs[*]"
    when: {"==": [1, 1]}
    message: "informational only"
"""
        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            _write_pack(tmpdir, "NO-FIX-PACK", no_fix)
            pack = load_pack("NO-FIX-PACK", packs_dir=tmpdir)
            self.assertEqual(len(pack.rules), 1)
            self.assertIsNone(pack.rules[0].fix)


if __name__ == "__main__":
    unittest.main()
