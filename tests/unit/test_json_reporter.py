"""Tests for app.core.reporters.json_reporter — Sprint 3.

Sprint 3 commit #3: 6 RED tests for JSON reporter.

设计契约（plan §3.3）：
- JSONReporter().report(findings) -> str
- 输出 SARIF-like 结构化 JSON (tool / summary / findings)
- summary.total / error / warning / info 正确
- finding 含 rule_id / severity / message / location / fix
- 无 fix 时 fix=null
- 输出可通过 json.loads() 解析
"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.ir import Document, DocumentMetadata, Finding, Fix, Location, Severity
from app.core.reporters.json_reporter import JSONReporter


def _finding(rule_id: str = "R-TEST", severity: Severity = Severity.WARNING,
             msg: str = "测试消息", paragraph_index: int = 0,
             fix: Fix | None = None) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        message=msg,
        location=Location(paragraph_index=paragraph_index, text_snippet="测试文本"),
        actual=None,
        expected=None,
        fix=fix,
    )


class TestJSONReporter(unittest.TestCase):

    def test_empty_findings_returns_valid_json(self):
        """空 findings → JSON 字符串，可解析；summary.total=0"""
        result = JSONReporter().report([])
        self.assertIsInstance(result, str)
        data = json.loads(result)  # 不抛错 = 有效 JSON
        self.assertEqual(data["summary"]["total"], 0)

    def test_json_has_required_top_level_keys(self):
        result = json.loads(JSONReporter().report([]))
        for key in ("$schema", "version", "tool", "summary", "findings"):
            self.assertIn(key, result, f"missing top-level key: {key}")
        self.assertEqual(result["tool"], "PaperGuard")

    def test_summary_counts_by_severity(self):
        """3 finding: 1 error + 1 warning + 1 info → summary 正确统计"""
        findings = [
            _finding(rule_id="R-E", severity=Severity.ERROR, paragraph_index=0),
            _finding(rule_id="R-W", severity=Severity.WARNING, paragraph_index=1),
            _finding(rule_id="R-I", severity=Severity.INFO, paragraph_index=2),
        ]
        data = json.loads(JSONReporter().report(findings))
        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["error"], 1)
        self.assertEqual(data["summary"]["warning"], 1)
        self.assertEqual(data["summary"]["info"], 1)

    def test_finding_serialized_with_required_fields(self):
        """finding 含 rule_id / severity / message / location"""
        f = _finding(rule_id="R-LINE", severity=Severity.WARNING,
                     msg="行距不对", paragraph_index=3)
        data = json.loads(JSONReporter().report([f]))
        self.assertEqual(len(data["findings"]), 1)
        finding_json = data["findings"][0]
        self.assertEqual(finding_json["rule_id"], "R-LINE")
        self.assertEqual(finding_json["severity"], "warning")
        self.assertEqual(finding_json["message"], "行距不对")
        self.assertEqual(finding_json["location"]["paragraph_index"], 3)

    def test_finding_with_fix_serializes_fix(self):
        """finding 带 fix → JSON 含 fix 结构（action / target / value）"""
        f = _finding(fix=Fix(action="set", target="paragraph.line_spacing", value=1.5))
        data = json.loads(JSONReporter().report([f]))
        fix_json = data["findings"][0]["fix"]
        self.assertIsNotNone(fix_json)
        self.assertEqual(fix_json["action"], "set")
        self.assertEqual(fix_json["target"], "paragraph.line_spacing")
        self.assertEqual(fix_json["value"], 1.5)

    def test_finding_without_fix_serializes_null(self):
        """finding 无 fix → JSON fix=null"""
        f = _finding(fix=None)
        data = json.loads(JSONReporter().report([f]))
        self.assertIsNone(data["findings"][0]["fix"])


if __name__ == "__main__":
    unittest.main()
