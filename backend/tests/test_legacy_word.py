"""Legacy Word upload preparation tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.legacy_word import (
    LegacyWordConversionError,
    convert_doc_bytes,
    prepare_docx,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "minimal.docx"


def test_prepare_docx_keeps_modern_word_files_unchanged(tmp_path: Path) -> None:
    data = FIXTURE.read_bytes()

    prepared = prepare_docx(data, "report.docx", tmp_path)

    assert prepared.parser_filename == "report.docx"
    assert prepared.path.read_bytes() == data


def test_legacy_word_conversion_reports_missing_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.services.legacy_word._find_converter", lambda: None)

    with pytest.raises(LegacyWordConversionError, match="另存为 .docx"):
        convert_doc_bytes(b"legacy-word", "report.doc", tmp_path)


@pytest.mark.skipif(
    not shutil.which("soffice") and not shutil.which("libreoffice"),
    reason="LibreOffice is required for legacy .doc conversion",
)
def test_legacy_word_conversion_produces_parser_ready_docx(tmp_path: Path) -> None:
    converter = shutil.which("soffice") or shutil.which("libreoffice")
    source = tmp_path / "source.docx"
    source.write_bytes(FIXTURE.read_bytes())
    subprocess.run(
        [converter, "--headless", "--convert-to", "doc", "--outdir", str(tmp_path), str(source)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    legacy = tmp_path / "source.doc"
    prepared = prepare_docx(legacy.read_bytes(), "legacy.doc", tmp_path / "prepared")

    assert prepared.parser_filename == "legacy.docx"
    assert prepared.path.suffix == ".docx"
    assert prepared.path.read_bytes()[:2] == b"PK"


@pytest.mark.skipif(
    not shutil.which("soffice") and not shutil.which("libreoffice"),
    reason="LibreOffice is required for legacy .doc conversion",
)
def test_template_parse_api_accepts_a_legacy_doc(tmp_path: Path) -> None:
    converter = shutil.which("soffice") or shutil.which("libreoffice")
    source = tmp_path / "source.docx"
    source.write_bytes(FIXTURE.read_bytes())
    subprocess.run(
        [converter, "--headless", "--convert-to", "doc", "--outdir", str(tmp_path), str(source)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    assert login.status_code == 200, login.text
    response = client.post(
        "/api/templates/parse",
        headers={"Authorization": f"Bearer {login.json()['token']}"},
        files={"file": ("legacy.doc", (tmp_path / "source.doc").read_bytes(), "application/msword")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["filename"] == "legacy.doc"
    assert response.json()["source_asset"]["storage_key"].endswith(".docx")
