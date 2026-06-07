"""Tests for app.core.reporters.pyqt_reporter.

把 Finding[] 投影为 QtIssue[]（6 字段 frozen dataclass），无 PyQt 依赖。
测试 4 项：field mapping / paragraph_index fallback / Severity 枚举 / Fix→text 转换。

Sprint 1.5 TDD step 1 — RED 阶段。
"""

import unittest

from app.core.ir.finding import Fix, Finding, Location, Severity
from app.core.reporters.pyqt_reporter import QtIssue, fix_to_human_text, to_qt_issue, to_qt_issues


def _make_finding(
    *,
    rule_id: str = "R001_line_spacing",
    severity: Severity = Severity.ERROR,
    message: str = "行距必须为 1.5",
    location: Location | None = None,
    fix: Fix | None = None,
) -> Finding:
    """辅助构造 Finding（最小有效 payload）。"""
    return Finding(
        rule_id=rule_id,
        severity=severity,
        message=message,
        location=location if location is not None else Location(section="第 3 章", paragraph_index=5),
        actual=None,
        expected=None,
        fix=fix,
    )


class TestToQtIssueMinimalFieldMapping(unittest.TestCase):
    def test_to_qt_issue_minimal_field_mapping(self):
        """完整字段映射：Finding → QtIssue，6 字段全部正确。"""
        loc = Location(section="第 3 章", paragraph_index=5, text_snippet="这是问题片段")
        fix = Fix(action="set", target="line_spacing", value=1.5)
        finding = _make_finding(
            rule_id="R001_line_spacing",
            severity=Severity.ERROR,
            message="行距必须为 1.5",
            location=loc,
            fix=fix,
        )

        issue = to_qt_issue(finding)

        self.assertIsInstance(issue, QtIssue)
        # 6 字段逐一断言
        self.assertEqual(issue.rule_id, "R001_line_spacing")
        self.assertEqual(issue.severity, "error")
        self.assertEqual(issue.message, "行距必须为 1.5")
        # location_text：section 段 + 段落索引提示
        self.assertIn("第 3 章", issue.location_text)
        self.assertEqual(issue.paragraph_index, 5)
        # fix 被投影为人类可读字符串
        self.assertIsNotNone(issue.fix_suggestion)
        self.assertIn("行距", issue.fix_suggestion)

    def test_to_qt_issue_is_frozen(self):
        """QtIssue 必须 frozen（不可变）—— 与 IR 风格一致。"""
        issue = to_qt_issue(_make_finding())
        with self.assertRaises(Exception):
            issue.rule_id = "mutated"  # type: ignore[misc]

    def test_to_qt_issue_no_fix_yields_none_suggestion(self):
        """Finding.fix=None → QtIssue.fix_suggestion=None。"""
        finding = _make_finding(fix=None)
        issue = to_qt_issue(finding)
        self.assertIsNone(issue.fix_suggestion)


class TestToQtIssueFallbackParagraphIndex(unittest.TestCase):
    def test_to_qt_issue_fallback_paragraph_index(self):
        """Finding 的 Location.paragraph_index=None → QtIssue.paragraph_index=-1。"""
        loc = Location(section="第 3 章", paragraph_index=None, text_snippet="无段落定位")
        finding = _make_finding(location=loc)
        issue = to_qt_issue(finding)
        self.assertEqual(issue.paragraph_index, -1)

    def test_to_qt_issue_default_location_fallback(self):
        """默认 Location()（全部 None）→ paragraph_index=-1。"""
        finding = Finding(
            rule_id="R000_x",
            severity=Severity.WARNING,
            message="全局警告",
            location=Location(),
        )
        issue = to_qt_issue(finding)
        self.assertEqual(issue.paragraph_index, -1)


class TestToQtIssuesBatched(unittest.TestCase):
    def test_to_qt_issues_batches_in_order(self):
        """to_qt_issues 接受 Iterable[Finding]，按输入顺序返回 list[QtIssue]。"""
        findings = [
            _make_finding(rule_id="R001_line_spacing", message="msg1"),
            _make_finding(rule_id="R002_font_size", message="msg2"),
            _make_finding(rule_id="R003_indent", message="msg3"),
        ]
        issues = to_qt_issues(findings)
        self.assertEqual(len(issues), 3)
        self.assertEqual([i.rule_id for i in issues], ["R001_line_spacing", "R002_font_size", "R003_indent"])

    def test_to_qt_issues_empty_input_returns_empty_list(self):
        """空 Iterable → 空 list（不应返回 None 或抛错）。"""
        issues = to_qt_issues([])
        self.assertEqual(issues, [])


class TestSeverityEnumToString(unittest.TestCase):
    def test_severity_enum_to_string(self):
        """Severity 枚举 → 字符串：ERROR='error' / WARNING='warning' / INFO='info'。"""
        self.assertEqual(Severity.ERROR.value, "error")
        self.assertEqual(Severity.WARNING.value, "warning")
        self.assertEqual(Severity.INFO.value, "info")

    def test_severity_propagates_to_qt_issue(self):
        """Finding.severity=Severity.WARNING → QtIssue.severity='warning'（不要大写）。"""
        finding = _make_finding(severity=Severity.WARNING)
        issue = to_qt_issue(finding)
        self.assertEqual(issue.severity, "warning")

        finding_info = _make_finding(severity=Severity.INFO)
        self.assertEqual(to_qt_issue(finding_info).severity, "info")


class TestFixToHumanText(unittest.TestCase):
    def test_fix_to_human_text(self):
        """Fix(action='set', target='line_spacing', value=1.5) → '设置行距为 1.5'。"""
        fix = Fix(action="set", target="line_spacing", value=1.5)
        text = fix_to_human_text(fix)
        self.assertIn("行距", text)
        self.assertIn("1.5", text)

    def test_fix_to_human_text_font_size_pt(self):
        """target='font_size_pt' → 字符串含 '字号' 与 'pt'。"""
        text = fix_to_human_text(Fix(action="set", target="font_size_pt", value=12))
        self.assertIn("字号", text)
        self.assertIn("12", text)

    def test_fix_to_human_text_first_line_indent_cm(self):
        """target='first_line_indent_cm' → 字符串含 '首行缩进' 与 'cm'。"""
        text = fix_to_human_text(Fix(action="set", target="first_line_indent_cm", value=0.74))
        self.assertIn("首行缩进", text)
        self.assertIn("cm", text)

    def test_fix_to_human_text_font_family(self):
        """target='font_family' → 字符串含 '字体' 与字体名。"""
        text = fix_to_human_text(Fix(action="set", target="font_family", value="宋体"))
        self.assertIn("字体", text)
        self.assertIn("宋体", text)

    def test_fix_to_human_text_paragraph_spacing(self):
        """target='paragraph_spacing' → 字符串含 '段间距'。"""
        text = fix_to_human_text(Fix(action="set", target="paragraph_spacing", value=0))
        self.assertIn("段间距", text)

    def test_fix_to_human_text_page_margin(self):
        """target='page_margin' → 字符串含 '页边距'。"""
        text = fix_to_human_text(Fix(action="set", target="page_margin", value=2.54))
        self.assertIn("页边距", text)

    def test_fix_to_human_text_unknown_target_returns_safe_fallback(self):
        """未知 target 不抛错，返回带 'Fix' 的安全降级字符串。"""
        text = fix_to_human_text(Fix(action="set", target="unknown_field_xyz", value=42))
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)


if __name__ == "__main__":
    unittest.main()
