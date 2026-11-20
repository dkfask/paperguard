"""Inventory manuscript source bytes independently of the project importer.

This is an import-content check, not a template or rendered-output validator.
Unmapped source structures remain unresolved until another import stage can
prove their destination.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
import posixpath
import re
from collections import Counter, defaultdict
from zipfile import BadZipFile, ZipFile

from lxml import etree

from app.core.extract.xml_utils import parse_xml
from app.services.docx_smartart import DGM, GRAPHIC_DATA, read_inline_smartart, read_package_part, resolve_package_theme
from app.services.upload_security import DOCX_MAX_BYTES, validate_docx_bytes


INVENTORY_VERSION = "docx-import-inventory/3"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _smartart_digest(parts: dict) -> str:
    return _digest(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode())


def inventory_sha256(inventory: dict) -> str:
    """Bind the independently observed inventory without storing source text twice."""
    return _digest(json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _target_path(target: str) -> str | None:
    if not target or "\\" in target or "#" in target or "?" in target:
        return None
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("word", target))
    return None if path in {".", ".."} or path.startswith("../") else path


def inventory_docx_source(data: bytes) -> dict:
    """Read source paragraphs, physical cells, media uses and relationships.

    Source positions are based on XML siblings, not importer-produced objects.
    Only the document body and its referenced document relationships are used
    as evidence for this import check. Unsupported source content is recorded.
    """
    if len(data) > DOCX_MAX_BYTES:
        raise ValueError("Word 文件超过导入大小限制")
    validate_docx_bytes(data)
    entries: list[dict] = []
    try:
        with ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Word 文件包含重复的包内路径，无法安全对账")
            root = parse_xml(archive.read("word/document.xml"))
            if root.tag != _q(W, "document"):
                raise ValueError("Word 正文根节点无效")
            body = root.find(_q(W, "body"))
            if body is None:
                raise ValueError("Word 正文不存在")
            relationships: dict[str, dict] = {}
            if "word/_rels/document.xml.rels" in names:
                rel_root = parse_xml(archive.read("word/_rels/document.xml.rels"))
                if rel_root.tag != _q(REL, "Relationships"):
                    raise ValueError("Word 关系文件无效")
                for relation in rel_root:
                    if relation.tag != _q(REL, "Relationship"):
                        raise ValueError("Word 关系文件包含未知结构")
                    identifier = relation.get("Id", "")
                    if not identifier or identifier in relationships:
                        raise ValueError("Word 关系标识缺失或重复")
                    relationships[identifier] = {
                        "type": relation.get("Type", ""),
                        "target": relation.get("Target", ""),
                        "external": relation.get("TargetMode", "").lower() == "external",
                    }
                    entries.append({"kind": "relationship_definition", "location": f"word/_rels/document.xml.rels#{identifier}",
                                    "relationship_type": relationships[identifier]["type"]})

            theme: tuple[dict[str, str], dict[str, str]] | None = None

            def get_theme() -> tuple[dict[str, str], dict[str, str]]:
                nonlocal theme
                if theme is None:
                    def read_rel_blob(target: str) -> bytes | None:
                        path = _target_path(target)
                        return archive.read(path) if path and path in names else None

                    theme = resolve_package_theme(
                        [(rel["type"], rel["external"], rel["target"]) for rel in relationships.values()],
                        read_rel_blob,
                    )
                return theme

            def special_nodes(element, location: str) -> None:
                for node in element.iter():
                    node_location = f"{location}:{node.getroottree().getpath(node)}"
                    if node.tag in {_q(W, "fldSimple"), _q(W, "fldChar"), _q(W, "instrText")}:
                        entries.append({"kind": "field", "location": node_location})
                    if node.tag in {_q(W, "bookmarkStart"), _q(W, "bookmarkEnd")}:
                        entries.append({"kind": "bookmark", "location": node_location})
                    for name, identifier in node.attrib.items():
                        diagram_reference = node.tag == _q(DGM, "relIds") and name in {
                            _q(R, key) for key in ("dm", "lo", "qs", "cs")
                        }
                        if not diagram_reference and name not in {_q(R, "id"), _q(R, "embed"), _q(R, "link")}:
                            continue
                        relation = relationships.get(identifier)
                        is_image = (node.tag == _q(A, "blip") and name == _q(R, "embed")
                                    and relation is not None and not relation["external"]
                                    and relation["type"].endswith("/image"))
                        entries.append({"kind": "relationship", "location": node_location,
                                        "relationship_id": identifier, "image_reference": is_image or diagram_reference})

            def paragraph(element, location: str) -> dict:
                segments: list[dict] = []
                images: list[str] = []

                def text(value: str) -> None:
                    if not value:
                        return
                    if segments and segments[-1]["kind"] == "text":
                        segments[-1]["text"] += value
                    else:
                        segments.append({"kind": "text", "text": value})

                def walk(node) -> None:
                    if node.tag in {_q(W, "pPr"), _q(W, "rPr")}:
                        return
                    if node.tag == _q(W, "t"):
                        text(node.text or "")
                        return
                    if node.tag == _q(W, "tab"):
                        text("\t")
                        return
                    if node.tag in {_q(W, "br"), _q(W, "cr")}:
                        text("\n")
                        if node.get(_q(W, "type")) in {"page", "column"}:
                            entries.append({"kind": "structure", "location": location,
                                            "reason": "源文档的手动分页或分栏需要规范化"})
                        return
                    if node.tag == _q(W, "drawing"):
                        diagram = next((n for n in node.iter(GRAPHIC_DATA) if n.get("uri") == DGM), None)
                        if diagram is not None:
                            def resolve(identifier, relation_type, content_type):
                                relation = relationships.get(identifier)
                                if not relation or relation["external"] or relation["type"] != relation_type:
                                    raise ValueError("SmartArt 关系无法核验")
                                return read_package_part(archive, _target_path(relation["target"]) or "", content_type)

                            try:
                                smartart, _ = read_inline_smartart(node, resolve, theme=get_theme())
                            except (KeyError, ValueError, etree.XMLSyntaxError):
                                entries.append({"kind": "structure", "location": location,
                                                "reason": "SmartArt 无法完整对账"})
                            else:
                                image_location = f"{location}/image[{len(images) + 1}]"
                                images.append(image_location)
                                rel_node = diagram.find(_q(DGM, "relIds"))
                                entries.append({"kind": "image", "location": image_location,
                                                "relationship_location": f"{location}:{rel_node.getroottree().getpath(rel_node)}",
                                                "smartart_sha256": _smartart_digest(smartart)})
                                segments.append({"kind": "image", "location": image_location})
                            return
                        anchors = node.xpath(".//wp:inline | .//wp:anchor", namespaces={"wp": WP})
                        found = False
                        for anchor in anchors:
                            for blip in anchor.xpath(".//a:blip", namespaces={"a": A}):
                                found = True
                                image_location = f"{location}/image[{len(images) + 1}]"
                                images.append(image_location)
                                identifier = blip.get(_q(R, "embed"), "")
                                relation = relationships.get(identifier)
                                path = _target_path(relation["target"]) if relation and not relation["external"] else None
                                media = archive.read(path) if path and path in names else None
                                entries.append({"kind": "image", "location": image_location,
                                                "relationship_id": identifier,
                                                "relationship_location": f"{location}:{blip.getroottree().getpath(blip)}",
                                                "media_sha256": _digest(media) if media is not None else None})
                                segments.append({"kind": "image", "location": image_location})
                        if not found or node.xpath(".//w:txbxContent", namespaces={"w": W}):
                            entries.append({"kind": "structure", "location": location,
                                            "reason": "图片中含未处理的图形、文本框或其他对象"})
                        return
                    allowed = {
                        _q(W, "p"), _q(W, "r"), _q(W, "hyperlink"),
                        _q(W, "bookmarkStart"), _q(W, "bookmarkEnd"), _q(W, "proofErr"),
                        _q(W, "fldSimple"), _q(W, "fldChar"), _q(W, "instrText"),
                        _q(W, "lastRenderedPageBreak"),
                    }
                    if node.tag not in allowed:
                        entries.append({"kind": "structure", "location": f"{location}:{node.getroottree().getpath(node)}",
                                        "reason": "源段落包含尚未对账的结构"})
                    for child in node:
                        walk(child)

                walk(element)
                special_nodes(element, location)
                item = {"kind": "paragraph", "location": location,
                        "text": "".join(part.get("text", "") for part in segments),
                        "segments": segments, "images": images}
                entries.append(item)
                return item

            paragraph_number = table_number = 0
            for item in body:
                if item.tag == _q(W, "p"):
                    paragraph_number += 1
                    paragraph(item, f"word/document.xml#body/p[{paragraph_number}]")
                elif item.tag == _q(W, "tbl"):
                    table_number += 1
                    table_location = f"word/document.xml#body/tbl[{table_number}]"
                    rows = item.findall(_q(W, "tr"))
                    entries.append({"kind": "table", "location": table_location,
                                    "row_lengths": [len(row.findall(_q(W, "tc"))) for row in rows]})
                    for child in item:
                        if child.tag not in {_q(W, "tblPr"), _q(W, "tblGrid"), _q(W, "tr")}:
                            entries.append({"kind": "structure", "location": table_location,
                                            "reason": "表格包含尚未处理的结构"})
                            special_nodes(child, table_location)
                    for row_index, row in enumerate(rows, 1):
                        for child in row:
                            if child.tag not in {_q(W, "trPr"), _q(W, "tc")}:
                                entries.append({"kind": "structure", "location": f"{table_location}/tr[{row_index}]",
                                                "reason": "表格行包含尚未处理的结构"})
                                special_nodes(child, f"{table_location}/tr[{row_index}]")
                        for col_index, cell in enumerate(row.findall(_q(W, "tc")), 1):
                            cell_location = f"{table_location}/tr[{row_index}]/tc[{col_index}]"
                            if cell.find(f"{_q(W, 'tcPr')}/{_q(W, 'gridSpan')}") is not None or cell.find(f"{_q(W, 'tcPr')}/{_q(W, 'vMerge')}") is not None:
                                entries.append({"kind": "structure", "location": cell_location,
                                                "reason": "合并单元格需要独立恢复及对账"})
                            if cell.find(_q(W, "tbl")) is not None:
                                entries.append({"kind": "structure", "location": cell_location,
                                                "reason": "嵌套表格需要独立恢复及对账"})
                            for child in cell:
                                if child.tag not in {_q(W, "tcPr"), _q(W, "p"), _q(W, "tbl")}:
                                    entries.append({"kind": "structure", "location": cell_location,
                                                    "reason": "表格单元格包含尚未处理的结构"})
                                    special_nodes(child, cell_location)
                            cell_text = []
                            for index, p in enumerate(cell.findall(_q(W, "p")), 1):
                                result = paragraph(p, f"{cell_location}/p[{index}]")
                                cell_text.append(result["text"])
                            entries.append({"kind": "table_cell", "location": cell_location,
                                            "table_location": table_location, "row": row_index,
                                            "column": col_index, "text": "\n".join(cell_text)})
                elif item.tag == _q(W, "sectPr"):
                    special_nodes(item, "word/document.xml#body/sectPr")
                else:
                    entries.append({"kind": "structure", "location": f"word/document.xml#body:{item.getroottree().getpath(item)}",
                                    "reason": "正文包含尚未处理的结构"})
                    special_nodes(item, "word/document.xml#body")
    except (BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise ValueError("Word 文件内部结构损坏，无法对账") from exc
    return {"version": INVENTORY_VERSION, "source_sha256": _digest(data), "entries": entries}


def reconcile_docx_import(inventory: dict, imported: dict) -> dict:
    """Compare each independently observed content occurrence with the candidate."""
    from app.services.docx_project_import import import_candidate_sha256

    unresolved: list[dict] = []
    entries = inventory.get("entries", [])
    source_paragraphs = {item["location"]: item for item in entries if item["kind"] == "paragraph"}
    source_tables = {item["location"]: item for item in entries if item["kind"] == "table"}
    source_images = {item["location"]: item for item in entries if item["kind"] == "image"}
    source_locations = set(source_paragraphs) | set(source_tables)
    paragraph_blocks: dict[str, list[dict]] = defaultdict(list)
    paragraph_segments: dict[str, list[dict]] = defaultdict(list)
    tables: dict[str, list[dict]] = defaultdict(list)
    images: dict[str, list[dict]] = defaultdict(list)
    titles: dict[str, list[str]] = defaultdict(list)
    observed_order: list[str] = []

    def missing(kind: str, location: str, reason: str) -> None:
        unresolved.append({"kind": kind, "location": location, "reason": reason})

    def observe(location: str) -> None:
        root_location = location.split("/image[", 1)[0]
        if root_location in source_locations and (not observed_order or observed_order[-1] != root_location):
            observed_order.append(root_location)

    def add_text(location: str, value: str) -> None:
        if not value:
            return
        segments = paragraph_segments[location]
        if segments and segments[-1]["kind"] == "text":
            segments[-1]["text"] += value
        else:
            segments.append({"kind": "text", "text": value})

    for section in imported.get("sections", []):
        location = section.get("_source_location", "")
        if location:
            titles[location].append(section.get("title", ""))
            observe(location)
            if location not in source_paragraphs or "/tc[" in location:
                missing("candidate", location, "候选标题没有对应的源正文段落")
        elif section.get("key") != "import-body" and section.get("title"):
            missing("candidate", "candidate/section", "候选标题缺少源文档位置")
        for block in section.get("paragraphs", []):
            location = block.get("_source_location", "")
            kind = block.get("type", "paragraph")
            if kind == "image":
                images[location].append(block)
                parent = location.split("/image[", 1)[0]
                if location in source_images and parent in source_paragraphs and "/tc[" not in parent:
                    paragraph_segments[parent].append({"kind": "image", "location": location})
                else:
                    missing("candidate", location or "candidate/image", "候选图片没有可对账的源正文位置")
            elif kind == "table":
                tables[location].append(block)
                if location not in source_tables:
                    missing("candidate", location or "candidate/table", "候选表格没有对应的源表格")
            elif kind == "paragraph":
                paragraph_blocks[location].append(block)
                if location in source_paragraphs and "/tc[" not in location:
                    add_text(location, block.get("text", ""))
                else:
                    missing("candidate", location or "candidate/paragraph", "候选段落没有对应的源正文段落")
            else:
                missing("candidate", location or "candidate/block", "候选含尚未对账的内容类型")
            caption_location = block.get("_caption_source_location", "")
            if caption_location and kind in {"image", "table"}:
                caption = (block.get(kind) or {}).get("caption", "")
                if caption_location not in source_paragraphs or "/tc[" in caption_location:
                    missing("caption", caption_location, "题注没有对应的源正文段落")
                else:
                    paragraph_blocks[caption_location].append({"text": caption})
                    add_text(caption_location, caption)
                if kind == "table":
                    observe(caption_location)
            if location:
                observe(location)
            if caption_location and kind == "image":
                observe(caption_location)

    matched_images: set[str] = set()
    for item in entries:
        kind, location = item["kind"], item["location"]
        if kind == "paragraph":
            if "/tc[" in location:
                continue  # Cell text is checked against the physical cell below.
            source_text = item["text"]
            if not source_text and not item["images"]:
                missing("normalization", location, "空段落的规范化去向尚未确认")
                continue
            candidate_text = "".join(block.get("text", "") for block in paragraph_blocks[location])
            heading = titles.get(location, [])
            if heading:
                title = heading[0]
                stripped = source_text.strip()
                prefix = title[:-len(stripped)] if stripped and title.endswith(stripped) else ""
                title_matches = title == stripped or bool(prefix and re.fullmatch(r"(?:第\s*)?\d+(?:[.．]\d+)*(?:\s*章)?[\s、.．-]*", prefix))
                if len(heading) == 1 and title_matches and not paragraph_segments[location] and not item["images"]:
                    continue
                missing(kind, location, "源标题未唯一完整地进入候选，或被重复使用")
                continue
            if candidate_text != source_text:
                missing(kind, location, "源段落文字未完整进入候选")
            if paragraph_segments[location] != item["segments"]:
                missing("order", location, "源段落中的文字与图片顺序或对应位置不一致")
        elif kind == "table":
            if len(tables[location]) != 1:
                missing(kind, location, "源表格未完整进入候选")
            else:
                table = tables[location][0].get("table") or {}
                cells = table.get("cells", [])
                row_lengths = [len(row) for row in cells]
                if (row_lengths != item["row_lengths"] or table.get("rows") != len(cells)
                        or table.get("cols") != max(row_lengths, default=0)):
                    missing(kind, location, "候选表格行列与源表格不一致")
        elif kind == "table_cell":
            blocks = tables[item["table_location"]]
            cells = (blocks[0].get("table") or {}).get("cells", []) if len(blocks) == 1 else []
            row, col = item["row"] - 1, item["column"] - 1
            if row >= len(cells) or col >= len(cells[row]) or cells[row][col] != item["text"]:
                missing(kind, location, "源表格单元格未完整进入候选")
        elif kind == "image":
            candidates = images[location]
            candidate = candidates[0] if len(candidates) == 1 else {}
            data = candidate.get("_image_data")
            diagram = (candidate.get("image") or {}).get("smartart")
            matched = (_smartart_digest(diagram) == item["smartart_sha256"]
                       if diagram and "smartart_sha256" in item else
                       isinstance(data, bytes) and _digest(data) == item.get("media_sha256"))
            if not matched:
                missing(kind, location, "源图片出现位置或媒体内容未完整进入候选")
            else:
                matched_images.add(location)
        elif kind in {"field", "bookmark", "structure"}:
            missing(kind, location, item.get("reason", "源文档字段或书签尚未建立目标映射"))

    matched_relationships = {source_images[location].get("relationship_location") for location in matched_images}
    for item in entries:
        if item["kind"] != "relationship":
            continue
        if not item.get("image_reference") or item["location"] not in matched_relationships:
            missing("relationship", item["location"], "源文档关系尚未建立目标映射")

    expected_order = [item["location"] for item in entries
                      if (item["kind"] == "table" or (item["kind"] == "paragraph"
                          and "/tc[" not in item["location"] and (item["text"] or item["images"])))]
    expected_set = set(expected_order)
    observed = [location for location in observed_order if location in expected_set]
    if observed != [location for location in expected_order if location in observed]:
        missing("order", "word/document.xml#body", "候选内容顺序与源文档不一致")
    if inventory.get("version") != INVENTORY_VERSION or inventory.get("source_sha256") != imported.get("source_sha256"):
        missing("source", "word/document.xml", "源文件摘要或盘点版本不匹配")
    report = {
        "version": INVENTORY_VERSION,
        "source_sha256": inventory.get("source_sha256"),
        "candidate_sha256": import_candidate_sha256(imported),
        "configuration_version": imported.get("configuration_version"),
        "status": "incomplete" if unresolved else "complete",
        "counts": dict(sorted(Counter(item["kind"] for item in entries).items())),
        "unresolved": unresolved,
    }
    report["sha256"] = _digest(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    return report


def reconcile_applied_docx_import(imported: dict, project, section_mapping: list[dict]) -> dict:
    """Check source occurrences against the actual merged project and saved images.

    Source positions remain in workflow metadata rather than editable project
    fields. Rebuilding this proof after a merge catches loss in the application
    step, independently of the source-to-candidate check.
    """
    from pathlib import Path

    from app.models.project import ParagraphBlock
    from app.services.document_assets import document_upload_dir
    from app.services.docx_project_import import import_candidate_sha256
    from app.services.project_versions import project_content_hash

    unresolved: list[dict] = []
    mappings: list[dict] = []
    blocks: dict[str, list[tuple]] = defaultdict(list)
    sections: dict[str, list] = defaultdict(list)
    positions: dict[tuple[int, int], int] = {}
    for section in project.sections:
        sections[section.key].append(section)
        for index, block in enumerate(section.paragraphs):
            blocks[block.id].append((section, index, block))
            positions[(id(section), index)] = len(positions)

    def missing(kind: str, location: str, reason: str) -> None:
        unresolved.append({"kind": kind, "location": location, "reason": reason})

    def target(section, index: int | None, block=None) -> dict:
        return {"section_key": section.key, "block_id": block.id if block is not None else None,
                "block_index": index}

    master_bound = bool((project.template_snapshot or {}).get("source_execution"))
    title_mappings = defaultdict(list)
    for item in section_mapping:
        title_mappings[item.get("source_location", "")].append(item)

    for source_section in imported.get("sections", []):
        source_location = source_section.get("_source_location", "")
        if source_location:
            entries = title_mappings[source_location]
            destinations = []
            if len(entries) == 1:
                item = entries[0]
                if item.get("target_block_id"):
                    for section, index, block in blocks[item["target_block_id"]]:
                        if block.text == source_section.get("title"):
                            destinations.append(target(section, index, block))
                else:
                    for section in sections[item.get("target_section_key", "")]:
                        if section.title == item.get("target_title"):
                            destinations.append(target(section, None))
            if len(destinations) != 1:
                missing("title", source_location, "源标题没有唯一进入已应用项目")
            mappings.append({"kind": "title", "source_location": source_location, "targets": destinations,
                             "transformation": entries[0].get("transformation") if len(entries) == 1 else None})

        observed_order = []
        for raw in source_section.get("paragraphs", []):
            location = raw.get("_source_location", "")
            kind = raw.get("type", "paragraph")
            expected = ParagraphBlock.model_validate(raw).model_dump(mode="json")
            candidates = blocks.get(raw.get("id", ""), [])
            expected_parts = [expected]
            # A frozen template separates keywords from the abstract. Validate
            # precisely this existing transformation instead of allowing any
            # changed text through the integrity check.
            if (master_bound and kind == "paragraph"
                    and source_section.get("type") in {"abstract", "english_abstract"}
                    and any(section.type in {"keywords", "english_keywords"} for section, _, _ in candidates)):
                match = re.search(r"(?:关键词|key\s*words?)\s*[：:]\s*", expected["text"], re.IGNORECASE)
                if match:
                    expected_parts = [dict(expected, text=text, inline_runs=[]) for text in
                                      (expected["text"][:match.start()].strip(), expected["text"][match.end():].strip()) if text]
            destinations = [target(section, index, block) for section, index, block in candidates]
            if len(candidates) == 1 and not any(
                section.type in {"keywords", "english_keywords"} for section, _, _ in candidates
            ):
                section, index, _ = candidates[0]
                observed_order.append(positions[(id(section), index)])
            if len(candidates) != len(expected_parts):
                missing(kind, location, "源内容块没有唯一完整进入已应用项目")
            else:
                actual_parts = []
                for section, index, block in candidates:
                    actual = block.model_dump(mode="json")
                    if kind == "image" and not (raw.get("image") or {}).get("smartart"):
                        image = actual.get("image") or {}
                        filename = Path(image.get("url", "")).name
                        path = document_upload_dir() / filename
                        source_data = raw.get("_image_data")
                        try:
                            valid_media = (image.get("url") == f"/media/uploads/{filename}"
                                           and filename.startswith(f"{project.id}-")
                                           and path.resolve().parent == document_upload_dir().resolve()
                                           and isinstance(source_data, bytes)
                                           and path.read_bytes() == source_data)
                        except OSError:
                            valid_media = False
                        if not valid_media:
                            missing("image", location, "已应用图片的文件或媒体内容与源文档不匹配")
                        image["url"] = ""
                    if master_bound and actual.get("table"):
                        actual["table"]["source_xml"] = ""
                    actual_parts.append(actual)
                for part in expected_parts:
                    if kind == "image" and part.get("image"):
                        part["image"]["url"] = ""
                    if master_bound and part.get("table"):
                        part["table"]["source_xml"] = ""
                # Keyword targets can precede their abstract in the frozen tree.
                canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False)
                if sorted(map(canonical, actual_parts)) != sorted(map(canonical, expected_parts)):
                    missing(kind, location, "已应用内容块的文字、表格或对象属性与确认候选不一致")
            mappings.append({"kind": kind, "source_location": location, "targets": destinations})
            caption_location = raw.get("_caption_source_location", "")
            if caption_location and kind in {"image", "table"}:
                mappings.append({"kind": "caption", "source_location": caption_location,
                                 "targets": destinations, "transformation": "object_caption"})
        if observed_order != sorted(observed_order):
            missing("order", source_location, "已应用内容的顺序与确认候选不一致")

    report = {"version": INVENTORY_VERSION, "source_sha256": imported.get("source_sha256"),
              "candidate_sha256": import_candidate_sha256(imported),
              "configuration_version": imported.get("configuration_version"),
              "project_id": project.id, "project_revision": project.revision,
              "project_content_sha256": project_content_hash(project),
              "status": "incomplete" if unresolved else "complete", "mappings": mappings,
              "unresolved": unresolved}
    report["sha256"] = inventory_sha256(report)
    return report
