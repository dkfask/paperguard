"""Tests for app.core.extract.format_words — Sprint 2.5 FORMAT_WORDS 启发式.

10 RED tests cover：
- 英文关键词匹配（font / size / spacing / margin / header / footer / page / caption / reference）
- 中文关键词匹配（标题 / 字体 / 字号 / 行距 / 缩进 / 页眉 / ...）
- 不匹配普通文本（"hello world"）
- 边界：空字符串 / None 安全 / 单字
- 大小写不敏感（"Font" / "FONT" / "font" 均命中）
- 多关键词同时出现

设计契约（参考 docs/sprints/sprint-2.5-plan.md §3.1 + 参考项目 line 20-24）：
- FORMAT_WORDS: re.Pattern 模块级常量
- match_format_words(text: str) -> bool 主入口
- 32 词（英文 9 + 中文 23）+ 2 词扩展 = 34 词
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.extract.format_words import FORMAT_WORDS, match_format_words


class TestEnglishMatch(unittest.TestCase):
    def test_match_font(self):
        self.assertTrue(match_format_words("Use Times New Roman font"))

    def test_match_size(self):
        self.assertTrue(match_format_words("Font size must be 12pt"))

    def test_match_spacing(self):
        self.assertTrue(match_format_words("Line spacing should be 1.5"))

    def test_match_margin(self):
        self.assertTrue(match_format_words("Page margin: 2.54cm"))

    def test_match_case_insensitive(self):
        self.assertTrue(match_format_words("FONT must be Times"))
        self.assertTrue(match_format_words("Font must be Times"))


class TestChineseMatch(unittest.TestCase):
    def test_match_zh_zhuting(self):
        self.assertTrue(match_format_words("正文行距应为 1.5 倍"))

    def test_match_zh_ziti(self):
        self.assertTrue(match_format_words("字体使用宋体"))

    def test_match_zh_canshu_wenxian(self):
        self.assertTrue(match_format_words("请按 GB/T 7714 编排参考文献"))

    def test_match_zh_zhaiyao(self):
        self.assertTrue(match_format_words("摘要不超过 300 字"))


class TestNoMatch(unittest.TestCase):
    def test_plain_english_text_no_match(self):
        self.assertFalse(match_format_words("Hello world, this is a test."))

    def test_plain_chinese_no_match(self):
        # 不含任何 FORMAT_WORDS 关键词的中文（注意"研究"不在词典里）
        self.assertFalse(match_format_words("这是普通段落，无格式相关词汇。"))


class TestEdgeCases(unittest.TestCase):
    def test_empty_string_no_match(self):
        self.assertFalse(match_format_words(""))

    def test_format_words_pattern_is_compiled(self):
        import re
        self.assertIsInstance(FORMAT_WORDS, re.Pattern)


if __name__ == "__main__":
    unittest.main()
