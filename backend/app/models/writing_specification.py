"""Source-bound observations and reviewable writing rules extracted from DOCX."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SpecificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    part: str
    path: str
    role: str
    region: str = ""
    text: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)
    property_sources: dict[str, list[str]] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class WritingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scope: dict[str, str]
    property: str
    expected: Any
    evidence_kind: Literal["observed_pattern", "single_observation", "explicit_instruction"]
    confidence: Literal["consistent", "single", "conflict", "needs_review"]
    evidence_node_ids: list[str]
    supporting_count: int = 0
    observed_count: int = 0
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    decision: Literal["pending"] = "pending"
    enforcement: Literal["suggestion"] = "suggestion"


class SpecificationReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    node_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)


class WritingSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    extractor_version: str = "word-writing-spec@2"
    source_sha256: str
    specification_sha256: str = ""
    status: Literal["needs_confirmation"] = "needs_confirmation"
    nodes: list[SpecificationEvidence] = Field(default_factory=list)
    rules: list[WritingRule] = Field(default_factory=list)
    review_items: list[SpecificationReviewItem] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
