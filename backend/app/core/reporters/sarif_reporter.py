"""SARIF Reporter — Finding[] -> SARIF 2.1.0 subset JSON."""

from __future__ import annotations

import json
from typing import Any

from app.core.ir import Finding


_SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
_INFORMATION_URI = "https://github.com/dkfask/paperguard"


class SARIFReporter:
    """Render findings as a compact SARIF 2.1.0 payload."""

    def report(self, findings: list[Finding], artifact_uri: str | None = None) -> str:
        payload = {
            "$schema": _SARIF_SCHEMA,
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "PaperGuard",
                            "version": "0.1.0",
                            "informationUri": _INFORMATION_URI,
                            "rules": self._rules(findings),
                        }
                    },
                    "results": [
                        self._result(finding, artifact_uri)
                        for finding in findings
                    ],
                }
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _rules(self, findings: list[Finding]) -> list[dict[str, Any]]:
        rules: dict[str, dict[str, Any]] = {}
        for finding in findings:
            rules.setdefault(
                finding.rule_id,
                {
                    "id": finding.rule_id,
                    "name": finding.rule_id,
                    "shortDescription": {"text": finding.message},
                    "help": {"text": finding.message},
                },
            )
        return list(rules.values())

    def _result(self, finding: Finding, artifact_uri: str | None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ruleId": finding.rule_id,
            "level": self._level(finding),
            "message": {"text": finding.message},
            "locations": [self._location(finding, artifact_uri)],
        }
        if finding.fix:
            result["fixes"] = [
                {
                    "description": {
                        "text": (
                            f"{finding.fix.action} {finding.fix.target} "
                            f"= {finding.fix.value!r}"
                        )
                    },
                    "artifactChanges": [],
                }
            ]
        return result

    def _location(self, finding: Finding, artifact_uri: str | None) -> dict[str, Any]:
        physical_location: dict[str, Any] = {
            "region": {
                "startLine": finding.location.paragraph_index or 1,
            }
        }
        if artifact_uri:
            physical_location["artifactLocation"] = {"uri": artifact_uri}
        return {"physicalLocation": physical_location}

    def _level(self, finding: Finding) -> str:
        if finding.severity.value == "info":
            return "note"
        return finding.severity.value


__all__ = ["SARIFReporter"]
