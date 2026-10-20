"""Pytest 共享配置和 fixture。

- ``QT_QPA_PLATFORM=offscreen``: 无显示器环境运行。
- 独立临时数据库: API 测试不污染真实 ``data/paperguard.db``。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

os.environ.setdefault("PAPERGUARD_ADMIN_PASSWORD", "TestAdminPassword123")
# Production requires an AEAD master key; tests use a deterministic non-secret fixture.
os.environ.setdefault("PAPERGUARD_SECRET_KEY", "0123456789abcdef" * 4)
# API 测试使用独立临时数据库
_TEST_DB_DIR = tempfile.mkdtemp(prefix="paperguard-test-db-")
os.environ["PAPERGUARD_DB_PATH"] = str(Path(_TEST_DB_DIR) / "test.db")
# 模板源文件也放临时目录, 不污染 backend/data/templates
os.environ["PAPERGUARD_TEMPLATE_DIR"] = str(Path(_TEST_DB_DIR) / "templates")
# Imported images and orphan cleanup must use the same disposable test root.
os.environ["PAPERGUARD_DATA_DIR"] = str(Path(_TEST_DB_DIR) / "data")


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


@pytest.fixture
def closed_role_session():
    """Model a session created before the pilot closed a role, in the test DB."""
    from app.auth import _issue_session
    from app.db import Store

    def existing_session(user_id, role="college_admin"):
        store = Store()
        user = store.get_user(user_id)
        store.update_user_role_and_status(user_id, "student", "active")
        session = _issue_session(store, store.get_user(user_id))
        store.update_user_role_and_status(user_id, role, user.get("status", "active"))
        return {"Authorization": "Bearer " + session["token"]}

    return existing_session
