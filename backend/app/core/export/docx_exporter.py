"""PaperGuard docx 导出引擎。

按模板约束把写作项目渲染为全新的 .docx:
- 章节树决定文档结构, 标题/正文段落按模板样式应用字体、字号、行距、缩进
- 模板规定的 page_break_before 生成分页
- 封面元信息字段渲染在首页
- 学生内容只含文本 + 少量行内格式, 排版完全由模板控制

输出全新文件, 不修改任何已有文档。
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.models.project import ParagraphBlock, WritingProject
from app.models.template import Align, PaperTemplate, SectionType, TextStyle


def _align_to_word(align: Align):
    return {
        Align.LEFT: WD_ALIGN_PARAGRAPH.LEFT,
        Align.CENTER: WD_ALIGN_PARAGRAPH.CENTER,
        Align.RIGHT: WD_ALIGN_PARAGRAPH.RIGHT,
        Align.JUSTIFY: WD_ALIGN_PARAGRAPH.JUSTIFY,
    }[align]


def _apply_run_font(run, style: TextStyle) -> None:
    """设置 run 的中西文字体、字号、字形。"""
    run.font.name = style.font_en
    run.font.size = Pt(style.size_pt)
    run.font.bold = style.bold
    run.font.italic = style.italic
    run.font.underline = style.underline
    # 中文字体需显式设置 eastAsia
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), style.font_zh)


def _apply_paragraph_format(par, style: TextStyle) -> None:
    pf = par.paragraph_format
    pf.alignment = _align_to_word(style.align)
    pf.line_spacing = style.line_spacing
    pf.space_before = Pt(style.space_before_pt)
    pf.space_after = Pt(style.space_after_pt)
    if style.first_line_indent_chars:
        pf.first_line_indent = Pt(style.first_line_indent_chars * style.size_pt)


def _add_text_paragraph(doc: Document, text: str, style: TextStyle) -> None:
    par = doc.add_paragraph()
    _apply_paragraph_format(par, style)
    run = par.add_run(text)
    _apply_run_font(run, style)


def _style_for(tpl: PaperTemplate | None, section_type: str,
               node_style: TextStyle | None) -> TextStyle:
    """取模板中该章节的生效样式: 节点覆盖 > 类型默认 > body。"""
    if node_style is not None:
        return node_style
    if tpl is not None:
        return tpl.styles.get(section_type) or tpl.styles["body"]
    return TextStyle()


def export_project_to_docx(project: WritingProject, template: PaperTemplate, out_path: str) -> None:
    """把项目按模板导出为 .docx (全新文件)。"""
    doc = Document()

    # 页面设置: A4 + 常规页边距
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.54)
    section.left_margin = section.right_margin = Cm(3.17)

    # 索引: section key -> ProjectSection
    proj_map = {s.key: s for s in project.sections}

    # 封面
    cover_style = _style_for(template, "cover_title", None)
    for f in template.meta_fields:
        label = f.label
        value = project.meta.get(f.key, "")
        _add_text_paragraph(doc, f"{label}: {value}", _style_for(template, "cover_meta", None))
    if template.sections and any(s.type == SectionType.COVER for s in template.sections):
        doc.add_page_break()

    def walk(node) -> None:
        proj_sec = proj_map.get(node.key)
        node_style = node.style
        stype = node.type.value

        if node.type in (SectionType.COVER,):
            return  # 封面已处理

        if node.type is SectionType.TOC:
            # 目录: 只输出标题占位 (Word 中可手动更新域)
            style = _style_for(template, stype, node_style)
            if style.page_break_before:
                doc.add_page_break()
            _add_text_paragraph(doc, node.title, style)
            return

        if node.type is SectionType.ABSTRACT:
            title_style = _style_for(template, stype, node_style)
            if title_style.page_break_before:
                doc.add_page_break()
            _add_text_paragraph(doc, node.title, title_style)
            # 摘要正文用正文样式, 而不是摘要标题样式
            body_style = _style_for(template, "body", None)
            for par in (proj_sec.paragraphs if proj_sec else []):
                _export_paragraph(doc, par, body_style, template)
            for child in node.children:
                walk(child)
            return

        if node.type in (SectionType.HEADING1, SectionType.HEADING2, SectionType.HEADING3):
            title = proj_sec.title if proj_sec else node.title
            style = _style_for(template, stype, node_style)
            if style.page_break_before:
                doc.add_page_break()
            _add_text_paragraph(doc, title, style)
            # 标题节自带正文段落, 用正文样式导出
            body_style = _style_for(template, "body", None)
            for par in (proj_sec.paragraphs if proj_sec else []):
                _export_paragraph(doc, par, body_style, template)
        elif node.type in (SectionType.BODY, SectionType.REFERENCES,
                           SectionType.ACKNOWLEDGEMENT, SectionType.APPENDIX):
            style = _style_for(template, stype, node_style)
            if style.page_break_before:
                doc.add_page_break()
            # 标题
            if proj_sec and proj_sec.title:
                _add_text_paragraph(doc, proj_sec.title, style)
            # 正文段落
            for par in (proj_sec.paragraphs if proj_sec else []):
                _export_paragraph(doc, par, style, template)

        for child in node.children:
            walk(child)

    for root in template.sections:
        walk(root)

    doc.save(out_path)


def _export_paragraph(doc: Document, par: ParagraphBlock, style: TextStyle,
                      template: PaperTemplate | None = None) -> None:
    """导出单个内容块: 文本段落 / 图片(带题注) / 表格(带题注)。"""
    if par.type.value == "image" and par.image and par.image.url:
        _export_image(doc, par.image, template)
        return
    if par.type.value == "table" and par.table:
        _export_table(doc, par.table, template)
        return
    if not par.text:
        return
    p = doc.add_paragraph()
    _apply_paragraph_format(p, style)
    run = p.add_run(par.text)
    _apply_run_font(run, style)
    # 行内格式覆盖
    run.font.bold = par.bold
    run.font.italic = par.italic
    run.font.underline = par.underline


def _export_image(doc: Document, img, template: PaperTemplate | None) -> None:
    """渲染图片 + 图注 (图注在上方或下方按模板样式)。"""
    from app.models.template import PaperTemplate as _PT

    tpl = template if isinstance(template, _PT) else None
    cap_style = _style_for(tpl, "figure_caption", None) if tpl else TextStyle()
    width_cm = 14.0  # 默认图片宽度
    if tpl and "figure_caption" in tpl.styles:
        pass  # 宽度暂用固定默认值

    # 图片
    local_path = _resolve_image_path(img.url)
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if local_path.exists():
        par.add_run().add_picture(str(local_path), width=Cm(width_cm))
    else:
        par.add_run(f"[图片缺失: {img.url}]")

    # 图注
    if img.caption:
        cap = doc.add_paragraph()
        _apply_paragraph_format(cap, cap_style)
        run = cap.add_run(img.caption)
        _apply_run_font(run, cap_style)


def _export_table(doc: Document, tbl, template: PaperTemplate | None) -> None:
    """渲染表格 + 表注 (表注在上方)。"""
    from app.models.template import PaperTemplate as _PT

    tpl = template if isinstance(template, _PT) else None
    cell_style = _style_for(tpl, "table_cell", None) if tpl else TextStyle()
    cap_style = _style_for(tpl, "table_caption", None) if tpl else TextStyle()

    # 表注在上方
    if tbl.caption:
        cap = doc.add_paragraph()
        _apply_paragraph_format(cap, cap_style)
        run = cap.add_run(tbl.caption)
        _apply_run_font(run, cap_style)

    rows = max(1, int(tbl.rows))
    cols = max(1, int(tbl.cols))
    table = doc.add_table(rows=rows, cols=cols)
    table.style = "Table Grid"
    cells = tbl.cells or []
    for r in range(rows):
        for c in range(cols):
            cell_text = ""
            if r < len(cells) and c < len(cells[r]):
                cell_text = cells[r][c]
            cell = table.cell(r, c)
            cell.text = ""
            p = cell.paragraphs[0]
            _apply_paragraph_format(p, cell_style)
            run = p.add_run(cell_text)
            _apply_run_font(run, cell_style)


def _resolve_image_path(url: str) -> Path:
    """把 /media/uploads/xxx.png 解析为本地文件路径。"""
    from app.main import app as _app  # noqa: F401  (确保 mount 已注册)
    base = Path(__file__).resolve().parent.parent.parent.parent / "data" / "uploads"
    name = url.rsplit("/", 1)[-1]
    return base / name
