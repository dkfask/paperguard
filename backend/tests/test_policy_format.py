"""规范排版解析、模板冲突优先级与写作检查回归。"""

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
