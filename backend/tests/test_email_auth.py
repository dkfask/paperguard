"""邮箱注册 / 微信一键注册 / 邮箱绑定回归。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.auth import _email_code_hash, hash_password
from app.db import Store, new_id
from app.main import app
from app.services import emailer


client = TestClient(app)


def _register_payload(**overrides) -> dict:
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"mail_{suffix}",
        "password": f"Mail-{uuid.uuid4().hex}-A1",
        "display_name": "邮箱注册用户",
        "role": "student",
    }
    payload.update(overrides)
    return payload


def test_register_with_email_and_login_by_email(monkeypatch):
    monkeypatch.delenv("PAPERGUARD_RESEND_API_KEY", raising=False)
    monkeypatch.setattr(emailer, "email_configured", lambda: True)
    captured: dict[str, str] = {}

    def fake_send(to: str, code: str, minutes: int = 10):
        captured["to"], captured["code"] = to, code

    monkeypatch.setattr(emailer, "send_verification_code", fake_send)
    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    password = f"Mail-{uuid.uuid4().hex}-A1"

    sent = client.post("/api/auth/email/code", json={"email": email})
    assert sent.status_code == 200
    assert captured["to"] == email

    registered = client.post(
        "/api/auth/register",
        json=_register_payload(email=email, password=password, email_code=captured["code"]),
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["username"].startswith("mail_")

    by_email = client.post("/api/auth/login", json={"username": email, "password": password})
    assert by_email.status_code == 200, by_email.text

    duplicate = client.post(
        "/api/auth/register",
        json=_register_payload(email=email, password=password, email_code=captured["code"]),
    )
    assert duplicate.status_code == 409

    bad_format = client.post("/api/auth/register", json=_register_payload(email="not-an-email", password=password))
    assert bad_format.status_code == 422


def test_register_with_email_requires_configured_provider(monkeypatch):
    monkeypatch.delenv("PAPERGUARD_RESEND_API_KEY", raising=False)
    monkeypatch.delenv("PAPERGUARD_SMTP_HOST", raising=False)
    monkeypatch.setattr(emailer, "email_configured", lambda: False)
    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    response = client.post(
        "/api/auth/register",
        json=_register_payload(email=email, password=f"Mail-{uuid.uuid4().hex}-A1", email_code="123456"),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EMAIL_NOT_CONFIGURED"


def test_email_code_flow_required_when_smtp_configured(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_SMTP_HOST", "smtp.example.edu")
    monkeypatch.setenv("PAPERGUARD_SMTP_FROM", "noreply@example.edu")
    captured: dict[str, str] = {}

    def fake_send(to: str, code: str, minutes: int = 10):
        captured["to"], captured["code"] = to, code

    monkeypatch.setattr(emailer, "smtp_configured", lambda: False)
    monkeypatch.setattr(emailer, "email_configured", lambda: True)
    monkeypatch.setattr(emailer, "send_verification_code", fake_send)
    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    password = f"Mail-{uuid.uuid4().hex}-A1"

    missing = client.post("/api/auth/register", json=_register_payload(email=email, password=password))
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "EMAIL_CODE_REQUIRED"

    sent = client.post("/api/auth/email/code", json={"email": email})
    assert sent.status_code == 200
    assert captured["to"] == email

    wrong = client.post("/api/auth/register", json=_register_payload(email=email, password=password, email_code="000000"))
    assert wrong.status_code == 400

    ok = client.post("/api/auth/register", json=_register_payload(email=email, password=password, email_code=captured["code"]))
    assert ok.status_code == 201
    login = client.post("/api/auth/login", json={"username": email, "password": password})
    assert login.status_code == 200
    assert login.json()["user"]["email_verified"] is True
    store = Store()
    assert store.get_email_code(email) is None


def test_mp_wechat_register_then_bind_email(monkeypatch):
    monkeypatch.delenv("PAPERGUARD_RESEND_API_KEY", raising=False)
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    monkeypatch.setattr(emailer, "email_configured", lambda: True)
    captured: dict[str, str] = {}

    def fake_send(to: str, code: str, minutes: int = 10):
        captured["to"], captured["code"] = to, code

    monkeypatch.setattr(emailer, "send_verification_code", fake_send)
    suffix = uuid.uuid4().hex[:10]

    async def exchange(_code: str) -> str:
        return f"openid-wxreg-{suffix}"

    monkeypatch.setattr("app.api.auth.exchange_mp_code", exchange)
    login = client.post("/api/auth/mp/login", json={"code": f"code-{suffix}"})
    ticket = login.json()["bind_ticket"]

    registered = client.post("/api/auth/mp/register", json={"bind_ticket": ticket})
    assert registered.status_code == 201, registered.text
    body = registered.json()
    assert body["needs_email_setup"] is True
    assert body["user"]["password_set"] is False
    assert body["user"]["username"].startswith("wx_")

    # 未设密码前无法用账号密码登录(密码为占位随机值)
    denied = client.post("/api/auth/login", json={"username": body["user"]["username"], "password": f"wrong-{uuid.uuid4().hex}"})
    assert denied.status_code == 401

    email = f"{suffix}@example.edu"
    new_password = f"Mail-{uuid.uuid4().hex}-A1"
    code_resp = client.post(
        "/api/auth/email/code",
        headers={"Authorization": f"Bearer {body['token']}"},
        json={"email": email, "purpose": "bind"},
    )
    assert code_resp.status_code == 200
    bound = client.post(
        "/api/auth/mp/bind-email",
        headers={"Authorization": f"Bearer {body['token']}"},
        json={"email": email, "email_code": captured["code"], "password": new_password},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["user"]["password_set"] is True
    assert bound.json()["user"]["email_verified"] is True

    web_login = client.post("/api/auth/login", json={"username": email, "password": new_password})
    assert web_login.status_code == 200

    again = client.post("/api/auth/mp/login", json={"code": f"code-{suffix}"})
    assert again.status_code == 200 and again.json()["bound"] is True

    duplicate = client.post("/api/auth/mp/register", json={"bind_ticket": ticket})
    assert duplicate.status_code in (400, 409)


def test_mp_bind_accepts_email_identifier(monkeypatch):
    monkeypatch.delenv("PAPERGUARD_RESEND_API_KEY", raising=False)
    monkeypatch.setattr(emailer, "email_configured", lambda: True)
    monkeypatch.setattr(emailer, "send_verification_code", lambda *_args, **_kwargs: None)
    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    password = f"Mail-{uuid.uuid4().hex}-A1"
    code_resp = client.post("/api/auth/email/code", json={"email": email})
    assert code_resp.status_code == 200
    store = Store()
    record = store.get_email_code(email)
    assert record is not None
    store.save_email_code(email, _email_code_hash("123456"), "register", record["expires_at"], record["created_at"])
    registered = client.post(
        "/api/auth/register",
        json=_register_payload(email=email, password=password, email_code="123456"),
    )
    assert registered.status_code == 201

    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")

    async def exchange(_code: str) -> str:
        return f"openid-mailbind-{uuid.uuid4().hex[:8]}"

    monkeypatch.setattr("app.api.auth.exchange_mp_code", exchange)
    login = client.post("/api/auth/mp/login", json={"code": "code-mailbind"})
    ticket = login.json()["bind_ticket"]

    bound = client.post("/api/auth/mp/bind", json={"bind_ticket": ticket, "username": email, "password": password})
    assert bound.status_code == 200, bound.text
    assert bound.json()["user"]["email"].endswith("@example.edu")


def test_school_student_first_login_is_gated_and_rotates_session(monkeypatch):
    monkeypatch.setattr(emailer, "email_configured", lambda: True)
    captured: dict[str, str] = {}

    def fake_send(to: str, code: str, minutes: int = 10):
        captured["to"], captured["code"] = to, code

    monkeypatch.setattr(emailer, "send_verification_code", fake_send)
    store = Store()
    username = f"school_{uuid.uuid4().hex[:8]}"
    initial_password = f"Initial-{uuid.uuid4().hex}-A1"
    password_hash, salt = hash_password(initial_password)
    store.save_user({
        "id": new_id("user"),
        "username": username,
        "email": "",
        "email_verified": 0,
        "password_set": 1,
        "password_hash": password_hash,
        "salt": salt,
        "role": "student",
        "display_name": "统一注册学生",
        "token": None,
        "must_change_password": 1,
        "status": "active",
        "teacher_id": "",
        "created_at": "2026-01-01T00:00:00+00:00",
    })

    login = client.post("/api/auth/login", json={"username": username, "password": initial_password})
    assert login.status_code == 200, login.text
    old_token = login.json()["token"]
    assert login.json()["user"]["onboarding_required"] is True

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert me.status_code == 200
    blocked = client.get("/api/projects", headers={"Authorization": f"Bearer {old_token}"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "ONBOARDING_REQUIRED"

    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    code = client.post("/api/auth/email/code", json={"email": email, "purpose": "first_login"})
    assert code.status_code == 200
    assert captured["to"] == email

    new_password = f"New-{uuid.uuid4().hex}-A1"
    completed = client.post(
        "/api/auth/first-login/complete",
        headers={"Authorization": f"Bearer {old_token}"},
        json={"email": email, "email_code": captured["code"], "new_password": new_password},
    )
    assert completed.status_code == 200, completed.text
    new_token = completed.json()["token"]
    assert new_token != old_token
    assert completed.json()["user"]["onboarding_required"] is False

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    ready = client.get("/api/projects", headers={"Authorization": f"Bearer {new_token}"})
    assert ready.status_code == 200
    assert client.post("/api/auth/login", json={"username": email, "password": new_password}).status_code == 200


def test_resend_provider_used_when_key_present(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_RESEND_API_KEY", "re_test-key-not-real")
    monkeypatch.delenv("PAPERGUARD_SMTP_HOST", raising=False)
    captured: dict[str, object] = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")

        class Response:
            status_code = 200
            text = "{}"

            def json(self):
                return {"id": "email-1"}

        return Response()

    monkeypatch.setattr(emailer.httpx, "post", fake_post)
    assert emailer.email_configured() is True
    emailer.send_email("owner@example.com", "主题", "正文")
    assert "api.resend.com/emails" in str(captured["url"])
    payload = captured["json"]
    assert payload["to"] == ["owner@example.com"]
    assert payload["subject"] == "主题"
    assert str(payload["from"]).startswith("论文卫士Pro <")


def test_send_failure_maps_to_502(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_RESEND_API_KEY", "re_test-key-not-real")
    monkeypatch.setenv("PAPERGUARD_RESEND_FROM", "onboarding@resend.dev")

    def fake_post(url, **kwargs):
        class Response:
            status_code = 422
            text = '{"message":"Invalid to field"}'

            def json(self):
                return {}

        return Response()

    monkeypatch.setattr(emailer.httpx, "post", fake_post)
    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    code_resp = client.post("/api/auth/email/code", json={"email": email})
    assert code_resp.status_code == 502
    assert code_resp.json()["detail"]["code"] == "EMAIL_SEND_FAILED"


def test_registration_validation_keeps_code_until_success(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(emailer, 'email_configured', lambda: True)
    captured = {}
    monkeypatch.setattr(emailer, 'send_verification_code',
                        lambda email, code, minutes: captured.update(code=code, minutes=minutes))
    email = f'{uuid.uuid4().hex}@example.com'
    sent = client.post('/api/auth/email/code', json={'email': email})
    assert sent.json()['expires_in'] == 600
    assert captured['minutes'] == 10
    store = Store()
    initial = store.get_email_code(email)
    payload = _register_payload(email=email, email_code=captured['code'])
    for invalid in ({'password': 'short'}, {'username': 'ab'}, {'username': 'admin'}):
        response = client.post('/api/auth/register', json={**payload, **invalid})
        assert response.status_code in (400, 409), response.text
        assert store.get_email_code(email) == initial
    # The original code still works nine minutes after sending.
    now = datetime.now(timezone.utc)
    store.save_email_code(email, initial['code_hash'], 'register',
                          (now + timedelta(minutes=1)).isoformat(),
                          (now - timedelta(minutes=9)).isoformat())
    response = client.post('/api/auth/register', json=payload)
    assert response.status_code == 201, response.text
    assert store.get_email_code(email) is None


def test_registration_rejects_expired_code(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(emailer, 'email_configured', lambda: True)
    email = f'{uuid.uuid4().hex}@example.com'
    now = datetime.now(timezone.utc)
    Store().save_email_code(email, _email_code_hash('123456'), 'register',
                            (now - timedelta(seconds=1)).isoformat(),
                            (now - timedelta(minutes=10, seconds=1)).isoformat())
    response = client.post('/api/auth/register', json=_register_payload(email=email, email_code='123456'))
    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'EMAIL_CODE_EXPIRED'
