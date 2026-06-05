"""Terminal reporter — 把 Document IR 渲染为人类可读的纯文本。

MVP 输出格式：
    Document: 5 paragraphs, 1 sections
    Section 1: A4 (21.0×29.7cm), margins T/B/L/R = 2.54/2.54/3.17/3.17 cm, header=1.5cm, footer=1.75cm, portrait
    Paragraph 1: [Heading 1] (level=1) "1 引言" — 18.0pt 黑体, line=1.5, indent=0.0cm
    Paragraph 2: [Body Text] (level=None) "本文研究..." — 12.0pt 宋体, line=1.5, indent=0.74cm
    ...
"""

from __future__ import annotations

from app.core.ir import Document, Paragraph, Run


def _format_run(run: Run) -> str:
    """Run → '12.0pt 宋体' 短描述。"""
    parts: list[str] = []
    if run.font_size_pt is not None:
        # 整数 12 显示为 12，浮点 12.5 显示 12.5
        if run.font_size_pt == int(run.font_size_pt):
            parts.append(f"{int(run.font_size_pt)}.0pt")
        else:
            parts.append(f"{run.font_size_pt}pt")
    if run.font_eastasia:
        parts.append(run.font_eastasia)
    return " ".join(parts) if parts else "—"


def _format_paragraph(idx: int, p: Paragraph) -> str:
    """Paragraph → 'Paragraph N: [style] (level=X) "text" — run, line, indent'。"""
    # 截断长文本
    text = p.text
    if len(text) > 60:
        text = text[:57] + "..."

    # style + level
    style_part = f"[{p.style_id}]" if p.style_id else "[<no-style>]"
    level_part = f" (level={p.level})" if p.level is not None else " (level=None)"

    # run 描述
    if p.runs:
        first_run = p.runs[0]
        run_desc = _format_run(first_run)
        if len(p.runs) > 1:
            run_desc += f" (+{len(p.runs) - 1} runs)"
    else:
        run_desc = "—"

    # 行距 / 缩进
    line_part = f"line={p.line_spacing}" if p.line_spacing != 1.0 else "line=1.0"
    if p.first_line_indent_cm > 0:
        indent_part = f"indent={p.first_line_indent_cm}cm"
    else:
        indent_part = "indent=0"

    return f'Paragraph {idx}: {style_part}{level_part} "{text}" — {run_desc}, {line_part}, {indent_part}'


def _format_section(idx: int, s) -> str:
    """Section → 'Section N: A4 (WxHcm), margins T/B/L/R = ..., portrait'。"""
    size_part = f"({s.page_width_cm}×{s.page_height_cm}cm)"
    margins_part = (
        f"margins T/B/L/R = {s.margin_top_cm}/{s.margin_bottom_cm}/"
        f"{s.margin_left_cm}/{s.margin_right_cm} cm"
    )
    if s.header_distance_cm > 0 or s.footer_distance_cm > 0:
        margins_part += (
            f", header={s.header_distance_cm}cm, footer={s.footer_distance_cm}cm"
        )
    margins_part += f", {s.orientation}"
    return f"Section {idx}: {size_part}, {margins_part}"


class TerminalReporter:
    """把 Document IR 渲染为纯文本。"""

    def report(self, doc: Document) -> str:
        """Render Document → str."""
        lines: list[str] = []

        # 头部
        lines.append(
            f"Document: {len(doc.paragraphs)} paragraphs, {len(doc.sections)} sections"
        )

        # 元数据
        if doc.metadata.title or doc.metadata.author:
            meta_parts: list[str] = []
            if doc.metadata.title:
                meta_parts.append(f'title="{doc.metadata.title}"')
            if doc.metadata.author:
                meta_parts.append(f'author="{doc.metadata.author}"')
            lines.append("  " + ", ".join(meta_parts))

        # sections
        for i, s in enumerate(doc.sections, start=1):
            lines.append(_format_section(i, s))

        # paragraphs（列出前 20 行 + 摘要）
        max_paragraphs_to_list = 20
        for i, p in enumerate(doc.paragraphs[:max_paragraphs_to_list], start=1):
            lines.append(_format_paragraph(i, p))

        if len(doc.paragraphs) > max_paragraphs_to_list:
            lines.append(
                f"... ({len(doc.paragraphs) - max_paragraphs_to_list} more paragraphs omitted)"
            )

        return "\n".join(lines)


__all__ = ["TerminalReporter"]
