"""Tests for app.core.extract.requirement — Sprint 2.5 Requirement dataclass.

8 RED tests cover：
- frozen dataclass（不可变）
- 7 category 常量（style / section / paragraph-example / table / numbering / explicit-instruction / document）
- id 格式（R0001 4 位序号）
- 所有字段类型
- auto_applicable 默认 True
- equality / hashability
- REQUIREMENT_CATEGORIES 常量导出

设计契约（参考 docs/sprints/sprint-2.5-plan.md §3.1 requirement.py）：
- @dataclass(frozen=True)
- 8 字段：id / category / target / property / expected / source_part / evidence / auto_applicable
- 与参考项目 add_item() (extract_requirements.py:131-143) 字段对齐
"""

from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.extract.requirement import (
    REQUIREMENT_CATEGORIES,
    Requirement,
)


class TestRequirementFrozen(unittest.TestCase):
    def test_requirement_is_frozen(self):
        req = Requirement(
            id="R0001",
            category="style",
            target="style:Normal",
            property="font.eastAsia",
            expected="宋体",
            source_part="word/styles.xml",
            evidence="Normal style",
        )
        with self.assertRaises(FrozenInstanceError):
            req.id = "R9999"  # type: ignore[misc]


class TestRequirementCategories(unittest.TestCase):
    def test_categories_constant_has_7_items(self):
        self.assertEqual(len(REQUIREMENT_CATEGORIES), 7)

    def test_categories_match_design_proposal(self):
        expected = {
            "style", "section", "paragraph-example", "table",
            "numbering", "explicit-instruction", "document",
        }
        self.assertEqual(set(REQUIREMENT_CATEGORIES), expected)


class TestRequirementFields(unittest.TestCase):
    def test_requirement_creates_with_all_fields(self):
        req = Requirement(
            id="R0001",
            category="style",
            target="style:Title",
            property="font.size_half_points",
            expected="24",
            source_part="word/styles.xml",
            evidence="Title style font",
            auto_applicable=True,
        )
        self.assertEqual(req.id, "R0001")
        self.assertEqual(req.category, "style")
        self.assertEqual(req.target, "style:Title")
        self.assertEqual(req.property, "font.size_half_points")
        self.assertEqual(req.expected, "24")
        self.assertEqual(req.source_part, "word/styles.xml")
        self.assertEqual(req.evidence, "Title style font")
        self.assertTrue(req.auto_applicable)

    def test_auto_applicable_defaults_to_true(self):
        req = Requirement(
            id="R0001", category="style", target="style:Normal",
            property="font.eastAsia", expected="宋体",
            source_part="word/styles.xml", evidence="...",
        )
        self.assertTrue(req.auto_applicable)


class TestRequirementId(unittest.TestCase):
    def test_id_4_digit_format(self):
        """ID 必须 R0001 4 位序号格式（与参考项目 f'R{len:04d}' 一致）。"""
        # 这里不强制 dataclass 验证 id 格式（dataclass 不该有副作用），
        # 但调用方（extractor）必须用 R{:04d} 生成。本测试验证 ID 可以正常存储 4 位格式
        req = Requirement(
            id="R0001", category="style", target="style:Normal",
            property="font.eastAsia", expected="宋体",
            source_part="word/styles.xml", evidence="...",
        )
        self.assertEqual(req.id, "R0001")
        # 验证 9999 边界
        req_max = Requirement(
            id="R9999", category="style", target="style:Normal",
            property="font.eastAsia", expected="宋体",
            source_part="word/styles.xml", evidence="...",
        )
        self.assertEqual(req_max.id, "R9999")


class TestRequirementEquality(unittest.TestCase):
    def test_two_requirements_with_same_fields_are_equal(self):
        kwargs = dict(
            id="R0001", category="style", target="style:Normal",
            property="font.eastAsia", expected="宋体",
            source_part="word/styles.xml", evidence="...",
        )
        a = Requirement(**kwargs)
        b = Requirement(**kwargs)
        self.assertEqual(a, b)
        # frozen dataclass 默认可哈希
        self.assertEqual(hash(a), hash(b))


if __name__ == "__main__":
    unittest.main()
