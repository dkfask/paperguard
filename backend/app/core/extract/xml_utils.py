"""XML 工具 — Sprint 2.5 抽取子包基础.

基于 OOXML W3 namespace + lxml 集成。
借鉴 references/format-thesis-docx/scripts/extract_requirements.py:27-33
（qn + attr 模式），**实现重写** + 增加 load_part 兜底（缺失 part 返 None 不抛）。

设计契约（docs/sprints/sprint-2.5-plan.md §3.1）：
- qn(tag: str) -> str: 'w:rFonts' -> Clark notation '{...}rFonts'
- attr(el, name: str) -> str|None: 安全取属性，el 为 None 也安全
- load_part(zf, name: str) -> Element|None: 加载 zipfile 内 OOXML part，缺失返 None
- NS: dict 含 w / w14 / r 三个 OOXML namespace
"""

from __future__ import annotations

import zipfile

from lxml import etree

# OOXML W3 namespaces（参考项目 line 27-31）
NS: dict[str, str] = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def qn(tag: str) -> str:
    """Convert prefixed tag 'w:rFonts' to Clark notation '{ns}rFonts'.

    >>> qn("w:rFonts")
    '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts'
    """
    if ":" not in tag:
        return tag
    prefix, local = tag.split(":", 1)
    ns = NS.get(prefix)
    if ns is None:
        raise ValueError(f"Unknown namespace prefix: {prefix!r}")
    return f"{{{ns}}}{local}"


def attr(el: etree._Element | None, name: str) -> str | None:
    """Get attribute by prefixed name (e.g. 'w:val'); return None if missing or el is None.

    >>> attr(None, "w:foo")  # 不抛错
    None
    """
    if el is None:
        return None
    return el.get(qn(name))


def load_part(zf: zipfile.ZipFile, part_name: str) -> etree._Element | None:
    """Load OOXML part from zipfile as lxml Element; return None if part missing.

    最小 docx 可能不含 word/numbering.xml / word/comments.xml 等，缺失时返
    None 让调用方跳过（不抛 KeyError）。
    """
    try:
        with zf.open(part_name) as f:
            return etree.fromstring(f.read())
    except KeyError:
        return None


__all__ = ["NS", "qn", "attr", "load_part"]
