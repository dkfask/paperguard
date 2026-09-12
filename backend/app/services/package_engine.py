"""Compose confirmed template/spec candidates into an immutable package."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.documents.hashing import canonical_hash
from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentTemplate,
    DocumentTypeStatus,
    VersionStatus,
)
from app.domain.documents.schemas import TemplateVersion
from app.domain.documents.specs import SpecSet, SpecVersion
from app.services.spec_engine import SpecParseCandidate, validate_spec_candidate
from app.services.template_engine import TemplateParseCandidate, validate_template_candidate
from app.services.template_source import resolve_template_source


class PackagePublishError(ValueError):
    """Raised when a confirmed candidate cannot form a publishable package."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def publish_package_candidates(
    template_candidate: TemplateParseCandidate,
    spec_candidate: SpecParseCandidate,
    *,
    document_type_id: str,
    published_by: str,
    renderer_version: str = "generic-docx-v1",
    rule_engine_version: str = "generic-check-v1",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build exact versioned aggregates without writing to storage."""
    if template_candidate.status != "confirmed":
        raise PackagePublishError("模板候选必须先确认")
    if spec_candidate.status != "confirmed":
        raise PackagePublishError("规范候选必须先确认")
    template_candidate = validate_template_candidate(template_candidate)
    spec_candidate = validate_spec_candidate(spec_candidate)
    blocking = [
        *[f"template:{issue.code}" for issue in template_candidate.issues if issue.severity == "error"],
        *[f"spec:{issue.code}" for issue in spec_candidate.issues if issue.severity == "error"],
    ]
    if blocking:
        raise PackagePublishError(f"候选存在阻断问题: {', '.join(blocking)}")

    source_asset = template_candidate.source_asset
    if source_asset is None or source_asset.scan_status != "validated":
        raise PackagePublishError("模板源文件必须经过上传安全校验")
    source_path = resolve_template_source(source_asset.storage_key)
    if source_path is None:
        raise PackagePublishError("模板源文件不存在或路径不安全")

    now = _now()
    template_version_id = f"{template_candidate.template_id}@{template_candidate.version}"
    template = DocumentTemplate(
        id=template_candidate.template_id,
        document_type_id=document_type_id,
        name=template_candidate.name,
        owner_id=published_by,
        status=DocumentTypeStatus.ACTIVE,
        current_version_id=template_version_id,
        created_at=now,
        updated_at=now,
        metadata={
            "source": "template_candidate",
            "candidate_id": template_candidate.candidate_id,
            "organization_context": {},
        },
    )
    template_version = TemplateVersion(
        id=template_version_id,
        template_id=template.id,
        version=template_candidate.version,
        status=VersionStatus.PUBLISHED,
        definition=template_candidate.definition,
        source_asset_id=source_asset.id,
        definition_hash=canonical_hash(template_candidate.definition),
        renderer_version=renderer_version,
        rule_engine_version=rule_engine_version,
        created_at=now,
        published_at=now,
        created_by=published_by,
        extensions={"source_asset": source_asset.model_dump(mode="json")},
    )

    spec_version_id = f"{spec_candidate.spec_set_id}@{spec_candidate.version}"
    spec_set = SpecSet(
        id=spec_candidate.spec_set_id,
        document_type_id=document_type_id,
        name=spec_candidate.name,
        owner_id=published_by,
        status="published",
        current_version_id=spec_version_id,
        metadata={
            "source": "spec_candidate",
            "candidate_id": spec_candidate.candidate_id,
        },
    )
    spec_payload = {
        "id": spec_version_id,
        "spec_set_id": spec_set.id,
        "version": spec_candidate.version,
        "status": "published",
        "description": spec_candidate.description,
        "effective_from": "",
        "effective_to": "",
        "rules": [rule.model_dump(mode="json") for rule in spec_candidate.rules],
        "source_asset_ids": [],
        "format_overrides": spec_candidate.format_overrides,
        "layout_overrides": spec_candidate.layout_overrides,
        "bindings": [],
        "metadata": {
            "source": "spec_candidate",
            "candidate_id": spec_candidate.candidate_id,
            "markdown_content": spec_candidate.markdown_content,
        },
    }
    spec_version = SpecVersion(**spec_payload, content_hash=canonical_hash(spec_payload))

    package_metadata = {
        **(metadata or {}),
        "source": "confirmed_candidates",
        "published_by": published_by,
        "published_at": now,
        "document_type_id": document_type_id,
    }
    package = DocumentPackage(
        id=f"package:{template_version_id}:{spec_version_id}:{renderer_version}:{rule_engine_version}",
        document_type_id=document_type_id,
        template_version_id=template_version.id,
        spec_version_id=spec_version.id,
        renderer_version=renderer_version,
        rule_engine_version=rule_engine_version,
        status=VersionStatus.PUBLISHED,
        published_at=now,
        published_by=published_by,
        metadata=package_metadata,
    )
    return {
        "package": package,
        "template": template,
        "template_version": template_version,
        "spec_set": spec_set,
        "spec_version": spec_version,
    }


__all__ = ["PackagePublishError", "publish_package_candidates"]
