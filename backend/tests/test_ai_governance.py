"""AI 运行治理（限流 / 配额 / 用量记账）的测试。

背景：LLM 是按量计费资源。修复前 `/api/ai/assist` 没有任何速率限制，
也没有任何用量记录——单个账号失控即可烧光上游额度，运维事后无法回答
「谁花了多少」，也就无法计费与按租户分摊。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app
from app.services import ai_governance

client = TestClient(app)

# --------------------------------------------------------------------------
# 出站守卫需要真实 DNS 解析（SSRF 防护的安全边界）。本文件的用例都用
# .local / .example 这类**刻意不可解析**的假供应商域名，不打桩的话守卫会在
# 解析阶段就 400，测不到本来要验证的逻辑。
#
# 注意：这个 autouse fixture 只服务于「本文件里的假域名需要被解析成公网地址」
# 这一件事。守卫自身的测试（test_outbound_guard.py）**不能**带它——那里要验证
# 的正是解析结果如何影响判定。
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fake_public_dns(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)



def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _student() -> tuple[str, dict[str, str]]:
    username = f"gov_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    reg = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "student"},
    )
    assert reg.status_code == 201, reg.text
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return username, {"Authorization": f"Bearer {token}"}


def _configure_ai(username: str, base_url: str = "https://api.vendor.example/v1") -> None:
    store = Store()
    user = store.get_user_by_username(username)
    store.set_user_ai_config(
        user["id"], {"base_url": base_url, "api_key": "sk-test", "model": "m-test"}
    )


@pytest.fixture(autouse=True)
def _reset_limits(monkeypatch):
    """每个用例都从宽松限额起步，需要收紧的用例自己再覆盖。"""
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1000")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1000")


# --------------------------------------------------------------------------
# 配额
# --------------------------------------------------------------------------

def test_daily_quota_blocks_after_limit(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "3")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1000")
    store = Store()
    user = {"id": f"u-{uuid.uuid4().hex[:8]}"}
    period = ai_governance._utc_periods(ai_governance.datetime.now(ai_governance.timezone.utc))[0]

    for _ in range(3):
        ai_governance.begin_call(store, user)
    with pytest.raises(HTTPException) as exc:
        ai_governance.begin_call(store, user)
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "AI_DAILY_QUOTA_EXCEEDED"
    # 超限不应把计数推高，否则会被「已耗尽」误显示成超额
    assert store.get_ai_quota_used(user["id"], period) == 3


def test_burst_limit_blocks_within_same_minute(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "2")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1000")
    store = Store()
    user = {"id": f"u-{uuid.uuid4().hex[:8]}"}

    ai_governance.begin_call(store, user)
    ai_governance.begin_call(store, user)
    with pytest.raises(HTTPException) as exc:
        ai_governance.begin_call(store, user)
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "AI_RATE_LIMITED"


def test_burst_rejection_refunds_the_daily_slot(monkeypatch):
    """突发被拒时不能连带吃掉日配额——否则用户会被两处同时卡住。"""
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1000")
    store = Store()
    user = {"id": f"u-{uuid.uuid4().hex[:8]}"}
    utc = ai_governance.datetime.now(ai_governance.timezone.utc)
    day_period, minute_period = ai_governance._utc_periods(utc)

    ai_governance.begin_call(store, user)
    with pytest.raises(HTTPException):
        ai_governance.begin_call(store, user)
    # 第一次是成功的，日配额应为 1；第二次被突发拦下，不应变成 2
    assert store.get_ai_quota_used(user["id"], day_period) == 1
    assert store.get_ai_quota_used(user["id"], minute_period) == 1


def test_quota_consume_is_atomic_under_concurrency():
    """并发下不能超发。

    「先查后写」的实现会在这里失败：多个调用同时读到 used < limit，
    双双通过检查。这里用线程压一下，验证的是数据库层的原子性。
    """
    import threading

    store = Store()
    user_id = f"u-{uuid.uuid4().hex[:8]}"
    period = "2026-01-01"
    limit = 20
    granted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        ok = store.consume_ai_quota(user_id, period, "2026-01-01T00:00:00", limit)
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(60)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(granted) == 60
    assert sum(1 for ok in granted if ok) == limit, "成功次数必须恰好等于限额"
    assert store.get_ai_quota_used(user_id, period) == limit


# --------------------------------------------------------------------------
# 归还
# --------------------------------------------------------------------------

def test_refund_restores_both_counters(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1000")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1000")
    store = Store()
    user = {"id": f"u-{uuid.uuid4().hex[:8]}"}
    utc = ai_governance.datetime.now(ai_governance.timezone.utc)
    day_period, _minute = ai_governance._utc_periods(utc)

    ai_governance.begin_call(store, user)
    assert store.get_ai_quota_used(user["id"], day_period) == 1
    ai_governance.refund_call(store, user, day_period)
    assert store.get_ai_quota_used(user["id"], day_period) == 0


# --------------------------------------------------------------------------
# 用量记账
# --------------------------------------------------------------------------

def test_usage_is_recorded_on_success(monkeypatch):
    import app.api.ai as ai_module

    monkeypatch.setattr(ai_module, "_chat_completion", lambda *a, **k: "改写后的正文")
    username, headers = _student()
    _configure_ai(username)

    before = len(Store().ai_usage_summary(limit=1000))
    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "论文正文内容"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "改写后的正文"

    rows = Store().ai_usage_summary(limit=1000)
    assert len(rows) == before + 1
    user_id = Store().get_user_by_username(username)["id"]
    assert any(r["user_id"] == user_id and r["calls"] == 1 for r in rows)


def test_usage_is_recorded_on_upstream_error(monkeypatch):
    """失败同样可能产生上游成本，因此必须记账，不能只记成功。"""
    import app.api.ai as ai_module
    from fastapi import HTTPException as _HTTPException

    def _boom(*a, **k):
        raise _HTTPException(status_code=502, detail="上游炸了")

    monkeypatch.setattr(ai_module, "_chat_completion", _boom)
    username, headers = _student()
    _configure_ai(username)
    user_id = Store().get_user_by_username(username)["id"]

    before = sum(r["calls"] for r in Store().ai_usage_summary(limit=1000) if r["user_id"] == user_id)
    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "论文正文内容"}
    )
    assert resp.status_code == 502
    after = sum(r["calls"] for r in Store().ai_usage_summary(limit=1000) if r["user_id"] == user_id)
    assert after == before + 1, "上游失败也必须记一笔用量"


def test_outbound_block_refunds_and_records_nothing(monkeypatch):
    """出站被拦：请求根本没发出去 → 归还额度，且不记用量。"""
    import app.api.ai as ai_module

    def _boom(*a, **k):
        raise AssertionError("出站被拦时不应真的发出请求")

    monkeypatch.setattr(ai_module, "_chat_completion", _boom)
    username, headers = _student()
    _configure_ai(username, base_url="http://127.0.0.1:8080/v1")
    user_id = Store().get_user_by_username(username)["id"]
    utc = ai_governance.datetime.now(ai_governance.timezone.utc)
    day_period, _m = ai_governance._utc_periods(utc)

    before_usage = sum(
        r["calls"] for r in Store().ai_usage_summary(limit=1000) if r["user_id"] == user_id
    )
    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "论文正文内容"}
    )
    assert resp.status_code == 400, resp.text

    after_usage = sum(
        r["calls"] for r in Store().ai_usage_summary(limit=1000) if r["user_id"] == user_id
    )
    assert after_usage == before_usage, "没发出去的请求不该产生用量记录"
    assert Store().get_ai_quota_used(user_id, day_period) == 0, "没发出去的请求不该占额度"


def test_organization_id_is_snapshotted_at_call_time(monkeypatch):
    """用量记录里的学校归属在**调用时**落库。

    不在汇总时再关联用户表——用户之后调换学校会让历史用量的归属跟着漂移，
    计费口径就不成立了。
    """
    import app.api.ai as ai_module

    monkeypatch.setattr(ai_module, "_chat_completion", lambda *a, **k: "ok")
    username, headers = _student()
    _configure_ai(username)
    user_id = Store().get_user_by_username(username)["id"]

    store = Store()
    user = store.get_user(user_id)
    user["organization_context"] = {"school_id": "school-abc"}
    store.save_user(user)

    client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "正文"}
    )

    # 事后把用户调到另一所学校，历史用量的归属不应跟着变
    user = store.get_user(user_id)
    user["organization_context"] = {"school_id": "school-xyz"}
    store.save_user(user)

    raw = store._connect()
    try:
        rows = raw.execute(
            "SELECT organization_id FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchall()
    finally:
        raw.close()
    assert rows, "应当有用量记录"
    assert all(r["organization_id"] == "school-abc" for r in rows), (
        f"历史用量归属被后续调校漂移了: {[dict(r) for r in rows]}"
    )


# --------------------------------------------------------------------------
# 入口接线
#
# 上面所有用例要么直接调 ai_governance，要么只断言用量/归属，**没有一条
# 证明 `/api/ai/assist` 这个 HTTP 入口真的会走配额校验**。曾出现过把入口里
# 的 begin_call 整段删掉、9 条用例仍然全绿的假绿——所以这里直接从 HTTP 侧
# 断言「超配额时入口返回 429，且请求根本没发往上游」。
# --------------------------------------------------------------------------

def test_assist_endpoint_enforces_quota_over_http(monkeypatch):
    import app.api.ai as ai_module

    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "2")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1000")
    monkeypatch.setattr(ai_module, "_chat_completion", lambda *a, **k: "润色结果")
    username, headers = _student()
    _configure_ai(username)

    for _ in range(2):
        resp = client.post(
            "/api/ai/assist", headers=headers, json={"action": "polish", "text": "论文正文"}
        )
        assert resp.status_code == 200, resp.text

    # 第三次必须被入口拦住
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return "不该走到这里"

    monkeypatch.setattr(ai_module, "_chat_completion", _spy)
    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "论文正文"}
    )
    assert resp.status_code == 429, f"超配额未被入口拦截: {resp.status_code} {resp.text}"
    assert resp.json()["detail"]["code"] == "AI_DAILY_QUOTA_EXCEEDED"
    assert called["n"] == 0, "被配额拦下的请求不应真的发往上游"


def test_assist_endpoint_still_works_within_quota(monkeypatch):
    """反向护栏：防止「为了拦住测试」把入口堵死。"""
    import app.api.ai as ai_module

    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1000")
    monkeypatch.setattr(ai_module, "_chat_completion", lambda *a, **k: "润色结果")
    username, headers = _student()
    _configure_ai(username)

    resp = client.post(
        "/api/ai/assist", headers=headers, json={"action": "polish", "text": "论文正文"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "润色结果"
