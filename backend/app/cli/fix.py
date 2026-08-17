"""paperguard fix — Sprint 3 commit #14 完整实现.

应用 fix 写新 docx（不破坏原文）；默认调 _fix_then_check 做修复后 audit。
"""

from __future__ import annotations

import sys

import click

from app.core.engine import validate_docx
from app.core.loaders.docx_loader import load_docx
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes
from app.cli._fix_then_check import fix_then_check


@click.command()
@click.argument("paper", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--pack", "-p", default="GB-T-7713-1-2006",
              help="规则包 ID")
@click.option("--output", "-o", required=True,
              type=click.Path(dir_okay=False, path_type=str),
              help="输出 .docx 路径（不覆盖原文件）")
@click.option("--skip-verify", is_flag=True, default=False,
              help="修复后不自动 check")
@click.option("--no-verify", is_flag=True, default=False, hidden=True)
def fix(paper: str, pack: str, output: str, skip_verify: bool, no_verify: bool) -> None:
    """修复 PAPER 的格式问题, 输出到 OUTPUT (不破坏原文)."""
    try:
        ir = load_docx(paper)
        findings = validate_docx(ir, pack=pack)
        if not findings:
            click.echo("No issues found. Nothing to fix.")
            sys.exit(0)
        fixed_ir, remaining = apply_all_fixes(ir, findings)
        write_ir_to_docx(fixed_ir, output)
        click.echo(f"Fixed and written to {output}")
        verify_remaining = remaining
        if not (skip_verify or no_verify):
            verify_remaining = fix_then_check(
                source_path=paper,
                output_path=output,
                pack=pack,
                pre_findings=findings,
                skipped_findings=remaining,
            )
        # 退出码: 0 = 全部修复; 1 = 仍有 remaining
        sys.exit(0 if not verify_remaining else 1)
    except Exception as exc:
        click.echo(f"Error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(2)


__all__ = ["fix"]
