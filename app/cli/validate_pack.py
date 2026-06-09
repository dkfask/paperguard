"""paperguard validate-pack — Sprint 3 commit #14 完整实现.

校验 YAML pack 是否符合 PaperGuard 规则包 schema (pydantic).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from pydantic import ValidationError

from app.core.engine.loader import RulePackSchema


@click.command()
@click.argument("pack_file", type=click.Path(exists=True, dir_okay=False, path_type=str))
def validate_pack(pack_file: str) -> None:
    """校验 PACK_FILE 是否符合 PaperGuard 规则包 schema."""
    try:
        data = yaml.safe_load(Path(pack_file).read_text(encoding="utf-8"))
        RulePackSchema.model_validate(data)
        click.echo(f"Valid: {data.get('id', 'unknown')} ({len(data.get('rules', []))} rules)")
        sys.exit(0)
    except ValidationError as exc:
        click.echo(f"Schema validation failed:\n{exc}", err=True)
        sys.exit(2)
    except Exception as exc:
        click.echo(f"Error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(2)


__all__ = ["validate_pack"]
