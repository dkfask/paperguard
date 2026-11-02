"""Compose confirmed template/spec candidates into an immutable package."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

from app.domain.documents.hashing import canonical_hash
from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentTemplate,
    DocumentTypeStatus,
    VersionStatus,
)
from app.domain.documents.schemas import TemplateVersion
from app.domain.documents.specs import SpecSet, SpecVersion, source_execution_to_spec_rules
from app.services.spec_engine import SpecParseCandidate, validate_spec_candidate
from app.services.template_engine import TemplateParseCandidate, validate_template_candidate
from app.services.template_admission import assess_template_definition
from app.services.template_source import resolve_template_source
from app.services.upload_security import validate_docx_bytes


class PackagePublishError(ValueError):
    """Raised when a confirmed candidate cannot form a publishable package."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _confirmed_template_content_rules(template_candidate, source_data: bytes) -> tuple[list[Any], dict[str, Any]]:
    """Load only source-bound, server-confirmed rules from a template profile."""
    definition = template_candidate.definition
    extensions = definition.extensions or {}
    specification = extensions.get("writing_specification") or {}
    explicit_instructions = any(
        isinstance(rule, dict) and rule.get("property") == "instruction.text"
        for rule in specification.get("rules", [])
    )
    raw_profile = extensions.get("source_execution")
    if not raw_profile:
        if explicit_instructions:
            raise PackagePublishError("Word 模板含有明确内容要求，请先预览并确认规则后再发布")
        return [], {}

    from app.services.source_template_profile import ProfileError, paper_from_definition, verify_profile

    try:
        verified_profile = verify_profile(
            paper_from_definition(template_candidate.template_id, template_candidate.version, definition),
            source_data,
        )
    except (ProfileError, ValueError, TypeError) as exc:
        raise PackagePublishError("Word 内容规范确认记录无效或与源模板不匹配，请重新预览确认") from exc

    rules = source_execution_to_spec_rules(
        definition,
        template_version_id=f"{template_candidate.template_id}@{template_candidate.version}",
        profile_sha256=str(verified_profile.get("profile_sha256") or ""),
    )
    rule_ids = [rule.id for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise PackagePublishError("Word 模板内容规则 ID 重复，不能发布")
    identity = {
        "source_sha256": verified_profile.get("source_sha256", ""),
        "specification_sha256": verified_profile.get("specification_sha256", ""),
        "profile_sha256": verified_profile.get("profile_sha256", ""),
        "compiler_version": verified_profile.get("content_rule_compiler_version", ""),
        "confirmed_rule_ids": list(verified_profile.get("confirmed_content_rule_ids") or []),
        "rule_ids": rule_ids,
    }
    return rules, identity


def publish_package_candidates(
    template_candidate: TemplateParseCandidate,
    spec_candidate: SpecParseCandidate | None,
    *,
    document_type_id: str,
    published_by: str,
    renderer_version: str = "generic-docx-v1",
    rule_engine_version: str = "generic-check-v1",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build exact versioned aggregates without writing to storage.

    A document type may have no separate specification. In that case the
    package still pins an explicit empty specification version so consumers do
    not need a second nullable reference.
    """
    if template_candidate.status != "confirmed":
        raise PackagePublishError("模板候选必须先确认")
    template_candidate = validate_template_candidate(template_candidate)
    if spec_candidate is not None:
        if spec_candidate.status != "confirmed":
            raise PackagePublishError("规范候选必须先确认")
        spec_candidate = validate_spec_candidate(spec_candidate)
    blocking = [
        *[f"template:{issue.code}" for issue in template_candidate.issues if issue.severity == "error"],
        *(
            [f"spec:{issue.code}" for issue in spec_candidate.issues if issue.severity == "error"]
            if spec_candidate is not None
            else []
        ),
    ]
    if blocking:
        raise PackagePublishError(f"候选存在阻断问题: {', '.join(blocking)}")

    source_asset = template_candidate.source_asset
    if source_asset is None or source_asset.scan_status != "validated":
        raise PackagePublishError("模板源文件必须经过上传安全校验")
    source_path = resolve_template_source(source_asset.storage_key)
    if source_path is None:
        raise PackagePublishError("模板源文件不存在或路径不安全")
    if source_asset.purpose != "template-source":
        raise PackagePublishError("模板源 asset 用途不合法")
    if source_asset.content_type != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        raise PackagePublishError("模板源 asset 类型不合法")
    if template_candidate.definition.source_docx != source_asset.storage_key:
        raise PackagePublishError("模板定义与源 asset 引用不一致")
    source_data = source_path.read_bytes()
    if source_asset.size_bytes != len(source_data):
        raise PackagePublishError("模板源 asset 大小校验失败")
    if not source_asset.checksum or source_asset.checksum != hashlib.sha256(source_data).hexdigest():
        raise PackagePublishError("模板源 asset checksum 校验失败")
    try:
        validate_docx_bytes(source_data)
    except Exception as exc:
        raise PackagePublishError("模板源文件内容校验失败") from exc
    admission = assess_template_definition(
        template_id=template_candidate.template_id,
        version=template_candidate.version,
        definition=template_candidate.definition,
        source_data=source_data,
        expected_source_sha256=source_asset.checksum,
    )
    if admission["blockers"]:
        raise PackagePublishError("模板准入评估发现当前检查器不支持的结构")
    if template_candidate.admission_assessment.get("assessment_hash") != admission["assessment_hash"]:
        raise PackagePublishError("模板定义或源文件已变化，请重新评估并确认此版本")

    template_content_rules, template_content_identity = _confirmed_template_content_rules(
        template_candidate,
        source_data,
    )

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
        extensions={
            "source_asset": source_asset.model_dump(mode="json"),
            "admission_assessment": admission,
        },
    )

    if spec_candidate is None:
        spec_set_id = f"no-spec:{template_candidate.template_id}"
        spec_version = "1"
        spec_name = "无额外规范"
        spec_description = "该文档未提供单独规范，不执行额外规则。"
        spec_rules: list[dict[str, Any]] = []
        spec_format_overrides: dict[str, dict[str, Any]] = {}
        spec_layout_overrides: dict[str, float] = {}
        spec_metadata = {
            "source": "no_spec",
            "optional": True,
        }
    else:
        spec_set_id = spec_candidate.spec_set_id
        spec_version = spec_candidate.version
        spec_name = spec_candidate.name
        spec_description = spec_candidate.description
        spec_rules = [rule.model_dump(mode="json") for rule in spec_candidate.rules]
        spec_format_overrides = spec_candidate.format_overrides
        spec_layout_overrides = spec_candidate.layout_overrides
        spec_metadata = {
            "source": "spec_candidate",
            "candidate_id": spec_candidate.candidate_id,
            "markdown_content": spec_candidate.markdown_content,
        }

    if template_content_rules:
        existing_rule_ids = {str(item.get("id") or "") for item in spec_rules}
        duplicate_ids = existing_rule_ids & {rule.id for rule in template_content_rules}
        if duplicate_ids:
            raise PackagePublishError("单独规范与 Word 模板内容要求存在重复规则 ID")
        spec_rules = [*spec_rules, *(rule.model_dump(mode="json") for rule in template_content_rules)]
        spec_metadata = {
            **spec_metadata,
            "template_content_requirements": template_content_identity,
        }
        if spec_candidate is None:
            spec_set_id = f"template-spec:{template_candidate.template_id}"
            spec_version = template_candidate.version
            spec_name = f"{template_candidate.name} 内容要求"
            spec_description = "从教师确认的 Word 模板要求编译，并绑定模板版本、源文件和确认记录。"
        else:
            # The package consumes a composed immutable spec. Distinguishing it
            # from the standalone spec version prevents another template from
            # overwriting this template's rule composition under the same ID.
            spec_set_id = f"{spec_candidate.spec_set_id}:template:{template_candidate.template_id}"
            spec_version = f"{spec_candidate.version}+{template_candidate.version}"
            spec_name = f"{spec_candidate.name} + {template_candidate.name} 内容要求"
            spec_description = f"{spec_candidate.description}\n\nWord 模板内容要求已绑定。".strip()

    spec_version_id = f"{spec_set_id}@{spec_version}"
    spec_set = SpecSet(
        id=spec_set_id,
        document_type_id=document_type_id,
        name=spec_name,
        owner_id=published_by,
        status="published",
        current_version_id=spec_version_id,
        metadata={
            **spec_metadata,
        },
    )
    spec_payload = {
        "id": spec_version_id,
        "spec_set_id": spec_set.id,
        "version": spec_version,
        "status": "published",
        "description": spec_description,
        "effective_from": "",
        "effective_to": "",
        "rules": spec_rules,
        "source_asset_ids": [],
        "format_overrides": spec_format_overrides,
        "layout_overrides": spec_layout_overrides,
        "bindings": [],
        "metadata": spec_metadata,
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
