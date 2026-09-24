"""Immutable project snapshots and attachment references for workflow handoff."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.models.project import WritingProject
from app.services.document_assets import document_upload_dir

_NON_CONTENT_FIELDS = {"revision", "updated_at", "check_run_ids"}


def project_snapshot(project: WritingProject) -> dict[str, Any]:
    """Return the complete model snapshot used by restore and submission history."""
    return project.model_dump(mode="json")


def _omit_empty_project_defaults(value: Any) -> Any:
    """Keep legacy project hashes stable when new model defaults are empty."""
    if isinstance(value, dict):
        return {
            key: _omit_empty_project_defaults(item)
            for key, item in value.items()
            if not (key == "inline_runs" and isinstance(item, list) and not item)
            and not (key in {"width_cm", "height_cm", "alignment"} and item is None)
        }
    if isinstance(value, list):
        return [_omit_empty_project_defaults(item) for item in value]
    return value


def project_content_hash(project_or_snapshot: WritingProject | dict[str, Any]) -> str:
    """Hash editable content and its policy/template basis, excluding run metadata."""
    if isinstance(project_or_snapshot, WritingProject):
        payload = project_or_snapshot.model_dump(mode="json")
    else:
        payload = dict(project_or_snapshot)
    payload = _omit_empty_project_defaults(payload)
    for key in _NON_CONTENT_FIELDS:
        payload.pop(key, None)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _attachment_path(filename: str) -> Path | None:
    clean = Path(str(filename or "")).name
    if not clean or clean != str(filename) or clean in {".", ".."}:
        return None
    upload_dir = document_upload_dir()
    root = upload_dir.resolve()
    path = (upload_dir / clean).resolve()
    if path.parent != root:
        return None
    return path


def project_attachment_refs(project: WritingProject | dict[str, Any]) -> list[dict[str, Any]]:
    """Collect stable image references without persisting server filesystem paths."""
    sections = project.sections if isinstance(project, WritingProject) else project.get("sections", [])
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in sections or []:
        paragraphs = section.paragraphs if not isinstance(section, dict) else section.get("paragraphs", [])
        for block in paragraphs or []:
            if isinstance(block, dict):
                block_type = block.get("type")
                image = block.get("image")
            else:
                block_type = getattr(block, "type", None)
                image = getattr(block, "image", None)
            block_type = getattr(block_type, "value", block_type)
            url = getattr(image, "url", "") if image is not None and not isinstance(image, dict) else (image or {}).get("url", "")
            if block_type != "image" or not url or not str(url).startswith("/media/uploads/"):
                continue
            filename = Path(str(url)).name
            if filename in seen:
                continue
            seen.add(filename)
            path = _attachment_path(filename)
            present = bool(path and path.is_file())
            ref: dict[str, Any] = {"url": str(url), "filename": filename, "present": present}
            if present and path:
                data = path.read_bytes()
                ref.update({"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
            else:
                ref.update({"sha256": "", "size_bytes": 0})
            refs.append(ref)
    return refs


def resolve_project_attachment(ref: dict[str, Any]) -> Path | None:
    """Resolve a stored reference under the controlled project upload directory."""
    return _attachment_path(ref.get("filename", ""))
