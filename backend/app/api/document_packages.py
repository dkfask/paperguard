"""Published generic document package API."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user, require_manager
from app.core.feature_flags import require_general_document_platform
from app.db import Store
from app.services.package_engine import PackagePublishError, publish_package_candidates
from app.services.document_candidates import (
    CandidateBindingError,
    SPEC_CANDIDATE_KIND,
    TEMPLATE_CANDIDATE_KIND,
    load_confirmed_candidate_with_record,
)
from app.services.template_engine import TemplateParseCandidate
from app.services.spec_engine import SpecParseCandidate
from app.services.document_access import document_package_visible, public_document_bundle

router = APIRouter(
    prefix="/api/document-packages",
    tags=["document-packages"],
    dependencies=[Depends(require_general_document_platform)],
)


class PublishPackageRequest(BaseModel):
    template_candidate: TemplateParseCandidate
    spec_candidate: SpecParseCandidate | None = None
    document_type_id: str = Field(default="generic", min_length=1, max_length=80)
    renderer_version: str = Field(default="generic-docx-v1", min_length=1, max_length=120)
    rule_engine_version: str = Field(default="generic-check-v1", min_length=1, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)
    usage: Literal["personal", "shared"] = "shared"


def get_store() -> Store:
    return Store()


def _public_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return public_document_bundle(bundle)


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


def _visible(store: Store, bundle: dict[str, Any], user: dict) -> bool:
    return document_package_visible(store, user, bundle["package"])


@router.get("")
def list_document_packages(
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    """List published packages available to the current user."""
    return [
        _public_bundle(bundle)
        for bundle in store.list_document_package_bundles(status="published")
        if _visible(store, bundle, user)
    ]


@router.post("", status_code=201)
def publish_document_package(
    request: PublishPackageRequest,
    store: Store = Depends(get_store),
    manager: dict = Depends(require_manager),
) -> dict[str, Any]:
    """Publish one exact template/spec composition after review.

    A separate specification is optional for form-like document types.
    """
    package_hint = f"{request.template_candidate.template_id}:{request.spec_candidate.spec_set_id if request.spec_candidate else 'no-spec'}"
    try:
        template_candidate, template_record = load_confirmed_candidate_with_record(
            store,
            TEMPLATE_CANDIDATE_KIND,
            request.template_candidate,
            TemplateParseCandidate,
            manager,
        )
        template_context = dict(template_record.get("organization_context") or {})
        spec_candidate = None
        if request.spec_candidate is not None:
            spec_candidate, spec_record = load_confirmed_candidate_with_record(
                store,
                SPEC_CANDIDATE_KIND,
                request.spec_candidate,
                SpecParseCandidate,
                manager,
            )
            spec_context = dict(spec_record.get("organization_context") or {})
            if template_context != spec_context:
                raise CandidateBindingError("模板与规范候选的组织范围不一致")
        bundle = publish_package_candidates(
            template_candidate,
            spec_candidate,
            document_type_id=request.document_type_id.strip(),
            published_by=str(manager.get("id") or ""),
            renderer_version=request.renderer_version.strip(),
            rule_engine_version=request.rule_engine_version.strip(),
            metadata={
                **request.metadata,
                "organization_context": template_context,
                # Teacher document configuration is personal writing; student
                # thesis templates use the separate thesis-template workflow.
                "usage": "personal" if manager.get("role") == "teacher" else request.usage,
            },
        )
        store.save_document_package(
            bundle["package"],
            bundle["template"],
            bundle["template_version"],
            bundle["spec_set"],
            bundle["spec_version"],
        )
    except CandidateBindingError as exc:
        _audit(store, manager, action="document.package.publish_failed", package_id=package_hint, result="failure", reason="candidate_binding_failed")
        raise HTTPException(status_code=404, detail="候选不存在、未确认或无权访问") from exc
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
    if bundle is None or not _visible(store, bundle, user):
        raise HTTPException(status_code=404, detail="文档包不存在或无权访问")
    return _public_bundle(bundle)


__all__ = ["router"]
