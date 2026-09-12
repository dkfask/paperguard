"""Operational controls: authenticated backups and retention configuration."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.templates import get_store
from app.api.workflow import _ensure_manager
from app.auth import current_user
from app.db import Store
from app.services.backup_restore import create_backup


router = APIRouter(prefix="/api/operations", tags=["operations"])
_RETENTION_KEY = "retention_policy"


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
    _ensure_manager(user)
    return _retention(store)


@router.put("/retention")
def set_retention(req: RetentionPolicyUpdate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    _ensure_manager(user)
    policy = req.model_dump(mode="json") | {"configured_by": user["id"]}
    store.set_system_setting(_RETENTION_KEY, json.dumps(policy, ensure_ascii=False))
    return {"configured": True, "policy": policy}


@router.post("/backup", status_code=200)
def download_backup(background_tasks: BackgroundTasks, store: Store = Depends(get_store), user: dict = Depends(current_user)):
    """Download a consistent backup; the temporary response file is removed after sending."""
    _ensure_manager(user)
    handle = tempfile.NamedTemporaryFile(prefix="paperguard-backup-", suffix=".zip", delete=False)
    output = Path(handle.name)
    handle.close()
    try:
        manifest = create_backup(output, db_path=store._db_path)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    background_tasks.add_task(output.unlink, missing_ok=True)
    return FileResponse(
        output, filename="paperguard-backup.zip", media_type="application/zip",
        headers={"X-PaperGuard-Backup-SHA256": digest, "X-PaperGuard-Manifest-Version": str(manifest["format_version"])},
        background=background_tasks,
    )
