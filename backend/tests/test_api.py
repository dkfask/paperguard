"""PaperGuard Web API 端到端测试。

用 FastAPI TestClient 验证:
- /api/health
- /api/packs + /api/packs/{id}
- /api/check  (上传 docx → 结构化报告)
- /api/fix    (上传 docx → 修复后的新 docx)
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from docx import Document as DocxDocument
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from tests._helpers import register_fixture_response

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

client = TestClient(app)
_admin_login = client.post(
    "/api/auth/login",
    json={"username": "admin", "password": "TestAdminPassword123"},
)
assert _admin_login.status_code == 200, _admin_login.text
client.headers.update({"Authorization": f"Bearer {_admin_login.json()['token']}"})


@pytest.fixture(scope="module")
def sample_paper() -> bytes:
    return (FIXTURES / "sample_paper.docx").read_bytes()


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.headers["x-request-id"].startswith("req-")
    assert len(resp.headers["x-request-id"]) == 20


def test_health_probes():
    live = client.get("/api/health/live")
    ready = client.get("/api/health/ready")
    assert live.status_code == 200
    assert live.json() == {"status": "ok", "service": "paperguard", "probe": "live"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "service": "paperguard", "probe": "ready"}
    assert live.headers["x-request-id"].startswith("req-")
    assert ready.headers["x-request-id"].startswith("req-")


def test_authenticated_request_logs_correlated_context_without_sensitive_content(caplog):
    caplog.set_level(logging.INFO, logger="paperguard.http")
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == "paperguard.http"]
    completed = next(item for item in events if item["event"] == "http.request.completed" and item["request_id"] == request_id)
    assert completed["actor_id"] == "user-admin"
    assert completed["role"] == "admin"  # legacy logging metadata, not a platform grant
    assert completed["authorization_kind"] == "unresolved"
    assert completed["path"] == "/api/auth/me"
    serialized = " ".join(record.getMessage() for record in caplog.records)
    assert "Bearer " not in serialized and "TestAdminPassword123" not in serialized


def test_unauthenticated_request_completion_log_keeps_request_id(caplog):
    caplog.set_level(logging.INFO, logger="paperguard.http")
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401
    request_id = response.headers["x-request-id"]
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == "paperguard.http"]
    completed = next(item for item in events if item["event"] == "http.request.completed" and item["request_id"] == request_id)
    assert completed["actor_id"] == ""
    assert completed["role"] == ""


def test_list_packs():
    resp = client.get("/api/packs")
    assert resp.status_code == 200
    packs = resp.json()
    assert "GB-T-7713-1-2006" in packs
    assert "GB-T-7714-1-2015" in packs
    assert "SIM-UNIV-generic" in packs


def test_pack_detail():
    resp = client.get("/api/packs/GB-T-7713-1-2006")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "GB-T-7713-1-2006"
    assert data["rule_count"] > 0
    assert data["rules"][0]["id"].startswith("PG-")


def test_pack_detail_404():
    resp = client.get("/api/packs/not-exist")
    assert resp.status_code == 404
    assert resp.headers["x-request-id"].startswith("req-")


def test_logout_revokes_token():
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "TestAdminPassword123"},
    )
    token = "Bearer " + login.json()["token"]
    resp = client.post("/api/auth/logout", headers={"Authorization": token})
    assert resp.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": token}).status_code == 401


def test_user_registration_roles_and_admin_management():
    # 1. 学生注册：默认为 student 且 active
    st_res = register_fixture_response(client, "student_alice", "AlicePassword123", role="student", display_name="爱丽丝同学")
    st_data = st_res.json()
    assert st_data["role"] == "student"
    assert st_data["status"] == "active"

    # 2. 教师注册：申请为 teacher，初始状态为 pending
    tc_res = register_fixture_response(client, "teacher_bob", "BobPassword123", role="teacher", display_name="鲍勃老师")
    tc_data = tc_res.json()
    assert tc_data["role"] == "teacher"
    assert tc_data["status"] == "pending"

    # 待认证教师登录
    tc_login = client.post(
        "/api/auth/login",
        json={"username": "teacher_bob", "password": "BobPassword123"},
    )
    assert tc_login.status_code == 200
    tc_token = "Bearer " + tc_login.json()["token"]
    tc_me = client.get("/api/auth/me", headers={"Authorization": tc_token}).json()
    assert tc_me["status"] == "pending"

    # 未认证的教师尝试创建模板应被拦截 (403)
    blocked_tpl = client.post(
        "/api/templates",
        headers={"Authorization": tc_token},
        json={"id": "tpl-blocked", "name": "未认证拦截测试", "styles": {}, "sections": []},
    )
    assert blocked_tpl.status_code == 403
    assert "认证审核中" in blocked_tpl.json()["detail"]

    # 非管理员无法访问管理用户列表与审核
    assert client.get("/api/auth/admin/users", headers={"Authorization": tc_token}).status_code == 403

    # 3. 管理员列出全站用户并完成审核
    admin_users = client.get("/api/auth/admin/users").json()
    bob_record = next(u for u in admin_users if u["username"] == "teacher_bob")
    assert bob_record["status"] == "pending"

    # 管理员通过鲍勃老师的认证
    approve_res = client.put(
        f"/api/auth/admin/users/{bob_record['id']}/role-status",
        json={"status": "active"},
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "active"

    # 鲍勃老师此时已获得管理权限，可以创建/管理模板
    ok_tpl = client.post(
        "/api/templates",
        headers={"Authorization": tc_token},
        json={
            "id": "tpl-bob-approved",
            "name": "鲍勃老师专属模板",
            "styles": {},
            "sections": [{"key": "cover", "type": "cover", "title": "封面"}],
        },
    )
    assert ok_tpl.status_code == 201

    # 4. 管理员也可以直接将教师提升为管理员角色 (实现管理员可以是教师)
    promote_res = client.put(
        f"/api/auth/admin/users/{bob_record['id']}/role-status",
        json={"role": "admin"},
    )
    assert promote_res.status_code == 200
    assert promote_res.json()["role"] == "admin"

    # 鲍勃老师现在具备系统管理员权限，可以查询全站用户
    assert client.get("/api/auth/admin/users", headers={"Authorization": tc_token}).status_code == 200


def test_fix_rejects_filename_injection(sample_paper: bytes):
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        data={"filename": "../evil.docx"},
    )
    assert resp.status_code == 400


def test_check_template_requires_both_files(sample_paper: bytes):
    resp = client.post(
        "/api/check-template",
        files={"file": ("paper.docx", sample_paper, "application/octet-stream")},
    )
    assert resp.status_code == 422


def test_check_template_returns_context(sample_paper: bytes):
    template = (FIXTURES / "templates" / "gb7713-official.docx").read_bytes()
    resp = client.post(
        "/api/check-template",
        files={
            "file": ("paper.docx", sample_paper, "application/octet-stream"),
            "template": ("template.docx", template, "application/octet-stream"),
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["context"]["mode"] == "template"
    assert data["context"]["template_filename"] == "template.docx"
    assert data["summary"]["total"] == len(data["findings"])
    if data["findings"]:
        assert "actual" in data["findings"][0]
        assert "expected" in data["findings"][0]
def test_check_returns_report(sample_paper: bytes):
    resp = client.post(
        "/api/check",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        data={"pack": "GB-T-7713-1-2006"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool"] == "PaperGuard"
    assert data["summary"]["total"] == len(data["findings"])
    if data["findings"]:
        f = data["findings"][0]
        assert set(f) >= {"rule_id", "severity", "message", "location", "fix"}


def test_check_rejects_non_docx():
    resp = client.post(
        "/api/check",
        files={"file": ("paper.txt", b"not a docx", "text/plain")},
    )
    assert resp.status_code == 400


def test_check_unknown_pack_404(sample_paper: bytes):
    resp = client.post(
        "/api/check",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        data={"pack": "no-such-pack"},
    )
    assert resp.status_code == 404


def test_fix_returns_new_docx(sample_paper: bytes):
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        data={"pack": "GB-T-7713-1-2006", "filename": "fixed_paper.docx"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "fixed_paper.docx" in resp.headers["content-disposition"]
    assert resp.content[:2] == b"PK"
    DocxDocument(io.BytesIO(resp.content))


def test_fix_encodes_non_ascii_filename_with_ascii_fallback(sample_paper: bytes):
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        data={"filename": "王俞尹-4.7-fixed.docx"},
    )
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert 'filename="fixed.docx"' in disposition
    assert "filename*=UTF-8''%E7%8E%8B%E4%BF%9E%E5%B0%B9-4.7-fixed.docx" in disposition
    DocxDocument(io.BytesIO(resp.content))


def test_fix_uses_default_filename(sample_paper: bytes):
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
    )
    assert resp.status_code == 200
    assert 'filename="fixed.docx"' in resp.headers["content-disposition"]


def test_fix_rejects_non_docx():
    resp = client.post(
        "/api/fix",
        files={"file": ("paper.txt", b"not a docx", "text/plain")},
    )
    assert resp.status_code == 400


def test_fix_unknown_pack_404(sample_paper: bytes):
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        data={"pack": "no-such-pack"},
    )
    assert resp.status_code == 404


def test_fix_cleans_temp_dir_after_response(
    sample_paper: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    tmp_dir = tmp_path / "paperguard-success"

    def make_temp_dir(prefix: str) -> str:
        tmp_dir.mkdir()
        return str(tmp_dir)

    monkeypatch.setattr(routes.tempfile, "mkdtemp", make_temp_dir)
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
    )

    assert resp.status_code == 200
    assert not tmp_dir.exists()


def test_fix_cleans_temp_dir_when_processing_fails(
    sample_paper: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    tmp_dir = tmp_path / "paperguard-error"

    def make_temp_dir(prefix: str) -> str:
        tmp_dir.mkdir()
        return str(tmp_dir)

    def fail_load_docx(path: str):
        raise ValueError("broken document")

    monkeypatch.setattr(routes.tempfile, "mkdtemp", make_temp_dir)
    monkeypatch.setattr(routes, "load_docx", fail_load_docx)
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
    )

    assert resp.status_code == 422
    assert "修复失败" in resp.json()["detail"]
    assert not tmp_dir.exists()
