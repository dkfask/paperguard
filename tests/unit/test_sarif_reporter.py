"""Tests for Sprint 4 SARIFReporter."""

from __future__ import annotations

import json
import unittest

from app.core.ir import Finding, Fix, Location, Severity
from app.core.reporters.sarif_reporter import SARIFReporter


def _finding(rule_id: str, severity: Severity) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        message=f"{rule_id} message",
        location=Location(paragraph_index=7, text_snippet="sample"),
        actual="actual",
        expected="expected",
        fix=Fix(action="set", target="paragraph.line_spacing", value=1.5),
    )


class TestSARIFReporter(unittest.TestCase):
    def test_report_is_valid_sarif_json(self):
        payload = json.loads(SARIFReporter().report([_finding("PG-A", Severity.ERROR)]))

        self.assertEqual(payload["version"], "2.1.0")
        self.assertIn("$schema", payload)
        self.assertEqual(payload["runs"][0]["tool"]["driver"]["name"], "PaperGuard")

    def test_report_maps_severity_to_sarif_level(self):
        payload = json.loads(SARIFReporter().report([
            _finding("PG-E", Severity.ERROR),
            _finding("PG-W", Severity.WARNING),
            _finding("PG-I", Severity.INFO),
        ]))

        levels = [result["level"] for result in payload["runs"][0]["results"]]
        self.assertEqual(levels, ["error", "warning", "note"])

    def test_report_deduplicates_rules_by_rule_id(self):
        payload = json.loads(SARIFReporter().report([
            _finding("PG-DUP", Severity.ERROR),
            _finding("PG-DUP", Severity.WARNING),
        ]))

        rules = payload["runs"][0]["tool"]["driver"]["rules"]
        self.assertEqual([rule["id"] for rule in rules], ["PG-DUP"])

    def test_report_includes_artifact_uri_and_region(self):
        payload = json.loads(SARIFReporter().report(
            [_finding("PG-A", Severity.ERROR)],
            artifact_uri="paper.docx",
        ))

        location = payload["runs"][0]["results"][0]["locations"][0]
        self.assertEqual(location["physicalLocation"]["artifactLocation"]["uri"], "paper.docx")
        self.assertEqual(location["physicalLocation"]["region"]["startLine"], 7)

    def test_report_includes_fix_metadata(self):
        payload = json.loads(SARIFReporter().report([_finding("PG-A", Severity.ERROR)]))

        fixes = payload["runs"][0]["results"][0]["fixes"]
        self.assertEqual(fixes[0]["description"]["text"], "set paragraph.line_spacing = 1.5")


if __name__ == "__main__":
    unittest.main()
