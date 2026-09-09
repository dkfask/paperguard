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
from app.db import Store
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

    # 查询不会回传明文密钥, 但更新 URL/模型时允许留空并沿用原密钥。
    retained = stu.put("/api/ai/config", json={
        "base_url": "http://my-llm.local/v1",
        "api_key": "",
        "model": "my-model",
    })
    assert retained.status_code == 200, retained.text
    assert stu.get("/api/ai/config").json()["api_key_masked"] == "***" + user_key[-4:]

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


def test_admin_ai_config_crud_and_permissions(monkeypatch):
    """测试管理员配置系统默认模型的权限、CRUD、三级回退与学生继承。"""
    student_client = _new_student("student_adm_test")

    # 1. 学生/非管理员访问管理员配置接口被拒绝 403
    assert student_client.get("/api/ai/admin/config").status_code == 403
    assert student_client.put("/api/ai/admin/config", json={"base_url": "u", "api_key": "k", "model": "m"}).status_code == 403
    assert student_client.delete("/api/ai/admin/config").status_code == 403
    assert student_client.post("/api/ai/admin/config/test", json={"base_url": "u", "api_key": "k", "model": "m"}).status_code == 403

    # 2. 清理环境变量以确保完全受控
    monkeypatch.delenv("PAPERGUARD_AI_BASE_URL", raising=False)
    monkeypatch.delenv("PAPERGUARD_AI_API_KEY", raising=False)
    monkeypatch.delenv("PAPERGUARD_AI_MODEL", raising=False)

    admin_client = client

    # 初始状态未配置
    get_res = admin_client.get("/api/ai/admin/config")
    assert get_res.status_code == 200
    assert get_res.json()["configured"] is False

    # 3. 管理员保存默认模型
    system_key = _gen_key()
    save_res = admin_client.put(
        "/api/ai/admin/config",
        json={"base_url": "https://sys-ai.test.com/v1", "api_key": system_key, "model": "sys-model-pro"},
    )
    assert save_res.status_code == 200
    assert save_res.json() == {"configured": True, "source": "database"}

    # 4. 管理员再次获取，检查脱敏与来源
    cur = admin_client.get("/api/ai/admin/config").json()
    assert cur["configured"] is True
    assert cur["source"] == "database"
    assert cur["base_url"] == "https://sys-ai.test.com/v1"
    assert cur["model"] == "sys-model-pro"
    assert cur["api_key_masked"] == "***" + system_key[-4:]

    retained = admin_client.put(
        "/api/ai/admin/config",
        json={"base_url": "https://sys-ai.test.com/v1", "api_key": "", "model": "sys-model-pro"},
    )
    assert retained.status_code == 200, retained.text
    assert admin_client.get("/api/ai/admin/config").json()["api_key_masked"] == "***" + system_key[-4:]

    # 5. 学生在未配置个人 Key 的情况下，状态显示继承系统默认模型
    st_status = student_client.get("/api/ai/status").json()
    assert st_status["enabled"] is True
    assert st_status["source"] == "system"
    assert st_status["model"] == "sys-model-pro"

    # 6. 学生发起调用，实际走管理员配置的系统默认模型
    calls = []
    def mock_chat(cfg, messages, temperature):
        calls.append(cfg)
        return "管理员默认模型润色结果"

    monkeypatch.setattr(ai_module, "_chat_completion", mock_chat)
    assist_res = student_client.post(
        "/api/ai/assist",
        json={"action": "polish", "text": "待润色段落"},
    )
    assert assist_res.status_code == 200
    assert assist_res.json() == {"text": "管理员默认模型润色结果"}
    assert len(calls) == 1
    assert calls[0]["model"] == "sys-model-pro"
    assert calls[0]["api_key"] == system_key

    # 7. 若学生配置了个人模型，学生优先走个人模型
    user_key = _gen_key()
    student_client.put(
        "/api/ai/config",
        json={"base_url": "https://user-ai.com/v1", "api_key": user_key, "model": "user-model"},
    )
    student_client.post("/api/ai/assist", json={"action": "polish", "text": "再次润色"})
    assert calls[-1]["model"] == "user-model"

    # 8. 管理员删除数据库配置，系统默认回退
    del_res = admin_client.delete("/api/ai/admin/config")
    assert del_res.status_code == 204

    after_del = admin_client.get("/api/ai/admin/config").json()
    assert after_del["configured"] is False


def test_admin_ai_config_survives_relogin(monkeypatch):
    """管理员配置落库后, 新会话仍可直接使用系统默认模型。"""
    _clear_ai_env(monkeypatch)
    admin_client = client
    admin_client.delete("/api/ai/admin/config")

    username = f"ai_relogin_{uuid.uuid4().hex[:8]}"
    password = _gen_password()
    assert client.post("/api/auth/register", json={
        "username": username, "password": password,
    }).status_code == 201
    first_login = client.post("/api/auth/login", json={
        "username": username, "password": password,
    })
    assert first_login.status_code == 200
    first = TestClient(app)
    first.headers.update({"Authorization": f"Bearer {first_login.json()['token']}"})

    key = _gen_key()
    assert admin_client.put("/api/ai/admin/config", json={
        "base_url": "https://persisted-ai.test/v1", "api_key": key, "model": "persisted-model",
    }).status_code == 200
    assert first.get("/api/ai/status").json() == {
        "enabled": True, "source": "system", "model": "persisted-model",
    }

    assert first.post("/api/auth/logout").status_code == 200
    second_login = client.post("/api/auth/login", json={
        "username": username, "password": password,
    })
    assert second_login.status_code == 200
    second = TestClient(app)
    second.headers.update({"Authorization": f"Bearer {second_login.json()['token']}"})
    assert second.get("/api/ai/status").json() == {
        "enabled": True, "source": "system", "model": "persisted-model",
    }

    captured = {}
    monkeypatch.setattr(ai_module, "_chat_completion", lambda cfg, messages, temperature: captured.update(cfg) or "ok")
    response = second.post("/api/ai/assist", json={"action": "polish", "text": "登录后继续写作"})
    assert response.status_code == 200
    assert captured["api_key"] == key
    assert captured["model"] == "persisted-model"

    admin_client.delete("/api/ai/admin/config")
