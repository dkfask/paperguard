"""paperguard check — 跑 validate_docx, 输出 findings.

Click 8.x subcommand (Sprint 3).
"""

from __future__ import annotations

import sys

import click

from app.core.engine import validate_docx
from app.core.loaders.docx_loader import load_docx
from app.core.reporters.html_reporter import HTMLReporter
from app.core.reporters.json_reporter import JSONReporter
from app.core.reporters.sarif_reporter import SARIFReporter
from app.core.reporters.terminal import TerminalReporter


@click.command()
@click.argument("paper", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--pack", "-p", default="GB-T-7713-1-2006",
              help="规则包 ID（默认 GB-T-7713-1-2006）")
@click.option("--format", "-f", "fmt", type=click.Choice(["text", "json", "html", "sarif"]),
              default="text", help="输出格式（默认 text）")
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=str),
              default=None, help="输出到文件（默认 stdout）")
def check(paper: str, pack: str, fmt: str, output: str | None) -> None:
    """检查 PAPER 的格式规范问题.

    PAPER  .docx 文件路径
    """
    try:
        ir = load_docx(paper)
        findings = validate_docx(ir, pack=pack)
        if fmt == "json":
            result = JSONReporter().report(findings)
        elif fmt == "html":
            result = HTMLReporter().report(findings)
        elif fmt == "sarif":
            result = SARIFReporter().report(findings, artifact_uri=paper)
        else:
            result = TerminalReporter().report_findings(findings)
        if output:
            from pathlib import Path
            Path(output).write_text(result, encoding="utf-8")
            click.echo(f"Report written to {output}")
        else:
            click.echo(result)
        sys.exit(1 if findings else 0)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(2)
    except Exception as exc:
        click.echo(f"Error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(2)


__all__ = ["check"]
