"""_fix_then_check — CLI 内部 helper for fix verification reports."""

from __future__ import annotations

from pathlib import Path

import click

from app.core.engine import validate_docx
from app.core.ir import Finding
from app.core.loaders.docx_loader import load_docx
from app.core.reporters.terminal import TerminalReporter


def fix_then_check(
    source_path: str,
    output_path: str,
    pack: str,
    pre_findings: list[Finding],
    skipped_findings: list[Finding],
) -> list[Finding]:
    """修复后自动调用 check, 输出完整 changes.md, 返回二次检查 remaining.

    Args:
        source_path: 原始 docx 路径
        output_path: 修复后的 docx 路径
        pack: 规则包 ID
        pre_findings: 修复前 findings
        skipped_findings: 无法自动应用的 findings
    """
    ir = load_docx(output_path)
    remaining = validate_docx(ir, pack=pack)

    changes_path = Path(output_path).parent / "changes.md"
    lines = _build_changes_report(
        source_path=source_path,
        output_path=output_path,
        pack=pack,
        pre_findings=pre_findings,
        skipped_findings=skipped_findings,
        remaining_findings=remaining,
    )
    changes_path.write_text("\n".join(lines), encoding="utf-8")
    if remaining:
        click.echo(f"{len(remaining)} issues remain. See {changes_path}")
    else:
        click.echo(f"All {len(pre_findings)} issues verified fixed. See {changes_path}")
    return remaining


def _build_changes_report(
    source_path: str,
    output_path: str,
    pack: str,
    pre_findings: list[Finding],
    skipped_findings: list[Finding],
    remaining_findings: list[Finding],
) -> list[str]:
    pre_count = len(pre_findings)
    skipped_count = len(skipped_findings)
    remaining_count = len(remaining_findings)
    auto_fixed_count = max(0, pre_count - skipped_count - remaining_count)
    auto_fixed = _confirmed_auto_fixed(pre_findings, skipped_findings, remaining_findings)

    lines = [
        "# PaperGuard Fix Report",
        "",
        f"- Source: `{Path(source_path).name}`",
        f"- Output: `{Path(output_path).name}`",
        f"- Pack: `{pack}`",
        f"- Pre-fix issues: {pre_count}",
        f"- Auto-fixed: {auto_fixed_count}",
        f"- Remaining: {remaining_count}",
        f"- Skipped: {skipped_count}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Pre-fix | {pre_count} |",
        f"| Auto-fixed | {auto_fixed_count} |",
        f"| Remaining | {remaining_count} |",
        f"| Skipped | {skipped_count} |",
        "",
        f"## Auto-fixed ({auto_fixed_count})",
        "",
    ]
    if auto_fixed:
        lines.extend(_finding_bullets(auto_fixed))
    else:
        lines.append("No auto-fixed issues confirmed.")

    lines.extend(["", f"## Remaining ({remaining_count})", ""])
    if remaining_findings:
        lines.append(TerminalReporter().report_findings(remaining_findings))
    else:
        lines.append("No issues remain.")

    lines.extend(["", f"## Skipped ({skipped_count})", ""])
    if skipped_findings:
        lines.extend(_finding_bullets(skipped_findings))
    else:
        lines.append("No fixes were skipped.")

    return lines


def _confirmed_auto_fixed(
    pre_findings: list[Finding],
    skipped_findings: list[Finding],
    remaining_findings: list[Finding],
) -> list[Finding]:
    skipped_keys = {_finding_key(f) for f in skipped_findings}
    remaining_keys = {_finding_key(f) for f in remaining_findings}
    return [
        f for f in pre_findings
        if _finding_key(f) not in skipped_keys and _finding_key(f) not in remaining_keys
    ]


def _finding_key(finding: Finding) -> tuple[str, int | None]:
    return finding.rule_id, finding.location.paragraph_index


def _finding_bullets(findings: list[Finding]) -> list[str]:
    bullets: list[str] = []
    for finding in findings:
        para = finding.location.paragraph_index
        location = f"paragraph {para}" if para is not None else "document"
        bullets.append(f"- [{finding.rule_id}] {location}: {finding.message}")
    return bullets


__all__ = ["fix_then_check"]
