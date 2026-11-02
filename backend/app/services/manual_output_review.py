"""Output-bound human review receipts for DOCX files needing Office verification."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

from app.domain.documents.hashing import canonical_hash


def review_checklist(report: dict[str, Any] | None) -> list[dict[str, str]]:
    report = report if isinstance(report, dict) else {}
    raw = report.get("manual_checks")
    checks = raw if isinstance(raw, list) else []
    if report.get("office_review_required") and not checks:
        checks = ["在目标版本 Word/WPS 打开成品，核对分页、目录、字体替换、表格跨页及内容裁切。"]
    result = []
    for index, item in enumerate(checks):
        description = str(item or "").strip()
        if not description:
            continue
        digest = hashlib.sha256(f"{index}:{description}".encode("utf-8")).hexdigest()[:20]
        result.append({"id": f"office-review:{digest}", "description": description})
    return result


def review_required(report: dict[str, Any] | None) -> bool:
    report = report if isinstance(report, dict) else {}
    return bool(report.get("office_review_required") or review_checklist(report))


def review_identity(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_sha256": str(report.get("output_sha256") or ""),
        "project_id": str(report.get("project_id") or ""),
        "project_revision": report.get("project_revision"),
        "project_content_hash": str(report.get("project_content_hash") or ""),
        "template_id": str(report.get("template_id") or ""),
        "template_version": str(report.get("template_version") or ""),
        "template_hash": str(report.get("template_hash") or ""),
        "source_template_sha256": str(report.get("source_template_sha256") or ""),
        "checklist_hash": canonical_hash(review_checklist(report)),
    }


def confirm_review(
    report: dict[str, Any],
    *,
    output_sha256: str,
    confirmed_check_ids: list[str],
    reviewer_id: str,
) -> dict[str, Any]:
    checklist = review_checklist(report)
    if not review_required(report) or not checklist:
        raise ValueError("当前成品没有待确认的 Office 人工核查项")
    identity = review_identity(report)
    if not identity["output_sha256"] or output_sha256 != identity["output_sha256"]:
        raise ValueError("人工核查摘要与当前成品不一致")
    expected = sorted(item["id"] for item in checklist)
    if sorted(confirmed_check_ids) != expected:
        raise ValueError("必须逐项确认当前成品的全部人工核查项")
    if not reviewer_id.strip():
        raise ValueError("缺少核查人员身份")
    return {
        "status": "confirmed",
        **identity,
        "confirmed_check_ids": expected,
        "reviewer_id": reviewer_id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def review_matches(report: dict[str, Any], receipt: dict[str, Any] | None) -> bool:
    if not review_required(report):
        return True
    if not isinstance(receipt, dict) or receipt.get("status") != "confirmed":
        return False
    if any(receipt.get(key) != value for key, value in review_identity(report).items()):
        return False
    expected = sorted(item["id"] for item in review_checklist(report))
    return bool(receipt.get("reviewer_id") and receipt.get("reviewed_at") and receipt.get("confirmed_check_ids") == expected)


def review_view(report: dict[str, Any] | None, receipt: dict[str, Any] | None) -> dict[str, Any]:
    report = report if isinstance(report, dict) else {}
    if not review_required(report):
        return {"status": "not_required", "checklist": []}
    return {
        "status": "confirmed" if review_matches(report, receipt) else "required",
        "output_sha256": str(report.get("output_sha256") or ""),
        "checklist": review_checklist(report),
        "receipt": receipt if review_matches(report, receipt) else None,
    }


__all__ = ["confirm_review", "review_checklist", "review_matches", "review_required", "review_view"]
