"""Deterministic DOCX anchors for generic template rendering.

The generic renderer must not infer a replacement location from arbitrary
paragraph text.  This module recognizes explicit, reviewable anchors and
rewrites only the matching OOXML parts, preserving the rest of the source
template package (styles, headers, footers, numbering and relationships).
"""

from __future__ import annotations

import copy
import os
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable

from lxml import etree

from app.domain.documents.schemas import ContentBlock, TemplateAnchor

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
W = lambda name: f"{{{W_NS}}}{name}"

_ANCHOR_TOKEN_RE = re.compile(r"\{\{\s*(?:(section)\s*:\s*)?([A-Za-z][A-Za-z0-9_.-]*)\s*\}\}")
_MERGE_FIELD_RE = re.compile(r"\bMERGEFIELD\s+([^\\\s]+)", re.IGNORECASE)
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_INTERNAL_BOOKMARK_RE = re.compile(r"^(?:_Toc|_GoBack|_Hlk)", re.IGNORECASE)
_SKIP_PARTS = {"word/styles.xml", "word/numbering.xml", "word/settings.xml", "word/fontTable.xml", "word/theme/theme1.xml"}


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


def _section_block_text(block: ContentBlock) -> str:
    if block.text:
        return block.text
    data = block.data if isinstance(block.data, dict) else {}
    kind = str(block.kind or "paragraph").lower()
    if kind in {"image", "figure"}:
        return str(data.get("caption") or "")
    if kind in {"table", "grid"}:
        rows = data.get("cells") or []
        return "\n".join("\t".join(str(cell) for cell in row) for row in rows)
    return ""


def _replace_section_paragraph(
    paragraph: etree._Element,
    section_key: str,
    sections: dict[str, list[ContentBlock]],
) -> bool:
    text = _visible_text(paragraph).strip()
    match = _ANCHOR_TOKEN_RE.fullmatch(text)
    if not match or not match.group(1) or match.group(2) != section_key:
        return False
    parent = paragraph.getparent()
    if parent is None:
        return False
    blocks = sections.get(section_key) or []
    rendered = [_section_block_text(block) for block in sorted(blocks, key=lambda item: (item.order, item.id))]
    rendered = [value for value in rendered if value]
    position = parent.index(paragraph)
    for value in rendered:
        clone = copy.deepcopy(paragraph)
        _clear_paragraph_text(clone, value)
        parent.insert(position, clone)
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


def render_anchored_docx(
    template_path: str | Path,
    out_path: str | Path,
    anchors: list[TemplateAnchor],
    field_values: dict[str, object],
    sections: dict[str, list[ContentBlock]],
) -> Path:
    """Render explicit anchors while preserving the original DOCX package."""
    source = Path(template_path).resolve()
    output = Path(out_path).resolve()
    if source == output:
        raise DocxAnchorError("render output must not overwrite the source template")
    if not source.is_file():
        raise DocxAnchorError("template source DOCX is missing")

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
    transformed: dict[str, bytes] = {}

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
                    if anchor.kind == "section_placeholder" and _replace_section_paragraph(paragraph, anchor.section_key or anchor.key, sections):
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
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


__all__ = ["DocxAnchorError", "extract_docx_anchors", "render_anchored_docx"]
