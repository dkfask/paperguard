from __future__ import annotations

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.core.export.docx_exporter import _export_paragraph
from app.models.project import ParagraphBlock
from app.models.template import TextStyle
from app.services.docx_project_import import import_docx_project


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
