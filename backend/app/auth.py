"""用户认证与权限。

- 密码: PBKDF2-HMAC-SHA256 + 随机盐 (标准库, 无额外依赖)
- 会话: 随机 token 存 users 表, 请求头 Authorization: Bearer <token>
- 角色: admin(全权限) / teacher(模板管理) / student(写作)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException

from app.db import Store, new_id

_PBKDF2_ITERATIONS = 200_000

_SESSION_HOURS = 24
ADMIN_USERNAME = "admin"
ADMIN_DISPLAY = "管理员"


def configured_admin_password() -> str:
    password = os.environ.get("PAPERGUARD_ADMIN_PASSWORD", "").strip()
    if len(password) < 12:
        raise RuntimeError("请设置 PAPERGUARD_ADMIN_PASSWORD, 且长度至少 12 位")
    return password

MANAGE_ROLES = ("admin", "teacher")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (hex_hash, salt)。"""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(digest.hex(), password_hash)


def seed_admin(store: Store) -> None:
    """首次启动时用环境变量创建管理员, 不再使用固定默认密码。"""
    if store.get_user_by_username(ADMIN_USERNAME) is not None:
        return
    try:
        password = configured_admin_password()
    except RuntimeError:
        return
    ph, salt = hash_password(password)
    store.save_user({
        "id": "user-admin",
        "username": ADMIN_USERNAME,
        "password_hash": ph,
        "salt": salt,
        "role": "admin",
        "display_name": ADMIN_DISPLAY,
        "token": None,
        "must_change_password": 1,
        "created_at": _now(),
    })


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=_SESSION_HOURS)).isoformat(timespec="seconds")


def _password_error(password: str) -> str | None:
    if len(password) < 8:
        return "密码至少 8 位"
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        return "密码需同时包含字母和数字"
    return None


def register_user(store: Store, username: str, password: str,
                  display_name: str = "", role: str = "student") -> dict:
    """注册新用户, 返回用户 dict(不含敏感字段)。
    学生直接 active; 教师默认 pending 待管理员认证。
    """
    username = username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少 3 个字符")
    password_error = _password_error(password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    if store.get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")

    target_role = "teacher" if role == "teacher" else "student"
    status = "pending" if target_role == "teacher" else "active"

    ph, salt = hash_password(password)
    user = store.save_user({
        "id": new_id("user"),
        "username": username,
        "password_hash": ph,
        "salt": salt,
        "role": target_role,
        "display_name": display_name.strip() or username,
        "token": None,
        "must_change_password": 0,
        "status": status,
        "created_at": _now(),
    })
    return public_user(user)


def login_user(store: Store, username: str, password: str) -> dict:
    """登录, 返回带过期时间的随机 bearer token。"""
    user = store.get_user_by_username(username.strip())
    if user is None or not verify_password(password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = secrets.token_hex(32)
    now = _now()
    store.create_session({
        "id": new_id("session"),
        "user_id": user["id"],
        "token_hash": _token_hash(token),
        "created_at": now,
        "expires_at": _expires_at(),
        "last_seen_at": now,
    })
    return {"token": token, "user": public_user(user)}


def logout_user(store: Store, token: str) -> None:
    store.revoke_session(_token_hash(token), _now())


def change_password(store: Store, user: dict, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if current_password == new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    if len(new_password) < 12:
        raise HTTPException(status_code=400, detail="新密码至少 12 位")
    if not any(ch.isalpha() for ch in new_password) or not any(ch.isdigit() for ch in new_password):
        raise HTTPException(status_code=400, detail="新密码需同时包含字母和数字")
    password_hash, salt = hash_password(new_password)
    user["password_hash"] = password_hash
    user["salt"] = salt
    user["must_change_password"] = 0
    store.save_user(user)
    store.revoke_user_sessions(user["id"], _now())


def public_user(user: dict) -> dict:
    """去除敏感字段。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "display_name": user["display_name"],
        "status": user.get("status", "active"),
        "teacher_id": user.get("teacher_id"),
        "must_change_password": bool(user.get("must_change_password", 0)),
    }


def current_user(authorization: str | None = Header(default=None),
                 store: Store = Depends(lambda: Store())) -> dict:
    """从 Authorization 头解析未过期、未撤销的当前用户。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    user = store.get_session_user(_token_hash(token), _now())
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效, 请重新登录")
    return user


def require_manager(user: dict = Depends(current_user)) -> dict:
    """模板管理权限: admin / 已认证 teacher。"""
    if user["role"] not in MANAGE_ROLES:
        raise HTTPException(status_code=403, detail="仅教师/管理员可管理模板")
    if user["role"] == "teacher" and user.get("status") != "active":
        raise HTTPException(status_code=403, detail="教师账号正在认证审核中，暂无管理权限")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    """系统级设置权限: 仅限 admin。"""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可执行此操作")
    return user


def can_manage(user: dict) -> bool:
    if user["role"] == "admin":
        return True
    if user["role"] == "teacher" and user.get("status") == "active":
        return True
    return False
