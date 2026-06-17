"""Pytest 共享配置和 fixture。

Sprint 6 引入：

- ``QT_QPA_PLATFORM=offscreen``：让 PyQt 测试在无显示器环境运行。
- ``qapp`` session 级 fixture：共享 :class:`QApplication` 单例。
- ``corrupt_docx_file`` / ``non_docx_file``：错误路径测试用的临时文件。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# offscreen 必须在 PyQt import 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """共享 :class:`QApplication` 单例（session 作用域）。"""
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def corrupt_docx_file(tmp_path: Path) -> Path:
    """生成一个损坏的 ``.docx`` 文件（写入非 zip 字节）。

    模拟用户打开的 docx 文件被损坏或被非 Office 程序覆盖后的状态。
    """
    path = tmp_path / "corrupt.docx"
    path.write_bytes(b"not a real docx, just plain text bytes")
    return path


@pytest.fixture
def non_docx_file(tmp_path: Path) -> Path:
    """生成一个非 ``.docx`` 文件（.txt）。"""
    path = tmp_path / "readme.txt"
    path.write_text("hello world", encoding="utf-8")
    return path


@pytest.fixture
def tmp_workdir():
    """带 cleanup 的临时工作目录（适合生成多文件的 error path 测试）。"""
    path = Path(tempfile.mkdtemp())
    yield path
    shutil.rmtree(path, ignore_errors=True)
