"""模板源文件存储: 留档导入的 docx, 供导出时作为排版基底。

解析出的 TextStyle 只是近似值; 以原始 docx 为基底导出, 才能完整继承
styles.xml / 页眉页脚 / 页边距 / 主题字体等包内资源。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from app.db import new_id
from app.services.docx_package_fixer import strip_comments_from_docx

# 只接受纯文件名(无路径分隔符), 防止路径穿越
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.docx$")

_DEFAULT_SOURCE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "templates"


def template_source_dir() -> Path:
    """模板源文件目录 (默认 backend/data/templates, 可用 PAPERGUARD_TEMPLATE_DIR 覆盖)。"""
    base = Path(os.environ.get("PAPERGUARD_TEMPLATE_DIR", str(_DEFAULT_SOURCE_DIR)))
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_safe_source_name(name: str) -> bool:
    return bool(name) and bool(_SAFE_NAME_RE.match(name))


def save_template_source(data: bytes) -> str:
    """保存模板 docx (去掉批注), 返回文件名。

    去批注失败时回退保存原始字节, 不影响导入流程。
    """
    name = f"{new_id('tplsrc')}.docx"
    target = template_source_dir() / name
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-tplsrc-"))
    raw = tmp_dir / "raw.docx"
    try:
        raw.write_bytes(data)
        try:
            strip_comments_from_docx(raw, target)
        except Exception:
            shutil.copyfile(raw, target)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return name


def resolve_template_source(name: str) -> Path | None:
    """把模板记录里的文件名解析为存在的绝对路径; 非法或缺失返回 None。"""
    if not is_safe_source_name(name):
        return None
    base = template_source_dir().resolve()
    path = (base / name).resolve()
    return path if path.parent == base and path.is_file() else None


def delete_template_source(name: str) -> None:
    path = resolve_template_source(name)
    if path is not None:
        path.unlink(missing_ok=True)


__all__ = [
    "delete_template_source",
    "is_safe_source_name",
    "resolve_template_source",
    "save_template_source",
    "template_source_dir",
]
