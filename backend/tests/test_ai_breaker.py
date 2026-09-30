"""上游 AI 熔断（S7）的测试。

修复前 `/api/ai/assist` 对上游故障零保护：超时 60 秒，上游一挂，每个
请求都要挂满 60 秒才返回 504。10 个并发请求就能占住 10 个同步工作线程
一分钟，写作池被慢慢吃干；而 S3 的突发限流挡不住这种情况——限流管的是
「次数」，管不住「单次挂多久」。

要锁住的性质：
1. 连续失败到阈值后打开，之后**不再打上游**（这是熔断的意义所在）
2. 打开期间请求立即返回，不等 60 秒超时
3. 冷却后进入半开，只放一个探针；探针成功才完全恢复
4. 熔断打开不影响用量记账——请求确实没发出去
5. 熔断按**上游端点**聚合，不是按 api_key
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import ai as ai_module
from app.db import Store
from app.main import app
from app.services import ai_breaker

client = TestClient(app)


@pytest.fixture(autouse=True)
def _breaker_env(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_AI_BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("PAPERGUARD_AI_BREAKER_RESET_SEC", "30")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1000")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1000")
    ai_breaker.reset_all()
    yield
    ai_breaker.reset_all()


@pytest.fixture(autouse=True)
def _fake_public_dns(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _student() -> tuple[str, dict[str, str]]:
    username = f"brk_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    assert client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "student"},
    ).status_code == 201
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return username, {"Authorization": f"Bearer {token}"}


def _configure(username: str, base_url: str = "https://vendor.example/v1") -> None:
    user = Store().get_user_by_username(username)
    Store().set_user_ai_config(
        user["id"], {"base_url": base_url, "api_key": "sk-t", "model": "m-t"}
    )


def _boom(*_a, **_k):
    raise HTTPException(status_code=502, detail="上游炸了")


# --------------------------------------------------------------------------
# 模块行为
# --------------------------------------------------------------------------

def test_opens_after_threshold_failures():
    key = "https://vendor.example/v1"
    for i in range(2):
        assert ai_breaker.allow(key)[0] is True
        ai_breaker.record_failure(key)
    assert ai_breaker.allow(key)[0] is True
    ai_breaker.record_failure(key)  # 第 3 次：达到阈值
    allowed, reason = ai_breaker.allow(key)
    assert allowed is False, "达到阈值后应打开熔断"
    assert reason


def test_success_resets_the_counter():
    key = "https://vendor.example/v1"
    ai_breaker.record_failure(key)
    ai_breaker.record_failure(key)
    ai_breaker.record_success(key)
    ai_breaker.record_failure(key)
    assert ai_breaker.allow(key)[0] is True, "成功后计数应清零"


def test_half_open_admits_only_one_probe(monkeypatch):
    """冷却结束那一刻只放一个探针——否则熔断等于没开。"""
    key = "https://vendor.example/v1"
    for _ in range(3):
        ai_breaker.record_failure(key)
    assert ai_breaker.allow(key)[0] is False

    # 快进到冷却结束之后
    import app.services.ai_breaker as mod

    real_monotonic = mod.time.monotonic
    fake = {"t": real_monotonic() + 31.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: fake["t"])

    assert ai_breaker.allow(key)[0] is True, "冷却后应放行一个探针"
    assert ai_breaker.allow(key)[0] is False, "半开期间不应放行第二个请求"


def test_probe_success_closes_the_breaker(monkeypatch):
    key = "https://vendor.example/v1"
    for _ in range(3):
        ai_breaker.record_failure(key)
    import app.services.ai_breaker as mod

    real = mod.time.monotonic
    monkeypatch.setattr(mod.time, "monotonic", lambda: real() + 31.0)
    ai_breaker.allow(key)  # 放行探针
    ai_breaker.record_success(key)
    assert ai_breaker.state(key)["open"] is False
    assert ai_breaker.allow(key)[0] is True


def test_probe_failure_reopens_immediately(monkeypatch):
    """探针失败不必再等阈值——它已经证明上游仍然坏着。"""
    key = "https://vendor.example/v1"
    for _ in range(3):
        ai_breaker.record_failure(key)
    import app.services.ai_breaker as mod

    real = mod.time.monotonic
    monkeypatch.setattr(mod.time, "monotonic", lambda: real() + 31.0)
    ai_breaker.allow(key)  # 探针
    ai_breaker.record_failure(key)
    assert ai_breaker.allow(key)[0] is False


def test_breaker_is_scoped_per_upstream():
    a = ai_breaker._get("https://a.example/v1")
    b = ai_breaker._get("https://b.example/v1")
    for _ in range(3):
        ai_breaker.record_failure("https://a.example/v1")
    assert ai_breaker.allow("https://a.example/v1")[0] is False
    assert ai_breaker.allow("https://b.example/v1")[0] is True, "A 校故障不该熔断 B 校"
    assert a.key != b.key


# --------------------------------------------------------------------------
# 接入
# --------------------------------------------------------------------------

def test_assist_returns_503_fast_when_circuit_open(monkeypatch):
    """熔断打开后，入口立刻 503，且**不再调用上游**。"""
    monkeypatch.setattr(ai_module, "_dispatch_completion", _boom)
    username, headers = _student()
    _configure(username)

    for _ in range(3):
        resp = client.post(
            "/api/ai/assist", headers=headers, json={"action": "polish", "text": "正文"}
        )
        assert resp.status_code == 502, resp.text

    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return "不该走到这里"

    monkeypatch.setattr(ai_module, "_dispatch_completion", _spy)
    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "正文"}
    )
    assert resp.status_code == 503, f"熔断打开时应快速失败: {resp.status_code}"
    assert resp.json()["detail"]["code"] == "AI_UPSTREAM_CIRCUIT_OPEN"
    assert called["n"] == 0, "熔断打开时不应再打上游"


def test_circuit_open_is_still_recorded_as_usage(monkeypatch):
    """熔断拒绝的请求同样消耗了一次用户额度，必须如实记账。"""
    monkeypatch.setattr(ai_module, "_dispatch_completion", _boom)
    username, headers = _student()
    _configure(username)
    user_id = Store().get_user_by_username(username)["id"]

    for _ in range(3):
        client.post(
            "/api/ai/assist", headers=headers, json={"action": "polish", "text": "正文"}
        )
    before = sum(
        r["calls"] for r in Store().ai_usage_summary(limit=1000) if r["user_id"] == user_id
    )
    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "正文"}
    )
    assert resp.status_code == 503
    after = sum(
        r["calls"] for r in Store().ai_usage_summary(limit=1000) if r["user_id"] == user_id
    )
    assert after == before + 1, "熔断拒绝的调用也要记用量（占用了用户额度）"
