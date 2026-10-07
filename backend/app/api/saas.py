"""Trusted SaaS contract slice; legacy API and authorization debt stay separate."""
from dataclasses import asdict
from typing import Annotated
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import Field, field_validator

from app.auth import trusted_context
from app.core.context import RequestContext
from app.core.saas_contract import (
    ERROR_RESPONSES, ApiEnvelope, ContractModel, ContextData, ContextView,
    MembershipView, TenantView, SaasRoute, AiRequestView, RecoveryPlan, envelope,
)
from app.db import Store, new_id
from app.services.tenant_access import ROLE_CAPABILITIES, TENANT_CAPABILITIES
from app.services import ai_recovery, saas_identity, tenant_projects
from app.models.project import WritingProject, ProjectSection

router = APIRouter(prefix="/api/saas/v1", tags=["saas"], route_class=SaasRoute, responses=ERROR_RESPONSES)


def get_store() -> Store:
    return Store()


def _platform(ctx: RequestContext) -> None:
    if ctx.actor_kind != "platform" or not ctx.has_capability("operations:manage"):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_REQUIRED", "message": "需要平台操作身份"})


def platform_context(ctx: Annotated[RequestContext, Depends(trusted_context)]) -> RequestContext:
    _platform(ctx)
    return ctx


def _tenant(ctx: RequestContext, tenant_id: str, capability: str) -> None:
    if ctx.actor_kind != "member" or ctx.tenant_id != tenant_id or not ctx.has_capability(capability):
        raise HTTPException(status_code=403, detail={"code": "TENANT_SCOPE_FORBIDDEN", "message": "租户范围或成员能力不足"})


class TenantApproval(ContractModel):
    approval_ref: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")
    responsible_user_id: str = Field(min_length=1, max_length=120)
    organization_root_id: str = Field(min_length=1, max_length=120)
    outbound_policy: str = Field(pattern=r"^disabled$")
    pilot_expires_at: datetime
    exit_contact_user_id: str = Field(min_length=1, max_length=120)

    @field_validator("pilot_expires_at")
    @classmethod
    def aware_expiry(cls, value):
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value


class TenantCreate(ContractModel):
    approval: TenantApproval
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
        tenants = store.list_tenants(status=None)
    else:
        _tenant(ctx, ctx.tenant_id, "tenant:read")
        tenants = [store.get_tenant(ctx.tenant_id)]
    return envelope(tenants, ctx)


@router.post("/tenants", dependencies=[Depends(platform_context)], status_code=201, response_model=ApiEnvelope[TenantView])
def create_tenant(req: TenantCreate, ctx: Annotated[RequestContext, Depends(trusted_context)], idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), store: Store = Depends(get_store)):
    _platform(ctx)
    tenant, replay = saas_identity.create_draft(store, ctx, req, idempotency_key)
    return envelope(tenant, ctx, action="tenant.create_draft", command_id=tenant["id"], replay=replay)


@router.get("/tenants/{tenant_id}/memberships", response_model=ApiEnvelope[list[MembershipView]])
def list_memberships(tenant_id: str, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    if ctx.actor_kind == "platform":
        _platform(ctx)
    else:
        _tenant(ctx, tenant_id, "membership:read")
    return envelope(store.list_memberships_for_tenant(tenant_id), ctx)


@router.post("/tenants/{tenant_id}/memberships", dependencies=[Depends(platform_context)], status_code=201, response_model=ApiEnvelope[MembershipView])
def add_membership(tenant_id: str, req: MembershipCreate, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    _platform(ctx)
    membership, replay = saas_identity.add_member(store, ctx, tenant_id, req)
    return envelope(membership, ctx, action="membership.create", command_id=membership["id"], replay=replay)


class OwnerAssign(ContractModel):
    user_id: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1)


class ActivationRequest(ContractModel):
    expected_version: int = Field(ge=1)


@router.post("/tenants/{tenant_id}/first-owner", dependencies=[Depends(platform_context)], response_model=ApiEnvelope[MembershipView])
def first_owner(tenant_id: str, req: OwnerAssign, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    _platform(ctx)
    membership, replay = saas_identity.assign_first_owner(store, ctx, tenant_id, req)
    return envelope(membership, ctx, action="tenant.first_owner", command_id=membership["id"], replay=replay)


@router.post("/tenants/{tenant_id}/activate", dependencies=[Depends(platform_context)], response_model=ApiEnvelope[TenantView])
def activate(tenant_id: str, req: ActivationRequest, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    _platform(ctx)
    saas_identity.refuse_activation(store, ctx, tenant_id, req.expected_version)


class ProjectCreate(ContractModel):
    title: str = Field(default="", max_length=500)
    sections: list[ProjectSection] = Field(default_factory=list, max_length=200)


class ProjectUpdate(ContractModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=500)
    sections: list[ProjectSection] | None = Field(default=None, max_length=200)

    @field_validator("title", "sections")
    @classmethod
    def no_null_update(cls, value):
        if value is None:
            raise ValueError("null update not supported")
        return value


class ProjectSummary(ContractModel):
    id: str
    title: str
    tenant_id: str = Field(json_schema_extra={"readOnly": True})
    membership_id: str = Field(json_schema_extra={"readOnly": True})
    revision: int
    updated_at: str


@router.get("/projects", response_model=ApiEnvelope[list[ProjectSummary]])
def list_tenant_projects(ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    return envelope(tenant_projects.list_projects(store, ctx), ctx)


@router.post("/projects", status_code=201, response_model=ApiEnvelope[WritingProject])
def create_tenant_project(req: ProjectCreate, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    project = tenant_projects.create_project(store, ctx, req)
    return envelope(project, ctx, action="project.create", command_id=project.id)


@router.get("/projects/{project_id}", response_model=ApiEnvelope[WritingProject])
def get_tenant_project(project_id: str, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    return envelope(tenant_projects.get_project(store, ctx, project_id), ctx)


@router.patch("/projects/{project_id}", response_model=ApiEnvelope[WritingProject])
def update_tenant_project(project_id: str, req: ProjectUpdate, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    project = tenant_projects.update_project(store, ctx, project_id, req)
    return envelope(project, ctx, action="project.update", command_id=project.id)


@router.post("/projects/{project_id}/exports", response_model=ApiEnvelope[ProjectSummary])
def refuse_unisolated_project_action(project_id: str, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    tenant_projects.refuse_export_or_job(store, ctx, project_id)


@router.post("/projects/{project_id}/jobs", response_model=ApiEnvelope[ProjectSummary])
def refuse_unisolated_project_job(project_id: str, ctx: Annotated[RequestContext, Depends(trusted_context)], store: Store = Depends(get_store)):
    tenant_projects.refuse_export_or_job(store, ctx, project_id)


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
