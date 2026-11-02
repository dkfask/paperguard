"""Generic specification contracts and legacy requirement mapping."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.documents.hashing import canonical_hash
from app.models.policy import RequirementSet


class SpecRule(BaseModel):
    """A normalized deterministic or human-reviewed rule candidate."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = "auto"
    target: str = "document"
    checker: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    severity: str = "warning"
    enforce: str = "soft"
    description: str = ""
    fix: dict[str, Any] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class SpecVersion(BaseModel):
    """Immutable normalized specification version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    spec_set_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: str = "draft"
    description: str = ""
    content_hash: str = ""
    effective_from: str = ""
    effective_to: str = ""
    rules: list[SpecRule] = Field(default_factory=list)
    source_asset_ids: list[str] = Field(default_factory=list)
    format_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    layout_overrides: dict[str, float] = Field(default_factory=dict)
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpecSet(BaseModel):
    """Logical specification set whose published contents are versioned."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    document_type_id: str = "thesis"
    name: str = Field(min_length=1)
    scope: str = "global"
    scope_id: str = ""
    owner_id: str | None = None
    status: str = "draft"
    current_version_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def source_execution_to_spec_rules(
    definition: Any,
    *,
    template_version_id: str,
    profile_sha256: str,
) -> list[SpecRule]:
    """Convert a verified Word execution profile into generic spec rules.

    The profile is the server-signed, source-bound result of teacher review.
    Only that frozen result is allowed to become a generic document rule set;
    the browser's extracted specification is never trusted directly.
    """
    raw_definition = definition.model_dump(mode="json") if hasattr(definition, "model_dump") else definition
    profile = (raw_definition.get("extensions") or {}).get("source_execution") or {}
    rules: list[SpecRule] = []
    for item in profile.get("content_requirements") or []:
        provenance = dict(item.get("provenance") or {})
        provenance.update({
            "source": item.get("source") or "Word template instruction",
            "source_version": item.get("source_version") or template_version_id,
            "template_version_id": template_version_id,
            "template_profile_sha256": profile_sha256,
        })
        rules.append(SpecRule(
            id=str(item["id"]),
            kind=str(item.get("type") or "manual"),
            target=str(item.get("target") or "document"),
            checker=str(item.get("checker") or ""),
            params=dict(item.get("params") or {}),
            severity=str(item.get("severity") or "info"),
            enforce=str(item.get("enforce") or "soft"),
            description=str(item.get("description") or item.get("source_text") or ""),
            provenance=provenance,
        ))
    return rules


def _family_id(requirement_set: RequirementSet) -> str:
    return requirement_set.family_id or requirement_set.id


def _version_id(spec_set_id: str, version: str) -> str:
    return f"{spec_set_id}@{version}"


def _rule_from_requirement(requirement_set: RequirementSet, item: Any) -> SpecRule:
    params = dict(item.params or {})
    candidate_fix = params.get("fix")
    if not isinstance(candidate_fix, dict):
        candidate_fix = None
    return SpecRule(
        id=item.id,
        kind=item.type.value,
        target=item.target,
        checker=item.checker,
        params=params,
        severity=item.severity.value,
        enforce=item.enforce.value,
        description=item.description,
        fix=candidate_fix,
        provenance={
            "source": item.source,
            "requirement_set_id": requirement_set.id,
            "requirement_set_version": requirement_set.version,
            "rule_pack_id": item.rule_pack_id,
            "ai_generated": item.source.startswith("ai:") if item.source else False,
        },
    )


def requirement_set_to_spec(
    requirement_set: RequirementSet,
    *,
    document_type_id: str = "thesis",
) -> tuple[SpecSet, SpecVersion]:
    """Map a legacy RequirementSet without discarding policy metadata."""
    spec_set_id = _family_id(requirement_set)
    spec_version_id = _version_id(spec_set_id, requirement_set.version)
    rules = [_rule_from_requirement(requirement_set, item) for item in requirement_set.items]
    version_payload = {
        "id": spec_version_id,
        "spec_set_id": spec_set_id,
        "version": requirement_set.version,
        "status": requirement_set.status.value,
        "description": requirement_set.description,
        "effective_from": requirement_set.effective_from,
        "effective_to": requirement_set.effective_to,
        "rules": [rule.model_dump(mode="json") for rule in rules],
        "source_asset_ids": [doc.id or doc.storage_key for doc in requirement_set.documents],
        "format_overrides": requirement_set.format_overrides,
        "layout_overrides": requirement_set.layout_overrides,
        "bindings": [binding.model_dump(mode="json") for binding in requirement_set.bindings],
        "metadata": {
            "source_requirement_set_id": requirement_set.id,
            "family_id": spec_set_id,
            "level": requirement_set.level.value,
            "scope": requirement_set.scope,
            "scope_id": requirement_set.scope_id,
            "course_id": requirement_set.course_id,
            "stage": requirement_set.stage,
            "deadline_at": requirement_set.deadline_at,
            "applies_to": requirement_set.applies_to,
            "markdown_content": requirement_set.markdown_content,
            "documents": [doc.model_dump(mode="json") for doc in requirement_set.documents],
        },
    }
    spec_version = SpecVersion(
        **version_payload,
        content_hash=canonical_hash(version_payload),
    )
    spec_set = SpecSet(
        id=spec_set_id,
        document_type_id=document_type_id,
        name=requirement_set.name,
        scope=requirement_set.scope,
        scope_id=requirement_set.scope_id,
        owner_id=requirement_set.owner_id,
        status=requirement_set.status.value,
        current_version_id=spec_version_id,
        metadata={
            "level": requirement_set.level.value,
            "source_requirement_set_id": requirement_set.id,
        },
    )
    return spec_set, spec_version


__all__ = ["SpecRule", "SpecSet", "SpecVersion", "requirement_set_to_spec", "source_execution_to_spec_rules"]
