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
    hanging_indent_cm: float = 0.0
    alignment: str | None = None       # left / center / right / justify
    is_page_break_before: bool = False
    table_index: int | None = None
    table_row_index: int | None = None
    table_cell_index: int | None = None
    list_type: str | None = None       # bullet / number
    list_level: int | None = None

    def to_dict(self) -> dict:
        """Sprint 2 evaluator 用 — 嵌套 runs 展开为 list[dict] 供 jmespath 消费。"""
        return {
            "style_id": self.style_id,
            "text": self.text,
            "level": self.level,
            "runs": [r.to_dict() for r in self.runs],
            "line_spacing": self.line_spacing,
            "space_before_pt": self.space_before_pt,
            "space_after_pt": self.space_after_pt,
            "first_line_indent_cm": self.first_line_indent_cm,
            "hanging_indent_cm": self.hanging_indent_cm,
            "alignment": self.alignment,
            "is_page_break_before": self.is_page_break_before,
            "table_index": self.table_index,
            "table_row_index": self.table_row_index,
            "table_cell_index": self.table_cell_index,
            "list_type": self.list_type,
            "list_level": self.list_level,
        }
