"""Shared organization-scope primitives for resource authorization."""

from __future__ import annotations

from typing import Any

from app.db import Store


MANAGER_ROLES = frozenset({"admin", "college_admin"})

_SCOPE_KEYS = ("batch_id", "course_id", "major_id", "college_id", "school_id")


def _scope_roots(user: dict[str, Any]) -> set[str]:
    """Use the most specific assigned organization instead of widening scope."""
    context = user.get("organization_context") or {}
    for key in _SCOPE_KEYS:
        value = context.get(key)
        if value:
            return {str(value)}
    return set()


def visible_organization_ids(store: Store, user: dict[str, Any]) -> set[str]:
    """Return the organizations an actor may operate on, including descendants."""
    if user.get("role") == "admin":
        return {unit.id for unit in store.list_organization_units()}
    values = _scope_roots(user)
    if user.get("role") != "college_admin":
        return values
    changed = True
    while changed:
        changed = False
        for unit in store.list_organization_units():
            if unit.parent_id in values and unit.id not in values:
                values.add(unit.id)
                changed = True
    return values


def can_access_organization(store: Store, user: dict[str, Any], organization_id: str) -> bool:
    return organization_id in visible_organization_ids(store, user)
