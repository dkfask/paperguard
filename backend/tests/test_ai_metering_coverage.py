"""计量覆盖：证明**每一个**真实上游派发点都记账（GAP-C-01 回归）。

背景（**修复前**的状态，已不可复算，仅作历史留存）：配额与用量记账内联在
`/api/ai/assist` 一处，当时有若干条派发路径**完全不计量**——不扣配额、
不留记录。后果是单账号可无限次免费消耗上游额度，且事后无法回答
「谁花了多少」。

> **数字已更正（2026-10-02，INC-87）**：本文件此前写「全仓共有 **7** 个
> `_chat_completion` 调用点」，而现状是 **2** 个（都在 `_metered_chat` 内的
> `max_tokens` 两个分支）。**7 是重构前的数字**，而重构后 6 条命令全部经
> `_metered_chat` 收敛——`ai_egress_check` 的 AE-1/AE-2 守的就是这个收敛。
> **本文件的 6 个场景对应的是「6 条 AI 写操作处理器」，不是 6 个调用点。**
> 这个对应关系现由 `ai_egress_check` 的 **AE-5** 按**路径逐条核对**
> （静态派发点 ↔ 本文件场景的 `url`，双向差集），
> 新增第 7 条 AI 命令时**不再静默失覆盖**。
>
> **口径升级记录（2026-10-02，INC-87）**：AE-5 首版只比「静态派发点数 == 场景条数」。
> 那有两个**数字基数相同就互相抵消**的假绿——「新增一条派发点 + 复制一条已有场景」
> 与「删掉一条场景 + 新增一条派发点」都是 6 == 6。现按 `url` 逐条比对，
> 并要求静态侧用 **AST 调用计数**（不是文本搜索）判定「真的经计量出口」。

当前 6 条派发点（`api/ai.py` 的两个配置测试与 `ai_assist`，
`api/policies.py` 规范导入、`api/projects.py` 需求辅助、`api/specs.py` 规范解析）：

本文件的设计要点：

1. **从 HTTP 侧断言，不直接调 `_metered_chat`**。直接调被测函数等于自证，
   无法证明入口真的接线（曾出现过删掉入口里的计量、9 条用例仍全绿的假绿）。
2. **逐个派发点断言 action 名**，而不是只断言「总条数增加了」。总条数会被
   重试路径放大，用总数断言等于给漏点留后门。
3. **按 user_id + action + 唯一标记四重过滤**取本次用例产生的行。
   `ORDER BY created_at DESC` 在同一秒内多条记录顺序不保证稳定，
   「取最新一条」会间歇性失败，也会取到别的测试写的行。
4. **重试路径单独断言次数**：初解析与重试是两次真实派发，必须各记一次。
5. **出站守卫不关**。它会解析 DNS 并逐个检查地址，这是 SSRF 防护的边界；
   本文件用 `.example` 假域名并注入公网解析结果（守卫自身的判定测试在
   test_outbound_guard.py，不带这个桩）。
"""

from __future__ import annotations

import json
import socket
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import ai as ai_module
from app.db import Store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fake_public_dns(monkeypatch):
    """把本文件的假供应商域名解析到公网地址，让出站守卫放行。"""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # 解除速率限制：本文件用共享账号，跨文件累积会撞默认每分钟 10 次上限。
    # 配额语义本身由 test_ai_governance.py 专门覆盖。
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "100000")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "100000")


def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _student() -> tuple[str, dict[str, str]]:
    username = f"meter_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    reg = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "role": "student"},
    )
    assert reg.status_code == 201, reg.text
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return username, {"Authorization": f"Bearer {login.json()['token']}"}


def _admin_headers() -> dict[str, str]:
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "TestAdminPassword123",
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _configure_ai(username: str, base_url: str = "https://api.vendor.example/v1",
                  model: str = "m-test") -> None:
    """给用户写一份 AI 配置。

    `model` 必须与 `_usage_rows` 用的标记一致：`record_usage` 记的是
    **实际派发时 cfg 里的 model**，不是替身函数的任何属性。用配置里的
    model 当标记，才能把本用例的行与其他行精确分开。
    """
    store = Store()
    user = store.get_user_by_username(username)
    store.set_user_ai_config(
        user["id"], {"base_url": base_url, "api_key": "sk-test", "model": model}
    )


@pytest.fixture
def preserve_admin_ai_config():
    """本文件有若干场景必须借用 `admin` 账号（它要管理权限才能跑
    `/api/policies/import`、`/api/specs/parse`），因此会覆盖 admin 的 AI 配置。
    但 `admin` 是**全仓共享**账号，test_ai_api.py 等文件依赖它自己的配置
    （那里断言的是「系统配置生效」）。不还原就是跨文件污染。
    """
    store = Store()
    user = store.get_user_by_username("admin")
    assert user is not None, "测试库缺少 admin 账号"
    saved = store.get_user_ai_config(user["id"])
    try:
        yield
    finally:
        store.set_user_ai_config(user["id"], saved)


def _usage_rows(user_id: str, action: str, marker: str) -> list[dict]:
    """按 user_id + action + 唯一标记精确取本次用例写入的行。

    不用「取最新一条」：`created_at` 精度到秒，同一秒内多条记录顺序不保证，
    那样既会间歇性失败，也会把别的 actor 写的行算进来。
    """
    store = Store()
    with store._connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM ai_usage WHERE user_id = ? AND action = ? AND model = ?",
                (user_id, action, marker),
            ).fetchall()
        ]


@pytest.fixture
def stub_upstream(monkeypatch):
    """把 `_chat_completion` 换成计数替身。

    唯一标记写在 `model` 里——但真正被记进 `ai_usage.model` 的是**派发时
    cfg 里的 model**，所以调用方仍需把同一标记写进配置（见 `_configure_ai`）。
    替身只负责计数，让用例能断言「记账条数 == 真实派发次数」。
    """
    state = {"calls": 0}

    def _install(reply: str = "ok") -> None:
        state["calls"] = 0

        def _fake(cfg, messages, temperature, **_kwargs):
            state["calls"] += 1
            return reply

        monkeypatch.setattr(ai_module, "_chat_completion", _fake)

    _install.state = state  # type: ignore[attr-defined]
    return _install


# --------------------------------------------------------------------------
# 派发点 1/7：用户测试自己的模型配置
# --------------------------------------------------------------------------

def test_config_test_is_metered(stub_upstream):
    username, headers = _student()
    model = "model-config-test"
    _configure_ai(username, model=model)
    stub_upstream()
    user_id = Store().get_user_by_username(username)["id"]

    resp = client.post("/api/ai/config/test", headers=headers, json={
        "base_url": "https://api.vendor.example/v1",
        "api_key": "sk-test", "model": model,
    })

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True, resp.text
    rows = _usage_rows(user_id, "config_test", model)
    assert len(rows) == 1, f"配置测试未计量或重复计量: {rows}"
    assert rows[0]["result"] == "success"
    assert rows[0]["output_chars"] == 2


# --------------------------------------------------------------------------
# 派发点 2/7：管理员测试系统默认配置
# --------------------------------------------------------------------------

def test_admin_config_test_is_metered(stub_upstream):
    admin = _admin_headers()
    admin_id = client.get("/api/auth/me", headers=admin).json()["id"]
    model = "model-admin-config-test"
    stub_upstream()

    resp = client.post("/api/ai/admin/config/test", headers=admin, json={
        "base_url": "https://api.vendor.example/v1",
        "api_key": "sk-test", "model": model,
    })

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True, resp.text
    rows = _usage_rows(admin_id, "admin_config_test", model)
    assert len(rows) == 1, f"管理员配置测试未计量或重复计量: {rows}"
    assert rows[0]["result"] == "success"
    # source 必须能分辨「这笔是管理员测平台配置产生的」，否则无法做成本分摊。
    # 三种取值都合法：请求自带配置 / 落库配置 / 环境变量配置。
    assert rows[0]["source"] in {"user", "system", "system_env"}, rows[0]["source"]


# --------------------------------------------------------------------------
# 派发点 3/7：写作辅助（修复前唯一被计量的路径，作为对照基准）
# --------------------------------------------------------------------------

def test_assist_is_metered(stub_upstream):
    username, headers = _student()
    model = "model-assist"
    _configure_ai(username, model=model)
    stub_upstream()
    user_id = Store().get_user_by_username(username)["id"]

    resp = client.post("/api/ai/assist", headers=headers, json={
        "action": "polish", "text": "论文正文",
    })

    assert resp.status_code == 200, resp.text
    rows = _usage_rows(user_id, "polish", model)
    assert len(rows) == 1, f"assist 未计量或重复计量: {rows}"
    assert stub_upstream.state["calls"] == 1, rows


# --------------------------------------------------------------------------
# 派发点 4/7 + 5/7：规范导入的初解析与重试
#
# 两次都是真实上游派发，必须**各记一次**。若只记一次，等于给重试留了
# 免费额度口子——而重试恰恰发生在模型输出质量最差的时刻。
# --------------------------------------------------------------------------

_POLICY_AI_JSON = json.dumps({
    "title": "本科毕业论文写作规范",
    "items": [
        {"type": "manual", "target": "document", "checker": "",
         "params": {}, "severity": "warning", "enforce": "soft",
         "description": "正文不少于 8000 字"},
    ],
})


def test_policy_import_retry_meters_each_dispatch(monkeypatch, stub_upstream, preserve_admin_ai_config):
    """初解析返回无效 JSON → 触发重试 → 两次派发各记一条。"""
    admin = _admin_headers()
    admin_id = client.get("/api/auth/me", headers=admin).json()["id"]
    model = "model-policy-import"
    _configure_ai("admin", model=model)
    stub_upstream()

    def _fake(cfg, messages, temperature, **_kwargs):
        # 第一次返回不可解析内容触发重试，第二次返回合法 JSON
        stub_upstream.state["calls"] += 1
        if stub_upstream.state["calls"] == 1:
            return "这不是 JSON"
        return _POLICY_AI_JSON

    monkeypatch.setattr(ai_module, "_chat_completion", _fake)

    resp = client.post(
        "/api/policies/import", headers=admin,
        files={"file": ("guide.md", "正文不少于 8000 字", "text/markdown")},
    )

    assert resp.status_code == 201, resp.text
    rows = _usage_rows(admin_id, "policy_import", model)
    assert len(rows) == 2, (
        f"规范导入的初解析+重试应各记一次，实际 {len(rows)} 条: {rows}"
    )
    assert stub_upstream.state["calls"] == 2, "替身被调用的次数与记账条数不一致"


# --------------------------------------------------------------------------
# 派发点 6/7：需求项 AI 辅助
# --------------------------------------------------------------------------

def test_requirement_assist_is_metered(monkeypatch, stub_upstream, preserve_admin_ai_config):
    admin = _admin_headers()
    admin_id = client.get("/api/auth/me", headers=admin).json()["id"]
    suffix = uuid.uuid4().hex[:8]
    policy_id = f"meter-policy-{suffix}"
    item_id = f"meter-item-{suffix}"
    policy = client.post("/api/policies", headers=admin, json={
        "id": policy_id, "name": f"计量覆盖规范 {suffix}", "level": "school",
        "status": "published", "version": "1", "scope_id": f"isolated-{suffix}",
        "items": [{
            "id": item_id, "type": "manual", "target": "document", "checker": "",
            "params": {}, "severity": "warning", "enforce": "soft",
            "description": "人工核对论文内容",
        }],
    })
    assert policy.status_code == 201, policy.text
    template = client.get("/api/templates/default-gb7713", headers=admin).json()
    template.update({
        "id": f"meter-template-{suffix}", "name": "计量覆盖模板",
        "requirement_set_ids": [policy_id],
    })
    assert client.post("/api/templates", headers=admin, json=template).status_code == 201
    project = client.post("/api/projects", headers=admin, json={
        "template_id": f"meter-template-{suffix}", "title": "计量覆盖项目",
    })
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    section = next(s for s in project.json()["sections"] if s["type"] not in ("cover", "toc"))
    assert client.put(f"/api/projects/{project_id}/section/{section['key']}",
                      headers=admin, json={
                          "paragraphs": [{"id": "meter-content", "text": "这是一段论文正文。"}],
                      }).status_code == 200

    # 该入口从系统配置取 cfg，不读用户配置，因此直接给管理员配一份。
    model = "model-requirement-assist"
    _configure_ai("admin", model=model)
    stub_upstream()
    monkeypatch.setattr(ai_module, "_resolve_cfg", lambda store, user: (
        {"base_url": "https://api.vendor.example/v1", "api_key": "sk-test", "model": model},
        "user",
    ))

    resp = client.post(
        f"/api/projects/{project_id}/requirements/{item_id}/assist",
        headers=admin, json={"question": "请核对正文内容"},
    )

    assert resp.status_code == 200, resp.text
    rows = _usage_rows(admin_id, "requirement_assist", model)
    assert len(rows) == 1, f"需求辅助未计量或重复计量: {rows}"


# --------------------------------------------------------------------------
# 派发点 7/7：规范解析导入
# --------------------------------------------------------------------------

def test_spec_import_is_metered(monkeypatch, stub_upstream, preserve_admin_ai_config):
    admin = _admin_headers()
    admin_id = client.get("/api/auth/me", headers=admin).json()["id"]
    model = "model-spec-import"
    _configure_ai("admin", model=model)
    # 规范解析引擎要的是一段结构化文本。解析结果本身不参与本用例断言——
    # 本用例只证明「发生过真实派发就一定留下记账行」。
    stub_upstream(reply="1. 格式要求")
    monkeypatch.setattr(ai_module, "_resolve_cfg", lambda store, user: (
        {"base_url": "https://api.vendor.example/v1", "api_key": "sk-test", "model": model},
        "user",
    ))

    resp = client.post(
        "/api/specs/parse", headers=admin,
        files={"file": ("spec.md", "1. 格式要求\n正文不少于 8000 字", "text/markdown")},
    )

    # 解析能否成功取决于替身返回的内容，本用例不关心；但只要**真的派发过**
    # 上游，就一定要有记账行。200/422/502 都表示「已派发」。
    assert resp.status_code in (200, 422, 502), resp.text
    rows = _usage_rows(admin_id, "spec_import", model)
    assert len(rows) >= 1, f"规范解析未计量: {rows}"
    assert all(r["result"] == "success" for r in rows), rows


# --------------------------------------------------------------------------
# 反向护栏：出站被拦下时**不得**占用额度
#
# 出站校验先于配额——请求根本没发出去，就不该扣用户额度。
# --------------------------------------------------------------------------

def test_outbound_blocked_does_not_consume_quota(monkeypatch, stub_upstream):
    username, headers = _student()
    _configure_ai(username, base_url="http://127.0.0.1:8080/v1")
    user_id = Store().get_user_by_username(username)["id"]

    def _boom(*_a, **_kw):
        raise AssertionError("被出站守卫拦下的请求不应真的发往上游")

    monkeypatch.setattr(ai_module, "_chat_completion", _boom)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: [
                            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))])

    resp = client.post("/api/ai/assist", headers=headers, json={
        "action": "polish", "text": "论文正文",
    })

    assert resp.status_code == 503 or resp.status_code == 400, resp.text
    with Store()._connect() as conn:
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
    assert used == 0, "被出站守卫拦下的请求不应产生任何用量记录"


# --------------------------------------------------------------------------
# 配额门禁覆盖：证明**每一个**派发点都真的会扣配额
#
# 为什么要单独一组：上面的用例只断言「留下了记账行」。而记账与扣配额是
# 两件独立的事——只验记账的话，把 `begin_call` 整段删掉，7 条用例仍然全绿。
# 变异实测：删掉 `_metered_chat` 里的 `begin_call` 后，全套只有
# `test_ai_governance.py::test_assist_endpoint_enforces_quota_over_http`
# 一条报警，而它只覆盖 `/api/ai/assist`——另外 5 条路径当时**完全没有**
# 配额门禁测试。
#
# 判据：把突发限额压到 1，第 1 次派发必须成功，第 2 次必须 429 且
# **上游替身一次都不能被再调用**（被配额拦下的请求根本没发出去）。
# --------------------------------------------------------------------------

def _scn_config_test() -> tuple[dict, dict]:
    username, headers = _student()
    model = f"quota-config-test-{uuid.uuid4().hex[:6]}"
    _configure_ai(username, model=model)
    return headers, {"url": "/api/ai/config/test", "json": {
        "base_url": "https://api.vendor.example/v1", "api_key": "sk-test", "model": model}}


def _scn_admin_config_test() -> tuple[dict, dict]:
    headers = _admin_headers()
    model = f"quota-admin-config-{uuid.uuid4().hex[:6]}"
    return headers, {"url": "/api/ai/admin/config/test", "json": {
        "base_url": "https://api.vendor.example/v1", "api_key": "sk-test", "model": model}}


def _scn_assist() -> tuple[dict, dict]:
    username, headers = _student()
    _configure_ai(username, model=f"quota-assist-{uuid.uuid4().hex[:6]}")
    return headers, {"url": "/api/ai/assist", "json": {
        "action": "polish", "text": "论文正文"}}


def _scn_policy_import() -> tuple[dict, dict]:
    headers = _admin_headers()
    _configure_ai("admin", model=f"quota-policy-{uuid.uuid4().hex[:6]}")
    return headers, {
        "url": "/api/policies/import",
        "files": {"file": ("guide.md", "正文不少于 8000 字", "text/markdown")},
    }


def _scn_requirement_assist() -> tuple[dict, dict]:
    headers = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    policy_id, item_id = f"quota-policy-{suffix}", f"quota-item-{suffix}"
    policy = client.post("/api/policies", headers=headers, json={
        "id": policy_id, "name": f"配额覆盖规范 {suffix}", "level": "school",
        "status": "published", "version": "1", "scope_id": f"isolated-{suffix}",
        "items": [{
            "id": item_id, "type": "manual", "target": "document", "checker": "",
            "params": {}, "severity": "warning", "enforce": "soft",
            "description": "人工核对论文内容",
        }],
    })
    assert policy.status_code == 201, policy.text
    template = client.get("/api/templates/default-gb7713", headers=headers).json()
    template.update({"id": f"quota-template-{suffix}", "name": "配额覆盖模板",
                     "requirement_set_ids": [policy_id]})
    assert client.post("/api/templates", headers=headers, json=template).status_code == 201
    project = client.post("/api/projects", headers=headers, json={
        "template_id": f"quota-template-{suffix}", "title": "配额覆盖项目"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    section = next(s for s in project.json()["sections"] if s["type"] not in ("cover", "toc"))
    assert client.put(f"/api/projects/{project_id}/section/{section['key']}", headers=headers,
                      json={"paragraphs": [{"id": "quota-content", "text": "论文正文。"}]}
                      ).status_code == 200
    _configure_ai("admin", model=f"quota-req-{uuid.uuid4().hex[:6]}")
    return headers, {
        "url": f"/api/projects/{project_id}/requirements/{item_id}/assist",
        "json": {"question": "请核对正文内容"},
    }


def _scn_spec_import() -> tuple[dict, dict]:
    headers = _admin_headers()
    _configure_ai("admin", model=f"quota-spec-{uuid.uuid4().hex[:6]}")
    return headers, {
        "url": "/api/specs/parse",
        "files": {"file": ("spec.md", "1. 格式要求\n正文不少于 8000 字", "text/markdown")},
    }


# 顺序与文件头部的 6 条派发点列表一致。
# ★ AE-5 核对的是**路径级双向差集**：本列表每条场景的 `url` 必须对应一个
#   静态派发点，且**每个静态派发点都必须有场景**。只比条数是不够的——
#   条数相同不代表是同一批（见文件头部「口径升级记录」）。
_SCENARIOS = [
    ("用户配置测试", _scn_config_test),
    ("管理员配置测试", _scn_admin_config_test),
    ("写作辅助", _scn_assist),
    ("规范导入", _scn_policy_import),
    ("需求项辅助", _scn_requirement_assist),
    ("规范解析", _scn_spec_import),
]


def _reset_quota(user_id: str) -> None:
    """清零该用户在**当前分钟**的突发计数。

    必须做：多个场景复用同一个 `admin` 账号，而本文件前面的用例已经在
    同一分钟内消耗过它的额度。突发计数是按 (user_id, 分钟) 累积的，
    不清零的话第 1 次请求就会直接 429，测不到「第 1 次放行、第 2 次拦截」
    这个真正要验的行为。日配额不清——它限的是当天总量，与本用例无关。
    """
    from app.services import ai_governance

    minute_period = ai_governance._utc_periods(
        ai_governance.datetime.now(ai_governance.timezone.utc))[1]
    with Store()._connect() as conn:
        conn.execute(
            "DELETE FROM ai_quota WHERE user_id = ? AND period = ?",
            (user_id, minute_period),
        )


# 两个配置测试端点**把限流包在 200 里返回**（`{"ok": false, "error": {...}}`），
# 这是它们的既有契约——「测试连通性」的语义是「告诉你结果」，不是「拒绝请求」。
# 所以判据必须按端点区分，不能一律断言 429。
_STATUS_CHECKS = {
    "/api/ai/config/test": False,
    "/api/ai/admin/config/test": False,
}


def _assert_rate_limited(resp, label: str) -> None:
    body = resp.json()
    detail = body.get("detail")
    if isinstance(detail, dict):
        code = detail.get("code")
    else:
        # 200 内嵌错误：{"ok": false, "error": {...}}
        assert body.get("ok") is False, f"[{label}] 第 2 次既没 429 也没报错: {resp.text}"
        code = (body.get("error") or {}).get("code")
    assert code == "AI_RATE_LIMITED", f"[{label}] 限流错误码不对: {resp.text}"


@pytest.mark.parametrize("label,setup", _SCENARIOS, ids=[s[0] for s in _SCENARIOS])
def test_every_dispatch_point_enforces_burst_quota(monkeypatch, stub_upstream, preserve_admin_ai_config, label, setup):
    """每条路径都必须扣突发配额：第 2 次被限流拦下，且不再触达上游。"""
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "1")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "100000")
    # 规范导入在模型输出不可解析时会自动重试，那会让一次请求派发两次、
    # 把「第 2 次」消耗在自己手里，测不出入口配额。这里固定返回合法 JSON。
    stub_upstream(reply=_POLICY_AI_JSON)

    headers, call = setup()
    url = call["url"]
    payload = {k: v for k, v in call.items() if k != "url"}
    # 共享账号（admin）的分钟计数可能被本文件前面的用例用掉，先清零。
    actor_id = client.get("/api/auth/me", headers=headers).json()["id"]
    _reset_quota(actor_id)

    first = client.post(url, headers=headers, **payload)
    assert stub_upstream.state["calls"] == 1, (
        f"[{label}] 第 1 次应真实派发一次，实际 {stub_upstream.state['calls']} 次: {first.text}"
    )
    assert not (
        first.status_code == 429 or (isinstance(first.json().get("error"), dict)
                                     and first.json()["error"].get("code") == "AI_RATE_LIMITED")
    ), f"[{label}] 第 1 次不应被限流: {first.text}"

    second = client.post(url, headers=headers, **payload)
    _assert_rate_limited(second, label)
    assert stub_upstream.state["calls"] == 1, (
        f"[{label}] 被配额拦下的请求不应真的发往上游"
    )
