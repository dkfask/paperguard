"""PaperGuard 多层级规范模型。

规范集合与论文模板保持独立。模板只负责排版，规范集合负责内容要求、人工清单
和参考文档；项目创建时把二者解析成不可变快照。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RequirementLevel(str, Enum):
    SCHOOL = "school"
    COLLEGE = "college"
    MAJOR = "major"
    COURSE = "course"
    BATCH = "batch"
    TEACHER = "teacher"
    PROJECT = "project"


class RequirementStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class RequirementType(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    DOC = "doc"


class RequirementSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class RequirementEnforce(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class RequirementDocument(BaseModel):
    """规范参考文档元数据；二进制内容保存在受控数据目录。"""

    id: str = Field(default="", min_length=0)
    filename: str = ""
    version: str = ""
    summary: str = ""
    sort_order: int = 0
    storage_key: str = ""
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(default=0, ge=0)
    checksum: str = ""


class RequirementBinding(BaseModel):
    """将规范绑定到组织、批次、课程、教师或项目作用域。"""

    id: str = Field(min_length=1)
    scope_type: str = "global"
    scope_id: str = ""
    priority: int = 0
    enabled: bool = True
    note: str = ""


class RequirementItem(BaseModel):
    """一条自动检查、人工检查或参考文档要求。"""

    id: str = Field(min_length=1)
    type: RequirementType = RequirementType.AUTO
    target: str = "document"
    checker: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    severity: RequirementSeverity = RequirementSeverity.WARNING
    enforce: RequirementEnforce = RequirementEnforce.SOFT
    description: str = ""
    source: str = ""
    rule_pack_id: str = ""
    rule_pack_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("description", mode="before")
    @classmethod
    def default_description(cls, value: Any) -> str:
        return str(value or "")


class RequirementSet(BaseModel):
    """可发布、可绑定、版本化的一组写作要求。"""

    id: str = Field(min_length=1)
    family_id: str = ""  # 同一规范的多个不可变版本共享一个 family_id
    name: str = Field(min_length=1)
    level: RequirementLevel = RequirementLevel.SCHOOL
    scope: str = "global"
    scope_id: str = ""
    owner_id: str | None = None
    status: RequirementStatus = RequirementStatus.DRAFT
    version: str = "1"
    description: str = ""
    markdown_content: str = ""
    effective_from: str = ""
    effective_to: str = ""
    course_id: str = ""
    stage: str = ""
    deadline_at: str = ""
    applies_to: dict[str, Any] = Field(default_factory=dict)
    # 规范解析出的排版约束；项目生效时覆盖模板同名样式字段。
    format_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # 规范解析出的文档级页面约束；项目导出时覆盖模板页面设置。
    layout_overrides: dict[str, float] = Field(default_factory=dict)
    items: list[RequirementItem] = Field(default_factory=list)
    documents: list[RequirementDocument] = Field(default_factory=list)
    bindings: list[RequirementBinding] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class WaiverStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"


class Waiver(BaseModel):
    id: str = Field(min_length=1)
    requirement_item_id: str = Field(min_length=1)
    status: WaiverStatus = WaiverStatus.PENDING
    reason: str = Field(min_length=1)
    requested_by: str = ""
    requested_at: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    review_note: str = ""
    effective_from: str = ""
    effective_to: str = ""
    revoked_by: str = ""
    revoked_at: str = ""
    revoke_reason: str = ""


class DisputeStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RequirementDispute(BaseModel):
    """对规范适用性或检查结果提出异议的留痕记录；接受申诉不等同于豁免。"""

    id: str = Field(min_length=1)
    requirement_item_id: str = Field(min_length=1)
    status: DisputeStatus = DisputeStatus.PENDING
    reason: str = Field(min_length=1)
    requested_by: str = ""
    requested_at: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    review_note: str = ""


class RequirementAcknowledgement(BaseModel):
    id: str = Field(min_length=1)
    user_id: str = ""
    snapshot_hash: str = ""
    acknowledged_at: str = ""


class RequirementUpgrade(BaseModel):
    id: str = Field(min_length=1)
    from_snapshot_hash: str = ""
    to_snapshot_hash: str = ""
    changed_policy_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    operated_by: str = ""
    operated_at: str = ""


class CheckRun(BaseModel):
    id: str = Field(min_length=1)
    project_id: str = ""
    snapshot_hash: str = ""
    content_hash: str = ""
    engine_version: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    performed_by: str = ""
    created_at: str = ""


class PolicyAuditEvent(BaseModel):
    id: str = Field(min_length=1)
    policy_id: str = ""
    action: str = ""
    actor_id: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""
    created_at: str = ""
