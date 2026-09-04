"""PaperGuard Web API 端到端测试。

用 FastAPI TestClient 验证:
- /api/health
- /api/packs + /api/packs/{id}
- /api/check  (上传 docx → 结构化报告)
- /api/fix    (上传 docx → 修复后的新 docx)
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document as DocxDocument
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app

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


def test_logout_revokes_token():
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "TestAdminPassword123"},
    )
    token = "Bearer " + login.json()["token"]
    resp = client.post("/api/auth/logout", headers={"Authorization": token})
    assert resp.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": token}).status_code == 401


def test_fix_rejects_filename_injection(sample_paper: bytes):
    resp = client.post(
        "/api/fix",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        data={"filename": "../evil.docx"},
    )
    assert resp.status_code == 400



    resp = client.post(
        "/api/check",
        files={"file": ("sample_paper.docx", sample_paper, "application/octet-stream")},
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401


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
