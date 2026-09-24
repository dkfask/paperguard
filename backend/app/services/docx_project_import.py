"""Import a Word manuscript into the editable project model.

This is deliberately a preview-first importer.  It keeps text, heading levels,
tables and common raster images; unsupported OOXML objects are reported instead
of being silently flattened into the paper.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
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
    style = (paragraph.style.name if paragraph.style else "").lower()
    if "heading 1" in style or "标题 1" in style or "标题1" in style:
        return 1
    if "heading 2" in style or "标题 2" in style or "标题2" in style:
        return 2
    if "heading 3" in style or "标题 3" in style or "标题3" in style:
        return 3
    return 0


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


def _image_from_run(paragraph: Paragraph, run) -> tuple[bytes, str, float | None, float | None] | None:
    for drawing in run._element.xpath(".//wp:inline | .//wp:anchor"):
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
            if not relationship_id:
                continue
            relation = paragraph.part.rels.get(relationship_id)
            if relation is None or not hasattr(relation, "target_part"):
                continue
            part = relation.target_part
            content_type = getattr(part, "content_type", "")
            extension = {
                "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
            }.get(content_type)
            if extension:
                return part.blob, extension, width_cm, height_cm
    return None


def _paragraph_alignment_name(paragraph: Paragraph) -> str | None:
    alignment = paragraph.alignment
    return alignment.name.lower() if alignment is not None else None


def _inline_style(run: Run) -> dict[str, Any]:
    vert_align = None
    if run.font.superscript:
        vert_align = "superscript"
    elif run.font.subscript:
        vert_align = "subscript"
    highlight_value = run.font.highlight_color
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


def _paragraph_blocks(paragraph: Paragraph, block_prefix: str, unsupported: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    inline_runs: list[dict[str, Any]] = []

    def flush_text() -> None:
        if not inline_runs:
            return
        text = "".join(run["text"] for run in inline_runs)
        if text:
            block = {"id": f"{block_prefix}-{uuid4().hex[:8]}", "text": text}
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
        })

    # ``paragraph.runs`` omits runs inside ``w:hyperlink``. Walk direct
    # paragraph children to retain link text in its original position. Text
    # runs are grouped into one project block so a Word paragraph stays one
    # paragraph when the project is exported again.
    for child in paragraph._element.iterchildren():
        if child.tag == qn("w:hyperlink"):
            linked_runs = child.xpath(".//w:r")
            if linked_runs:
                for linked_run in linked_runs:
                    run = Run(linked_run, paragraph)
                    _append_inline_text(inline_runs, run.text or "", _inline_style(run))
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
        run = Run(child, paragraph)
        text = run.text or ""
        if text:
            _append_inline_text(inline_runs, text, _inline_style(run))
        if run._element.xpath(".//a:blip"):
            image = _image_from_run(paragraph, run)
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
            })
        for equation in run._element.xpath(".//m:oMath"):
            append_equation(equation)
    flush_text()
    if not blocks and (paragraph.text or "").strip():
        blocks.append({"id": f"{block_prefix}-{uuid4().hex[:8]}", "text": paragraph.text})
    return blocks


def import_docx_project(data: bytes) -> dict[str, Any]:
    document = Document(BytesIO(data))
    sections: list[dict[str, Any]] = []
    unsupported: list[str] = []
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

    for item in _iter_block_items(document):
        if isinstance(item, Paragraph):
            paragraph_count += 1
            text = (item.text or "").strip()
            level = _heading_level(item)
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
            if level:
                in_cover = False

            if front_matter_type:
                in_cover = False
                current = {
                    "key": front_matter_type if not any(section.get("key") == front_matter_type for section in sections) else f"import-heading-{len(sections) + 1}",
                    "type": front_matter_type, "title": text, "required": False, "paragraphs": [],
                }
                sections.append(current)
                continue

            if level and text:
                special_type = _special_section_type(text)
                key = special_type if special_type and not any(section.get("key") == special_type for section in sections) else f"import-heading-{len(sections) + 1}"
                current = {
                    "key": key, "type": special_type or f"heading{level}",
                    "title": text, "required": False, "paragraphs": [],
                }
                sections.append(current)
                continue
            blocks = _paragraph_blocks(item, f"import-p-{paragraph_count + 1}", unsupported)
            image_count += sum(1 for block in blocks if block.get("type") == "image")
            equation_count += sum(1 for block in blocks if block.get("type") == "equation")
            if blocks:
                ensure_section()["paragraphs"].extend(blocks)
        else:
            table_count += 1
            if in_cover or in_contents:
                continue
            rows = [[cell.text for cell in row.cells] for row in item.rows]
            if rows:
                ensure_section()["paragraphs"].append({
                    "id": f"import-table-{table_count}", "type": "table",
                    "table": {
                        "rows": len(rows), "cols": max(len(row) for row in rows), "cells": rows, "caption": "",
                        "source_xml": _table_source_xml(item),
                    },
                })

    properties = document.core_properties
    metadata = {"title": properties.title or "", "author": properties.author or ""}
    metadata.update(_cover_metadata(document))
    return {
        "sections": sections,
        "metadata": metadata,
        "stats": {
            "paragraphs": paragraph_count, "tables": table_count, "images": image_count, "equations": equation_count,
            "section_count": len(sections), "unsupported_count": len(unsupported),
        },
        "warnings": list(dict.fromkeys(unsupported)),
    }


def public_import_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove transient image bytes before sending a preview to the client."""
    result = []
    for section in sections:
        copied = {key: value for key, value in section.items() if key != "paragraphs"}
        paragraphs = []
        for block in section.get("paragraphs", []):
            public_block = {key: value for key, value in block.items() if not key.startswith("_")}
            if public_block.get("type") == "table" and isinstance(public_block.get("table"), dict):
                public_block["table"] = {
                    key: value for key, value in public_block["table"].items() if key != "source_xml"
                }
                public_block["table"]["source_preserved"] = bool(block.get("table", {}).get("source_xml"))
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
