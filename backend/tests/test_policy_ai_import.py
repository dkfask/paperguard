"""AI 导入写作规范：文档提取、结构化草稿和失败回滚。"""

from __future__ import annotations

import json
import uuid
from io import BytesIO

import pytest
from docx import Document as DocxDocument
from fastapi.testclient import TestClient

from app.api import ai as ai_api
from app.main import app
from app.services.policy_import import normalize_ai_policy


client = TestClient(app)


# AI 导入现在走 _metered_chat（真实上游调用的唯一计量出口），其中第一步就是
# 出站 SSRF 守卫，会在**发请求前**解析 DNS 并逐个检查地址。本文件用
# `example.test` 这类刻意不可解析的假域名，不给公网解析结果就会在守卫阶段
# 直接 400，测不到本来要验证的导入逻辑。
#
# 只解决「假域名需要被解析成公网地址」这一件事；守卫自身的判定测试在
# test_outbound_guard.py，那里不能带这个 fixture。
@pytest.fixture(autouse=True)
def _fake_public_dns(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # 解除本文件的速率限制。AI 导入现在走 _metered_chat，7 个真实上游派发点
    # **全部**扣突发配额（修复前只有 ai_assist 扣）。而本文件用共享的
    # `admin` 账号，全量套件里多个文件都用它跑 AI，累积后会撞上默认的
    # 每分钟 10 次上限，报 429/AI_RATE_LIMITED——那是跨文件污染，不是本文件
    # 要验证的行为。速率/配额语义本身由 test_ai_governance.py 专门覆盖。
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "100000")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "100000")


def _admin_headers() -> dict[str, str]:
    response = client.post("/api/auth/login", json={
        "username": "admin", "password": "TestAdminPassword123",
    })
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _docx_bytes() -> bytes:
    document = DocxDocument()
    document.add_paragraph("本科毕业论文写作规范")
    document.add_paragraph("正文不少于 8000 字，参考文献不少于 10 篇。")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _ai_result() -> str:
    return json.dumps({
        "name": "AI 导入的本科论文规范",
        "version": "2026.1",
        "description": "由上传规范整理的草稿",
        "markdown_content": "# AI 导入的本科论文规范\n\n正文与参考文献要求。",
        "items": [
            {
                "id": "body-min",
                "type": "word_count",
                "target": "body",
                "params": {"min": "8000"},
                "severity": "error",
                "enforce": "hard",
                "description": "正文不少于 8000 字",
            },
            {
                "id": "format-note",
                "type": "auto",
                "target": "body",
                "checker": "未知检查器",
                "description": "正文按学校要求排版",
            },
        ],
    }, ensure_ascii=False)


def _enable_ai(monkeypatch) -> None:
    monkeypatch.setenv("PAPERGUARD_AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("PAPERGUARD_AI_API_KEY", "test-key")
    monkeypatch.setenv("PAPERGUARD_AI_MODEL", "test-model")


def test_ai_import_creates_draft_and_keeps_source_document(monkeypatch):
    _enable_ai(monkeypatch)
    captured: dict[str, str] = {}

    def fake_completion(_cfg, messages, _temperature, **_kwargs):
        captured["prompt"] = messages[1]["content"]
        return _ai_result()

    monkeypatch.setattr(ai_api, "_chat_completion", fake_completion)
    headers = _admin_headers()
    response = client.post(
        "/api/policies/import",
        headers=headers,
        files={"file": ("school-guide.md", "正文不少于 8000 字", "text/markdown")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    policy = body["policy"]
    assert policy["status"] == "draft"
    assert policy["name"] == "AI 导入的本科论文规范"
    assert policy["items"][0]["checker"] == "section.word_count"
    assert policy["items"][0]["params"]["min"] == 8000
    assert policy["items"][1]["type"] == "manual"
    assert policy["items"][1]["checker"] == ""
    assert policy["documents"][0]["filename"] == "school-guide.md"
    assert body["analysis"]["auto_rule_count"] == 1
    assert "正文不少于 8000 字" in captured["prompt"]

    downloaded = client.get(
        f"/api/policies/{policy['id']}/documents/{policy['documents'][0]['id']}",
        headers=headers,
    )
    assert downloaded.status_code == 200
    assert downloaded.content == "正文不少于 8000 字".encode()
    audit = client.get(f"/api/policies/{policy['id']}/audit", headers=headers)
    assert audit.status_code == 200
    assert any(event["action"] == "ai_imported" for event in audit.json())


def test_policy_import_keeps_explicit_cover_and_reference_checkers_automatic():
    raw = json.dumps({"name": "1101 试点规则", "items": [
        {
            "id": "cover-required-fields", "type": "auto", "target": "cover",
            "checker": "cover.fields_present",
            "params": {"field_keys": ["title", "college", "major", "class_name", "author", "student_id", "advisor", "date"]},
            "severity": "error", "enforce": "hard", "description": "封面必填字段均应填写",
        },
        {
            "id": "reference-list", "type": "auto", "target": "references",
            "checker": "references.list_facts",
            "params": {"required": True, "numbering": "sequential", "bracket_style": "square",
                        "language_order": "zh_before_en"},
            "severity": "error", "enforce": "hard", "description": "参考文献事实、编号与排序符合试点规则",
        },
        {
            "id": "reference-number-year", "type": "auto", "target": "references",
            "checker": "references.format", "params": {"format_check": "number_and_year"},
            "severity": "warning", "enforce": "soft", "description": "只核验序号与年份",
        },
    ]}, ensure_ascii=False)

    policy, analysis = normalize_ai_policy(raw, "1101.md")

    assert [item["type"] for item in policy["items"]] == ["auto", "auto", "auto"]
    assert [item["checker"] for item in policy["items"]] == [
        "cover.fields_present", "references.list_facts", "references.format",
    ]
    assert analysis["auto_rule_count"] == 3


@pytest.mark.parametrize("item", [
    {"id": "cover", "type": "auto", "checker": "cover.fields_present", "params": {"field_keys": ["title", "not-a-field"]}},
    {"id": "refs", "type": "auto", "checker": "references.list_facts", "params": {"numbering": "sort-by-author"}},
    {"id": "refs-empty", "type": "auto", "checker": "references.list_facts", "params": {}},
    {"id": "refs-generic-format", "type": "reference_format", "params": {}},
])
def test_policy_import_downgrades_incomplete_or_unsupported_rule_mappings(item):
    policy, analysis = normalize_ai_policy(json.dumps({"items": [item]}), "guide.md")

    assert policy["items"][0]["type"] == "manual"
    assert policy["items"][0]["checker"] == ""
    assert analysis["auto_rule_count"] == 0
    assert analysis["manual_rule_count"] == 1


def test_policy_import_preserves_optional_sequential_reference_check():
    item = {"id": "refs-optional-numbering", "type": "auto", "checker": "references.list_facts",
            "params": {"required": False, "numbering": "sequential"}}
    policy, _ = normalize_ai_policy(json.dumps({"items": [item]}), "guide.md")
    assert policy["items"][0]["type"] == "auto"
    assert policy["items"][0]["params"] == item["params"]


def test_ai_import_extracts_docx_text_before_calling_model(monkeypatch):
    _enable_ai(monkeypatch)
    captured: dict[str, str] = {}

    def fake_completion(_cfg, messages, _temperature, **_kwargs):
        captured["prompt"] = messages[1]["content"]
        return json.dumps({"name": "Word 规范", "items": [{"type": "manual", "description": "人工核对"}]})

    monkeypatch.setattr(ai_api, "_chat_completion", fake_completion)
    response = client.post(
        "/api/policies/import",
        headers=_admin_headers(),
        files={"file": (
            "guide.docx", _docx_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )},
    )

    assert response.status_code == 201, response.text
    assert "本科毕业论文写作规范" in captured["prompt"]
    assert "正文不少于 8000 字" in captured["prompt"]


def test_ai_import_retries_when_first_structured_response_is_invalid(monkeypatch):
    _enable_ai(monkeypatch)
    calls: list[dict] = []

    def fake_completion(_cfg, messages, _temperature, **kwargs):
        calls.append({"prompt": messages[1]["content"], "max_tokens": kwargs["max_tokens"]})
        return "不完整的 JSON" if len(calls) == 1 else _ai_result()

    monkeypatch.setattr(ai_api, "_chat_completion", fake_completion)
    response = client.post(
        "/api/policies/import",
        headers=_admin_headers(),
        files={"file": ("guide.md", b"PDF text is extracted before this mocked call", "text/markdown")},
    )

    assert response.status_code == 201, response.text
    assert len(calls) == 2
    assert all(call["max_tokens"] == 8000 for call in calls)
    assert "上一次结果不完整" in calls[1]["prompt"]
    assert response.json()["analysis"]["ai_retried"] is True


def test_ai_import_merges_duplicate_section_checks(monkeypatch):
    _enable_ai(monkeypatch)
    result = json.dumps({
        "name": "重复章节规则规范",
        "items": [
            {
                "id": "chapter-intro",
                "type": "auto",
                "target": "document",
                "checker": "section.exists",
                "params": {"section_key": "introduction"},
                "description": "绪论必须存在",
            },
            {
                "id": "chapter-body",
                "type": "auto",
                "target": "document",
                "checker": "section.exists",
                "params": {"section_key": "body"},
                "description": "正文必须存在",
            },
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr(ai_api, "_chat_completion", lambda *_args, **_kwargs: result)

    response = client.post(
        "/api/policies/import",
        headers=_admin_headers(),
        files={"file": ("duplicate.md", "规范正文", "text/markdown")},
    )

    assert response.status_code == 201, response.text
    items = response.json()["policy"]["items"]
    assert len(items) == 1
    assert items[0]["type"] == "auto"
    assert items[0]["params"]["section_keys"] == ["introduction", "body"]
    assert response.json()["analysis"]["duplicate_rule_count"] == 1


def test_ai_import_preserves_explicit_format_requirements(monkeypatch):
    _enable_ai(monkeypatch)
    result = json.dumps({
        "name": "格式规范",
        "markdown_content": "# 格式规范\n\n仅有摘要。",
        "items": [{"id": "body-min", "type": "auto", "checker": "word_count", "target": "body", "params": {"min": 8000}}],
    }, ensure_ascii=False)
    monkeypatch.setattr(ai_api, "_chat_completion", lambda *_args, **_kwargs: result)

    source = "正文中文字体应为宋体，西文字体应为 Times New Roman。\n\n正文行距应为 1.5 倍，首行缩进 2 字符。"
    response = client.post(
        "/api/policies/import",
        headers=_admin_headers(),
        files={"file": ("format.md", source, "text/markdown")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert "上传规范原文" in body["policy"]["markdown_content"]
    assert "宋体" in body["policy"]["markdown_content"]
    format_items = [item for item in body["policy"]["items"] if item["id"].startswith("source-format-")]
    assert len(format_items) == 2
    assert any("Times New Roman" in item["description"] for item in format_items)
    assert any("1.5 倍" in item["description"] for item in format_items)
    assert body["analysis"]["format_rule_count"] == 2
    assert body["analysis"]["source_text_preserved"] is True
    assert body["analysis"]["preserved_source_characters"] == len(source)


def test_ai_import_does_not_save_policy_when_model_returns_invalid_json(monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(ai_api, "_chat_completion", lambda *_args, **_kwargs: "这不是 JSON")
    response = client.post(
        "/api/policies/import",
        headers=_admin_headers(),
        files={"file": (f"invalid-{uuid.uuid4().hex}.md", "规范正文", "text/markdown")},
    )
    assert response.status_code == 502
    assert "无法转换" in response.json()["detail"]


def test_ai_import_rejects_legacy_doc_file(monkeypatch):
    called = False

    def fake_completion(*_args):
        nonlocal called
        called = True
        return _ai_result()

    monkeypatch.setattr(ai_api, "_chat_completion", fake_completion)
    response = client.post(
        "/api/policies/import",
        headers=_admin_headers(),
        files={"file": ("legacy.doc", b"legacy", "application/msword")},
    )
    assert response.status_code == 415
    assert called is False
