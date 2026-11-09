"""Server-side pagination evidence for a master document.

The evidence describes LibreOffice pagination, not pagination in a later
Word or WPS session. Temporary row bookmarks are used only in probe copies.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

from lxml import etree
from pypdf import PdfReader

from app.core.extract.xml_utils import NS, qn, safe_xml_parser
from app.services import office_layout as layout
from app.services.export_time_budget import check_export_budget
from app.services.master_layout_anchors import required_layout_bookmarks, required_table_row_targets
from app.services.project_versions import project_content_hash
from app.services.source_template_profile import ProfileError, digest
from app.services.word_native_layout import (
    WordNativeLayoutError, _directory_references, _validate_extra_bookmarks, _validate_table_targets,
)


def _write_package(path: Path, parts: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def _instrument_rows(parts: dict[str, bytes], targets: list[dict]) -> tuple[dict[str, bytes], list[dict]]:
    """Add zero-width row markers to a probe copy, never to the delivered file."""
    root = etree.fromstring(parts["word/document.xml"], safe_xml_parser())
    ids = root.xpath(".//w:bookmarkStart/@w:id", namespaces=NS)
    if any(not value.isdecimal() for value in ids):
        raise ProfileError("文档书签标识无法用于服务器分页核验")
    next_id = max((int(value) for value in ids), default=0) + 1
    existing = set(root.xpath(".//w:bookmarkStart/@w:name", namespaces=NS))
    starts = {node.get(qn("w:name")): node for node in root.xpath(".//w:bookmarkStart", namespaces=NS)}
    tables = []
    for target in targets:
        anchor = starts.get(target["bookmark"])
        matches = anchor.xpath("ancestor::w:tbl[1]", namespaces=NS) if anchor is not None else []
        if len(matches) != 1:
            raise ProfileError("表格分页书签缺失")
        rows = matches[0].findall(qn("w:tr"))
        if len(rows) != target["row_count"]:
            raise ProfileError("表格分页行数与当前内容不一致")
        markers = []
        for index, row in enumerate(rows, 1):
            paragraphs = row.xpath("./w:tc/w:p", namespaces=NS)
            if not paragraphs:
                raise ProfileError("表格行缺少可定位的段落")
            names = []
            for side, paragraph in (("S", paragraphs[0]), ("E", paragraphs[-1])):
                name = "PGZ" + hashlib.sha256(f'{target["bookmark"]}:{index}:{side}'.encode()).hexdigest()[:30]
                if name in existing:
                    raise ProfileError("表格临时分页书签发生冲突")
                existing.add(name)
                start = etree.Element(qn("w:bookmarkStart"))
                start.set(qn("w:id"), str(next_id))
                start.set(qn("w:name"), name)
                end = etree.Element(qn("w:bookmarkEnd"))
                end.set(qn("w:id"), str(next_id))
                next_id += 1
                position = len(paragraph) if side == "E" else int(len(paragraph) > 0 and paragraph[0].tag == qn("w:pPr"))
                paragraph.insert(position, start)
                paragraph.insert(position + 1, end)
                names.append(name)
            markers.append(names)
        tables.append({"bookmark": target["bookmark"], "markers": markers})
    result = dict(parts)
    result["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return result, tables


def _pdf_destinations(path: Path) -> tuple[int, dict[str, int]]:
    reader = PdfReader(path)
    if not 0 < len(reader.pages) <= 500:
        raise ProfileError("服务器排版页数无效")
    result = {}
    for name, destination in reader.named_destinations.items():
        name = str(name).lstrip("/")
        if not name.startswith("PG"):
            continue
        page = reader.get_destination_page_number(destination) + 1
        if name in result or not 1 <= page <= len(reader.pages):
            raise ProfileError("服务器排版书签页码无效")
        result[name] = page
    return len(reader.pages), result


def _probe(parts: dict[str, bytes], project, profile, rows: list[dict], directory: Path) -> dict:
    names = required_layout_bookmarks(project, profile)
    targets = required_table_row_targets(project, profile)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    try:
        _validate_extra_bookmarks(raw.getvalue(), names)
        _validate_table_targets(raw.getvalue(), targets)
    except (WordNativeLayoutError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ProfileError("服务器分页书签与当前文件不一致") from exc
    measured_parts, tables = _instrument_rows(layout._layout_input_parts(parts, profile), targets)
    source = directory / "probe.docx"
    _write_package(source, measured_parts)
    layout._office_roundtrip(source, directory / "render", named_destinations=True)
    before = layout._pdf_state(directory / "render/before.pdf")
    after = layout._pdf_state(directory / "render/after.pdf")
    before_pages, before_positions = _pdf_destinations(directory / "render/before.pdf")
    after_pages, after_positions = _pdf_destinations(directory / "render/after.pdf")
    all_names = names + [name for table in tables for pair in table["markers"] for name in pair]
    if (before_pages != after_pages or before_pages != before["page_count"] or after_pages != after["page_count"]
            or len(all_names) != len(set(all_names))
            or any(name not in before_positions or name not in after_positions
                   or before_positions[name] != after_positions[name] for name in all_names)):
        raise ProfileError("服务器分页未收敛或内容位置不完整")
    before_fonts = {layout._font_name(font) for font in before["fonts"]}
    actual_fonts = {layout._font_name(font) for font in after["fonts"]}
    if any(layout._font_name(font) not in before_fonts or layout._font_name(font) not in actual_fonts
           for font in layout._required_fonts(project, profile, parts)):
        raise ProfileError("服务器排版结果缺少所需字体")
    updated_rows, _ = layout._read_toc(directory / "render/updated.docx", profile, allow_server_style_aliases=True)
    if layout._toc_state(updated_rows) != layout._toc_state(rows):
        raise ProfileError("最终文件的目录页码与服务器排版不一致")
    layout._layout_destinations(after["destinations"], rows)
    positions = [{"bookmark": name, "physical_page": after_positions[name]} for name in names]
    measured_tables = []
    for table, target in zip(tables, targets):
        measured_rows = [{"row": index, "start_page": after_positions[pair[0]], "end_page": after_positions[pair[1]]}
                         for index, pair in enumerate(table["markers"], 1)]
        measured_tables.append({"bookmark": target["bookmark"], "rows": measured_rows})
    return {"page_count": after_pages, "extra_bookmarks": positions, "table_rows": {"tables": measured_tables},
            "fonts": after["fonts"], "pdf_sha256": hashlib.sha256((directory / "render/after.pdf").read_bytes()).hexdigest()}


def refresh_server_layout(output_path: Path, project, profile) -> None:
    if layout._cover_policy(profile) is not None:
        raise ProfileError("服务器排版暂不能核验该模板的封面分页规则")
    with zipfile.ZipFile(output_path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    with tempfile.TemporaryDirectory(prefix="paperguard-server-layout-") as temporary:
        directory = Path(temporary)
        previous = None
        rows = []
        for attempt in range(4):
            check_export_budget()
            source = directory / f"input-{attempt}.docx"
            _write_package(source, layout._layout_input_parts(parts, profile))
            run = directory / f"round-{attempt}"
            layout._office_roundtrip(source, run, named_destinations=True)
            before, after = layout._pdf_state(run / "before.pdf"), layout._pdf_state(run / "after.pdf")
            rows, _ = layout._read_toc(run / "updated.docx", profile, allow_server_style_aliases=True)
            with zipfile.ZipFile(run / "updated.docx") as measured:
                from app.core.extract.xml_utils import load_part
                styles = load_part(measured, "word/styles.xml")
            root = etree.fromstring(parts["word/document.xml"])
            placeholder = root.xpath(".//w:sdtPr/w:tag[@w:val='pg-toc:placeholder']", namespaces=NS)
            current = None if placeholder else layout._source_toc_state(root, profile)
            before_state = (before["page_count"], before["destinations"])
            after_state = (after["page_count"], after["destinations"])
            stable = current == layout._toc_state(rows) and before_state == after_state and previous == after_state
            layout._replace_toc_rows(parts, rows, profile, character_styles_root=styles)
            previous = after_state
            if stable:
                break
        else:
            raise ProfileError("服务器目录与分页未能收敛")
        content_types = etree.fromstring(parts["[Content_Types].xml"])
        if not any(node.get("Extension") == "json" for node in content_types):
            etree.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Default",
                             Extension="json", ContentType="application/json")
        parts["[Content_Types].xml"] = etree.tostring(content_types, xml_declaration=True, encoding="UTF-8", standalone=True)
        first_dir = directory / "first-probe"
        first_dir.mkdir()
        measured = _probe(parts, project, profile, rows, first_dir)
        manifest = digest({name: hashlib.sha256(data).hexdigest() for name, data in sorted(parts.items())})
        receipt = {"version": layout.SERVER_TOC_VERSION, "page_reference_mode": "bookmark_pageref",
                   "pagination_source": "libreoffice_server", "state_stable": True,
                   "worker_sha256": hashlib.sha256(layout.WORKER.read_bytes()).hexdigest(),
                   "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "dependency_sha256": json.loads(layout.DEPENDENCY.read_text())["sha256"],
                   "source_profile_sha256": profile["profile_sha256"], "project_content_hash": project_content_hash(project),
                   "document_manifest_sha256": manifest, "toc": layout._toc_state(rows),
                   "page_count": measured["page_count"], "server_layout": measured}
        receipt["signature"] = layout._mac(receipt, layout.SERVER_TOC_VERSION)
        parts[layout.RECEIPT_PART] = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        final_dir = directory / "final-probe"
        final_dir.mkdir()
        final = _probe(parts, project, profile, rows, final_dir)
        if any(final[key] != measured[key] for key in ("page_count", "extra_bookmarks", "table_rows", "fonts")):
            raise ProfileError("最终文件与服务器分页证据不一致")
        candidate = directory / "final.docx"
        _write_package(candidate, parts)
        from app.services.source_template_safety import audit_generated_package
        final_bytes = candidate.read_bytes()
        audit_generated_package(final_bytes)
        verify_server_receipt(final_bytes, project, profile, receipt)
        check_export_budget()
        output_path.write_bytes(final_bytes)


def verify_server_receipt(data: bytes, project, profile, receipt: dict) -> dict:
    receipt = dict(receipt)
    signature = receipt.pop("signature", "")
    if not hmac.compare_digest(str(signature), layout._mac(receipt, layout.SERVER_TOC_VERSION)):
        raise ProfileError("服务器排版证据签名无效")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist() if name != layout.RECEIPT_PART}
    manifest = digest({name: hashlib.sha256(payload).hexdigest() for name, payload in sorted(parts.items())})
    server = receipt.get("server_layout")
    if (receipt.get("pagination_source") != "libreoffice_server" or receipt.get("state_stable") is not True
            or receipt.get("page_reference_mode") != "bookmark_pageref"
            or receipt.get("document_manifest_sha256") != manifest
            or receipt.get("source_profile_sha256") != profile.get("profile_sha256")
            or receipt.get("project_content_hash") != project_content_hash(project)
            or receipt.get("worker_sha256") != hashlib.sha256(layout.WORKER.read_bytes()).hexdigest()
            or receipt.get("probe_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            or receipt.get("dependency_sha256") != json.loads(layout.DEPENDENCY.read_text())["sha256"]
            or not isinstance(server, dict) or type(receipt.get("page_count")) is not int
            or not 1 <= receipt["page_count"] <= 500 or server.get("page_count") != receipt["page_count"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(server.get("pdf_sha256", "")))
            or layout._cover_policy(profile) is not None):
        raise ProfileError("服务器排版证据与当前文件或模板不一致")
    names = required_layout_bookmarks(project, profile)
    targets = required_table_row_targets(project, profile)
    try:
        _validate_extra_bookmarks(data, names)
        _validate_table_targets(data, targets)
    except (WordNativeLayoutError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ProfileError("服务器分页书签与当前文件不一致") from exc
    positions = server.get("extra_bookmarks")
    if (not isinstance(positions, list) or len(positions) != len(names)
            or any(not isinstance(row, dict) or row.get("bookmark") != name
                   or type(row.get("physical_page")) is not int
                   or not 1 <= row["physical_page"] <= receipt["page_count"]
                   for row, name in zip(positions, names))):
        raise ProfileError("服务器内容分页证据不完整")
    fonts = server.get("fonts")
    if (not isinstance(fonts, list) or any(not isinstance(font, str) for font in fonts)
            or any(layout._font_name(font) not in {layout._font_name(item) for item in fonts}
                   for font in layout._required_fonts(project, profile, parts))):
        raise ProfileError("服务器排版证据缺少所需字体")
    toc = receipt.get("toc")
    refs = _directory_references(data)
    expected = {item["target_key"] for item in profile.get("toc_entries", [])}
    if (not isinstance(toc, list) or len(toc) != len(refs)
            or len({row.get("key") for row in toc if isinstance(row, dict)}) != len(toc)
            or not expected.issubset({row.get("key") for row in toc if isinstance(row, dict)})):
        raise ProfileError("服务器目录证据不完整")
    headings = {item["key"]: item for item in profile.get("headings", [])}
    for row, (bookmark, page) in zip(toc, refs):
        heading = headings.get(row.get("key"))
        if (not heading or row.get("target_title") != heading.get("title")
                or row.get("bookmark") != bookmark or bookmark != heading.get("bookmark_name")
                or row.get("page") != page or not re.fullmatch(r"[1-9][0-9]*|[ivxlcdmIVXLCDM]+", str(page))):
            raise ProfileError("服务器目录证据与当前文档不一致")
    tables = (server.get("table_rows") or {}).get("tables")
    if not isinstance(tables, list) or len(tables) != len(targets):
        raise ProfileError("服务器表格分页证据不完整")
    by_name = {row["bookmark"]: row["physical_page"] for row in positions}
    for table, target in zip(tables, targets):
        rows = table.get("rows") if isinstance(table, dict) else None
        if (not isinstance(table, dict) or table.get("bookmark") != target["bookmark"]
                or not isinstance(rows, list) or len(rows) != target["row_count"]):
            raise ProfileError("服务器表格分页行数不一致")
        previous = 0
        for number, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or type(row.get("row")) is not int or row.get("row") != number
                    or type(row.get("start_page")) is not int or type(row.get("end_page")) is not int
                    or not 1 <= row["start_page"] <= row["end_page"] <= receipt["page_count"]
                    or row["start_page"] < previous):
                raise ProfileError("服务器表格分页位置无效")
            previous = row["end_page"]
        if rows[0]["start_page"] != by_name.get(target["bookmark"]) or not (
                rows[-1]["start_page"] <= by_name.get(target["end_bookmark"], 0) <= rows[-1]["end_page"]):
            raise ProfileError("服务器表格分页与内容边界不一致")
    return receipt
