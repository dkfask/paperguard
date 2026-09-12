"""Controlled assets owned by a generic document project."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DEFAULT_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"
_UPLOAD_PREFIX = "/media/uploads/"
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Generic DOCX rendering is intentionally narrower than legacy image uploads.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif"}


def document_upload_dir() -> Path:
    """Resolve the configured local upload root at call time for tests and workers."""
    return Path(os.environ.get("PAPERGUARD_DATA_DIR", str(_DEFAULT_UPLOAD_DIR.parent))) / "uploads"


def resolve_document_image_path(project_id: str, url: str) -> Path | None:
    """Return an existing image path only when it belongs to the project."""
    if not isinstance(project_id, str) or not project_id or not isinstance(url, str):
        return None
    if not url.startswith(_UPLOAD_PREFIX):
        return None
    filename = url[len(_UPLOAD_PREFIX):]
    if Path(filename).name != filename or not _SAFE_FILENAME_RE.fullmatch(filename):
        return None
    if not filename.startswith(f"{project_id}-"):
        return None
    if Path(filename).suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    root = document_upload_dir().resolve()
    path = (root / filename).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


__all__ = ["document_upload_dir", "resolve_document_image_path"]
