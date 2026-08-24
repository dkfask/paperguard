"""写作项目实时检查: 项目内容 → IR → 规则引擎。

把结构化写作项目转换为检查引擎的 IR Document, 复用 YAML 规则包
(与上传检查同一套规则), 让学生写作时就能看到格式问题。
"""

from __future__ import annotations

from app.core.engine import validate_docx
from app.core.ir import Document, DocumentMetadata, Paragraph, Run
from app.models.project import WritingProject
from app.models.template import PaperTemplate, SectionType

# 模板节类型 → 检查引擎 style_id
_STYLE_ID = {
    "heading1": "Heading 1",
    "heading2": "Heading 2",
    "heading3": "Heading 3",
    "abstract": "Body Text",
    "body": "Body Text",
    "references": "Bibliography",
    "acknowledgement": "Body Text",
    "appendix": "Body Text",
}


def project_to_ir(project: WritingProject, template: PaperTemplate
                  ) -> tuple[Document, list[str]]:
    """项目 → IR Document, 同时返回 IR 段落索引 → 章节 key 的映射表。

    run 的字体属性按模板样式填充, 避免"模板未设置"被误报为违规。
    """
    paragraphs: list[Paragraph] = []
    index_to_section: list[str] = []
    proj_map = {s.key: s for s in project.sections}
    table_no = 0

    def walk(node) -> None:
        nonlocal table_no
        sec = proj_map.get(node.key)
        if sec is None:
            return
        stype = sec.type

        # 标题节: 输出标题段落
        if stype in ("heading1", "heading2", "heading3"):
            if sec.title and sec.title.strip():
                paragraphs.append(_heading_par(sec.title, stype, template))
                index_to_section.append(sec.key)
            stype = "body"

        body_style = template.styles.get(stype) or template.styles.get("body")
        style_id = {
            "abstract": "AbstractBody",
            "references": "Bibliography",
            "body": "Body Text",
            "acknowledgement": "Body Text",
            "appendix": "Body Text",
        }.get(stype, "Body Text")

        # 内容块
        for par in sec.paragraphs:
            if par.type.value == "paragraph" and par.text.strip():
                paragraphs.append(_body_par(par.text, par, template, body_style, style_id))
                index_to_section.append(sec.key)
            elif par.type.value == "table" and par.table:
                for r, row in enumerate(par.table.cells):
                    for c, cell in enumerate(row):
                        if str(cell).strip():
                            paragraphs.append(_cell_par(str(cell), table_no, r, c, template))
                            index_to_section.append(sec.key)
                table_no += 1
            # 图片块跳过(无文本检查项)

        for child in node.children:
            walk(child)

    for root in template.sections:
        walk(root)

    ir = Document(
        sections=(),
        paragraphs=tuple(paragraphs),
        metadata=DocumentMetadata(title=project.title),
    )
    return ir, index_to_section


def check_project(project: WritingProject, template: PaperTemplate,
                  pack: str = "GB-T-7713-1-2006") -> dict:
    """跑规则包, 返回带章节定位的检查结果。"""
    ir, index_to_section = project_to_ir(project, template)
    findings = validate_docx(ir, pack=pack)

    result = []
    for f in findings:
        item = {
            "rule_id": f.rule_id,
            "severity": f.severity.value,
            "message": f.message,
            "location": {
                "paragraph_index": f.location.paragraph_index,
                "text_snippet": f.location.text_snippet,
            },
        }
        idx = f.location.paragraph_index
        if idx is not None and 0 <= idx < len(index_to_section):
            item["section_key"] = index_to_section[idx]
        result.append(item)

    return {
        "summary": {
            "total": len(result),
            "error": sum(1 for r in result if r["severity"] == "error"),
            "warning": sum(1 for r in result if r["severity"] == "warning"),
            "info": sum(1 for r in result if r["severity"] == "info"),
        },
        "findings": result,
    }


def _heading_par(title: str, stype: str, template: PaperTemplate) -> Paragraph:
    style_id = _STYLE_ID.get(stype, "Heading 1")
    level = {"heading1": 1, "heading2": 2, "heading3": 3}.get(stype)
    style = template.styles.get(stype) or template.styles.get("heading1", None)
    if style is None:
        style = template.styles["body"]
    return Paragraph(
        style_id=style_id,
        text=title,
        level=level,
        runs=(Run(
            text=title,
            font_eastasia=style.font_zh,
            font_ascii=style.font_en,
            font_hansi=style.font_en,
            font_size_pt=style.size_pt,
            bold=style.bold,
            color_hex="000000",
        ),),
        line_spacing=style.line_spacing,
        space_before_pt=style.space_before_pt,
        space_after_pt=style.space_after_pt,
        alignment=style.align.value,
        is_page_break_before=style.page_break_before,
        first_line_indent_cm=0.0,
    )


def _body_par(text: str, par, template: PaperTemplate, style=None, style_id: str = "Body Text") -> Paragraph:
    style = style or template.styles.get("body") or template.styles["body"]
    return Paragraph(
        style_id=style_id,
        text=text,
        level=None,
        runs=(Run(
            text=text,
            font_eastasia=style.font_zh,
            font_ascii=style.font_en,
            font_hansi=style.font_en,
            font_size_pt=style.size_pt,
            bold=par.bold,
            italic=par.italic,
            underline="single" if par.underline else None,
            color_hex="000000",
        ),),
        line_spacing=style.line_spacing,
        space_before_pt=style.space_before_pt,
        space_after_pt=style.space_after_pt,
        first_line_indent_cm=style.first_line_indent_chars * 0.37,
        alignment=style.align.value,
    )


def _cell_par(text: str, table_index: int, row: int, col: int,
             template: PaperTemplate) -> Paragraph:
    style = template.styles.get("table_cell") or template.styles["body"]
    return Paragraph(
        style_id="Body Text",
        text=text,
        level=None,
        runs=(Run(text=text, font_eastasia=style.font_zh,
                  font_ascii=style.font_en, font_size_pt=style.size_pt),),
        line_spacing=1.0,
        space_before_pt=0.0,
        space_after_pt=0.0,
        alignment="center",
        table_index=table_index,
        table_row_index=row,
        table_cell_index=col,
        first_line_indent_cm=0.0,
    )
