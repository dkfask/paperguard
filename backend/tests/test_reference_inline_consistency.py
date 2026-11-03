"""Reference numbering must preserve and independently verify inline formatting."""
import io

import pytest
from docx import Document
from lxml import etree

from app.api.projects import _apply_imported_docx
from app.core.export.docx_exporter import export_project_to_docx
from app.core.extract.xml_utils import NS, qn
from app.models.project import InlineTextRun, ParagraphBlock, ProjectSection, ReferenceEntry, WritingProject
from app.models.template import PaperTemplate, SectionType, TemplateSection, TextStyle
from app.services.docx_output_check import inspect_docx_output
from app.services.manuscript_template import derive_manuscript_template
from tests.test_manuscript_template import manuscript_case
from tests.test_source_template_profile import rewrite


@pytest.fixture(params=["unprefixed", "existing", "normalized", "structured"])
def reference_block(request):
    prefix = {"unprefixed": "", "existing": "[8] ", "normalized": "  ［ 8 ］  ", "structured": ""}[request.param]
    suffix = "  " if request.param == "normalized" else ""
    block = ParagraphBlock(
        id="ref-inline", text=prefix + "作者. 学报[J]" + suffix,
        reference_number=8,
        inline_runs=[InlineTextRun(text=prefix + "作者. "), InlineTextRun(text="学报", italic=True),
                     InlineTextRun(text="[J]" + suffix)],
    )
    if request.param == "structured":
        block.reference = ReferenceEntry(kind="unparsed", unformatted_text="作者. 学报[J]")
    return block


def _generic_case(tmp_path, block):
    template = PaperTemplate(id="refs", name="文献测试",
        sections=[TemplateSection(key="references", type=SectionType.REFERENCES, title="参考文献")],
        styles={"body": TextStyle(first_line_indent_chars=0), "references": TextStyle(first_line_indent_chars=0)})
    project = WritingProject(id="refs", template_id=template.id, sections=[
        ProjectSection(key="references", type="references", title="参考文献", paragraphs=[block])])
    output = tmp_path / "refs.docx"
    export_project_to_docx(project, template, str(output))
    doc = Document(output)
    for section in doc.sections:
        for paragraph in section.footer.paragraphs:
            paragraph.clear()
    payload = io.BytesIO()
    doc.save(payload)
    return project, template, payload.getvalue()


def test_numbered_reference_preserves_format_and_checker_detects_tampering(tmp_path, reference_block):
    project, template, data = _generic_case(tmp_path, reference_block)
    report = inspect_docx_output(data, project, template)
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]
    doc = Document(io.BytesIO(data))
    paragraph = next(item for item in doc.paragraphs if item.text == "[8] 作者. 学报[J]")
    assert next(run for run in paragraph.runs if run.text == "学报").italic is True
    next(run for run in paragraph.runs if run.text == "学报").italic = False
    modified = io.BytesIO()
    doc.save(modified)
    report = inspect_docx_output(modified.getvalue(), project, template)
    assert report["status"] == "fail"
    assert any(item["rule"] == "format.italic" and item["status"] == "fail" for item in report["checks"])


def test_master_reference_preserves_format_and_checker_detects_tampering(manuscript_case, tmp_path, reference_block):
    path, template, imported, _ = manuscript_case
    derived, payload = derive_manuscript_template(template, path, imported, actor="student")
    source = tmp_path / derived.source_docx
    source.write_bytes(payload)
    project = WritingProject(id="master-refs", template_id=derived.id,
        template_snapshot=derived.model_dump(mode="json"))
    _apply_imported_docx(project, imported)
    next(section for section in project.sections if section.type == "references").paragraphs = [reference_block]
    output = tmp_path / "master-refs.docx"
    export_project_to_docx(project, derived, str(output), template_path=str(source))
    data = output.read_bytes()
    report = inspect_docx_output(data, project, derived, source_template_bytes=payload)
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]

    def remove_italic(root):
        run = next(run for run in root.xpath('.//w:r', namespaces=NS)
                   if ''.join(run.xpath('./w:t/text()', namespaces=NS)) == '学报')
        rpr = run.find(qn('w:rPr'))
        italic = rpr.find(qn('w:i'))
        assert italic is not None
        rpr.remove(italic)

    tampered = rewrite(data, "word/document.xml", remove_italic)
    report = inspect_docx_output(tampered, project, derived, source_template_bytes=payload)
    assert report["status"] == "fail"
    assert any(item["rule"] == "master.inline_decoration" and item["status"] == "fail" for item in report["checks"])


def test_structured_reference_with_unmappable_runs_fails_closed(tmp_path):
    block = ParagraphBlock(id="ref", text="旧的作者. 旧题名", reference_number=1,
        inline_runs=[InlineTextRun(text="旧的作者. "), InlineTextRun(text="旧题名", italic=True)],
        reference=ReferenceEntry(kind="journal", authors="新作者", title="新题名", source="期刊", year="2025"))
    with pytest.raises(ValueError, match="行内格式"):
        _generic_case(tmp_path, block)


def test_numbered_reference_with_styled_existing_prefix_preserves_its_format(tmp_path):
    block = ParagraphBlock(id="styled-ref", text=" ［ 8 ］  作者. 学报[J] ", reference_number=8,
        inline_runs=[InlineTextRun(text=" ［ 8 ］", bold=True), InlineTextRun(text="  作者. "),
                     InlineTextRun(text="学报", italic=True), InlineTextRun(text="[J] ")])
    project, template, data = _generic_case(tmp_path, block)
    report = inspect_docx_output(data, project, template)
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]
    paragraph = next(item for item in Document(io.BytesIO(data)).paragraphs if item.text == "[8] 作者. 学报[J]")
    assert paragraph.runs[0].text == "[8]" and paragraph.runs[0].bold is True
    assert next(run for run in paragraph.runs if run.text == "学报").italic is True


def test_numbered_reference_with_ambiguous_prefix_format_fails_closed(tmp_path):
    block = ParagraphBlock(id="ambiguous-ref", text="［8］ 作者", reference_number=8,
        inline_runs=[InlineTextRun(text="［"), InlineTextRun(text="8", bold=True), InlineTextRun(text="］ 作者")])
    with pytest.raises(ValueError, match="编号的行内格式"):
        _generic_case(tmp_path, block)
