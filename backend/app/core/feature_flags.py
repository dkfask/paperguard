"""Environment-backed rollout switches for additive product surfaces."""

from __future__ import annotations

import os

from fastapi import HTTPException


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def general_document_platform_enabled() -> bool:
    """Return whether the generic DOCX platform is available in this environment."""
    return _enabled("PAPERGUARD_FEATURE_GENERAL_DOCUMENT_PLATFORM", default=True)


def require_general_document_platform() -> None:
    if not general_document_platform_enabled():
        raise HTTPException(status_code=404, detail="通用文档平台尚未开放")


__all__ = ["general_document_platform_enabled", "require_general_document_platform"]
