"""AI 写作助手 API 测试。

覆盖: 两级配置解析(用户 > 系统)、status 开关、assist 全链路(stub 模型)、
config CRUD 与连通性测试端点、输入校验。

凭据全部动态生成, 不使用字面量口令/密钥。
"""

from __future__ import annotations

import os
import unittest.mock
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import ai as ai_module
from app.main import app

client = TestClient(app)
_admin_login = client.post(
    "/api/auth/login",
    json={"username": "admin", "password": os.environ["PAPERGUARD_ADMIN_PASSWORD"]},
)
client.headers.update({"Authorization": f"Bearer {_admin_login.json()['token']}"})


def _gen_password() -> str:
    return f"Ai-{uuid.uuid4().hex[:12]}-9z"


def _gen_key() -> str:
    return f"sk-{uuid.uuid4().hex}"


def _clear_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PAPERGUARD_AI_BASE_URL", "PAPERGUARD_AI_API_KEY", "PAPERGUARD_AI_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _set_system_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERGUARD_AI_BASE_URL", "http://system-llm.local/v1")
    monkeypatch.setenv("PAPERGUARD_AI_API_KEY", _gen_key())
    monkeypatch.setenv("PAPERGUARD_AI_MODEL", "system-model")


def _new_student(name: str) -> TestClient:
    """注册并登录一个独立学生用户, 返回带其 token 的 client。"""
    pwd = _gen_password()
    client.post("/api/auth/register", json={"username": name, "password": pwd})
    login = client.post("/api/auth/login", json={"username": name, "password": pwd})
    assert login.status_code == 200, login.text
    stu = TestClient(app)
    stu.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
    return stu


def test_status_disabled_without_any_config(monkeypatch: pytest.MonkeyPatch):
    _clear_ai_env(monkeypatch)
    stu = _new_student(f"ai_none_{uuid.uuid4().hex[:8]}")
    resp = stu.get("/api/ai/status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "source": None, "model": None}

    resp = stu.post("/api/ai/assist", json={"action": "polish", "text": "一些内容"})
    assert resp.status_code == 503
    assert "AI 功能未启用" in resp.json()["detail"]


def test_status_system_source(monkeypatch: pytest.MonkeyPatch):
    _set_system_env(monkeypatch)
    stu = _new_student(f"ai_sys_{uuid.uuid4().hex[:8]}")
    data = stu.get("/api/ai/status").json()
    assert data["enabled"] is True
    assert data["source"] == "system"
    assert data["model"] == "system-model"


def test_assist_uses_system_config(monkeypatch: pytest.MonkeyPatch):
    _set_system_env(monkeypatch)
    captured: dict = {}

    def fake_completion(cfg, messages, temperature):
        captured["cfg"] = cfg
        captured["messages"] = messages
        return "润色后的文本内容"

    monkeypatch.setattr(ai_module, "_chat_completion", fake_completion)
    resp = client.post("/api/ai/assist", json={
        "action": "polish", "text": "本文研究了一个问题。",
        "section_title": "第一章 绪论",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": "润色后的文本内容"}
    assert captured["cfg"]["api_key"] == os.environ["PAPERGUARD_AI_API_KEY"]
    assert captured["cfg"]["model"] == "system-model"
    assert "本文研究了一个问题。" in captured["messages"][1]["content"]
    assert "润色" in captured["messages"][1]["content"]


def test_assist_validation(monkeypatch: pytest.MonkeyPatch):
    _clear_ai_env(monkeypatch)
    assert client.post("/api/ai/assist", json={
        "action": "hack", "text": "内容"
    }).status_code == 400
    assert client.post("/api/ai/assist", json={
        "action": "polish", "text": "   "
    }).status_code == 400
    long_text = "字" * (ai_module._MAX_INPUT_CHARS + 1)
    assert client.post("/api/ai/assist", json={
        "action": "polish", "text": long_text
    }).status_code == 400


def test_user_config_crud_and_priority(monkeypatch: pytest.MonkeyPatch):
    _clear_ai_env(monkeypatch)
    stu = _new_student(f"ai_own_{uuid.uuid4().hex[:8]}")
    user_key = _gen_key()

    assert stu.get("/api/ai/config").json()["configured"] is False

    put = stu.put("/api/ai/config", json={
        "base_url": "http://my-llm.local/v1/",
        "api_key": user_key,
        "model": "my-model",
    })
    assert put.status_code == 200, put.text

    got = stu.get("/api/ai/config").json()
    assert got["configured"] is True
    assert got["base_url"] == "http://my-llm.local/v1"
    assert got["model"] == "my-model"
    assert got["api_key_masked"] == "***" + user_key[-4:]

    status = stu.get("/api/ai/status").json()
    assert status["enabled"] is True
    assert status["source"] == "user"
    assert status["model"] == "my-model"

    captured: dict = {}

    def fake_completion(cfg, messages, temperature):
        captured["cfg"] = cfg
        return "ok"

    monkeypatch.setattr(ai_module, "_chat_completion", fake_completion)
    resp = stu.post("/api/ai/assist", json={"action": "continue", "text": "上文内容"})
    assert resp.status_code == 200
    assert captured["cfg"]["api_key"] == user_key
    assert captured["cfg"]["base_url"] == "http://my-llm.local/v1"

    assert stu.delete("/api/ai/config").status_code == 204
    assert stu.get("/api/ai/status").json()["enabled"] is False


def test_user_config_overrides_system(monkeypatch: pytest.MonkeyPatch):
    _set_system_env(monkeypatch)
    stu = _new_student(f"ai_both_{uuid.uuid4().hex[:8]}")
    mine_key = _gen_key()
    stu.put("/api/ai/config", json={
        "base_url": "http://mine.local/v1", "api_key": mine_key, "model": "mine-model",
    })
    captured: dict = {}

    def fake_completion(cfg, messages, temperature):
        captured["cfg"] = cfg
        return "ok"

    monkeypatch.setattr(ai_module, "_chat_completion", fake_completion)
    assert stu.post("/api/ai/assist", json={"action": "polish", "text": "内容"}).status_code == 200
    assert captured["cfg"]["api_key"] == mine_key
    assert captured["cfg"]["model"] == "mine-model"


def test_config_test_endpoint(monkeypatch: pytest.MonkeyPatch):
    payload = {"base_url": "http://t.local/v1", "api_key": _gen_key(), "model": "t-model"}

    monkeypatch.setattr(
        ai_module, "_chat_completion", lambda cfg, messages, temperature: "ok"
    )
    ok = client.post("/api/ai/config/test", json=payload)
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "model": "t-model"}

    def failing(cfg, messages, temperature):
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="AI 服务调用失败 (HTTP 401)")

    monkeypatch.setattr(ai_module, "_chat_completion", failing)
    bad = client.post("/api/ai/config/test", json=payload)
    assert bad.status_code == 200
    body = bad.json()
    assert body["ok"] is False
    assert "HTTP 401" in body["error"]


def test_status_requires_auth():
    anon = TestClient(app)
    assert anon.get("/api/ai/status").status_code == 401
    assert anon.post("/api/ai/assist", json={"action": "polish", "text": "x"}).status_code == 401


def test_base_url_fallback_and_error_parsing(monkeypatch):
    """测试当 Base URL 未加 /v1 且根路径返回 HTML 时能自动 fallback 并解析错误。"""
    calls = []

    def mock_post(url, json=None, headers=None):
        calls.append(url)
        if url == "https://api.test.com/chat/completions":
            # 模拟根路径返回 200 HTML
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text="<!doctype html><html>Home</html>"
            )
        # 模拟 fallback 到 /v1 后的 401 详细错误
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid API key provided"}}
        )

    client_mock = unittest.mock.MagicMock()
    client_mock.__enter__.return_value.post = mock_post
    monkeypatch.setattr(httpx, "Client", lambda **kw: client_mock)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        ai_module._chat_completion(
            {"base_url": "https://api.test.com", "api_key": "k", "model": "m"},
            [{"role": "user", "content": "hi"}],
            0.0
        )
    assert exc.value.status_code == 502
    assert "Invalid API key provided" in exc.value.detail
    assert calls == [
        "https://api.test.com/chat/completions",
        "https://api.test.com/v1/chat/completions"
    ]
