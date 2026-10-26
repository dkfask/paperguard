"""Independent checks over emitted Word files, including negative mutations."""

from io import BytesIO
import zipfile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from lxml import etree
import pytest

from app.core.export.docx_exporter import export_project_to_docx
from app.models.project import BlockType, InlineTextRun, ParagraphBlock, ProjectSection, TableBlock, WritingProject
from app.models.template import Align, PaperTemplate, SectionType, TemplateSection, TextStyle
from app.services.projects import default_template
from app.services.docx_output_check import (
    DocxOutputCheckError,
    TEXT_STYLE_COVERAGE,
    expected_output_check_identity,
    inspect_docx_output,
    require_docx_output_pass,
    verify_report_binding,
    verify_report_identity,
    verify_source_template_binding,
)


def _sample(tmp_path, style=None):
    style = style or TextStyle(first_line_indent_chars=0)
    template = PaperTemplate(
        id="template-output-check",
        name="检查样稿",
        version="3",
        sections=[TemplateSection(key="body", type=SectionType.BODY, title="正文")],
        styles={"body": style},
    )
    project = WritingProject(
        id="project-output-check",
        template_id=template.id,
        revision=7,
        title="检查样稿",
        sections=[ProjectSection(
            key="body",
            type="body",
            title="正文",
            paragraphs=[ParagraphBlock(
                id="paragraph-1",
                text="第一段包含多个 run",
                inline_runs=[
                    {"text": "第一段包含", "bold": False},
                    {"text": "多个 run", "bold": False},
                ],
            )],
        )],
    )
    output = tmp_path / "sample.docx"
    export_project_to_docx(project, template, str(output))
    doc = Document(output)
    for section in doc.sections:
        for paragraph in section.footer.paragraphs:
            paragraph.clear()
    normalized = BytesIO()
    doc.save(normalized)
    output.write_bytes(normalized.getvalue())
    return output.read_bytes(), project, template


def _full_default_gb7713_sample(tmp_path):
    template = default_template()
    meta = {
        "school": "示例大学",
        "title": "格式一致性研究",
        "author": "张同学",
        "student_id": "20261001",
        "major": "计算机科学与技术",
        "advisor": "李老师",
        "date": "2026年10月",
    }
    contents = {
        "abstract": "本文研究论文格式检查与修复方法。",
        "english_abstract": "This thesis studies document format verification.",
        "keywords": "关键词：论文格式；一致性检查",
        "english_keywords": "Key Words: document format; consistency",
        "ch1": "本章介绍研究背景和目标。",
        "ch1_1": "研究背景说明问题来源。",
        "ch1_2": "相关研究提供方法依据。",
        "ch1_3": "本文工作包括检查、诊断与验证。",
        "ch2": "本章介绍系统采用的技术。",
        "ch2_1": "技术概述说明系统边界。",
        "ch2_2": "关键技术支撑格式检查。",
        "ch3": "本章给出系统设计。",
        "ch4": "本章说明系统实现与测试。",
        "ch5": "本章总结研究并讨论后续工作。",
        "references": "[1] 示例作者. 文档格式研究. 2026.",
        "acknowledgement": "感谢指导教师的帮助。",
    }
    sections = []

    def append_nodes(nodes):
        for node in nodes:
            kind = str(getattr(node.type, "value", node.type))
            if kind not in {"cover", "toc"}:
                sections.append(ProjectSection(
                    key=node.key,
                    type=kind,
                    title=node.title,
                    required=node.required,
                    paragraphs=[ParagraphBlock(id=f"{node.key}-p1", text=contents[node.key])],
                ))
            append_nodes(node.children)

    append_nodes(template.sections)
    project = WritingProject(
        id="project-full-gb7713-sample",
        template_id=template.id,
        template_name=template.name,
        title=meta["title"],
        revision=4,
        meta=meta,
        sections=sections,
        template_snapshot=template.model_dump(mode="json"),
    )
    output = tmp_path / "full-default-gb7713.docx"
    export_project_to_docx(project, template, str(output))
    return output.read_bytes(), project, template


def test_generated_docx_passes_and_report_binds_output_bytes(tmp_path):
    data, project, template = _sample(tmp_path)

    report = require_docx_output_pass(data, project, template)

    assert report["status"] == "pass"
    assert report["check_count"] > 0
    assert report["passed_count"] == report["check_count"]
    assert report["project_revision"] == 7
    assert report["template_version"] == "3"
    assert verify_report_binding(report, data)
    assert not verify_report_binding(report, data + b"changed")
    assert not verify_report_binding({**report, "check_count": 0, "passed_count": 0}, data)
    assert not verify_report_binding({**report, "status": "fail"}, data)
    assert not verify_report_binding({**report, "status": "unverifiable"}, data)
    assert not verify_report_binding({**report, "checks": []}, data)


@pytest.mark.parametrize("location", ["body", "header", "extended_chart", "package", "source_only"])
def test_office_charts_fail_closed_even_when_text_and_styles_match(tmp_path, location):
    data, project, template = _sample(tmp_path)
    rebuilt = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as archive, zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as target:
        for name in archive.namelist():
            part = archive.read(name)
            if name == "word/document.xml" and location in {"body", "extended_chart", "source_only"}:
                root = etree.fromstring(part)
                paragraph = root.find(f".//{qn('w:p')}")
                namespace = "http://schemas.microsoft.com/office/drawing/2014/chartex" if location == "extended_chart" else "http://schemas.openxmlformats.org/drawingml/2006/chart"
                etree.SubElement(paragraph, f"{{{namespace}}}chart")
                part = etree.tostring(root)
            target.writestr(name, part)
        if location == "header":
            target.writestr("word/header-chart.xml", b'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/></w:hdr>')
        if location == "package":
            target.writestr("word/charts/chart1.xml", b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/>')
    changed = rebuilt.getvalue()
    output = data if location == "source_only" else changed
    kwargs = {"source_template_bytes": changed} if location == "source_only" else {}

    report = inspect_docx_output(output, project, template, **kwargs)

    assert report["status"] != "pass"
    rule = "capability.source_chart_objects" if location == "source_only" else "capability.chart_objects"
    assert any(item["rule"] == rule and item["status"] == "unverifiable" for item in report["checks"])
    with pytest.raises(DocxOutputCheckError):
        require_docx_output_pass(output, project, template, **kwargs)


def test_output_package_xml_dtd_cannot_receive_a_passing_report(tmp_path):
    data, project, template = _sample(tmp_path)
    rebuilt = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as archive, zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as target:
        for name in archive.namelist():
            target.writestr(name, archive.read(name))
        target.writestr("word/unsafe.xml", b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>')

    report = inspect_docx_output(rebuilt.getvalue(), project, template)

    assert report["status"] == "unverifiable"
    assert report["checks"][0]["rule"] == "docx.open"
    assert "root:" not in str(report)


def test_supported_template_sample_uses_real_exporter_and_passes_without_postprocessing(tmp_path):
    source_path = tmp_path / "supported-source.docx"
    source = Document()
    source.sections[0].footer.paragraphs  # Preserve an intentionally empty footer; no page-number field.
    source.save(source_path)
    template = PaperTemplate(
        id="supported-template-sample",
        name="第一阶段支持范围样稿",
        version="1",
        source_docx=str(source_path),
        sections=[TemplateSection(key="body", type=SectionType.BODY, title="正文")],
        styles={"body": TextStyle(first_line_indent_chars=0, space_before_pt=6, space_after_pt=9)},
    )
    project = WritingProject(
        id="supported-project-sample",
        template_id=template.id,
        revision=1,
        title="支持范围样稿",
        sections=[ProjectSection(
            key="body", type="body", title="正文",
            paragraphs=[ParagraphBlock(id="body-1", text="本样稿仅使用第一阶段支持的纯文本正文格式。")],
        )],
    )
    output_path = tmp_path / "supported-output.docx"
    export_project_to_docx(project, template, str(output_path), template_path=source_path)

    report = require_docx_output_pass(
        output_path.read_bytes(), project, template,
        source_template_bytes=source_path.read_bytes(),
        context_kind="supported-template-sample",
    )

    assert report["status"] == "pass"
    assert report["check_count"] == len(report["checks"]) > 0
    assert not report["unsupported"]
    assert report["source_template_sha256"]


def test_complete_default_gb7713_structure_passes_static_output_checks(tmp_path):
    data, project, template = _full_default_gb7713_sample(tmp_path)

    report = require_docx_output_pass(data, project, template, context_kind="full-default-gb7713")

    rules = [item["rule"] for item in report["checks"]]
    assert report["status"] == "pass"
    assert report["check_count"] == report["passed_count"] > 0
    assert not report["unsupported"]
    assert rules.count("cover.field_content") == len(template.meta_fields)
    assert "structure.heading_semantics" in rules
    assert "structure.toc_field" in rules
    assert "structure.toc_update_on_open" in rules
    assert rules.count("section.page_number_format") == 3
    assert rules.count("section.page_field") == 3
    assert rules.count("section.break_type") == 3
    assert rules.count("page.page_width_cm") == 3
    assert rules.count("page.page_height_cm") == 3
    assert rules.count("page.margin_left_cm") == 3


@pytest.mark.parametrize("mutation", ["cover_field", "toc_field", "page_format", "page_field", "page_layout", "heading_style"])
def test_complete_default_gb7713_structural_mutations_fail(tmp_path, mutation):
    data, project, template = _full_default_gb7713_sample(tmp_path)
    doc = Document(BytesIO(data))
    if mutation == "cover_field":
        paragraph = next(item for item in doc.paragraphs if item.text.startswith("学校名称："))
        paragraph.runs[0].text = "院校名称："
    elif mutation == "toc_field":
        instruction = next(item for item in doc.element.body.iter() if item.tag == qn("w:instrText") and "TOC" in (item.text or ""))
        instruction.text = 'TOC \\o "1-2" \\h \\z \\u'
    elif mutation == "page_format":
        page_number = doc.sections[1]._sectPr.find(qn("w:pgNumType"))
        page_number.set(qn("w:fmt"), "decimal")
    elif mutation == "page_field":
        page_field = next(item for item in doc.sections[1].footer._element.iter() if item.tag == qn("w:fldSimple"))
        page_field.getparent().remove(page_field)
    elif mutation == "page_layout":
        page_margins = doc.sections[2]._sectPr.find(qn("w:pgMar"))
        page_margins.set(qn("w:right"), "700")
    else:
        heading = next(item for item in doc.paragraphs if item.text == "第一章 绪论")
        heading.style = "Normal"
    mutated = BytesIO()
    doc.save(mutated)

    report = inspect_docx_output(mutated.getvalue(), project, template)

    assert report["status"] == "fail"
    target_rule = {
        "cover_field": "cover.field_content",
        "toc_field": "structure.toc_field",
        "page_format": "section.page_number_format",
        "page_field": "section.page_field",
        "page_layout": "page.margin_right_cm",
        "heading_style": "structure.heading_semantics",
    }[mutation]
    assert any(item["rule"] == target_rule and item["status"] == "fail" for item in report["checks"])


def test_supported_scope_template_with_nonzero_paragraph_spacing_passes(tmp_path):
    data, project, template = _sample(tmp_path, TextStyle(
        space_before_pt=6.0,
        space_after_pt=9.0,
        first_line_indent_chars=0,
    ))

    report = require_docx_output_pass(data, project, template)

    assert report["status"] == "pass"
    assert {(item["rule"], item["actual"]) for item in report["checks"] if item["rule"] in {"format.space_before", "format.space_after"}} == {
        ("format.space_before", 6.0), ("format.space_after", 9.0),
    }


def test_wrong_nonzero_paragraph_spacing_fails(tmp_path):
    data, project, template = _sample(tmp_path, TextStyle(space_before_pt=6.0, space_after_pt=9.0))
    doc = Document(BytesIO(data))
    paragraph = next(item for item in doc.paragraphs if item.text == "第一段包含多个 run")
    paragraph.paragraph_format.space_before = Pt(10)
    mutated = BytesIO()
    doc.save(mutated)

    report = inspect_docx_output(mutated.getvalue(), project, template)

    assert report["status"] == "fail"
    assert any(item["rule"] == "format.space_before" and item["status"] == "fail" for item in report["checks"])


def test_character_indent_attributes_take_precedence_over_length_indent(tmp_path):
    data, project, template = _sample(
        tmp_path,
        TextStyle(first_line_indent_chars=2.0),
    )
    doc = Document(BytesIO(data))
    paragraph = next(item for item in doc.paragraphs if item.text == "第一段包含多个 run")
    ind = paragraph._p.get_or_add_pPr().get_or_add_ind()
    ind.set(qn("w:firstLineChars"), "200")
    ind.set(qn("w:firstLine"), "1")
    good = BytesIO()
    doc.save(good)
    good_report = inspect_docx_output(good.getvalue(), project, template)
    assert any(item["rule"] == "format.indent" and item["status"] == "pass" for item in good_report["checks"])

    ind.set(qn("w:firstLineChars"), "100")
    bad = BytesIO()
    doc.save(bad)
    bad_report = inspect_docx_output(bad.getvalue(), project, template)
    indent_checks = [item for item in bad_report["checks"] if item["rule"] == "format.indent"]
    assert any(item["status"] == "fail" for item in indent_checks)


def test_length_hanging_indent_is_checked_in_twips(tmp_path):
    data, project, template = _sample(tmp_path, TextStyle(hanging_indent_cm=0.5))
    report = inspect_docx_output(data, project, template)
    assert any(item["rule"] == "format.indent" and item["status"] == "pass" for item in report["checks"])


def test_exporter_emits_at_least_rule_and_wrong_rule_is_rejected(tmp_path):
    data, project, template = _sample(tmp_path, TextStyle(line_spacing=20, line_spacing_rule="atLeast"))
    passing = require_docx_output_pass(data, project, template)
    assert {item["actual"]["rule"] for item in passing["checks"] if item["rule"] == "format.line_spacing"} == {"atLeast"}

    doc = Document(BytesIO(data))
    for paragraph in doc.paragraphs:
        ppr = paragraph._p.pPr
        spacing = ppr.find(qn("w:spacing")) if ppr is not None else None
        if spacing is not None:
            spacing.set(qn("w:lineRule"), "exact")
    mutated = BytesIO()
    doc.save(mutated)
    rejected = inspect_docx_output(mutated.getvalue(), project, template)
    assert rejected["status"] == "fail"
    assert any(item["rule"] == "format.line_spacing" and item["status"] == "fail" for item in rejected["checks"])


@pytest.mark.parametrize(("rule", "length"), [("exact", 18.0), ("atLeast", 20.0)])
def test_line_rule_inherited_from_paragraph_style_is_resolved(tmp_path, rule, length):
    data, project, template = _sample(tmp_path, TextStyle(line_spacing=length, line_spacing_rule=rule))
    doc = Document(BytesIO(data))
    paragraph_style = doc.styles.add_style("Line Rule Inheritance", WD_STYLE_TYPE.PARAGRAPH)
    paragraph_style.paragraph_format.line_spacing = Pt(length)
    spacing = paragraph_style._element.pPr.find(qn("w:spacing"))
    spacing.set(qn("w:lineRule"), rule)
    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            paragraph.style = paragraph_style
            ppr = paragraph._p.pPr
            if ppr is not None:
                spacing = ppr.find(qn("w:spacing"))
                if spacing is not None:
                    ppr.remove(spacing)
    mutated = BytesIO()
    doc.save(mutated)

    report = inspect_docx_output(mutated.getvalue(), project, template)

    body_spacing = next(item for item in report["checks"] if item["rule"] == "format.line_spacing" and item["object"] == "paragraph:1")
    assert body_spacing["status"] == "pass"
    assert body_spacing["actual"]["rule"] == rule


def test_doc_defaults_run_fonts_and_size_are_resolved(tmp_path):
    data, project, template = _sample(tmp_path)
    doc = Document(BytesIO(data))
    defaults = doc.styles.element.find(qn("w:docDefaults"))
    rpr_default = defaults.find(qn("w:rPrDefault")).find(qn("w:rPr"))
    rpr_default.get_or_add_rFonts().set(qn("w:ascii"), "Times New Roman")
    rpr_default.get_or_add_rFonts().set(qn("w:hAnsi"), "Times New Roman")
    rpr_default.get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    size = rpr_default.get_or_add_sz()
    size.set(qn("w:val"), "24")
    paragraph = next(item for item in doc.paragraphs if item.text == "第一段包含多个 run")
    for run in paragraph.runs:
        run._element.get_or_add_rPr().clear()
    mutated = BytesIO()
    doc.save(mutated)

    report = inspect_docx_output(mutated.getvalue(), project, template)

    assert report["status"] == "pass"
    assert all(item["status"] == "pass" for item in report["checks"] if item["rule"] in {"format.font_ascii", "format.font_east_asia", "format.font_size"})


def test_paragraph_style_run_font_and_size_are_resolved(tmp_path):
    data, project, template = _sample(tmp_path)
    doc = Document(BytesIO(data))
    style = doc.styles.add_style("Paragraph Run Defaults", WD_STYLE_TYPE.PARAGRAPH)
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    paragraph = next(item for item in doc.paragraphs if item.text == "第一段包含多个 run")
    paragraph.style = style
    for run in paragraph.runs:
        run._element.get_or_add_rPr().clear()
    mutated = BytesIO()
    doc.save(mutated)

    report = inspect_docx_output(mutated.getvalue(), project, template)

    assert report["status"] == "pass"


def test_source_template_digest_change_invalidates_report_binding(tmp_path):
    data, project, template = _sample(tmp_path)
    report = inspect_docx_output(data, project, template, source_template_bytes=b"template version one")

    assert verify_source_template_binding(report, b"template version one")
    assert not verify_source_template_binding(report, b"template version two")


def test_report_identity_is_bound_to_the_frozen_project_and_template_snapshot(tmp_path):
    data, project, template = _sample(tmp_path)
    report = inspect_docx_output(data, project, template)
    expected = expected_output_check_identity(project, template, None)

    assert verify_report_identity(report, expected)
    for field, changed in (
        ("project_id", "another-project"),
        ("project_revision", project.revision + 1),
        ("project_content_hash", "0" * 64),
        ("template_id", "another-template"),
        ("template_version", "another-version"),
        ("template_hash", "1" * 64),
        ("source_template_sha256", "2" * 64),
    ):
        assert not verify_report_identity({**report, field: changed}, expected)
    assert not verify_report_identity({key: value for key, value in report.items() if key != "project_id"}, expected)


def test_missing_required_template_heading_fails_even_when_export_force_was_used(tmp_path):
    data, project, template = _sample(tmp_path)
    template.sections.append(TemplateSection(key="required-abstract", type=SectionType.ABSTRACT, title="摘要"))

    report = inspect_docx_output(data, project, template, context_kind="paper-sync-force")

    assert report["status"] == "fail"
    assert any(item["rule"] == "structure.required_section" and item["status"] == "fail" for item in report["checks"])


def test_unknown_required_toc_capability_fails_closed(tmp_path):
    data, project, template = _sample(tmp_path)
    template.sections.append(TemplateSection(key="required-toc", type=SectionType.TOC, title="目录"))

    with pytest.raises(DocxOutputCheckError) as error:
        require_docx_output_pass(data, project, template)

    assert getattr(error.value, "report", {}).get("status") == "unverifiable"
    assert any("目录缓存/域" in item for item in error.value.report["unsupported"])


def test_missing_required_section_title_or_body_fails_even_if_source_snapshot_is_empty(tmp_path):
    data, project, template = _sample(tmp_path)
    project.sections[0].title = ""
    project.sections[0].paragraphs[0].text = ""
    project.sections[0].paragraphs[0].inline_runs = []

    report = inspect_docx_output(data, project, template)

    assert report["status"] == "fail"
    assert any(item["rule"] == "structure.required_title" and item["status"] == "fail" for item in report["checks"])
    assert any(item["rule"] == "content.required_section_body" and item["status"] == "fail" for item in report["checks"])


def test_second_run_font_mutation_is_detected(tmp_path):
    data, project, template = _sample(tmp_path)
    doc = Document(BytesIO(data))
    text_paragraph = next(paragraph for paragraph in doc.paragraphs if paragraph.text == "第一段包含多个 run")
    text_paragraph.runs[1].font.name = "Courier New"
    text_paragraph.runs[1].font.size = Pt(10)
    mutated = BytesIO()
    doc.save(mutated)

    report = inspect_docx_output(mutated.getvalue(), project, template)

    assert report["status"] == "fail"
    assert any(
        item["rule"] == "format.font_ascii"
        and item["status"] == "fail"
        and item["object"].endswith("run:1")
        for item in report["checks"]
    )
    assert any(item["rule"] == "format.font_size" and item["status"] == "fail" and item["object"].endswith("run:1") for item in report["checks"])


def test_paragraph_and_character_style_inheritance_is_resolved(tmp_path):
    data, project, template = _sample(tmp_path)
    doc = Document(BytesIO(data))
    paragraph_style = doc.styles.add_style("Inherited Body", WD_STYLE_TYPE.PARAGRAPH)
    paragraph_style.paragraph_format.alignment = 3
    paragraph_style.paragraph_format.line_spacing = 1.5
    paragraph_style.paragraph_format.space_before = Pt(0)
    paragraph_style.paragraph_format.space_after = Pt(0)
    paragraph_style.paragraph_format.first_line_indent = Pt(0)
    char_style = doc.styles.add_style("Inherited Character", WD_STYLE_TYPE.CHARACTER)
    char_style.font.name = "Times New Roman"
    char_style.font.size = Pt(12)
    char_style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    text_paragraph = next(paragraph for paragraph in doc.paragraphs if paragraph.text == "第一段包含多个 run")
    text_paragraph.style = paragraph_style
    ppr = text_paragraph._p.pPr
    for tag in ("w:jc", "w:spacing", "w:ind"):
        node = ppr.find(qn(tag))
        if node is not None:
            ppr.remove(node)
    for run in text_paragraph.runs:
        run.style = char_style
        rpr = run._element.rPr
        for tag in ("w:rFonts", "w:sz", "w:szCs", "w:b", "w:i", "w:u"):
            node = rpr.find(qn(tag)) if rpr is not None else None
            if node is not None:
                rpr.remove(node)
    inherited = BytesIO()
    doc.save(inherited)

    report = inspect_docx_output(inherited.getvalue(), project, template)

    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]


def test_empty_or_corrupt_output_never_passes(tmp_path):
    _, project, template = _sample(tmp_path)

    empty = inspect_docx_output(b"", project, template)
    corrupt = inspect_docx_output(b"not a zip", project, template)

    assert empty["status"] == "unverifiable" and empty["check_count"] > 0
    assert corrupt["status"] == "unverifiable" and corrupt["check_count"] > 0
    assert empty["passed_count"] == corrupt["passed_count"] == 0


def test_missing_and_duplicated_paragraphs_are_detected(tmp_path):
    data, project, template = _sample(tmp_path)
    doc = Document(BytesIO(data))
    target = next(paragraph for paragraph in doc.paragraphs if paragraph.text == "第一段包含多个 run")
    target.text = "被删改的正文"
    duplicate = BytesIO()
    doc.save(duplicate)
    missing = inspect_docx_output(duplicate.getvalue(), project, template)
    doc = Document(BytesIO(data))
    doc.add_paragraph("第一段包含多个 run")
    repeated = BytesIO()
    doc.save(repeated)
    duplicated = inspect_docx_output(repeated.getvalue(), project, template)

    assert missing["status"] == "fail"
    assert any(item["rule"] == "content.order_and_presence" and item["status"] == "fail" for item in missing["checks"])
    assert duplicated["status"] == "fail"
    assert any(item["rule"] == "content.no_unexpected_body" and item["status"] == "fail" for item in duplicated["checks"])


def test_fixed_spacing_and_hanging_indent_are_checked_in_word_units(tmp_path):
    style = TextStyle(
        line_spacing=18,
        line_spacing_rule="exact",
        first_line_indent_chars=0,
        hanging_indent_cm=0.74,
    )
    data, project, template = _sample(tmp_path, style)

    report = inspect_docx_output(data, project, template)

    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]


def test_text_style_coverage_matrix_matches_every_model_field():
    assert set(TEXT_STYLE_COVERAGE) == set(TextStyle.model_fields)


def test_user_color_probe_now_fails_after_mutating_last_body_run(tmp_path):
    data, project, template = _sample(tmp_path, TextStyle(first_line_indent_chars=0, color="000000"))
    assert inspect_docx_output(data, project, template)["status"] == "pass"
    doc = Document(BytesIO(data))
    paragraph = doc.paragraphs[-1]
    next(run for run in reversed(paragraph.runs) if run.text).font.color.rgb = RGBColor(255, 0, 0)
    mutated = BytesIO()
    doc.save(mutated)
    report = inspect_docx_output(mutated.getvalue(), project, template)
    assert report["status"] == "fail"
    assert any(item["rule"] == "format.font_color" and item["status"] == "fail" for item in report["checks"])


def test_unsupported_explicit_style_value_is_unverifiable_and_blocks_export(tmp_path):
    data, project, template = _sample(tmp_path, TextStyle(first_line_indent_chars=0, vert_align="baseline"))
    report = inspect_docx_output(data, project, template)
    assert report["status"] == "unverifiable"
    assert report["unsupported"]
    assert any(item["rule"] == "capability.style_vert_align" and item["status"] == "unverifiable" for item in report["checks"])
    with pytest.raises(DocxOutputCheckError):
        require_docx_output_pass(data, project, template)


@pytest.mark.parametrize(
    ("property_name", "expected_rule"),
    [
        ("color", "format.font_color"),
        ("highlight", "format.highlight"),
        ("vert_align", "format.vert_align"),
    ],
)
def test_run_color_highlight_and_vertical_alignment_mutations_fail(tmp_path, property_name, expected_rule):
    style = TextStyle(first_line_indent_chars=0, color="000000", highlight="yellow", vert_align="superscript")
    data, project, template = _sample(tmp_path, style)
    assert inspect_docx_output(data, project, template)["status"] == "pass"

    doc = Document(BytesIO(data))
    paragraph = next(item for item in doc.paragraphs if item.text == "第一段包含多个 run")
    run = paragraph.runs[-1]
    if property_name == "color":
        run.font.color.rgb = RGBColor(255, 0, 0)
    elif property_name == "highlight":
        run.font.highlight_color = None
    else:
        run.font.superscript = False
        run.font.subscript = False
    mutated = BytesIO()
    doc.save(mutated)
    report = inspect_docx_output(mutated.getvalue(), project, template)
    assert report["status"] == "fail"
    assert any(item["rule"] == expected_rule and item["status"] == "fail" for item in report["checks"])


@pytest.mark.parametrize("damage", ["font_size", "font", "alignment"])
def test_cover_format_mutations_fail(tmp_path, damage):
    data, project, template = _full_default_gb7713_sample(tmp_path)
    doc = Document(BytesIO(data))
    paragraph = next(item for item in doc.paragraphs if item.text.startswith("学校名称："))
    if damage == "font_size":
        paragraph.runs[0].font.size = Pt(8)
        expected_rule = "format.font_size"
    elif damage == "font":
        paragraph.runs[0].font.name = "Arial"
        expected_rule = "format.font_ascii"
    else:
        paragraph.alignment = 0
        expected_rule = "format.alignment"
    mutated = BytesIO()
    doc.save(mutated)
    report = inspect_docx_output(mutated.getvalue(), project, template)
    assert report["status"] == "fail"
    assert any(item["rule"] == expected_rule and item["status"] == "fail" for item in report["checks"])


def test_table_cell_and_caption_styles_are_checked(tmp_path):
    data, project, template = _full_default_gb7713_sample(tmp_path)
    chapter = next(section for section in project.sections if section.key == "ch1")
    chapter.paragraphs.append(ParagraphBlock(
        id="table-style-case", type=BlockType.TABLE,
        table=TableBlock(rows=1, cols=1, cells=[["测试单元格"]], caption="样式检查"),
    ))
    chapter.paragraphs.extend([
        ParagraphBlock(id="table-with-explicit-caption", type=BlockType.TABLE, table=TableBlock(rows=1, cols=1, cells=[["第二表"]])),
        ParagraphBlock(id="explicit-table-caption", text="表 1.2 显式标题"),
    ])
    output = tmp_path / "table-style-case.docx"
    export_project_to_docx(project, template, str(output))
    data = output.read_bytes()
    assert inspect_docx_output(data, project, template)["status"] == "pass"

    for target, expected_rule in (("cell", "format.bold"), ("caption", "format.alignment"), ("explicit_caption", "format.alignment")):
        doc = Document(BytesIO(data))
        if target == "cell":
            cell_runs = [run for run in doc.tables[0].cell(0, 0).paragraphs[0].runs if run.text]
            cell_runs[0].bold = True
        elif target == "caption":
            caption = next(paragraph for paragraph in doc.paragraphs if paragraph.text == "表 1.1 样式检查")
            caption.alignment = 0
        else:
            caption = next(paragraph for paragraph in doc.paragraphs if paragraph.text == "表 1.2 显式标题")
            caption.alignment = 0
        mutated = BytesIO()
        doc.save(mutated)
        report = inspect_docx_output(mutated.getvalue(), project, template)
        assert report["status"] == "fail", target
        assert any(item["rule"] == expected_rule and item["status"] == "fail" for item in report["checks"]), (target, [item for item in report["checks"] if item["status"] != "pass"])


def test_page_break_before_mutation_is_rejected(tmp_path):
    data, project, template = _full_default_gb7713_sample(tmp_path)
    doc = Document(BytesIO(data))
    chapter_heading = next(paragraph for paragraph in doc.paragraphs if paragraph.text.startswith("第二章"))
    index = next(i for i, paragraph in enumerate(doc.paragraphs) if paragraph._p is chapter_heading._p)
    break_paragraph = doc.paragraphs[index - 1]
    breaks = break_paragraph._p.xpath(".//w:br[@w:type='page']")
    assert breaks
    for node in breaks:
        node.getparent().remove(node)
    mutated = BytesIO()
    doc.save(mutated)
    report = inspect_docx_output(mutated.getvalue(), project, template)
    assert report["status"] == "fail"
    assert any(item["rule"] == "format.page_break_before" and item["status"] == "fail" for item in report["checks"])


def test_duplicate_inline_text_runs_are_checked_by_position(tmp_path):
    data, project, template = _sample(tmp_path)
    block = project.sections[0].paragraphs[0]
    block.text = "重复重复"
    block.inline_runs = [
        InlineTextRun(text="重复", bold=True),
        InlineTextRun(text="重复", bold=False),
    ]
    output = tmp_path / "duplicate-runs.docx"
    export_project_to_docx(project, template, str(output))
    doc = Document(output)
    for section in doc.sections:
        for footer_paragraph in section.footer.paragraphs:
            footer_paragraph.clear()
    normalized = BytesIO()
    doc.save(normalized)
    data = normalized.getvalue()
    report = inspect_docx_output(data, project, template)
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]

    doc = Document(BytesIO(data))
    paragraph = next(item for item in doc.paragraphs if item.text == "重复重复")
    paragraph.runs[1].bold = True
    mutated = BytesIO()
    doc.save(mutated)
    bad_report = inspect_docx_output(mutated.getvalue(), project, template)
    assert bad_report["status"] == "fail"
    assert any(item["rule"] == "format.bold" and item["status"] == "fail" and item["object"].endswith("run:1") for item in bad_report["checks"])


@pytest.mark.parametrize(
    ("field", "value", "rule", "damage"),
    [
        ("font_zh", "黑体", "format.font_east_asia", "font_zh"),
        ("font_en", "Arial", "format.font_ascii", "font_en"),
        ("size_pt", 16.0, "format.font_size", "size_pt"),
        ("bold", True, "format.bold", "bold"),
        ("italic", True, "format.italic", "italic"),
        ("underline", True, "format.underline", "underline"),
        ("align", Align.LEFT, "format.alignment", "alignment"),
        ("line_spacing", 2.0, "format.line_spacing", "line_spacing"),
        ("line_spacing_rule", "exact", "format.line_spacing", "line_spacing_rule"),
        ("space_before_pt", 6.0, "format.space_before", "space_before_pt"),
        ("space_after_pt", 6.0, "format.space_after", "space_after_pt"),
        ("first_line_indent_chars", 2.0, "format.indent", "first_line_indent_chars"),
        ("hanging_indent_cm", 0.5, "format.indent", "hanging_indent_cm"),
        ("color", "123456", "format.font_color", "color"),
        ("highlight", "yellow", "format.highlight", "highlight"),
        ("vert_align", "superscript", "format.vert_align", "vert_align"),
    ],
)
def test_every_explicit_text_style_constraint_is_checked(tmp_path, field, value, rule, damage):
    style_kwargs = {field: value}
    style_kwargs.setdefault("first_line_indent_chars", 0)
    if field == "line_spacing_rule":
        style_kwargs["line_spacing"] = 18.0
    data, project, template = _sample(tmp_path, TextStyle(**style_kwargs))
    assert inspect_docx_output(data, project, template)["status"] == "pass", field

    doc = Document(BytesIO(data))
    paragraph = next(item for item in doc.paragraphs if item.text == "第一段包含多个 run")
    run = next(item for item in paragraph.runs if item.text)
    if damage == "font_zh":
        run._r.rPr.rFonts.set(qn("w:eastAsia"), "仿宋")
    elif damage == "font_en":
        run._r.rPr.rFonts.set(qn("w:ascii"), "Courier New")
        run._r.rPr.rFonts.set(qn("w:hAnsi"), "Courier New")
    elif damage == "size_pt":
        run.font.size = Pt(10)
    elif damage == "bold":
        run.bold = False
    elif damage == "italic":
        run.italic = False
    elif damage == "underline":
        run.underline = False
    elif damage == "alignment":
        paragraph.alignment = 1
    elif damage == "line_spacing":
        paragraph.paragraph_format.line_spacing = 1.0
    elif damage == "line_spacing_rule":
        paragraph.paragraph_format.line_spacing = 1.5
    elif damage == "space_before_pt":
        paragraph.paragraph_format.space_before = Pt(0)
    elif damage == "space_after_pt":
        paragraph.paragraph_format.space_after = Pt(0)
    elif damage in {"first_line_indent_chars", "hanging_indent_cm"}:
        paragraph.paragraph_format.first_line_indent = Pt(0)
    elif damage == "color":
        run.font.color.rgb = RGBColor(255, 0, 0)
    elif damage == "highlight":
        run.font.highlight_color = None
    elif damage == "vert_align":
        run.font.superscript = False
        run.font.subscript = False
    mutated = BytesIO()
    doc.save(mutated)
    report = inspect_docx_output(mutated.getvalue(), project, template)
    assert report["status"] == "fail", field
    assert any(item["rule"] == rule and item["status"] == "fail" for item in report["checks"]), (field, report["checks"])


def test_mismatched_inline_run_text_fails_closed(tmp_path):
    data, project, template = _sample(tmp_path)
    project.sections[0].paragraphs[0].inline_runs = [InlineTextRun(text="不同文本", bold=True)]
    report = inspect_docx_output(data, project, template)
    assert report["status"] == "unverifiable"
    assert any(item["rule"] == "capability.inline_run_mapping" and item["status"] == "unverifiable" for item in report["checks"])


def test_zero_required_text_checks_are_unverifiable(tmp_path):
    data, project, template = _sample(tmp_path)
    project.sections[0].paragraphs[0].text = ""
    project.sections[0].paragraphs[0].inline_runs = []
    project.sections[0].title = ""
    report = inspect_docx_output(data, project, template)

    assert report["status"] != "pass"
    assert any("检查数量为零" in item for item in report["unsupported"])
