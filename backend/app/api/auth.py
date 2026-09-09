"""用户认证 API: 注册 / 登录 / 登出 / 当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.auth import (
    public_user,
    change_password,
    current_user,
    login_user,
    logout_user,
    register_user,
    require_admin,
    seed_admin,
)
from app.db import Store

router = APIRouter(prefix="/api/auth")


def get_store() -> Store:
    store = Store()
    seed_admin(store)   # 懒加载: 保证内置管理员始终存在
    return store


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "student"


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register", status_code=201)
def register(req: RegisterRequest, store: Store = Depends(get_store)) -> dict:
    """注册账号 (可选择学生 student 或教师 teacher)。"""
    return register_user(store, req.username, req.password, req.display_name, req.role)


@router.post("/login")
def login(req: LoginRequest, store: Store = Depends(get_store)) -> dict:
    """登录, 返回 {token, user}。"""
    return login_user(store, req.username, req.password)


@router.post("/logout")
def logout(authorization: str | None = Header(default=None), store: Store = Depends(get_store)) -> dict:
    if authorization and authorization.startswith("Bearer "):
        logout_user(store, authorization.removeprefix("Bearer ").strip())
    return {"status": "ok"}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_user_password(req: ChangePasswordRequest,
                         user: dict = Depends(current_user),
                         store: Store = Depends(get_store)) -> dict:
    change_password(store, user, req.current_password, req.new_password)
    return {"status": "ok", "message": "密码已更新, 请重新登录"}


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    """当前登录用户。"""
    return public_user(user)


class UserRoleStatusUpdate(BaseModel):
    role: str | None = None
    status: str | None = None


@router.get("/admin/users")
def list_admin_users(admin: dict = Depends(require_admin),
                     store: Store = Depends(get_store)) -> list[dict]:
    """管理员查看全站用户列表。"""
    users = store.list_users()
    return [public_user(u) for u in users]


@router.put("/admin/users/{user_id}/role-status")
def update_user_role_status(user_id: str,
                            req: UserRoleStatusUpdate,
                            admin: dict = Depends(require_admin),
                            store: Store = Depends(get_store)) -> dict:
    """管理员审核/调整用户角色与认证状态。"""
    target = store.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    new_role = req.role if req.role in ("admin", "college_admin", "teacher", "student") else target["role"]
    new_status = req.status if req.status in ("active", "pending", "rejected") else target.get("status", "active")

    # 防止取消唯一内置管理员自身角色
    if target["username"] == "admin" and (new_role != "admin" or new_status != "active"):
        raise HTTPException(status_code=400, detail="不可降级或禁用系统内置管理员")

    store.update_user_role_and_status(user_id, new_role, new_status)
    updated = store.get_user(user_id)
    return public_user(updated)
