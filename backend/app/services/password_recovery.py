"""通过已验证的绑定邮箱恢复密码；沿用验证码账本，无新增迁移。"""

from __future__ import annotations

import hmac
import re

from fastapi import HTTPException

from app.auth import _EMAIL_CODE_MAX_ATTEMPTS, _EMAIL_RE, _password_reset_code_hash, _now, hash_password, verify_password
from app.db import Store


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def reset_password(store: Store, email: str, code: str, new_password: str) -> None:
    email, code = email.strip().lower(), code.strip()
    if not _EMAIL_RE.match(email):
        raise _error(422, "EMAIL_INVALID", "邮箱格式不正确")
    if not re.fullmatch(r"[0-9]{6}", code):
        raise _error(422, "EMAIL_CODE_REQUIRED", "请填写 6 位邮箱验证码")
    if len(new_password) < 12:
        raise _error(400, "PASSWORD_TOO_SHORT", "新密码至少 12 位")
    if not any(ch.isalpha() for ch in new_password) or not any(ch.isdigit() for ch in new_password):
        raise _error(400, "PASSWORD_INVALID", "新密码需同时包含字母和数字")

    mismatch = None
    with store.workflow_transaction() as conn:
        # Lock before checking: a code cannot be replayed by concurrent resets,
        # and attempt increments must survive an invalid-code response.
        now = _now()
        record = conn.execute(
            "SELECT * FROM email_verification_codes WHERE email = ?", (email,),
        ).fetchone()
        if not record or record["purpose"] != "password_reset":
            raise _error(400, "EMAIL_CODE_INVALID", "请先获取重置密码的邮箱验证码")
        if record["expires_at"] <= now:
            raise _error(400, "EMAIL_CODE_EXPIRED", "验证码已过期，请重新获取")
        if int(record["attempts"]) >= _EMAIL_CODE_MAX_ATTEMPTS:
            raise _error(429, "EMAIL_CODE_LOCKED", "尝试次数过多，请重新获取验证码")
        if not hmac.compare_digest(record["code_hash"], _password_reset_code_hash(email, code)):
            conn.execute(
                "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE email = ?", (email,),
            )
            mismatch = _error(400, "EMAIL_CODE_MISMATCH", "验证码不正确")
        else:
            user = conn.execute(
                "SELECT id, password_hash, salt FROM users WHERE email = ? AND email_verified = 1 "
                "AND status IN ('active', 'pending')", (email,),
            ).fetchone()
            if not user:
                raise _error(400, "PASSWORD_RECOVERY_UNAVAILABLE",
                             "无法使用该邮箱重置密码，请确认已绑定并验证邮箱，或联系导师、管理员")
            if verify_password(new_password, user["password_hash"], user["salt"]):
                raise _error(400, "PASSWORD_UNCHANGED", "新密码不能与当前密码相同")
            password_hash, salt = hash_password(new_password)
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, password_set = 1, "
                "must_change_password = 0, token = NULL WHERE id = ?",
                (password_hash, salt, user["id"]),
            )
            conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, user["id"]),
            )
            store.save_audit_event_conn(conn, {
                "actor_id": user["id"], "action": "auth.password_reset",
                "resource_type": "user", "resource_id": user["id"],
            })
    if mismatch:
        raise mismatch
