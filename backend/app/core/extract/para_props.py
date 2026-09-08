"""para_props — 从 <w:pPr> 提取段落属性 (点分 property dict).

借鉴 references/format-thesis-docx/scripts/extract_requirements.py:84-107
但实现**重写**。

设计契约：
- para_props(ppr: Element|None) -> dict[str, str]
- 字段点分命名 ('paragraph.style_id' / 'paragraph.spacing.line')
- 与 IR v1.1 保持一致

**OOXML 模式**：大部分 pPr 子节点都用 `<w:xxx w:val="value"/>` 形式，
子节点的 `w:val` 属性存值；而 pPr 自身的直接属性（如 `w:rsidR`）才直接
放在 pPr 上。所以正确做法是先 find 子节点再读 val。
"""

from __future__ import annotations

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _w(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def para_props(ppr: etree._Element | None) -> dict[str, str]:
    """Extract paragraph properties from <w:pPr>; return point-dotted dict.

    >>> para_props(None)
    {}
    """
    if ppr is None:
        return {}

    result: dict[str, str] = {}

    # 段落样式: <w:pStyle w:val="X"/>
    el = ppr.find(_w("pStyle"))
    if el is not None:
        v = el.get(_w("val"))
        if v:
            result["paragraph.style_id"] = v

    # 对齐: <w:jc w:val="X"/>
    el = ppr.find(_w("jc"))
    if el is not None:
        v = el.get(_w("val"))
        if v:
            result["paragraph.align"] = v

    # 间距: <w:spacing w:before/after/line w:val="..."/>
    spacing = ppr.find(_w("spacing"))
    if spacing is not None:
        for sub, dotted in (
            ("before", "paragraph.spacing.before"),
            ("after", "paragraph.spacing.after"),
            ("line", "paragraph.spacing.line"),
        ):
            v = spacing.get(_w(sub))
            if v:
                result[dotted] = v
        # 行距规则: auto=倍数, exact=固定值, atLeast=最小值
        v = spacing.get(_w("lineRule"))
        if v:
            result["paragraph.spacing.line_rule"] = v
        # 中文"段前/段后 N 行" (单位 1/100 行)
        for sub, dotted in (
            ("beforeLines", "paragraph.spacing.before_lines"),
            ("afterLines", "paragraph.spacing.after_lines"),
        ):
            v = spacing.get(_w(sub))
            if v:
                result[dotted] = v

    # 缩进: <w:ind w:left/right/firstLine/hanging w:val="..."/>
    ind = ppr.find(_w("ind"))
    if ind is not None:
        for sub, dotted in (
            ("left", "paragraph.indent.left"),
            ("right", "paragraph.indent.right"),
            ("firstLine", "paragraph.indent.first_line"),
            ("hanging", "paragraph.indent.hanging"),
            # 首行缩进按字符数 (中文模板最常用)
            ("firstLineChars", "paragraph.indent.first_line_chars"),
        ):
            v = ind.get(_w(sub))
            if v:
                result[dotted] = v

    # keepNext: <w:keepNext/> 存在即 true
    if ppr.find(_w("keepNext")) is not None:
        result["paragraph.keep_next"] = "true"

    # pageBreakBefore: <w:pageBreakBefore/> 存在即 true
    if ppr.find(_w("pageBreakBefore")) is not None:
        result["paragraph.page_break_before"] = "true"

    return result


__all__ = ["para_props"]
