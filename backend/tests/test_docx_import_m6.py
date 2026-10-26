from __future__ import annotations

import pytest

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.api.projects import _apply_imported_docx, _parse_par
from app.core.export.docx_exporter import _export_paragraph
from app.models.project import InlineTextRun, ParagraphBlock, ProjectSection, WritingProject
from app.models.template import PaperTemplate, TextStyle
from app.services.docx_project_import import import_docx_project
from app.services.project_versions import project_content_hash
from app.services.project_validation import validate_project_completeness


def _add_equation(paragraph):
    math = OxmlElement("m:oMath")
    run = OxmlElement("m:r")
    text = OxmlElement("m:t")
    text.text = "x"
    run.append(text)
    math.append(run)
    paragraph._p.append(math)


def _add_hyperlink(paragraph, text: str):
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), "example")
    run = OxmlElement("w:r")
    text_node = OxmlElement("w:t")
    text_node.text = text
    run.append(text_node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def test_import_keeps_hyperlink_text_when_mixed_with_regular_text(tmp_path):
    source = Document()
    paragraph = source.add_paragraph("前缀 ")
    _add_hyperlink(paragraph, "参考链接")
    paragraph.add_run(" 后缀")
    path = tmp_path / "hyperlink.docx"
    source.save(path)

    imported = import_docx_project(path.read_bytes())
    blocks = imported["sections"][0]["paragraphs"]
    assert len(blocks) == 1
    assert blocks[0]["text"] == "前缀 参考链接 后缀"


def test_word_automatic_heading_number_is_preserved_in_imported_title(tmp_path):
    source = Document()
    root = source.part.numbering_part.element
    abstract = OxmlElement('w:abstractNum'); abstract.set(qn('w:abstractNumId'), '500')
    level = OxmlElement('w:lvl'); level.set(qn('w:ilvl'), '0')
    for tag, value in [('w:start', '2'), ('w:numFmt', 'decimal'), ('w:lvlText', '第%1章　'), ('w:suff', 'nothing')]:
        node = OxmlElement(tag); node.set(qn('w:val'), value); level.append(node)
    abstract.append(level); root.append(abstract)
    number = OxmlElement('w:num'); number.set(qn('w:numId'), '501')
    aid = OxmlElement('w:abstractNumId'); aid.set(qn('w:val'), '500'); number.append(aid); root.append(number)
    paragraph = source.add_heading('关键技术', 1)
    props = OxmlElement('w:numPr')
    for tag, value in [('w:ilvl', '0'), ('w:numId', '501')]:
        node = OxmlElement(tag); node.set(qn('w:val'), value); props.append(node)
    paragraph._p.get_or_add_pPr().append(props)
    source.add_paragraph('技术内容。')
    path = tmp_path / 'automatic-chapter.docx'; source.save(path)
    imported = import_docx_project(path.read_bytes())
    assert imported['sections'][0]['title'] == '第2章　关键技术'
    assert imported['sections'][0]['type'] == 'heading1'
    assert imported['sections'][0]['paragraphs'][0]['text'] == '技术内容。'


def test_import_groups_inline_runs_and_export_keeps_one_paragraph(tmp_path):
    source = Document()
    paragraph = source.add_paragraph()
    paragraph.add_run("普通文字")
    paragraph.add_run("加粗").bold = True
    paragraph.add_run("，随后")
    paragraph.add_run("斜体").italic = True
    source_path = tmp_path / "inline-runs.docx"
    source.save(source_path)

    imported = import_docx_project(source_path.read_bytes())
    blocks = imported["sections"][0]["paragraphs"]
    assert len(blocks) == 1
    assert blocks[0]["text"] == "普通文字加粗，随后斜体"
    assert [(run["text"], run["bold"], run["italic"]) for run in blocks[0]["inline_runs"]] == [
        ("普通文字", False, False),
        ("加粗", True, False),
        ("，随后", False, False),
        ("斜体", False, True),
    ]

    output = Document()
    _export_paragraph(output, ParagraphBlock.model_validate(blocks[0]), TextStyle())
    output_path = tmp_path / "inline-runs-roundtrip.docx"
    output.save(output_path)
    reloaded = Document(output_path)
    assert len(reloaded.paragraphs) == 1
    assert reloaded.paragraphs[0].text == blocks[0]["text"]
    assert [run.text for run in reloaded.paragraphs[0].runs] == ["普通文字", "加粗", "，随后", "斜体"]
    assert reloaded.paragraphs[0].runs[1].bold is True
    assert reloaded.paragraphs[0].runs[3].italic is True

    edited = ParagraphBlock(
        id="edited-inline-text", text="用户修改后的段落",
        inline_runs=[{"text": "旧的导入文字", "bold": True}],
    )
    edited_output = Document()
    _export_paragraph(edited_output, edited, TextStyle())
    assert len(edited_output.paragraphs) == 1
    assert edited_output.paragraphs[0].text == "用户修改后的段落"
    assert len(edited_output.paragraphs[0].runs) == 1
    assert edited_output.paragraphs[0].runs[0].bold is False


def test_section_save_keeps_inline_runs_only_when_text_still_matches():
    imported = {
        "id": "saved-inline-text",
        "text": "普通加粗",
        "inline_runs": [
            {"text": "普通", "bold": False},
            {"text": "加粗", "bold": True},
        ],
    }
    saved = _parse_par(imported, "project-inline-text")
    assert "".join(run.text for run in saved.inline_runs) == saved.text
    assert saved.inline_runs[1].bold is True

    edited = _parse_par(imported | {"text": "修改后的段落"}, "project-inline-text")
    assert edited.text == "修改后的段落"
    assert edited.inline_runs == []


def test_empty_inline_runs_do_not_change_legacy_project_hash():
    project = WritingProject(
        id="project-inline-hash", template_id="template-inline-hash",
        sections=[ProjectSection(
            key="body", type="body", title="正文",
            paragraphs=[ParagraphBlock(id="body-1", text="稳定内容")],
        )],
    )
    legacy_snapshot = project.model_dump(mode="json")

    def remove_inline_defaults(value):
        if isinstance(value, dict):
            value.pop("inline_runs", None)
            for child in value.values():
                remove_inline_defaults(child)
        elif isinstance(value, list):
            for child in value:
                remove_inline_defaults(child)

    remove_inline_defaults(legacy_snapshot)
    assert project_content_hash(project) == project_content_hash(legacy_snapshot)

    project.sections[0].paragraphs[0].inline_runs = [InlineTextRun(text="稳定", bold=True)]
    assert project_content_hash(project) != project_content_hash(legacy_snapshot)


def test_import_preserves_reference_section_and_office_math(tmp_path):
    source = Document()
    source.add_heading("参考文献", level=1)
    source.add_paragraph("[1] 示例文献")
    equation_paragraph = source.add_paragraph()
    _add_equation(equation_paragraph)
    path = tmp_path / "manuscript.docx"
    source.save(path)

    imported = import_docx_project(path.read_bytes())
    references = next(section for section in imported["sections"] if section["key"] == "references")
    assert references["type"] == "references"
    equation = next(block for section in imported["sections"] for block in section["paragraphs"] if block.get("type") == "equation")
    assert "oMath" in equation["equation"]["omml"]
    assert equation["equation"]["alt_text"] == "x"
    assert imported["stats"]["equations"] == 1


def test_import_routes_cover_abstracts_and_skips_table_of_contents(tmp_path):
    source = Document()
    source.add_paragraph("成都东软学院")
    source.add_paragraph("本科毕业设计报告")
    source.add_paragraph("题目：示例论文")
    source.add_paragraph("摘 要")
    source.add_paragraph("中文摘要正文。")
    source.add_paragraph("关键词：嵌入式系统")
    source.add_paragraph("Abstract")
    source.add_paragraph("English abstract text.")
    source.add_paragraph("Key Words: embedded system")
    source.add_paragraph("目 录")
    toc_style = source.styles.add_style("TOC Test", WD_STYLE_TYPE.PARAGRAPH)
    source.add_paragraph("第1章 绪论 1", style=toc_style)
    source.add_heading("第1章 绪论", level=1)
    source.add_heading("1.1 研究背景", level=2)
    source.add_paragraph("研究背景正文。")
    path = tmp_path / "front-matter.docx"
    source.save(path)

    imported = import_docx_project(path.read_bytes())
    assert [section["type"] for section in imported["sections"]] == [
        "abstract", "english_abstract", "heading1", "heading2",
    ]
    assert "中文摘要正文。" in "".join(block.get("text", "") for block in imported["sections"][0]["paragraphs"])
    assert "关键词：嵌入式系统" in "".join(block.get("text", "") for block in imported["sections"][0]["paragraphs"])
    assert "English abstract text." in "".join(block.get("text", "") for block in imported["sections"][1]["paragraphs"])
    assert all("目录" not in section["title"] and "导入正文" != section["title"] for section in imported["sections"])


def test_apply_import_maps_by_section_number_and_keeps_source_order(tmp_path):
    source = Document()
    source.add_paragraph("本科毕业设计报告")
    source.add_paragraph("摘 要")
    source.add_paragraph("中文摘要正文。")
    source.add_paragraph("Abstract")
    source.add_paragraph("English abstract text.")
    source.add_paragraph("目 录")
    source.add_paragraph("第1章 绪论 1")
    source.add_heading("第1章 绪论", level=1)
    source.add_heading("1.1 研究背景", level=2)
    source.add_paragraph("研究背景正文。")
    source.add_heading("第2章 技术", level=1)
    source.add_heading("2.1 硬件", level=2)
    source.add_paragraph("硬件正文。")
    path = tmp_path / "numbered-manuscript.docx"
    source.save(path)

    project = WritingProject(
        id="project-import-order", template_id="template-import-order",
        sections=[
            ProjectSection(key="cover", type="cover", title="封面", required=False),
            ProjectSection(key="toc", type="toc", title="目录", required=False),
            ProjectSection(key="abstract", type="abstract", title="摘  要", required=True),
            ProjectSection(key="english_abstract", type="english_abstract", title="Abstract", required=True),
            ProjectSection(key="ch1", type="heading1", title="第1章　绪论", required=True),
            ProjectSection(key="ch2", type="heading1", title="第2章 关键技术", required=True),
            ProjectSection(key="ch2_1", type="heading2", title="2.1 感知技术", required=False),
        ],
    )
    imported = import_docx_project(path.read_bytes())
    merge = _apply_imported_docx(project, imported)

    assert merge["matched_section_keys"] == ["abstract", "ch1", "ch2", "ch2_1", "english_abstract"]
    assert [section.title for section in project.sections] == [
        "封面", "目录", "摘 要", "Abstract", "第1章 绪论", "1.1 研究背景", "第2章 技术", "2.1 硬件",
    ]
    assert "中文摘要正文。" in "".join(block.text for block in project.sections[2].paragraphs)
    assert "English abstract text." in "".join(block.text for block in project.sections[3].paragraphs)
    assert "研究背景正文。" in "".join(block.text for block in project.sections[5].paragraphs)
    assert "硬件正文。" in "".join(block.text for block in project.sections[7].paragraphs)
    assert validate_project_completeness(project, PaperTemplate(id="template-import-order", name="test")) == []


def test_frozen_master_does_not_consume_future_heading_for_unmatched_source_title():
    def paragraph(block_id: str, text: str) -> dict:
        return ParagraphBlock(id=block_id, text=text).model_dump(mode="json")

    project = WritingProject(
        id="project-frozen-heading-order", template_id="template-frozen-heading-order",
        template_snapshot={"source_execution": {"profile_sha256": "profile-test"}},
        sections=[
            ProjectSection(key="body", type="body", title="正文", required=False),
            ProjectSection(key="ch1", type="heading1", title="第1章 绪论"),
            ProjectSection(key="ch2", type="heading1", title="第2章 系统设计"),
        ],
    )
    imported = {
        "sections": [
            {"key": "ch1", "type": "heading1", "title": "第1章 绪论", "paragraphs": [paragraph("p1", "第一章正文")]},
            {"key": "extra", "type": "heading1", "title": "实验补充", "paragraphs": [paragraph("p2", "补充正文")]},
            {"key": "ch2", "type": "heading1", "title": "第2章 系统设计", "paragraphs": [paragraph("p3", "第二章正文")]},
        ],
        "metadata": {},
    }

    merge = _apply_imported_docx(project, imported)

    assert merge["added_section_count"] == 0
    assert merge["flattened_section_titles"] == ["实验补充"]
    assert [section.title for section in project.sections] == ["正文", "第1章 绪论", "第2章 系统设计"]
    assert "补充正文" in "".join(block.text for block in project.sections[1].paragraphs)
    assert "第二章正文" in "".join(block.text for block in project.sections[2].paragraphs)
    assert "补充正文" not in "".join(block.text for block in project.sections[2].paragraphs)


def test_frozen_master_routes_keywords_and_rebuilds_table_from_cells():
    project = WritingProject(
        id="project-frozen-import-normalization", template_id="template-frozen-import-normalization",
        template_snapshot={"source_execution": {"profile_sha256": "profile-test"}},
        sections=[
            ProjectSection(key="abstract", type="abstract", title="摘要"),
            ProjectSection(key="keywords", type="keywords", title="关键词"),
            ProjectSection(key="english_abstract", type="english_abstract", title="Abstract"),
            ProjectSection(key="english_keywords", type="english_keywords", title="Key Words"),
            ProjectSection(key="body", type="body", title="正文"),
        ],
    )
    imported = {
        "sections": [
            {"key": "abstract", "type": "abstract", "title": "摘要", "paragraphs": [
                {"id": "abstract-text", "text": "中文摘要内容。关键词：STM32；智能控制", "inline_runs": [
                    {"text": "中文摘要内容。", "bold": False},
                    {"text": "关键词：STM32；智能控制", "bold": True},
                ]},
            ]},
            {"key": "english_abstract", "type": "english_abstract", "title": "Abstract", "paragraphs": [
                {"id": "english-abstract-text", "text": "English abstract. Key Words: STM32; smart control"},
            ]},
            {"key": "import-body", "type": "body", "title": "导入正文", "paragraphs": [
                {"id": "table-1", "type": "table", "table": {
                    "rows": 1, "cols": 2, "cells": [["指标", "结果"]], "source_xml": "<w:tbl/>",
                }},
            ]},
        ],
        "metadata": {},
    }

    merge = _apply_imported_docx(project, imported)

    assert merge["preserved_section_order"] is True
    assert merge["added_section_count"] == 0
    assert [section.key for section in project.sections] == [
        "abstract", "keywords", "english_abstract", "english_keywords", "body",
    ]
    assert [block.text for block in project.sections[0].paragraphs] == ["中文摘要内容。"]
    assert [block.text for block in project.sections[1].paragraphs] == ["STM32；智能控制"]
    assert project.sections[1].paragraphs[0].inline_runs == []
    assert [block.text for block in project.sections[2].paragraphs] == ["English abstract."]
    assert [block.text for block in project.sections[3].paragraphs] == ["STM32; smart control"]
    table = project.sections[4].paragraphs[0].table
    assert table is not None
    assert table.cells == [["指标", "结果"]]
    assert table.source_xml == ""
    assert merge["normalized_source_table_count"] == 1


def test_frozen_master_assigns_unnumbered_chapter_to_gap_without_shifting_later_matches():
    def paragraph(block_id: str, text: str) -> dict:
        return ParagraphBlock(id=block_id, text=text).model_dump(mode="json")

    project = WritingProject(
        id="project-frozen-heading-gap", template_id="template-frozen-heading-gap",
        template_snapshot={"source_execution": {"profile_sha256": "profile-test"}},
        sections=[
            ProjectSection(key="ch1", type="heading1", title="第一章 绪论"),
            ProjectSection(key="ch2", type="heading1", title="第二章 技术基础"),
            ProjectSection(key="ch3", type="heading1", title="第三章 系统分析"),
        ],
    )
    imported = {
        "sections": [
            {"key": "ch1", "type": "heading1", "title": "1 绪论", "paragraphs": [paragraph("p1", "第一章正文")]},
            {"key": "ch2", "type": "heading1", "title": "关键技术", "paragraphs": [paragraph("p2", "关键技术正文")]},
            {"key": "ch3", "type": "heading1", "title": "第3章 系统分析", "paragraphs": [paragraph("p3", "第三章正文")]},
        ],
        "metadata": {},
    }

    merge = _apply_imported_docx(project, imported)

    assert merge["added_section_count"] == 0
    assert [section.key for section in project.sections] == ["ch1", "ch2", "ch3"]
    assert "关键技术正文" in "".join(block.text for block in project.sections[1].paragraphs)
    assert "第三章正文" in "".join(block.text for block in project.sections[2].paragraphs)
    assert "关键技术正文" not in "".join(block.text for block in project.sections[2].paragraphs)


def test_frozen_master_never_maps_heading_to_keyword_section_and_preserves_pending_content():
    project = WritingProject(
        id="project-frozen-heading-type", template_id="template-frozen-heading-type",
        template_snapshot={"source_execution": {"profile_sha256": "profile-test"}},
        sections=[
            ProjectSection(key="keywords", type="keywords", title="关键词"),
            ProjectSection(key="ch1", type="heading1", title="第1章 绪论"),
        ],
    )
    imported = {
        "sections": [
            {"key": "import-body", "type": "body", "title": "导入正文", "paragraphs": [
                {"id": "preface", "text": "章节前置文字"},
            ]},
            {"key": "misleading-title", "type": "heading1", "title": "关键词", "paragraphs": [
                {"id": "misleading-content", "text": "这是一级章节，不是关键词"},
            ]},
            {"key": "ch1", "type": "heading1", "title": "第1章 绪论", "paragraphs": [
                {"id": "ch1-content", "text": "第一章正文"},
            ]},
        ],
        "metadata": {},
    }

    merge = _apply_imported_docx(project, imported)

    assert merge["added_section_count"] == 0
    assert merge["unplaced_section_titles"] == []
    assert [section.key for section in project.sections] == ["keywords", "ch1"]
    assert project.sections[0].paragraphs == []
    chapter_text = "".join(block.text for block in project.sections[1].paragraphs)
    assert all(text in chapter_text for text in ("章节前置文字", "这是一级章节，不是关键词", "第一章正文"))


def test_frozen_master_rebuilds_inflated_project_sections_from_template_snapshot():
    project = WritingProject(
        id="project-frozen-restore-structure", template_id="template-frozen-restore-structure",
        template_snapshot={
            "id": "template-frozen-restore-structure", "name": "冻结模板",
            "source_execution": {"profile_sha256": "profile-test"},
            "sections": [
                {"key": "cover", "type": "cover", "title": "封面", "children": []},
                {"key": "toc", "type": "toc", "title": "目录", "children": []},
                {"key": "abstract", "type": "abstract", "title": "摘要", "children": []},
                {"key": "ch1", "type": "heading1", "title": "第1章 绪论", "children": []},
                {"key": "ch2", "type": "heading1", "title": "第2章 系统设计", "children": []},
            ],
        },
        sections=[
            ProjectSection(key="abstract", type="abstract", title="旧摘要", paragraphs=[ParagraphBlock(id="old-a", text="旧导入")]),
            ProjectSection(key="ch1", type="heading1", title="旧章节", paragraphs=[ParagraphBlock(id="old-1", text="旧导入")]),
            ProjectSection(key="ch2", type="heading1", title="旧章节", paragraphs=[ParagraphBlock(id="old-2", text="旧导入")]),
            ProjectSection(key="stale-import-section", type="heading1", title="旧的临时章节", paragraphs=[ParagraphBlock(id="old-x", text="旧导入")]),
        ],
    )
    imported = {
        "sections": [
            {"key": "abstract", "type": "abstract", "title": "摘要", "paragraphs": [{"id": "a", "text": "摘要正文"}]},
            {"key": "ch1", "type": "heading1", "title": "第1章 绪论", "paragraphs": [{"id": "one", "text": "第一章正文"}]},
            {"key": "ch2", "type": "heading1", "title": "第2章 系统设计", "paragraphs": [{"id": "two", "text": "第二章正文"}]},
        ],
        "metadata": {},
    }

    _apply_imported_docx(project, imported)

    assert [section.key for section in project.sections] == ["abstract", "ch1", "ch2"]
    assert [section.title for section in project.sections] == ["摘要", "第1章 绪论", "第2章 系统设计"]
    assert [section.paragraphs[0].text for section in project.sections] == ["摘要正文", "第一章正文", "第二章正文"]


def test_frozen_master_rejects_complex_imported_table_xml():
    project = WritingProject(
        id="project-frozen-complex-table", template_id="template-frozen-complex-table",
        template_snapshot={"source_execution": {"profile_sha256": "profile-test"}},
        sections=[ProjectSection(key="body", type="body", title="正文")],
    )
    imported = {
        "sections": [{"key": "import-body", "type": "body", "title": "正文", "paragraphs": [
            {"id": "complex-table", "type": "table", "table": {
                "rows": 1, "cols": 1, "cells": [["合并单元格"]],
                "source_xml": '<w:tbl><w:tr><w:tc><w:gridSpan w:val="2"/></w:tc></w:tr></w:tbl>',
            }},
        ]}],
        "metadata": {},
    }

    with pytest.raises(ValueError, match="合并单元格"):
        _apply_imported_docx(project, imported)


def test_export_writes_retained_office_math(tmp_path):
    block = ParagraphBlock(
        id="equation-1", type="equation",
        equation={"omml": '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x</m:t></m:r></m:oMath>', "alt_text": "x"},
    )
    document = Document()
    _export_paragraph(document, block, TextStyle())
    output = tmp_path / "roundtrip.docx"
    document.save(output)
    reloaded = Document(output)
    assert "oMath" in reloaded.paragraphs[0]._p.xml


def test_complex_table_source_xml_is_kept_for_unedited_roundtrip(tmp_path):
    source = Document()
    table = source.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "合并表头"
    table.cell(1, 0).text = "指标"
    table.cell(1, 1).text = "结果"
    path = tmp_path / "table.docx"
    source.save(path)

    imported = import_docx_project(path.read_bytes())
    raw = next(block for section in imported["sections"] for block in section["paragraphs"] if block.get("type") == "table")
    assert raw["table"]["source_xml"]
    block = ParagraphBlock.model_validate(raw)
    output = tmp_path / "table-roundtrip.docx"
    document = Document()
    _export_paragraph(document, block, TextStyle())
    document.save(output)
    xml = Document(output).tables[0]._tbl.xml
    assert "合并表头" in xml and "gridSpan" in xml


def test_imported_table_source_xml_drops_orphaned_comment_markers(tmp_path):
    source = Document()
    table = source.add_table(rows=1, cols=1)
    paragraph = table.cell(0, 0).paragraphs[0]
    paragraph.add_run("保留的表格文本")
    start = OxmlElement("w:commentRangeStart")
    start.set(qn("w:id"), "0")
    end = OxmlElement("w:commentRangeEnd")
    end.set(qn("w:id"), "0")
    reference_run = OxmlElement("w:r")
    reference = OxmlElement("w:commentReference")
    reference.set(qn("w:id"), "0")
    reference_run.append(reference)
    paragraph._p.insert(0, start)
    paragraph._p.append(end)
    paragraph._p.append(reference_run)
    path = tmp_path / "table-with-comment-markers.docx"
    source.save(path)

    imported = import_docx_project(path.read_bytes())
    raw = next(
        block for section in imported["sections"] for block in section["paragraphs"]
        if block.get("type") == "table"
    )["table"]["source_xml"]

    assert "保留的表格文本" in raw
    assert "commentRangeStart" not in raw
    assert "commentRangeEnd" not in raw
    assert "commentReference" not in raw
    # Sanitizing imported XML must leave the user's source file untouched.
    assert "commentRangeStart" in Document(path).tables[0]._tbl.xml


def test_import_extracts_cover_values_for_round_trip(tmp_path):
    source = Document()
    source.add_paragraph("本科毕业设计报告")
    source.add_paragraph("题目：测试论文")
    cover_table = source.add_table(rows=2, cols=2)
    cover_table.cell(0, 0).text = "学    院："
    cover_table.cell(0, 1).text = "计算机学院"
    cover_table.cell(1, 0).text = "学生姓名："
    cover_table.cell(1, 1).text = "王同学"
    source.add_paragraph("2026年5月")
    source.add_paragraph("摘  要")
    source.add_paragraph("摘要正文不属于封面")
    path = tmp_path / "cover-values.docx"
    source.save(path)

    imported = import_docx_project(path.read_bytes())

    assert imported["metadata"]["title"] == "测试论文"
    assert imported["metadata"]["college"] == "计算机学院"
    assert imported["metadata"]["author"] == "王同学"
    assert imported["metadata"]["date"] == "2026年5月"
    assert "摘要正文不属于封面" not in imported["metadata"].values()


def test_optional_image_geometry_defaults_do_not_change_legacy_project_hash():
    project = WritingProject(
        id="project-image-hash", template_id="template-image-hash",
        sections=[ProjectSection(
            key="body", type="body", title="正文",
            paragraphs=[ParagraphBlock(
                id="figure", type="image",
                image={"url": "/media/uploads/figure.png", "caption": "图1 系统图"},
            )],
        )],
    )
    legacy_snapshot = project.model_dump(mode="json")
    image = legacy_snapshot["sections"][0]["paragraphs"][0]["image"]
    for key in ("width_cm", "height_cm", "alignment"):
        image.pop(key, None)

    assert project_content_hash(project) == project_content_hash(legacy_snapshot)
