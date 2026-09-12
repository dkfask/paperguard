"""Regression checks for the persisted runtime data root."""

from __future__ import annotations

from pathlib import Path

import app.main as main_api
from app import db
from app.services import backup_restore


def test_default_runtime_assets_share_backend_data_root() -> None:
    data_root = Path(__file__).resolve().parents[1] / "data"

    assert db._DEFAULT_DB_PATH == data_root / "paperguard.db"
    assert backup_restore._DEFAULT_DATA_DIR == data_root
    assert main_api._uploads_dir == data_root / "uploads"


def test_data_dir_override_controls_database_and_backup_paths(monkeypatch, tmp_path) -> None:
    data_root = tmp_path / "persistent-data"
    monkeypatch.delenv("PAPERGUARD_DB_PATH", raising=False)
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(data_root))

    assert db._default_db_path() == data_root / "paperguard.db"
    assert backup_restore.configured_data_dir() == data_root
    assert backup_restore.configured_db_path() == data_root / "paperguard.db"