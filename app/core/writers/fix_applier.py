"""FixApplier — 把 Finding.fix 应用到 IR Document，输出新 Document.

设计契约（plan §3.5）：
- 不可变操作：frozen dataclass，每次修改返回新 Document
- 只支持 action='set'（insert/delete/wrap 留 Sprint 5）
- target 点分解析: 'paragraph.X' / 'run.X'
- 未知 target / 非 set action → fail-safe 返原 IR
- apply_all_fixes 返 (fixed_ir, remaining_findings)；remaining 含 fix=None 和未知 target 的 finding

借鉴 references/format-thesis-docx/scripts/apply_format.py:40-67 (set_run_property mapping)
"""

from __future__ import annotations

from dataclasses import replace

from app.core.ir import Document, Finding, Paragraph, Run


_TARGET_ALIASES = {
    "paragraph.page_break_before": "paragraph.is_page_break_before",
}


def _normalize_target(target: str) -> str:
    return _TARGET_ALIASES.get(target, target)


def apply_fix(ir: Document, finding: Finding) -> Document:
    """对单个 Finding 应用 fix，返回新的 IR Document（不可变）。

    失败安全：未知 target / 非 set action → 返原 ir。
    """
    if finding.fix is None or finding.fix.action != "set":
        return ir

    target = _normalize_target(finding.fix.target)
    value = finding.fix.value
    para_idx = finding.location.paragraph_index
    if para_idx is None or para_idx >= len(ir.paragraphs):
        return ir

    parts = target.split(".", 1)
    if len(parts) != 2:
        return ir
    section, attr = parts[0], parts[1]

    orig_para = ir.paragraphs[para_idx]

    if section == "paragraph":
        # 设置段落级属性
        if not hasattr(orig_para, attr):
            return ir
        try:
            new_para = replace(orig_para, **{attr: value})
        except (TypeError, ValueError):
            return ir
    elif section == "run":
        # 设置第一个 run 的属性
        if not orig_para.runs:
            return ir
        first_run = orig_para.runs[0]
        if not hasattr(first_run, attr):
            return ir
        try:
            new_first = replace(first_run, **{attr: value})
        except (TypeError, ValueError):
            return ir
        new_runs = (new_first,) + orig_para.runs[1:]
        new_para = replace(orig_para, runs=new_runs)
    elif section == "runs" and attr.startswith("*."):
        run_attr = attr[2:]
        if not orig_para.runs:
            return ir
        if any(not hasattr(run, run_attr) for run in orig_para.runs):
            return ir
        try:
            new_runs = tuple(replace(run, **{run_attr: value}) for run in orig_para.runs)
        except (TypeError, ValueError):
            return ir
        new_para = replace(orig_para, runs=new_runs)
    else:
        return ir

    # 构造新 Document
    new_paragraphs = list(ir.paragraphs)
    new_paragraphs[para_idx] = new_para
    return Document(
        sections=ir.sections,
        paragraphs=tuple(new_paragraphs),
        metadata=ir.metadata,
    )


def apply_all_fixes(
    ir: Document, findings: list[Finding]
) -> tuple[Document, list[Finding]]:
    """顺序应用全部 fix，返回 (fixed_ir, remaining_findings).

    remaining 含：
    - fix=None 的 finding（无法自动 fix）
    - 未知 target 的 finding（fail-safe 跳过）
    - 非 set action 的 finding（MVP 不支持）
    """
    current_ir = ir
    remaining: list[Finding] = []
    for f in findings:
        if f.fix is None:
            remaining.append(f)
            continue
        if f.fix.action != "set":
            remaining.append(f)
            continue
        # 检查 target 是否可解析（避免多余 IR 重建）
        target = _normalize_target(f.fix.target)
        parts = target.split(".", 1)
        if len(parts) != 2:
            remaining.append(f)
            continue
        section, attr = parts[0], parts[1]
        para_idx = f.location.paragraph_index
        if para_idx is None or para_idx >= len(current_ir.paragraphs):
            remaining.append(f)
            continue
        orig_para = current_ir.paragraphs[para_idx]
        if section == "paragraph" and not hasattr(orig_para, attr):
            remaining.append(f)
            continue
        if section == "run" and (not orig_para.runs or not hasattr(orig_para.runs[0], attr)):
            remaining.append(f)
            continue
        if section == "runs":
            if not attr.startswith("*."):
                remaining.append(f)
                continue
            run_attr = attr[2:]
            if not orig_para.runs or any(not hasattr(run, run_attr) for run in orig_para.runs):
                remaining.append(f)
                continue
        new_ir = apply_fix(current_ir, f)
        if new_ir is current_ir:
            remaining.append(f)
        else:
            current_ir = new_ir
    return current_ir, remaining


__all__ = ["apply_fix", "apply_all_fixes"]
