"""Tests for app.services.format_highlighter — Sprint 1.5 FormatHighlighter.

Sprint 1.5 TDD step 5（见 docs/sprints/sprint-1.5-plan.md §4.1）。

5 个测试覆盖：
- mark() 给 paragraph_index 添加红色波浪下划线 ExtraSelection
- 第二次 mark() 替换（不是追加）之前的选择
- 越界 paragraph_index 不崩、不画线
- 重复 paragraph_index 合并为 1 个 selection
- clear() 清空所有 extraSelections

参考 `tests/test_workflow.py:11-12, 47-50` 复用 ``QT_QPA_PLATFORM=offscreen`` 模式。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor, QTextCharFormat, QTextFormat  # noqa: E402
from PyQt5.QtWidgets import QApplication, QTextEdit  # noqa: E402

from app.core.reporters.pyqt_reporter import QtIssue  # noqa: E402
from app.services.format_highlighter import FormatHighlighter  # noqa: E402


def _make_issue(paragraph_index: int, rule_id: str = "R001") -> QtIssue:
    """构造一个最小 QtIssue（仅 paragraph_index 重要）。"""
    return QtIssue(
        rule_id=rule_id,
        severity="warning",
        message=f"issue at paragraph {paragraph_index}",
        location_text=f"第 {paragraph_index} 段",
        paragraph_index=paragraph_index,
        fix_suggestion=None,
    )


class FormatHighlighterTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # 3 段 plain text → document 有 3 个 block（0/1/2）
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText("paragraph 0\nparagraph 1\nparagraph 2\n")
        self.addCleanup(self.text_edit.deleteLater)
        self.highlighter = FormatHighlighter(self.text_edit)


class TestMarkUnderline(FormatHighlighterTestBase):
    def test_mark_adds_underline_for_paragraph_index(self):
        """mark([QtIssue(paragraph_index=0)]) 在 block 0 上添加红色波浪下划线。"""
        self.highlighter.mark([_make_issue(paragraph_index=0)])

        selections = self.text_edit.extraSelections()
        self.assertEqual(len(selections), 1)
        sel = selections[0]
        # 红色波浪下划线
        self.assertEqual(sel.format.underlineStyle(), QTextCharFormat.SpellCheckUnderline)
        self.assertEqual(sel.format.underlineColor(), QColor("#FF0000"))
        # 覆盖 block 0
        self.assertEqual(sel.cursor.blockNumber(), 0)


class TestMarkReplaces(FormatHighlighterTestBase):
    def test_mark_clears_previous_selections(self):
        """mark() 第二次调用替换之前的选择（不追加）。"""
        self.highlighter.mark([_make_issue(paragraph_index=0)])
        self.assertEqual(len(self.text_edit.extraSelections()), 1)

        # 第二次 mark：只标记 block 1 → 应该只有 1 个 selection（block 1）
        self.highlighter.mark([_make_issue(paragraph_index=1)])
        selections = self.text_edit.extraSelections()
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0].cursor.blockNumber(), 1)


class TestOutOfRange(FormatHighlighterTestBase):
    def test_paragraph_index_out_of_range_no_crash(self):
        """mark([QtIssue(paragraph_index=999)]) 不崩、不加 selection。"""
        # 不抛异常
        self.highlighter.mark([_make_issue(paragraph_index=999)])
        # 没有 selection 被添加
        self.assertEqual(self.text_edit.extraSelections(), [])


class TestDuplicateMerge(FormatHighlighterTestBase):
    def test_duplicate_paragraph_index_merged(self):
        """mark([issue(0), issue(0)]) → 1 个 selection（不是 2 个）。"""
        self.highlighter.mark([
            _make_issue(paragraph_index=0, rule_id="R001"),
            _make_issue(paragraph_index=0, rule_id="R002"),
        ])
        selections = self.text_edit.extraSelections()
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0].cursor.blockNumber(), 0)


class TestClear(FormatHighlighterTestBase):
    def test_clear_removes_all_selections(self):
        """mark() 后 clear() → extraSelections 为空。"""
        self.highlighter.mark([
            _make_issue(paragraph_index=0),
            _make_issue(paragraph_index=2),
        ])
        self.assertEqual(len(self.text_edit.extraSelections()), 2)

        self.highlighter.clear()
        self.assertEqual(self.text_edit.extraSelections(), [])


if __name__ == "__main__":
    unittest.main()
