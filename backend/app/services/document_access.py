"""Authorization and public projections for generic document resources."""

from __future__ import annotations

from typing import Any

from app.db import Store
from app.services.access_service import visible_organization_ids


_SCOPE_KEYS = ("batch_id", "course_id", "major_id", "college_id", "school_id")


def organization_id_for_context(context: dict[str, Any] | None) -> str:
    """Return one stable audit scope without storing the full context."""
    context = context or {}
    for key in reversed(_SCOPE_KEYS):
        value = context.get(key)
        if value:
            return str(value)
    return ""


def document_context_visible(
    store: Store,
    user: dict[str, Any],
    resource_context: dict[str, Any] | None,
    *,
    allow_global: bool = True,
) -> bool:
    """Check generic resource scope without cross-field value collisions.

    A package can be global, match an actor's same-named scope, or target an
    organization visible to a college administrator. Values from different
    scope keys are never considered equivalent.
    """
    if user.get("role") == "admin":
        return True
    resource_context = resource_context or {}
    if not resource_context:
        return allow_global
    user_context = user.get("organization_context") or {}
    if any(
        key in user_context
        and key in resource_context
        and str(user_context[key]) == str(resource_context[key])
        for key in _SCOPE_KEYS
    ):
        return True
    if user.get("role") != "college_admin":
        return False
    visible_ids = visible_organization_ids(store, user)
    return any(str(value) in visible_ids for key, value in resource_context.items() if key in _SCOPE_KEYS and value)


def can_access_document_project(store: Store, user: dict[str, Any], project: Any) -> bool:
    """Allow owners, administrators, and scoped college administrators."""
    if user.get("role") == "admin" or project.owner_id == user.get("id"):
        return True
    return user.get("role") == "college_admin" and document_context_visible(
        store, user, project.organization_context, allow_global=False
    )


def document_package_visible(store: Store, user: dict[str, Any], package: Any) -> bool:
    """Personal writing templates belong to their server-recorded publisher.

    Earlier explicitly published packages retain their shared organization scope.
    Project access continues to use the project's own frozen ownership policy.
    """
    if package.metadata.get("usage") == "personal":
        return bool(package.published_by) and package.published_by == user.get("id")
    return document_context_visible(store, user, package.metadata.get("organization_context"))


def audit_document_access_denied(
    store: Store,
    user: dict[str, Any],
    *,
    resource_type: str,
    resource_id: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Record a content-free denial event for security investigation."""
    store.save_audit_event({
        "actor_id": str(user.get("id") or ""),
        "action": "document.access.denied",
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "organization_id": organization_id_for_context(context or user.get("organization_context")),
        "result": "failure",
        "reason": "access_denied",
    })


def public_document_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Project a package/editor bundle without exposing storage keys."""
    payload = {
        key: value.model_dump(mode="json")
        for key, value in bundle.items()
    }
    template_version = payload.get("template_version") or {}
    definition = template_version.get("definition") or {}
    definition["source_docx"] = ""
    template_version["definition"] = definition
    extensions = template_version.get("extensions") or {}
    source_asset = extensions.get("source_asset")
    if isinstance(source_asset, dict):
        source_asset = dict(source_asset)
        source_asset.pop("storage_key", None)
        extensions["source_asset"] = source_asset
        template_version["extensions"] = extensions
    payload["template_version"] = template_version
    return payload


__all__ = [
    "audit_document_access_denied",
    "can_access_document_project",
    "document_context_visible",
    "document_package_visible",
    "organization_id_for_context",
    "public_document_bundle",
]
