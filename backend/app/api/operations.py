"""Operational controls: authenticated backups and retention configuration."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.templates import get_store
from app.auth import trusted_context
from app.core.context import RequestContext
from app.db import Store, _now
from app.services.backup_restore import create_backup
from app.services.saas_identity import check_platform


router = APIRouter(prefix="/api/operations", tags=["operations"])
_RETENTION_KEY = "retention_policy"


def approved_operator(ctx: RequestContext = Depends(trusted_context), store: Store = Depends(get_store)) -> RequestContext:
    if ctx.actor_kind != "platform" or not ctx.has_capability("operations:manage"):
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_OPERATION_ADMIN_REQUIRED", "message": "仅显式批准的平台管理员可执行平台级运维操作"})
    with store.workflow_transaction() as conn:
        check_platform(conn, ctx)
    return ctx


def _audit_operation(store: Store, conn, ctx: RequestContext, action: str, reason: str = "") -> None:
    store.save_audit_event_conn(conn, {
        "actor_id": ctx.actor_id,
        "action": action,
        "resource_type": "operations",
        "resource_id": action,
        "organization_id": "",
        "reason": reason,
        "request_id": ctx.request_id,
    })


class RetentionPolicyUpdate(BaseModel):
    draft_days: int = Field(ge=1, le=36500)
    attachment_days: int = Field(ge=1, le=36500)
    submission_days: int = Field(ge=1, le=36500)
    archive_days: int = Field(ge=1, le=36500)
    audit_days: int = Field(ge=1, le=36500)
    local_cache_days: int = Field(ge=1, le=36500)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)


def _retention(store: Store) -> dict[str, Any]:
    raw = store.get_system_setting(_RETENTION_KEY)
    if not raw:
        return {"configured": False, "policy": None}
    try:
        policy = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        policy = None
    return {"configured": isinstance(policy, dict), "policy": policy if isinstance(policy, dict) else None}


@router.get("/retention", dependencies=[Depends(approved_operator)])
def get_retention(store: Store = Depends(get_store), ctx: RequestContext = Depends(trusted_context)) -> dict[str, Any]:
    with store.workflow_transaction() as conn:
        check_platform(conn, ctx)
        return _retention(store)


@router.put("/retention", dependencies=[Depends(approved_operator)])
def set_retention(req: RetentionPolicyUpdate, store: Store = Depends(get_store), ctx: RequestContext = Depends(trusted_context)) -> dict[str, Any]:
    policy = req.model_dump(mode="json") | {"configured_by": ctx.actor_id}
    with store.workflow_transaction() as conn:
        check_platform(conn, ctx)
        conn.execute("INSERT OR REPLACE INTO system_settings (key, organization_id, value, updated_at) VALUES (?, '', ?, ?)", (_RETENTION_KEY, json.dumps(policy, ensure_ascii=False), _now()))
        _audit_operation(store, conn, ctx, "operations.retention.updated", "retention policy changed")
    return {"configured": True, "policy": policy}


@router.post("/backup", status_code=200, dependencies=[Depends(approved_operator)])
def download_backup(background_tasks: BackgroundTasks, store: Store = Depends(get_store), ctx: RequestContext = Depends(trusted_context)):
    """Download a consistent backup; the temporary response file is removed after sending."""
    handle = tempfile.NamedTemporaryFile(prefix="paperguard-backup-", suffix=".zip", delete=False)
    output = Path(handle.name)
    handle.close()
    try:
        with store.workflow_transaction() as conn:
            check_platform(conn, ctx)
        manifest = create_backup(output, db_path=store._db_path)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        # Snapshot I/O cannot share SQLite's writer lock. Recheck approval after
        # generation and discard the file on revocation before returning it.
        with store.workflow_transaction() as conn:
            check_platform(conn, ctx)
            _audit_operation(store, conn, ctx, "operations.backup.created", "platform backup generated")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    background_tasks.add_task(output.unlink, missing_ok=True)
    return FileResponse(
        output, filename="paperguard-backup.zip", media_type="application/zip",
        headers={"X-PaperGuard-Backup-SHA256": digest, "X-PaperGuard-Manifest-Version": str(manifest["format_version"])},
        background=background_tasks,
    )
