"""Tests for app.services.live_format_checker.

Sprint 1.5 TDD step 3 — RED 阶段。
LiveFormatChecker: QObject + QTimer debounce + Qt signal。
8 个测试覆盖：timer 启动 / debounce 重置 / issues_found / no_issues /
check_failed / get_fix 缓存命中 / 未知 issue / no_issues 清空缓存。

参考 `tests/test_workflow.py:11-12, 47-50` 复用 ``QT_QPA_PLATFORM=offscreen`` 模式。
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.core.ir.doc import Document, DocumentMetadata  # noqa: E402
from app.core.ir.finding import Fix, Finding, Location, Severity  # noqa: E402
from app.core.reporters.pyqt_reporter import QtIssue  # noqa: E402
from app.models.project import ParagraphBlock, Project, SectionContent  # noqa: E402
from app.services.live_format_checker import LiveFormatChecker  # noqa: E402


def _make_project(html: str = "<p>正文段落</p>") -> Project:
    """构造一个最小 Project（一个 section，一个段落）。"""
    return Project(
        title="测试论文",
        author="测试",
        sections={
            "chapter_1": SectionContent(
                section_id="chapter_1",
                blocks=[ParagraphBlock(html=html)],
            )
        },
    )


def _make_empty_document() -> Document:
    """构造空 Document（无段落）→ engine 返回空 findings → no_issues 触发。"""
    return Document(sections=(), paragraphs=(), metadata=DocumentMetadata())


def _make_finding(
    *,
    rule_id: str = "R001",
    paragraph_index: int = 0,
    fix: Fix | None = None,
) -> Finding:
    """构造一条带 Fix 的 Finding（默认 fix=R001 line_spacing set 1.5）。"""
    if fix is None:
        fix = Fix(action="set", target="paragraph.line_spacing", value=1.5)
    return Finding(
        rule_id=rule_id,
        severity=Severity.WARNING,
        message=f"违规 {rule_id}",
        location=Location(paragraph_index=paragraph_index, text_snippet="x"),
        actual=1.0,
        expected=1.5,
        fix=fix,
    )


class LiveFormatCheckerTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.project = _make_project()
        self.checker = LiveFormatChecker(self.project, debounce_ms=500)
        self.addCleanup(self.checker._timer.stop)
        # 信号捕获槽
        self.issues_found_received: list[list] = []
        self.no_issues_received: list[None] = []
        self.check_failed_received: list[str] = []
        self.checker.issues_found.connect(
            lambda issues: self.issues_found_received.append(issues)
        )
        self.checker.no_issues.connect(
            lambda: self.no_issues_received.append(None)
        )
        self.checker.check_failed.connect(
            lambda msg: self.check_failed_received.append(msg)
        )


class TestRequestCheckTimer(LiveFormatCheckerTestBase):
    def test_request_check_starts_timer(self):
        """request_check() 启动 QTimer（isActive() == True）。"""
        self.assertFalse(self.checker._timer.isActive())
        self.checker.request_check()
        self.assertTrue(self.checker._timer.isActive())

    def test_continuous_request_resets_timer(self):
        """两次 request_check() 在 debounce 窗口内 → 只触发一次 check。"""
        fired_before = len(self.issues_found_received) + len(self.no_issues_received)
        self.checker.request_check()
        QTest.qWait(200)
        # 在 debounce 窗口内：timer 还没 fire
        self.assertEqual(
            len(self.issues_found_received) + len(self.no_issues_received),
            fired_before,
        )
        # 第二次 request 重置 timer
        self.checker.request_check()
        QTest.qWait(700)
        # 等待超过 debounce：应该 fire 一次
        self.assertEqual(
            len(self.issues_found_received) + len(self.no_issues_received),
            fired_before + 1,
        )


class TestRunCheckEmits(LiveFormatCheckerTestBase):
    def test_run_check_emits_issues_found(self):
        """engine 返回 findings → issues_found 发 list[QtIssue]。"""
        findings = [
            _make_finding(rule_id="R001", paragraph_index=0),
            _make_finding(
                rule_id="R003",
                paragraph_index=0,
                fix=Fix(action="set", target="paragraph.first_line_indent_cm", value=0.74),
            ),
        ]
        with patch("app.services.live_format_checker.project_to_ir") as mock_ir, \
             patch("app.services.live_format_checker.validate_docx") as mock_validate:
            mock_ir.return_value = "fake_ir"
            mock_validate.return_value = findings
            self.checker.request_check()
            QTest.qWait(600)

        self.assertEqual(len(self.issues_found_received), 1)
        issues = self.issues_found_received[0]
        self.assertEqual(len(issues), 2)
        for issue in issues:
            self.assertIsInstance(issue, QtIssue)
        self.assertEqual(issues[0].rule_id, "R001")
        self.assertEqual(issues[1].rule_id, "R003")

    def test_run_check_empty_emits_no_issues(self):
        """engine 返回空 → no_issues 发，issues_found 不发。"""
        with patch("app.services.live_format_checker.project_to_ir") as mock_ir, \
             patch("app.services.live_format_checker.validate_docx") as mock_validate:
            mock_ir.return_value = _make_empty_document()
            mock_validate.return_value = []
            self.checker.request_check()
            QTest.qWait(600)

        self.assertEqual(len(self.no_issues_received), 1)
        self.assertEqual(self.issues_found_received, [])

    def test_engine_exception_emits_check_failed(self):
        """engine 抛异常 → check_failed 发；checker 不崩。"""
        with patch("app.services.live_format_checker.project_to_ir") as mock_ir, \
             patch("app.services.live_format_checker.validate_docx") as mock_validate:
            mock_ir.return_value = "fake_ir"
            mock_validate.side_effect = RuntimeError("engine boom")
            self.checker.request_check()
            QTest.qWait(600)

        self.assertEqual(len(self.check_failed_received), 1)
        self.assertIn("engine boom", self.check_failed_received[0])
        # checker 仍然存活（未崩）
        self.assertIsNotNone(self.checker)


class TestGetFix(LiveFormatCheckerTestBase):
    def test_get_fix_returns_fix_from_cache(self):
        """check 后，get_fix(QtIssue) 返回对应 Fix。"""
        expected_fix = Fix(action="set", target="paragraph.line_spacing", value=1.5)
        finding = Finding(
            rule_id="R001",
            severity=Severity.WARNING,
            message="msg",
            location=Location(paragraph_index=3, text_snippet="x"),
            actual=1.0,
            expected=1.5,
            fix=expected_fix,
        )
        with patch("app.services.live_format_checker.project_to_ir") as mock_ir, \
             patch("app.services.live_format_checker.validate_docx") as mock_validate:
            mock_ir.return_value = "fake_ir"
            mock_validate.return_value = [finding]
            self.checker.request_check()
            QTest.qWait(600)

        self.assertEqual(len(self.issues_found_received), 1)
        issue = self.issues_found_received[0][0]
        self.assertEqual(issue.rule_id, "R001")
        self.assertEqual(issue.paragraph_index, 3)
        cached = self.checker.get_fix(issue)
        self.assertEqual(cached, expected_fix)

    def test_get_fix_unknown_issue_returns_none(self):
        """get_fix 未知 QtIssue → None。"""
        unknown = QtIssue(
            rule_id="R999_unknown",
            severity="warning",
            message="unknown",
            location_text="",
            paragraph_index=999,
            fix_suggestion=None,
        )
        self.assertIsNone(self.checker.get_fix(unknown))

    def test_no_issues_clears_fix_cache(self):
        """no_issues 发后，fix cache 为空（之前缓存的 Fix 无法再查到）。"""
        finding = _make_finding(rule_id="R001", paragraph_index=0)
        with patch("app.services.live_format_checker.project_to_ir") as mock_ir, \
             patch("app.services.live_format_checker.validate_docx") as mock_validate:
            mock_ir.return_value = "fake_ir"
            # 第一次 check 返回 findings；第二次 check 返回空
            mock_validate.side_effect = [[finding], []]
            self.checker.request_check()
            QTest.qWait(600)
            # 第一次 fire：issues_found
            self.assertEqual(len(self.issues_found_received), 1)
            issue = self.issues_found_received[0][0]
            self.assertIsNotNone(self.checker.get_fix(issue))
            # 第二次 request + wait：no_issues
            self.checker.request_check()
            QTest.qWait(600)

        self.assertEqual(len(self.no_issues_received), 1)
        # 缓存已被清空
        self.assertIsNone(self.checker.get_fix(issue))


if __name__ == "__main__":
    unittest.main()
