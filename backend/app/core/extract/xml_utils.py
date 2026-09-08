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


def safe_xml_parser() -> etree.XMLParser:
    """加固的 XML 解析器：禁用实体解析与网络访问。

    解析用户上传的 docx 部件必须用它 —— lxml 默认解析器会展开内部实体
    (可构造实体膨胀耗尽内存/CPU)。预定义实体(&amp; 等)与数字字符引用不受影响。
    每次返回新实例：lxml 解析器不适合跨线程共享。
    """
    return etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)


def parse_xml(data: bytes) -> etree._Element:
    """用加固解析器解析字节流 (resolve_entities=False, no_network=True)。"""
    return etree.fromstring(data, safe_xml_parser())  # noqa: S320 - 解析器已禁用实体与网络


def load_part(zf: zipfile.ZipFile, part_name: str) -> etree._Element | None:
    """Load OOXML part from zipfile as lxml Element; return None if part missing.

    最小 docx 可能不含 word/numbering.xml / word/comments.xml 等，缺失时返
    None 让调用方跳过（不抛 KeyError）。
    """
    try:
        with zf.open(part_name) as f:
            return parse_xml(f.read())
    except KeyError:
        return None


__all__ = ["NS", "attr", "load_part", "parse_xml", "qn", "safe_xml_parser"]
