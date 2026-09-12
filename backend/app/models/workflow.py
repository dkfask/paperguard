"""论文指导业务闭环的稳定对象模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CohortCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    organization_id: str = Field(min_length=1)
    academic_year: str = ""
    owner_id: str = ""
    starts_at: str = ""
    ends_at: str = ""


class StageCreate(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    open_at: str = ""
    deadline_at: str = ""
    revision_days: int = Field(default=7, ge=0, le=365)
    prerequisite_stage_ids: list[str] = Field(default_factory=list)
    template_id: str = ""
    requirement_set_ids: list[str] = Field(default_factory=list)
    rule_packs: list[str] = Field(default_factory=list)
    required_materials: list[str] = Field(default_factory=list)
    required_manual_checks: list[str] = Field(default_factory=list)
    allow_early_write: bool = False
    is_final: bool = False


class StageVersionCreate(BaseModel):
    """已发布阶段的新配置版本；未提供的字段沿用当前版本。"""
    name: str | None = Field(default=None, min_length=1, max_length=120)
    open_at: str | None = None
    deadline_at: str | None = None
    revision_days: int | None = Field(default=None, ge=0, le=365)
    prerequisite_stage_ids: list[str] | None = None
    template_id: str | None = None
    requirement_set_ids: list[str] | None = None
    rule_packs: list[str] | None = None
    required_materials: list[str] | None = None
    required_manual_checks: list[str] | None = None
    allow_early_write: bool | None = None
    is_final: bool | None = None
    reason: str = Field(min_length=1, max_length=4000)
    base_revision: int | None = Field(default=None, ge=0)


class StageVersionPublishRequest(BaseModel):
    """发布配置版本，并可明确迁移指定的存量阶段任务。"""
    participant_ids: list[str] = Field(default_factory=list, max_length=2000)
    reason: str = Field(min_length=1, max_length=4000)


class MemberUpsert(BaseModel):
    # Public import flows may use business identifiers (student number, staff
    # number, username or display name). The service resolves them to IDs.
    student_id: str = ""
    teacher_id: str = ""
    student_ref: str = ""
    teacher_ref: str = ""
    reason: str = ""
    effective_from: str = ""


class MemberImportRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    dry_run: bool = False


class BindProjectRequest(BaseModel):
    project_id: str = ""
    title: str = ""


class CandidateCreate(BaseModel):
    participant_id: str = Field(min_length=1)
    modification_note: str = ""
    materials: list[str] = Field(default_factory=list)


class SubmissionConfirm(BaseModel):
    candidate_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=8, max_length=160)
    warning_acknowledged: bool = False


class ReviewDecision(BaseModel):
    decision: str = Field(pattern="^(returned|approved)$")
    reason: str = ""
    manual_confirmations: dict[str, bool] = Field(default_factory=dict)
    revision_deadline_at: str = ""


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    severity: str = Field(default="info", pattern="^(info|blocking)$")
    block_id: str = ""
    quoted_text: str = ""


class CommentResponse(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class ArchiveCreate(BaseModel):
    submission_id: str = Field(min_length=1)


class RetryRequest(BaseModel):
    reason: str = ""


class OutboxClaimRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    lease_seconds: int = Field(default=120, ge=10, le=900)
    consumer_id: str = Field(default="", max_length=120)


class OutboxAckRequest(BaseModel):
    lease_token: str = Field(default="", max_length=160)


class OutboxFailureRequest(BaseModel):
    error: str = Field(min_length=1, max_length=2000)
    lease_token: str = Field(default="", max_length=160)


class ExtensionRequest(BaseModel):
    effective_deadline_at: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ParticipantLifecycleRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)


class CohortLifecycleRequest(BaseModel):
    reason: str = Field(default="", max_length=4000)
    force: bool = False


def record_base(record_id: str, **values: Any) -> dict[str, Any]:
    return {"id": record_id, "revision": 0, **values}
