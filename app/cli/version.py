"""paperguard version — local version plus optional update check."""

from __future__ import annotations

import json
import urllib.request

import click

from app import __version__


@click.command()
@click.option("--check-update", is_flag=True, default=False,
              help="可选联网检查最新版本")
def version(check_update: bool) -> None:
    """显示 PaperGuard 版本."""
    click.echo(f"PaperGuard {__version__}")
    if check_update:
        click.echo(_update_status())


def _update_status() -> str:
    try:
        with urllib.request.urlopen(
            "https://pypi.org/pypi/paperguard/json",
            timeout=3,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest = payload.get("info", {}).get("version", "unknown")
        return f"Latest: {latest}"
    except Exception as exc:
        return f"Update check failed: {type(exc).__name__}"


__all__ = ["version"]
