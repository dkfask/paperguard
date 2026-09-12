"""Phase 3 generic specification parsing and confirmation tests."""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.domain.documents.specs import SpecRule
from app.main import app
from app.services.spec_engine import (
    SpecCandidateError,
    SpecParseCandidate,
    confirm_spec_candidate,
    parse_spec_document,
    validate_spec_candidate,
)


MARKDOWN_SPEC = """---
name: 实验报告规范
version: '2'
description: 课程实验报告结构化规则
rules:
  - id: body-min
    type: auto
    target: body
    checker: word_count
    params:
      min: 100
    severity: error
    enforce: hard
    description: 正文不少于 100 字
    source: 第 2 条
  - id: cover-manual
    type: manual
    target: document
    description: 封面信息需人工核对
    source: 第 1 条
---

# 实验报告规范

提交前请核对封面信息。
"""


def test_markdown_frontmatter_is_parsed_without_ai() -> None:
    candidate = parse_spec_document(
        MARKDOWN_SPEC.encode(),
        "lab-spec.md",
        candidate_id="speccand-1",
        spec_set_id="spec-1",
    )

    assert candidate.extraction_mode == "deterministic"
    assert candidate.status == "needs_confirmation"
    assert candidate.name == "实验报告规范"
    assert candidate.rules[0].checker == "section.word_count"
    assert candidate.rules[0].params["min"] == 100
    assert candidate.rules[1].kind == "manual"
    assert not [issue for issue in candidate.issues if issue.severity == "error"]


def test_non_structured_document_requires_injected_ai_completion() -> None:
    with pytest.raises(RuntimeError, match="AI 配置未提供"):
        parse_spec_document(
            "正文规范".encode(),
            "spec.txt",
            candidate_id="speccand-2",
            spec_set_id="spec-2",
        )


def test_ai_result_is_normalized_and_unknown_checker_is_not_executable() -> None:
    calls: list[list[dict[str, str]]] = []

    def completion(messages: list[dict[str, str]]) -> str:
        calls.append(messages)
        return json.dumps({
            "name": "通用报告规范",
            "version": "1",
            "items": [
                {
                    "id": "known",
                    "type": "auto",
                    "target": "body",
                    "checker": "word_count",
                    "params": {"min": 20},
                    "description": "正文至少 20 字",
                },
                {
                    "id": "unknown",
                    "type": "auto",
                    "target": "document",
                    "checker": "invented.checker",
                    "description": "未知检查器不得自动执行",
                },
            ],
        }, ensure_ascii=False)

    candidate = parse_spec_document(
            "正文规范".encode(),
        "spec.txt",
        candidate_id="speccand-3",
        spec_set_id="spec-3",
        ai_completion=completion,
    )

    assert len(calls) == 1
    assert candidate.extraction_mode == "ai"
    assert candidate.rules[0].kind == "auto"
    assert candidate.rules[0].checker == "section.word_count"
    assert candidate.rules[1].kind == "manual"
    assert candidate.rules[1].checker == ""


def test_conflicting_auto_rules_are_blocked_before_confirmation() -> None:
    candidate = SpecParseCandidate(
        candidate_id="speccand-4",
        spec_set_id="spec-4",
        name="冲突规范",
        rules=[
            SpecRule(id="rule-a", target="body", checker="section.word_count", params={"min": 100}),
            SpecRule(id="rule-b", target="body", checker="section.word_count", params={"min": 200}),
        ],
    )

    checked = validate_spec_candidate(candidate)
    assert {issue.code for issue in checked.issues} == {"conflicting_rules"}
    with pytest.raises(SpecCandidateError):
        confirm_spec_candidate(checked, confirmed_by="user-admin", confirmed_at="2026-09-12T00:00:00+00:00")


def test_spec_parse_and_confirm_api_is_authenticated_and_review_only() -> None:
    client = TestClient(app)
    content = MARKDOWN_SPEC.encode()
    unauthenticated = client.post(
        "/api/specs/parse",
        files={"file": ("lab-spec.md", content, "text/markdown")},
    )
    assert unauthenticated.status_code == 401

    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    parsed = client.post(
        "/api/specs/parse",
        headers=headers,
        files={"file": ("lab-spec.md", content, "text/markdown")},
    )
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    assert body["status"] == "needs_confirmation"
    assert body["extraction_mode"] == "deterministic"

    confirmed = client.post("/api/specs/parse/confirm", headers=headers, json=body)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_by"] == "user-admin"

    audit = client.get(
        "/api/audit",
        headers=headers,
        params={"resource_type": "spec_candidate", "actor_id": "user-admin"},
    )
    assert audit.status_code == 200, audit.text
    actions = {event["action"] for event in audit.json() if event["resource_id"] == body["candidate_id"]}
    assert {"spec.candidate.parsed", "spec.candidate.confirmed"}.issubset(actions)
    checksum = hashlib.sha256(content).hexdigest()
    assert checksum not in " ".join(str(event) for event in audit.json())
