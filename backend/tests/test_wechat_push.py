"""微信订阅消息渠道回归: 配额记账、事件去重、openid 可逆、43101 纠偏。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.auth import _openid_cipher, _openid_from_cipher
from app.db import Store
from app.main import app
from app.services import wechat_push


client = TestClient(app)


def _account() -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:10]
    username = f"push_{suffix}"
    password = f"push-{uuid.uuid4().hex}-A1"
    registered = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "display_name": "推送测试用户", "role": "student"},
    )
    assert registered.status_code == 201, registered.text
    return username, password, registered.json()["id"]


def _bind(monkeypatch, username: str, password: str) -> None:
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    async def exchange(_code: str) -> str:
        return f"openid-push-{_code}"

    monkeypatch.setattr("app.api.auth.exchange_mp_code", exchange)

    login = client.post("/api/auth/mp/login", json={"code": f"code-{username}"})
    assert login.status_code == 200
    ticket = login.json()["bind_ticket"]
    bound = client.post("/api/auth/mp/bind", json={"bind_ticket": ticket, "username": username, "password": password})
    assert bound.status_code == 200, bound.text


def _insert_delivered_event(store: Store, recipient_id: str, event_type: str, title: str) -> str:
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    with store.workflow_transaction() as conn:
        store.save_workflow_event_conn(
            conn,
            event_type=event_type,
            recipient_id=recipient_id,
            resource_type="submission",
            resource_id=f"sub-{event_id}",
            dedupe_key=f"{event_type}:{recipient_id}:{event_id}",
            data={"title": title, "event_type": event_type},
        )
    with store._connect() as conn:
        row = conn.execute(
            "SELECT id FROM workflow_events WHERE dedupe_key = ?",
            (f"{event_type}:{recipient_id}:{event_id}",),
        ).fetchone()
    actual_id = row["id"] if row else None
    assert actual_id, "事件未写入"
    assert store.mark_workflow_event_delivered_with_lease(actual_id)
    return actual_id


def _configure(monkeypatch, sent_result: list[tuple[str, str]] | None = None):
    monkeypatch.setenv("PAPERGUARD_WECHAT_APP_ID", "wx-test-appid")
    monkeypatch.setenv("PAPERGUARD_WECHAT_APP_SECRET", "wx-test-secret")
    monkeypatch.setenv(
        "PAPERGUARD_WECHAT_SUBSCRIBE_TEMPLATES",
        json.dumps({"submission.returned": "TMPL_RETURN", "submission.approved": "TMPL_APPROVE"}),
    )
    monkeypatch.setattr(wechat_push, "fetch_access_token", lambda app_id, secret: "test-token")

    def fake_send(token: str, openid: str, template_id: str, title: str):
        if sent_result is not None:
            sent_result.append((openid, template_id, title))
        return (True, "0") if sent_result is not None else (False, "43101")

    monkeypatch.setattr(wechat_push, "send_subscribe", fake_send)


def test_openid_cipher_roundtrip(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "roundtrip-secret")
    cipher = _openid_cipher("openid-abc-123")
    assert cipher != "openid-abc-123"
    assert _openid_from_cipher(cipher) == "openid-abc-123"


def test_subscribe_quota_endpoint(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    username, password, _ = _account()
    _bind(monkeypatch, username, password)
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    first = client.post("/api/auth/mp/subscribe", headers=headers, json={"template_id": "TMPL_RETURN", "accepted": True})
    assert first.status_code == 200 and first.json()["remaining"] == 1
    second = client.post("/api/auth/mp/subscribe", headers=headers, json={"template_id": "TMPL_RETURN", "accepted": True})
    assert second.json()["remaining"] == 2
    rejected = client.post("/api/auth/mp/subscribe", headers=headers, json={"template_id": "TMPL_RETURN", "accepted": False})
    assert rejected.json()["remaining"] == 0


def test_push_delivers_once_and_consumes_quota(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    username, password, user_id = _account()
    _bind(monkeypatch, username, password)
    store = Store()

    sent_calls: list[tuple[str, str, str]] = []
    _configure(monkeypatch, sent_result=sent_calls)
    store.grant_push_quota(user_id, "TMPL_RETURN", 2, "2026-09-13T00:00:00+00:00")
    event_id = _insert_delivered_event(store, user_id, "submission.returned", "论文需要修改")

    result = wechat_push.deliver_wechat_push(store, consumer_id="test", now=datetime.now(timezone.utc))
    assert result["sent"] == 1
    assert len(sent_calls) == 1
    openid, template_id, title = sent_calls[0]
    assert openid.startswith("openid-push-")
    assert template_id == "TMPL_RETURN"
    assert title == "论文需要修改"
    assert store.has_mini_program_push(event_id)

    # 同一事件幂等: 第二轮不再发送
    wechat_push.deliver_wechat_push(store, consumer_id="test")
    assert len(sent_calls) == 1

    # 配额被消费一次(2 -> 1)
    with store._connect() as conn:
        row = conn.execute(
            "SELECT remaining FROM mini_program_push_quotas WHERE user_id = ? AND template_id = ?",
            (user_id, "TMPL_RETURN"),
        ).fetchone()
    assert row["remaining"] == 1


def test_push_43101_resets_quota(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    username, password, user_id = _account()
    _bind(monkeypatch, username, password)
    store = Store()

    _configure(monkeypatch, sent_result=None)  # send 返回 43101 未订阅
    store.grant_push_quota(user_id, "TMPL_APPROVE", 1, "2026-09-13T00:00:00+00:00")
    _insert_delivered_event(store, user_id, "submission.approved", "论文审核已通过")

    result = wechat_push.deliver_wechat_push(store, consumer_id="test", now=datetime.now(timezone.utc))
    assert result["failed"] == 1
    with store._connect() as conn:
        row = conn.execute(
            "SELECT remaining FROM mini_program_push_quotas WHERE user_id = ? AND template_id = ?",
            (user_id, "TMPL_APPROVE"),
        ).fetchone()
    assert row["remaining"] == 0


def test_push_skips_without_binding_or_quota(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    _, _, user_id = _account()
    store = Store()

    sent_calls: list[tuple[str, str, str]] = []
    _configure(monkeypatch, sent_result=sent_calls)

    # 无绑定: 跳过
    _insert_delivered_event(store, user_id, "submission.returned", "论文需要修改")
    result = wechat_push.deliver_wechat_push(store, consumer_id="test", now=datetime.now(timezone.utc))
    assert result["skipped_events"] == 1 and not sent_calls

    # 有绑定无配额: 跳过且不消耗
    monkeypatch.setenv("PAPERGUARD_MP_BINDING_SECRET", "test-only-mp-binding-secret")
    username, password, user_id2 = _account()
    _bind(monkeypatch, username, password)
    _insert_delivered_event(store, user_id2, "submission.returned", "论文需要修改")
    result = wechat_push.deliver_wechat_push(store, consumer_id="test", now=datetime.now(timezone.utc))
    assert not sent_calls
    assert result["skipped_events"] >= 1
