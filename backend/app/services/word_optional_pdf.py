"""On-demand PDF copies of checked Word artifacts using local Microsoft Word."""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

from app.services.source_template_safety import audit_generated_package
from app.services.word_native_layout import (
    WordNativeLayoutError,
    _cleanup_word_temporary_document,
    _word_workspace,
)


class WordPdfError(RuntimeError):
    """The optional PDF could not be produced or verified."""


_SCRIPT = r'''
on run argv
    set inputPath to item 1 of argv
    set outputPath to item 2 of argv
    with timeout of 100 seconds
        tell application "Microsoft Word"
            if (count documents) is not 0 then error "PAPERGUARD_WORD_BUSY"
            try
                open (POSIX file inputPath) read only true add to recent files false
                -- Word may expose a temporary display name rather than the file name.
                -- Resolve the document that was just opened, then always clean it up
                -- through the active-document handle in both success and failure paths.
                delay 1
                set docRef to active document
                if (posix full name of docRef) is not inputPath then error "PAPERGUARD_WORD_PATH"
                set wordVersion to version as text
                repaginate docRef
                set firstPages to compute statistics docRef statistic statistic pages
                repaginate docRef
                set secondPages to compute statistics docRef statistic statistic pages
                if firstPages is not secondPages then error "PAPERGUARD_WORD_UNSTABLE"
                save as docRef file name outputPath file format format PDF
                close docRef saving no
                return wordVersion & "|" & firstPages & "|" & secondPages
            on error errorText number errorNumber
                try
                    if (count documents) is not 0 then
                        set closeRef to active document
                        if (posix full name of closeRef) is inputPath then close active document saving no
                    end if
                end try
                error errorText number errorNumber
            end try
        end tell
    end timeout
end run
'''

def _cleanup_pdf_temporary_document(source: Path) -> bool:
    """Use the same path-scoped cleanup as the native layout worker."""
    return _cleanup_word_temporary_document(source)


def word_pdf_available() -> bool:
    """The first adapter is for a locally installed Mac Word, not Linux Office."""
    enabled = os.environ.get("PAPERGUARD_WORD_PDF_ENABLED", "true").strip().lower()
    return (enabled in {"1", "true", "yes", "on", "enabled"}
            and sys.platform == "darwin"
            and Path("/Applications/Microsoft Word.app").is_dir()
            and (Path.home() / "Library/Containers/com.microsoft.Word/Data/Documents").is_dir())


def _run_word_pdf(source: Path, destination: Path, timeout: int) -> tuple[str, int]:
    if sys.platform != "darwin":
        raise WordPdfError("本机 Word PDF 转换组件不可用")
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-", str(source), str(destination), source.name],
            input=_SCRIPT, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleaned = _cleanup_pdf_temporary_document(source)
        message = ("PDF 生成超时，请稍后重试" if cleaned else
                   "PDF 生成超时，临时文档状态暂未确认，请联系系统团队处理")
        raise WordPdfError(message) from exc
    except OSError as exc:
        raise WordPdfError("本机 Word PDF 转换组件不可用") from exc
    if result.returncode:
        if "PAPERGUARD_WORD_BUSY" in result.stderr:
            raise WordPdfError("Word 正在使用其他文档，请关闭后重试 PDF 生成")
        _cleanup_pdf_temporary_document(source)
        raise WordPdfError("Word 未能生成 PDF，请稍后重试")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+){1,3})\|([1-9][0-9]*)\|([1-9][0-9]*)\s*", result.stdout)
    if not match or match.group(2) != match.group(3):
        raise WordPdfError("Word 分页结果不稳定，未生成可交付 PDF")
    return match.group(1), int(match.group(2))


def create_optional_pdf(source: bytes, *, word_documents_dir: Path | None = None,
                        timeout: int = 110) -> tuple[bytes, dict]:
    """Generate a PDF from exactly these bytes without changing the Word artifact."""
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be between 1 and 120 seconds")
    audit_generated_package(source)
    try:
        with _word_workspace(word_documents_dir) as directory:
            copy = directory / "paperguard-input.docx"
            output = directory / "paperguard-output.pdf"
            fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as target:
                target.write(source)
            version, word_pages = _run_word_pdf(copy, output, timeout)
            if copy.read_bytes() != source:
                raise WordPdfError("PDF 生成期间 Word 文件发生变化")
            if not output.is_file() or output.stat().st_size > 100 * 1024 * 1024:
                raise WordPdfError("PDF 未生成或超过大小限制")
            pdf = output.read_bytes()
    except (WordNativeLayoutError, OSError) as exc:
        raise WordPdfError("PDF 生成组件暂不可用，请稍后重试") from exc
    try:
        if not pdf.startswith(b"%PDF-"):
            raise ValueError("invalid PDF signature")
        reader = PdfReader(io.BytesIO(pdf))
        if reader.is_encrypted or not 0 < len(reader.pages) <= 500 or len(reader.pages) != word_pages:
            raise ValueError("PDF page count differs from Word")
    except Exception as exc:
        raise WordPdfError("PDF 未通过完整性或分页检查") from exc
    return pdf, {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "word_version": version,
        "page_count": word_pages,
    }
