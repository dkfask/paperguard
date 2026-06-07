"""Tests for app.services.issue_sidebar.

Sprint 1.5 TDD step 5 — IssueSidebar widget (severity colors + click/fix signals)。

侧边栏：QWidget 包含 QListWidget（每行一个 issue）+ 单一"修复"按钮。
- 点击行 → issue_activated(QtIssue)
- 点击"修复"按钮 → fix_requested(QtIssue)（当前选中行对应的 issue）
- 颜色映射：error=#FFCDD2 / warning=#FFF9C4 / info=#BBDEFB

6 个测试（严格按 prompt 列表）。
参考 `tests/test_workflow.py:11-12, 47-50` 复用 ``QT_QPA_PLATFORM=offscreen`` 模式。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from app.core.reporters.pyqt_reporter import QtIssue  # noqa: E402
from app.services.issue_sidebar import IssueSidebar  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_issue(
    rule_id: str = "R001_line_spacing",
    severity: str = "error",
    message: str = "行距必须为 1.5",
    location_text: str = "第 0 段",
    paragraph_index: int = 0,
    fix_suggestion: str | None = "设置行距为 1.5",
) -> QtIssue:
    """构造一个 QtIssue（最小有效 payload）。"""
    return QtIssue(
        rule_id=rule_id,
        severity=severity,
        message=message,
        location_text=location_text,
        paragraph_index=paragraph_index,
        fix_suggestion=fix_suggestion,
    )


# ---------------------------------------------------------------------------
# Test base
# ---------------------------------------------------------------------------

class IssueSidebarTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.sidebar = IssueSidebar()
        self.addCleanup(self.sidebar.deleteLater)
        # 信号捕获
        self.activated_received: list[QtIssue] = []
        self.fix_received: list[QtIssue] = []
        self.sidebar.issue_activated.connect(
            lambda issue: self.activated_received.append(issue)
        )
        self.sidebar.fix_requested.connect(
            lambda issue: self.fix_received.append(issue)
        )


# ---------------------------------------------------------------------------
# 1. init creates widget
# ---------------------------------------------------------------------------

class TestInit(IssueSidebarTestBase):
    def test_init_creates_widget(self):
        """IssueSidebar() 构造后是一个 QWidget 实例。"""
        self.assertIsInstance(self.sidebar, QWidget)


# ---------------------------------------------------------------------------
# 2-3. update_issues / clear
# ---------------------------------------------------------------------------

class TestUpdateIssues(IssueSidebarTestBase):
    def test_update_issues_populates_list(self):
        """update_issues([issue1, issue2]) 后 list 包含 2 行。"""
        issues = [
            _make_issue(rule_id="R001", message="msg1"),
            _make_issue(rule_id="R002", message="msg2", severity="warning"),
        ]
        self.sidebar.update_issues(issues)
        self.assertEqual(self.sidebar._list.count(), 2)
        # 第一行文本包含 message
        self.assertIn("msg1", self.sidebar._list.item(0).text())
        self.assertIn("msg2", self.sidebar._list.item(1).text())

    def test_update_issues_empty_clears_list(self):
        """update_issues([]) → list 0 行（同时清空旧 issues）。"""
        # 先填 2 条
        self.sidebar.update_issues(
            [_make_issue(rule_id="R001"), _make_issue(rule_id="R002")]
        )
        self.assertEqual(self.sidebar._list.count(), 2)
        # 再传空列表
        self.sidebar.update_issues([])
        self.assertEqual(self.sidebar._list.count(), 0)


# ---------------------------------------------------------------------------
# 4. severity color mapping
# ---------------------------------------------------------------------------

class TestSeverityColorMapping(IssueSidebarTestBase):
    def test_severity_color_mapping(self):
        """error → #FFCDD2 红底 / warning → #FFF9C4 黄底 / info → #BBDEFB 蓝底。"""
        issues = [
            _make_issue(rule_id="R_err", severity="error"),
            _make_issue(rule_id="R_warn", severity="warning"),
            _make_issue(rule_id="R_info", severity="info"),
        ]
        self.sidebar.update_issues(issues)
        # 每一行的 background 应是淡色调
        error_item = self.sidebar._list.item(0)
        warning_item = self.sidebar._list.item(1)
        info_item = self.sidebar._list.item(2)
        # QListWidgetItem.background() -> QBrush; brush.color() -> QColor
        error_color = error_item.background().color()
        warning_color = warning_item.background().color()
        info_color = info_item.background().color()
        # 断言 RGB 等于预期 hex
        expected_error = QColor("#FFCDD2")
        expected_warning = QColor("#FFF9C4")
        expected_info = QColor("#BBDEFB")
        self.assertEqual(error_color.red(), expected_error.red())
        self.assertEqual(error_color.green(), expected_error.green())
        self.assertEqual(error_color.blue(), expected_error.blue())
        self.assertEqual(warning_color.red(), expected_warning.red())
        self.assertEqual(warning_color.green(), expected_warning.green())
        self.assertEqual(warning_color.blue(), expected_warning.blue())
        self.assertEqual(info_color.red(), expected_info.red())
        self.assertEqual(info_color.green(), expected_info.green())
        self.assertEqual(info_color.blue(), expected_info.blue())


# ---------------------------------------------------------------------------
# 5. click row emits issue_activated
# ---------------------------------------------------------------------------

class TestClickRowEmits(IssueSidebarTestBase):
    def test_click_row_emits_issue_activated(self):
        """点击行 → issue_activated 发出对应 QtIssue。"""
        target_issue = _make_issue(rule_id="R001", paragraph_index=3, message="click me")
        self.sidebar.update_issues([target_issue])
        # 通过 list itemClicked 信号模拟点击（offscreen 环境最稳的方式）
        item = self.sidebar._list.item(0)
        self.sidebar._list.itemClicked.emit(item)
        # 信号被触发 1 次，payload 是同一 QtIssue
        self.assertEqual(len(self.activated_received), 1)
        emitted = self.activated_received[0]
        self.assertEqual(emitted, target_issue)
        self.assertEqual(emitted.rule_id, "R001")
        self.assertEqual(emitted.paragraph_index, 3)


# ---------------------------------------------------------------------------
# 6. fix button emits fix_requested
# ---------------------------------------------------------------------------

class TestFixButtonEmits(IssueSidebarTestBase):
    def test_fix_button_emits_fix_requested(self):
        """点击"修复"按钮 → fix_requested 发出当前选中行对应的 QtIssue。"""
        target_issue = _make_issue(rule_id="R_fix", paragraph_index=7, message="fix me")
        self.sidebar.update_issues([target_issue])
        # 选中第一行（修复按钮基于 currentItem）
        self.sidebar._list.setCurrentRow(0)
        # 点击修复按钮（offscreen 环境直接调 click()）
        self.sidebar._fix_button.click()
        # 信号被触发 1 次，payload 是同一 QtIssue
        self.assertEqual(len(self.fix_received), 1)
        emitted = self.fix_received[0]
        self.assertEqual(emitted, target_issue)
        self.assertEqual(emitted.rule_id, "R_fix")
        self.assertEqual(emitted.paragraph_index, 7)


if __name__ == "__main__":
    unittest.main()
