"""Canonical hashes for versioned document-domain contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_payload(value: Any) -> Any:
    """Return a JSON-compatible payload with model values normalized."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def canonical_hash(value: Any) -> str:
    """Hash a JSON-compatible value without depending on dictionary order."""
    payload = canonical_payload(value)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def project_content_hash(project_data: dict[str, Any]) -> str:
    """Hash legacy project content while excluding volatile run metadata."""
    payload = dict(project_data)
    for key in ("revision", "updated_at", "check_run_ids"):
        payload.pop(key, None)
    return canonical_hash(payload)
