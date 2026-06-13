"""LiveMutator — Sprint 1.5 实时约束 "一键修复" 组件。

Sprint 1.5 起步只支持 3 类 Fix（与当时 stub engine 范围对齐）：
  - ``line_spacing``           → 段行距
  - ``font_size_pt``           → 段首个 run 的字号
  - ``first_line_indent_cm``   → 段首行缩进

Sprint 5 扩展支持 ``runs.*.<attr>``：可把字号、字体、颜色、粗斜体、
下划线等安全样式修复应用到段内所有 HTML run。

设计要点（来自 plan §3 "app/services/live_mutator.py"）：

- **Must-fix #1（单参数签名）**：
  ``apply_fix(issue: QtIssue) -> bool``。``Fix`` 由 ``checker.get_fix(issue)``
  内部取，禁止外部传 Fix；checker 接口参考 plan §3 live_format_checker。
- **Must-fix #3（改 blocks 不直接改 content_html）**：
  ``SectionContent.content_html`` 是 computed property
  （``blocks_to_editor_html(self.blocks)``），mutator 必须修改
  ``section.blocks[i]``，让 UI 在 ``editor.setHtml(section.content_html)`` 时
  自然看到新值。
- **零 PyQt 依赖**：mutator 只动 model 数据，UI 后续重读（plan ADR-4）。
- **Section 定位**：``QtIssue`` 不携带 ``section_id``（plan ADR-1），mutator
  遍历 ``project.sections`` 找首个 ``len(blocks) > paragraph_index`` 的 section。
  MVP 阶段 ContentEditor 只编辑一个 section（Sprint 2 再处理多 section）。

``ParagraphBlock`` 当前模型只有 ``html`` 字段（``app/models/project.py``），
不可在 Sprint 1.5 加 ``line_spacing`` / ``first_line_indent`` 字段（任务硬约束
"ONLY touch the 2 files specified"）。所以 mutator 把新值编码进 ``<p>`` 标签
的 inline ``style`` 属性：
  - ``line_spacing=1.5``      → ``style="line-height: 1.5"``
  - ``font_size_pt=12.0``     → 首个 ``<span>`` 加 ``style="font-size: 12.0pt;"``
  - ``first_line_indent=0.74``→ ``style="text-indent: 0.74cm;"``

这样 ``blocks_to_editor_html`` 输出的 HTML 已经是合规的 docx-ready 富文本，
``docx_writer``（Sprint 5）可直接读 ``style`` 转 OOXML。

不在 Sprint 1.5 范围：
  - 自定义 undo：``QTextEdit`` 自带 Ctrl+Z（Q6 决策）。
  - 跨 section 定位：MVP 单 section（ContentEditor 设计）。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.ir.finding import Fix
from app.core.reporters.pyqt_reporter import QtIssue
from app.models.project import (
    ParagraphBlock,
    Project,
    SectionContent,
)


# 支持的 Fix target leaf 集合（白名单）。
SUPPORTED_RULE_IDS: frozenset[str] = frozenset({
    "line_spacing",
    "space_before_pt",
    "space_after_pt",
    "font_size_pt",
    "first_line_indent_cm",
    "alignment",
    "is_page_break_before",
    "page_break_before",
    "font_eastasia",
    "font_ascii",
    "font_hansi",
    "color_hex",
    "bold",
    "italic",
    "underline",
    "highlight",
    "vert_align",
})


# ---------------------------------------------------------------------------
# HTML 内部小工具 — 改 <p> 的 style 属性 / 改首个 run 的 font-size
# ---------------------------------------------------------------------------

_BLOCK_TAG_RE = re.compile(r"<(p|div|h[1-6])\b([^>]*)>", re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r"""style\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_RUN_TAG_RE = re.compile(r"<(span|font)\b([^>]*)>", re.IGNORECASE)


def _set_style_property(attrs: str, prop_name: str, prop_value: str) -> str:
    """在一段 HTML attribute 字符串中 upsert 一个 CSS property。

    - 如果已有 ``style="..."``：替换或追加
    - 如果没有 ``style`` 属性：追加 ``style="prop_name: prop_value;"``
    """
    style_match = _STYLE_ATTR_RE.search(attrs)
    if style_match is None:
        new_style = f"{prop_name}: {prop_value};"
        return f'{attrs} style="{new_style}"'

    current_style = style_match.group(1)
    prop_re = re.compile(rf"{re.escape(prop_name)}\s*:\s*[^;]+", re.IGNORECASE)
    if prop_re.search(current_style):
        new_style = prop_re.sub(f"{prop_name}: {prop_value}", current_style)
    else:
        joined = current_style.rstrip(";").rstrip()
        new_style = f"{joined}; {prop_name}: {prop_value}" if joined else f"{prop_name}: {prop_value}"
    return attrs[:style_match.start()] + f'style="{new_style}"' + attrs[style_match.end():]


def _update_paragraph_style(html: str, prop_name: str, prop_value: str) -> str:
    """更新首个 ``<p>`` 标签的 inline style 属性；无 ``<p>`` 时包一层。"""
    p_match = _BLOCK_TAG_RE.search(html or "")
    if p_match is None:
        wrapped = f'<p style="{prop_name}: {prop_value};">{html or ""}</p>'
        return wrapped
    tag_name = p_match.group(1)
    new_attrs = _set_style_property(p_match.group(2), prop_name, prop_value)
    new_tag = f"<{tag_name}{new_attrs}>"
    return html[:p_match.start()] + new_tag + html[p_match.end():]


def _update_first_run_font_size(html: str, value_pt: float) -> str:
    """把 block HTML 里的首个 run 字号设为 ``value_pt``，并保留段落/标题标签。"""
    return _update_first_run_style(html, "font-size", f"{value_pt}pt")


def _update_first_run_style(html: str, prop_name: str, prop_value: str) -> str:
    """Update the first explicit HTML run; wrap plain paragraph text when needed."""
    p_open_re = re.compile(r"<(p|div|h[1-6])\b([^>]*)>(.*)</\1>", re.IGNORECASE | re.DOTALL)
    p_match = p_open_re.search(html or "")
    if p_match is None:
        return f'<p><span style="{prop_name}: {prop_value};">{html or ""}</span></p>'

    tag_name = p_match.group(1)
    p_attrs = p_match.group(2)
    inner = p_match.group(3)
    run_match = _RUN_TAG_RE.search(inner)
    if run_match is None:
        new_inner = f'<span style="{prop_name}: {prop_value};">{inner}</span>'
    else:
        run_tag_name = run_match.group(1)
        tag_attrs = run_match.group(2)
        new_tag_attrs = _set_style_property(tag_attrs, prop_name, prop_value)
        new_tag = f"<{run_tag_name}{new_tag_attrs}>"
        new_inner = inner[:run_match.start()] + new_tag + inner[run_match.end():]
    return f"<{tag_name}{p_attrs}>{new_inner}</{tag_name}>"


def _update_all_runs_style(html: str, prop_name: str, prop_value: str) -> str:
    """Update every explicit HTML run; wrap plain paragraph text when needed."""
    p_open_re = re.compile(r"<p\b([^>]*)>(.*)</p>", re.IGNORECASE | re.DOTALL)
    p_match = p_open_re.search(html or "")
    if p_match is None:
        return f'<p><span style="{prop_name}: {prop_value};">{html or ""}</span></p>'

    p_attrs = p_match.group(1)
    inner = p_match.group(2)
    if _RUN_TAG_RE.search(inner) is None:
        return f'<p{p_attrs}><span style="{prop_name}: {prop_value};">{inner}</span></p>'

    def replace_run(match: re.Match) -> str:
        tag_name = match.group(1)
        tag_attrs = match.group(2)
        new_tag_attrs = _set_style_property(tag_attrs, prop_name, prop_value)
        return f"<{tag_name}{new_tag_attrs}>"

    return f"<p{p_attrs}>{_RUN_TAG_RE.sub(replace_run, inner)}</p>"


def _is_runs_wildcard(target: str) -> bool:
    return target.startswith("runs.*.")


def _highlight_css_value(value: Any) -> str:
    if value is None:
        return "transparent"
    normalized = str(value).strip()
    if not normalized or normalized.lower() in {"none", "null"}:
        return "transparent"
    return normalized


def _vert_align_css_value(value: Any) -> str:
    if value is None:
        return "baseline"
    normalized = str(value).strip().lower()
    if normalized in {"", "none", "null", "baseline"}:
        return "baseline"
    if normalized == "superscript":
        return "super"
    if normalized == "subscript":
        return "sub"
    return normalized


def _page_break_css_value(value: Any) -> str:
    return "always" if bool(value) else "auto"


# ---------------------------------------------------------------------------
# Fix application dispatch
# ---------------------------------------------------------------------------

def _apply_fix_to_block(block: ParagraphBlock, fix: Fix) -> None:
    """按 ``fix.target`` 的 leaf 段把值写进 ``ParagraphBlock``。非目标 target 不动。

    兼容点分（``"paragraph.line_spacing"``）和简写（``"line_spacing"``）两种
    target 形式 — 统一取最后一段。
    """
    target = _target_leaf(fix.target)
    if target == "line_spacing":
        block.html = _update_paragraph_style(block.html, "line-height", f"{fix.value}")
    elif target == "space_before_pt":
        block.html = _update_paragraph_style(block.html, "margin-top", f"{float(fix.value)}pt")
    elif target == "space_after_pt":
        block.html = _update_paragraph_style(block.html, "margin-bottom", f"{float(fix.value)}pt")
    elif target == "first_line_indent_cm":
        block.html = _update_paragraph_style(block.html, "text-indent", f"{fix.value}cm")
    elif target == "alignment":
        block.html = _update_paragraph_style(block.html, "text-align", str(fix.value))
    elif target in {"is_page_break_before", "page_break_before"}:
        block.html = _update_paragraph_style(
            block.html,
            "page-break-before",
            _page_break_css_value(fix.value),
        )
    elif target == "font_size_pt" and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(block.html, "font-size", f"{float(fix.value)}pt")
    elif target == "font_size_pt":
        block.html = _update_first_run_font_size(block.html, float(fix.value))
    elif target in {"font_eastasia", "font_ascii", "font_hansi"} and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(block.html, "font-family", str(fix.value))
    elif target in {"font_eastasia", "font_ascii", "font_hansi"}:
        block.html = _update_first_run_style(block.html, "font-family", str(fix.value))
    elif target == "color_hex" and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(block.html, "color", f"#{str(fix.value).lstrip('#')}")
    elif target == "color_hex":
        block.html = _update_first_run_style(block.html, "color", f"#{str(fix.value).lstrip('#')}")
    elif target == "bold" and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(
            block.html,
            "font-weight",
            "bold" if bool(fix.value) else "normal",
        )
    elif target == "bold":
        block.html = _update_first_run_style(
            block.html,
            "font-weight",
            "bold" if bool(fix.value) else "normal",
        )
    elif target == "italic" and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(
            block.html,
            "font-style",
            "italic" if bool(fix.value) else "normal",
        )
    elif target == "italic":
        block.html = _update_first_run_style(
            block.html,
            "font-style",
            "italic" if bool(fix.value) else "normal",
        )
    elif target == "underline" and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(
            block.html,
            "text-decoration",
            "underline" if fix.value else "none",
        )
    elif target == "underline":
        block.html = _update_first_run_style(
            block.html,
            "text-decoration",
            "underline" if fix.value else "none",
        )
    elif target == "highlight" and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(
            block.html,
            "background-color",
            _highlight_css_value(fix.value),
        )
    elif target == "highlight":
        block.html = _update_first_run_style(
            block.html,
            "background-color",
            _highlight_css_value(fix.value),
        )
    elif target == "vert_align" and _is_runs_wildcard(fix.target):
        block.html = _update_all_runs_style(
            block.html,
            "vertical-align",
            _vert_align_css_value(fix.value),
        )
    elif target == "vert_align":
        block.html = _update_first_run_style(
            block.html,
            "vertical-align",
            _vert_align_css_value(fix.value),
        )


def _find_target_section(project: Project, paragraph_index: int) -> Optional[SectionContent]:
    """找首个 ``len(blocks) > paragraph_index`` 的 section。

    QtIssue 不带 section_id（plan ADR-1），单 ContentEditor 场景下 project
    通常只有 1 个 section；遍历足够。
    """
    for section in project.sections.values():
        if paragraph_index < len(section.blocks):
            return section
    return None


# ---------------------------------------------------------------------------
# LiveMutator
# ---------------------------------------------------------------------------

class LiveMutator:
    """把 ``QtIssue`` 对应的 ``Fix`` 应用到 ``Project`` 的 blocks。

    用法（ContentEditor）：

    .. code-block:: python

        checker = LiveFormatChecker(project, ...)
        mutator = LiveMutator(project, checker=checker)
        if mutator.can_apply(issue) and mutator.apply_fix(issue):
            editor.setHtml(section.content_html)  # QTextEdit 重读
    """

    def __init__(self, project: Project, checker: Any = None) -> None:
        """``checker`` 提供 ``get_fix(issue) -> Fix | None``。

        若 ``checker`` 为 None，使用一个永远返回 None 的退化 checker；
        此时 ``apply_fix`` 一律返 False（无 fix 可应用）。
        """
        self.project = project
        self.checker = checker

    def can_apply(self, issue: QtIssue) -> bool:
        """``issue`` 对应的 ``Fix.target`` 命中 3 类支持集合即可（不看 paragraph_index）。

        通过 checker.get_fix(issue) 拿到 Fix，从 ``target`` 字段（如
        ``"paragraph.line_spacing"``）取最后一段（``"line_spacing"``）
        判断 mutator 是否能改写。rule_id 本身（``R001`` 等）不参与匹配。

        paragraph_index 的合法性由 ``apply_fix`` 检查；``can_apply`` 用于
        UI 灰显"修复"按钮，所以只看"是否本 mutator 知道怎么改"。
        """
        if self.checker is None:
            return False
        fix = self.checker.get_fix(issue)
        if fix is None:
            return False
        return _target_leaf(fix.target) in SUPPORTED_RULE_IDS

    def apply_fix(self, issue: QtIssue) -> bool:
        """把 ``issue`` 对应的 ``Fix`` 写到 ``Project`` 的对应 block。

        Returns:
            True: 成功改写 ``SectionContent.blocks[i]``。
            False: 不支持的 target / 无 checker / checker 无 fix /
                   paragraph_index 越界 / 目标 block 非 ParagraphBlock。
        """
        if issue.paragraph_index < 0:
            return False
        if self.checker is None:
            return False

        fix = self.checker.get_fix(issue)
        if fix is None:
            return False
        if _target_leaf(fix.target) not in SUPPORTED_RULE_IDS:
            return False

        section = _find_target_section(self.project, issue.paragraph_index)
        if section is None:
            return False

        block = section.blocks[issue.paragraph_index]
        if not isinstance(block, ParagraphBlock):
            return False

        _apply_fix_to_block(block, fix)
        return True


def _target_leaf(target: str) -> str:
    """从 ``"paragraph.line_spacing"`` 取最后一段 ``"line_spacing"``。"""
    return target.rsplit(".", 1)[-1]


__all__ = ["LiveMutator", "SUPPORTED_RULE_IDS"]
