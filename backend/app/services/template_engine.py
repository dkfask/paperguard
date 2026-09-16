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
from app.services.docx_anchors import extract_docx_anchors
from app.services.import_template import (
    extract_cover_blocks,
    extract_document_structure,
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
    version: str = Field(default="1", min_length=1)
    filename: str = ""
    parser_version: str = "docx-template-engine@2"
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
    source_document = extract_document_structure(path)
    raw_sections = extract_section_tree(path)
    anchors = extract_docx_anchors(path)
    meta_fields = extract_meta_fields(path)
    issues: list[TemplateParseIssue] = []

    section_anchor_keys = list(dict.fromkeys(
        anchor.section_key or anchor.key
        for anchor in anchors
        if anchor.kind in {"section_placeholder", "section_bookmark", "section_content_control"}
    ))
    if not raw_sections and section_anchor_keys:
        raw_sections = [
            {
                "key": key,
                "type": "content",
                "title": key.replace("_", " ").replace("-", " ").title(),
                "required": True,
                "children": [],
            }
            for key in section_anchor_keys
        ]

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
    if not anchors:
        issues.append(TemplateParseIssue(
            code="stable_anchors_not_detected",
            severity="info" if source_document["table_count"] else "warning",
            message=(
                "未识别到明确的 DOCX 字段/章节锚点；已识别源文档表格，确认后将保留源模板结构"
                if source_document["table_count"]
                else "未识别到明确的 DOCX 字段/章节锚点；导出将使用兼容渲染器，无法保证陌生模板的原位保真"
            ),
            path="definition.anchors",
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

    fields_by_key: dict[str, TemplateField] = {
        field.key: TemplateField(
            key=field.key,
            label=field.label,
            type="text",
            required=field.required,
            location="metadata",
            source="docx.cover",
        )
        for field in meta_fields
    }
    for anchor in anchors:
        if anchor.kind in {"section_placeholder", "section_bookmark", "section_content_control"}:
            continue
        existing = fields_by_key.get(anchor.key)
        if existing is None:
            fields_by_key[anchor.key] = TemplateField(
                key=anchor.key,
                label=anchor.key.replace("_", " ").replace("-", " ").title(),
                type="text",
                required=anchor.required,
                location="anchor",
                constraints={"anchor_kind": anchor.kind, "anchor_selector": anchor.selector},
                source="docx.anchor",
            )
        elif anchor.required and not existing.required:
            fields_by_key[anchor.key] = existing.model_copy(update={"required": True})

    definition = TemplateDefinition(
        sections=[_generic_section(section, order) for order, section in enumerate(raw_sections)],
        fields=list(fields_by_key.values()),
        anchors=anchors,
        styles={
            key: StyleDefinition.model_validate(style.model_dump(mode="json"))
            for key, style in (imported_styles.get("styles") or {}).items()
        },
        source_docx=source_asset.storage_key if source_asset else "",
        cover_blocks=[block.model_dump(mode="json") for block in extract_cover_blocks(path)],
        extensions={"source_document": source_document},
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

    if candidate.definition.mode not in {"fixed_form", "structured_document", "hybrid"}:
        add("unsupported_document_mode", "error", f"不支持的文档模式: {candidate.definition.mode}", "definition.mode")
    if candidate.definition.export_strategy not in {"auto", "source_template", "legacy_adapter"}:
        add("unsupported_export_strategy", "error", f"不支持的导出策略: {candidate.definition.export_strategy}", "definition.export_strategy")
    if candidate.definition.export_strategy == "source_template" and not candidate.definition.anchors:
        add("source_template_anchor_required", "error", "源模板导出策略必须至少配置一个稳定 DOCX 锚点", "definition.anchors")

    section_keys: set[str] = set()

    def walk(nodes: list[TemplateSection], parent: str = "") -> None:
        for node in nodes:
            path = f"definition.sections[{node.key}]"
            if node.key in section_keys:
                add("duplicate_section_key", "error", f"章节 key 重复: {node.key}", path)
            section_keys.add(node.key)
            if not node.title.strip():
                add("empty_section_title", "error", "章节标题不能为空", path)
            if node.style_ref and node.style_ref not in candidate.definition.styles:
                add("unknown_section_style", "error", f"章节引用未知样式: {node.style_ref}", f"{path}.style_ref")
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
        if field.type.lower() in {"select", "enum"}:
            options = field.constraints.get("options") or field.constraints.get("enum")
            if not isinstance(options, list) or not options:
                add("choice_options_required", "error", "选择字段至少需要一个选项", f"{path}.constraints.options")
            elif len({str(option.get("value", option.get("key", ""))) if isinstance(option, dict) else str(option) for option in options}) != len(options):
                add("duplicate_choice_option", "error", "选择字段的选项不能重复", f"{path}.constraints.options")

    anchor_keys: set[tuple[str, str, str, int]] = set()
    allowed_anchor_kinds = {
        "placeholder", "section_placeholder", "content_control", "section_content_control",
        "bookmark", "section_bookmark", "merge_field",
    }
    for anchor in candidate.definition.anchors:
        path = f"definition.anchors[{anchor.kind}:{anchor.selector}]"
        identity = (anchor.part, anchor.kind, anchor.selector, anchor.occurrence)
        if identity in anchor_keys:
            add("duplicate_anchor", "error", f"DOCX 锚点重复: {anchor.selector}", path)
        anchor_keys.add(identity)
        if anchor.kind not in allowed_anchor_kinds:
            add("unsupported_anchor_kind", "error", f"不支持的 DOCX 锚点类型: {anchor.kind}", path)
        if not anchor.part.startswith("word/") or not anchor.part.endswith(".xml") or anchor.part.endswith(".rels"):
            add("invalid_anchor_part", "error", f"DOCX 锚点部件不合法: {anchor.part}", path)
        if anchor.kind in {"section_placeholder", "section_bookmark", "section_content_control"}:
            section_key = anchor.section_key or anchor.key
            if section_key not in section_keys:
                add("unknown_anchor_section", "error", f"锚点引用未知章节: {section_key}", path)
        elif anchor.key not in field_keys:
            add("unknown_anchor_field", "error", f"锚点引用未知字段: {anchor.key}", path)

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
