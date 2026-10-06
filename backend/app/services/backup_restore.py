"""Consistent, portable PaperGuard data backups and offline restore."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


# The database and file assets must share the same persisted data root.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DEFAULT_DB_PATH = _DEFAULT_DATA_DIR / "paperguard.db"
_BACKUP_FORMAT_VERSION = 1


class BackupError(ValueError):
    """Raised when a backup is invalid or cannot be restored safely."""


def configured_data_dir() -> Path:
    """数据根解析。**必须与 `app.db.data_dir()` 完全同规则。**

    修复前本函数只读 `PAPERGUARD_DATA_DIR`，而 `db.data_dir()` 的优先级是
    `PAPERGUARD_DB_PATH` > `PAPERGUARD_DATA_DIR` > 推导值。当只设了
    `PAPERGUARD_DB_PATH` 而未设 `PAPERGUARD_DATA_DIR` 时，在线数据库位于
    `<DB_PATH 的父目录>`，而备份根仍解析到代码目录下的 `data/`
    ——**备份根不含在线数据库**（见 docs/tenancy/data-root-fragmentation.md DR-02）。

    直接复用 `db.data_dir()`，让两者不可能再分叉。惰性导入以保持本模块
    作为独立工具的零顶层依赖（`db` 不依赖本模块，无循环风险）。
    """
    from app.db import data_dir

    explicit = os.environ.get("PAPERGUARD_DATA_DIR", "").strip()
    return Path(explicit).expanduser().resolve() if explicit else data_dir()


def configured_db_path() -> Path:
    value = os.environ.get("PAPERGUARD_DB_PATH", "")
    if value:
        return Path(value)
    return configured_data_dir() / "paperguard.db"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "" in path.parts:
        raise BackupError(f"备份包含不安全路径: {name}")
    return path


def _snapshot_database(db_path: Path, output: Path) -> None:
    if not db_path.is_file():
        raise BackupError(f"数据库不存在: {db_path}")
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(output)
    try:
        source.execute("PRAGMA wal_checkpoint(FULL)")
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError("数据库快照完整性检查失败")
    finally:
        target.close()
        source.close()


def _file_manifest(path: Path, archive_name: str) -> dict[str, Any]:
    return {"name": archive_name, "sha256": _sha256(path), "size_bytes": path.stat().st_size}


def create_backup(output_path: Path | str, *, db_path: Path | str | None = None,
                  data_dir: Path | str | None = None) -> dict[str, Any]:
    """Create a ZIP backup with a SQLite snapshot and every persisted data asset."""
    output = Path(output_path).expanduser().resolve()
    database = Path(db_path) if db_path else configured_db_path()
    root = Path(data_dir) if data_dir else configured_data_dir()
    output.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    database = database.resolve()
    root = root.resolve()

    with tempfile.TemporaryDirectory(prefix="paperguard-backup-") as temp:
        snapshot = Path(temp) / "paperguard.db"
        _snapshot_database(database, snapshot)
        persisted: list[tuple[Path, str]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.resolve() == database or path.resolve() == output:
                continue
            relative = path.relative_to(root).as_posix()
            persisted.append((path, f"data/{relative}"))
        files = [_file_manifest(snapshot, "database/paperguard.db")]
        files.extend(_file_manifest(path, archive_name) for path, archive_name in persisted)
        manifest = {
            "format": "paperguard-backup",
            "format_version": _BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database": "database/paperguard.db",
            "data_root": "data/",
            "files": files,
        }
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(snapshot, "database/paperguard.db")
            bundle.writestr("data/", b"")
            for path, archive_name in persisted:
                bundle.write(path, archive_name)
            bundle.writestr("backup-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def _read_and_validate_backup(backup_path: Path) -> tuple[dict[str, Any], list[zipfile.ZipInfo]]:
    if not backup_path.is_file():
        raise BackupError(f"备份文件不存在: {backup_path}")
    try:
        bundle = zipfile.ZipFile(backup_path)
    except zipfile.BadZipFile as exc:
        raise BackupError("备份文件不是有效 ZIP") from exc
    with bundle:
        try:
            manifest = json.loads(bundle.read("backup-manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise BackupError("备份缺少有效 backup-manifest.json") from exc
        if manifest.get("format") != "paperguard-backup" or manifest.get("format_version") != _BACKUP_FORMAT_VERSION:
            raise BackupError("不支持的 PaperGuard 备份格式")
        infos = bundle.infolist()
        names = {info.filename for info in infos}
        for info in infos:
            _safe_member(info.filename)
            if info.is_dir() or info.filename == "backup-manifest.json":
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise BackupError(f"备份不允许包含符号链接: {info.filename}")
        entries = manifest.get("files")
        if not isinstance(entries, list) or not any(
            isinstance(entry, dict) and entry.get("name") == "database/paperguard.db"
            for entry in entries
        ):
            raise BackupError("备份清单必须包含数据库快照")
        for entry in entries:
            name = str(entry.get("name", ""))
            _safe_member(name)
            if name not in names:
                raise BackupError(f"备份清单缺少文件: {name}")
            info = bundle.getinfo(name)
            if info.file_size != int(entry.get("size_bytes", -1)):
                raise BackupError(f"备份文件大小不匹配: {name}")
            if hashlib.sha256(bundle.read(name)).hexdigest() != entry.get("sha256"):
                raise BackupError(f"备份文件摘要不匹配: {name}")
        if manifest.get("database") != "database/paperguard.db":
            raise BackupError("备份数据库路径无效")
        if manifest.get("data_root") != "data/":
            raise BackupError("备份数据目录路径无效")
    return manifest, infos


def restore_backup(backup_path: Path | str, *, db_path: Path | str | None = None,
                   data_dir: Path | str | None = None) -> dict[str, Any]:
    """Restore a validated backup, retaining rollback copies beside live paths."""
    backup = Path(backup_path).expanduser().resolve()
    database = (Path(db_path) if db_path else configured_db_path()).resolve()
    root = (Path(data_dir) if data_dir else configured_data_dir()).resolve()
    manifest, _ = _read_and_validate_backup(backup)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rollback_data = Path(f"{root}.pre-restore-{stamp}")
    rollback_db = Path(f"{database}.pre-restore-{stamp}")
    moved_data = False
    moved_db = False

    with tempfile.TemporaryDirectory(prefix="paperguard-restore-") as temp:
        staging = Path(temp)
        with zipfile.ZipFile(backup) as bundle:
            for info in bundle.infolist():
                if info.is_dir() or info.filename == "backup-manifest.json":
                    continue
                relative = _safe_member(info.filename)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(info.filename))
        staged_data = staging / "data"
        staged_db = staging / "database" / "paperguard.db"
        if not staged_db.is_file() or not staged_data.is_dir():
            raise BackupError("备份缺少数据库快照或数据目录")
        result = sqlite3.connect(staged_db).execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError("待恢复数据库完整性检查失败")
        try:
            if root.exists():
                os.replace(root, rollback_data)
                moved_data = True
            shutil.copytree(staged_data, root)
            if database.exists() and not (moved_data and database.is_relative_to(rollback_data)):
                database.parent.mkdir(parents=True, exist_ok=True)
                os.replace(database, rollback_db)
                moved_db = True
            database.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_db, database)
        except Exception:
            if database.exists() and moved_db:
                database.unlink()
            if moved_db and rollback_db.exists():
                os.replace(rollback_db, database)
            if root.exists():
                shutil.rmtree(root)
            if moved_data and rollback_data.exists():
                os.replace(rollback_data, root)
            raise
    return {
        "restored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest": manifest,
        "rollback_data": str(rollback_data) if moved_data else "",
        "rollback_db": str(rollback_db) if moved_db else "",
    }
