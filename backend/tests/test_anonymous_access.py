"""匿名访问白名单：除白名单外，所有 /api 接口都必须拒绝未认证请求。

背景：逐屏审查第四轮用生产库副本遍历全部 GET 接口时发现，
`GET /api/templates` 与 `GET /api/templates/{id}` 连 Depends(current_user)
都没有，未登录即可枚举并读取学校内部论文模板的完整章节树、排版样式、
封面块与源 Word 文档元数据。

白名单式断言的价值在于：新增接口若忘记加认证，本用例会直接失败，
而不是等下一次安全扫描才发现。
"""

from __future__ import annotations

import pytest
import re
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app

client = TestClient(app)

# 允许匿名访问的接口及理由。健康检查供探针使用；规则包是公开的国标编号，
# 登录页在选包前就需要它，且响应只含编号与名称，不含任何校内资料。
ANONYMOUS_ALLOWED = {
    "/api/health": "健康检查，供容器与负载均衡探针使用",
    "/api/health/live": "存活探针",
    "/api/health/ready": "就绪探针",
    "/api/packs": "公开的国标规则包编号，登录页需要",
    "/api/packs/{pack_id}": "公开国标规则定义，不含校内数据",
}


@pytest.fixture
def any_template_id() -> str:
    templates = Store().list_templates()
    assert templates, "测试库应至少有一个模板"
    return templates[0].id


def test_health_and_packs_stay_public() -> None:
    """白名单中的接口必须继续可匿名访问，避免过度收紧打断登录流程。"""
    for path in ANONYMOUS_ALLOWED:
        actual = path.replace("{pack_id}", client.get("/api/packs").json()[0])
        assert client.get(actual).status_code == 200, f"{path} 应保持匿名可访问"


def test_template_list_requires_authentication() -> None:
    """模板列表：未登录必须 401。

    模板是学校内部论文资料，登录前无需、也不应可见。
    """
    resp = client.get("/api/templates")
    assert resp.status_code == 401, resp.text
    assert resp.json().get("detail") == "未登录"


def test_template_detail_requires_authentication(any_template_id: str) -> None:
    """模板详情：未登录必须 401。

    该端点返回完整章节树、styles、page_layout、cover_blocks、source_docx
    与 rule_packs，是本次漏洞里泄露面最大的一个。
    """
    resp = client.get(f"/api/templates/{any_template_id}")
    assert resp.status_code == 401, resp.text
    assert resp.json().get("detail") == "未登录"


def test_template_detail_still_hidden_when_missing_for_anonymous() -> None:
    """未认证时即便 id 有效也应先被 401 拦下，而不是走到 404 分支泄露存在性。"""
    assert client.get("/api/templates/does-not-exist").status_code == 401


def test_every_other_get_endpoint_rejects_anonymous(any_template_id: str) -> None:
    """Missing resources and validation errors must not masquerade as authentication."""
    schema = app.openapi()
    get_paths = sorted(p for p, ops in schema["paths"].items() if "get" in ops)
    assert len(get_paths) > 50, "接口数量异常，检查 OpenAPI 是否正常生成"

    # Authenticate before both resource lookup and request validation.
    leaked = []
    for path in get_paths:
        if path in ANONYMOUS_ALLOWED:
            continue
        actual = re.sub(r"\{([^}]+)\}", lambda m: any_template_id if m[1] == "template_id" else "anonymous-fixture-id", path)
        status = client.get(actual).status_code
        if status not in {401, 403}:
            leaked.append((path, status))
    assert not leaked, f"以下接口未认证即可访问：{leaked}"
