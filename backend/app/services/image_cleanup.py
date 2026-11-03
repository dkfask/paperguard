"""图片文件清理。

规则:
- 删除内容块时, 若该块是图片, 同步删除对应文件
- 删除项目时, 删除该项目所有图片文件
- 启动时清理孤儿文件(未被任何项目段落引用的上传文件)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.db import Store
from app.models.project import WritingProject
from app.services.document_assets import document_upload_dir

_ORPHAN_GRACE_SECONDS = 24 * 60 * 60
_UPLOAD_PREFIX = "/media/uploads/"


def _url_to_path(url: str) -> Path | None:
    """把 /media/uploads/xxx.png 解析为 uploads 目录内的路径。

    仅接受我们生成的 URL 格式, 防路径穿越。
    """
    name = url.rsplit("/", 1)[-1]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    return document_upload_dir() / name


def remove_image_file(url: str) -> bool:
    """删除单个图片文件, 不存在或非法 URL 返回 False。"""
    path = _url_to_path(url)
    if path is None:
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def project_image_urls(proj: WritingProject) -> set[str]:
    """收集项目所有内容块引用的图片 URL。"""
    urls: set[str] = set()
    for sec in proj.sections:
        for par in sec.paragraphs:
            if par.type.value == "image" and par.image and par.image.url:
                urls.add(par.image.url)
    return urls


def remove_removed_images(proj: WritingProject, new_paragraphs: list) -> int:
    """对比章节内容, 删除从段落中移除的图片文件。返回删除数量。"""
    old_urls = project_image_urls(proj)
    new_urls: set[str] = set()
    for par in new_paragraphs:
        if par.type.value == "image" and par.image and par.image.url:
            new_urls.add(par.image.url)
    removed = 0
    for url in old_urls - new_urls:
        if remove_image_file(url):
            removed += 1
    return removed


def remove_project_images(proj: WritingProject) -> int:
    """删除项目所有图片文件, 返回删除数量。"""
    removed = 0
    for url in project_image_urls(proj):
        if remove_image_file(url):
            removed += 1
    return removed


def _referenced_filenames(value: object, referenced: set[str]) -> None:
    """Collect upload references from all persisted shapes, including older snapshots."""
    if isinstance(value, dict):
        refs = value.get("attachment_refs")
        if refs is not None:
            if not isinstance(refs, list):
                raise ValueError("Invalid attachment reference list")
            for ref in refs:
                if not isinstance(ref, dict):
                    raise ValueError("Invalid attachment reference")
                filename = ref.get("filename")
                if filename is not None:
                    if not isinstance(filename, str) or not _valid_filename(filename):
                        raise ValueError("Invalid attachment filename")
                    referenced.add(filename)
                elif not isinstance(ref.get("url"), str) or not ref["url"].startswith(_UPLOAD_PREFIX):
                    raise ValueError("Attachment reference has no usable location")
        for item in value.values():
            _referenced_filenames(item, referenced)
    elif isinstance(value, list):
        for item in value:
            _referenced_filenames(item, referenced)
    elif isinstance(value, str) and value.startswith(_UPLOAD_PREFIX):
        filename = value[len(_UPLOAD_PREFIX):]
        if not _valid_filename(filename):
            raise ValueError("Invalid upload reference")
        referenced.add(filename)


def _valid_filename(filename: str) -> bool:
    return bool(filename and filename not in {".", ".."} and "/" not in filename and "\\" not in filename and ".." not in filename)


def cleanup_orphan_images(store: Store, grace_seconds: int = 0) -> int:
    """Remove uploads only after a complete, transactionally consistent reference scan."""
    upload_dir = document_upload_dir()
    if not upload_dir.exists():
        return 0

    try:
        # Lock saved project/version writes until the inventory and deletions finish.
        # The startup grace period also protects newly uploaded, not-yet-saved files.
        with store.workflow_transaction() as conn:
            referenced: set[str] = set()
            for payload in store.iter_upload_reference_payloads_conn(conn):
                _referenced_filenames(payload, referenced)

            removed = 0
            for path in upload_dir.iterdir():
                if path.is_symlink() or not path.is_file() or path.name in referenced:
                    continue
                try:
                    if grace_seconds and time.time() - path.stat().st_mtime < grace_seconds:
                        continue
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
            return removed
    except (ValueError, TypeError, json.JSONDecodeError):
        # A partial inventory must never be treated as proof that files are orphaned.
        return 0
