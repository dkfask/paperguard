"""Common response contracts for asynchronous operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class JobResponse(BaseModel):
    """Public durable-job shape; internal lease details remain optional."""

    model_config = ConfigDict(extra="ignore")

    id: str
    job_type: str
    resource_type: str
    resource_id: str
    state: str
    attempt_count: int = 0
    max_attempts: int = 5
    available_at: str | None = None
    lease_until: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
    archive: dict[str, Any] | None = None
    export: dict[str, Any] | None = None


class AuditEventResponse(BaseModel):
    """Content-free audit projection exposed to governance screens."""

    id: str
    source: str = "workflow"
    actor_id: str = ""
    actor_display_name: str = ""
    action: str = ""
    resource_type: str = ""
    resource_id: str = ""
    organization_id: str = ""
    result: str = "success"
    reason: str = ""
    request_id: str = ""
    created_at: str = ""
