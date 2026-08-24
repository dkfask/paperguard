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


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register", status_code=201)
def register(req: RegisterRequest, store: Store = Depends(get_store)) -> dict:
    """注册学生账号。"""
    return public_user(register_user(store, req.username, req.password, req.display_name))


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
