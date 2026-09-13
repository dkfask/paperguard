from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _account(role: str = "student") -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    username = f"mp_{suffix}"
    password = "MpBindingPassword123"
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "display_name": "小程序测试用户", "role": role},
    )
    assert response.status_code == 201, response.text
    return username, password


def _fake_exchange(monkeypatch, openid: str):
    async def exchange(_code: str) -> str:
        return openid

    monkeypatch.setattr("app.api.auth.exchange_mp_code", exchange)


def test_mp_bind_ticket_survives_bad_password_and_unbinds(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    username, password = _account()
    _fake_exchange(monkeypatch, "openid-ticket-survives")

    first = client.post("/api/auth/mp/login", json={"code": "wx-code"})
    assert first.status_code == 200
    assert first.json()["bound"] is False
    ticket = first.json()["bind_ticket"]

    bad = client.post("/api/auth/mp/bind", json={
        "bind_ticket": ticket,
        "username": username,
        "password": "wrong-password1",
    })
    assert bad.status_code == 401

    bound = client.post("/api/auth/mp/bind", json={
        "bind_ticket": ticket,
        "username": username,
        "password": password,
    })
    assert bound.status_code == 200, bound.text
    token = bound.json()["token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == username

    second = client.post("/api/auth/mp/login", json={"code": "wx-code"})
    assert second.status_code == 200
    assert second.json()["bound"] is True
    assert second.json()["user"]["username"] == username

    unbound = client.post(
        "/api/auth/mp/unbind",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": password},
    )
    assert unbound.status_code == 200, unbound.text
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    after_unbind = client.post("/api/auth/mp/login", json={"code": "wx-code"})
    assert after_unbind.status_code == 200
    assert after_unbind.json()["bound"] is False


def test_mp_binding_is_one_to_one(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    first_user, first_password = _account()

    async def exchange(code: str) -> str:
        return {"first-code": "openid-one-to-one-a", "second-code": "openid-one-to-one-b"}[code]

    monkeypatch.setattr("app.api.auth.exchange_mp_code", exchange)

    first_ticket = client.post("/api/auth/mp/login", json={"code": "first-code"}).json()["bind_ticket"]
    first_bind = client.post("/api/auth/mp/bind", json={
        "bind_ticket": first_ticket,
        "username": first_user,
        "password": first_password,
    })
    assert first_bind.status_code == 200, first_bind.text

    second_login = client.post("/api/auth/mp/login", json={"code": "second-code"})
    assert second_login.status_code == 200
    second_ticket = second_login.json()["bind_ticket"]
    second_bind = client.post("/api/auth/mp/bind", json={
        "bind_ticket": second_ticket,
        "username": first_user,
        "password": first_password,
    })
    assert second_bind.status_code == 409
    assert second_bind.json()["detail"]["code"] == "MP_BINDING_CONFLICT"


def test_mp_requires_provider_configuration(monkeypatch):
    monkeypatch.delenv("PAPERGUARD_WECHAT_APP_ID", raising=False)
    monkeypatch.delenv("PAPERGUARD_WECHAT_APP_SECRET", raising=False)
    response = client.post("/api/auth/mp/login", json={"code": "wx-code"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MP_NOT_CONFIGURED"
