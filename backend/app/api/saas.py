"""Trusted SaaS contract slice; legacy API and authorization debt stay separate."""
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator

from app.auth import trusted_context
from app.core.context import RequestContext
from app.core.saas_contract import (
    ERROR_RESPONSES, ApiEnvelope, ContractModel, ContextData, ContextView,
    MembershipView, TenantView, SaasRoute, AiRequestView, RecoveryPlan, envelope,
)
from app.db import Store, new_id
from app.services.tenant_access import ROLE_CAPABILITIES, TENANT_CAPABILITIES
from app.services import ai_recovery

router = APIRouter(prefix="/api/saas/v1", tags=["saas"], route_class=SaasRoute, responses=ERROR_RESPONSES)


def get_store() -> Store:
    return Store()


def _platform(ctx: RequestContext) -> None:
    if ctx.actor_kind != "platform" or not ctx.has_capability("operations:manage"):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_REQUIRED", "message": "需要平台操作身份"})


def _tenant(ctx: RequestContext, tenant_id: str, capability: str) -> None:
    if ctx.actor_kind != "member" or ctx.tenant_id != tenant_id or not ctx.has_capability(capability):
        raise HTTPException(status_code=403, detail={"code": "TENANT_SCOPE_FORBIDDEN", "message": "租户范围或成员能力不足"})


class TenantCreate(ContractModel):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=200)


class MembershipCreate(ContractModel):
    user_id: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("role")
    @classmethod
    def known_role(cls, value: str) -> str:
        if value not in ROLE_CAPABILITIES:
            raise ValueError("unsupported tenant role")
        return value

    @field_validator("capabilities")
    @classmethod
    def tenant_only_capabilities(cls, values: list[str]) -> list[str]:
        if set(values) - TENANT_CAPABILITIES:
            raise ValueError("unsupported tenant capability")
        return sorted(set(values))


@router.get("/context", response_model=ApiEnvelope[ContextData])
def current_context(ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    values = asdict(ctx)
    context = ContextView(**{name: values[name] for name in ContextView.model_fields})
    tenant = store.get_tenant(ctx.tenant_id) if ctx.tenant_id else None
    return envelope(ContextData(context=context, tenant=tenant), ctx)


@router.get("/tenants", response_model=ApiEnvelope[list[TenantView]])
def list_tenants(ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    if ctx.actor_kind == "platform":
        _platform(ctx)
        tenants = store.list_tenants()
    else:
        _tenant(ctx, ctx.tenant_id, "tenant:read")
        tenants = [store.get_tenant(ctx.tenant_id)]
    return envelope(tenants, ctx)


@router.post("/tenants", status_code=201, response_model=ApiEnvelope[TenantView])
def create_tenant(req: TenantCreate, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    _platform(ctx)
    if any(item["slug"] == req.slug for item in store.list_tenants(status=None)):
        raise HTTPException(status_code=409, detail={"code": "TENANT_SLUG_TAKEN", "message": "租户标识已存在"})
    tenant = store.create_tenant({"id": new_id("tenant"), "slug": req.slug, "name": req.name})
    return envelope(tenant, ctx, action="tenant.create", command_id=tenant["id"])


@router.get("/tenants/{tenant_id}/memberships", response_model=ApiEnvelope[list[MembershipView]])
def list_memberships(tenant_id: str, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    if ctx.actor_kind == "platform":
        _platform(ctx)
    else:
        _tenant(ctx, tenant_id, "membership:read")
    return envelope(store.list_memberships_for_tenant(tenant_id), ctx)


@router.post("/tenants/{tenant_id}/memberships", status_code=201, response_model=ApiEnvelope[MembershipView])
def add_membership(tenant_id: str, req: MembershipCreate, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    _platform(ctx)
    tenant = store.get_tenant(tenant_id)
    if not tenant or tenant.get("status") != "active":
        raise HTTPException(status_code=404, detail={"code": "TENANT_NOT_FOUND", "message": "租户不存在"})
    if not store.get_user(req.user_id):
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "用户不存在"})
    membership = store.save_membership({"id": new_id("membership"), "tenant_id": tenant_id,
        "user_id": req.user_id, "role": req.role, "capabilities": req.capabilities})
    return envelope(membership, ctx, action="membership.save", command_id=membership["id"])


class ReconciliationRequest(ContractModel):
    expected_status: str = Field(pattern=r"^unknown$")
    reason_code: str = Field(pattern=r"^unknown_external_result$")


@router.get("/ai/requests", response_model=ApiEnvelope[list[AiRequestView]])
def ai_requests(ctx: Annotated[RequestContext, Depends(trusted_context)], limit: int = Query(default=100, ge=1, le=100), store: Store = Depends(get_store)):
    return envelope(ai_recovery.list_requests(ctx, store, limit=limit), ctx)


@router.get("/ai/requests/{request_id}/recovery-plan", response_model=ApiEnvelope[RecoveryPlan])
def ai_recovery_plan(request_id: str, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    return envelope(ai_recovery.recovery_plan(ctx, store, request_id), ctx)


@router.post("/ai/requests/{request_id}/reconciliation", response_model=ApiEnvelope[AiRequestView])
def request_reconciliation(request_id: str, req: ReconciliationRequest, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    record, replay = ai_recovery.request_reconciliation(ctx, store, request_id)
    return envelope(record, ctx, action="ai.request_reconciliation", command_id=request_id, replay=replay)
