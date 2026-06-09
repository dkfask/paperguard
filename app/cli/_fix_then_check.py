"""_fix_then_check — CLI 内部 helper (Sprint 3 Sprint 4.5 闭环预铺).

修复后自动调用 check，输出 remaining findings 到 changes.md。
如果 remaining=0，输出"全修复"提示。
"""

from __future__ import annotations

from pathlib import Path

import click

from app.core.engine import validate_docx
from app.core.loaders.docx_loader import load_docx
from app.core.reporters.terminal import TerminalReporter


def fix_then_check(docx_path: str, pack: str, pre_fixes_count: int) -> None:
    """修复后自动调用 check, 输出 remaining findings 到 changes.md.

    Args:
        docx_path: 修复后的 docx 路径
        pack: 规则包 ID
        pre_fixes_count: 修复前的 findings 数（用于报告）
    """
    ir = load_docx(docx_path)
    remaining = validate_docx(ir, pack=pack)

    if not remaining:
        click.echo(f"✓ All {pre_fixes_count} issues auto-fixed successfully.")
        return

    # 输出 remaining 到 changes.md
    changes_path = Path(docx_path).parent / "changes.md"
    lines = [
        f"# PaperGuard Fix Report",
        f"",
        f"- Source: `{Path(docx_path).name}`",
        f"- Pack: `{pack}`",
        f"- Pre-fix issues: {pre_fixes_count}",
        f"- Auto-fixed: {pre_fixes_count - len(remaining)}",
        f"- Remaining: {len(remaining)}",
        f"",
        TerminalReporter().report_findings(remaining),
    ]
    changes_path.write_text("\n".join(lines), encoding="utf-8")
    click.echo(f"⚠ {len(remaining)} issues remain. See {changes_path}")


__all__ = ["fix_then_check"]
