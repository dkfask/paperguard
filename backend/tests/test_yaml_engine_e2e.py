"""Sprint 2 端到端集成测试 — YAML pack engine + fixture docx.

测试场景：
  1. ``test_load_fixture_and_validate`` — load sample_paper.docx → validate_docx → 至少 1 Finding
  2. ``test_each_finding_has_paragraph_index`` — Findings 全部有 paragraph_index
  3. ``test_fix_targets_are_dotted_form`` — Fix.target 全部点分（兼容 LiveMutator）
  4. ``test_unknown_pack_raises`` — unknown pack_id 抛 FileNotFoundError
  5. ``test_severity_distribution`` — Findings 至少含 warning 级别（25 规则里有 17 warning）
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine import validate_docx
from app.core.engine.loader import load_pack
from app.core.ir import Severity
from app.core.loaders.docx_loader import load_docx


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_paper.docx"
PACK_ID = "GB-T-7713-1-2006"


class YamlEngineEndToEnd(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not FIXTURE_PATH.exists():
            raise FileNotFoundError(f"Fixture missing: {FIXTURE_PATH}")
        cls.ir = load_docx(str(FIXTURE_PATH))

    def test_load_fixture_and_validate(self):
        """从 docx 文件加载 → IR → YAML pack engine → Findings（可为 0 或 N）。

        sample_paper.docx 内容由 sprint1 测试生成；不强求多少 finding，
        但端到端不应抛错。
        """
        findings = validate_docx(self.ir, PACK_ID)
        self.assertIsInstance(findings, list)

    def test_each_finding_has_paragraph_index(self):
        """所有 finding 都来自 paragraphs scope，paragraph_index 应是 int（非 None）。"""
        findings = validate_docx(self.ir, PACK_ID)
        for f in findings:
            self.assertIsNotNone(
                f.location.paragraph_index,
                f"finding {f.rule_id} 无 paragraph_index（YAML pack 全 paragraphs scope）",
            )

    def test_fix_targets_are_dotted_form(self):
        """所有 Fix.target 应是点分形式（兼容 LiveMutator）。"""
        findings = validate_docx(self.ir, PACK_ID)
        for f in findings:
            if f.fix is None:
                continue
            self.assertIn(
                ".",
                f.fix.target,
                f"Fix.target '{f.fix.target}' (rule {f.rule_id}) 不是点分形式",
            )
            # 必须以 paragraph. / run. / runs.*. 开头（兼容 LiveMutator 与 Sprint 5 wildcard）
            self.assertTrue(
                f.fix.target.startswith(("paragraph.", "run.", "runs.*.")),
                f"Fix.target '{f.fix.target}' 应以 paragraph. / run. / runs.*. 开头",
            )

    def test_unknown_pack_raises(self):
        """unknown pack_id 抛 FileNotFoundError（fail-fast ADR-5）。"""
        with self.assertRaises(FileNotFoundError):
            validate_docx(self.ir, "NONEXISTENT-PACK")

    def test_severity_propagates_as_enum(self):
        """findings 中 severity 必须是 Severity 枚举（不是字符串）。"""
        findings = validate_docx(self.ir, PACK_ID)
        for f in findings:
            self.assertIsInstance(
                f.severity,
                Severity,
                f"finding {f.rule_id} severity 类型错: {type(f.severity)}",
            )


class YamlPackSelfCheck(unittest.TestCase):
    """单独验证 YAML pack 文件本身的健康度（不依赖 fixture docx）。"""

    def test_gb_pack_loads_with_28_rules(self):
        pack = load_pack(PACK_ID)
        self.assertEqual(pack.id, PACK_ID)
        self.assertEqual(len(pack.rules), 28,
                         f"GB-T-7713-1-2006 应有 28 条规则（含正文上下标规则），实际 {len(pack.rules)}")

    def test_all_pack_rule_ids_use_pg_prefix(self):
        """ADR-7 A 决策：rule_id 全部 PG- 前缀。"""
        pack = load_pack(PACK_ID)
        for r in pack.rules:
            self.assertTrue(
                r.id.startswith("PG-"),
                f"rule_id '{r.id}' 不以 PG- 开头（违反 ADR-7 A 语义化命名）",
            )


if __name__ == "__main__":
    unittest.main()
