"""邮箱注册 / 微信一键注册 / 邮箱绑定回归。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.db import Store
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

    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    password = f"Mail-{uuid.uuid4().hex}-A1"
    registered = client.post("/api/auth/register", json=_register_payload(email=email, password=password))
    assert registered.status_code == 201, registered.text
    assert registered.json()["username"].startswith("mail_")

    by_email = client.post("/api/auth/login", json={"username": email, "password": password})
    assert by_email.status_code == 200, by_email.text

    duplicate = client.post("/api/auth/register", json=_register_payload(email=email, password=password))
    assert duplicate.status_code == 409

    bad_format = client.post("/api/auth/register", json=_register_payload(email="not-an-email", password=password))
    assert bad_format.status_code == 422


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
    bound = client.post(
        "/api/auth/mp/bind-email",
        headers={"Authorization": f"Bearer {body['token']}"},
        json={"email": email, "email_code": "", "password": new_password},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["user"]["password_set"] is True
    assert "@example.edu" in bound.json()["user"]["email"]

    web_login = client.post("/api/auth/login", json={"username": email, "password": new_password})
    assert web_login.status_code == 200

    again = client.post("/api/auth/mp/login", json={"code": f"code-{suffix}"})
    assert again.status_code == 200 and again.json()["bound"] is True

    duplicate = client.post("/api/auth/mp/register", json={"bind_ticket": ticket})
    assert duplicate.status_code in (400, 409)


def test_mp_bind_accepts_email_identifier(monkeypatch):
    monkeypatch.delenv("PAPERGUARD_RESEND_API_KEY", raising=False)
    email = f"{uuid.uuid4().hex[:10]}@example.edu"
    password = f"Mail-{uuid.uuid4().hex}-A1"
    registered = client.post("/api/auth/register", json=_register_payload(email=email, password=password))
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
