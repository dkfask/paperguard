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

    # 出站守卫会在**请求前**解析 DNS 并逐个检查地址（SSRF 防护的安全边界）。
    # api.test.com 是刻意不可解析的假域名，这里给它一个公网解析结果，
    # 让测试聚焦在它本来要验证的 /v1 fallback 逻辑上。
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

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


def test_minimax_anthropic_base_url_uses_messages_contract(monkeypatch):
    """MiniMax /anthropic 地址走 /v1/messages、X-Api-Key 与 content 文本块。"""
    captured = {}

    def mock_post(url, json=None, headers=None):
        captured.update(url=url, json=json, headers=headers)
        return httpx.Response(
            200,
            json={
                "type": "message",
                "content": [
                    {"type": "thinking", "thinking": "内部推理"},
                    {"type": "text", "text": "MiniMax 返回内容"},
                ],
            },
        )

    client_mock = unittest.mock.MagicMock()
    client_mock.__enter__.return_value.post = mock_post
    monkeypatch.setattr(httpx, "Client", lambda **kw: client_mock)

    result = ai_module._chat_completion(
        {"base_url": "https://api.minimax.cn/anthropic", "api_key": "k", "model": "MiniMax-M2.7"},
        [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "请继续写作"},
        ],
        0.7,
    )

    assert result == "MiniMax 返回内容"
    assert captured["url"] == "https://api.minimax.cn/anthropic/v1/messages"
    assert captured["headers"]["X-Api-Key"] == "k"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"] == {
        "model": "MiniMax-M2.7",
        "messages": [{"role": "user", "content": "请继续写作"}],
        "system": "系统提示",
        "temperature": 0.7,
        "max_tokens": 4000,
    }


def test_anthropic_v1_base_url_does_not_duplicate_version(monkeypatch):
    """已填写 /anthropic/v1 时直接追加 /messages。"""
    calls = []

    def mock_post(url, json=None, headers=None):
        calls.append(url)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client_mock = unittest.mock.MagicMock()
    client_mock.__enter__.return_value.post = mock_post
    monkeypatch.setattr(httpx, "Client", lambda **kw: client_mock)

    assert ai_module._chat_completion(
        {"base_url": "https://api.minimax.cn/anthropic/v1/", "api_key": "k", "model": "m"},
        [{"role": "user", "content": "hi"}],
        0.0,
    ) == "ok"
    assert calls == ["https://api.minimax.cn/anthropic/v1/messages"]


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


def test_request_time_guard_blocks_legacy_saved_private_config(monkeypatch):
    """安全边界在**请求前**，不在保存时。

    场景：修复上线前，用户已经保存过指向内网的 base_url，那条记录还在库里。
    保存路径现在会拦，但对**存量配置**不起作用——必须由请求前的完整校验兜住。
    如果只修保存路径，这些老配置会继续被使用，SSRF 依然成立。
    """
    import socket
    import uuid

    import httpx as _httpx

    username = f"legacy_{uuid.uuid4().hex[:8]}"
    password = "Legacy-aaaaaaaaaa1"
    reg = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "student"},
    )
    assert reg.status_code == 201, reg.text
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 绕过保存路径的检查，直接往库里写一条「修复前就已存在」的私网配置，
    # 精确模拟存量数据，而不是假设保存接口一定会拦住。
    from app.db import Store

    store = Store()
    user = store.get_user_by_username(username)
    store.set_user_ai_config(
        user["id"],
        # 端口取允许列表内的 8080：否则端口检查会先触发，
        # 测到的就不是「私网地址被请求前校验拦下」这一条。
        {"base_url": "http://127.0.0.1:8080/v1", "api_key": "sk-legacy", "model": "m"},
    )

    # 确保请求确实会尝试外发（若没被拦住，这里就会走到 httpx）
    def _boom(*a, **kw):
        raise AssertionError("请求前的出站校验没有拦住私网地址，请求被放行了")

    monkeypatch.setattr(_httpx, "Client", _boom)

    resp = client.post(
        "/api/ai/assist",
        headers=headers,
        json={"action": "polish", "text": "论文正文"},
    )
    assert resp.status_code == 400, f"应被出站校验 400 拦下，实际 {resp.status_code}: {resp.text}"
    detail = resp.text
    assert "回环" in detail or "内网" in detail, detail
