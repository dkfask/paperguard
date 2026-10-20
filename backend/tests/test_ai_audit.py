"""AI 配置变更审计（S5）的测试。

修复前 `ai.py` 里对 `_audit` **零调用**：改一个 base_url 就等于把整份
论文正文改道到另一个外部处理者，而这类变更在任何 SaaS 合规视角下都
属于必须可追溯的事件。同一代码库的 `document_projects.py` 却对每一次
项目变更都在记——这个不对称正是缺陷本身。

这些用例要锁住的性质：
1. 四个变更端点（用户级存/清、系统级存/清）都留痕
2. 审计里**只有元数据**：没有 API Key，也没有论文内容
3. 审计写入失败**不影响**配置本身写入成功
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app
from tests._helpers import register_fixture

client = TestClient(app)

_SECRET = "sk-audit-must-not-appear-DO-NOT-LEAK"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "0123456789abcdef" * 4)


def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _register(role: str = "student") -> tuple[str, str, dict[str, str]]:
    username = f"aud_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    register_fixture(client, username, password, role=role)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return username, password, {"Authorization": f"Bearer {token}"}


def _admin() -> tuple[str, str, dict[str, str]]:
    """用 conftest 预设的 PAPERGUARD_ADMIN_PASSWORD 建管理员。"""
    import os

    password = os.environ["PAPERGUARD_ADMIN_PASSWORD"]
    resp = client.post(
        "/api/auth/login", json={"username": "admin", "password": password}
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return "admin", password, {"Authorization": f"Bearer {token}"}


def _events(action_prefix: str = "ai.config") -> list[dict]:
    return [
        e for e in Store().list_audit_events(limit=1000)
        if e["action"].startswith(action_prefix)
    ]


def _events_for(
    action: str, resource_type: str, actor_id: str = "", needle: str = ""
) -> list[dict]:
    """按 **精确 action + resource_type (+ 操作者 + reason 标记)** 取事件。

    每一层过滤都是必需的，去掉任何一层都会让断言变得不确定：
    - 只用 action 前缀：S2 之后同一个 action 会有 `ai_config:system`（平台
      默认）和 `ai_config:system:<school>`（按校配置）两种 resource_type，
      前缀匹配会把别的测试产生的记录一并抓进来。
    - 只按 actor：`test_ai_api.py` 也用 admin 身份保存系统配置，全量跑时
      两边同为 admin。
    - 只取 `[0]`：`list_audit_events` 按时间倒序，但同一秒内的多条记录顺序
      不保证稳定，`[0]` 未必是本用例刚写的那条。

    `needle` 用本用例独有的 model 名定位，是唯一确定的锚点。
    """
    return [
        e for e in Store().list_audit_events(limit=1000)
        if e["action"] == action
        and e["resource_type"] == resource_type
        and (not actor_id or e["actor_id"] == actor_id)
        and (not needle or needle in (e["reason"] or ""))
    ]


# --------------------------------------------------------------------------
# 留痕
# --------------------------------------------------------------------------

def test_user_config_save_is_audited():
    username, _, headers = _register()
    resp = client.put(
        "/api/ai/config",
        headers=headers,
        json={
            "base_url": "https://api.vendor.example/v1",
            "api_key": _SECRET,
            "model": "m-audit",
        },
    )
    assert resp.status_code == 200, resp.text
    actor_id = Store().get_user_by_username(username)["id"]
    events = _events_for("ai.config.saved", "ai_config:user", actor_id, needle="m-audit")
    assert events, "用户保存 AI 配置没有产生审计事件"
    latest = events[0]
    assert latest["actor_id"], "审计必须记录操作者"
    assert "vendor.example" in latest["reason"]
    assert "m-audit" in latest["reason"]


def test_user_config_clear_is_audited():
    username, _, headers = _register()
    actor_id = Store().get_user_by_username(username)["id"]
    client.put(
        "/api/ai/config",
        headers=headers,
        json={"base_url": "https://api.vendor.example/v1", "api_key": _SECRET, "model": "m"},
    )
    before = len(_events_for("ai.config.cleared", "ai_config:user", actor_id))
    resp = client.delete("/api/ai/config", headers=headers)
    assert resp.status_code == 204, resp.text
    assert len(_events_for("ai.config.cleared", "ai_config:user", actor_id)) == before + 1, (
        "清除用户配置没有留痕"
    )


def test_system_config_save_and_clear_are_audited():
    admin_username, _, admin_headers = _admin()
    admin_id = Store().get_user_by_username(admin_username)["id"]

    resp = client.put(
        "/api/ai/admin/config",
        headers=admin_headers,
        json={
            "base_url": "https://api.vendor.example/v1",
            "api_key": _SECRET,
            "model": "m-sys-audit",
        },
    )
    assert resp.status_code == 200, resp.text
    saved = _events_for(
        "ai.config.system_saved", "ai_config:system", admin_id, needle="m-sys-audit"
    )
    assert saved, "管理员保存系统配置没有产生审计事件"
    assert "m-sys-audit" in saved[0]["reason"]

    resp = client.delete("/api/ai/admin/config", headers=admin_headers)
    assert resp.status_code == 204, resp.text
    assert _events_for("ai.config.system_cleared", "ai_config:system", admin_id), (
        "管理员清除系统配置没有留痕"
    )


def test_audit_never_contains_the_api_key():
    """审计流是管理端可见的，凭据绝不能进这里。"""
    _, _, headers = _register()
    client.put(
        "/api/ai/config",
        headers=headers,
        json={
            "base_url": "https://api.vendor.example/v1",
            "api_key": _SECRET,
            "model": "m",
        },
    )
    for event in _events("ai.config"):
        blob = " ".join(str(v) for v in event.values())
        assert _SECRET not in blob, f"审计事件里出现了 API Key: {event}"
        # 密文同样不能进审计
        assert "pgsec1$" not in blob


# --------------------------------------------------------------------------
# 健壮性
# --------------------------------------------------------------------------

def test_audit_failure_does_not_block_config_write(monkeypatch):
    """审计是旁路证据，不该反过来阻断业务写入。

    若审计表出错就让管理员改不了配置，那是在用「可追溯性」换「可用性」，
    两头都输。正确行为是记录失败并让配置写入照常成功。
    """
    import app.db as db_module

    def _boom(*_a, **_k):
        raise RuntimeError("审计表写入失败")

    monkeypatch.setattr(db_module.Store, "save_audit_event", _boom)

    _, _, headers = _register()
    resp = client.put(
        "/api/ai/config",
        headers=headers,
        json={
            "base_url": "https://api.vendor.example/v1",
            "api_key": _SECRET,
            "model": "m-resilient",
        },
    )
    assert resp.status_code == 200, f"审计失败不该让配置保存失败: {resp.text}"

    # 配置确实写进去了
    user = Store().get_user_by_username(
        client.get("/api/auth/me", headers=headers).json()["username"]
    )
    assert Store().get_user_ai_config(user["id"])["model"] == "m-resilient"
