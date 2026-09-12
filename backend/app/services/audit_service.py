"""Read-only audit stream projection shared by HTTP and UI consumers.

Audit records may contain internal workflow fields, so this service owns the
public projection and organization-scope filtering instead of exposing the
raw JSON records directly from an API route.
"""

from __future__ import annotations

from typing import Any

from app.db import Store
from app.services.access_service import visible_organization_ids


_AUDIT_MANAGER_ROLES = frozenset({"admin", "college_admin"})


def _policy_visible(store: Store, user: dict[str, Any], requirement_set: Any) -> bool:
    if user.get("role") == "admin":
        return True
    if user.get("role") != "college_admin" or user.get("status") != "active":
        return False
    visible_ids = visible_organization_ids(store, user)
    bindings = getattr(requirement_set, "bindings", []) or []
    return any(
        str(getattr(binding, "scope_id", "")) in visible_ids
        for binding in bindings
        if getattr(binding, "scope_type", "") not in {"global", "all"}
    )


def _actor_name(store: Store, actor_id: str) -> str:
    if not actor_id:
        return "系统任务"
    actor = store.get_user(actor_id)
    return str((actor or {}).get("display_name") or (actor or {}).get("username") or actor_id)


def _public_event(store: Store, event: dict[str, Any], *, source: str = "workflow") -> dict[str, Any]:
    """Keep identifiers and decision metadata, never manuscript/document content."""
    actor_id = str(event.get("actor_id") or "")
    reason = str(event.get("reason") or "")
    return {
        "id": str(event.get("id") or ""),
        "source": source,
        "actor_id": actor_id,
        "actor_display_name": _actor_name(store, actor_id),
        "action": str(event.get("action") or ""),
        "resource_type": str(event.get("resource_type") or ""),
        "resource_id": str(event.get("resource_id") or ""),
        "organization_id": str(event.get("organization_id") or ""),
        "result": str(event.get("result") or "success"),
        "reason": reason[:240],
        "request_id": str(event.get("request_id") or ""),
        "created_at": str(event.get("created_at") or ""),
    }


def list_audit_events(
    store: Store,
    user: dict[str, Any],
    *,
    limit: int = 100,
    resource_type: str = "",
    action: str = "",
    actor_id: str = "",
    organization_id: str = "",
) -> list[dict[str, Any]]:
    """Return the actor's organization-scoped, content-free audit projection."""
    if user.get("role") not in _AUDIT_MANAGER_ROLES or user.get("status") != "active":
        return []

    visible_ids = visible_organization_ids(store, user)
    result: list[dict[str, Any]] = []
    for event in store.list_audit_events(limit=5000):
        event_org = str(event.get("organization_id") or "")
        if user.get("role") != "admin" and event_org not in visible_ids:
            continue
        if resource_type and event.get("resource_type") != resource_type:
            continue
        if action and event.get("action") != action:
            continue
        if actor_id and event.get("actor_id") != actor_id:
            continue
        if organization_id and event_org != organization_id:
            continue
        result.append(_public_event(store, event))

    # Policy audits live in a separate ledger, but use the same public shape.
    for requirement_set in store.list_requirement_sets():
        if not _policy_visible(store, user, requirement_set):
            continue
        policy_org = str(getattr(requirement_set, "scope_id", "") or "")
        for event in store.list_policy_audits(requirement_set.id, limit=500):
            normalized = _public_event(store, {
                "id": event.id,
                "actor_id": event.actor_id,
                "action": event.action,
                "resource_type": "policy",
                "resource_id": requirement_set.id,
                "organization_id": policy_org,
                "request_id": event.request_id,
                "created_at": event.created_at,
            }, source="policy")
            if resource_type and normalized["resource_type"] != resource_type:
                continue
            if action and normalized["action"] != action:
                continue
            if actor_id and normalized["actor_id"] != actor_id:
                continue
            if organization_id and normalized["organization_id"] != organization_id:
                continue
            result.append(normalized)

    result.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return result[: max(1, min(limit, 500))]
