"""Governance audit center API."""

from __future__ import annotations

import csv
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth import current_user
from app.contracts.common import AuditEventResponse
from app.db import Store
from app.services.audit_service import list_audit_events


router = APIRouter(prefix="/api/audit", tags=["audit"])


def get_store() -> Store:
    """Use a read-only dependency; template routes lazily seed default data."""
    return Store()


def _ensure_audit_manager(user: dict) -> None:
    if user.get("role") not in {"admin", "college_admin"} or user.get("status") != "active":
        raise HTTPException(status_code=403, detail="仅学校管理员或学院管理员可查看审计记录")


@router.get("", response_model=list[AuditEventResponse])
def audit_center(
    limit: int = Query(default=100, ge=1, le=500),
    resource_type: str = Query(default="", max_length=80),
    action: str = Query(default="", max_length=120),
    actor_id: str = Query(default="", max_length=120),
    organization_id: str = Query(default="", max_length=120),
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict]:
    _ensure_audit_manager(user)
    return list_audit_events(
        store,
        user,
        limit=limit,
        resource_type=resource_type.strip(),
        action=action.strip(),
        actor_id=actor_id.strip(),
        organization_id=organization_id.strip(),
    )


@router.get("/export")
def export_audit_events(
    limit: int = Query(default=500, ge=1, le=500),
    resource_type: str = Query(default="", max_length=80),
    action: str = Query(default="", max_length=120),
    actor_id: str = Query(default="", max_length=120),
    organization_id: str = Query(default="", max_length=120),
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> Response:
    """Download the same content-free, organization-scoped audit projection."""
    _ensure_audit_manager(user)
    rows = list_audit_events(
        store,
        user,
        limit=limit,
        resource_type=resource_type.strip(),
        action=action.strip(),
        actor_id=actor_id.strip(),
        organization_id=organization_id.strip(),
    )
    buffer = StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "id",
            "source",
            "actor_id",
            "actor_display_name",
            "action",
            "resource_type",
            "resource_id",
            "organization_id",
            "result",
            "reason",
            "request_id",
            "created_at",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'},
    )
