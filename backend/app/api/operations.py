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
from app.auth import current_user
from app.core.context import get_request_context
from app.db import Store
from app.services.backup_restore import create_backup


router = APIRouter(prefix="/api/operations", tags=["operations"])
_RETENTION_KEY = "retention_policy"


def _audit_operation(store: Store, user: dict, action: str, reason: str = "") -> None:
    context = get_request_context()
    store.save_audit_event({
        "actor_id": str(user.get("id") or ""),
        "action": action,
        "resource_type": "operations",
        "resource_id": action,
        "organization_id": context.tenant_id or "",
        "reason": reason,
        "request_id": context.request_id,
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


@router.get("/retention")
def get_retention(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    if user.get("role") != "admin" or user.get("status") != "active":
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_OPERATION_ADMIN_REQUIRED", "message": "仅平台管理员可执行平台级运维操作"})
    return _retention(store)


@router.put("/retention")
def set_retention(req: RetentionPolicyUpdate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    if user.get("role") != "admin" or user.get("status") != "active":
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_OPERATION_ADMIN_REQUIRED", "message": "仅平台管理员可执行平台级运维操作"})
    policy = req.model_dump(mode="json") | {"configured_by": user["id"]}
    store.set_system_setting(_RETENTION_KEY, json.dumps(policy, ensure_ascii=False))
    _audit_operation(store, user, "operations.retention.updated", "retention policy changed")
    return {"configured": True, "policy": policy}


@router.post("/backup", status_code=200)
def download_backup(background_tasks: BackgroundTasks, store: Store = Depends(get_store), user: dict = Depends(current_user)):
    """Download a consistent backup; the temporary response file is removed after sending."""
    if user.get("role") != "admin" or user.get("status") != "active":
        raise HTTPException(status_code=403, detail={"code": "PLATFORM_OPERATION_ADMIN_REQUIRED", "message": "仅平台管理员可执行平台级运维操作"})
    handle = tempfile.NamedTemporaryFile(prefix="paperguard-backup-", suffix=".zip", delete=False)
    output = Path(handle.name)
    handle.close()
    try:
        manifest = create_backup(output, db_path=store._db_path)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    _audit_operation(store, user, "operations.backup.created", "platform backup generated")
    background_tasks.add_task(output.unlink, missing_ok=True)
    return FileResponse(
        output, filename="paperguard-backup.zip", media_type="application/zip",
        headers={"X-PaperGuard-Backup-SHA256": digest, "X-PaperGuard-Manifest-Version": str(manifest["format_version"])},
        background=background_tasks,
    )
