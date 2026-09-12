"""结构化过程材料的默认值、校验和可审计导出。"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.models.process_record import artifact_form_schema, catalog_item


def _value(data: dict[str, Any], key: str) -> Any:
    value = data.get(key)
    return "" if value is None else value


def prefill_artifact_data(artifact_type: str, *, user: dict, participant: dict,
                          cohort: dict, project: Any | None = None) -> dict[str, Any]:
    """Build a form snapshot from the identity/project context, never from client input."""
    meta = dict(getattr(project, "meta", {}) or {}) if project else {}
    topic = getattr(project, "title", "") if project else ""
    return {
        "college": meta.get("college") or meta.get("school") or cohort.get("name", ""),
        "department": meta.get("department") or meta.get("系别", ""),
        "major": meta.get("major", ""), "class_name": meta.get("class_name") or meta.get("class", ""),
        "student_id": meta.get("student_id") or user.get("username", ""),
        "student_name": meta.get("author") or user.get("display_name", ""),
        "advisor": meta.get("advisor") or participant.get("teacher_id", ""),
        "advisor_title": meta.get("advisor_title", ""), "topic": topic,
        "entries": [], "completed_items": {}, "committee_members": [], "questions": [],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    } | {"artifact_type": artifact_type}


def validate_artifact_data(artifact_type: str, data: dict[str, Any], *, allow_partial: bool = False) -> dict[str, Any]:
    """Validate the fields that make a school form actionable.

    Legacy records may contain only a title/content pair. They remain readable, but
    a newly structured submission must satisfy all fields marked required in schema.
    """
    schema = artifact_form_schema(artifact_type)
    normalized = dict(data or {})
    structured = any(key not in {"title", "content"} for key in normalized)
    if not structured:
        return normalized
    errors: list[str] = []
    for field in schema.get("fields", []):
        key = field["key"]
        value = normalized.get(key)
        missing = value is None or value == "" or value == [] or value == {}
        if field.get("required") and missing and not allow_partial:
            errors.append(field["label"])
        if missing:
            continue
        if field["type"] == "number":
            try:
                number = float(value)
            except (TypeError, ValueError):
                errors.append(f"{field['label']}必须是数字")
                continue
            if field["key"] in {"copy_ratio", "score", "total_score", "advisor_score", "reviewer_score", "defense_score"} and not 0 <= number <= 100:
                errors.append(f"{field['label']}必须在 0-100 之间")
            normalized[key] = number
        if field["type"] == "json" and not isinstance(value, (list, dict)):
            errors.append(f"{field['label']}必须是结构化列表或对象")
        if field["type"] == "table" and not isinstance(value, list):
            errors.append(f"{field['label']}必须逐行填写")
    if errors:
        raise ValueError("、".join(errors))
    if artifact_type == "similarity_report" and "copy_ratio" in normalized:
        ratio = float(normalized["copy_ratio"])
        normalized["classification"] = "A" if ratio < 40 else "B" if ratio <= 60 else "C"
        normalized["requires_second_round"] = 40 <= ratio <= 60
    return normalized


def _display(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value or "")


def _table_rows(data: dict[str, Any], field: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get(field["key"], [])
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _excel_column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def render_artifact_docx(artifact_type: str, data: dict[str, Any], output: Path,
                        *, participant: dict | None = None, cohort: dict | None = None) -> None:
    """Generate a stable filled snapshot when an original office template is unavailable.

    The raw template name and schema are included in the document so reviewers can
    distinguish a generated preview from the institution's original attachment.
    """
    schema = artifact_form_schema(artifact_type)
    doc = Document()
    title = doc.add_heading(schema.get("title", artifact_type), level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph("PaperGuard 结构化填充快照")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(f"材料类型：{artifact_type}    生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    if cohort:
        doc.add_paragraph(f"批次：{cohort.get('name', '')}")
    if participant:
        doc.add_paragraph(f"阶段任务：{participant.get('id', '')}")
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = "字段"
    table.rows[0].cells[1].text = "填写内容"
    for field in schema.get("fields", []):
        if field.get("type") == "table":
            doc.add_heading(field["label"], level=1)
            columns = field.get("columns", [])
            detail = doc.add_table(rows=1, cols=max(1, len(columns)))
            detail.style = "Table Grid"
            for index, column in enumerate(columns):
                detail.rows[0].cells[index].text = column["label"]
            rows = _table_rows(data, field)
            if rows:
                for item in rows:
                    cells = detail.add_row().cells
                    for index, column in enumerate(columns):
                        cells[index].text = _display(item.get(column["key"]))
            else:
                detail.add_row().cells[0].text = "暂无明细"
            continue
        row = table.add_row().cells
        row[0].text = field["label"]
        row[1].text = _display(_value(data, field["key"]))
    note = data.get("content") or data.get("note")
    if note:
        doc.add_heading("补充说明", level=1)
        doc.add_paragraph(_display(note))
    doc.save(output)


def render_artifact_xlsx(artifact_type: str, data: dict[str, Any], output: Path,
                         *, participant: dict | None = None, cohort: dict | None = None) -> None:
    """Write a dependency-free XLSX snapshot for batch registers and score sheets."""
    schema = artifact_form_schema(artifact_type)
    rows = [[schema.get("title", artifact_type), "PaperGuard 结构化填充快照"]]
    rows.append(["材料类型", artifact_type])
    if cohort:
        rows.append(["批次", cohort.get("name", "")])
    if participant:
        rows.append(["阶段任务", participant.get("id", "")])
    rows.append(["字段", "填写内容"])
    for field in schema.get("fields", []):
        if field.get("type") == "table":
            columns = field.get("columns", [])
            rows.append([field["label"]])
            rows.append([column["label"] for column in columns])
            detail_rows = _table_rows(data, field)
            if detail_rows:
                rows.extend([
                    [_display(item.get(column["key"])) for column in columns]
                    for item in detail_rows
                ])
            else:
                rows.append(["暂无明细"])
            continue
        rows.append([field["label"], _display(_value(data, field["key"]))])

    sheet_rows = []
    for row_number, row in enumerate(rows, 1):
        cells = []
        for column_number, value in enumerate(row, 1):
            column = _excel_column_name(column_number)
            text = escape(str(value or ""))
            cells.append(f'<c r="{column}{row_number}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    workbook = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="填写表" sheetId="1" r:id="rId1"/></sheets></workbook>'
    content_types = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    workbook_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", content_types)
        bundle.writestr("_rels/.rels", rels)
        bundle.writestr("xl/workbook.xml", workbook)
        bundle.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        bundle.writestr("xl/worksheets/sheet1.xml", sheet)


def artifact_file_extension(artifact_type: str) -> str:
    item = catalog_item(artifact_type) or {}
    suffix = Path(str(item.get("source", ""))).suffix.lower()
    return suffix if suffix in {".docx", ".xlsx", ".pdf", ".pptx"} else ".docx"
