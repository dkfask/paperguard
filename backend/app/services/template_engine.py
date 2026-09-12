"""DOCX template parsing into a reviewable generic candidate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.documents.schemas import (
    TemplateAsset,
    TemplateDefinition,
    TemplateField,
    TemplateSection,
    StyleDefinition,
)
from app.services.import_template import (
    extract_meta_fields,
    extract_section_tree,
    import_template_styles,
)


class TemplateParseIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: str = "warning"
    message: str = Field(min_length=1)
    path: str = ""


class TemplateParseCandidate(BaseModel):
    """Parser output that must be reviewed before becoming a template version."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    filename: str = ""
    parser_version: str = "docx-template-engine@1"
    definition: TemplateDefinition
    source_asset: TemplateAsset | None = None
    issues: list[TemplateParseIssue] = Field(default_factory=list)
    status: str = "needs_confirmation"
    confirmed_by: str = ""
    confirmed_at: str = ""


class TemplateCandidateError(ValueError):
    """Raised when an unreviewable candidate is submitted for confirmation."""

    def __init__(self, issues: list[TemplateParseIssue]):
        self.issues = issues
        super().__init__("template candidate has blocking issues")


def _generic_section(raw: dict[str, Any], order: int) -> TemplateSection:
    known = {"key", "type", "title", "required", "children"}
    return TemplateSection(
        key=str(raw.get("key") or f"section-{order + 1}"),
        title=str(raw.get("title") or ""),
        kind=str(raw.get("type") or "content"),
        order=order,
        required=bool(raw.get("required", True)),
        children=[
            _generic_section(child, child_order)
            for child_order, child in enumerate(raw.get("children") or [])
        ],
        extensions={key: value for key, value in raw.items() if key not in known},
    )


def parse_docx_template(
    docx_path: str | Path,
    *,
    candidate_id: str,
    template_id: str,
    filename: str = "",
    name: str = "",
    source_asset: TemplateAsset | None = None,
) -> TemplateParseCandidate:
    """Parse a DOCX into a generic candidate without AI or persistence side effects."""
    path = Path(docx_path)
    imported_styles = import_template_styles(path)
    raw_sections = extract_section_tree(path)
    meta_fields = extract_meta_fields(path)
    issues: list[TemplateParseIssue] = []

    for style_name in dict.fromkeys(imported_styles.get("unmapped") or []):
        issues.append(TemplateParseIssue(
            code="unmapped_word_style",
            severity="warning",
            message=f"Word 样式未映射为标准模板样式: {style_name}",
            path="definition.styles",
        ))
    if not raw_sections:
        issues.append(TemplateParseIssue(
            code="sections_not_detected",
            severity="warning",
            message="未识别到标题章节；确认前需要人工补充章节结构",
            path="definition.sections",
        ))
    if not meta_fields:
        issues.append(TemplateParseIssue(
            code="fields_not_detected",
            severity="info",
            message="未识别到封面填写字段；如模板不需要字段可以保留",
            path="definition.fields",
        ))
    if not imported_styles.get("styles"):
        issues.append(TemplateParseIssue(
            code="styles_not_detected",
            severity="warning",
            message="未识别到可复用的段落样式；确认前需要人工指定样式",
            path="definition.styles",
        ))

    definition = TemplateDefinition(
        sections=[_generic_section(section, order) for order, section in enumerate(raw_sections)],
        fields=[
            TemplateField(
                key=field.key,
                label=field.label,
                type="text",
                required=field.required,
                location="metadata",
                source="docx.cover",
            )
            for field in meta_fields
        ],
        styles={
            key: StyleDefinition.model_validate(style.model_dump(mode="json"))
            for key, style in (imported_styles.get("styles") or {}).items()
        },
        source_docx=source_asset.storage_key if source_asset else "",
    )
    return TemplateParseCandidate(
        candidate_id=candidate_id,
        template_id=template_id,
        name=name or Path(filename).stem or "未命名模板",
        filename=filename,
        definition=definition,
        source_asset=source_asset,
        issues=issues,
    )


def validate_template_candidate(candidate: TemplateParseCandidate) -> TemplateParseCandidate:
    """Validate identity and references before a human can confirm a candidate."""
    issues = list(candidate.issues)
    seen_issue_keys = {(issue.code, issue.path) for issue in issues}

    def add(code: str, severity: str, message: str, path: str) -> None:
        if (code, path) not in seen_issue_keys:
            issues.append(TemplateParseIssue(code=code, severity=severity, message=message, path=path))
            seen_issue_keys.add((code, path))

    section_keys: set[str] = set()

    def walk(nodes: list[TemplateSection], parent: str = "") -> None:
        for node in nodes:
            path = f"definition.sections[{node.key}]"
            if node.key in section_keys:
                add("duplicate_section_key", "error", f"章节 key 重复: {node.key}", path)
            section_keys.add(node.key)
            if not node.title.strip():
                add("empty_section_title", "error", "章节标题不能为空", path)
            if node.parent_key and node.parent_key != parent:
                add("invalid_parent_key", "error", f"父章节引用不一致: {node.key}", path)
            walk(node.children, node.key)

    walk(candidate.definition.sections)
    if not section_keys:
        add("sections_required", "error", "至少需要一个章节才能生成填写/写作界面", "definition.sections")

    field_keys: set[str] = set()
    for field in candidate.definition.fields:
        path = f"definition.fields[{field.key}]"
        if field.key in field_keys:
            add("duplicate_field_key", "error", f"字段 key 重复: {field.key}", path)
        field_keys.add(field.key)
        if field.section_key and field.section_key not in section_keys:
            add("unknown_field_section", "error", f"字段引用未知章节: {field.section_key}", path)

    if candidate.source_asset is None:
        add("source_asset_required", "error", "确认模板必须保留源 DOCX asset", "source_asset")
    elif not candidate.source_asset.storage_key:
        add("source_asset_key_required", "error", "源 DOCX asset 缺少 storage key", "source_asset.storage_key")

    return candidate.model_copy(update={"issues": issues})


def confirm_template_candidate(
    candidate: TemplateParseCandidate,
    *,
    confirmed_by: str,
    confirmed_at: str,
) -> TemplateParseCandidate:
    """Return a confirmed copy; this function does not publish or persist it."""
    checked = validate_template_candidate(candidate)
    if any(issue.severity == "error" for issue in checked.issues):
        raise TemplateCandidateError(checked.issues)
    return checked.model_copy(update={
        "status": "confirmed",
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
    })


__all__ = [
    "TemplateCandidateError",
    "TemplateParseCandidate",
    "TemplateParseIssue",
    "confirm_template_candidate",
    "parse_docx_template",
    "validate_template_candidate",
]
