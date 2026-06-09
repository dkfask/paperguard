"""section_props — 从 <w:sectPr> 提取节（页面）属性 (点分 property dict).

借鉴 references/format-thesis-docx/scripts/extract_requirements.py:110-128
但实现**重写**。

设计契约：
- section_props(sect: Element|None) -> dict[str, str]
- 字段点分命名 ('section.page.width' / 'section.margin.top')
- 与 IR v1.1 Section dataclass 字段保持一致（IR Section 有 9 字段，本模块
  对应 w:pgSz + w:pgMar + w:cols + w:pgNumType + w:formProt 等）
"""

from __future__ import annotations

from lxml import etree

from app.core.extract.xml_utils import attr, qn


def section_props(sect: etree._Element | None) -> dict[str, str]:
    """Extract section properties from <w:sectPr>; return point-dotted dict."""
    if sect is None:
        return {}

    result: dict[str, str] = {}

    # 页面尺寸 (w:pgSz)
    pgsz = sect.find(qn("w:pgSz"))
    if pgsz is not None:
        v = attr(pgsz, "w:w")
        if v is not None:
            result["section.page.width"] = v
        v = attr(pgsz, "w:h")
        if v is not None:
            result["section.page.height"] = v
        v = attr(pgsz, "w:orient")
        if v is not None:
            result["section.orientation"] = v

    # 页边距 (w:pgMar)
    pgmar = sect.find(qn("w:pgMar"))
    if pgmar is not None:
        for sub, dotted in (
            ("top", "section.margin.top"),
            ("right", "section.margin.right"),
            ("bottom", "section.margin.bottom"),
            ("left", "section.margin.left"),
            ("header", "section.margin.header"),
            ("footer", "section.margin.footer"),
        ):
            v = pgmar.get(qn(f"w:{sub}"))
            if v:
                result[dotted] = v

    # 栏数 (w:cols)
    cols = sect.find(qn("w:cols"))
    if cols is not None:
        v = attr(cols, "w:num")
        if v is not None:
            result["section.columns"] = v

    return result


__all__ = ["section_props"]
