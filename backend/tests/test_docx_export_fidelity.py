"""导出模板节设置的回归测试。"""

from __future__ import annotations

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm
from PIL import Image
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import pytest

from app.core.export import docx_exporter
from app.core.export.docx_exporter import (
    _add_page_number_footer,
    _export_paragraph,
    _open_base_document,
    export_project_to_docx,
)
from app.models.project import BlockType, ImageBlock, ParagraphBlock, ProjectSection, ReferenceEntry, TableBlock, WritingProject
from app.models.template import CoverBlock, CoverField, DEFAULT_STYLES, PaperTemplate, SectionType, TextStyle
from app.services.docx_project_import import import_docx_project


@pytest.mark.parametrize("template_kind", ["missing", "corrupt", "wrong_extension"])
def test_declared_template_never_silently_falls_back_to_blank_document(tmp_path, template_kind):
    path = tmp_path / ("declared.docx" if template_kind != "wrong_extension" else "declared.doc")
    if template_kind == "corrupt":
        path.write_bytes(b"not a Word package")
    elif template_kind == "wrong_extension":
        Document().save(path)
    with pytest.raises(ValueError, match="母版"):
        _open_base_document(path)


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


def test_docx_import_export_preserves_inline_image_geometry_and_alignment(tmp_path, monkeypatch):
    image_path = tmp_path / "source.png"
    Image.new("RGB", (40, 20), color="white").save(image_path)

    source = Document()
    source_paragraph = source.add_paragraph()
    source_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    source_paragraph.add_run().add_picture(str(image_path), width=Cm(4), height=Cm(2))
    source_path = tmp_path / "source-image.docx"
    source.save(source_path)

    imported = import_docx_project(source_path.read_bytes())
    image_block = next(
        block for section in imported["sections"] for block in section["paragraphs"]
        if block.get("type") == "image"
    )
    assert image_block["image"]["width_cm"] == pytest.approx(4.0, abs=0.01)
    assert image_block["image"]["height_cm"] == pytest.approx(2.0, abs=0.01)
    assert image_block["image"]["alignment"] == "center"

    monkeypatch.setattr(docx_exporter, "_resolve_image_path", lambda _: image_path)
    exported_doc = Document()
    paragraph = ParagraphBlock(
        id="preserved-image", type="image",
        image=ImageBlock(url="source.png", **{
            key: value for key, value in image_block["image"].items() if key != "url"
        }),
    )
    _export_paragraph(exported_doc, paragraph, TextStyle())

    exported_shape = exported_doc.inline_shapes[0]
    assert exported_shape.width.cm == pytest.approx(4.0, abs=0.01)
    assert exported_shape.height.cm == pytest.approx(2.0, abs=0.01)
    assert exported_doc.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_export_reuses_cached_toc_only_when_source_content_is_unchanged(tmp_path):
    source = Document()
    source.add_paragraph("目录")
    source.styles.add_style("TOC 1", WD_STYLE_TYPE.PARAGRAPH)
    source.add_paragraph("第一章\t1", style="TOC 1")
    source.add_paragraph("第一章", style="Heading 1")
    source.add_paragraph("正文")
    source_path = tmp_path / "toc-source.docx"
    source.save(source_path)

    styles = {key: value.model_copy() for key, value in DEFAULT_STYLES.items()}
    styles["toc"].page_break_before = False
    template = PaperTemplate(
        id="toc-fidelity-template", name="目录保真测试", styles=styles, meta_fields=[],
        sections=[
            {"key": "toc", "type": SectionType.TOC, "title": "目录"},
            {"key": "ch1", "type": SectionType.HEADING1, "title": "第一章"},
        ],
    )

    def make_project(body_text: str) -> WritingProject:
        return WritingProject(
            id="toc-fidelity-project", template_id=template.id, template_name=template.name,
            sections=[
                ProjectSection(key="toc", type=SectionType.TOC, title="目录"),
                ProjectSection(
                    key="ch1", type=SectionType.HEADING1, title="第一章",
                    paragraphs=[ParagraphBlock(id="body", text=body_text)],
                ),
            ],
        )

    matched_path = tmp_path / "toc-matched.docx"
    export_project_to_docx(make_project("正文"), template, str(matched_path), template_path=source_path)
    matched = Document(matched_path)
    assert "第一章\t1" in [paragraph.text for paragraph in matched.paragraphs]
    assert "请右键更新目录" not in "".join(paragraph.text for paragraph in matched.paragraphs)

    edited_path = tmp_path / "toc-edited.docx"
    export_project_to_docx(make_project("内容已经修改"), template, str(edited_path), template_path=source_path)
    edited = Document(edited_path)
    assert "第一章\t1" not in [paragraph.text for paragraph in edited.paragraphs]
    assert "请右键更新目录" in "".join(paragraph.text for paragraph in edited.paragraphs)


def test_unchanged_source_manuscript_is_copied_without_reflow(tmp_path, monkeypatch):
    source = Document()
    source.add_paragraph("第1章 绪论", style="Heading 1")
    source.add_paragraph("保持原样的正文")
    source_path = tmp_path / "source-manuscript.docx"
    source.save(source_path)

    imported = import_docx_project(source_path.read_bytes())
    project = WritingProject(
        id="source-roundtrip", template_id="source-template", template_name="源文档模板",
        sections=[ProjectSection.model_validate(section) for section in imported["sections"]],
    )
    template = PaperTemplate(
        id="source-template", name="源文档模板", styles=DEFAULT_STYLES,
        sections=[{"key": "ch1", "type": SectionType.HEADING1, "title": "第1章 绪论"}],
    )
    # Keep this test focused on the copy-through guard; full source-template
    # equivalence is covered by the end-to-end reference comparison.
    monkeypatch.setattr(docx_exporter, "_source_template_matches", lambda *_args: True)
    output_path = tmp_path / "roundtrip.docx"

    export_project_to_docx(project, template, str(output_path), template_path=source_path)

    assert output_path.read_bytes() == source_path.read_bytes()

    project.sections[0].title = "第1章 已修改"
    changed_output = tmp_path / "edited.docx"
    export_project_to_docx(project, template, str(changed_output), template_path=source_path)
    assert changed_output.read_bytes() != source_path.read_bytes()
    assert "第1章 已修改" in [paragraph.text for paragraph in Document(changed_output).paragraphs]


def test_changed_toc_title_prevents_source_copy_through(tmp_path, monkeypatch):
    source = Document()
    source.add_paragraph("目录")
    source.styles.add_style("TOC 1", WD_STYLE_TYPE.PARAGRAPH)
    source.add_paragraph("第一章\t1", style="TOC 1")
    source.add_paragraph("第一章", style="Heading 1")
    source.add_paragraph("正文")
    source_path = tmp_path / "toc-copy-source.docx"
    source.save(source_path)

    styles = {key: value.model_copy() for key, value in DEFAULT_STYLES.items()}
    styles["toc"].page_break_before = False
    template = PaperTemplate(
        id="toc-copy-template", name="目录复制保护", styles=styles, meta_fields=[],
        sections=[
            {"key": "toc", "type": SectionType.TOC, "title": "目录"},
            {"key": "ch1", "type": SectionType.HEADING1, "title": "第一章"},
        ],
    )
    project = WritingProject(
        id="toc-copy-project", template_id=template.id, template_name=template.name,
        sections=[
            ProjectSection(key="toc", type=SectionType.TOC, title="项目目录"),
            ProjectSection(
                key="ch1", type=SectionType.HEADING1, title="第一章",
                paragraphs=[ParagraphBlock(id="body", text="正文")],
            ),
        ],
    )
    monkeypatch.setattr(docx_exporter, "_source_template_matches", lambda *_args: True)
    output_path = tmp_path / "toc-copy-guarded.docx"

    export_project_to_docx(project, template, str(output_path), template_path=source_path)

    output = Document(output_path)
    assert output_path.read_bytes() != source_path.read_bytes()
    assert "项目目录" in [paragraph.text for paragraph in output.paragraphs]


def test_references_export_keeps_historical_citation_numbers(tmp_path):
    template = PaperTemplate(
        id="reference-template", name="参考文献测试", styles=DEFAULT_STYLES,
        sections=[{"key": "references", "type": SectionType.REFERENCES, "title": "参考文献"}],
    )
    project = WritingProject(
        id="reference-project", template_id=template.id, template_name=template.name,
        sections=[ProjectSection(
            key="references", type="references", title="参考文献",
            paragraphs=[
                ParagraphBlock(id="ref-1", reference=ReferenceEntry(kind="journal", authors="张三", title="论文甲", source="期刊", year="2024")),
                ParagraphBlock(id="table", type=BlockType.TABLE, table=TableBlock(rows=1, cols=1, cells=[["表格"]])),
                ParagraphBlock(id="ref-2", text="[8] 李四. 论文乙. 2025."),
            ],
        )],
    )
    output_path = tmp_path / "references.docx"

    export_project_to_docx(project, template, str(output_path))

    paragraphs = [paragraph.text for paragraph in Document(output_path).paragraphs]
    assert "[1] 张三. 论文甲[J]. 期刊, 2024" in paragraphs
    assert "[8] 李四. 论文乙. 2025." in paragraphs
