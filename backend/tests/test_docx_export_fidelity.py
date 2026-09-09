"""导出模板节设置的回归测试。"""

from __future__ import annotations

from docx import Document
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import pytest

from app.core.export.docx_exporter import (
    _add_page_number_footer,
    export_project_to_docx,
)
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import CoverBlock, CoverField, DEFAULT_STYLES, PaperTemplate, SectionType


def _set_page_number_format(section, value: str) -> None:
    element = OxmlElement("w:pgNumType")
    element.set(qn("w:fmt"), value)
    element.set(qn("w:start"), "1")
    section._sectPr.append(element)


def test_export_preserves_cover_front_and_body_section_layouts(tmp_path):
    source = Document()
    source.sections[0].left_margin = source.sections[0].right_margin = 2 * source.sections[0].left_margin

    front = source.add_section(WD_SECTION.NEW_PAGE)
    front.left_margin = front.right_margin = source.sections[0].left_margin
    front.header.is_linked_to_previous = False
    front.footer.is_linked_to_previous = False
    front.header.paragraphs[0].text = "FRONT HEADER"
    _add_page_number_footer(source, front)
    _set_page_number_format(front, "upperRoman")

    body = source.add_section(WD_SECTION.NEW_PAGE)
    body.left_margin = body.right_margin = source.sections[0].left_margin
    body.header.is_linked_to_previous = False
    body.footer.is_linked_to_previous = False
    body.header.paragraphs[0].text = "BODY HEADER"
    _add_page_number_footer(source, body)
    _set_page_number_format(body, "decimal")
    source_path = tmp_path / "multi-section-template.docx"
    source.save(source_path)

    styles = {k: v.model_copy() for k, v in DEFAULT_STYLES.items()}
    styles["toc"].page_break_before = False
    template = PaperTemplate(
        id="fidelity-template",
        name="保真测试模板",
        styles=styles,
        meta_fields=[],
        cover_blocks=[CoverBlock(kind="text", text="封面"), CoverBlock(
            kind="table",
            fields=[
                CoverField(key="title", label="题目："),
                CoverField(key="author", label="姓名："),
            ],
        )],
        sections=[
            {"key": "cover", "type": SectionType.COVER, "title": "封面"},
            {"key": "abstract", "type": SectionType.ABSTRACT, "title": "摘要"},
            {"key": "toc", "type": SectionType.TOC, "title": "目录"},
            {"key": "ch1", "type": SectionType.HEADING1, "title": "第一章"},
        ],
    )
    project = WritingProject(
        id="fidelity-project",
        template_id=template.id,
        template_name=template.name,
        meta={"title": "测试论文", "author": "测试作者"},
        sections=[
            ProjectSection(
                key="abstract", type=SectionType.ABSTRACT, title="摘要",
                paragraphs=[ParagraphBlock(id="abstract-p1", text="摘要正文")],
            ),
            ProjectSection(
                key="ch1", type=SectionType.HEADING1, title="第一章",
                paragraphs=[ParagraphBlock(id="ch1-p1", text="正文内容")],
            ),
        ],
    )
    output_path = tmp_path / "export.docx"
    export_project_to_docx(project, template, str(output_path), template_path=source_path)

    exported = Document(output_path)
    assert len(exported.sections) == 3
    assert "FRONT HEADER" not in "".join(p.text for p in exported.sections[0].header.paragraphs)
    assert "FRONT HEADER" in "".join(p.text for p in exported.sections[1].header.paragraphs)
    assert "BODY HEADER" in "".join(p.text for p in exported.sections[2].header.paragraphs)
    assert "PAGE" not in exported.sections[0].footer._element.xml
    assert "PAGE" in exported.sections[1].footer._element.xml
    assert "PAGE" in exported.sections[2].footer._element.xml
    assert 'w:updateFields' in exported.settings._element.xml
    assert "请右键更新目录" in "".join(p.text for p in exported.paragraphs)
    assert not any("w:br" in paragraph._p.xml for paragraph in exported.paragraphs)

    cover_borders = exported.tables[0].cell(0, 0)._tc.tcPr.find(qn("w:tcBorders"))
    assert cover_borders is not None
    assert cover_borders.find(qn("w:bottom")).get(qn("w:val")) == "single"
    assert cover_borders.find(qn("w:top")) is None
    second_row_borders = exported.tables[0].cell(1, 0)._tc.tcPr.find(qn("w:tcBorders"))
    assert second_row_borders.find(qn("w:top")).get(qn("w:val")) == "single"


def test_export_applies_template_indent_and_run_decorations(tmp_path):
    """项目模板中的悬挂缩进、高亮和上下标应写入最终 DOCX。"""
    styles = {k: v.model_copy() for k, v in DEFAULT_STYLES.items()}
    styles["body"].hanging_indent_cm = 0.74
    styles["body"].first_line_indent_chars = 0.0
    styles["body"].highlight = "yellow"
    styles["body"].vert_align = "superscript"
    template = PaperTemplate(
        id="decorations-template",
        name="装饰格式测试模板",
        styles=styles,
        meta_fields=[],
        sections=[{"key": "ch1", "type": SectionType.HEADING1, "title": "第一章"}],
    )
    project = WritingProject(
        id="decorations-project",
        template_id=template.id,
        template_name=template.name,
        sections=[ProjectSection(
            key="ch1", type=SectionType.HEADING1, title="第一章",
            paragraphs=[ParagraphBlock(id="ch1-p1", text="参考文献条目")],
        )],
    )
    output_path = tmp_path / "decorations.docx"
    export_project_to_docx(project, template, str(output_path))

    exported = Document(output_path)
    paragraph = next(p for p in exported.paragraphs if p.text == "参考文献条目")
    assert paragraph.paragraph_format.first_line_indent.cm == pytest.approx(-0.74, abs=0.02)
    run = paragraph.runs[0]
    assert str(run.font.highlight_color).split(" ")[0] == "YELLOW"
    assert run.font.superscript is True
    assert run.font.subscript is False
