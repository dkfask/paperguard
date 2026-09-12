from __future__ import annotations

import zipfile

from docx import Document

from app.models.process_record import (
    ARTIFACT_BY_TYPE,
    MANAGEMENT_ARTIFACT_CATALOG,
    PROCESS_ARTIFACT_CATALOG,
    MANAGEMENT_FORM_SCHEMAS,
    PROCESS_FORM_SCHEMAS,
    artifact_form_schema,
)
from app.services.process_artifacts import render_artifact_docx, render_artifact_xlsx


def _field(schema: dict, key: str) -> dict:
    return next(item for item in schema["fields"] if item["key"] == key)


def test_management_schemas_cover_supplied_batch_table_columns():
    expected = {
        "process_material_list": ("rows", ["序号", "文件名", "备注"]),
        "topic_review_summary": ("rows", ["序号", "学院", "系", "专业", "行政班号", "学号", "姓名"]),
        "topic_summary": ("rows", ["序号", "学院", "系", "专业", "行政班号", "学号", "姓名"]),
        "internship_statistics": ("rows", ["序号", "学院（部门）名称", "承担单位（系）", "学号", "姓名"]),
        "midterm_quality_check": ("rows", ["学生姓名", "学号", "专业", "毕业设计（论文）题目", "文献查阅、调研情况"]),
        "defense_schedule": ("schedule_rows", ["序号", "系部", "班级", "学号", "姓名"]),
        "grade_summary": ("rows", ["序号", "学院", "系", "专业", "行政班号", "学号", "姓名"]),
    }
    for artifact_type, (key, labels) in expected.items():
        field = _field(artifact_form_schema(artifact_type), key)
        assert field["type"] == "table"
        actual = [column["label"] for column in field["columns"]]
        assert actual[: len(labels)] == labels

    direction = artifact_form_schema("topic_direction_review")
    assert [column["label"] for column in _field(direction, "direction_rows")["columns"]] == [
        "适用专业", "选题（方向）", "选题（方向）概述",
    ]
    assert [column["label"] for column in _field(direction, "teacher_rows")["columns"]] == [
        "毕业指导老师", "手机", "QQ", "电子邮件",
    ]


def test_student_score_and_defense_schemas_expose_template_detail_rows():
    assert [column["label"] for column in _field(artifact_form_schema("advisor_score"), "rubric")["columns"]] == [
        "序号", "评审项目", "评审指标", "满分", "评分",
    ]
    assert [column["label"] for column in _field(artifact_form_schema("defense_record"), "question_rows")["columns"]] == [
        "序号", "提问", "回答",
    ]
    assert [column["label"] for column in _field(artifact_form_schema("comprehensive_score"), "components")["columns"]] == [
        "成绩构成", "成绩", "占总成绩比例", "折算成绩",
    ]


def test_every_supplied_process_and_management_artifact_has_a_structured_schema():
    for item in (*PROCESS_ARTIFACT_CATALOG, *MANAGEMENT_ARTIFACT_CATALOG):
        schema = artifact_form_schema(item["artifact_type"])
        assert schema["fields"], item["artifact_type"]
        assert item["artifact_type"] in ARTIFACT_BY_TYPE
        assert item["artifact_type"] in PROCESS_FORM_SCHEMAS or item["artifact_type"] in MANAGEMENT_FORM_SCHEMAS


def test_table_exports_preserve_headers_and_rows(tmp_path):
    data = {
        "rows": [
            {"sequence": 1, "file_name": "任务书.docx", "note": "已确认"},
        ],
        "generated_by": "学院管理员",
    }
    xlsx_path = tmp_path / "materials.xlsx"
    render_artifact_xlsx("process_material_list", data, xlsx_path)
    with zipfile.ZipFile(xlsx_path) as bundle:
        xml = bundle.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "学生过程材料明细" in xml
    assert "序号" in xml and "文件名" in xml and "任务书.docx" in xml

    docx_path = tmp_path / "materials.docx"
    render_artifact_docx("process_material_list", data, docx_path)
    document = Document(docx_path)
    text = "\n".join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    assert "学生过程材料明细" in "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "任务书.docx" in text
