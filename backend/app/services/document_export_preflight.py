"""Deterministic checks that must pass before a generic DOCX export starts."""

from __future__ import annotations

import hashlib
from typing import Any

from app.domain.documents.schemas import DocumentPackage, DocumentProject, DocumentTemplate, TemplateVersion
from app.services.document_project_engine import (
    DocumentProjectError,
    validate_field_values,
    validate_section_content,
)
from app.services.template_source import resolve_template_source
from app.services.upload_security import DOCX_MAX_BYTES, validate_docx_bytes


def _issue(code: str, message: str, path: str = "") -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def run_document_export_preflight(
    project: DocumentProject,
    package: DocumentPackage,
    template: DocumentTemplate,
    template_version: TemplateVersion,
) -> dict[str, Any]:
    """Return an auditable result without invoking a renderer or AI service."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    definition = template_version.definition

    if project.package_id != package.id:
        errors.append(_issue("package_mismatch", "项目未绑定当前文档包", "project.package_id"))
    if package.document_type_id != template.document_type_id:
        errors.append(_issue("document_type_mismatch", "文档包与模板文档类型不一致", "package.document_type_id"))
    if package.template_version_id != template_version.id:
        errors.append(_issue("package_template_mismatch", "文档包与模板版本不一致", "package.template_version_id"))
    if project.template_version_id != template_version.id:
        errors.append(_issue("template_version_mismatch", "项目未绑定当前模板版本", "project.template_version_id"))
    if project.renderer_version != package.renderer_version:
        errors.append(_issue("renderer_mismatch", "项目渲染器版本与文档包不一致", "project.renderer_version"))
    if project.rule_engine_version != package.rule_engine_version:
        errors.append(_issue("rule_engine_mismatch", "项目规则引擎版本与文档包不一致", "project.rule_engine_version"))

    try:
        validate_field_values(definition, project.field_values)
        validate_section_content(definition, project.sections, project.id)
    except DocumentProjectError as exc:
        errors.append(_issue("project_schema_invalid", str(exc), "project"))

    source = resolve_template_source(definition.source_docx)
    if source is None:
        errors.append(_issue("source_docx_missing", "源 DOCX 不存在或路径不安全", "definition.source_docx"))
    else:
        try:
            if source.stat().st_size > DOCX_MAX_BYTES:
                errors.append(_issue("source_docx_too_large", "源 DOCX 超过大小上限", "definition.source_docx"))
            else:
                source_bytes = source.read_bytes()
                validate_docx_bytes(source_bytes)
                source_asset = (template_version.extensions or {}).get("source_asset") or {}
                if source_asset.get("storage_key") and source_asset["storage_key"] != definition.source_docx:
                    errors.append(_issue("source_asset_mismatch", "模板源 asset 与模板定义不一致", "template_version.source_asset_id"))
                expected_checksum = str(source_asset.get("checksum") or "")
                if expected_checksum and hashlib.sha256(source_bytes).hexdigest() != expected_checksum:
                    errors.append(_issue("source_checksum_mismatch", "源 DOCX checksum 校验失败", "template_version.source_asset_id"))
        except Exception:
            errors.append(_issue("source_docx_invalid", "源 DOCX 内容校验失败", "definition.source_docx"))

    if definition.export_strategy == "source_template" and not definition.anchors:
        errors.append(_issue("source_template_anchor_required", "源模板导出策略必须配置稳定锚点", "definition.anchors"))
    if definition.export_strategy == "auto" and not definition.anchors:
        warnings.append(_issue("legacy_adapter_selected", "模板没有稳定锚点，将使用论文兼容适配器", "definition.anchors"))

    return {"ok": not errors, "errors": errors, "warnings": warnings}


__all__ = ["run_document_export_preflight"]
