"""paperguard init — Sprint 3 commit #14 完整实现.

生成空白 pack 骨架，方便学校从基础规则包 fork 定制。
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml


@click.command()
@click.option("--school", "-s", default=None, help="学校名称（生成学校样式 pack）")
@click.option("--base", "-b", default="GB-T-7713-1-2006", help="基础规则包 ID")
@click.option("--out", "-o", required=True,
              type=click.Path(dir_okay=False, path_type=str),
              help="输出 .yaml 路径")
def init(school: str | None, base: str, out: str) -> None:
    """生成空白 pack 骨架, 可直接编辑后用于 paperguard check --pack."""
    try:
        # pack_id: SCHOOL-<name> or custom-pack
        if school:
            pack_id = f"SCHOOL-{school.upper().replace(' ', '-')}"
            name = f"{school} 论文格式规范"
        else:
            pack_id = "custom-pack"
            name = "Custom Pack"
        # 骨架: rules 空列表, base 信息在 description
        skeleton = {
            "id": pack_id,
            "name": name,
            "version": "0.1.0",
            "description": f"基于 {base} 的自定义 pack 骨架（用 paperguard extract <template.docx> --out 自动生成完整规则）",
            "extends": base,
            "rules": [],
        }
        Path(out).write_text(
            yaml.dump(skeleton, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        click.echo(f"Skeleton written to {out} (pack_id={pack_id})")
        click.echo("下一步: paperguard extract <template.docx> --out 生成的 YAML 内容追加到本文件")
        sys.exit(0)
    except Exception as exc:
        click.echo(f"Error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(2)


__all__ = ["init"]
