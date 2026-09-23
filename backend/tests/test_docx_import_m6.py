from __future__ import annotations

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.api.projects import _apply_imported_docx
from app.core.export.docx_exporter import _export_paragraph
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import PaperTemplate, TextStyle
from app.services.docx_project_import import import_docx_project
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
    assert [block["text"] for block in blocks] == ["前缀 ", "参考链接", " 后缀"]


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
