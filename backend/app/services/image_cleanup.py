"""图片文件清理。

规则:
- 删除内容块时, 若该块是图片, 同步删除对应文件
- 删除项目时, 删除该项目所有图片文件
- 启动时清理孤儿文件(未被任何项目段落引用的上传文件)
"""

from __future__ import annotations

import time
from pathlib import Path

from app.db import Store
from app.models.project import WritingProject

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
_ORPHAN_GRACE_SECONDS = 24 * 60 * 60


def _url_to_path(url: str) -> Path | None:
    """把 /media/uploads/xxx.png 解析为 uploads 目录内的路径。

    仅接受我们生成的 URL 格式, 防路径穿越。
    """
    name = url.rsplit("/", 1)[-1]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    return _UPLOAD_DIR / name


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


def cleanup_orphan_images(store: Store, grace_seconds: int = 0) -> int:
    """删除未被项目引用的图片; 启动清理可传入宽限期。"""
    if not _UPLOAD_DIR.exists():
        return 0

    referenced: set[str] = set()
    for proj in store.list_projects():
        referenced.update(project_image_urls(proj))

    removed = 0
    for path in _UPLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        url = f"/media/uploads/{path.name}"
        if url not in referenced:
            try:
                if grace_seconds and time.time() - path.stat().st_mtime < grace_seconds:
                    continue
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
