"""FormatHighlighter — 在 QTextEdit 段落上画红色波浪线（Grammarly 风格）。

Sprint 1.5 实时约束的一部分：``ContentEditor`` 持有本服务，
``LiveFormatChecker.issues_found`` → ``FormatHighlighter.mark(issues)`` →
``QTextEdit.setExtraSelections([...])`` 在有问题的段落上画红色波浪下划线。

设计要点（参考 docs/sprints/sprint-1.5-plan.md §3）：

- **段落定位**：用 ``paragraph_index`` 作为 block 编号，``document().findBlockByNumber(n)``。
  block 越界（>= blockCount）→ 跳过，不抛错（plan §3 "paragraph_index 越界降级"）。
- **重复合并**：同一 paragraph 多次出现只生成 1 个 selection（用 ``set`` 去重）。
  避免 N 条同段落 issue 画出 N 条重叠的下划线。
- **全量替换**：每次 ``mark()`` 都覆盖之前的 selections（plan ADR-3）。
  ``setExtraSelections([...])`` 本身就是替换语义。
- **样式**：``QTextFormat.SpellCheckUnderline``（波浪）+ ``QColor("#FF0000")``（红）。
  跨平台一致，PyQt 原生支持。
- **clear()**：``setExtraSelections([])`` — 学生一键"清除所有提示"或文档转合规时调。

Public API:
    FormatHighlighter   包装 QTextEdit，给 QtIssue 段落画红色波浪线
    mark(issues)        重新计算 selections（替换）
    clear()             清空所有 selections
"""

from __future__ import annotations

from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat
from PyQt5.QtWidgets import QTextEdit

from app.core.reporters.pyqt_reporter import QtIssue


class FormatHighlighter:
    """在 ``QTextEdit`` 的指定段落上画红色波浪下划线。"""

    def __init__(self, text_edit: QTextEdit) -> None:
        self._text_edit = text_edit

    def mark(self, issues: list[QtIssue]) -> None:
        """为 ``issues`` 中每个唯一 paragraph_index 生成一个 ExtraSelection。

        - 同一 paragraph 多次出现 → 合并为 1 个 selection
        - paragraph_index 越界（>= blockCount 或 < 0）→ 跳过
        - 调用前会自动 clear 之前的 selections
        """
        doc = self._text_edit.document()
        total_blocks = doc.blockCount()
        # 用 set 去重：同一 paragraph_index 只画一条线
        unique_indexes = {
            i.paragraph_index for i in issues
            if 0 <= i.paragraph_index < total_blocks
        }
        selections: list[QTextEdit.ExtraSelection] = []
        for idx in unique_indexes:
            block = doc.findBlockByNumber(idx)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format = self._build_wave_format()
            sel.cursor = QTextCursor(block)
            selections.append(sel)
        self._text_edit.setExtraSelections(selections)

    def clear(self) -> None:
        """清空所有 ExtraSelections（合规 / 学生按"清除提示"时调）。"""
        self._text_edit.setExtraSelections([])

    @staticmethod
    def _build_wave_format() -> QTextCharFormat:
        """红色波浪下划线样式（Grammarly 风格）。"""
        fmt = QTextCharFormat()
        fmt.setProperty(QTextFormat.FullWidthSelection, True)
        fmt.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        fmt.setUnderlineColor(QColor("#FF0000"))
        return fmt


__all__ = ["FormatHighlighter"]
