"""Published generic document package API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user, require_manager
from app.db import Store
from app.services.package_engine import PackagePublishError, publish_package_candidates
from app.services.template_engine import TemplateParseCandidate
from app.services.spec_engine import SpecParseCandidate

router = APIRouter(prefix="/api/document-packages", tags=["document-packages"])


class PublishPackageRequest(BaseModel):
    template_candidate: TemplateParseCandidate
    spec_candidate: SpecParseCandidate
    document_type_id: str = Field(default="generic", min_length=1, max_length=80)
    renderer_version: str = Field(default="generic-docx-v1", min_length=1, max_length=120)
    rule_engine_version: str = Field(default="generic-check-v1", min_length=1, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_store() -> Store:
    return Store()


def _public_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.model_dump(mode="json")
        for key, value in bundle.items()
    }


def _audit(store: Store, manager: dict, *, action: str, package_id: str, result: str, reason: str = "") -> None:
    context = manager.get("organization_context") or {}
    store.save_audit_event({
        "id": f"audit-{package_id}-{action.replace('.', '-')}",
        "actor_id": str(manager.get("id") or ""),
        "action": action,
        "resource_type": "document_package",
        "resource_id": package_id,
        "organization_id": str(context.get("school_id") or ""),
        "result": result,
        "reason": reason,
    })


def _visible(bundle: dict[str, Any], user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    context = bundle["package"].metadata.get("organization_context") or {}
    if not context:
        return True
    user_context = user.get("organization_context") or {}
    return bool(set(map(str, context.values())) & set(map(str, user_context.values())))


@router.get("")
def list_document_packages(
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    """List published packages available to the current user."""
    return [
        _public_bundle(bundle)
        for bundle in store.list_document_package_bundles(status="published")
        if _visible(bundle, user)
    ]


@router.post("", status_code=201)
def publish_document_package(
    request: PublishPackageRequest,
    store: Store = Depends(get_store),
    manager: dict = Depends(require_manager),
) -> dict[str, Any]:
    """Publish one exact template/spec composition after both confirmations."""
    package_hint = f"{request.template_candidate.template_id}:{request.spec_candidate.spec_set_id}"
    try:
        bundle = publish_package_candidates(
            request.template_candidate,
            request.spec_candidate,
            document_type_id=request.document_type_id.strip(),
            published_by=str(manager.get("id") or ""),
            renderer_version=request.renderer_version.strip(),
            rule_engine_version=request.rule_engine_version.strip(),
            metadata={
                **request.metadata,
                "organization_context": dict(manager.get("organization_context") or {}),
            },
        )
        store.save_document_package(
            bundle["package"],
            bundle["template"],
            bundle["template_version"],
            bundle["spec_set"],
            bundle["spec_version"],
        )
    except PackagePublishError as exc:
        _audit(store, manager, action="document.package.publish_failed", package_id=package_hint, result="failure", reason="candidate_not_publishable")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        _audit(store, manager, action="document.package.publish_failed", package_id=package_hint, result="failure", reason="duplicate_or_invalid_package")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(store, manager, action="document.package.published", package_id=bundle["package"].id, result="success")
    return _public_bundle(bundle)


@router.get("/{package_id}")
def get_document_package(
    package_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    bundle = store.get_document_package_bundle(package_id)
    if bundle is None or not _visible(bundle, user):
        raise HTTPException(status_code=404, detail="文档包不存在或无权访问")
    return _public_bundle(bundle)


__all__ = ["router"]
