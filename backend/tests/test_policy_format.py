"""规范排版解析、模板冲突优先级与写作检查回归。"""

import pytest

from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import PaperTemplate, TextStyle
from app.core.export.docx_exporter import export_project_to_docx
from app.services.policy_format import extract_format_overrides, extract_layout_overrides
from app.services.policy_resolver import apply_requirement_format_overrides
from app.services.project_check import check_project


GUIDELINE = """
（二）毕业设计报告（论文）前置部分格式要求
1.中文摘要页：中文摘要标题采用黑体小二号，居中，单倍行距；中文摘要内容和中文关键词采用小四号宋体，首行缩进2字\n符，1.5倍行距。
2.英文摘要页：英文摘要标题采用Times\nNew Roman字体小二号，居中，单倍行距；英文摘要内容和英文关键词采用小四号Times New Roman字体，首行缩进2字符，1.5倍行距。
（三）毕业设计报告（论文）主体部分格式要求
1.标题要求：每章应另起一页。第一级（章）文字：小二号黑体，数字、英文：小二号Times New Roman，居中，段前1行，段后1行，单倍行距。第二级（节）文字：小三号黑体，数字、英文：小三号Times New Roman，居左，段前1行，段后1行，单倍行距。第三级（条）文字：四号黑体，数字、英文：四号Times New Roman，居左，段前1行，段后1行，单倍行距。
2.正文要求字体字号：中文小四号宋体，数字、英文小四号Times New Roman字体，全文统一，两端对齐，首行缩进2个中文字符，1.5倍行距。正文页眉：说明五号宋体。
（四）毕业设计报告（论文）结尾部分格式要求
1.参考文献：参考文献标题采用一级标题格式排版。中文字体采用小四号宋体。外文、数字、标点字号与汉字字号相同，字体用Times New Roman字体，1.5倍行距。序号左顶格。
2.致谢：中文字体采用小四号宋体，1.5倍行距。外文、数字字号与同行汉字字号相同，字体用Times New Roman字体，1.5倍行距。
3.附录：附录标题采用一级标题格式排版。
三、格式要求（一）毕业设计报告（论文）用纸、页面设置要求
纸张大小：标准A4复印纸（210mm×297mm）。页边距：上、下、左、右各25mm。页眉距边距：1.5cm，页脚距边距：1.75cm。
"""


def test_guideline_format_and_page_constraints_are_extracted_by_scope():
    overrides = extract_format_overrides(GUIDELINE)
    assert overrides["body"] == {
        "font_zh": "宋体",
        "font_en": "Times New Roman",
        "size_pt": 12.0,
        "align": "justify",
        "line_spacing": 1.5,
        "line_spacing_rule": "auto",
        "first_line_indent_chars": 2.0,
    }
    assert overrides["heading1"]["page_break_before"] is True
    assert overrides["heading1"]["line_spacing"] == 1.0
    assert overrides["references"] == {
        "font_zh": "宋体",
        "font_en": "Times New Roman",
        "size_pt": 12.0,
        "line_spacing": 1.5,
        "line_spacing_rule": "auto",
        "first_line_indent_chars": 0.0,
        "page_break_before": True,
    }
    assert overrides["acknowledgement"]["size_pt"] == 12.0
    assert overrides["acknowledgement"]["page_break_before"] is True
    code_overrides = extract_format_overrides(
        "代码：第1行段前0.5行，其后各行段前段后各0行，多倍行距1.25，Times New Roman字体五号。"
    )
    assert code_overrides["code"]["line_spacing"] == 1.25

    layout = extract_layout_overrides(GUIDELINE)
    assert layout == {
        "page_width_cm": 21.0,
        "page_height_cm": 29.7,
        "top_margin_cm": 2.5,
        "bottom_margin_cm": 2.5,
        "left_margin_cm": 2.5,
        "right_margin_cm": 2.5,
        "header_distance_cm": 1.5,
        "footer_distance_cm": 1.75,
    }


def test_policy_overrides_win_over_template_and_rule_pack_defaults():
    template = PaperTemplate(
        id="wrong-template",
        name="有误模板",
        styles={
            "body": TextStyle(
                font_zh="仿宋", font_en="Calibri", size_pt=10, line_spacing=1.0,
                align="left", first_line_indent_chars=0, space_before_pt=4, space_after_pt=6,
            ),
        },
    )
    snapshot = {
        "format_overrides": extract_format_overrides(GUIDELINE),
        "layout_overrides": extract_layout_overrides(GUIDELINE),
        "format_override_count": 1,
        "format_resolution": {"policy_over_template": True},
    }
    snapshot["format_overrides"]["body"].update({"space_before_pt": 0.0, "space_after_pt": 0.0})
    effective = apply_requirement_format_overrides(template, snapshot)
    assert effective.styles["body"].font_zh == "宋体"
    assert effective.styles["body"].font_en == "Times New Roman"
    assert effective.styles["body"].size_pt == 12
    assert effective.styles["body"].line_spacing == 1.5
    assert effective.styles["body"].align.value == "justify"
    assert effective.styles["body"].first_line_indent_chars == 2
    assert effective.page_layout["left_margin_cm"] == 2.5
    assert template.styles["body"].font_zh == "仿宋"

    project = WritingProject(
        id="format-priority-project",
        template_id=template.id,
        title="排版优先级",
        requirement_snapshot=snapshot,
        sections=[ProjectSection(
            key="body", type="body", title="正文",
            paragraphs=[ParagraphBlock(id="body-1", text="正文内容")],
        )],
    )
    report = check_project(project, template, pack="GB-T-7713-1-2006")
    assert report["findings"] == []
    assert report["summary"]["not_checked"] == 0
    assert report["context"]["format_override_count"] == 1
    assert report["context"]["format_resolution"]["policy_over_template"] is True


def test_policy_overrides_resign_source_execution_profile_and_bind_snapshot(tmp_path, monkeypatch):
    from docx import Document

    from app.core.extract.specification import extract_writing_specification
    from app.services.import_template import extract_section_tree, import_template_styles
    from app.services.source_template_profile import ConfirmWritingProfile, ProfileError, compile_profile, verify_profile

    path = tmp_path / "policy-profile.docx"
    document = Document()
    document.add_heading("第1章 正文", 1)
    document.add_paragraph("正文样例段落用于确认源模板规范。" * 8)
    document.save(path)
    monkeypatch.setattr("app.services.template_source.resolve_template_source", lambda _: path)
    template = PaperTemplate(
        id="policy-profile-template",
        name="策略签名模板",
        source_docx=path.name,
        sections=extract_section_tree(path),
        styles=import_template_styles(path)["styles"],
    )
    specification = extract_writing_specification(path)
    template = compile_profile(
        template,
        path,
        ConfirmWritingProfile(
            expected_specification_sha256=specification.specification_sha256,
            accept_recommended=True,
            accept_new_object_defaults=True,
            remove_template_instructions=True,
        ),
        actor="teacher-test",
    )
    snapshot = {"content_hash": "sha256:policy-v1", "format_overrides": {"body": {"size_pt": 14.0}}}

    effective = apply_requirement_format_overrides(template, snapshot)

    assert effective.styles["body"].size_pt == 14.0
    assert template.styles["body"].size_pt != 14.0
    assert effective.source_execution["policy_snapshot_sha256"]
    assert effective.source_execution["policy_format_overrides_sha256"]
    verify_profile(effective, path.read_bytes())
    verify_profile(template, path.read_bytes())
    corrupted = effective.model_copy(deep=True)
    corrupted.styles["body"].size_pt = 15.0
    with pytest.raises(ProfileError):
        verify_profile(corrupted, path.read_bytes())


def test_policy_page_layout_is_written_to_exported_docx(tmp_path):
    template = PaperTemplate(
        id="layout-template",
        name="页面设置模板",
        styles={"body": TextStyle()},
        page_layout=extract_layout_overrides(GUIDELINE),
    )
    project = WritingProject(
        id="layout-project",
        template_id=template.id,
        title="页面设置导出",
        sections=[ProjectSection(
            key="body", type="body", title="正文",
            paragraphs=[ParagraphBlock(id="body-1", text="正文")],
        )],
    )
    output = tmp_path / "layout.docx"
    export_project_to_docx(project, template, str(output))

    from docx import Document
    section = Document(output).sections[0]
    assert round(section.page_width.cm, 2) == 21.0
    assert round(section.page_height.cm, 2) == 29.7
    assert round(section.top_margin.cm, 2) == 2.5
    assert round(section.left_margin.cm, 2) == 2.5
    assert round(section.header_distance.cm, 2) == 1.5
    assert round(section.footer_distance.cm, 2) == 1.75


@pytest.mark.parametrize(('title', 'kind', 'body_style'), [
    ('摘要', 'abstract', 'abstract_body'),
    ('Abstract', 'english_abstract', 'english_abstract_body'),
])
def test_signed_abstract_title_and_body_policy_styles_stay_distinct(tmp_path, monkeypatch, title, kind, body_style):
    from docx import Document
    from docx.oxml.ns import qn
    from app.core.extract.specification import extract_writing_specification
    from app.services.docx_output_check import inspect_docx_output
    from app.services.import_template import extract_section_tree, import_template_styles
    from app.services.master_docx_renderer import render_master_docx
    from app.services.source_template_profile import ConfirmWritingProfile, compile_profile, verify_profile

    source = tmp_path / 'abstract-master.docx'
    document = Document()
    document.add_heading(title, 1)
    document.add_paragraph('摘要正文排版样例。' * 12)
    document.add_heading('第1章 正文', 1)
    document.add_paragraph('正文排版样例。' * 15)
    document.save(source)
    monkeypatch.setattr('app.services.template_source.resolve_template_source', lambda _: source)
    template = PaperTemplate(id='abstract-master', name='摘要排版模板', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'])
    specification = extract_writing_specification(source)
    template = compile_profile(template, source, ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256, accept_recommended=True,
        accept_new_object_defaults=True, remove_template_instructions=True), actor='teacher')
    effective = apply_requirement_format_overrides(template, {'format_overrides': {
        kind: {'size_pt': 16.0, 'bold': True},
        body_style: {'size_pt': 12.0, 'bold': False},
    }})
    profile = verify_profile(effective, source.read_bytes())
    heading = next(item for item in profile['headings'] if item['kind'] == kind)
    assert heading['properties']['font.size']['value'] == 12.0
    assert heading['properties']['font.bold'] is False
    assert heading['title_properties']['font.size']['value'] == 16.0
    project = WritingProject(id='abstract-project', template_id=effective.id,
        sections=[ProjectSection(key=item['key'], type=item['kind'], title=item['title'],
            paragraphs=[ParagraphBlock(id=item['key'] + '-body',
                text='学生摘要内容' if item['kind'] == kind else '学生正文内容')])
            for item in profile['headings']])
    output = tmp_path / 'abstract-output.docx'
    render_master_docx(project, effective, str(output), source_path=str(source))
    report = inspect_docx_output(output.read_bytes(), project, effective, source_template_bytes=source.read_bytes())
    assert report['status'] == 'pass', [item for item in report['checks'] if item['status'] != 'pass']
    paragraphs = Document(output).element.body.findall('.//' + qn('w:p'))
    for text, half_points in [(title, '32'), ('学生摘要内容', '24')]:
        paragraph = next(p for p in paragraphs if ''.join(p.itertext(qn('w:t'))) == text)
        sizes = [node.get(qn('w:val')) for node in paragraph.findall('.//' + qn('w:sz'))]
        assert sizes and set(sizes) == {half_points}
