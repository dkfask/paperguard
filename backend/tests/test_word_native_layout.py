from __future__ import annotations

import hashlib
import io
import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from lxml import etree

from app.core.extract.xml_utils import qn
from app.services import word_native_layout
from app.services.source_template_profile import ProfileError
from app.services.source_template_safety import audit_generated_package, audit_source_package
from app.services.word_native_layout import WordNativeLayoutError


def _field(paragraph, name: str, cached: str, *, nested: bool = False):
    parent = paragraph._p
    if nested:
        run = etree.SubElement(parent, qn("w:r"))
        etree.SubElement(run, qn("w:fldChar")).set(qn("w:fldCharType"), "begin")
        run = etree.SubElement(parent, qn("w:r"))
        etree.SubElement(run, qn("w:instrText")).text = f" PAGEREF {name} \\h "
        run = etree.SubElement(parent, qn("w:r"))
        etree.SubElement(run, qn("w:fldChar")).set(qn("w:fldCharType"), "separate")
    field = etree.SubElement(parent, qn("w:fldSimple"))
    field.set(qn("w:instr"), f" PAGEREF {name} \\h ")
    etree.SubElement(etree.SubElement(field, qn("w:r")), qn("w:t")).text = cached
    if nested:
        run = etree.SubElement(parent, qn("w:r"))
        etree.SubElement(run, qn("w:fldChar")).set(qn("w:fldCharType"), "end")


def _docx(*, cached: str = "5", generated_tag: bool = False) -> bytes:
    doc = Document()
    _field(doc.add_paragraph("摘要"), "PGHabstract", "II", nested=True)
    _field(doc.add_paragraph("第一章"), "PGHchapter", cached)
    for number, name in enumerate(("PGHabstract", "PGHchapter"), 1):
        paragraph = doc.add_paragraph("标题")
        start = etree.SubElement(paragraph._p, qn("w:bookmarkStart"))
        start.set(qn("w:id"), str(number))
        start.set(qn("w:name"), name)
        etree.SubElement(paragraph._p, qn("w:bookmarkEnd")).set(qn("w:id"), str(number))
    if generated_tag:
        control = etree.SubElement(doc._body._body, qn("w:sdt"))
        props = etree.SubElement(control, qn("w:sdtPr"))
        etree.SubElement(props, qn("w:tag")).set(qn("w:val"), "pg-block:test")
    result = io.BytesIO()
    doc.save(result)
    return result.getvalue()


def _measurement(*, second_chapter: int = 5, include_chapter: bool = True) -> str:
    lines = ["V|16.113.3"]
    for number in (1, 2):
        lines.extend([f"T|{number}|12", f"P|{number}|PGHabstract|3|2"])
        if include_chapter:
            logical = 5 if number == 1 else second_chapter
            lines.append(f"P|{number}|PGHchapter|6|{logical}")
    return "\n".join(lines)


def _with_part(data: bytes, name: str, content: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr(name, content)
    return output.getvalue()


def _with_bookmarks(data: bytes, names: list[str]) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(result, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "word/document.xml":
                root = etree.fromstring(content)
                paragraph = root.find(".//" + qn("w:p"))
                for number, name in enumerate(names, 100):
                    start = etree.SubElement(paragraph, qn("w:bookmarkStart"))
                    start.set(qn("w:id"), str(number))
                    start.set(qn("w:name"), name)
                    etree.SubElement(paragraph, qn("w:bookmarkEnd")).set(qn("w:id"), str(number))
                content = etree.tostring(root)
            target.writestr(item, content)
    return result.getvalue()


def test_native_probe_compares_frozen_directory_with_two_word_pagination_passes(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    data = _docx(generated_tag=True)
    source.write_bytes(data)
    used = []

    def run_word(copy: Path, names: list[str], timeout: int) -> str:
        assert copy != source and copy.read_bytes() == data
        assert os.stat(copy).st_mode & 0o777 == 0o600
        assert names == ["PGHabstract", "PGHchapter"] and timeout == 90
        used.append(copy)
        return _measurement()

    monkeypatch.setattr(word_native_layout, "_run_word", run_word)
    report = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path)
    assert report["scope"] == "local_word_pagination_probe_only"
    assert report["page_count"] == 12 and report["toc_matches"] is True
    assert [row["logical_page"] for row in report["toc"]] == [2, 5]
    assert source.read_bytes() == data and not used[0].exists()


def test_native_probe_measures_extra_bookmarks_in_the_same_word_pass(tmp_path, monkeypatch):
    names = ["PGBbody", "PGEbody", "PGCfigure", "PGHchapter"]
    source = tmp_path / "source.docx"
    data = _with_bookmarks(_docx(), names[:-1])
    source.write_bytes(data)

    def run_word(copy, requested, timeout):
        assert copy.read_bytes() == data
        assert requested == ["PGHabstract", "PGHchapter", *names[:-1]]
        lines = ["V|16.113.3"]
        for number in (1, 2):
            lines.extend([f"T|{number}|12", f"P|{number}|PGHabstract|3|2",
                          f"P|{number}|PGHchapter|6|5", f"P|{number}|PGBbody|6|5",
                          f"P|{number}|PGEbody|7|6", f"P|{number}|PGCfigure|7|6"])
        return "\n".join(lines)

    monkeypatch.setattr(word_native_layout, "_run_word", run_word)
    report = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path,
                                                        extra_bookmarks=names)
    assert report["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert [(row["bookmark"], row["physical_page"], row["logical_page"])
            for row in report["extra_bookmarks"]] == [
                ("PGBbody", 6, 5), ("PGEbody", 7, 6), ("PGCfigure", 7, 6),
                ("PGHchapter", 6, 5),
            ]
    assert source.read_bytes() == data


@pytest.mark.parametrize("names,created", [
    (["PGBmissing"], []),
    (["PGBduplicate"], ["PGBduplicate", "PGBduplicate"]),
    (["PGBduplicate", "pgbduplicate"], ["PGBduplicate"]),
    (["bad bookmark"], ["bad bookmark"]),
])
def test_native_probe_rejects_missing_duplicate_or_invalid_content_bookmarks(tmp_path, monkeypatch, names, created):
    source = tmp_path / "source.docx"
    source.write_bytes(_with_bookmarks(_docx(), created))
    monkeypatch.setattr(word_native_layout, "_run_word",
                        lambda *_: pytest.fail("Invalid anchors must be rejected before Word is opened"))
    with pytest.raises(WordNativeLayoutError, match="书签"):
        word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path, extra_bookmarks=names)


def test_native_probe_supports_more_than_256_content_bookmarks(tmp_path, monkeypatch):
    names = [f"PGB{number:04}" for number in range(300)]
    source = tmp_path / "source.docx"
    source.write_bytes(_with_bookmarks(_docx(), names))

    def run_word(_copy, requested, _timeout):
        lines = ["V|16.113.3"]
        for number in (1, 2):
            lines.append(f"T|{number}|12")
            lines.extend(f"P|{number}|{name}|1|1" for name in requested)
        return "\n".join(lines)

    monkeypatch.setattr(word_native_layout, "_run_word", run_word)
    report = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path,
                                                        extra_bookmarks=names)
    assert len(report["extra_bookmarks"]) == 300


def test_native_probe_reports_incorrect_cached_page_without_changing_source(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(cached="6"))
    monkeypatch.setattr(word_native_layout, "_run_word", lambda *_: _measurement())
    report = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path)
    assert report["toc_matches"] is False
    assert [(row["cached_page"], row["logical_page"], row["matches"]) for row in report["toc"]] == [
        ("II", 2, True), ("6", 5, False),
    ]


@pytest.mark.parametrize("output", [_measurement(second_chapter=6), _measurement(include_chapter=False)])
def test_native_probe_rejects_unstable_or_incomplete_measurement(tmp_path, monkeypatch, output):
    source = tmp_path / "source.docx"
    source.write_bytes(_docx())
    monkeypatch.setattr(word_native_layout, "_run_word", lambda *_: output)
    with pytest.raises(WordNativeLayoutError, match="分页未收敛或书签定位不完整"):
        word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path)


def test_native_probe_rejects_source_changed_during_pagination(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(_docx())

    def mutate_source(*_):
        source.write_bytes(b"changed")
        return _measurement()

    monkeypatch.setattr(word_native_layout, "_run_word", mutate_source)
    with pytest.raises(WordNativeLayoutError, match="文件发生变化"):
        word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path)


@pytest.mark.parametrize("part", ["word/vbaProject.bin", "word/activeX/activeX1.bin", "word/embeddings/object1.bin"])
def test_generated_document_safety_rejects_executable_parts(part):
    with pytest.raises(ProfileError, match="宏或可执行嵌入对象"):
        audit_generated_package(_with_part(_docx(), part, b"unsafe"))


def test_generated_bindings_do_not_make_an_uploaded_master_admissible():
    data = _docx(generated_tag=True)
    audit_generated_package(data)
    with pytest.raises(ProfileError, match="保留的 PaperGuard 填充标记"):
        audit_source_package(data)


def test_native_word_timeout_does_not_return_a_measurement(monkeypatch, tmp_path):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(word_native_layout.subprocess, "run", timeout)
    with pytest.raises(WordNativeLayoutError, match="超时"):
        word_native_layout._run_word(tmp_path / "copy.docx", ["PGHchapter"], 1)


def test_native_timeout_cleans_its_word_copy_before_the_next_job(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    directory = tmp_path / "paperguard-word-test"
    directory.mkdir()
    source = directory / "paperguard-input.docx"
    events = []
    state = {"temporary_open": False, "attempts": 0}

    def run(args, **kwargs):
        if kwargs["input"] == word_native_layout._WORD_SCRIPT:
            events.append("measure")
            state["attempts"] += 1
            if state["attempts"] == 1:
                state["temporary_open"] = True
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            if state["temporary_open"]:
                return SimpleNamespace(returncode=1, stderr="PAPERGUARD_WORD_BUSY", stdout="")
            return SimpleNamespace(returncode=0, stderr="", stdout=_measurement())
        events.append("cleanup")
        assert args[-1] == str(source)
        assert kwargs["timeout"] <= 10
        state["temporary_open"] = False
        return SimpleNamespace(returncode=0, stderr="", stdout="CLOSED\n")

    monkeypatch.setattr(word_native_layout.subprocess, "run", run)
    with pytest.raises(WordNativeLayoutError, match="超时"):
        word_native_layout._run_word(source, ["PGHchapter"], 1)
    assert word_native_layout._run_word(source, ["PGHchapter"], 1) == _measurement()
    assert events == ["measure", "cleanup", "measure"]


def test_native_timeout_cleanup_failure_keeps_the_original_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    directory = tmp_path / "paperguard-word-test"
    directory.mkdir()
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["input"])
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(word_native_layout.subprocess, "run", run)
    with pytest.raises(WordNativeLayoutError, match="超时"):
        word_native_layout._run_word(directory / "paperguard-input.docx", ["PGHchapter"], 1)
    assert len(calls) == 2


def test_native_timeout_does_not_leave_the_optional_pdf_worker_busy(tmp_path, monkeypatch):
    from app.services import word_optional_pdf

    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    directory = tmp_path / "paperguard-word-test"
    directory.mkdir()
    source = directory / "paperguard-input.docx"
    state = {"temporary_open": False}

    def run(args, **kwargs):
        if kwargs["input"] == word_native_layout._WORD_SCRIPT:
            state["temporary_open"] = True
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if kwargs["input"] == word_optional_pdf._SCRIPT:
            if state["temporary_open"]:
                return SimpleNamespace(returncode=1, stderr="PAPERGUARD_WORD_BUSY", stdout="")
            return SimpleNamespace(returncode=0, stderr="", stdout="16.113.3|1|1")
        assert args[-1] == str(source)
        state["temporary_open"] = False
        return SimpleNamespace(returncode=0, stderr="", stdout="CLOSED\n")

    monkeypatch.setattr(word_native_layout.subprocess, "run", run)
    with pytest.raises(WordNativeLayoutError, match="超时"):
        word_native_layout._run_word(source, ["PGHchapter"], 1)
    assert word_optional_pdf._run_word_pdf(source, directory / "out.pdf", 1) == ("16.113.3", 1)


def test_native_measurement_failure_attempts_own_copy_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    source = tmp_path / "paperguard-word-test" / "paperguard-input.docx"
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["input"])
        if kwargs["input"] == word_native_layout._WORD_SCRIPT:
            return SimpleNamespace(returncode=1, stderr="word error", stdout="")
        assert args[-1] == str(source)
        return SimpleNamespace(returncode=0, stderr="", stdout="CLOSED\n")

    monkeypatch.setattr(word_native_layout.subprocess, "run", run)
    with pytest.raises(WordNativeLayoutError, match="未能完成分页"):
        word_native_layout._run_word(source, ["PGHchapter"], 1)
    assert calls == [word_native_layout._WORD_SCRIPT, word_native_layout._CLEANUP_SCRIPT]


@pytest.mark.parametrize(("stderr", "expected_code"), [
    ("private document /secret/path: Word failed (-1728)\n", "-1728"),
    ("private document /secret/path: Word failed (unrecognized)\n", "unknown"),
    ("private document /secret/path: Word failed (-9999)\n", "unknown"),
])
def test_native_measurement_failure_logs_only_safe_error_code(tmp_path, monkeypatch, caplog, stderr, expected_code):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    source = tmp_path / "paperguard-word-test" / "paperguard-input.docx"

    def run(args, **kwargs):
        if kwargs["input"] == word_native_layout._WORD_SCRIPT:
            return SimpleNamespace(returncode=1, stderr=stderr, stdout="")
        return SimpleNamespace(returncode=0, stderr="", stdout="CLOSED\n")

    monkeypatch.setattr(word_native_layout.subprocess, "run", run)
    with caplog.at_level("WARNING", logger="paperguard.word_layout"):
        with pytest.raises(WordNativeLayoutError, match="未能完成分页"):
            word_native_layout._run_word(source, ["PGHchapter"], 1)
    assert f"exit_code=1 apple_event_code={expected_code}" in caplog.text
    assert "private document" not in caplog.text
    assert "/secret/path" not in caplog.text
    assert str(source) not in caplog.text


def test_native_busy_does_not_try_to_close_an_existing_document(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["input"])
        return SimpleNamespace(returncode=1, stderr="PAPERGUARD_WORD_BUSY", stdout="")

    monkeypatch.setattr(word_native_layout.subprocess, "run", run)
    with pytest.raises(WordNativeLayoutError, match="其他文档"):
        word_native_layout._run_word(tmp_path / "paperguard-word-test" / "paperguard-input.docx", [], 1)
    assert calls == [word_native_layout._WORD_SCRIPT]


@pytest.mark.parametrize(("text", "expected"), [("IV", 4), ("iv", 4), ("5", 5), ("IIII", None), ("0", None)])
def test_page_values_accept_canonical_numbers_only(text, expected):
    assert word_native_layout._page_value(text) == expected


def test_update_cache_uses_word_measurement_and_preserves_numbering(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    original = _docx(cached="6")
    source.write_bytes(original)
    monkeypatch.setattr(word_native_layout, "_run_word", lambda *_: _measurement())
    measurement = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path)
    corrected = word_native_layout.update_word_directory_cache(original, measurement)
    assert source.read_bytes() == original
    assert word_native_layout._directory_references(corrected) == [("PGHabstract", "II"), ("PGHchapter", "5")]
    assert word_native_layout._formatted_page("iv", 9) == "ix"
    assert word_native_layout._formatted_page("IV", 9) == "IX"


def test_update_cache_rejects_evidence_for_another_document(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(cached="6"))
    monkeypatch.setattr(word_native_layout, "_run_word", lambda *_: _measurement())
    measurement = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path)
    with pytest.raises(WordNativeLayoutError, match="证据与目录文件不匹配"):
        word_native_layout.update_word_directory_cache(_docx(cached="7"), measurement)


def test_update_cache_rejects_mismatched_bookmark_evidence(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(cached="6"))
    monkeypatch.setattr(word_native_layout, "_run_word", lambda *_: _measurement())
    measurement = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path)
    measurement["toc"][1]["bookmark"] = "PGHabstract"
    with pytest.raises(WordNativeLayoutError, match="证据与目录字段不匹配"):
        word_native_layout.update_word_directory_cache(source.read_bytes(), measurement)


def test_word_toc_refresh_uses_private_copy_and_preserves_source(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    original, refreshed = _docx(cached="5"), _docx(cached="6")
    source.write_bytes(original)
    copies = []

    def refresh(copy, timeout):
        assert copy != source and copy.read_bytes() == original
        assert copy.stat().st_mode & 0o777 == 0o600 and timeout == 30
        copies.append(copy)
        copy.write_bytes(refreshed)

    monkeypatch.setattr(word_native_layout, "_run_word_toc", refresh)
    assert word_native_layout.refresh_word_toc(source, word_documents_dir=tmp_path, timeout=30) == refreshed
    assert source.read_bytes() == original and not copies[0].exists()


def test_word_toc_refresh_rejects_unsafe_input_before_opening_word(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(_with_part(_docx(), "word/vbaProject.bin", b"unsafe"))
    monkeypatch.setattr(word_native_layout, "_run_word_toc", lambda *_: pytest.fail("unsafe input reached Word"))
    with pytest.raises(ProfileError, match="宏或可执行嵌入对象"):
        word_native_layout.refresh_word_toc(source, word_documents_dir=tmp_path)


def test_word_toc_refresh_rejects_unsafe_result_and_preserves_source(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    original = _docx()
    source.write_bytes(original)

    def refresh(copy, _timeout):
        copy.write_bytes(_with_part(original, "word/embeddings/object1.bin", b"unsafe"))

    monkeypatch.setattr(word_native_layout, "_run_word_toc", refresh)
    with pytest.raises(ProfileError, match="宏或可执行嵌入对象"):
        word_native_layout.refresh_word_toc(source, word_documents_dir=tmp_path)
    assert source.read_bytes() == original


def test_word_toc_refresh_rejects_source_changes_and_symlink_results(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    original = _docx()
    source.write_bytes(original)

    def change_source(_copy, _timeout):
        source.write_bytes(_docx(cached="7"))

    monkeypatch.setattr(word_native_layout, "_run_word_toc", change_source)
    with pytest.raises(WordNativeLayoutError, match="原 Word 文件发生变化"):
        word_native_layout.refresh_word_toc(source, word_documents_dir=tmp_path)
    source.write_bytes(original)

    def replace_with_symlink(copy, _timeout):
        copy.unlink()
        copy.symlink_to(source)

    monkeypatch.setattr(word_native_layout, "_run_word_toc", replace_with_symlink)
    with pytest.raises(WordNativeLayoutError, match="受控的临时文件"):
        word_native_layout.refresh_word_toc(source, word_documents_dir=tmp_path)
    assert source.read_bytes() == original


def test_word_toc_timeout_cleans_only_its_temporary_document(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    copy = tmp_path / "paperguard-word-test" / "paperguard-input.docx"
    cleaned = []

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(word_native_layout.subprocess, "run", timeout)
    monkeypatch.setattr(word_native_layout, "_cleanup_word_temporary_document",
                        lambda path: cleaned.append(path) or True)
    with pytest.raises(WordNativeLayoutError, match="目录更新超时"):
        word_native_layout._run_word_toc(copy, 1)
    assert cleaned == [copy]


def test_word_toc_busy_does_not_close_someone_elses_document(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    monkeypatch.setattr(word_native_layout.subprocess, "run", lambda *_a, **_kw:
                        SimpleNamespace(returncode=1, stderr="PAPERGUARD_WORD_BUSY", stdout=""))
    monkeypatch.setattr(word_native_layout, "_cleanup_word_temporary_document",
                        lambda *_: pytest.fail("busy Word must not be cleaned up"))
    with pytest.raises(WordNativeLayoutError, match="Word 正在使用其他文档"):
        word_native_layout._run_word_toc(tmp_path / "copy.docx", 1)


def test_word_toc_rejects_non_unique_table_of_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    monkeypatch.setattr(word_native_layout.subprocess, "run", lambda *_a, **_kw:
                        SimpleNamespace(returncode=1, stderr="PAPERGUARD_TOC_COUNT", stdout=""))
    monkeypatch.setattr(word_native_layout, "_cleanup_word_temporary_document", lambda *_: True)
    with pytest.raises(WordNativeLayoutError, match="必须包含唯一目录"):
        word_native_layout._run_word_toc(tmp_path / "paperguard-word-test" / "copy.docx", 1)


def test_word_toc_rejects_path_binding_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")
    monkeypatch.setattr(word_native_layout.subprocess, "run", lambda *_a, **_kw:
                        SimpleNamespace(returncode=1, stderr="PAPERGUARD_WORD_PATH", stdout=""))
    monkeypatch.setattr(word_native_layout, "_cleanup_word_temporary_document", lambda *_: True)
    with pytest.raises(WordNativeLayoutError, match="绑定受控临时文档"):
        word_native_layout._run_word_toc(tmp_path / "paperguard-word-test" / "copy.docx", 1)


def _row_probe_docx():
    doc = Document(io.BytesIO(_docx()))
    table = doc.add_table(rows=3, cols=1)
    for index, row in enumerate(table.rows, 1):
        row.cells[0].text = f"Row {index}"
    for ident, name, paragraph in (
            (101, "PGBtable", table.rows[0].cells[0].paragraphs[0]),
            (102, "PGEtable", table.rows[-1].cells[0].paragraphs[0])):
        marker = etree.SubElement(paragraph._p, qn("w:bookmarkStart"))
        marker.set(qn("w:id"), str(ident))
        marker.set(qn("w:name"), name)
        etree.SubElement(paragraph._p, qn("w:bookmarkEnd")).set(qn("w:id"), str(ident))
    result = io.BytesIO()
    doc.save(result)
    return result.getvalue()


def _row_probe_output():
    lines = ["V|16.113.3"]
    for number in (1, 2):
        lines += [f"T|{number}|3", f"C|{number}|PGBtable|3",
                  f"R|{number}|PGBtable|1|2|2", f"R|{number}|PGBtable|2|2|2",
                  f"R|{number}|PGBtable|3|3|3"]
    return "\n".join(lines)


def _row_probe_targets():
    return [{"bookmark": "PGBtable", "end_bookmark": "PGEtable", "row_count": 3}]


def test_word_workspace_uses_owned_configured_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_WORD_DOCUMENTS_DIR", str(tmp_path))
    with word_native_layout._word_workspace() as directory:
        assert directory.parent == tmp_path.resolve()
        assert directory.name.startswith("paperguard-word-")
        assert directory.stat().st_uid == os.getuid()


def test_word_workspace_rejects_unowned_configured_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_WORD_DOCUMENTS_DIR", str(tmp_path / "missing"))
    with pytest.raises(WordNativeLayoutError, match="文档目录不可用"):
        with word_native_layout._word_workspace():
            pass


def test_word_workspace_rejects_symlinked_configured_directory(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("PAPERGUARD_WORD_DOCUMENTS_DIR", str(link))
    with pytest.raises(WordNativeLayoutError, match="文档目录不可用"):
        with word_native_layout._word_workspace():
            pass


def test_native_table_row_probe_binds_unchanged_source_and_double_pagination(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    data = _row_probe_docx()
    source.write_bytes(data)
    copies = []

    def run(copy, names, timeout):
        assert copy != source and copy.read_bytes() == data
        assert copy.stat().st_mode & 0o777 == 0o600
        assert names == ["PGBtable"] and timeout == 30
        copies.append(copy)
        return _row_probe_output()

    monkeypatch.setattr(word_native_layout, "_run_word_table_rows", run)
    result = word_native_layout.inspect_word_table_rows(source, _row_probe_targets(),
                                                        word_documents_dir=tmp_path, timeout=30)
    assert result["scope"] == "word_table_row_locations_only"
    assert result["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["page_count"] == 3 and result["pagination_stable"] is True
    assert result["tables"][0]["rows"] == [
        {"row": 1, "start_page": 2, "end_page": 2},
        {"row": 2, "start_page": 2, "end_page": 2},
        {"row": 3, "start_page": 3, "end_page": 3},
    ]
    assert source.read_bytes() == data and not copies[0].exists()


@pytest.mark.parametrize("damage", ["missing", "reordered", "unstable", "outside", "count", "duplicate"])
def test_native_table_row_probe_rejects_incomplete_or_inconsistent_word_measurements(damage):
    output = _row_probe_output()
    if damage == "missing":
        output = output.replace("R|2|PGBtable|2|2|2\n", "")
    elif damage == "reordered":
        output = output.replace("R|1|PGBtable|1|2|2\nR|1|PGBtable|2|2|2",
                                "R|1|PGBtable|2|2|2\nR|1|PGBtable|1|2|2")
    elif damage == "unstable":
        output = output.replace("R|2|PGBtable|3|3|3", "R|2|PGBtable|3|2|2")
    elif damage == "outside":
        output = output.replace("|3|3|3", "|3|4|4")
    elif damage == "count":
        output = output.replace("C|1|PGBtable|3", "C|1|PGBtable|2")
    else:
        output += "\nC|1|PGBtable|3"
    with pytest.raises(WordNativeLayoutError):
        word_native_layout._parse_table_measurement(output, _row_probe_targets())


def test_native_table_row_probe_rejects_wrong_table_boundaries_and_row_count(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(_row_probe_docx())
    monkeypatch.setattr(word_native_layout, "_run_word_table_rows", lambda *_: pytest.fail("unexpected Word call"))
    for targets in (
            [{"bookmark": "PGBtable", "end_bookmark": "PGEtable", "row_count": 2}],
            [{"bookmark": "PGEtable", "end_bookmark": "PGBtable", "row_count": 3}],
            [{"bookmark": "PGBtable", "end_bookmark": "PGEtable", "row_count": 3}] * 2):
        with pytest.raises(WordNativeLayoutError):
            word_native_layout.inspect_word_table_rows(source, targets, word_documents_dir=tmp_path)


@pytest.mark.parametrize("changed", ["copy", "source"])
def test_native_table_row_probe_rejects_file_changes_during_measurement(tmp_path, monkeypatch, changed):
    source = tmp_path / "source.docx"
    source.write_bytes(_row_probe_docx())

    def run(copy, _names, _timeout):
        (copy if changed == "copy" else source).write_bytes(b"changed")
        return _row_probe_output()

    monkeypatch.setattr(word_native_layout, "_run_word_table_rows", run)
    with pytest.raises(WordNativeLayoutError, match="发生变化"):
        word_native_layout.inspect_word_table_rows(source, _row_probe_targets(), word_documents_dir=tmp_path)


def test_native_table_row_timeout_cleans_only_the_controlled_copy(tmp_path, monkeypatch):
    copy = tmp_path / "copy.docx"
    cleaned = []
    monkeypatch.setattr(word_native_layout.sys, "platform", "darwin")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(word_native_layout.subprocess, "run", timeout)
    monkeypatch.setattr(word_native_layout, "_cleanup_word_temporary_document", lambda path: cleaned.append(path) or True)
    with pytest.raises(WordNativeLayoutError, match="超时"):
        word_native_layout._run_word_table_rows(copy, ["PGBtable"], 3)
    assert cleaned == [copy]


def test_native_table_row_probe_keeps_a_row_that_spans_pages():
    output = _row_probe_output()
    for number in (1, 2):
        output = output.replace(f"R|{number}|PGBtable|2|2|2", f"R|{number}|PGBtable|2|2|3")
    _, total, tables = word_native_layout._parse_table_measurement(output, _row_probe_targets())
    assert total == 3
    assert tables[0]['rows'][1] == {'row': 2, 'start_page': 2, 'end_page': 3}


def test_pagination_probe_allows_longer_calls_but_keeps_an_upper_bound(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(_docx())
    observed = []

    def run_word(_path, _names, timeout):
        observed.append(timeout)
        return _measurement()

    monkeypatch.setattr(word_native_layout, "_run_word", run_word)
    result = word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path, timeout=150)
    assert result["pagination_stable"] is True
    assert observed == [150]
    with pytest.raises(ValueError, match="180"):
        word_native_layout.inspect_word_pagination(source, word_documents_dir=tmp_path, timeout=181)
