"""PyQt reporter — 把 Finding[] 投影为 QtIssue[]（frozen dataclass）。

**Sprint 1.5 关键设计点（见 docs/sprints/sprint-1.5-plan.md §3）**：
- 100% 无 PyQt 依赖（`app/core/` 硬约束保持）。
- 故意偏离 design-proposal.md §4.1.2 的 `PyQtReporter(QObject)` 设计：
  本文件只做"纯函数 + 纯 dataclass 投影"，QObject + pyqtSignal 由
  `app/services/live_format_checker.py` 持有。Qt signal 仍然发 `QtIssue[]`。
- `QtIssue` 暴露 6 字段，丢 `Finding` 内部细节（如 `Location` 元组、
  `Fix.action/target/value` 内部命名）—— UI 层只能看到这 6 字段。

Public API:
    QtIssue           frozen dataclass，6 字段
    to_qt_issue       Finding → QtIssue
    to_qt_issues      Iterable[Finding] → list[QtIssue]
    fix_to_human_text Fix → 人类可读中文（侧边栏"修复"提示用）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.core.ir.finding import Finding, Fix


# Fix.target → "人类可读中文模板" 的查表。未知 target 走安全降级（不抛错）。
# 模板与 Sprint 1.5 决策 6 类 Fix 一一对应（line_spacing / font_size_pt /
# first_line_indent_cm / font_family / paragraph_spacing / page_margin）。
_FIX_TEMPLATES: dict[str, str] = {
    "line_spacing": "设置行距为 {value}",
    "font_size_pt": "设置字号为 {value}pt",
    "first_line_indent_cm": "设置首行缩进为 {value}cm",
    "font_family": "设置字体为 {value}",
    "paragraph_spacing": "设置段间距为 {value}pt",
    "page_margin": "设置页边距为 {value}cm",
}


@dataclass(frozen=True)
class QtIssue:
    """PyQt 边界用的扁平 issue（6 字段，不可变）。

    设计目的：让 UI 层（侧边栏 / 高亮 / 状态栏）只看到 6 个简单字段，
    不需要 import `app.core.ir.*`，也不暴露 `Fix` 的内部结构。
    """

    rule_id: str
    severity: str          # "error" | "warning" | "info"（来自 Severity.value）
    message: str
    location_text: str     # 人类可读的位置描述，如 "第 3 章 第 5 段"
    paragraph_index: int   # -1 if unknown（高亮定位；找不到段落时不画波浪线）
    fix_suggestion: str | None


def _format_location_text(section: str | None, paragraph_index: int | None,
                          text_snippet: str | None) -> str:
    """Location → 人类可读字符串。

    优先级：section + 段落号 > section > 文本片段 > 空。
    """
    parts: list[str] = []
    if section:
        parts.append(section)
    if paragraph_index is not None:
        parts.append(f"第 {paragraph_index} 段")
    if text_snippet:
        # 截断长 snippet
        snippet = text_snippet if len(text_snippet) <= 30 else text_snippet[:27] + "..."
        parts.append(f"\"{snippet}\"")
    return " ".join(parts) if parts else ""


def fix_to_human_text(fix: Fix) -> str:
    """Fix → 人类可读中文（侧边栏"修复"按钮提示用）。

    未知 target 不抛错：返回带 action/target/value 的安全降级字符串，
    便于后续新增 Fix 类型时 UI 仍能显示（不白屏）。
    """
    template = _FIX_TEMPLATES.get(fix.target)
    if template is not None:
        return template.format(value=fix.value)
    # 降级：把内部结构拼成可读字符串（保留 value 类型可读性）
    return f"执行 {fix.action} {fix.target} = {fix.value}"


def to_qt_issue(finding: Finding) -> QtIssue:
    """Finding → QtIssue（单条投影）。"""
    location = finding.location
    return QtIssue(
        rule_id=finding.rule_id,
        severity=finding.severity.value,
        message=finding.message,
        location_text=_format_location_text(
            location.section,
            location.paragraph_index,
            location.text_snippet,
        ),
        paragraph_index=location.paragraph_index if location.paragraph_index is not None else -1,
        fix_suggestion=fix_to_human_text(finding.fix) if finding.fix is not None else None,
    )


def to_qt_issues(findings: Iterable[Finding]) -> list[QtIssue]:
    """Iterable[Finding] → list[QtIssue]（按输入顺序）。"""
    return [to_qt_issue(f) for f in findings]


__all__ = [
    "QtIssue",
    "fix_to_human_text",
    "to_qt_issue",
    "to_qt_issues",
]
