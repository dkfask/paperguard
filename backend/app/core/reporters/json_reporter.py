"""JSON Reporter — Finding[] → SARIF-like 结构化 JSON 输出.

设计契约（plan §3.3）：
- JSONReporter().report(findings) -> str
- 输出 5 个 top-level keys: $schema / version / tool / summary / findings
- summary 含 total / error / warning / info 计数
- 每个 finding 含 rule_id / severity / message / location (paragraph_index + text_snippet) / fix (或 null)
- 输出可被 json.loads() 解析（valid JSON）

借鉴 references/format-thesis-docx/scripts/audit_format.py:150-155 字段命名
但结构用 SARIF 2.1 风格组织。
"""

from __future__ import annotations

import json

from app.core.ir import Finding


class JSONReporter:
    """Sprint 3 新增 — Finding[] → 结构化 JSON."""

    def report(self, findings: list[Finding]) -> str:
        """把 Finding[] 序列化为结构化 JSON string."""
        return json.dumps(self._build_payload(findings), ensure_ascii=False, indent=2)

    def _build_payload(self, findings: list[Finding]) -> dict:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "version": "1.0",
            "tool": "PaperGuard",
            "summary": {
                "total": len(findings),
                "error": sum(1 for f in findings if f.severity.value == "error"),
                "warning": sum(1 for f in findings if f.severity.value == "warning"),
                "info": sum(1 for f in findings if f.severity.value == "info"),
            },
            "findings": [self._finding_to_dict(f) for f in findings],
        }

    def _finding_to_dict(self, f: Finding) -> dict:
        return {
            "rule_id": f.rule_id,
            "severity": f.severity.value,
            "message": f.message,
            "location": {
                "paragraph_index": f.location.paragraph_index,
                "text_snippet": f.location.text_snippet,
            },
            "fix": {
                "action": f.fix.action,
                "target": f.fix.target,
                "value": f.fix.value,
            } if f.fix else None,
        }


__all__ = ["JSONReporter"]
