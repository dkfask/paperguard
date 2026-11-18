"""Import a Word manuscript into the editable project model.

This is deliberately a preview-first importer.  It keeps text, heading levels,
tables and common raster images; unsupported OOXML objects are reported instead
of being silently flattened into the paper.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from lxml import etree

IMPORT_CONFIGURATION_VERSION = "docx-project-import/5"
_MANUAL_HEADING_PATTERN = re.compile(
    r"^(?P<number>\d{1,3}(?:[.．]\d{1,3}){1,2})[\s\u3000]+(?P<title>\S.*)$"
)
_HEADING_DECISIONS = {"body", "heading1", "heading2", "heading3"}


_COMMENT_MARKUP_TAGS = {
    qn("w:commentRangeStart"),
    qn("w:commentRangeEnd"),
    qn("w:commentReference"),
}


def _table_source_xml(table: Table) -> str:
    """Keep complex table layout without carrying comments into a new package.

    Comment range markers are tied to the source document's comments.xml part.
    Project export uses the selected template package, which may not contain
    that part, so preserve the table structure while dropping those markers.
    """
    source = deepcopy(table._tbl)
    for node in list(source.iter()):
        if node.tag in _COMMENT_MARKUP_TAGS:
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
    return etree.tostring(source, encoding="unicode")


def _iter_block_items(parent: DocumentType | _Cell):
    if isinstance(parent, DocumentType):
        element = parent.element.body
    else:
        element = parent._tc
    for child in element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


_COVER_LABEL_KEYS = {
    "学生学号": "student_id", "学号": "student_id",
    "学生姓名": "author", "姓名": "author",
    "指导教师": "advisor", "导师": "advisor",
    "论文题目": "title", "题目": "title",
    "学院": "college", "专业": "major", "班级": "class_name",
    "学校名称": "school", "学校": "school", "日期": "date",
}
_COVER_DATE_PATTERN = re.compile(r"^\s*\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?\s*$")


def _cover_metadata(document: DocumentType) -> dict[str, str]:
    """Read visible cover values so importing a manuscript can round-trip its cover."""
    values: dict[str, str] = {}
    in_cover = False

    def add_text_fields(text: str) -> None:
        compact_chars: list[str] = []
        offsets: list[int] = []
        for offset, char in enumerate(text):
            if not char.isspace():
                compact_chars.append(char)
                offsets.append(offset)
        compact = "".join(compact_chars)
        matches = list(re.finditer(r"(学生学号|学生姓名|学号|姓名|指导教师|导师|论文题目|题目|学院|专业|班级|学校名称|学校|日期)[：:]", compact))
        for index, match in enumerate(matches):
            key = _COVER_LABEL_KEYS[match.group(1)]
            value_start = offsets[match.end() - 1] + 1
            value_end = offsets[matches[index + 1].start()] if index + 1 < len(matches) else len(text)
            value = text[value_start:value_end].strip()
            if value:
                values[key] = value
        if "date" not in values and _COVER_DATE_PATTERN.match(text):
            values["date"] = re.sub(r"\s+", "", text)

    def label_key(text: str) -> str:
        compact = re.sub(r"\s+", "", text).rstrip("：:")
        for label, key in _COVER_LABEL_KEYS.items():
            if compact == label:
                return key
        return ""

    for item in _iter_block_items(document):
        if isinstance(item, Paragraph):
            text = (item.text or "").strip()
            if _is_cover_marker(text):
                in_cover = True
            if not in_cover:
                continue
            if _is_contents_heading(text) or _front_matter_section_type(text) or _heading_level(item):
                break
            add_text_fields(item.text or "")
        elif in_cover:
            for row in item.rows:
                cells = row.cells
                if len(cells) < 2:
                    continue
                key = label_key(cells[0].text)
                value = (cells[1].text or "").strip()
                if key and value:
                    values[key] = value
    return values


def _heading_level(paragraph: Paragraph) -> int:
    explicit = paragraph._p.find(qn("w:pPr") + "/" + qn("w:outlineLvl"))
    style = paragraph.style
    seen = set()
    while True:
        if explicit is not None:
            value = int(explicit.get(qn("w:val"), "9"))
            if value == 9: return 0
            level = value + 1
            break
        if style is None or style.style_id in seen:
            return 0
        seen.add(style.style_id)
        named = re.fullmatch(r"(?:heading|标题)\s*([1-9])", style.name or "", re.IGNORECASE)
        if named:
            level = int(named.group(1))
            break
        explicit = style.element.find(qn("w:pPr") + "/" + qn("w:outlineLvl"))
        style = style.base_style
    if not 1 <= level <= 3:
        raise ValueError(f"论文包含当前模板流程尚不支持的 {level} 级标题：{paragraph.text}")
    return level


class _HeadingNumbering:
    """Materialize visible decimal heading labels stored in numbering.xml."""
    def __init__(self, document):
        try:
            self.root = document.part.numbering_part.element
        except (KeyError, NotImplementedError):
            self.root = None
        self.counters = {}

    def text(self, paragraph, text, is_heading):
        number = paragraph._p.find(qn("w:pPr") + "/" + qn("w:numPr"))
        style = paragraph.style
        seen = set()
        while number is None and style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            number = style.element.find(qn("w:pPr") + "/" + qn("w:numPr"))
            style = style.base_style
        if number is None:
            return text
        num_id = number.find(qn("w:numId"))
        if num_id is None or num_id.get(qn("w:val")) == "0":
            return text
        num_id = num_id.get(qn("w:val"))
        if self.root is None or not re.fullmatch(r"[0-9]{1,10}", num_id or ""):
            raise ValueError("自动标题编号定义无效")
        level = number.find(qn("w:ilvl"))
        level = int(level.get(qn("w:val"), "0")) if level is not None else 0
        if not 0 <= level <= 8:
            raise ValueError("自动标题编号层级无效")
        nums = self.root.xpath(f'./w:num[@w:numId="{num_id}"]')
        if not nums:
            raise ValueError("自动标题编号引用的定义不存在")
        abstract = nums[0].find(qn("w:abstractNumId"))
        abstract_id = abstract.get(qn("w:val")) if abstract is not None else ""
        if not re.fullmatch(r"[0-9]{1,10}", abstract_id):
            raise ValueError("自动标题编号引用无效")
        definitions = self.root.xpath(f'./w:abstractNum[@w:abstractNumId="{abstract_id}"]/w:lvl')
        definitions = {int(n.get(qn("w:ilvl"))): n for n in definitions}
        definition = definitions.get(level)
        if definition is None:
            raise ValueError("自动标题编号缺少对应层级")
        start = definition.find(qn("w:start"))
        first = int(start.get(qn("w:val"), "1")) if start is not None else 1
        overrides = nums[0].xpath(f'./w:lvlOverride[@w:ilvl="{level}"]/w:startOverride/@w:val')
        if overrides: first = int(overrides[0])
        values = self.counters.setdefault(num_id, {})
        for higher in range(level):
            if higher not in values and higher in definitions:
                start_node = definitions[higher].find(qn("w:start"))
                start_value = int(start_node.get(qn("w:val"), "1")) if start_node is not None else 1
                override = nums[0].xpath(f'./w:lvlOverride[@w:ilvl="{higher}"]/w:startOverride/@w:val')
                values[higher] = int(override[0]) if override else start_value
        values[level] = values.get(level, first - 1) + 1
        for deeper in list(values):
            if deeper > level: values.pop(deeper)
        if not is_heading:
            return text
        pattern = definition.find(qn("w:lvlText"))
        if pattern is None:
            raise ValueError("自动标题编号缺少显示格式")
        label = pattern.get(qn("w:val"), "")
        for token in re.findall(r"%([1-9])", label):
            index = int(token) - 1
            item = definitions.get(index)
            fmt = item.find(qn("w:numFmt")) if item is not None else None
            if fmt is None or fmt.get(qn("w:val")) != "decimal" or index not in values:
                raise ValueError("此自动标题编号格式需要核对后导入：" + text)
            label = label.replace("%" + token, str(values[index]))
        suffix = definition.find(qn("w:suff"))
        separator = "" if suffix is not None and suffix.get(qn("w:val")) == "nothing" else " "
        return label + separator + text


def _special_section_type(text: str) -> str | None:
    normalized = "".join(str(text).split()).lower()
    if "参考文献" in normalized or "references" == normalized or "bibliography" in normalized:
        return "references"
    if "致谢" in normalized or "acknowledgement" in normalized or "acknowledgment" in normalized:
        return "acknowledgement"
    if "附录" in normalized or normalized == "appendix":
        return "appendix"
    return None


def _front_matter_section_type(text: str) -> str | None:
    normalized = "".join(str(text).split()).rstrip("：:").casefold()
    if normalized in {"摘要", "中文摘要"}:
        return "abstract"
    if normalized in {"abstract", "英文摘要"}:
        return "english_abstract"
    return None


def _is_contents_heading(text: str) -> bool:
    normalized = "".join(str(text).split()).casefold()
    return normalized in {"目录", "contents", "tableofcontents"}


def _is_cover_marker(text: str) -> bool:
    normalized = "".join(str(text).split())
    return "毕业设计报告" in normalized or normalized.startswith(("题目：", "题目:"))


def _images_from_drawing(paragraph: Paragraph, element):
    """Yield each referenced picture, including repeated uses of one media part."""
    for drawing in element.xpath(".//wp:inline | .//wp:anchor"):
        extent = next(iter(drawing.xpath("./wp:extent")), None)
        width_cm = height_cm = None
        if extent is not None:
            try:
                width_cm = int(extent.get("cx")) / 360_000
                height_cm = int(extent.get("cy")) / 360_000
            except (TypeError, ValueError):
                width_cm = height_cm = None
        for blip in drawing.xpath(".//a:blip"):
            relationship_id = blip.get(qn("r:embed"))
            relation = paragraph.part.rels.get(relationship_id) if relationship_id else None
            if relation is None or relation.is_external:
                yield None
                continue
            part = relation.target_part
            content_type = getattr(part, "content_type", "")
            extension = {
                "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
            }.get(content_type)
            yield (part.blob, extension, width_cm, height_cm) if extension else None


def _paragraph_alignment_name(paragraph: Paragraph) -> str | None:
    alignment = paragraph.alignment
    return alignment.name.lower() if alignment is not None else None


def _inline_style(run: Run) -> dict[str, Any]:
    vert_align = None
    if run.font.superscript:
        vert_align = "superscript"
    elif run.font.subscript:
        vert_align = "subscript"
    # Word writes an explicit clear value that python-docx's color enum omits.
    highlight = run._element.find(f"{qn('w:rPr')}/{qn('w:highlight')}")
    highlight_value = (
        run.font.highlight_color
        if highlight is not None and highlight.get(qn("w:val")) != "none"
        else None
    )
    return {
        "bold": bool(run.bold),
        "italic": bool(run.italic),
        "underline": bool(run.underline),
        "highlight": getattr(highlight_value, "name", "").lower() or None,
        "vert_align": vert_align,
    }


def _append_inline_text(runs: list[dict[str, Any]], text: str,
                        style: dict[str, Any]) -> None:
    if not text:
        return
    if runs and all(runs[-1].get(key) == value for key, value in style.items()):
        runs[-1]["text"] += text
        return
    runs.append({"text": text, **style})


def _paragraph_blocks(paragraph: Paragraph, block_prefix: str, unsupported: list[str],
                      source_location: str) -> list[dict[str, Any]]:
    from app.services.docx_smartart import DGM, GRAPHIC_DATA, WP, read_inline_smartart

    blocks: list[dict[str, Any]] = []
    inline_runs: list[dict[str, Any]] = []
    image_index = 0

    def flush_text() -> None:
        if not inline_runs:
            return
        text = "".join(run["text"] for run in inline_runs)
        if text:
            block = {"id": f"{block_prefix}-{uuid4().hex[:8]}", "text": text,
                     "_source_location": source_location}
            if any(run["bold"] or run["italic"] or run["underline"] or run["highlight"] or run["vert_align"]
                   for run in inline_runs):
                block["inline_runs"] = [dict(run) for run in inline_runs]
            blocks.append(block)
        inline_runs.clear()

    def append_equation(equation) -> None:
        flush_text()
        alt_text = "".join(equation.itertext()).strip()
        blocks.append({
            "id": f"{block_prefix}-equation-{uuid4().hex[:8]}",
            "type": "equation",
            "equation": {
                "omml": etree.tostring(equation, encoding="unicode"),
                "alt_text": alt_text,
            },
            "_source_location": source_location,
        })

    def append_run(element) -> None:
        nonlocal image_index
        run = Run(element, paragraph)
        style = _inline_style(run)
        for child in element.iterchildren():
            if child.tag == qn("w:t"):
                _append_inline_text(inline_runs, child.text or "", style)
            elif child.tag == qn("w:tab"):
                _append_inline_text(inline_runs, "\t", style)
            elif child.tag in {qn("w:br"), qn("w:cr")}:
                _append_inline_text(inline_runs, "\n", style)
            elif child.tag == qn("w:drawing"):
                diagram = next((node for node in child.iter(GRAPHIC_DATA) if node.get("uri") == DGM), None)
                if diagram is not None:
                    def resolve(identifier, relation_type, content_type):
                        relation = paragraph.part.rels.get(identifier)
                        if relation is None or relation.is_external or relation.reltype != relation_type:
                            raise ValueError("SmartArt 存在不可核验的关系")
                        part = relation.target_part
                        if part.content_type != content_type or not str(part.partname).startswith("/word/diagrams/"):
                            raise ValueError("SmartArt 部件类型错误")
                        if part.rels:
                            raise ValueError("SmartArt 部件包含额外关系")
                        return part.blob

                    try:
                        smartart, _ = read_inline_smartart(child, resolve)
                        extent = child.find(f"{{{WP}}}inline/{{{WP}}}extent")
                        width, height = [int(extent.get(axis, "0")) / 360000 for axis in ("cx", "cy")]
                        if not (0 < width <= 100 and 0 < height <= 100):
                            raise ValueError("SmartArt 尺寸无效")
                    except (AttributeError, ValueError, etree.XMLSyntaxError):
                        unsupported.append("含有无法完整导入的 SmartArt")
                        continue
                    image_index += 1
                    flush_text()
                    blocks.append({
                        "id": f"{block_prefix}-image-{uuid4().hex[:8]}",
                        "type": "image",
                        "image": {"url": "", "caption": "", "width_cm": width, "height_cm": height,
                                  "alignment": _paragraph_alignment_name(paragraph), "smartart": smartart},
                        "_source_location": f"{source_location}/image[{image_index}]",
                    })
                    continue
                for image in _images_from_drawing(paragraph, child):
                    image_index += 1
                    if image is None:
                        unsupported.append("含有不支持的图片格式或外部链接")
                        continue
                    data, extension, width_cm, height_cm = image
                    flush_text()
                    blocks.append({
                        "id": f"{block_prefix}-image-{uuid4().hex[:8]}",
                        "type": "image",
                        "image": {
                            "url": "", "caption": "", "width_cm": width_cm,
                            "height_cm": height_cm,
                            "alignment": _paragraph_alignment_name(paragraph),
                        },
                        "_image_data": data, "_image_extension": extension,
                        "_source_location": f"{source_location}/image[{image_index}]",
                    })
            elif child.tag == qn("m:oMath"):
                append_equation(child)

    # ``paragraph.runs`` omits runs inside ``w:hyperlink``. Walk direct
    # paragraph children to retain link text in its original position. Text
    # runs are grouped into one project block so a Word paragraph stays one
    # paragraph when the project is exported again.
    for child in paragraph._element.iterchildren():
        if child.tag == qn("w:hyperlink"):
            linked_runs = child.xpath(".//w:r")
            if linked_runs:
                for linked_run in linked_runs:
                    append_run(linked_run)
            else:
                text = "".join(node.text or "" for node in child.iter(qn("w:t")))
                _append_inline_text(inline_runs, text, {
                    "bold": False, "italic": False, "underline": False,
                    "highlight": None, "vert_align": None,
                })
            continue
        if child.tag in {qn("m:oMath"), qn("m:oMathPara")}:
            equations = [child] if child.tag == qn("m:oMath") else child.xpath(".//m:oMath")
            for equation in equations:
                append_equation(equation)
            continue
        if child.tag != qn("w:r"):
            continue
        append_run(child)
    flush_text()
    if not blocks and (paragraph.text or "").strip():
        blocks.append({"id": f"{block_prefix}-{uuid4().hex[:8]}", "text": paragraph.text,
                       "_source_location": source_location})
    return blocks


def _manual_heading_candidate(paragraph: Paragraph, text: str, position: int) -> dict[str, Any] | None:
    match = _MANUAL_HEADING_PATTERN.fullmatch(text)
    if not match or len(match.group("title")) > 60 or re.search(r"[。；;！？!?]$", text):
        return None
    # Images and equations must remain separate objects instead of becoming a title.
    if paragraph._element.xpath(".//a:blip | .//m:oMath | .//w:drawing | .//w:object"):
        return None
    return {
        "id": f"paragraph-{position}",
        "position": position,
        "text": text,
        "suggested": f"heading{len(re.split(r'[.．]', match.group('number')))}",
    }


def _bind_adjacent_captions(sections: list[dict[str, Any]]) -> None:
    """Bind only unambiguous, adjacent source captions to their objects."""
    pattern = re.compile(r"^\s*([图表])\s*\d+(?:[.．-]\d+)+\s*(\S.*?)\s*$")
    for section in sections:
        blocks = section.get("paragraphs", [])
        to_remove: set[int] = set()
        for index, block in enumerate(blocks):
            kind = block.get("type")
            if kind not in {"image", "table"}:
                continue
            caption_index = index + 1 if kind == "image" else index - 1
            if caption_index < 0 or caption_index >= len(blocks) or caption_index in to_remove:
                continue
            caption = blocks[caption_index]
            if caption.get("type", "paragraph") != "paragraph" or caption.get("inline_runs"):
                continue
            match = pattern.fullmatch(caption.get("text", ""))
            if not match or match.group(1) != ("图" if kind == "image" else "表"):
                continue
            title = match.group(2)
            if len(title) > 100 or re.search(r"[。；;！？!?]", title) or re.match(r"(?:是|为|显示|展示|表示|可以|说明)", title):
                continue
            distance = caption.get("_source_body_index", -1000) - block.get("_source_body_index", 1000)
            if distance != (1 if kind == "image" else -1):
                continue
            if kind == "image":
                parent = block.get("_source_location", "").split("/image[", 1)[0]
                if sum(b.get("type") == "image" and b.get("_source_location", "").split("/image[", 1)[0] == parent for b in blocks) != 1:
                    continue
            block[kind]["caption"] = caption["text"]
            block["_caption_source_location"] = caption["_source_location"]
            to_remove.add(caption_index)
        section["paragraphs"] = [block for i, block in enumerate(blocks) if i not in to_remove]


def import_docx_project(data: bytes, heading_decisions: dict[str, str] | None = None) -> dict[str, Any]:
    decisions = heading_decisions or {}
    if not isinstance(decisions, dict) or any(
        not isinstance(key, str) or value not in _HEADING_DECISIONS
        for key, value in decisions.items()
    ):
        raise ValueError("手工标题确认内容无效，请重新预览")
    document = Document(BytesIO(data))
    numbering = _HeadingNumbering(document)
    sections: list[dict[str, Any]] = []
    unsupported: list[str] = []
    heading_candidates: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_cover = False
    in_contents = False
    paragraph_count = 0
    table_count = 0
    image_count = 0
    equation_count = 0

    def ensure_section() -> dict[str, Any]:
        nonlocal current
        if current is None:
            current = {"key": "import-body", "type": "body", "title": "导入正文", "required": False, "paragraphs": []}
            sections.append(current)
        return current

    for body_index, item in enumerate(_iter_block_items(document)):
        if isinstance(item, Paragraph):
            paragraph_count += 1
            source_location = f"word/document.xml#body/p[{paragraph_count}]"
            text = (item.text or "").strip()
            level = _heading_level(item)
            text = numbering.text(item, text, bool(level))
            style = (item.style.name if item.style else "").strip().lower()

            # A manuscript's cover is represented by the project's cover form.
            # Drop its free-form page text when an explicit cover is detected,
            # then route unstyled abstract labels into their own required sections.
            if text and _is_cover_marker(text):
                sections.clear()
                current = None
                in_cover = True
                in_contents = False
                continue

            if _is_contents_heading(text):
                in_contents = True
                current = None
                continue
            if in_contents:
                if level and not style.startswith("toc"):
                    in_contents = False
                    in_cover = False
                else:
                    continue
            if style.startswith("toc"):
                continue

            front_matter_type = _front_matter_section_type(text) if text else None
            if in_cover and not level and not front_matter_type:
                continue
            if not level and text and not front_matter_type:
                candidate = _manual_heading_candidate(item, text, paragraph_count)
                if candidate:
                    decision = decisions.get(candidate["id"])
                    candidate["decision"] = decision
                    heading_candidates.append(candidate)
                    if decision and decision != "body":
                        level = int(decision[-1])
            if level:
                in_cover = False

            if front_matter_type:
                in_cover = False
                current = {
                    "key": front_matter_type if not any(section.get("key") == front_matter_type for section in sections) else f"import-heading-{len(sections) + 1}",
                    "type": front_matter_type, "title": text, "required": False, "paragraphs": [],
                    "_source_location": source_location,
                }
                sections.append(current)
                continue

            if level and text:
                special_type = _special_section_type(text)
                key = special_type if special_type and not any(section.get("key") == special_type for section in sections) else f"import-heading-{len(sections) + 1}"
                current = {
                    "key": key, "type": special_type or f"heading{level}",
                    "title": text, "required": False, "paragraphs": [],
                    "_source_location": source_location,
                }
                sections.append(current)
                continue
            blocks = _paragraph_blocks(item, f"import-p-{paragraph_count + 1}", unsupported, source_location)
            image_count += sum(1 for block in blocks if block.get("type") == "image")
            equation_count += sum(1 for block in blocks if block.get("type") == "equation")
            if blocks:
                for block in blocks:
                    block["_source_body_index"] = body_index
                ensure_section()["paragraphs"].extend(blocks)
        else:
            table_count += 1
            if in_cover or in_contents:
                continue
            rows = [[cell.text for cell in row.cells] for row in item.rows]
            if rows:
                from app.services.table_borders import observe_table_borders, borderless_table_borders
                try:
                    borders = observe_table_borders(item)
                    if borders is None:
                        borders = borderless_table_borders()
                    border_error = ""
                except ValueError as exc:
                    borders = None
                    border_error = str(exc)
                    unsupported.append(border_error)
                ensure_section()["paragraphs"].append({
                    "id": f"import-table-{table_count}", "type": "table",
                    "_source_location": f"word/document.xml#body/tbl[{table_count}]",
                    "_source_body_index": body_index,
                    "_border_error": border_error,
                    "table": {
                        "rows": len(rows), "cols": max(len(row) for row in rows), "cells": rows, "caption": "",
                        "source_xml": _table_source_xml(item), "borders": borders,
                    },
                })

    _bind_adjacent_captions(sections)
    properties = document.core_properties
    metadata = {"title": properties.title or "", "author": properties.author or ""}
    metadata.update(_cover_metadata(document))
    if set(decisions) - {candidate["id"] for candidate in heading_candidates}:
        raise ValueError("标题确认项与当前 Word 文件不匹配，请重新预览")
    return {
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "configuration_version": IMPORT_CONFIGURATION_VERSION,
        "heading_decisions": decisions,
        "heading_candidates": heading_candidates,
        "sections": sections,
        "metadata": metadata,
        "stats": {
            "paragraphs": paragraph_count, "tables": table_count, "images": image_count, "equations": equation_count,
            "section_count": len(sections), "unsupported_count": len(unsupported),
        },
        "warnings": list(dict.fromkeys(unsupported)),
    }


def import_candidate_sha256(imported: dict[str, Any]) -> str:
    """Bind the candidate's content, including image bytes, without transient block IDs."""
    candidate = deepcopy(imported)
    for section in candidate["sections"]:
        for block in section.get("paragraphs", []):
            block.pop("id", None)
            image_data = block.pop("_image_data", None)
            if image_data is not None:
                block["_image_sha256"] = hashlib.sha256(image_data).hexdigest()
    encoded = json.dumps(candidate, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def public_import_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove transient image bytes before sending a preview to the client."""
    result = []
    for section in sections:
        copied = {key: value for key, value in section.items() if key != "paragraphs" and not key.startswith("_")}
        paragraphs = []
        for block in section.get("paragraphs", []):
            public_block = {key: value for key, value in block.items() if not key.startswith("_")}
            if public_block.get("type") == "table" and isinstance(public_block.get("table"), dict):
                public_block["table"] = {
                    key: value for key, value in public_block["table"].items() if key != "source_xml"
                }
                public_block["table"]["source_preserved"] = bool(block.get("table", {}).get("source_xml"))
            if public_block.get("type") == "image" and (public_block.get("image") or {}).get("smartart"):
                public_block["image"] = {key: value for key, value in public_block["image"].items() if key != "smartart"}
                public_block["image"]["source_preserved"] = True
            paragraphs.append(public_block)
        copied["paragraphs"] = paragraphs
        result.append(copied)
    return result


def imported_image_bytes(sections: list[dict[str, Any]]):
    for section in sections:
        for block in section.get("paragraphs", []):
            data = block.get("_image_data")
            if data:
                yield block, data, block.get("_image_extension", Path("image.png").suffix)
