"""Tests for Sprint 4 HTMLReporter."""

from __future__ import annotations

import unittest

from app.core.ir import Finding, Fix, Location, Severity
from app.core.reporters.html_reporter import HTMLReporter


def _finding(
    rule_id: str = "PG-HTML-XSS",
    severity: Severity = Severity.ERROR,
    message: str = "Unsafe <script>alert(1)</script>",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        message=message,
        location=Location(paragraph_index=3, text_snippet="body <script>x</script>"),
        actual="bad",
        expected="good",
        fix=Fix(action="set", target="paragraph.line_spacing", value=1.5),
    )


class TestHTMLReporter(unittest.TestCase):
    def test_report_returns_full_html_document(self):
        html = HTMLReporter().report([_finding()])

        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<html", html)
        self.assertIn("</html>", html)
        self.assertIn("PaperGuard", html)

    def test_report_escapes_message_and_snippet(self):
        html = HTMLReporter().report([_finding()])

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_report_renders_empty_findings(self):
        html = HTMLReporter().report([])

        self.assertIn("No issues found", html)
        self.assertIn("0 findings", html)

    def test_report_renders_finding_details(self):
        html = HTMLReporter().report([_finding(rule_id="PG-BODY-LINE-SPACING")])

        self.assertIn("PG-BODY-LINE-SPACING", html)
        self.assertIn("paragraph.line_spacing", html)
        self.assertIn("1.5", html)


if __name__ == "__main__":
    unittest.main()
