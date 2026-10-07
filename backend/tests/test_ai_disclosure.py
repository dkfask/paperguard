"""数据出境与委托处理披露（S6）的测试。

修复前：用户填一个 Base URL，论文正文就发给了那家服务商，但系统在任何
地方都没有说过「你的数据会发给谁、会发什么」。在「一套部署服务多个学校」
的形态下，这意味着学校无法向师生说明数据流向，也无法回答合规问询。

披露必须由**服务端**给出，不能写死在界面文案里：
- 接收方随配置而变（用户可能自带 key 指向别家供应商），
  前端写死就成了假披露；
- 学校导出披露时读接口比抓界面可靠。

要锁住的性质：
1. 披露里的接收方 = **实际生效**的那家（用户配置 > 本校系统配置 > 全局默认）
2. 未配置时不谎报接收方
3. 披露不含 API Key、不含论文内容
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app

client = TestClient(app)

SECRET = "sk-disclosure-must-not-leak-abcdef"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "0123456789abcdef" * 4)


@pytest.fixture(autouse=True)
def _no_env_fallback(monkeypatch):
    """清掉环境变量兜底，否则「未配置」用例会被 PAPERGUARD_AI_* 变成已配置。

    环境变量兜底是既有的三级配置链最后一级，不是缺陷。
    """
    for name in ("PAPERGUARD_AI_BASE_URL", "PAPERGUARD_AI_API_KEY", "PAPERGUARD_AI_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _student_at(school: str = "school-disclosure") -> tuple[str, dict[str, str]]:
    username = f"dis_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    assert client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "student"},
    ).status_code == 201
    store = Store()
    user = store.get_user_by_username(username)
    user["organization_context"] = {"school_id": school}
    store.save_user(user)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return username, {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------

def test_disclosure_requires_login():
    assert client.get("/api/ai/disclosure").status_code == 401


def test_disclosure_reports_nothing_when_unconfigured():
    store = Store()
    store.set_system_ai_config(None, "")
    _, headers = _student_at()
    body = client.get("/api/ai/disclosure", headers=headers).json()
    assert body["enabled"] is False
    assert body["data_recipient_host"] == "", "未配置时不应编造接收方"
    assert body["notice"], "即使未配置也应说明一旦使用会发生什么"


def test_disclosure_names_the_actual_recipient():
    """披露的接收方必须是**真正生效**的那家。"""
    store = Store()
    store.set_system_ai_config(
        {
            "base_url": "https://global-vendor.example/v1",
            "api_key": SECRET,
            "model": "m-global",
        },
        "",
    )
    _, headers = _student_at()
    body = client.get("/api/ai/disclosure", headers=headers).json()
    assert body["enabled"] is True
    assert body["data_recipient_host"] == "global-vendor.example"
    assert body["source"] == "system"
    assert body["model"] == "m-global"
    store.set_system_ai_config(None, "")


def test_user_own_config_wins_over_platform_default():
    """用户自带 key 指向别家时，披露必须跟着变——否则就是假披露。"""
    store = Store()
    store.set_system_ai_config(
        {
            "base_url": "https://global-vendor.example/v1",
            "api_key": SECRET,
            "model": "m-global",
        },
        "",
    )
    username, headers = _student_at()
    user = store.get_user_by_username(username)
    store.set_user_ai_config(
        user["id"],
        {
            "base_url": "https://own-vendor.example/v1",
            "api_key": "sk-own",
            "model": "m-own",
        },
    )
    body = client.get("/api/ai/disclosure", headers=headers).json()
    assert body["data_recipient_host"] == "own-vendor.example", (
        "披露的接收方没有跟随用户自有配置"
    )
    assert body["source"] == "user"
    store.set_system_ai_config(None, "")


def test_disclosure_never_leaks_the_key():
    store = Store()
    store.set_system_ai_config(
        {
            "base_url": "https://global-vendor.example/v1",
            "api_key": SECRET,
            "model": "m-global",
        },
        "",
    )
    _, headers = _student_at()
    raw = client.get("/api/ai/disclosure", headers=headers).text
    assert SECRET not in raw, "披露里出现了 API Key"
    assert "pgsec1$" not in raw, "披露里出现了密文"
    store.set_system_ai_config(None, "")


def test_disclosure_reports_tenant_config_for_that_school():
    """S2 之后不同学校的数据接收方可以不同，披露要跟着租户走。"""
    store = Store()
    store.set_system_ai_config(
        {
            "base_url": "https://school-a-vendor.example/v1",
            "api_key": "sk-a",
            "model": "m-a",
        },
        "school-a",
    )
    store.set_system_ai_config(
        {
            "base_url": "https://school-b-vendor.example/v1",
            "api_key": "sk-b",
            "model": "m-b",
        },
        "school-b",
    )
    _, ha = _student_at("school-a")
    _, hb = _student_at("school-b")
    a = client.get("/api/ai/disclosure", headers=ha).json()
    b = client.get("/api/ai/disclosure", headers=hb).json()
    assert a["data_recipient_host"] == "school-a-vendor.example"
    assert b["data_recipient_host"] == "school-b-vendor.example"
    store.set_system_ai_config(None, "school-a")
    store.set_system_ai_config(None, "school-b")
