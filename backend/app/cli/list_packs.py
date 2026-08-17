"""paperguard list-packs — list installed built-in rule packs."""

from __future__ import annotations

import json
from pathlib import Path

import click

from app.core.engine.loader import list_packs


_PACKS_DIR = Path(__file__).parent.parent / "core" / "rules" / "packs"


@click.command("list-packs")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="输出格式（默认 text）",
)
def list_packs_command(fmt: str) -> None:
    """列出已安装的内置规则包."""
    pack_ids = list_packs(_PACKS_DIR)
    rows = [
        {"id": pack_id, "path": str(_PACKS_DIR / f"{pack_id}.yaml")}
        for pack_id in pack_ids
    ]
    if fmt == "json":
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    click.echo("Installed rule packs:")
    for row in rows:
        click.echo(f"- {row['id']}  {row['path']}")


__all__ = ["list_packs_command"]
