"""Import a Word manuscript into the editable project model.

This is deliberately a preview-first importer.  It keeps text, heading levels,
tables and common raster images; unsupported OOXML objects are reported instead
of being silently flattened into the paper.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
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


def _image_from_run(paragraph: Paragraph, run) -> tuple[bytes, str] | None:
    for blip in run._element.xpath(".//a:blip"):
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
            return part.blob, extension
    return None


def _paragraph_blocks(paragraph: Paragraph, block_prefix: str, unsupported: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    # ``paragraph.runs`` omits runs inside ``w:hyperlink``. Walk direct
    # paragraph children to retain link text in its original position.
    for child in paragraph._element.iterchildren():
        if child.tag == qn("w:hyperlink"):
            text = "".join(node.text or "" for node in child.iter(qn("w:t")))
            if text:
                blocks.append({"id": f"{block_prefix}-link-{uuid4().hex[:8]}", "text": text})
            continue
        if child.tag != qn("w:r"):
            continue
        run = Run(child, paragraph)
        text = run.text or ""
        if text:
            blocks.append({
                "id": f"{block_prefix}-{uuid4().hex[:8]}", "text": text,
                "bold": bool(run.bold), "italic": bool(run.italic), "underline": bool(run.underline),
            })
        if run._element.xpath(".//a:blip"):
            image = _image_from_run(paragraph, run)
            if image is None:
                unsupported.append("含有不支持的图片格式或外部链接")
                continue
            data, extension = image
            blocks.append({
                "id": f"{block_prefix}-image-{uuid4().hex[:8]}",
                "type": "image", "image": {"url": "", "caption": ""},
                "_image_data": data, "_image_extension": extension,
            })
    for equation_index, equation in enumerate(paragraph._element.xpath(".//m:oMath"), 1):
        alt_text = "".join(equation.itertext()).strip()
        blocks.append({
            "id": f"{block_prefix}-equation-{equation_index}-{uuid4().hex[:8]}",
            "type": "equation",
            "equation": {
                "omml": etree.tostring(equation, encoding="unicode"),
                "alt_text": alt_text,
            },
        })
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
                        "source_xml": etree.tostring(item._tbl, encoding="unicode"),
                    },
                })

    properties = document.core_properties
    return {
        "sections": sections,
        "metadata": {"title": properties.title or "", "author": properties.author or ""},
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
