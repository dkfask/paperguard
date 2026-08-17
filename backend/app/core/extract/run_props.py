"""run_props — 从 <w:rPr> 提取 run 属性 (点分 property dict).

借鉴 references/format-thesis-docx/scripts/extract_requirements.py:63-80
但实现**重写**。中文字体 (eastAsia) 必走 lxml —— python-docx 高层不暴露
（Context7 调研确认）。

设计契约（plan §3.1）：
- run_props(rpr: Element|None) -> dict[str, str]
- 返回字典仅包含实际有值的属性（过滤 None / 空字符串）
- 全部 key 用点分命名 ('font.eastAsia' / 'font.size_half_points')
"""

from __future__ import annotations

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RPR = f"{{{W_NS}}}rPr"
RFONTS = f"{{{W_NS}}}rFonts"
SZ = f"{{{W_NS}}}sz"
B = f"{{{W_NS}}}b"
I = f"{{{W_NS}}}i"
U = f"{{{W_NS}}}u"


def _w(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def run_props(rpr: etree._Element | None) -> dict[str, str]:
    """Extract run properties from <w:rPr>; return point-dotted dict.

    >>> run_props(None)
    {}
    """
    if rpr is None:
        return {}

    result: dict[str, str] = {}

    # 字体 (w:rFonts 元素的 4 个属性)
    rfonts = rpr.find(RFONTS)
    if rfonts is not None:
        for sub, dotted in (
            ("ascii", "font.ascii"),
            ("eastAsia", "font.eastAsia"),
            ("hAnsi", "font.hAnsi"),
            ("cs", "font.cs"),
        ):
            v = rfonts.get(_w(sub))
            if v:
                result[dotted] = v

    # 字号 (w:sz w:val=半磅)
    sz = rpr.find(SZ)
    if sz is not None:
        v = sz.get(_w("val"))
        if v:
            result["font.size_half_points"] = v

    # 加粗
    if rpr.find(B) is not None:
        result["font.bold"] = "true"

    # 斜体
    if rpr.find(I) is not None:
        result["font.italic"] = "true"

    # 下划线
    u = rpr.find(U)
    if u is not None:
        v = u.get(_w("val"))
        if v:
            result["font.underline"] = v

    return result


__all__ = ["run_props"]
