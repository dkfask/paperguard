"""Versioned acceptance contract for source-bound Word master output checks."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections import Counter
from typing import Any


CONTRACT_VERSION = "1101-output-acceptance/6"

# A rule is scoreable only at the declared unit granularity. Composite or
# unknown checks remain hard blockers until the template mapping is reviewed.
_SCORED_FORMAT_UNITS = frozenset({
    ("master.effective_format", "paragraph.spacing.before"),
    ("master.effective_format", "paragraph.spacing.after"),
})
_RULE_MAPPING_SHA256 = hashlib.sha256(json.dumps(
    {
        "contract": CONTRACT_VERSION,
        "scored_format_units": sorted(
            [{"rule": rule, "unit": unit} for rule, unit in _SCORED_FORMAT_UNITS],
            key=lambda item: (item["rule"], item["unit"]),
        ),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
).encode("utf-8")).hexdigest()


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _check_identity(item: dict[str, Any]) -> str:
    rule = str(item.get("rule") or "unknown")
    obj = str(item.get("object") or "document")
    unit = str(item.get("unit") or "")
    return f"{CONTRACT_VERSION}:{rule}:{obj}:{unit}"


def _is_scored_format_unit(item: dict[str, Any]) -> bool:
    rule = str(item.get("rule") or "unknown")
    unit = str(item.get("unit") or "")
    property_name = unit.rsplit("#", 1)[-1] if "#" in unit else ""
    return (rule, property_name) in _SCORED_FORMAT_UNITS


def _manifest(report: dict[str, Any], check_ids: list[str]) -> dict[str, Any]:
    checks_payload = json.dumps({
        "checks": report["checks"],
        "unsupported": report.get("unsupported") or [],
        "layout_evidence_limitations": report.get("layout_evidence_limitations") or [],
        "excluded_checks": report.get("excluded_checks") or [],
        "project_content_hash": report.get("project_content_hash"),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "contract": CONTRACT_VERSION,
        "source_profile_sha256": report.get("source_profile_sha256"),
        "output_sha256": report.get("output_sha256"),
        "rule_mapping_sha256": _RULE_MAPPING_SHA256,
        "expected_check_ids": sorted(check_ids),
        "checks_sha256": hashlib.sha256(checks_payload.encode("utf-8")).hexdigest(),
    }


def _sign_manifest(manifest: dict[str, Any]) -> str:
    from app.services.secret_store import _require_master_key

    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(_require_master_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _unverifiable(report: dict[str, Any], message: str) -> dict[str, Any]:
    report.update({
        "acceptance_contract": CONTRACT_VERSION,
        "format_accuracy": {"passed": 0, "denominator": 0, "percentage": 0.0},
        "rule_accuracy": {},
        "failed_checks": [],
        "unverifiable_checks": [],
        "excluded_checks": [],
        "hard_blockers": [message],
        "acceptance_manifest": None,
        "acceptance_signature": "",
        "acceptance_status": "unverifiable",
        "status": "unverifiable",
    })
    return report


def apply_1101_acceptance_contract(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute the score and authenticate the complete check census."""
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        return _unverifiable(report, "检查项缺失或不可读取")
    if not all(isinstance(item, dict) for item in checks):
        return _unverifiable(report, "检查报告含无效检查项")

    identities = []
    for item in checks:
        check_id = _check_identity(item)
        item["check_id"] = check_id
        item["classification"] = "scored_format" if _is_scored_format_unit(item) else "hard_block"
        identities.append(check_id)

    hard_blockers = []
    if len(set(identities)) != len(identities):
        hard_blockers.append("检查单元身份重复")
    passed_checks = 0
    rule_accuracy: dict[str, dict[str, Any]] = {}
    rule_classifications: dict[str, set[str]] = {}
    failed_checks = []
    unverifiable_checks = []
    for item in checks:
        rule = str(item.get("rule") or "unknown")
        status = item.get("status")
        check_id = item["check_id"]
        summary = rule_accuracy.setdefault(rule, {
            "classification": item["classification"],
            "passed": 0,
            "denominator": 0,
            "percentage": 0.0,
            "failed": 0,
            "unverifiable": 0,
        })
        summary["denominator"] += 1
        rule_classifications.setdefault(rule, set()).add(item["classification"])
        if status == "pass":
            passed_checks += 1
            summary["passed"] += 1
        elif status == "fail":
            summary["failed"] += 1
            failed_checks.append(check_id)
        elif status == "unverifiable":
            summary["unverifiable"] += 1
            unverifiable_checks.append(check_id)
            hard_blockers.append(f"{rule}:{item.get('object') or 'document'}:unverifiable")
        elif status not in {"pass", "fail"}:
            hard_blockers.append(f"{rule}:{item.get('object') or 'document'}:invalid_status")
        if item["classification"] == "hard_block" and status != "pass":
            hard_blockers.append(f"{rule}:{item.get('object') or 'document'}:{status or 'unverifiable'}")

    denominator = len(checks)
    for summary in rule_accuracy.values():
        summary["percentage"] = round(summary["passed"] / summary["denominator"] * 100, 4)
    for rule, classifications in rule_classifications.items():
        rule_accuracy[rule]["classification"] = (
            next(iter(classifications)) if len(classifications) == 1 else "mixed"
        )
    percentage = round(passed_checks / denominator * 100, 4)
    if report.get("check_count") != denominator or report.get("passed_count") != passed_checks:
        hard_blockers.append("检查汇总与明细不一致")
    if report.get("unsupported"):
        hard_blockers.extend(f"unsupported:{item}" for item in report["unsupported"])
    if report.get("excluded_checks") not in (None, []):
        hard_blockers.append("排除检查项缺少本版本支持的适用性证明")
    if report.get("layout_evidence_limitations"):
        hard_blockers.extend(f"布局证据不完整:{item}" for item in report["layout_evidence_limitations"])
    if not _valid_digest(report.get("source_profile_sha256")):
        hard_blockers.append("源模板 profile 摘要缺失或无效")
    if not _valid_digest(report.get("output_sha256")):
        hard_blockers.append("输出 DOCX 摘要缺失或无效")
    hard_blockers = list(dict.fromkeys(hard_blockers))

    manifest = _manifest(report, identities)
    try:
        signature = _sign_manifest(manifest)
    except Exception:
        signature = ""
        hard_blockers.append("检查单元清单签名不可用")
    accepted = denominator > 0 and percentage > 90.0 and not hard_blockers
    report.update({
        "acceptance_contract": CONTRACT_VERSION,
        "acceptance_rule_mapping_sha256": _RULE_MAPPING_SHA256,
        "format_accuracy": {"passed": passed_checks, "denominator": denominator, "percentage": percentage},
        "rule_accuracy": rule_accuracy,
        "failed_checks": failed_checks,
        "unverifiable_checks": unverifiable_checks,
        "excluded_checks": [],
        "hard_blockers": hard_blockers,
        "acceptance_manifest": manifest,
        "acceptance_signature": signature,
        "acceptance_status": "pass" if accepted else "fail",
        "status": "pass" if accepted else "fail",
    })
    return report


def output_report_is_acceptable(report: dict[str, Any] | None) -> bool:
    """Verify the authenticated check census, then recompute every decision."""
    if not isinstance(report, dict):
        return False
    checks = report.get("checks")
    master_report = (
        "source_profile_sha256" in report
        or "master" in str(report.get("inspector_version") or "")
        or isinstance(checks, list) and any(
            isinstance(item, dict) and str(item.get("rule") or "").startswith("master.")
            for item in checks
        )
    )
    if master_report:
        if (report.get("acceptance_contract") != CONTRACT_VERSION
                or not _valid_digest(report.get("source_profile_sha256"))
                or not _valid_digest(report.get("output_sha256"))):
            return False
        manifest = report.get("acceptance_manifest")
        if not isinstance(checks, list) or not checks or not isinstance(manifest, dict):
            return False
        if (manifest.get("contract") != CONTRACT_VERSION
                or manifest.get("source_profile_sha256") != report.get("source_profile_sha256")
                or manifest.get("output_sha256") != report.get("output_sha256")
                or manifest.get("rule_mapping_sha256") != _RULE_MAPPING_SHA256):
            return False
        try:
            expected_signature = _sign_manifest(manifest)
        except Exception:
            return False
        if not hmac.compare_digest(str(report.get("acceptance_signature") or ""), expected_signature):
            return False
        copied = {**report, "checks": [dict(item) if isinstance(item, dict) else item for item in checks]}
        expected = apply_1101_acceptance_contract(copied)
        return bool(
            manifest == expected.get("acceptance_manifest")
            and report.get("acceptance_signature") == expected.get("acceptance_signature")
            and report.get("acceptance_rule_mapping_sha256") == expected.get("acceptance_rule_mapping_sha256")
            and report.get("status") == expected.get("status") == "pass"
            and report.get("acceptance_status") == expected.get("acceptance_status")
            and report.get("format_accuracy") == expected.get("format_accuracy")
            and report.get("rule_accuracy") == expected.get("rule_accuracy")
            and report.get("failed_checks") == expected.get("failed_checks")
            and report.get("unverifiable_checks") == expected.get("unverifiable_checks")
            and report.get("excluded_checks") == expected.get("excluded_checks") == []
            and report.get("hard_blockers") == expected.get("hard_blockers") == []
            and report.get("checks") == expected.get("checks")
        )
    checks = report.get("checks")
    return bool(
        report.get("status") == "pass"
        and isinstance(checks, list) and checks
        and report.get("check_count") == len(checks)
        and report.get("passed_count") == len(checks)
        and not report.get("unsupported")
        and all(isinstance(item, dict) and item.get("status") == "pass" for item in checks)
    )
