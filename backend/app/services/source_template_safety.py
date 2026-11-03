"""Read-only safety checks for uploaded Word masters.

The source is data. It is never opened with macros, external fields, or
embedded executable parts enabled.  The checks run before any Office worker.
"""
from __future__ import annotations

import io
import posixpath
import zipfile

from lxml import etree

from app.core.extract.xml_utils import NS, qn, safe_xml_parser
from app.services.source_template_profile import ProfileError

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
_DANGEROUS_PART_PREFIXES = ("word/vbaproject", "word/activex/", "word/embeddings/")
_DANGEROUS_FIELDS = {
    "dde", "ddeauto", "includetext", "includepicture", "link", "database",
    "macrobutton", "autotext", "autotextlist", "gotobutton", "rd", "ask",
    "fillin", "compare", "private", "addressblock", "advance",
}
_SAFE_FIELDS = {
    "page", "numpages", "section", "sectionpages", "pageref", "ref", "noteref",
    "styleref", "seq", "toc", "tc", "symbol", "hyperlink",
}


def _xml(data: bytes, name: str):
    try:
        return etree.fromstring(data, safe_xml_parser())
    except etree.XMLSyntaxError as exc:
        raise ProfileError(f"母版 XML 无法安全解析：{name}") from exc


def _field_instructions(root) -> list[str]:
    values = []
    for paragraph in root.xpath(".//w:p", namespaces=NS):
        stack: list[list[str]] = []
        for node in paragraph.iter():
            if node.tag == qn("w:fldSimple"):
                values.append(node.get(qn("w:instr"), ""))
            elif node.tag == qn("w:fldChar"):
                kind = node.get(qn("w:fldCharType"), "").lower()
                if kind == "begin":
                    stack.append([])
                elif kind == "end" and stack:
                    values.append("".join(stack.pop()))
            elif node.tag == qn("w:instrText"):
                if stack:
                    stack[-1].append(node.text or "")
                elif (node.text or "").strip():
                    values.append(node.text or "")
    return values


def _audit_package(source: bytes, *, allow_generated_bindings: bool) -> None:
    if len(source) > 80 * 1024 * 1024:
        raise ProfileError("Word 母版超过 80 MB 安全上限")
    try:
        archive = zipfile.ZipFile(io.BytesIO(source))
    except zipfile.BadZipFile as exc:
        raise ProfileError("Word 母版不是有效的 DOCX 压缩包") from exc
    names = archive.namelist()
    if len(names) > 10_000 or len(set(names)) != len(names):
        raise ProfileError("Word 母版目录异常，已拒绝执行")
    infos = archive.infolist()
    if sum(item.file_size for item in infos) > 250 * 1024 * 1024:
        raise ProfileError("Word 母版解压后超过 250 MB 安全上限")
    if any(name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/") or "\\" in name for name in names):
        raise ProfileError("Word 母版包含越界部件路径")
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ProfileError("Word 母版缺少必要文档部件")
    if any(name.lower().startswith(_DANGEROUS_PART_PREFIXES) or name.lower().endswith("/vbaproject.bin")
           for name in names):
        raise ProfileError("母版包含宏或可执行嵌入对象，无法纳入安全排版流程")
    for name in names:
        if not name.endswith(".xml") and not name.endswith(".rels"):
            continue
        root = _xml(archive.read(name), name)
        if root.getroottree().docinfo.doctype:
            raise ProfileError(f"母版部件包含 DTD：{name}")
        instructions = _field_instructions(root) if name.startswith("word/") else []
        for instruction in instructions:
            normalized = instruction.strip().lstrip("\\*").strip()
            operation = normalized.split(maxsplit=1)[0].rstrip("\\").lower() if normalized else ""
            if operation in _DANGEROUS_FIELDS or operation not in _SAFE_FIELDS:
                raise ProfileError(f"母版包含不支持或外部数据域：{operation or 'unknown'}")
        if name.endswith(".rels"):
            for rel in root:
                if rel.get("TargetMode") == "External" and rel.get("Type") != HYPERLINK:
                    raise ProfileError("母版包含非超链接外部关系，无法安全排版")
    if not allow_generated_bindings:
        # A pre-existing PaperGuard tag/receipt would make source bindings ambiguous.
        for name in names:
            if name.startswith("word/") and name.endswith(".xml"):
                root = _xml(archive.read(name), name)
                if root.xpath(".//w:tag[starts-with(@w:val, 'pg-')]", namespaces=NS):
                    raise ProfileError("母版已包含保留的 PaperGuard 填充标记，请先使用原始母版")
        if "customXml/paperguard-layout.json" in names:
            raise ProfileError("母版已包含排版证据，不能重复作为新母版")


def audit_source_package(source: bytes) -> None:
    """Reject active content and reserved bindings in an uploaded master."""
    _audit_package(source, allow_generated_bindings=False)


def audit_generated_package(source: bytes) -> None:
    """Apply the same active-content checks to a generated document.

    Generated bindings are permitted here, but do not establish document
    authenticity or grant delivery eligibility.
    """
    _audit_package(source, allow_generated_bindings=True)
