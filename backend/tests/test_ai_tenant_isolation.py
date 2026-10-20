"""系统级 AI 配置的租户隔离（S2）的测试。

用户已确认部署形态是「一套部署服务多个学校」。修复前 `system_settings`
是全局键值表（key 为主键），意味着**所有租户共用同一份模型配置**——
每个学校的论文正文都流向同一个外部处理者，而平台既无法按租户区分，
也无法在某一租户要求换供应商时只改它自己那一份。

要锁住的性质：
1. 各校配置互相独立：A 校配置不影响 B 校解析到什么
2. 本校未配置时回退到平台全局默认（升级后行为不回退）
3. 存量数据（organization_id=''）仍可读——迁移不能让线上 AI 立刻失效
4. 解析链路真的用上了用户所属学校，而不只是 API 层支持了参数
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import ai as ai_module
from app.db import Store
from app.main import app
from tests._helpers import register_fixture

client = TestClient(app)

SCHOOL_A = "school-alpha"
SCHOOL_B = "school-beta"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "0123456789abcdef" * 4)


# 出站守卫（S1）需要真实 DNS 解析，这是它的安全边界，不能为了测试关掉。
# 本文件用 .example 这类刻意不可解析的假供应商域名，因此注入 DNS 桩把它们
# 解析到公网地址。守卫自身的测试（test_outbound_guard.py）**不带**这个桩。
@pytest.fixture(autouse=True)
def _fake_public_dns(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # 解除速率限制。本文件用共享的 `admin` 账号跑 AI，全量套件里多个文件
    # 都用同一个账号；真实上游派发点现在全部扣突发配额（修复前只有 ai_assist
    # 扣），累积后会撞上默认的每分钟 10 次上限报 429——那是跨文件污染。
    # 配额语义由 test_ai_governance.py 覆盖。
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "100000")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "100000")


def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _student_at(school_id: str) -> tuple[str, dict[str, str]]:
    """建一个属于指定学校的学生并登录。"""
    username = f"ten_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    register_fixture(client, username, password)
    store = Store()
    user = store.get_user_by_username(username)
    user["organization_context"] = {"school_id": school_id}
    store.save_user(user)

    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return username, {"Authorization": f"Bearer {token}"}


def _admin() -> dict[str, str]:
    import os

    token = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": os.environ["PAPERGUARD_ADMIN_PASSWORD"]},
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _cfg(host: str, key: str) -> dict:
    return {
        "base_url": f"https://{host}/v1",
        "api_key": key,
        "model": f"model-of-{host}",
    }


# --------------------------------------------------------------------------
# 隔离
# --------------------------------------------------------------------------

def test_tenant_configs_are_independent():
    store = Store()
    store.set_system_ai_config(_cfg("a-vendor.example", "sk-a-tenant"), SCHOOL_A)
    store.set_system_ai_config(_cfg("b-vendor.example", "sk-b-tenant"), SCHOOL_B)

    a = store.get_system_ai_config(SCHOOL_A)
    b = store.get_system_ai_config(SCHOOL_B)
    assert a["base_url"] == "https://a-vendor.example/v1"
    assert b["base_url"] == "https://b-vendor.example/v1"
    assert a["api_key"] == "sk-a-tenant"
    assert b["api_key"] == "sk-b-tenant"
    store.set_system_ai_config(None, SCHOOL_A)
    store.set_system_ai_config(None, SCHOOL_B)


def test_unconfigured_tenant_falls_back_to_global():
    store = Store()
    store.set_system_ai_config(_cfg("global-vendor.example", "sk-global"), "")
    # 学校 B 从未单独配置过
    cfg = store.get_system_ai_config(SCHOOL_B)
    assert cfg is not None, "未单独配置的租户应回退到平台全局默认"
    assert cfg["base_url"] == "https://global-vendor.example/v1"
    store.set_system_ai_config(None, "")


def test_global_setting_does_not_leak_into_school_namespace():
    """全局配置不该被写进某个学校名下——那是反向的数据污染。"""
    store = Store()
    store.set_system_ai_config(_cfg("global-vendor.example", "sk-global"), "")
    assert store.get_system_setting("ai_default_config", SCHOOL_A) is None
    store.set_system_ai_config(None, "")


def test_legacy_row_survives_migration():
    """存量数据以 organization_id='' 保留，升级后仍可读。

    真实风险在生产：迁移若把行丢掉，上线瞬间所有 AI 功能一起失效。
    """
    store = Store()
    store.set_system_ai_config(_cfg("legacy-vendor.example", "sk-legacy"), "")
    conn = store._connect()
    try:
        rows = conn.execute(
            "SELECT organization_id FROM system_settings WHERE key = 'ai_default_config'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "存量行不应消失"
    assert all(r["organization_id"] == "" for r in rows)
    assert store.get_system_ai_config()["api_key"] == "sk-legacy"
    assert store.get_system_ai_config(SCHOOL_A)["api_key"] == "sk-legacy"
    store.set_system_ai_config(None, "")


# --------------------------------------------------------------------------
# 解析链路（API 层支持参数 ≠ 真的用上了）
# --------------------------------------------------------------------------

def test_resolve_cfg_uses_the_users_school(monkeypatch):
    """这是 S2 的核心断言：解析链路真的按用户所属学校取值。

    只测存储层是不够的——完全可能出现「db 层支持了 organization_id，
    但 _resolve_cfg 压根没传」这种假修复。
    """
    store = Store()
    store.set_system_ai_config(_cfg("a-vendor.example", "sk-a"), SCHOOL_A)
    store.set_system_ai_config(_cfg("b-vendor.example", "sk-b"), SCHOOL_B)

    monkeypatch.setattr(ai_module, "_chat_completion", lambda *a, **k: "ok")

    user_a = {"id": "u-a", "organization_context": {"school_id": SCHOOL_A}}
    user_b = {"id": "u-b", "organization_context": {"school_id": SCHOOL_B}}

    cfg_a, source_a = ai_module._resolve_cfg(store, user_a)
    cfg_b, source_b = ai_module._resolve_cfg(store, user_b)

    assert cfg_a["base_url"] == "https://a-vendor.example/v1", "A 校用到了别人的配置"
    assert cfg_b["base_url"] == "https://b-vendor.example/v1", "B 校用到了别人的配置"
    assert source_a == source_b == "system"

    store.set_system_ai_config(None, SCHOOL_A)
    store.set_system_ai_config(None, SCHOOL_B)


def test_assist_routes_to_the_tenants_own_vendor(monkeypatch):
    """端到端：A 校学生点「润色」，请求必须发往 A 校的供应商。"""
    store = Store()
    store.set_system_ai_config(_cfg("a-vendor.example", "sk-a"), SCHOOL_A)

    seen: list[str] = []

    def _capture(cfg, *_a, **_k):
        seen.append(cfg["base_url"])
        return "润色结果"

    monkeypatch.setattr(ai_module, "_chat_completion", _capture)
    username, headers = _student_at(SCHOOL_A)
    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "论文正文"}
    )
    assert resp.status_code == 200, resp.text
    assert seen == ["https://a-vendor.example/v1"], f"请求发往了错误的供应商: {seen}"
    store.set_system_ai_config(None, SCHOOL_A)


# --------------------------------------------------------------------------
# 管理端
# --------------------------------------------------------------------------

def test_admin_can_set_config_per_school():
    headers = _admin()
    resp = client.put(
        "/api/ai/admin/config?organization_id=school-admin-test",
        headers=headers,
        json={
            "base_url": "https://tenant-vendor.example/v1",
            "api_key": "sk-tenant-admin",
            "model": "m-tenant",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["organization_id"] == "school-admin-test"
    assert Store().get_system_ai_config("school-admin-test")["api_key"] == "sk-tenant-admin"
    Store().set_system_ai_config(None, "school-admin-test")


def test_admin_listing_shows_tenant_overrides():
    headers = _admin()
    Store().set_system_ai_config(_cfg("t-vendor.example", "sk-t"), "school-listed")
    resp = client.get("/api/ai/admin/config", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "school-listed" in resp.json()["tenant_overrides"]
    Store().set_system_ai_config(None, "school-listed")


def test_non_admin_cannot_set_system_config():
    """租户隔离的前提是权限边界本身没破。"""
    _, headers = _student_at(SCHOOL_A)
    resp = client.put(
        "/api/ai/admin/config",
        headers=headers,
        json={"base_url": "https://evil.example/v1", "api_key": "sk-x", "model": "m"},
    )
    assert resp.status_code == 403, resp.text


def test_tenant_config_is_audited_with_its_school():
    """审计要能回答「哪一校的配置被谁改了」。"""
    headers = _admin()
    resp = client.put(
        "/api/ai/admin/config?organization_id=school-audited",
        headers=headers,
        json={
            "base_url": "https://aud-vendor.example/v1",
            "api_key": "sk-aud",
            "model": "m-aud",
        },
    )
    assert resp.status_code == 200, resp.text
    events = [
        e for e in Store().list_audit_events(limit=1000)
        if e["action"] == "ai.config.system_saved"
    ]
    assert events, "按租户保存配置没有留痕"
    assert any("school-audited" in e["reason"] for e in events)
    Store().set_system_ai_config(None, "school-audited")
