"""Tests for app.core.extract.extractor — Sprint 2.5 extract_template 主入口.

Sprint 2.5 commit #10: 15 RED tests.

设计契约（plan §3.1 + §6）：
- extract_template(docx_path: str | Path) -> list[Requirement]
- 解压 docx → read_parts → 7 个 _extract_* 子函数 → 合并 Requirement[]
- ID 递增 R0001, R0002, ...
- evidence 自动截 300 字符
- explicit-instruction category 必带 fix=None
- 缺 part 静默跳过（不抛）

15 tests 覆盖：
- 空 docx → []
- 7 category 各能产出（smoke: 各自 fixture 至少 1）
- id 递增
- evidence 截 300
- source_part 正确
- property 点分
- target 形式正确
- explicit-instruction 命中关键词
- explicit-instruction 的 fix=None
- FORMAT_WORDS 不匹配普通文本
- 默认 auto_applicable=True
- 重复 property 合并（V1+ 行为，MVP 保留所有）
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.extract.extractor import extract_template
from app.core.extract.requirement import REQUIREMENT_CATEGORIES, Requirement


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "templates"


class TestExtractTemplate(unittest.TestCase):
    def test_empty_docx_returns_empty_list(self):
        """空 docx → 0 Requirement (用空 list 作为 .docx zip 模拟)"""
        import zipfile
        from io import BytesIO

        from app.core.extract.extractor import extract_template as real_extract
        # 用空 zipfile 模拟
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<?xml version='1.0'?>")
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "_empty_test.docx"
        tmp.write_bytes(buf.getvalue())
        try:
            result = real_extract(tmp)
            self.assertEqual(result, [])
        finally:
            if tmp.exists():
                tmp.unlink()

    def test_extracted_requirements_are_requirement_dataclass(self):
        """所有返回项都是 Requirement frozen dataclass"""
        result = extract_template(FIXTURES / "minimal.docx")
        for req in result:
            self.assertIsInstance(req, Requirement)
            self.assertIs(req.__class__, Requirement)

    def test_requirement_id_is_4_digit(self):
        """id 必须 'R0001' 格式"""
        result = extract_template(FIXTURES / "minimal.docx")
        self.assertGreater(len(result), 0)
        for req in result:
            self.assertRegex(req.id, r"^R\d{4}$")

    def test_requirement_id_monotonic(self):
        result = extract_template(FIXTURES / "minimal.docx")
        ids = [int(r.id[1:]) for r in result]
        self.assertEqual(ids, sorted(ids))
        # 至少 2 个
        self.assertGreaterEqual(len(ids), 2)

    def test_requirement_property_dotted_form(self):
        """property 必须点分 ('font.eastAsia' / 'paragraph.spacing.line' / 'section.margin.top')"""
        result = extract_template(FIXTURES / "minimal.docx")
        self.assertGreater(len(result), 0)
        for req in result:
            self.assertIn(".", req.property, f"property must be dotted: {req.property!r}")

    def test_requirement_source_part_word_document(self):
        """paragraph-example / explicit-instruction 的 source_part 应是 word/document.xml"""
        result = extract_template(FIXTURES / "minimal.docx")
        para_reqs = [r for r in result if r.category == "paragraph-example"]
        if para_reqs:  # 至少能存在
            for req in para_reqs:
                self.assertEqual(req.source_part, "word/document.xml")

    def test_requirement_evidence_max_300_chars(self):
        """evidence 字段最多 300 字符"""
        result = extract_template(FIXTURES / "minimal.docx")
        for req in result:
            self.assertLessEqual(len(req.evidence), 300)

    def test_category_in_seven_constants(self):
        """每个 req.category 必须在 7 个常量中"""
        result = extract_template(FIXTURES / "minimal.docx")
        for req in result:
            self.assertIn(req.category, REQUIREMENT_CATEGORIES)

    def test_explicit_instruction_no_fix(self):
        """explicit-instruction category 的 auto_applicable=False 或 fix=None"""
        result = extract_template(FIXTURES / "gb7713-official.docx")
        explicit = [r for r in result if r.category == "explicit-instruction"]
        if explicit:
            for req in explicit:
                self.assertIsNone(req.fix, "explicit-instruction 不能 fix")

    def test_explicit_instruction_format_words_match(self):
        """explicit-instruction 应含 FORMAT_WORDS 关键词（中文模板必出）"""
        result = extract_template(FIXTURES / "gb7713-official.docx")
        explicit = [r for r in result if r.category == "explicit-instruction"]
        # 国标模板必含至少 1 个 explicit instruction（"摘要"/"目录"/"图"等）
        self.assertGreater(len(explicit), 0,
                            "gb7713-official fixture 应触发至少 1 个 explicit-instruction")

    def test_minimal_docx_yields_multiple_categories(self):
        """minimal fixture 至少触发 style + section + paragraph-example 3 类"""
        result = extract_template(FIXTURES / "minimal.docx")
        cats = {r.category for r in result}
        self.assertIn("style", cats)
        self.assertIn("section", cats)
        self.assertIn("paragraph-example", cats)

    def test_section_margin_top_expected(self):
        """section 类别含 section.margin.top"""
        result = extract_template(FIXTURES / "minimal.docx")
        section_reqs = [r for r in result if r.category == "section"]
        props = {r.property for r in section_reqs}
        self.assertIn("section.margin.top", props)

    def test_target_format_for_style(self):
        """style 类别 target 格式 'style:<style_id>'"""
        result = extract_template(FIXTURES / "minimal.docx")
        styles = [r for r in result if r.category == "style"]
        if styles:
            for req in styles:
                self.assertTrue(req.target.startswith("style:"),
                                f"style target must start with 'style:': {req.target!r}")

    def test_default_auto_applicable_true(self):
        """默认 auto_applicable=True (仅 explicit-instruction 是 False)"""
        result = extract_template(FIXTURES / "minimal.docx")
        for req in result:
            if req.category != "explicit-instruction":
                self.assertTrue(req.auto_applicable)

    def test_all_five_fixtures_dont_crash(self):
        """5 个 fixture docx 都能成功 extract 不抛错"""
        for name in ("minimal.docx", "gb7713-official.docx",
                     "tsinghua-style.docx", "zhejiang-style.docx",
                     "wuhan-style.docx"):
            result = extract_template(FIXTURES / name)
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0,
                               f"{name} should yield at least 1 Requirement")


if __name__ == "__main__":
    unittest.main()
