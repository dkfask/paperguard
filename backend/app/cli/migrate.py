"""Idempotent database migration command backed by Store's schema ledger."""

from __future__ import annotations

import json
from pathlib import Path

import click

from app.db import SCHEMA_MIGRATIONS, Store


@click.command("migrate")
@click.option("--db-path", type=click.Path(path_type=Path), default=None,
              help="迁移指定 SQLite 文件；省略时使用 PAPERGUARD_DB_PATH。")
def migrate(db_path: Path | None) -> None:
    """Create or upgrade the database and print its applied migration ledger."""
    try:
        store = Store(db_path)
        applied = store.list_schema_migrations()
    except Exception as exc:
        raise click.ClickException(f"数据库迁移失败: {exc}") from exc
    versions = {item["version"] for item in applied}
    missing = [version for version in SCHEMA_MIGRATIONS if version not in versions]
    if missing:
        raise click.ClickException(f"迁移账本不完整: {', '.join(missing)}")
    click.echo(json.dumps({"status": "ok", "migrations": applied}, ensure_ascii=False))
