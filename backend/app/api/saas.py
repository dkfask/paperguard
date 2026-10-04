"""Minimal runtime SaaS boundary for tenant and membership context."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user, require_admin
from app.db import Store, new_id
from app.services.tenant_access import assert_tenant_scope, context_dict, effective_capabilities, resolve_membership

router = APIRouter(prefix="/api/saas/v1", tags=["saas"])


def get_store() -> Store:
    return Store()


class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=200)


class MembershipCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(default_factory=list, max_length=100)


@router.get("/context")
def current_context(user: dict = Depends(current_user), store: Store = Depends(get_store)) -> dict:
    membership = resolve_membership(store, user)
    if membership is None:
        return {"authenticated": True, "context": context_dict(user, None), "tenant": None}
    tenant = store.get_tenant(str(membership["tenant_id"]))
    return {"authenticated": True, "context": context_dict(user, membership), "tenant": tenant}


@router.get("/tenants")
def list_tenants(user: dict = Depends(current_user), store: Store = Depends(get_store)) -> list[dict]:
    memberships = store.list_memberships_for_user(user["id"])
    if user.get("role") == "admin":
        return store.list_tenants()
    allowed = {str(item["tenant_id"]) for item in memberships if item.get("status") == "active"}
    return [tenant for tenant in store.list_tenants() if tenant["id"] in allowed]


@router.post("/tenants", status_code=201)
def create_tenant(req: TenantCreate, admin: dict = Depends(require_admin), store: Store = Depends(get_store)) -> dict:
    if any(item["slug"] == req.slug for item in store.list_tenants(status=None)):
        raise HTTPException(status_code=409, detail={"code": "TENANT_SLUG_TAKEN", "message": "租户标识已存在"})
    return store.create_tenant({"id": new_id("tenant"), "slug": req.slug, "name": req.name})


@router.get("/tenants/{tenant_id}/memberships")
def list_memberships(tenant_id: str, user: dict = Depends(current_user), store: Store = Depends(get_store)) -> list[dict]:
    if user.get("role") != "admin":
        assert_tenant_scope(tenant_id, user=user, store=store, capability="membership:read")
    return store.list_memberships_for_tenant(tenant_id)


@router.post("/tenants/{tenant_id}/memberships", status_code=201)
def add_membership(tenant_id: str, req: MembershipCreate, admin: dict = Depends(require_admin), store: Store = Depends(get_store)) -> dict:
    if not store.get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail={"code": "TENANT_NOT_FOUND", "message": "租户不存在"})
    if not store.get_user(req.user_id):
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "用户不存在"})
    return store.save_membership({
        "id": new_id("membership"), "tenant_id": tenant_id, "user_id": req.user_id,
        "role": req.role, "capabilities": req.capabilities,
    })
