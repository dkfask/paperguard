"""Deterministic DOCX anchors for generic template rendering.

The generic renderer must not infer a replacement location from arbitrary
paragraph text.  This module recognizes explicit, reviewable anchors and
rewrites only the matching OOXML parts, preserving the rest of the source
template package (styles, headers, footers, numbering and relationships).
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Iterable

from docx.image.image import Image as DocxImage
from lxml import etree

from app.domain.documents.schemas import ContentBlock, TemplateAnchor

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
W = lambda name: f"{{{W_NS}}}{name}"
M = lambda name: f"{{{M_NS}}}{name}"
REL = lambda name: f"{{{REL_NS}}}{name}"
R = lambda name: f"{{{R_NS}}}{name}"
WP = lambda name: f"{{{WP_NS}}}{name}"
A = lambda name: f"{{{A_NS}}}{name}"
PIC = lambda name: f"{{{PIC_NS}}}{name}"

_ANCHOR_TOKEN_RE = re.compile(r"\{\{\s*(?:(section)\s*:\s*)?([A-Za-z][A-Za-z0-9_.-]*)\s*\}\}")
_MERGE_FIELD_RE = re.compile(r"\bMERGEFIELD\s+([^\\\s]+)", re.IGNORECASE)
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_INTERNAL_BOOKMARK_RE = re.compile(r"^(?:_Toc|_GoBack|_Hlk)", re.IGNORECASE)
_SKIP_PARTS = {"word/styles.xml", "word/numbering.xml", "word/settings.xml", "word/fontTable.xml", "word/theme/theme1.xml"}
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}
_RICH_BLOCK_KINDS = {"image", "figure", "table", "grid", "equation", "formula"}


class DocxAnchorError(ValueError):
    """Raised when an explicitly declared DOCX anchor cannot be rendered."""


def _xml_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
        remove_blank_text=False,
    )


def _xml_parts(names: Iterable[str]) -> list[str]:
    return sorted(
        name for name in names
        if name.startswith("word/")
        and name.endswith(".xml")
        and not name.endswith(".rels")
        and name not in _SKIP_PARTS
        and (
            name == "word/document.xml"
            or name.startswith("word/header")
            or name.startswith("word/footer")
            or name in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
        )
    )


def _safe_key(value: str) -> str | None:
    raw = value.strip()
    changed = True
    while changed:
        changed = False
        for prefix in ("pg:", "field:", "section:", "pg_", "field_", "section_"):
            if raw.lower().startswith(prefix.lower()):
                raw = raw[len(prefix):]
                changed = True
                break
    raw = re.sub(r"\s+", "_", raw.strip())
    return raw if _KEY_RE.match(raw) else None


def _anchor_kind_and_key(raw: str, default_kind: str = "field") -> tuple[str, str] | None:
    value = raw.strip()
    if value.lower().startswith("pg:"):
        value = value[3:]
    lowered = value.lower()
    if lowered.startswith(("section:", "section_")):
        key = _safe_key(value)
        return ("section", key) if key else None
    if lowered.startswith(("field:", "field_", "pg:", "pg_")):
        key = _safe_key(value)
        return (default_kind, key) if key else None
    key = _safe_key(value)
    return (default_kind, key) if key else None


def _text_nodes(paragraph: etree._Element) -> list[etree._Element]:
    return list(paragraph.iter(W("t")))


def _visible_text(paragraph: etree._Element) -> str:
    return "".join(node.text or "" for node in _text_nodes(paragraph))


def _next_occurrence(counts: Counter[tuple[str, str, str]], kind: str, key: str, part: str) -> int:
    identity = (kind, key, part)
    counts[identity] += 1
    return counts[identity]


def extract_docx_anchors(docx_path: str | Path) -> list[TemplateAnchor]:
    """Extract only explicit field/section anchors from supported DOCX parts."""
    path = Path(docx_path)
    anchors: list[TemplateAnchor] = []
    occurrences: Counter[tuple[str, str, str]] = Counter()
    with zipfile.ZipFile(path, "r") as archive:
        for part in _xml_parts(archive.namelist()):
            root = etree.fromstring(archive.read(part), _xml_parser())

            for paragraph in root.iter(W("p")):
                text = _visible_text(paragraph)
                for match in _ANCHOR_TOKEN_RE.finditer(text):
                    is_section = bool(match.group(1))
                    key = match.group(2)
                    kind = "section_placeholder" if is_section else "placeholder"
                    occurrence = _next_occurrence(occurrences, kind, key, part)
                    anchors.append(TemplateAnchor(
                        key=key,
                        kind=kind,
                        selector=match.group(0),
                        part=part,
                        occurrence=occurrence,
                        required=is_section,
                        section_key=key if is_section else "",
                    ))

                for instr in paragraph.iter(W("instrText")):
                    merge = _MERGE_FIELD_RE.search(instr.text or "")
                    if not merge:
                        continue
                    key = _safe_key(merge.group(1))
                    if not key:
                        continue
                    occurrence = _next_occurrence(occurrences, "merge_field", key, part)
                    anchors.append(TemplateAnchor(
                        key=key,
                        kind="merge_field",
                        selector=merge.group(1),
                        part=part,
                        occurrence=occurrence,
                    ))

                children = list(paragraph)
                for index, child in enumerate(children):
                    if child.tag != W("bookmarkStart"):
                        continue
                    name = str(child.get(W("name")) or "")
                    if not name or _INTERNAL_BOOKMARK_RE.match(name):
                        continue
                    parsed = _anchor_kind_and_key(name, "bookmark")
                    if not parsed:
                        continue
                    kind, key = parsed
                    if kind == "section":
                        anchor_kind = "section_bookmark"
                        section_key = key
                    else:
                        anchor_kind = "bookmark"
                        section_key = ""
                    if not any(
                        sibling.tag == W("bookmarkEnd")
                        and sibling.get(W("id")) == child.get(W("id"))
                        for sibling in children[index + 1:]
                    ):
                        continue
                    occurrence = _next_occurrence(occurrences, anchor_kind, key, part)
                    anchors.append(TemplateAnchor(
                        key=key,
                        kind=anchor_kind,
                        selector=name,
                        part=part,
                        occurrence=occurrence,
                        required=bool(section_key),
                        section_key=section_key,
                    ))

            for sdt in root.iter(W("sdt")):
                tag = sdt.find(f"./{W('sdtPr')}/{W('tag')}")
                alias = sdt.find(f"./{W('sdtPr')}/{W('alias')}")
                selector = str((tag if tag is not None else alias).get(W("val")) if (tag is not None or alias is not None) else "")
                parsed = _anchor_kind_and_key(selector)
                if not parsed:
                    continue
                kind, key = parsed
                anchor_kind = "section_content_control" if kind == "section" else "content_control"
                section_key = key if kind == "section" else ""
                occurrence = _next_occurrence(occurrences, anchor_kind, key, part)
                anchors.append(TemplateAnchor(
                    key=key,
                    kind=anchor_kind,
                    selector=selector,
                    part=part,
                    occurrence=occurrence,
                    required=bool(section_key),
                    section_key=section_key,
                ))
    return anchors


def _set_text(node: etree._Element, value: str) -> None:
    node.text = value
    if value[:1].isspace() or value[-1:].isspace():
        node.set(f"{{{XML_NS}}}space", "preserve")
    else:
        node.attrib.pop(f"{{{XML_NS}}}space", None)


def _replace_match(nodes: list[etree._Element], start: int, end: int, replacement: str) -> None:
    nodes = [node for node in nodes if node.text]
    offsets: list[tuple[etree._Element, int, int]] = []
    cursor = 0
    for node in nodes:
        text = node.text or ""
        offsets.append((node, cursor, cursor + len(text)))
        cursor += len(text)
    start_item = next((item for item in offsets if item[1] <= start < item[2] or (start == item[1] == item[2])), None)
    end_item = next((item for item in offsets if item[1] < end <= item[2] or (end == item[1] == item[2])), None)
    if start_item is None or end_item is None:
        raise DocxAnchorError("DOCX anchor text range is invalid")
    start_node, start_begin, _ = start_item
    end_node, end_begin, _ = end_item
    start_text = start_node.text or ""
    end_text = end_node.text or ""
    prefix = start_text[:start - start_begin]
    suffix = end_text[end - end_begin:]
    if start_node is end_node:
        _set_text(start_node, prefix + replacement + suffix)
        return
    _set_text(start_node, prefix + replacement)
    seen_start = False
    for node, _, _ in offsets:
        if node is start_node:
            seen_start = True
            continue
        if node is end_node:
            break
        if seen_start:
            _set_text(node, "")
    _set_text(end_node, suffix)


def _replace_allowed_tokens(paragraph: etree._Element, values: dict[str, str], allowed: set[str]) -> Counter[str]:
    replacements: Counter[str] = Counter()
    nodes = _text_nodes(paragraph)
    text = "".join(node.text or "" for node in nodes)
    matches = [item for item in _ANCHOR_TOKEN_RE.finditer(text) if item.group(0) in allowed]
    # Work from right to left so replacement text is never parsed as a new
    # instruction and offsets before a replacement remain stable.
    for match in reversed(matches):
        nodes = _text_nodes(paragraph)
        key = match.group(2)
        replacement = values.get(f"section:{key}", values.get(key, ""))
        _replace_match(nodes, match.start(), match.end(), replacement)
        replacements[match.group(0)] += 1
    return replacements


def _clear_paragraph_text(paragraph: etree._Element, text: str) -> None:
    nodes = _text_nodes(paragraph)
    if nodes:
        _set_text(nodes[0], text)
        for node in nodes[1:]:
            _set_text(node, "")
        return
    run = etree.Element(W("r"))
    text_node = etree.SubElement(run, W("t"))
    _set_text(text_node, text)
    paragraph.append(run)


def _clear_paragraph_content(paragraph: etree._Element) -> None:
    """Keep paragraph properties while replacing its authored content."""
    for child in list(paragraph):
        if child.tag != W("pPr"):
            paragraph.remove(child)


def _block_kind(block: ContentBlock) -> str:
    return str(block.kind or "paragraph").strip().lower()


def _section_block_text(block: ContentBlock) -> str:
    if block.text:
        return block.text
    data = block.data if isinstance(block.data, dict) else {}
    kind = _block_kind(block)
    if kind in {"image", "figure"}:
        return str(data.get("caption") or "")
    if kind in {"table", "grid"}:
        rows = data.get("cells") or []
        return "\n".join("\t".join(str(cell) for cell in row) for row in rows)
    return ""


def _safe_omml(block: ContentBlock) -> etree._Element | None:
    data = block.data if isinstance(block.data, dict) else {}
    raw = str(data.get("omml") or "")
    if not raw:
        return None
    try:
        node = etree.fromstring(raw.encode("utf-8"), _xml_parser())
    except (UnicodeEncodeError, etree.XMLSyntaxError) as exc:
        raise DocxAnchorError(f"公式块 {block.id} 的 Office Math XML 无效") from exc
    if etree.QName(node).namespace != M_NS or etree.QName(node).localname not in {"oMath", "oMathPara"}:
        raise DocxAnchorError(f"公式块 {block.id} 的根节点不是 Office Math")
    if etree.QName(node).localname == "oMathPara":
        math = next((item for item in node if item.tag == M("oMath")), None)
        if math is None:
            raise DocxAnchorError(f"公式块 {block.id} 不包含可渲染的 Office Math")
        return copy.deepcopy(math)
    return node


def _append_text(parent: etree._Element, text: str) -> None:
    """Append text with explicit breaks so newlines survive OOXML insertion."""
    run = etree.SubElement(parent, W("r"))
    pieces = str(text).split("\n")
    for index, piece in enumerate(pieces):
        if index:
            etree.SubElement(run, W("br"))
        text_node = etree.SubElement(run, W("t"))
        _set_text(text_node, piece)


def _table_node(block: ContentBlock) -> etree._Element:
    data = block.data if isinstance(block.data, dict) else {}
    try:
        rows = int(data.get("rows") or 1)
        cols = int(data.get("cols") or 1)
    except (TypeError, ValueError) as exc:
        raise DocxAnchorError(f"表格块 {block.id} 的行列数无效") from exc
    cells = data.get("cells") or []
    if rows < 1 or cols < 1 or rows > 100 or cols > 100:
        raise DocxAnchorError(f"表格块 {block.id} 的行列数超出范围")
    if not isinstance(cells, list) or any(not isinstance(row, list) for row in cells):
        raise DocxAnchorError(f"表格块 {block.id} 的单元格数据无效")

    table = etree.Element(W("tbl"))
    properties = etree.SubElement(table, W("tblPr"))
    style = etree.SubElement(properties, W("tblStyle"))
    style.set(W("val"), "TableGrid")
    width = etree.SubElement(properties, W("tblW"))
    width.set(W("type"), "auto")
    grid = etree.SubElement(table, W("tblGrid"))
    cell_width = str(max(1, 9000 // cols))
    for _ in range(cols):
        column = etree.SubElement(grid, W("gridCol"))
        column.set(W("w"), cell_width)
    for row_index in range(rows):
        row = etree.SubElement(table, W("tr"))
        for column_index in range(cols):
            cell = etree.SubElement(row, W("tc"))
            cell_properties = etree.SubElement(cell, W("tcPr"))
            cell_width_node = etree.SubElement(cell_properties, W("tcW"))
            cell_width_node.set(W("type"), "dxa")
            cell_width_node.set(W("w"), cell_width)
            paragraph = etree.SubElement(cell, W("p"))
            value = ""
            if row_index < len(cells) and column_index < len(cells[row_index]):
                value = str(cells[row_index][column_index] or "")
            _append_text(paragraph, value)
    return table


def _image_drawing(block: ContentBlock, relationship_id: str, image_path: Path, drawing_id: int) -> etree._Element:
    try:
        image = DocxImage.from_blob(image_path.read_bytes())
        px_width = int(image.px_width)
        px_height = int(image.px_height)
    except Exception as exc:
        raise DocxAnchorError(f"图片块 {block.id} 无法被 DOCX 渲染器读取") from exc
    if px_width <= 0 or px_height <= 0:
        raise DocxAnchorError(f"图片块 {block.id} 的尺寸无效")
    max_width = 6.5 * 914400
    cx = min(int(max_width), max(1, px_width * 9525))
    cy = max(1, int(px_height * 9525 * cx / (px_width * 9525)))

    drawing = etree.Element(W("drawing"))
    inline = etree.SubElement(drawing, WP("inline"), distT="0", distB="0", distL="0", distR="0")
    etree.SubElement(inline, WP("extent"), cx=str(cx), cy=str(cy))
    etree.SubElement(inline, WP("docPr"), id=str(drawing_id), name=f"Picture {block.id}")
    graphic = etree.SubElement(inline, A("graphic"))
    graphic_data = etree.SubElement(graphic, A("graphicData"), uri="http://schemas.openxmlformats.org/drawingml/2006/picture")
    picture = etree.SubElement(graphic_data, PIC("pic"))
    non_visual = etree.SubElement(picture, PIC("nvPicPr"))
    etree.SubElement(non_visual, PIC("cNvPr"), id="0", name=image_path.name)
    etree.SubElement(non_visual, PIC("cNvPicPr"))
    fill = etree.SubElement(picture, PIC("blipFill"))
    etree.SubElement(fill, A("blip"), {R("embed"): relationship_id})
    stretch = etree.SubElement(fill, A("stretch"))
    etree.SubElement(stretch, A("fillRect"))
    shape = etree.SubElement(picture, PIC("spPr"))
    transform = etree.SubElement(shape, A("xfrm"))
    etree.SubElement(transform, A("off"), x="0", y="0")
    etree.SubElement(transform, A("ext"), cx=str(cx), cy=str(cy))
    geometry = etree.SubElement(shape, A("prstGeom"), prst="rect")
    etree.SubElement(geometry, A("avLst"))
    return drawing


def _rich_block_nodes(
    paragraph: etree._Element,
    block: ContentBlock,
    *,
    part: str,
    add_image,
    resolve_image,
    next_drawing_id,
) -> list[etree._Element]:
    kind = _block_kind(block)
    if kind not in _RICH_BLOCK_KINDS:
        clone = copy.deepcopy(paragraph)
        _clear_paragraph_text(clone, block.text or "")
        return [clone] if block.text else []
    data = block.data if isinstance(block.data, dict) else {}
    if kind in {"image", "figure"}:
        image_path = resolve_image(block)
        relationship_id = add_image(part, image_path)
        clone = copy.deepcopy(paragraph)
        _clear_paragraph_content(clone)
        run = etree.SubElement(clone, W("r"))
        run.append(_image_drawing(block, relationship_id, image_path, next_drawing_id()))
        result = [clone]
        caption = str(data.get("caption") or "")
        if caption:
            caption_node = copy.deepcopy(paragraph)
            _clear_paragraph_text(caption_node, caption)
            result.append(caption_node)
        return result
    if kind in {"table", "grid"}:
        result: list[etree._Element] = []
        caption = str(data.get("caption") or "")
        if caption:
            caption_node = copy.deepcopy(paragraph)
            _clear_paragraph_text(caption_node, caption)
            result.append(caption_node)
        result.append(_table_node(block))
        return result
    equation = _safe_omml(block)
    clone = copy.deepcopy(paragraph)
    _clear_paragraph_content(clone)
    if equation is not None:
        clone.append(equation)
    else:
        fallback = str(data.get("alt_text") or block.text or "")
        if fallback:
            _clear_paragraph_text(clone, fallback)
        else:
            return []
    return [clone]


def _replace_section_paragraph(
    paragraph: etree._Element,
    section_key: str,
    sections: dict[str, list[ContentBlock]],
    *,
    part: str,
    add_image,
    resolve_image,
    next_drawing_id,
) -> bool:
    text = _visible_text(paragraph).strip()
    match = _ANCHOR_TOKEN_RE.fullmatch(text)
    if not match or not match.group(1) or match.group(2) != section_key:
        return False
    parent = paragraph.getparent()
    if parent is None:
        return False
    blocks = sections.get(section_key) or []
    position = parent.index(paragraph)
    for block in sorted(blocks, key=lambda item: (item.order, item.id)):
        for node in _rich_block_nodes(
            paragraph,
            block,
            part=part,
            add_image=add_image,
            resolve_image=resolve_image,
            next_drawing_id=next_drawing_id,
        ):
            parent.insert(position, node)
            position += 1
    parent.remove(paragraph)
    return True


def _replace_content_control(
    sdt: etree._Element,
    selector: str,
    values: dict[str, str],
    section_values: dict[str, str],
) -> bool:
    parsed = _anchor_kind_and_key(selector)
    if not parsed:
        return False
    kind, key = parsed
    content = sdt.find(f"./{W('sdtContent')}")
    if content is None:
        return False
    if kind == "section":
        replacement = section_values.get(key, "")
    else:
        replacement = values.get(key, "")
    nodes = list(content.iter(W("t")))
    if nodes:
        _set_text(nodes[0], replacement)
        for node in nodes[1:]:
            _set_text(node, "")
        return True
    paragraph = next(content.iter(W("p")), None)
    if paragraph is None:
        return False
    _clear_paragraph_text(paragraph, replacement)
    return True


def _replace_bookmark(paragraph: etree._Element, anchor: TemplateAnchor, replacement: str) -> bool:
    children = list(paragraph)
    start = next((index for index, child in enumerate(children)
                  if child.tag == W("bookmarkStart") and child.get(W("name")) == anchor.selector), None)
    if start is None:
        return False
    end = next((index for index in range(start + 1, len(children))
                if children[index].tag == W("bookmarkEnd")
                and children[index].get(W("id")) == children[start].get(W("id"))), None)
    if end is None:
        return False
    nodes: list[etree._Element] = []
    for child in children[start + 1:end]:
        nodes.extend(child.iter(W("t")))
    if not nodes:
        return False
    _set_text(nodes[0], replacement)
    for node in nodes[1:]:
        _set_text(node, "")
    return True


def _replace_merge_field(paragraph: etree._Element, anchor: TemplateAnchor, replacement: str) -> bool:
    children = list(paragraph)
    for index, child in enumerate(children):
        instr = next(child.iter(W("instrText")), None)
        if instr is None or not _MERGE_FIELD_RE.search(instr.text or ""):
            continue
        match = _MERGE_FIELD_RE.search(instr.text or "")
        if not match or match.group(1).lower() != anchor.selector.lower():
            continue
        result_nodes: list[etree._Element] = []
        for sibling in children[index + 1:]:
            if sibling.tag == W("r") and sibling.find(f"./{W('fldChar')}[@{W('fldCharType')}='end']") is not None:
                break
            result_nodes.extend(sibling.iter(W("t")))
        if not result_nodes:
            return False
        _set_text(result_nodes[0], replacement)
        for node in result_nodes[1:]:
            _set_text(node, "")
        return True
    return False


def _relationships_part(part: str) -> str:
    source = PurePosixPath(part)
    return str(source.parent / "_rels" / f"{source.name}.rels")


def _next_relationship_id(root: etree._Element) -> str:
    ids = {str(node.get("Id") or "") for node in root if node.tag == REL("Relationship")}
    number = 1
    while f"rId{number}" in ids:
        number += 1
    return f"rId{number}"


def _next_media_name(names: set[str], suffix: str) -> str:
    numbers = []
    pattern = re.compile(r"^word/media/image(\d+)(?:\.[A-Za-z0-9]+)$")
    for name in names:
        match = pattern.match(name)
        if match:
            numbers.append(int(match.group(1)))
    return f"word/media/image{max(numbers, default=0) + 1}{suffix}"


def _ensure_content_type(root: etree._Element, suffix: str, content_type: str) -> None:
    extension = suffix.lstrip(".").lower()
    for node in root:
        if node.tag == "{http://schemas.openxmlformats.org/package/2006/content-types}Default" and str(node.get("Extension") or "").lower() == extension:
            return
    default = etree.Element("{http://schemas.openxmlformats.org/package/2006/content-types}Default")
    default.set("Extension", extension)
    default.set("ContentType", content_type)
    root.append(default)


def _guard_rich_section_anchors(
    anchors: list[TemplateAnchor],
    sections: dict[str, list[ContentBlock]],
) -> None:
    section_anchors: dict[str, set[str]] = {}
    for anchor in anchors:
        if anchor.kind in {"section_placeholder", "section_bookmark", "section_content_control"}:
            section_anchors.setdefault(anchor.section_key or anchor.key, set()).add(anchor.kind)
    for key, blocks in sections.items():
        if not any(_block_kind(block) in _RICH_BLOCK_KINDS for block in blocks):
            continue
        kinds = section_anchors.get(key, set())
        if not kinds:
            raise DocxAnchorError(
                f"章节 {key} 没有可用章节锚点, 无法安全渲染富内容块"
            )
        if kinds and "section_placeholder" not in kinds:
            names = ", ".join(sorted(kinds))
            raise DocxAnchorError(
                f"章节 {key} 包含富内容块, 但锚点类型 {names} 目前只支持文本; 请使用 {{section:{key}}} 段落锚点"
            )


def render_anchored_docx(
    template_path: str | Path,
    out_path: str | Path,
    anchors: list[TemplateAnchor],
    field_values: dict[str, object],
    sections: dict[str, list[ContentBlock]],
    image_paths: dict[str, Path] | None = None,
) -> Path:
    """Render explicit anchors while preserving the original DOCX package."""
    source = Path(template_path).resolve()
    output = Path(out_path).resolve()
    if source == output:
        raise DocxAnchorError("render output must not overwrite the source template")
    if not source.is_file():
        raise DocxAnchorError("template source DOCX is missing")

    _guard_rich_section_anchors(anchors, sections)
    value_map = {key: "" if value is None else str(value) for key, value in field_values.items()}
    section_values = {
        key: "\n".join(
            value for value in (_section_block_text(block) for block in blocks)
            if value
        )
        for key, blocks in sections.items()
    }
    expected = Counter((anchor.part, anchor.kind, anchor.selector) for anchor in anchors)
    used: Counter[tuple[str, str, str]] = Counter()
    allowed_tokens = {
        anchor.selector for anchor in anchors
        if anchor.kind == "placeholder"
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source, "r") as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    data_by_name = {info.filename: data for info, data in entries}
    transformed: dict[str, bytes] = {}
    extra_entries: dict[str, bytes] = {}
    image_targets: dict[Path, str] = {}
    relationship_roots: dict[str, etree._Element] = {}
    content_types_name = "[Content_Types].xml"
    content_types_root = etree.fromstring(data_by_name[content_types_name], _xml_parser())
    used_drawing_ids = [int(node.get("id")) for part in _xml_parts(data_by_name) for node in etree.fromstring(data_by_name[part], _xml_parser()).iter(WP("docPr")) if str(node.get("id", "")).isdigit()]
    drawing_id = max(used_drawing_ids, default=0)

    def next_drawing_id() -> int:
        nonlocal drawing_id
        drawing_id += 1
        return drawing_id

    def add_image(part: str, image_path: Path) -> str:
        path = image_path.resolve()
        suffix = path.suffix.lower()
        if suffix not in _IMAGE_TYPES or not path.is_file():
            raise DocxAnchorError("图片资源不存在或格式不受 DOCX 支持")
        rels_name = _relationships_part(part)
        if rels_name not in data_by_name:
            raise DocxAnchorError(f"DOCX 部件缺少图片关系文件: {part}")
        rels_root = relationship_roots.get(rels_name)
        if rels_root is None:
            rels_root = etree.fromstring(data_by_name[rels_name], _xml_parser())
            relationship_roots[rels_name] = rels_root
        media_name = image_targets.get(path)
        if media_name is None:
            media_name = _next_media_name(set(data_by_name) | set(extra_entries), suffix)
            image_targets[path] = media_name
            extra_entries[media_name] = path.read_bytes()
            _ensure_content_type(content_types_root, suffix, _IMAGE_TYPES[suffix])
        target = posixpath.relpath(media_name, PurePosixPath(part).parent)
        for relation in rels_root:
            if relation.tag == REL("Relationship") and relation.get("Target") == target:
                return str(relation.get("Id"))
        relationship_id = _next_relationship_id(rels_root)
        relation = etree.Element(REL("Relationship"))
        relation.set("Id", relationship_id)
        relation.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image")
        relation.set("Target", target)
        rels_root.append(relation)
        transformed[rels_name] = etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8", standalone=True)
        return relationship_id

    def image_path_for(block: ContentBlock) -> Path:
        path = (image_paths or {}).get(block.id)
        if path is None:
            raise DocxAnchorError(f"图片块 {block.id} 缺少项目图片资源")
        return path

    for part in _xml_parts(info.filename for info, _ in entries):
        raw = next(data for info, data in entries if info.filename == part)
        root = etree.fromstring(raw, _xml_parser())
        for sdt in list(root.iter(W("sdt"))):
            tag = sdt.find(f"./{W('sdtPr')}/{W('tag')}")
            alias = sdt.find(f"./{W('sdtPr')}/{W('alias')}")
            selector = str((tag if tag is not None else alias).get(W("val")) if (tag is not None or alias is not None) else "")
            matching = next((anchor for anchor in anchors if anchor.part == part and anchor.kind in {"content_control", "section_content_control"} and anchor.selector == selector), None)
            if matching is None:
                continue
            if _replace_content_control(sdt, selector, value_map, section_values):
                used[(part, matching.kind, selector)] += 1

        for paragraph in list(root.iter(W("p"))):
            for anchor in anchors:
                if anchor.part != part:
                    continue
                if anchor.kind in {"section_placeholder", "section_bookmark", "section_content_control"}:
                    if anchor.kind == "section_placeholder" and _replace_section_paragraph(
                        paragraph,
                        anchor.section_key or anchor.key,
                        sections,
                        part=part,
                        add_image=lambda current_part, path: add_image(current_part, path),
                        resolve_image=image_path_for,
                        next_drawing_id=next_drawing_id,
                    ):
                        used[(part, anchor.kind, anchor.selector)] += 1
                    elif anchor.kind == "section_bookmark" and _replace_bookmark(paragraph, anchor, section_values.get(anchor.section_key or anchor.key, "")):
                        used[(part, anchor.kind, anchor.selector)] += 1
                    continue
                if anchor.kind == "bookmark" and _replace_bookmark(paragraph, anchor, value_map.get(anchor.key, "")):
                    used[(part, anchor.kind, anchor.selector)] += 1
                elif anchor.kind == "merge_field" and _replace_merge_field(paragraph, anchor, value_map.get(anchor.key, "")):
                    used[(part, anchor.kind, anchor.selector)] += 1
            for selector, count in _replace_allowed_tokens(
                paragraph,
                value_map | {f"section:{key}": value for key, value in section_values.items()},
                allowed_tokens,
            ).items():
                used[(part, "placeholder", selector)] += count

        transformed[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    transformed[content_types_name] = etree.tostring(
        content_types_root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )

    missing = [key for key, count in expected.items() if used[key] < count]
    if missing:
        formatted = ", ".join(f"{part}:{kind}:{selector}" for part, kind, selector in missing)
        raise DocxAnchorError(f"declared DOCX anchor not found during render: {formatted}")

    fd, temporary_name = tempfile.mkstemp(
        prefix="paperguard-anchored-",
        suffix=".docx",
        dir=str(output.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as result:
            for info, data in entries:
                result.writestr(info, transformed.get(info.filename, data))
            for name, data in extra_entries.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                result.writestr(info, data)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


__all__ = ["DocxAnchorError", "extract_docx_anchors", "render_anchored_docx"]
