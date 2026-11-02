from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from app.services import word_optional_pdf
from app.services.word_optional_pdf import WordPdfError, create_optional_pdf


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def source() -> bytes:
    return (Path(__file__).parent / "fixtures/templates/admissible.docx").read_bytes()


def test_optional_pdf_is_bound_to_exact_word_bytes_and_removed_from_temporary_workspace(source, tmp_path, monkeypatch):
    produced = _pdf()
    used = []

    def convert(copy, output, timeout):
        assert copy.read_bytes() == source
        assert copy.stat().st_mode & 0o777 == 0o600
        assert timeout == 110
        output.write_bytes(produced)
        used.extend([copy, output])
        return "16.113.3", 1

    monkeypatch.setattr(word_optional_pdf, "_run_word_pdf", convert)
    pdf, evidence = create_optional_pdf(source, word_documents_dir=tmp_path)
    assert pdf == produced
    assert evidence["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert evidence["pdf_sha256"] == hashlib.sha256(produced).hexdigest()
    assert evidence["word_version"] == "16.113.3" and evidence["page_count"] == 1
    assert all(not path.exists() for path in used)


@pytest.mark.parametrize("failure", ["changed-source", "invalid-pdf", "page-mismatch", "missing-pdf"])
def test_optional_pdf_rejects_conversion_or_consistency_failures(source, tmp_path, monkeypatch, failure):
    def convert(copy, output, _timeout):
        if failure == "changed-source":
            copy.write_bytes(b"changed")
        if failure != "missing-pdf":
            output.write_bytes(b"invalid" if failure == "invalid-pdf" else _pdf())
        return "16.113.3", 2 if failure == "page-mismatch" else 1

    monkeypatch.setattr(word_optional_pdf, "_run_word_pdf", convert)
    with pytest.raises(WordPdfError):
        create_optional_pdf(source, word_documents_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_optional_pdf_rejects_unsafe_word_before_starting_word(source, tmp_path, monkeypatch):
    monkeypatch.setattr(word_optional_pdf, "audit_generated_package", lambda _: (_ for _ in ()).throw(ValueError("unsafe")))
    monkeypatch.setattr(word_optional_pdf, "_run_word_pdf", lambda *_: pytest.fail("Word must not be invoked"))
    with pytest.raises(ValueError, match="unsafe"):
        create_optional_pdf(source, word_documents_dir=tmp_path)


def test_pdf_availability_is_explicitly_disableable(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_WORD_PDF_ENABLED", "false")
    assert word_optional_pdf.word_pdf_available() is False


def test_word_script_cleans_the_document_opened_by_word():
    # Word can expose a temporary display name after opening a path. Cleanup must
    # use the active document handle so a failed optional PDF request cannot leave
    # a hidden PaperGuard document that blocks the next request.
    assert "set docRef to active document" in word_optional_pdf._SCRIPT
    assert "close active document saving no" in word_optional_pdf._SCRIPT
    assert "set closeRef to document documentName" not in word_optional_pdf._SCRIPT


@pytest.mark.parametrize("outcome", ["busy", "unstable", "timeout"])
def test_word_failure_or_unstable_pagination_cannot_produce_a_pdf(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(word_optional_pdf.sys, "platform", "darwin")

    def run(*args, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        if outcome == "busy":
            return SimpleNamespace(returncode=1, stderr="PAPERGUARD_WORD_BUSY", stdout="")
        return SimpleNamespace(returncode=0, stderr="", stdout="16.113.3|2|3")

    monkeypatch.setattr(word_optional_pdf.subprocess, "run", run)
    with pytest.raises(WordPdfError):
        word_optional_pdf._run_word_pdf(tmp_path / "input.docx", tmp_path / "output.pdf", 1)


def test_pdf_timeout_cleans_its_word_copy_before_a_later_attempt(tmp_path, monkeypatch):
    """An abandoned optional conversion must not make the next Word job busy."""
    monkeypatch.setattr(word_optional_pdf.sys, "platform", "darwin")
    directory = tmp_path / "paperguard-word-test"
    directory.mkdir()
    source = directory / "paperguard-input.docx"
    destination = directory / "paperguard-output.pdf"
    events = []
    word = {"temporary_open": False, "attempts": 0}

    def run(args, **kwargs):
        if kwargs["input"] == word_optional_pdf._SCRIPT:
            events.append("convert")
            word["attempts"] += 1
            if word["attempts"] == 1:
                word["temporary_open"] = True
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            if word["temporary_open"]:
                return SimpleNamespace(returncode=1, stderr="PAPERGUARD_WORD_BUSY", stdout="")
            return SimpleNamespace(returncode=0, stderr="", stdout="16.113.3|1|1")
        events.append("cleanup")
        assert args[-1] == str(source)
        assert kwargs["timeout"] <= 10
        word["temporary_open"] = False
        return SimpleNamespace(returncode=0, stderr="", stdout="CLOSED\n")

    monkeypatch.setattr(word_optional_pdf.subprocess, "run", run)
    with pytest.raises(WordPdfError, match="超时"):
        word_optional_pdf._run_word_pdf(source, destination, 1)
    assert word_optional_pdf._run_word_pdf(source, destination, 1) == ("16.113.3", 1)
    assert events == ["convert", "cleanup", "convert"]


def test_pdf_timeout_cleanup_failure_does_not_hide_original_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(word_optional_pdf.sys, "platform", "darwin")
    directory = tmp_path / "paperguard-word-test"
    directory.mkdir()
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["input"])
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(word_optional_pdf.subprocess, "run", run)
    with pytest.raises(WordPdfError, match="超时"):
        word_optional_pdf._run_word_pdf(directory / "paperguard-input.docx", directory / "out.pdf", 1)
    assert len(calls) == 2


@pytest.mark.parametrize("path", ["ordinary.docx", "paperguard-input.docx", "paperguard-word-test/other.docx"])
def test_pdf_timeout_cleanup_refuses_paths_outside_its_temporary_copy(tmp_path, monkeypatch, path):
    monkeypatch.setattr(word_optional_pdf.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not touch Word"))
    assert word_optional_pdf._cleanup_pdf_temporary_document(tmp_path / path) is False
