"""Request-scoped identity and correlation context.

The context deliberately contains identifiers and control metadata only. It
never stores request bodies, document text, bearer tokens, or API keys.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RequestContext:
    actor_id: str = ""
    role: str = ""
    organization_ids: tuple[str, ...] = field(default_factory=tuple)
    school_id: str = ""
    request_id: str = ""
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"


_current_context: ContextVar[RequestContext] = ContextVar(
    "paperguard_request_context", default=RequestContext()
)


def set_request_context(context: RequestContext) -> Token[RequestContext]:
    return _current_context.set(context)


def reset_request_context(token: Token[RequestContext]) -> None:
    _current_context.reset(token)


def get_request_context() -> RequestContext:
    return _current_context.get()


def context_for_user(user: dict[str, Any], *, request_id: str = "") -> RequestContext:
    organization = user.get("organization_context") or {}
    organization_ids = tuple(
        str(value) for key, value in organization.items()
        if key.endswith("_id") and value
    )
    school_id = str(organization.get("school_id") or "")
    return RequestContext(
        actor_id=str(user.get("id") or ""),
        role=str(user.get("role") or ""),
        organization_ids=organization_ids,
        school_id=school_id,
        request_id=request_id,
        locale=str(organization.get("locale") or "zh-CN"),
        timezone=str(organization.get("timezone") or "Asia/Shanghai"),
    )
