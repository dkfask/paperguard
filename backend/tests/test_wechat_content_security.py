"""UGC msgSecCheck 前置: 未配置跳过、命中 422 且不落库。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import wechat_content_security
from tests.test_workflow_api import _create_people, _login, _prepare_project

client = TestClient(app)


def test_inspect_skips_when_wechat_not_configured(monkeypatch):
    monkeypatch.setattr(wechat_content_security, "wechat_security_configured", lambda: False)
    result = wechat_content_security.inspect_ugc_text("请补充实验分析")
    assert result["status"] == "skipped"
    assert result["reason"] == "not_configured"


def test_inspect_skips_empty_text():
    assert wechat_content_security.inspect_ugc_text("  ")["status"] == "skipped"


def test_inspect_rejects_wechat_risky_text(monkeypatch):
    monkeypatch.setattr(wechat_content_security, "wechat_security_configured", lambda: True)
    monkeypatch.setattr(wechat_content_security, "fetch_access_token", lambda app_id, secret: "")

    def fake_post(url, params=None, json=None, timeout=10):
        assert url == "https://api.weixin.qq.com/wxa/msg_sec_check"
        assert json == {"content": "risky-ugc"}

        class Response:
            def json(self):
                return {"errcode": 87014, "errmsg": "risky content"}

        return Response()

    monkeypatch.setattr(wechat_content_security.httpx, "post", fake_post)
    result = wechat_content_security.inspect_ugc_text("risky-ugc")
    assert result == {"status": "rejected", "reason": "87014"}


def test_inspect_passes_safe_text(monkeypatch):
    monkeypatch.setattr(wechat_content_security, "wechat_security_configured", lambda: True)
    monkeypatch.setattr(wechat_content_security, "fetch_access_token", lambda app_id, secret: "")

    class Response:
        def json(self):
            return {"errcode": 0, "errmsg": "ok"}

    monkeypatch.setattr(wechat_content_security.httpx, "post", lambda *args, **kwargs: Response())
    result = wechat_content_security.inspect_ugc_text("请补充实验分析")
    assert result["status"] == "pass"


def test_assert_ugc_safe_raises_422_on_reject(monkeypatch):
    monkeypatch.setattr(
        wechat_content_security,
        "inspect_ugc_text",
        lambda content: {"status": "rejected", "reason": "87014"},
    )
    with pytest.raises(HTTPException) as exc:
        wechat_content_security.assert_ugc_safe("risky-ugc", field="comment")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == wechat_content_security.CONTENT_SECURITY_CODE


def test_comment_create_rejects_risky_text_without_persisting(monkeypatch):
    def fake_inspect(content: str) -> dict:
        if "risky-ugc" in str(content):
            return {"status": "rejected", "reason": "87014"}
        return {"status": "pass", "reason": "0"}

    monkeypatch.setattr(wechat_content_security, "inspect_ugc_text", fake_inspect)
    admin = _login("admin", "TestAdminPassword123")
    suffix = uuid.uuid4().hex[:8]
    organization = client.post("/api/organizations", headers=admin, json={
        "id": f"ugc-school-{suffix}", "name": "内容安全学院", "type": "school",
    })
    assert organization.status_code == 201, organization.text
    teacher, student, teacher_id, student_id = _create_people(admin)
    cohort = client.post("/api/cohorts", headers=admin, json={
        "name": "内容安全批次", "organization_id": organization.json()["id"], "academic_year": "2027",
    })
    assert cohort.status_code == 201, cohort.text
    assert client.post(f"/api/cohorts/{cohort.json()['id']}/members", headers=admin, json={
        "student_id": student_id, "teacher_id": teacher_id,
    }).status_code == 201
    now = datetime.now(timezone.utc)
    stage = client.post(f"/api/cohorts/{cohort.json()['id']}/stages", headers=admin, json={
        "key": "final", "name": "终稿", "open_at": (now - timedelta(minutes=1)).isoformat(),
        "deadline_at": (now + timedelta(days=1)).isoformat(), "is_final": True,
        "required_manual_checks": ["导师确认"],
    })
    assert stage.status_code == 201, stage.text
    assert client.post(f"/api/stages/{stage.json()['id']}/preview", headers=admin).json()["ok"] is True
    assert client.post(f"/api/stages/{stage.json()['id']}/publish", headers=admin).status_code == 200
    participant_id = client.get("/api/my-tasks", headers=student).json()[0]["id"]
    _prepare_project(student, participant_id)
    assert client.post(f"/api/stage-participants/{participant_id}/acknowledge", headers=student, json={}).status_code == 200
    candidate = client.post("/api/submission-candidates", headers=student, json={
        "participant_id": participant_id, "modification_note": "首次提交",
    })
    assert candidate.status_code == 201, candidate.text
    submitted = client.post("/api/submissions", headers=student, json={
        "candidate_id": candidate.json()["id"], "idempotency_key": f"ugc-{suffix}-1",
    })
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission"]["id"]

    blocked = client.post(
        f"/api/submissions/{submission_id}/comments",
        headers=teacher,
        json={"body": "risky-ugc", "severity": "info"},
    )
    assert blocked.status_code == 422, blocked.text
    detail = blocked.json()["detail"]
    assert detail["code"] == "CONTENT_SECURITY_REJECTED"

    listing = client.get(f"/api/submissions/{submission_id}", headers=student)
    assert listing.status_code == 200, listing.text
    comments = listing.json().get("comments") or []
    assert all("risky-ugc" not in str(item.get("body") or "") for item in comments)
