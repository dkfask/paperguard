"""Paragraph IR — docx 中一个 w:p 段落的不可变表示。

`level` 是从 style_id 推断的层级（Heading 1 → 1，Body Text → None）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ir.run import Run


@dataclass(frozen=True)
class Paragraph:
    """一个段落。"""
    style_id: str
    text: str
    level: int | None
    runs: tuple[Run, ...]
    line_spacing: float
    space_before_pt: float
    space_after_pt: float
    first_line_indent_cm: float
    is_page_break_before: bool = False
