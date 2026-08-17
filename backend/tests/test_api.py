"""PaperGuard Web API 端到端测试。

用 FastAPI TestClient 验证:
- /api/health
- /api/packs + /api/packs/{id}
- /api/check  (上传 docx → 结构化报告)
- /api/fix    (上传 docx → 修复后的新 docx)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

client = TestClient(app)


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
    # 返回内容必须是合法的 docx (zip 魔数)
    assert resp.content[:2] == b"PK"
