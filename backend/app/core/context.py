"""Request-scoped identity and correlation context.

The context deliberately contains identifiers and control metadata only. It
never stores request bodies, document text, bearer tokens, or API keys.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class RequestContext:
    actor_id: str = ""
    role: str = ""
    organization_ids: tuple[str, ...] = field(default_factory=tuple)
    school_id: str = ""
    # SaaS identity is explicit and separate from the legacy organization
    # context.  These values are authorization evidence only after they have
    # been resolved from the server-side membership record.
    tenant_id: str = ""
    membership_id: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    auth_version: int = 0
    request_id: str = ""
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


_current_context: ContextVar[RequestContext] = ContextVar(
    "paperguard_request_context", default=RequestContext()
)


def set_request_context(context: RequestContext) -> Token[RequestContext]:
    return _current_context.set(context)


def reset_request_context(token: Token[RequestContext]) -> None:
    _current_context.reset(token)


def get_request_context() -> RequestContext:
    return _current_context.get()


def _string_tuple(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in (values or ()) if value}))


def context_for_user(
    user: dict[str, Any],
    *,
    request_id: str = "",
    membership: dict[str, Any] | None = None,
) -> RequestContext:
    organization = user.get("organization_context") or {}
    organization_ids = tuple(
        str(value) for key, value in organization.items()
        if key.endswith("_id") and value
    )
    school_id = str(organization.get("school_id") or "")
    from app.services.tenant_access import effective_capabilities

    capabilities = effective_capabilities(user, membership)
    membership = membership or {}
    raw_version = membership.get("auth_version", user.get("auth_version", 0))
    try:
        auth_version = int(raw_version or 0)
    except (TypeError, ValueError):
        auth_version = 0
    return RequestContext(
        actor_id=str(user.get("id") or ""),
        role=str(membership.get("role") or user.get("role") or ""),
        organization_ids=organization_ids,
        school_id=school_id,
        tenant_id=str(membership.get("tenant_id") or ""),
        membership_id=str(membership.get("id") or ""),
        capabilities=_string_tuple(capabilities),
        auth_version=auth_version,
        request_id=request_id,
        locale=str(organization.get("locale") or "zh-CN"),
        timezone=str(organization.get("timezone") or "Asia/Shanghai"),
    )
