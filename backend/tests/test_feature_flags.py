"""Generic document platform rollout flag tests."""

import uuid

from fastapi.testclient import TestClient

from app.core.feature_flags import general_document_platform_enabled
from app.main import app


def _student_headers(client: TestClient) -> dict:
    """注册并登录一个学生，返回其认证头。

    /api/templates 此前无认证可读，补上登录校验后，用例若继续匿名断言 200
    就会变成断言 401 —— 路由是否受特性开关影响这件事反而测不到了。
    """
    username = f"flagtest_{uuid.uuid4().hex[:8]}"
    password = f"Pwd-{uuid.uuid4().hex[:10]}-8a"
    reg = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "student", "display_name": "特性开关测试学生"},
    )
    assert reg.status_code == 201, reg.text
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_generic_document_routes_can_be_disabled_without_disabling_thesis_routes(monkeypatch) -> None:
    monkeypatch.setenv("PAPERGUARD_FEATURE_GENERAL_DOCUMENT_PLATFORM", "false")
    assert general_document_platform_enabled() is False
    client = TestClient(app)
    headers = _student_headers(client)

    assert client.get("/api/document-packages").status_code == 404
    assert client.get("/api/document-projects").status_code == 404
    # 论文路由不受通用文档开关影响，登录后仍可用
    assert client.get("/api/templates", headers=headers).status_code == 200
    # 补认证后未登录必须被挡住，这里顺带锁住「模板接口不得匿名放行」
    assert client.get("/api/templates").status_code == 401

    monkeypatch.setenv("PAPERGUARD_FEATURE_GENERAL_DOCUMENT_PLATFORM", "true")
    assert general_document_platform_enabled() is True
    assert client.get("/api/document-packages").status_code == 401
