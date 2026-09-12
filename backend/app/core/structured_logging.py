"""Small JSON-line logging helper with a strict non-content payload boundary."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.context import RequestContext, get_request_context


def log_event(logger: logging.Logger, event: str, *, context: RequestContext | None = None, **fields: Any) -> None:
    current = context or get_request_context()
    payload = {
        "event": event,
        "request_id": current.request_id,
        "actor_id": current.actor_id,
        "role": current.role,
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
