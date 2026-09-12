"""CLI commands for portable PaperGuard backups and offline restore."""

from __future__ import annotations

import json
from pathlib import Path

import click

from app.services.backup_restore import BackupError, create_backup, restore_backup


@click.command("backup")
@click.argument("output", type=click.Path(path_type=Path))
def backup(output: Path) -> None:
    """Create a consistent database + data backup at OUTPUT."""
    try:
        manifest = create_backup(output)
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"output": str(output), "manifest": manifest}, ensure_ascii=False))


@click.command("restore")
@click.argument("backup_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--yes", is_flag=True, help="Confirm offline restore and retain rollback copies.")
def restore(backup_file: Path, yes: bool) -> None:
    """Restore BACKUP_FILE; run only while the PaperGuard service is stopped."""
    if not yes:
        raise click.UsageError("恢复会替换活动数据目录，请使用 --yes 明确确认，并先停止服务")
    try:
        result = restore_backup(backup_file)
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False))
