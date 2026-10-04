"""Tenant membership and capability authorization primitives."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.db import Store

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "admin": frozenset({
        "tenant:read", "tenant:manage", "membership:read", "membership:manage",
        "ai:manage", "audit:read",
    }),
    "college_admin": frozenset({
        "tenant:read", "membership:read", "ai:read", "audit:read",
    }),
    "teacher": frozenset({"tenant:read"}),
    "student": frozenset({"tenant:read"}),
    "user": frozenset({"tenant:read"}),
}


def capabilities_for_role(role: str) -> frozenset[str]:
    return ROLE_CAPABILITIES.get(role, frozenset())


TENANT_CAPABILITIES = frozenset().union(*ROLE_CAPABILITIES.values())
PLATFORM_CAPABILITIES = ROLE_CAPABILITIES["admin"] | frozenset({"operations:manage", "backup:global"})


def effective_capabilities(user: dict[str, Any], membership: dict[str, Any] | None = None) -> frozenset[str]:
    if user.get("status") != "active":
        return frozenset()
    if membership is None:
        # The legacy global admin column is the platform identity, not a
        # tenant membership role. Unmapped ordinary users have no tenant grants.
        return PLATFORM_CAPABILITIES if user.get("role") == "admin" else frozenset()
    if membership.get("status", "active") != "active":
        return frozenset()
    if "capabilities" in membership:
        # [] is a deliberate deny, never a request to inherit role defaults.
        return frozenset(str(item) for item in (membership["capabilities"] or ())) & TENANT_CAPABILITIES
    return capabilities_for_role(str(membership.get("role") or ""))


def resolve_membership(store: Store, user: dict[str, Any]) -> dict[str, Any] | None:
    """Read existing identity only; mapping/backfill is an explicit operation."""
    organization = user.get("organization_context") or {}
    tenant_id = str(user.get("tenant_id") or organization.get("tenant_id") or "").strip()
    if tenant_id:
        membership = store.get_membership_for_user(user["id"], tenant_id)
    else:
        memberships = [item for item in store.list_memberships_for_user(user["id"])
                       if item.get("status") == "active"]
        # Never choose whichever tenant happened to be updated last.
        membership = memberships[0] if len(memberships) == 1 else None
    if membership is None or user.get("status") != "active":
        return None
    tenant = store.get_tenant(str(membership["tenant_id"]))
    return membership if tenant and tenant.get("status") == "active" else None


def require_capability(capability: str, *, user: dict[str, Any], store: Store) -> dict[str, Any]:
    membership = resolve_membership(store, user)
    if membership is None or capability not in effective_capabilities(user, membership):
        raise HTTPException(status_code=403, detail={
            "code": "CAPABILITY_REQUIRED", "capability": capability,
            "message": "当前成员身份无权执行此操作",
        })
    return membership or {}


def assert_tenant_scope(
    tenant_id: str,
    *,
    user: dict[str, Any],
    store: Store,
    capability: str = "tenant:read",
) -> dict[str, Any]:
    if not tenant_id:
        raise HTTPException(status_code=400, detail={"code": "TENANT_CONTEXT_MISSING", "message": "缺少租户上下文"})
    membership = store.get_membership_for_user(user["id"], tenant_id)
    tenant = store.get_tenant(tenant_id)
    if membership is None or user.get("status") != "active" or not tenant or tenant.get("status") != "active":
        raise HTTPException(status_code=403, detail={"code": "TENANT_SCOPE_FORBIDDEN", "message": "当前账号不属于该租户"})
    if capability not in effective_capabilities(user, membership):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_REQUIRED", "capability": capability, "message": "成员能力不足"})
    return membership


def context_dict(user: dict[str, Any], membership: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "actor_id": str(user.get("id") or ""),
        "tenant_id": str((membership or {}).get("tenant_id") or ""),
        "membership_id": str((membership or {}).get("id") or ""),
        "role": str((membership or {}).get("role") or user.get("role") or ""),
        "capabilities": sorted(effective_capabilities(user, membership)),
        "auth_version": int((membership or {}).get("auth_version") or 0),
    }
