"""组织层级与用户作用域管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import _save_user_metadata, current_user, require_admin, require_manager
from app.db import Store, new_id
from app.models.organization import OrganizationType, OrganizationUnit
from app.services.access_service import visible_organization_ids

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


def get_store() -> Store:
    return Store()


def _visible_unit_ids(store: Store, user: dict) -> set[str]:
    return visible_organization_ids(store, user)


def _can_access(unit: OrganizationUnit, store: Store, user: dict) -> bool:
    return unit.id in _visible_unit_ids(store, user)


def _validate_parent(unit: OrganizationUnit, store: Store) -> None:
    if not unit.parent_id:
        if unit.type != OrganizationType.SCHOOL:
            raise HTTPException(status_code=422, detail="非学校组织必须指定 parent_id")
        return
    parent = store.get_organization_unit(unit.parent_id)
    if parent is None:
        raise HTTPException(status_code=422, detail="父级组织不存在")
    expected_parent = {
        OrganizationType.COLLEGE: OrganizationType.SCHOOL,
        OrganizationType.MAJOR: OrganizationType.COLLEGE,
        OrganizationType.COURSE: OrganizationType.MAJOR,
        OrganizationType.BATCH: OrganizationType.MAJOR,
    }.get(unit.type)
    if expected_parent and parent.type != expected_parent:
        raise HTTPException(status_code=422, detail=f"{unit.type.value} 的父级必须是 {expected_parent.value}")


@router.get("")
def list_organizations(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[OrganizationUnit]:
    units = store.list_organization_units()
    if user["role"] == "admin":
        return units
    visible_ids = _visible_unit_ids(store, user)
    return [unit for unit in units if unit.id in visible_ids]


@router.get("/{unit_id}")
def get_organization(unit_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> OrganizationUnit:
    unit = store.get_organization_unit(unit_id)
    if unit is None or not _can_access(unit, store, user):
        raise HTTPException(status_code=404, detail="组织不存在或无权访问")
    return unit


@router.post("", status_code=201)
def create_organization(payload: dict, store: Store = Depends(get_store),
                        admin: dict = Depends(require_admin)) -> OrganizationUnit:
    """创建组织节点。仅活跃的系统管理员；不沿用教师的模板管理权限。"""
    try:
        data = dict(payload)
        data["id"] = data.get("id") or new_id("org")
        unit = OrganizationUnit.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"组织数据无效: {exc}") from exc
    if store.get_organization_unit(unit.id):
        raise HTTPException(status_code=409, detail="组织 ID 已存在")
    _validate_parent(unit, store)
    return store.save_organization_unit(unit)


@router.put("/{unit_id}")
def update_organization(unit_id: str, payload: OrganizationUnit,
                        store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> OrganizationUnit:
    existing = store.get_organization_unit(unit_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="组织不存在")
    if payload.id != unit_id or payload.type != existing.type or payload.parent_id != existing.parent_id:
        raise HTTPException(status_code=422, detail="组织 ID、类型和父级不可修改")
    if not _can_access(existing, store, user):
        raise HTTPException(status_code=403, detail="无权修改此组织")
    _validate_parent(payload, store)
    return store.save_organization_unit(payload)


@router.delete("/{unit_id}")
def delete_organization(unit_id: str, store: Store = Depends(get_store),
                        user: dict = Depends(require_manager)) -> dict:
    unit = store.get_organization_unit(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="组织不存在")
    if not _can_access(unit, store, user):
        raise HTTPException(status_code=403, detail="无权删除此组织")
    if any(value.parent_id == unit_id for value in store.list_organization_units()):
        raise HTTPException(status_code=409, detail="请先删除或迁移下级组织")
    if store.delete_organization_unit(unit_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="组织不存在")


class UserContextRequest(BaseModel):
    organization_context: dict[str, str]


@router.put("/users/{user_id}/context")
def update_user_context(user_id: str, req: UserContextRequest,
                        store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> dict:
    if user["role"] not in ("admin", "college_admin"):
        raise HTTPException(status_code=403, detail="只有管理员可以设置用户组织作用域")
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    for unit_id in req.organization_context.values():
        if store.get_organization_unit(unit_id) is None:
            raise HTTPException(status_code=422, detail=f"组织不存在: {unit_id}")
    if user["role"] == "college_admin":
        visible_ids = _visible_unit_ids(store, user)
        if not set(req.organization_context.values()).issubset(visible_ids):
            raise HTTPException(status_code=403, detail="只能设置本人组织范围内的作用域")
    target["organization_context"] = req.organization_context
    _save_user_metadata(store, target, ("organization_context",), actor=user)
    return {"id": target["id"], "organization_context": req.organization_context}
