"""Click 8.x CLI group — Sprint 3.

5 子命令：check / fix / extract / init / validate-pack.

注：避免循环 import — 5 个子命令模块直接挂到 cli group 上，
不经过 app.cli.__init__ 中转（__init__.py 只导出 cli，避免重入）。
"""

from __future__ import annotations

import click


# 定义 click group（顶级）
@click.group()
@click.version_option("0.1.0", prog_name="paperguard")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="显示详细进度")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """PaperGuard — 论文格式规范检查与修复工具."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# 必须在 cli 定义后 import + 注册子命令（避免循环）
from app.cli.check import check  # noqa: E402
from app.cli.fix import fix  # noqa: E402
from app.cli.extract import extract  # noqa: E402
from app.cli.init import init  # noqa: E402
from app.cli.validate_pack import validate_pack  # noqa: E402

cli.add_command(check)
cli.add_command(fix)
cli.add_command(extract)
cli.add_command(init)
cli.add_command(validate_pack)


__all__ = ["cli"]
