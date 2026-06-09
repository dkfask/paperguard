"""Tests for app.core.extract.to_pack — Sprint 2.5 requirements_to_pack.

12 RED tests 覆盖：
- 基本映射：1 个 style → 1 Rule
- B1 follow-up 规避：paragraph.spacing.before/after 拆两条
- section / paragraph-example / table / numbering / document 类别映射
- auto_applicable=False → 不生成 Rule
- fix.target 强制点分形式（兼容 LiveMutator）
- 半磅 → pt 转换（size_half_points 24 → 12.0）
- 重复 property+expected 去重
- explicit-instruction 不生成 Rule
- id 命名 PG-EXTRACT-* 前缀
- severity 全 warning
- version 字段
- 生成 RulePack 可被 Sprint 2 loader 验证（schema 兼容）
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine.loader import RulePack
from app.core.extract.requirement import Requirement
from app.core.extract.to_pack import requirements_to_pack


def _req(
    id: str = "R0001",
    category: str = "style",
    target: str = "style:Normal",
    property: str = "font.eastAsia",
    expected: str = "宋体",
    source_part: str = "word/styles.xml",
    evidence: str = "<w:style> font.eastAsia=宋体",
    auto_applicable: bool = True,
    fix=None,
) -> Requirement:
    return Requirement(
        id=id,
        category=category,
        target=target,
        property=property,
        expected=expected,
        source_part=source_part,
        evidence=evidence,
        auto_applicable=auto_applicable,
        fix=fix,
    )


class TestToPackBasic(unittest.TestCase):
    def test_returns_rulepack_instance(self):
        reqs = [_req()]
        pack = requirements_to_pack(reqs, "TPL-001", "测试模板", version="1.0.0")
        self.assertIsInstance(pack, RulePack)

    def test_pack_id_name_version_preserved(self):
        pack = requirements_to_pack([_req()], "TPL-X", "我的模板", version="0.5.0")
        self.assertEqual(pack.id, "TPL-X")
        self.assertEqual(pack.name, "我的模板")
        self.assertEqual(pack.version, "0.5.0")

    def test_empty_reqs_yields_zero_rules(self):
        pack = requirements_to_pack([], "EMPTY", "空")
        self.assertEqual(len(pack.rules), 0)

    def test_one_style_yields_one_rule(self):
        reqs = [_req(category="style", target="style:Normal", property="font.eastAsia", expected="宋体")]
        pack = requirements_to_pack(reqs, "T1", "t")
        self.assertEqual(len(pack.rules), 1)
        rule = pack.rules[0]
        self.assertEqual(rule.severity.value, "warning")
        self.assertIn("Normal", rule.scope)
        self.assertIn("宋体", str(rule.message))


class TestB1FollowupAvoidance(unittest.TestCase):
    """**关键** — Sprint 2 follow-up B1 规避测试."""

    def test_paragraph_spacing_before_after_split_into_two_rules(self):
        """当模板同时有 space.before + space.after，拆成两条独立 Rule（避免 B1）"""
        reqs = [
            _req(id="R1", category="section", target="section:0",
                 property="paragraph.spacing.before", expected="0"),
            _req(id="R2", category="section", target="section:0",
                 property="paragraph.spacing.after", expected="0"),
        ]
        pack = requirements_to_pack(reqs, "B1-TEST", "b1 test")
        # 2 个 input → 至少 2 个 output（每个独立 fix）
        self.assertGreaterEqual(len(pack.rules), 2)
        # 两个 rule 的 fix.target 应该不同
        targets = {r.fix.target for r in pack.rules if r.fix}
        self.assertIn("paragraph.space_before_pt", targets)
        self.assertIn("paragraph.space_after_pt", targets)

    def test_paragraph_spacing_before_alone_yields_one_rule(self):
        """仅有 before → 1 个 rule（不强制拆）"""
        reqs = [
            _req(category="section", target="section:0",
                 property="paragraph.spacing.before", expected="0"),
        ]
        pack = requirements_to_pack(reqs, "B1-S", "b1 s")
        before_rules = [r for r in pack.rules if r.fix and r.fix.target == "paragraph.space_before_pt"]
        self.assertEqual(len(before_rules), 1)


class TestCategoryMapping(unittest.TestCase):
    def test_section_category_maps_to_sections_scope(self):
        reqs = [_req(category="section", target="section:0",
                     property="section.margin.top", expected="1440")]
        pack = requirements_to_pack(reqs, "S", "s")
        rule = pack.rules[0]
        self.assertIn("sections", rule.scope)
        self.assertEqual(rule.fix.target, "section.margin_top_cm")

    def test_paragraph_example_category_maps_to_paragraphs(self):
        reqs = [_req(category="paragraph-example", target="paragraph-example:word/document.xml:0",
                     property="font.eastAsia", expected="宋体")]
        pack = requirements_to_pack(reqs, "P", "p")
        rule = pack.rules[0]
        self.assertIn("paragraphs", rule.scope)

    def test_table_category_yields_rule(self):
        reqs = [_req(category="table", target="table:word/document.xml:0",
                     property="table.width", expected="9000")]
        pack = requirements_to_pack(reqs, "T", "t")
        self.assertGreaterEqual(len(pack.rules), 1)


class TestFiltering(unittest.TestCase):
    def test_explicit_instruction_not_applicable_no_rule(self):
        """explicit-instruction (auto_applicable=False) 不生成 Rule"""
        reqs = [_req(category="explicit-instruction", auto_applicable=False,
                     property="paragraph.instruction_text", expected="行距应为 1.5")]
        pack = requirements_to_pack(reqs, "E", "e")
        self.assertEqual(len(pack.rules), 0)

    def test_dedup_same_property_expected(self):
        """相同 property+expected 应去重为 1 条规则"""
        reqs = [
            _req(id="R1", property="font.eastAsia", expected="宋体"),
            _req(id="R2", property="font.eastAsia", expected="宋体"),
        ]
        pack = requirements_to_pack(reqs, "D", "d")
        self.assertEqual(len(pack.rules), 1)


class TestSpecial(unittest.TestCase):
    def test_rule_id_prefix_pg_extract(self):
        """生成 rule.id 用 PG-EXTRACT- 前缀（避免与已有 R001-R030 冲突）"""
        reqs = [_req()]
        pack = requirements_to_pack(reqs, "P", "p")
        for rule in pack.rules:
            self.assertTrue(rule.id.startswith("PG-EXTRACT-"),
                            f"id must start with PG-EXTRACT-: {rule.id!r}")

    def test_size_half_points_divided_by_2(self):
        """font.size_half_points 24 → run.font_size_pt = 12.0 (pt 转换)"""
        reqs = [_req(property="font.size_half_points", expected="24")]
        pack = requirements_to_pack(reqs, "S", "s")
        rule = pack.rules[0]
        self.assertEqual(rule.fix.value, 12.0)


if __name__ == "__main__":
    unittest.main()
