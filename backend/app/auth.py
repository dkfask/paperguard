"""用户认证与权限。

- 密码: PBKDF2-HMAC-SHA256 + 随机盐 (标准库, 无额外依赖)
- 会话: 随机 token 存 users 表, 请求头 Authorization: Bearer <token>
- 角色: admin(全权限) / teacher(模板管理) / student(写作)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, Header, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.core.context import context_for_user, set_request_context
from app.services.tenant_access import effective_capabilities, resolve_membership
from app.db import Store, new_id
from app.services import emailer
from app.services.role_access import require_business_role, require_session_role

_PBKDF2_ITERATIONS = 200_000

_SESSION_HOURS = 24
_MP_SESSION_DAYS = 30
_MP_BIND_TICKET_MINUTES = 10
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_CODE_MINUTES = 10
_EMAIL_CODE_MAX_ATTEMPTS = 5
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


def _expires_at(*, hours: int | None = None, days: int | None = None) -> str:
    delta = timedelta(hours=hours or 0, days=days or 0)
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


def _password_error(password: str) -> str | None:
    if len(password) < 8:
        return "密码至少 8 位"
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        return "密码需同时包含字母和数字"
    return None


def _credential_user_conn(conn, user: dict):
    """Check a trusted request's credential snapshot under the writer lock."""
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    if row["status"] not in {"active", "pending"}:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    if (not hmac.compare_digest(row["password_hash"], user["password_hash"])
            or not hmac.compare_digest(row["salt"], user["salt"])
            or row["email"] != (user.get("email") or "")
            or row["role"] != user["role"]
            or row["status"] != user.get("status", "active")):
        raise HTTPException(status_code=401, detail="登录信息已变化，请重新登录")
    return row


def _replace_credentials(store: Store, user: dict, password: str, *,
                         email: str | None = None, revoke_sessions: bool = True,
                         must_change_password: bool = False, actor: dict | None = None) -> dict:
    password_hash, salt = hash_password(password)
    with store.workflow_transaction() as conn:
        if actor is not None:
            _credential_user_conn(conn, actor)
        _credential_user_conn(conn, user)
        if email is not None:
            if conn.execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, user["id"])).fetchone():
                raise HTTPException(status_code=409, detail={"code": "EMAIL_TAKEN", "message": "该邮箱已被其他账号使用"})
            conn.execute(
                "UPDATE users SET email = ?, email_verified = 1, password_set = 1, "
                "password_hash = ?, salt = ?, must_change_password = ?, token = NULL WHERE id = ?",
                (email, password_hash, salt, int(must_change_password), user["id"]),
            )
        else:
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, password_set = 1, "
                "must_change_password = ?, token = NULL WHERE id = ?",
                (password_hash, salt, int(must_change_password), user["id"]),
            )
        if revoke_sessions:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_now(), user["id"]),
            )
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return store._hydrate_user(dict(updated))


def _save_user_metadata(store: Store, target: dict, fields: tuple[str, ...], *, actor: dict) -> None:
    """Metadata updates must never replay a stale full user's credentials."""
    allowed = {"role", "status", "display_name", "teacher_id", "organization_context",
               "requested_role", "requested_organization_context", "role_application_status"}
    if not fields or not set(fields).issubset(allowed):
        raise ValueError("unsupported user metadata fields")
    params = [json.dumps(target.get(field) or {}, ensure_ascii=False)
              if field in {"organization_context", "requested_organization_context"}
              else target.get(field) for field in fields]
    with store.workflow_transaction() as conn:
        _credential_user_conn(conn, actor)
        changed = conn.execute(
            "UPDATE users SET " + ", ".join(f"{field} = ?" for field in fields) + " WHERE id = ?",
            (*params, target["id"]),
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=404, detail="用户不存在")


def register_user(store: Store, username: str, password: str,
                  display_name: str = "", role: str = "student",
                  email: str = "", email_code: str = "") -> dict:
    """注册新用户, 返回用户 dict(不含敏感字段)。

    - 学生直接 active; 教师默认 pending 待管理员认证
    - username 可缺省: 提供邮箱时由邮箱本地部分派生
    - 个人邮箱注册在邮件通道可用时必须完成 register 验证码
    - 无邮箱的旧账号注册保留为兼容路径, 不作为新的个人邮箱注册入口
    """
    username = username.strip()
    email = email.strip().lower()
    email_verified = 0
    if email:
        _validate_email(store, email)
        if not emailer.email_configured():
            raise HTTPException(status_code=503, detail={
                "code": "EMAIL_NOT_CONFIGURED", "message": "邮件服务尚未配置，暂不能使用个人邮箱注册",
            })
        if not email_code.strip():
            raise HTTPException(status_code=422, detail={
                "code": "EMAIL_CODE_REQUIRED", "message": "请先获取并填写邮箱验证码",
            })
        _verify_email_code(store, email, email_code.strip(), purpose="register", consume=False)
        email_verified = 1
    if not username and email:
        username = _username_from_email(store, email)
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少 3 个字符")
    password_error = _password_error(password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    if store.get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")

    # Public registration is always identity-neutral. Role assignment belongs
    # to the authenticated administrator/import path and must not be selected
    # by an unauthenticated caller through the request body.
    target_role = "user"
    status = "active"
    application_status = "unselected"

    ph, salt = hash_password(password)
    user = store.save_user({
        "id": new_id("user"),
        "username": username,
        "email": email,
        "email_verified": email_verified,
        "password_set": 1,
        "password_hash": ph,
        "salt": salt,
        "role": target_role,
        "display_name": display_name.strip() or username,
        "token": None,
        "must_change_password": 0,
        "status": status,
        "requested_role": None,
        "requested_organization_context": {},
        "role_application_status": application_status,
        "created_at": _now(),
    })
    if email:
        store.delete_email_code(email)
    return public_user(user)


def _validate_email(store: Store, email: str) -> None:
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail={"code": "EMAIL_INVALID", "message": "邮箱格式不正确"})
    if store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail={"code": "EMAIL_TAKEN", "message": "该邮箱已被其他账号使用"})


def _validate_email_for_user(store: Store, email: str, user_id: str) -> None:
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail={"code": "EMAIL_INVALID", "message": "邮箱格式不正确"})
    existing = store.get_user_by_email(email)
    if existing and existing.get("id") != user_id:
        raise HTTPException(status_code=409, detail={"code": "EMAIL_TAKEN", "message": "该邮箱已被其他账号使用"})


def complete_first_login(store: Store, user: dict, email: str, code: str, new_password: str) -> dict:
    """学校统一注册学生首次绑定邮箱并设置新密码。"""
    if user.get("role") != "student":
        raise HTTPException(status_code=403, detail={
            "code": "FIRST_LOGIN_STUDENT_REQUIRED", "message": "首次学生引导仅适用于学生账号",
        })
    if not onboarding_required(user):
        raise HTTPException(status_code=409, detail={
            "code": "ONBOARDING_NOT_REQUIRED", "message": "账号已完成首次登录引导",
        })
    email = email.strip().lower()
    _validate_email_for_user(store, email, user["id"])
    if not code.strip():
        raise HTTPException(status_code=422, detail={
            "code": "EMAIL_CODE_REQUIRED", "message": "请先获取并填写邮箱验证码",
        })
    _verify_email_code(store, email, code.strip(), purpose="first_login")
    password_error = _password_error(new_password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    updated = _replace_credentials(store, user, new_password, email=email)
    return _issue_session(store, updated, source=user.get("session_source", "web"))


def onboarding_required(user: dict) -> bool:
    """学生首次邮箱验证/密码初始化未完成时阻断业务访问。"""
    if user.get("role") != "student":
        return False
    # Legacy no-email accounts created before the dual-track rollout remain usable.
    if not user.get("email") and not user.get("must_change_password") and user.get("password_set", 1):
        return False
    return not bool(user.get("email_verified")) or not bool(user.get("password_set", 1)) or bool(user.get("must_change_password"))


def _username_from_email(store: Store, email: str) -> str:
    """由邮箱本地部分派生唯一用户名(不合法字符转 '-', 冲突加随机后缀)。"""
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", email.split("@", 1)[0]).strip("-") or "user"
    candidate = base[:24]
    while len(candidate) < 3 or store.get_user_by_username(candidate):
        candidate = f"{base[:18]}-{secrets.token_hex(3)}"
    return candidate


def _email_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _password_reset_code_hash(email: str, code: str) -> str:
    from app.services.secret_store import SecretDecryptError, _require_master_key
    try:
        key = _require_master_key()
    except SecretDecryptError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "PASSWORD_RECOVERY_NOT_CONFIGURED",
            "message": "密码恢复服务尚未配置，请联系导师或管理员重置密码",
        }) from exc
    payload = f"paperguard:password-reset:v1:{email.strip().lower()}:{code}"
    return "pgrst1$" + hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def send_email_code(store: Store, email: str, purpose: str = "register") -> dict:
    """生成并邮件发送 6 位验证码; SMTP 未配置时明确报 503。"""
    if purpose not in {"register", "bind", "first_login", "password_reset"}:
        raise HTTPException(status_code=422, detail={
            "code": "EMAIL_PURPOSE_INVALID", "message": "邮箱验证码用途不受支持",
        })
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail={"code": "EMAIL_INVALID", "message": "邮箱格式不正确"})
    existing = store.get_email_code(email)
    if existing and (datetime.now(timezone.utc) - datetime.fromisoformat(existing["created_at"])).total_seconds() < 60:
        raise HTTPException(status_code=429, detail={"code": "EMAIL_CODE_COOLDOWN", "message": "验证码发送过于频繁, 请 1 分钟后再试"})
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = _password_reset_code_hash(email, code) if purpose == "password_reset" else _email_code_hash(code)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=_EMAIL_CODE_MINUTES)).isoformat(timespec="seconds")
    try:
        emailer.send_verification_code(email, code, _EMAIL_CODE_MINUTES)
    except emailer.EmailNotConfigured as exc:
        raise HTTPException(status_code=503, detail={
            "code": "EMAIL_NOT_CONFIGURED",
            "message": ("邮件服务尚未配置，请联系导师或管理员重置密码"
                        if purpose == "password_reset"
                        else "邮件服务尚未配置, 请联系管理员或使用账号密码注册"),
        }) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail={
            "code": "EMAIL_SEND_FAILED", "message": "验证码邮件发送失败, 请稍后重试",
        }) from exc
    store.save_email_code(email, code_hash, purpose, expires_at, _now())
    return {"ok": True, "expires_in": _EMAIL_CODE_MINUTES * 60}


def _verify_email_code(store: Store, email: str, code: str, *, purpose: str, consume: bool = True) -> None:
    record = store.get_email_code(email)
    if not record or record.get("purpose") != purpose:
        raise HTTPException(status_code=400, detail={"code": "EMAIL_CODE_INVALID", "message": "请先获取邮箱验证码"})
    if record["expires_at"] <= _now():
        raise HTTPException(status_code=400, detail={"code": "EMAIL_CODE_EXPIRED", "message": "验证码已过期, 请重新获取"})
    if int(record.get("attempts", 0)) >= _EMAIL_CODE_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail={"code": "EMAIL_CODE_LOCKED", "message": "尝试次数过多, 请重新获取验证码"})
    expected_hash = (_password_reset_code_hash(email, code.strip()) if purpose == "password_reset"
                     else _email_code_hash(code.strip()))
    if not hmac.compare_digest(record["code_hash"], expected_hash):
        store.bump_email_code_attempts(email)
        raise HTTPException(status_code=400, detail={"code": "EMAIL_CODE_MISMATCH", "message": "验证码不正确"})
    if consume:
        store.delete_email_code(email)


def _issue_session(store: Store, user: dict, *, source: str = "web") -> dict:
    token = secrets.token_hex(32)
    now = _now()
    # Recheck the credential snapshot under the same write lock used by reset.
    # A login verified before a reset cannot insert a valid session afterward.
    with store.workflow_transaction() as conn:
        row = _credential_user_conn(conn, user)
        platform_bound = conn.execute(
            "SELECT 1 FROM platform_role_bindings "
            "WHERE user_id = ? AND role_code = 'platform_admin' AND status = 'active'",
            (user["id"],),
        ).fetchone() is not None
        require_session_role(dict(row), platform_bound=platform_bound)
        conn.execute(
            "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, revoked_at, last_seen_at, source) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (new_id("session"), user["id"], _token_hash(token), now,
             _expires_at(days=_MP_SESSION_DAYS) if source == "mp" else _expires_at(hours=_SESSION_HOURS),
             now, source),
        )
        current = store._hydrate_user(dict(row))
    return {"token": token, "user": public_user(current)}


def login_user(store: Store, username: str, password: str) -> dict:
    """登录, 返回带过期时间的随机 bearer token。identifier 支持用户名或邮箱。"""
    user = store.get_user_by_identifier(username.strip())
    if user is None or not verify_password(password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _issue_session(store, user)


def _mini_program_binding_secret() -> str:
    secret = os.environ.get("PAPERGUARD_MP_BINDING_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail={
            "code": "MP_NOT_CONFIGURED",
            "message": "小程序登录尚未配置服务端密钥",
        })
    return secret


def _mini_program_openid_hash(openid: str) -> str:
    return hmac.new(
        _mini_program_binding_secret().encode("utf-8"),
        openid.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _openid_cipher(openid: str) -> str:
    """openid 的可逆保护: 绑定密钥派生流加密 + 随机 nonce, 仅用于服务端推送。"""
    secret = _mini_program_binding_secret().encode("utf-8")
    nonce = os.urandom(12)
    data = openid.encode("utf-8")
    stream = b""
    block = 0
    while len(stream) < len(data):
        stream += hashlib.sha256(nonce + block.to_bytes(4, "big") + secret).digest()
        block += 1
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    return base64.urlsafe_b64encode(nonce + cipher).decode("ascii")


def _openid_from_cipher(cipher_text: str) -> str:
    raw = base64.urlsafe_b64decode(cipher_text.encode("ascii"))
    secret = _mini_program_binding_secret().encode("utf-8")
    nonce, cipher = raw[:12], raw[12:]
    stream = b""
    block = 0
    while len(stream) < len(cipher):
        stream += hashlib.sha256(nonce + block.to_bytes(4, "big") + secret).digest()
        block += 1
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")


async def exchange_mp_code(code: str) -> str:
    """Exchange a WeChat login code without exposing provider credentials."""
    app_id = os.environ.get("PAPERGUARD_WECHAT_APP_ID", "").strip()
    app_secret = os.environ.get("PAPERGUARD_WECHAT_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise HTTPException(status_code=503, detail={
            "code": "MP_NOT_CONFIGURED",
            "message": "小程序登录尚未配置微信服务端参数",
        })
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={"appid": app_id, "secret": app_secret, "js_code": code, "grant_type": "authorization_code"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail={
            "code": "MP_PROVIDER_UNAVAILABLE",
            "message": "微信登录服务暂时不可用",
        }) from exc
    if not isinstance(payload, dict) or payload.get("errcode") or not payload.get("openid"):
        raise HTTPException(status_code=401, detail={
            "code": "MP_LOGIN_FAILED",
            "message": "微信登录凭证无效，请重试",
        })
    return str(payload["openid"])


def mini_program_login(store: Store, openid: str) -> dict:
    openid_hash = _mini_program_openid_hash(openid)
    binding = store.get_mini_program_binding_by_openid(openid_hash)
    if not binding:
        ticket = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=_MP_BIND_TICKET_MINUTES)).isoformat(timespec="seconds")
        store.create_mini_program_bind_ticket({
            "id": new_id("mp-ticket"),
            "ticket_hash": _token_hash(ticket),
            "openid_hash": openid_hash,
            "openid_cipher": _openid_cipher(openid),
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": expires_at,
        })
        return {"bound": False, "bind_ticket": ticket, "expires_at": expires_at}
    user = store.get_user(binding["user_id"])
    if not user or user.get("status") != "active":
        raise HTTPException(status_code=403, detail={
            "code": "ACCOUNT_UNAVAILABLE",
            "message": "账号当前不可使用",
        })
    return {"bound": True, **_issue_session(store, user, source="mp")}


def mini_program_bind(store: Store, bind_ticket: str, username: str, password: str) -> dict:
    ticket = store.get_mini_program_bind_ticket(_token_hash(bind_ticket))
    if not ticket or ticket.get("used_at") or ticket["expires_at"] <= _now():
        raise HTTPException(status_code=400, detail={
            "code": "MP_BIND_TICKET_INVALID",
            "message": "绑定已失效，请重新发起微信登录",
        })
    user = store.get_user_by_identifier(username.strip())
    if user is None or not verify_password(password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=401, detail={
            "code": "INVALID_CREDENTIALS",
            "message": "用户名或密码错误",
        })
    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail={
            "code": "ACCOUNT_UNAVAILABLE",
            "message": "账号当前不可绑定",
        })
    require_session_role(user)
    result = store.bind_mini_program(
        _token_hash(bind_ticket), user["id"], ticket["openid_hash"],
        str(ticket.get("openid_cipher") or ""), _now(),
    )
    if result is None:
        raise HTTPException(status_code=400, detail={
            "code": "MP_BIND_TICKET_INVALID",
            "message": "绑定已失效，请重新发起微信登录",
        })
    if result.get("conflict"):
        raise HTTPException(status_code=409, detail={
            "code": "MP_BINDING_CONFLICT",
            "message": "微信或账号已绑定其他身份",
        })
    return {"bound": True, **_issue_session(store, user, source="mp")}


def mp_register(store: Store, bind_ticket: str) -> dict:
    """微信一键注册: 无需预先拥有校内账号, 以 openid 直接建号并绑定。

    新账号 username 自动生成、无密码(password_set=0), 登录仅经微信;
    绑定邮箱并设置密码后才可在桌面端使用账号密码登录(见 mp_bind_email)。
    """
    ticket = store.get_mini_program_bind_ticket(_token_hash(bind_ticket))
    if not ticket or ticket.get("used_at") or ticket["expires_at"] <= _now():
        raise HTTPException(status_code=400, detail={
            "code": "MP_BIND_TICKET_INVALID", "message": "注册已失效，请重新发起微信登录",
        })
    openid_hash = ticket["openid_hash"]
    if store.get_mini_program_binding_by_openid(openid_hash):
        raise HTTPException(status_code=409, detail={
            "code": "MP_BINDING_CONFLICT", "message": "该微信已绑定账号，请直接登录",
        })
    username = f"wx_{openid_hash[:10]}"
    while store.get_user_by_username(username):
        username = f"wx_{secrets.token_hex(4)}"
    user = store.save_user({
        "id": new_id("user"),
        "username": username,
        "password_hash": secrets.token_hex(32),  # 占位: 绑定邮箱时设置真实密码
        "salt": secrets.token_hex(16),
        "role": "student",
        "display_name": "微信用户",
        "token": None,
        "must_change_password": 0,
        "password_set": 0,
        "status": "active",
        "created_at": _now(),
    })
    result = store.bind_mini_program(
        _token_hash(bind_ticket), user["id"], openid_hash,
        str(ticket.get("openid_cipher") or ""), _now(),
    )
    if result is None or result.get("conflict"):
        raise HTTPException(status_code=409, detail={
            "code": "MP_BINDING_CONFLICT", "message": "该微信已绑定账号，请直接登录",
        })
    return {"bound": True, "needs_email_setup": True, **_issue_session(store, user, source="mp")}


def mp_bind_email(store: Store, user: dict, email: str, code: str, password: str) -> dict:
    """为账号绑定邮箱并设置密码(微信注册账号的桌面端凭证来源)。"""
    if user.get("session_source") != "mp":
        raise HTTPException(status_code=403, detail={
            "code": "MP_SESSION_REQUIRED", "message": "请使用小程序会话执行绑定",
        })
    email = email.strip().lower()
    _validate_email(store, email)
    if user.get("email"):
        raise HTTPException(status_code=409, detail={
            "code": "EMAIL_ALREADY_BOUND", "message": "该账号已绑定邮箱，换绑请联系管理员",
        })
    if not emailer.email_configured():
        raise HTTPException(status_code=503, detail={
            "code": "EMAIL_NOT_CONFIGURED", "message": "邮件服务尚未配置，暂不能完成邮箱绑定",
        })
    if not code.strip():
        raise HTTPException(status_code=422, detail={
            "code": "EMAIL_CODE_REQUIRED", "message": "请先获取并填写邮箱验证码",
        })
    _verify_email_code(store, email, code.strip(), purpose="bind")
    password_error = _password_error(password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    updated = _replace_credentials(store, user, password, email=email, revoke_sessions=False)
    return public_user(updated)


def report_mp_subscription(store: Store, user: dict, template_id: str, accepted: bool) -> int:
    """记录小程序内订阅消息授权结果: 接受 +1 次推送机会, 拒绝清零。"""
    now = _now()
    if accepted:
        return store.grant_push_quota(user["id"], template_id, 1, now)
    store.reset_push_quota(user["id"], template_id, now)
    return 0


def mini_program_unbind(store: Store, user: dict, password: str) -> None:
    if user.get("session_source") != "mp":
        raise HTTPException(status_code=403, detail={
            "code": "MP_SESSION_REQUIRED",
            "message": "请使用小程序会话执行解绑",
        })
    if not verify_password(password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_PASSWORD",
            "message": "当前密码不正确",
        })
    store.delete_mini_program_binding(user["id"])


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
    _replace_credentials(store, user, new_password)


def public_user(user: dict) -> dict:
    """去除敏感字段。"""
    email = str(user.get("email") or "")
    if email and "@" in email:
        local, domain = email.split("@", 1)
        email = f"{local[:2]}***@{domain}"
    return {
        "id": user["id"],
        "username": user["username"],
        "email": email,
        "email_verified": bool(user.get("email_verified", 0)),
        "password_set": bool(user.get("password_set", 1)),
        "onboarding_required": onboarding_required(user),
        "role": user["role"],
        "display_name": user["display_name"],
        "status": user.get("status", "active"),
        "teacher_id": user.get("teacher_id"),
        "must_change_password": bool(user.get("must_change_password", 0)),
        "organization_context": user.get("organization_context") or {},
        "tenant_id": user.get("tenant_id") or None,
        "membership_id": user.get("membership_id") or None,
        "capabilities": sorted({str(item) for item in (user.get("capabilities") or [])}),
        "auth_version": int(user.get("auth_version") or 0),
        "requested_role": user.get("requested_role"),
        "requested_organization_context": user.get("requested_organization_context") or {},
        "role_application_status": user.get("role_application_status", "unselected"),
    }


_ONBOARDING_EXEMPT_PATHS = {
    "/api/auth/me",
    "/api/auth/first-login/complete",
    "/api/auth/change-password",
    "/api/auth/mp/bind-email",
    "/api/auth/mp/unbind",
}


def _onboarding_path_exempt(path: str) -> bool:
    return path in _ONBOARDING_EXEMPT_PATHS or path == "/api/auth/logout" or path == "/api/auth/email/code"


def get_store() -> Store:
    """Store 依赖。

    全项目统一用 `Depends(get_store)` 注入，路由里不再写
    `Depends(lambda: Store())`——两种写法并存时，测试要 monkeypatch
    的入口就有两个，漏掉一个就会出现「测试全绿但线上走了另一条路径」。
    """
    return Store()


async def current_user(request: Request, authorization: str | None = Header(default=None),
                 store: Store = Depends(get_store)) -> dict:
    """从 Authorization 头解析未过期、未撤销的当前用户, 并阻断未完成学生引导的业务请求。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    user = await run_in_threadpool(
        store.get_session_user, _token_hash(token), _now(),
        refresh_expires_at=_expires_at(days=_MP_SESSION_DAYS),
    )
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效, 请重新登录")
    if user.get("status", "active") not in {"active", "pending"}:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    require_business_role(user, path=request.url.path)
    if onboarding_required(user) and not _onboarding_path_exempt(request.url.path):
        raise HTTPException(status_code=403, detail={
            "code": "ONBOARDING_REQUIRED", "message": "请先完成邮箱验证和首次登录设置",
        })
    # Shared members (including revoked members) cannot fall through global-role
    # legacy resource APIs. Genuine unmapped single-instance users retain their
    # old flow, but Store also hides tenant-bound project rows from that flow.
    legacy_resources = ("/api/projects", "/api/workflow", "/api/process-records",
                        "/api/document", "/api/templates", "/api/policies", "/api/specs",
                        "/api/teacher", "/api/organizations")
    if request.url.path.startswith(legacy_resources) and (
        os.environ.get("PAPERGUARD_TENANCY_MODE", "standalone") == "shared"
        or user.get("tenant_id") or (user.get("organization_context") or {}).get("tenant_id")
        or await run_in_threadpool(store.list_memberships_for_user, user["id"])
    ):
        raise HTTPException(status_code=403, detail={
            "code": "LEGACY_TENANT_PATH_DISABLED", "message": "租户成员必须使用已隔离的业务入口",
        })
    # This header is only a selector: resolve_membership checks persisted
    # ownership and current member/tenant state before exposing credentials.
    expected_tenant = request.headers.get("X-Expected-Tenant", "").strip() if request.url.path.startswith("/api/ai/") else ""
    selected_user = user | {"tenant_id": expected_tenant} if expected_tenant else user
    membership = await run_in_threadpool(resolve_membership, store, selected_user)
    explicit_tenant = str(selected_user.get("tenant_id") or (user.get("organization_context") or {}).get("tenant_id") or "").strip()
    unresolved_ai_identity = (
        membership is None and request.url.path.startswith("/api/ai/")
        and bool(await run_in_threadpool(store.list_memberships_for_user, user["id"]))
    )
    if membership is None and (explicit_tenant or unresolved_ai_identity):
        raise HTTPException(status_code=403, detail={
            "code": "TENANT_CONTEXT_INVALID", "message": "租户上下文无效或成员身份已停用",
        })
    if membership:
        require_business_role(membership)
        user["tenant_id"] = membership.get("tenant_id", "")
        user["membership_id"] = membership.get("id", "")
        user["auth_version"] = int(membership.get("auth_version") or 1)
    user["capabilities"] = sorted(effective_capabilities(user, membership))
    user.setdefault("auth_version", 1)
    context = context_for_user(
        user, request_id=getattr(request.state, "request_id", ""), membership=membership
    )
    request.state.context = context
    request.state.actor_id = str(user.get("id") or "")
    request.state.logging_metadata = {"legacy_role": user.get("role", "")}
    set_request_context(context)
    return user


def require_manager(user: dict = Depends(current_user)) -> dict:
    """模板管理权限: admin / 已认证 teacher。"""
    if user["role"] not in MANAGE_ROLES:
        raise HTTPException(status_code=403, detail="仅教师/管理员可管理模板")
    if user["role"] == "teacher" and user.get("status") != "active":
        raise HTTPException(status_code=403, detail="账号正在认证审核中，暂无管理权限")
    return user


def require_template_manager(user: dict = Depends(current_user)) -> dict:
    """模板权限只授予学校管理员与已认证指导教师。"""
    if user["role"] not in ("admin", "teacher"):
        raise HTTPException(status_code=403, detail="仅教师/学校管理员可管理模板")
    if user["role"] == "teacher" and user.get("status") != "active":
        raise HTTPException(status_code=403, detail="教师账号正在认证审核中，暂无模板管理权限")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    """系统级设置权限: 仅限 admin。"""
    if user["role"] != "admin" or user.get("status") != "active":
        raise HTTPException(status_code=403, detail="仅系统管理员可执行此操作")
    return user


def can_manage(user: dict) -> bool:
    if user["role"] == "admin":
        return True
    if user["role"] == "teacher" and user.get("status") == "active":
        return True
    return False


_saas_bearer = HTTPBearer(auto_error=False, scheme_name="bearerAuth")


async def trusted_context(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Security(_saas_bearer),
    expected_tenant: str | None = Header(default=None, alias="X-Expected-Tenant"),
    store: Store = Depends(get_store),
):
    """Shared-SaaS boundary; does not reuse permissive legacy user/role guards."""
    from app.services.tenant_access import resolve_request_context

    user = None
    if credentials is not None:
        token = credentials.credentials.strip()
        user = await run_in_threadpool(
            store.get_session_user, _token_hash(token), _now(),
            refresh_expires_at=_expires_at(days=_MP_SESSION_DAYS),
        )
    if user is not None:
        from app.services.saas_identity import platform_binding
        binding = await run_in_threadpool(platform_binding, store, user["id"])
        require_session_role(user, platform_bound=binding is not None)
        if binding is None:
            require_business_role(user)
    context = await run_in_threadpool(
        resolve_request_context, store, user, request_id=getattr(request.state, "request_id", ""),
        expected_tenant=expected_tenant,
    )
    if user is not None and context.actor_kind != "platform":
        require_business_role(user)
        require_business_role({"role": context.role})
    if user is not None and onboarding_required(user):
        raise HTTPException(status_code=403, detail={"code": "AUTH_ACCOUNT_BLOCKED", "message": "请先完成账号设置"})
    request.state.context = context
    request.state.actor_id = context.actor_id
    set_request_context(context)
    return context
