"""Immutable authorization identity; neutral defaults are never grants.

Only identifiers/control metadata belong here, never content or credentials.
Legacy logging identities are distinct from resolved shared-SaaS identities.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class RequestContext:
    actor_kind: Literal["unresolved", "member", "platform", "support"] = "unresolved"
    actor_id: str = ""
    role: str = ""
    organization_ids: tuple[str, ...] = field(default_factory=tuple)
    school_id: str = ""
    tenant_id: str | None = None
    membership_id: str | None = None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    auth_version: int = 0
    support_grant_id: str | None = None
    request_id: str = ""
    trace_id: str = ""
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        # CI-4: snapshot mutable caller inputs; frozen alone is not deep immutability.
        for name in ("capabilities", "organization_ids"):
            raw = getattr(self, name)
            if isinstance(raw, str):
                raise ValueError("CI-4 invalid identity collection")
            values = tuple(raw)
            if any(not isinstance(v, str) or not v for v in values):
                raise ValueError("CI-4 invalid identity collection")
            object.__setattr__(self, name, tuple(sorted(set(values))))
        if self.actor_kind == "member":
            # CI-1: a role or a client hint cannot replace persisted member identity.
            if not (self.actor_id and self.tenant_id and self.membership_id) or type(self.auth_version) is not int or self.auth_version < 1:
                raise ValueError("CI-1 incomplete member identity")
            if self.support_grant_id is not None:
                raise ValueError("CI-1 member cannot carry a support grant")
        elif self.actor_kind == "platform":
            # CI-2: platform identity is deliberately not a tenant impersonation.
            if not self.actor_id or self.tenant_id is not None or self.membership_id is not None or self.support_grant_id is not None or self.organization_ids or self.school_id:
                raise ValueError("CI-2 invalid platform scope")
        elif self.actor_kind == "support":
            # CI-3: grant persistence/expiry/revocation is not implemented yet.
            # Reject all support identities instead of trusting an arbitrary grant ID.
            raise ValueError("CI-3 support grants are not implemented")
        elif self.actor_kind == "unresolved":
            # CI-5: import-safe defaults have no identity, scope or authority.
            if self.actor_id or self.role or self.tenant_id is not None or self.membership_id is not None or self.capabilities or self.organization_ids or self.school_id or self.auth_version or self.support_grant_id is not None:
                raise ValueError("CI-5 unresolved context must be neutral")
        else:
            raise ValueError("CI-5 unknown actor kind")

    def has_capability(self, capability: str) -> bool:
        return self.actor_kind in {"member", "platform"} and capability in self.capabilities


_current_context: ContextVar[RequestContext] = ContextVar(
    "paperguard_request_context", default=RequestContext()
)


def set_request_context(context: RequestContext) -> Token[RequestContext]:
    return _current_context.set(context)


def reset_request_context(token: Token[RequestContext]) -> None:
    _current_context.reset(token)


def get_request_context() -> RequestContext:
    return _current_context.get()


def context_for_user(user: dict[str, Any], *, request_id: str = "", membership: dict[str, Any] | None = None) -> RequestContext:
    """Compatibility bridge: caller must supply an already server-resolved member.

    Global organization hints are not member organization grants. They are kept
    in the legacy user dictionary, not in shared-SaaS authorization scope.
    """
    from app.services.tenant_access import effective_capabilities

    if user.get("status") != "active":
        return RequestContext(request_id=request_id, trace_id=request_id)
    if membership is not None:
        if membership.get("status") != "active" or membership.get("user_id") != user.get("id"):
            return RequestContext(request_id=request_id, trace_id=request_id)
        return RequestContext(
            actor_kind="member", actor_id=str(user["id"]), role=str(membership.get("role") or ""),
            tenant_id=membership.get("tenant_id"), membership_id=membership.get("id"),
            capabilities=tuple(effective_capabilities(user, membership)),
            auth_version=int(membership.get("auth_version") or 0),
            request_id=request_id, trace_id=request_id,
        )
    return RequestContext(request_id=request_id, trace_id=request_id)
