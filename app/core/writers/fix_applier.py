"""Apply Finding.fix operations to immutable IR Documents.

Supported actions are intentionally conservative:
- set: paragraph.<attr>, run.<attr>, or runs.*.<attr>
- insert: paragraph.before or paragraph.after
- delete: paragraph

Unknown actions, targets, indexes, or malformed values return the original
Document so callers can record the finding as remaining.
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
    """Apply one finding fix and return a new Document when it succeeds."""
    if finding.fix is None:
        return ir

    action = finding.fix.action
    target = _normalize_target(finding.fix.target)
    value = finding.fix.value
    para_idx = finding.location.paragraph_index
    if para_idx is None or para_idx >= len(ir.paragraphs):
        return ir

    if action == "delete":
        return _apply_delete(ir, target, para_idx)
    if action == "insert":
        return _apply_insert(ir, target, para_idx, value)
    if action == "set":
        return _apply_set(ir, target, para_idx, value)
    return ir


def _apply_set(ir: Document, target: str, para_idx: int, value) -> Document:
    parts = target.split(".", 1)
    if len(parts) != 2:
        return ir
    section, attr = parts[0], parts[1]
    orig_para = ir.paragraphs[para_idx]

    if section == "paragraph":
        if not hasattr(orig_para, attr):
            return ir
        try:
            new_para = replace(orig_para, **{attr: value})
        except (TypeError, ValueError):
            return ir
    elif section == "run":
        if not orig_para.runs:
            return ir
        first_run = orig_para.runs[0]
        if not hasattr(first_run, attr):
            return ir
        try:
            new_first = replace(first_run, **{attr: value})
        except (TypeError, ValueError):
            return ir
        new_para = replace(orig_para, runs=(new_first,) + orig_para.runs[1:])
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

    new_paragraphs = list(ir.paragraphs)
    new_paragraphs[para_idx] = new_para
    return _replace_paragraphs(ir, tuple(new_paragraphs))


def _apply_delete(ir: Document, target: str, para_idx: int) -> Document:
    if target != "paragraph":
        return ir
    new_paragraphs = list(ir.paragraphs)
    del new_paragraphs[para_idx]
    return _replace_paragraphs(ir, tuple(new_paragraphs))


def _apply_insert(ir: Document, target: str, para_idx: int, value) -> Document:
    if target not in ("paragraph.before", "paragraph.after"):
        return ir
    new_para = _paragraph_from_value(value)
    if new_para is None:
        return ir
    insert_at = para_idx if target == "paragraph.before" else para_idx + 1
    new_paragraphs = list(ir.paragraphs)
    new_paragraphs.insert(insert_at, new_para)
    return _replace_paragraphs(ir, tuple(new_paragraphs))


def _replace_paragraphs(ir: Document, paragraphs: tuple[Paragraph, ...]) -> Document:
    return Document(
        sections=ir.sections,
        paragraphs=paragraphs,
        metadata=ir.metadata,
        header_paragraphs=ir.header_paragraphs,
        footer_paragraphs=ir.footer_paragraphs,
    )


def _paragraph_from_value(value) -> Paragraph | None:
    if isinstance(value, Paragraph):
        return value
    if not isinstance(value, dict):
        return None
    text = str(value.get("text", ""))
    try:
        return Paragraph(
            style_id=str(value.get("style_id", "")),
            text=text,
            level=value.get("level"),
            runs=_runs_from_value(value.get("runs"), text),
            line_spacing=float(value.get("line_spacing", 1.0)),
            space_before_pt=float(value.get("space_before_pt", 0.0)),
            space_after_pt=float(value.get("space_after_pt", 0.0)),
            first_line_indent_cm=float(value.get("first_line_indent_cm", 0.0)),
            alignment=value.get("alignment"),
            is_page_break_before=bool(value.get("is_page_break_before", False)),
        )
    except (TypeError, ValueError):
        return None


def _runs_from_value(value, fallback_text: str) -> tuple[Run, ...]:
    if value is None:
        return (Run(text=fallback_text),) if fallback_text else ()
    if not isinstance(value, (list, tuple)):
        return ()
    runs: list[Run] = []
    for item in value:
        run = _run_from_value(item)
        if run is None:
            return ()
        runs.append(run)
    return tuple(runs)


def _run_from_value(value) -> Run | None:
    if isinstance(value, Run):
        return value
    if not isinstance(value, dict):
        return None
    try:
        return Run(
            text=str(value.get("text", "")),
            font_eastasia=value.get("font_eastasia"),
            font_ascii=value.get("font_ascii"),
            font_hansi=value.get("font_hansi"),
            font_cs=value.get("font_cs"),
            font_size_pt=value.get("font_size_pt"),
            bold=bool(value.get("bold", False)),
            italic=bool(value.get("italic", False)),
            underline=value.get("underline"),
            color_hex=value.get("color_hex"),
            highlight=value.get("highlight"),
            vert_align=value.get("vert_align"),
        )
    except (TypeError, ValueError):
        return None


def apply_all_fixes(
    ir: Document, findings: list[Finding]
) -> tuple[Document, list[Finding]]:
    """Apply fixes in order and return (fixed_ir, remaining_findings)."""
    current_ir = ir
    remaining: list[Finding] = []
    for finding in findings:
        if finding.fix is None:
            remaining.append(finding)
            continue
        new_ir = apply_fix(current_ir, finding)
        if new_ir is current_ir:
            remaining.append(finding)
        else:
            current_ir = new_ir
    return current_ir, remaining


__all__ = ["apply_fix", "apply_all_fixes"]
