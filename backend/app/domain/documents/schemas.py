"""Generic document-domain contracts used by compatibility adapters.

These models describe the stable shape introduced in Phase 1. They do not
persist anything and deliberately keep legacy-specific details in extensions
so existing PaperGuard data can be mapped without loss.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DocumentTypeStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class VersionStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class TemplateAsset(BaseModel):
    """A controlled file reference; bytes remain outside the domain model."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(default=0, ge=0)
    checksum: str = ""
    purpose: str = "template"
    scan_status: str = "unknown"


class StyleDefinition(BaseModel):
    """Normalized style properties with room for renderer-specific extensions."""

    model_config = ConfigDict(extra="allow")

    font_zh: str = ""
    font_en: str = ""
    size_pt: float = 12.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "justify"
    line_spacing: float = 1.5
    line_spacing_rule: str = "auto"
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    first_line_indent_chars: float = 0.0
    hanging_indent_cm: float = 0.0
    color: str = ""
    highlight: str | None = None
    vert_align: str | None = None
    page_break_before: bool = False


class TemplateField(BaseModel):
    """A user-entered value declared by a template."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = ""
    type: str = "text"
    required: bool = False
    location: str = "metadata"
    section_key: str = ""
    default: Any = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    source: str = ""


class TemplateSection(BaseModel):
    """A recursive section tree independent of academic-thesis semantics."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    title: str = ""
    kind: str = "content"
    order: int = Field(default=0, ge=0)
    parent_key: str = ""
    required: bool = True
    repeatable: bool = False
    # Existing generic projects always exposed an add-block affordance; keep
    # that behavior for snapshots created before these controls existed.
    allow_user_add: bool = True
    allow_user_delete: bool = False
    style_ref: str = ""
    style: StyleDefinition | None = None
    fields: list[str] = Field(default_factory=list)
    content_schema: dict[str, Any] = Field(default_factory=dict)
    children: list["TemplateSection"] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class TemplateAnchor(BaseModel):
    """A deterministic location in a DOCX template.

    Anchors are metadata only; the renderer resolves them against the frozen
    source DOCX.  Keeping the selector and part explicit makes the mapping
    reviewable and prevents a renderer from guessing against arbitrary text.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    selector: str = Field(min_length=1)
    part: str = Field(default="word/document.xml", min_length=1)
    occurrence: int = Field(default=1, ge=1)
    required: bool = False
    section_key: str = ""


class TemplateDefinition(BaseModel):
    """The normalized, versioned definition used to build an editor."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["fixed_form", "structured_document", "hybrid"] = "hybrid"
    export_strategy: Literal["auto", "source_template", "legacy_adapter"] = "auto"
    sections: list[TemplateSection] = Field(default_factory=list)
    fields: list[TemplateField] = Field(default_factory=list)
    anchors: list[TemplateAnchor] = Field(default_factory=list)
    styles: dict[str, StyleDefinition] = Field(default_factory=dict)
    page_layout: dict[str, float] = Field(default_factory=dict)
    allow_structure_edit: bool = True
    source_docx: str = ""
    cover_blocks: list[dict[str, Any]] = Field(default_factory=list)
    rule_packs: list[str] = Field(default_factory=list)
    requirement_set_ids: list[str] = Field(default_factory=list)
    organization_context: dict[str, str] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class DocumentType(BaseModel):
    """A product-level document category such as thesis or application."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: DocumentTypeStatus = DocumentTypeStatus.ACTIVE
    capabilities: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentTemplate(BaseModel):
    """A logical template whose published content lives in versions."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    document_type_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    owner_id: str = ""
    organization_context: dict[str, str] = Field(default_factory=dict)
    status: DocumentTypeStatus = DocumentTypeStatus.ACTIVE
    current_version_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TemplateVersion(BaseModel):
    """An immutable template definition and its execution compatibility."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: VersionStatus = VersionStatus.DRAFT
    definition: TemplateDefinition
    source_asset_id: str = ""
    definition_hash: str = ""
    schema_version: str = "1"
    renderer_version: str = "legacy-docx-v1"
    rule_engine_version: str = "legacy-check-v1"
    created_at: str = ""
    published_at: str = ""
    created_by: str = ""
    extensions: dict[str, Any] = Field(default_factory=dict)


class DocumentPackage(BaseModel):
    """Published composition of exact template, spec, and execution versions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    document_type_id: str = Field(min_length=1)
    template_version_id: str = Field(min_length=1)
    spec_version_id: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    rule_engine_version: str = Field(min_length=1)
    status: VersionStatus = VersionStatus.DRAFT
    published_at: str = ""
    published_by: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentBlock(BaseModel):
    """A generic user-authored block; renderers interpret its declared kind."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(default="paragraph", min_length=1, max_length=80)
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    order: int = Field(default=0, ge=0)


class DocumentProject(BaseModel):
    """A user-authored instance pinned to one immutable document package."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    document_type_id: str = Field(min_length=1)
    template_version_id: str = Field(min_length=1)
    spec_version_id: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    rule_engine_version: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    title: str = ""
    status: str = "draft"
    revision: int = Field(default=0, ge=0)
    field_values: dict[str, Any] = Field(default_factory=dict)
    sections: dict[str, list[ContentBlock]] = Field(default_factory=dict)
    organization_context: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class ValidationFinding(BaseModel):
    """A deterministic result produced from one frozen specification rule."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    checker: str = ""
    severity: str = "warning"
    enforce: str = "soft"
    message: str = Field(min_length=1)
    actual: Any = None
    expected: Any = None
    section_key: str = ""
    fixable: bool = False
    source: str = ""
    source_version: str = ""


class ValidationRun(BaseModel):
    """Immutable validation evidence for one project revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    template_version_id: str = Field(min_length=1)
    spec_version_id: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    rule_engine_version: str = Field(min_length=1)
    project_revision: int = Field(default=0, ge=0)
    content_hash: str = ""
    status: str = "completed"
    summary: dict[str, Any] = Field(default_factory=dict)
    findings: list[ValidationFinding] = Field(default_factory=list)
    not_checked: list[dict[str, Any]] = Field(default_factory=list)
    performed_by: str = ""
    created_at: str = ""


class RenderJob(BaseModel):
    """Durable rendering request with immutable execution inputs."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    project_revision: int = Field(default=0, ge=0)
    content_hash: str = ""
    snapshot_hash: str = ""
    template_version_id: str = Field(min_length=1)
    spec_version_id: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    rule_engine_version: str = Field(min_length=1)
    force: bool = False
    status: str = "queued"
    job_id: str = Field(min_length=1)
    artifact_id: str = ""
    requested_by: str = ""
    attempt_count: int = Field(default=0, ge=0)
    error_code: str = ""
    last_error: str = ""
    snapshot: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(default=0, ge=0)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    failed_at: str = ""
    cancelled_at: str = ""
    recovery_reason: str = ""


class Artifact(BaseModel):
    """Metadata for a generated file stored behind a controlled key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    render_job_id: str = Field(min_length=1)
    kind: str = "docx"
    storage_key: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(default=0, ge=0)
    checksum: str = ""
    status: str = "available"
    renderer_version: str = ""
    created_at: str = ""
    expires_at: str = ""


TemplateSection.model_rebuild()
