"""Deterministic validation for generic document projects.

The validator consumes only the project content and the immutable specification
snapshot selected by its package. AI is not involved in execution.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.domain.documents.hashing import document_project_content_hash
from app.domain.documents.schemas import (
    DocumentProject,
    TemplateDefinition,
    ValidationFinding,
    ValidationRun,
)
from app.domain.documents.specs import SpecRule, SpecVersion


_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_SECTION_TARGETS = {"document", "body", "abstract", "keywords", "references"}
_PRESENTATION_KINDS = {"cover", "toc"}


def _section_nodes(definition: TemplateDefinition) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []

    def walk(nodes) -> None:
        for node in nodes:
            result.append({"key": node.key, "kind": node.kind, "title": node.title})
            walk(node.children)

    walk(definition.sections)
    return result


def _block_text(block: Any) -> str:
    return str(getattr(block, "text", "") or "")


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, set)) and not value:
        return False
    return bool(str(value).strip())


def _section_text(project: DocumentProject, key: str) -> str:
    blocks = project.sections.get(key, [])
    ordered = sorted(blocks, key=lambda block: (block.order, block.id))
    return "\n".join(_block_text(block) for block in ordered).strip()


def _target_text(project: DocumentProject, nodes: dict[str, dict[str, str]], target: str, params: dict[str, Any]) -> tuple[str, str]:
    section_key = str(params.get("section_key") or "").strip()
    if section_key:
        return _section_text(project, section_key), section_key
    if target not in _SECTION_TARGETS:
        return _section_text(project, target), target
    if target in {"abstract", "keywords", "references"}:
        matching = next(
            (key for key, node in nodes.items() if node["kind"] == target or key == target),
            target,
        )
        return _section_text(project, matching), matching
    keys = [
        key for key, node in nodes.items()
        if target == "document" or node["kind"] not in _PRESENTATION_KINDS
    ]
    return "\n".join(_section_text(project, key) for key in keys).strip(), ""


def _count(text: str, mode: str) -> int:
    if mode == "words":
        return len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?", text))
    return len(re.sub(r"\s+", "", text))


def _keywords(text: str) -> list[str]:
    text = re.sub(r"^\s*(?:关键词|key\s*words?)\s*[:：]?\s*", "", text, flags=re.I)
    return [item.strip() for item in re.split(r"[;,；，、\n]+", text) if item.strip()]


def _expectation(params: dict[str, Any]) -> str | dict[str, Any]:
    expected = {key: params[key] for key in ("min", "max") if key in params}
    if "min" in expected and "max" in expected:
        return f"{expected['min']} - {expected['max']}"
    if "min" in expected:
        return f"at least {expected['min']}"
    if "max" in expected:
        return f"at most {expected['max']}"
    return expected or "satisfies the rule"


def _finding(
    rule: SpecRule,
    *,
    message: str,
    actual: Any,
    expected: Any,
    section_key: str = "",
    spec_version_id: str,
) -> ValidationFinding:
    return ValidationFinding(
        rule_id=rule.id,
        checker=rule.checker,
        severity=rule.severity,
        enforce=rule.enforce,
        message=message,
        actual=actual,
        expected=expected,
        section_key=section_key,
        source=rule.provenance.get("source") or "document specification",
        source_version=spec_version_id,
    )


def _not_checked(rule: SpecRule, reason: str, spec_version_id: str) -> dict[str, Any]:
    return {
        "rule_id": rule.id,
        "description": rule.description or rule.checker or "unnamed requirement",
        "checker": rule.checker,
        "severity": rule.severity,
        "enforce": rule.enforce,
        "source": rule.provenance.get("source") or "document specification",
        "source_version": spec_version_id,
        "reason": reason,
        "fixable": False,
    }


def _recent_reference_count(text: str, params: dict[str, Any]) -> tuple[int, int]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    current_year = int(params.get("current_year") or datetime.now().year)
    years = int(params.get("years") or 3)
    recent = sum(
        1
        for line in lines
        if any(current_year - years + 1 <= int(year) <= current_year for year in _YEAR_RE.findall(line))
    )
    return recent, len(lines)


def _reference_format_issues(text: str, params: dict[str, Any]) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    prefix = str(params.get("prefix_pattern") or r"(?:\[\d+\]|\d+[.)、])")
    pattern = re.compile(rf"^\s*{prefix}")
    return [line for line in lines if not pattern.search(line) or not _YEAR_RE.search(line)]


def validate_document_project(
    project: DocumentProject,
    definition: TemplateDefinition,
    spec_version: SpecVersion,
    *,
    run_id: str,
    performed_by: str,
    created_at: str,
    rule_engine_version: str,
) -> ValidationRun:
    """Run supported rules and retain explicit evidence for deferred rules."""
    nodes = {item["key"]: item for item in _section_nodes(definition)}
    findings: list[ValidationFinding] = []
    not_checked: list[dict[str, Any]] = []

    # Required fields are intrinsic template constraints, independent of the
    # uploaded specification. Drafts may remain incomplete, but validation and
    # export must expose a deterministic hard finding for each missing value.
    for field in definition.fields:
        if field.required and not _value_present(project.field_values.get(field.key)):
            findings.append(ValidationFinding(
                rule_id=f"template.required.{field.key}",
                checker="field.required",
                severity="error",
                enforce="hard",
                message=f"Required field is missing: {field.label or field.key}",
                actual=False,
                expected=True,
                source="template definition",
                source_version=project.template_version_id,
            ))

    for rule in spec_version.rules:
        if rule.kind != "auto":
            not_checked.append(_not_checked(rule, "manual_or_reference_rule", spec_version.id))
            continue

        params = rule.params or {}
        checker = rule.checker
        if checker == "chapter.page_break":
            not_checked.append(_not_checked(rule, "renderer_required", spec_version.id))
            continue

        if checker == "section.exists":
            keys = params.get("section_keys") or params.get("section_key")
            keys = keys if isinstance(keys, list) else [keys or rule.target]
            keys = [str(key).strip() for key in keys if str(key).strip()]
            if not keys:
                not_checked.append(_not_checked(rule, "section_key_required", spec_version.id))
                continue
            for key in keys:
                if key not in nodes or not _section_text(project, key):
                    findings.append(_finding(
                        rule,
                        message=rule.description or f"Section {key} is missing or empty",
                        actual=0,
                        expected="exists and is non-empty",
                        section_key=key,
                        spec_version_id=spec_version.id,
                    ))
            continue

        if checker in {"section.word_count", "abstract.word_count"}:
            text, section_key = _target_text(project, nodes, rule.target, params)
            actual = _count(text, str(params.get("count_mode") or "characters"))
            expected = _expectation(params)
            failed = ("min" in params and actual < params["min"]) or ("max" in params and actual > params["max"])
            if failed:
                findings.append(_finding(
                    rule,
                    message=rule.description or f"Content length does not satisfy {expected}",
                    actual=actual,
                    expected=expected,
                    section_key=section_key,
                    spec_version_id=spec_version.id,
                ))
            continue

        if checker == "keywords.count":
            text, section_key = _target_text(project, nodes, "keywords", params)
            actual = len(_keywords(text))
            expected = _expectation(params)
            failed = ("min" in params and actual < params["min"]) or ("max" in params and actual > params["max"])
            if failed:
                findings.append(_finding(
                    rule,
                    message=rule.description or f"Keyword count does not satisfy {expected}",
                    actual=actual,
                    expected=expected,
                    section_key=section_key,
                    spec_version_id=spec_version.id,
                ))
            continue

        if checker in {"cover.field_present", "meta.field_present"}:
            field_key = str(params.get("field_key") or rule.target or "").strip()
            if not field_key or field_key in _SECTION_TARGETS:
                not_checked.append(_not_checked(rule, "field_key_required", spec_version.id))
                continue
            actual = bool(str(project.field_values.get(field_key, "") or "").strip())
            if not actual:
                findings.append(_finding(
                    rule,
                    message=rule.description or f"Field {field_key} is required",
                    actual=False,
                    expected=True,
                    section_key="",
                    spec_version_id=spec_version.id,
                ))
            continue

        if checker == "ai.usage_declared":
            field_key = str(params.get("field_key") or "ai_usage_declared")
            if not str(project.field_values.get(field_key, "") or "").strip():
                findings.append(_finding(
                    rule,
                    message=rule.description or "AI usage has not been declared",
                    actual=False,
                    expected=True,
                    spec_version_id=spec_version.id,
                ))
            continue

        if checker in {"references.count", "references.recent_count", "references.recent_ratio", "references.format"}:
            text, section_key = _target_text(project, nodes, "references", params)
            lines = [line for line in text.splitlines() if line.strip()]
            if checker == "references.count":
                actual = len(lines)
                expected = _expectation(params)
                failed = ("min" in params and actual < params["min"]) or ("max" in params and actual > params["max"])
            elif checker in {"references.recent_count", "references.recent_ratio"}:
                recent, total = _recent_reference_count(text, params)
                if checker == "references.recent_ratio":
                    actual = round(recent / total, 4) if total else 0
                    expected = params.get("min_ratio", params.get("min", 0))
                    failed = actual < expected
                else:
                    actual = recent
                    expected = params.get("min_count", params.get("min", 0))
                    failed = actual < expected
            else:
                issues = _reference_format_issues(text, params)
                actual = {"invalid_count": len(issues), "sample": issues[:3]}
                expected = "each reference has an index and year"
                failed = bool(issues)
            if failed:
                findings.append(_finding(
                    rule,
                    message=rule.description or "References do not satisfy the requirement",
                    actual=actual,
                    expected=expected,
                    section_key=section_key,
                    spec_version_id=spec_version.id,
                ))
            continue

        not_checked.append(_not_checked(rule, "unsupported_checker", spec_version.id))

    summary = {
        "total": len(findings),
        "error": sum(item.severity == "error" for item in findings),
        "warning": sum(item.severity == "warning" for item in findings),
        "info": sum(item.severity == "info" for item in findings),
        "hard_failures": sum(item.enforce == "hard" and item.severity == "error" for item in findings),
        "not_checked": len(not_checked),
        "checked_rules": len(spec_version.rules) - len(not_checked),
    }
    return ValidationRun(
        id=run_id,
        project_id=project.id,
        package_id=project.package_id,
        template_version_id=project.template_version_id,
        spec_version_id=project.spec_version_id,
        renderer_version=project.renderer_version,
        rule_engine_version=rule_engine_version,
        project_revision=project.revision,
        content_hash=document_project_content_hash(project),
        summary=summary,
        findings=findings,
        not_checked=not_checked,
        performed_by=performed_by,
        created_at=created_at,
    )


__all__ = ["validate_document_project"]
