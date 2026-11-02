"""Private source files for preview-bound manuscript imports."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from app.services.document_assets import document_upload_dir

_SOURCE_NAME = re.compile(r"^candidate-[a-f0-9]{32}\.docx$")


def _candidate_dir() -> Path:
    path = document_upload_dir().parent / "import_candidates"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def candidate_path(name: str) -> Path:
    if not _SOURCE_NAME.fullmatch(name):
        raise ValueError("导入候选文件名无效")
    return _candidate_dir() / name


def write_candidate_source(name: str, data: bytes) -> Path:
    path = candidate_path(name)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def read_candidate_source(name: str, expected_sha256: str) -> bytes:
    try:
        data = candidate_path(name).read_bytes()
    except (OSError, ValueError) as exc:
        raise ValueError("导入候选源文件不可用，请重新上传并预览") from exc
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("导入候选源文件已变化，请重新上传并预览")
    return data
