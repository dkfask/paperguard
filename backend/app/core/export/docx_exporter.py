"""PaperGuard docx 导出引擎。

按模板约束把写作项目渲染为全新的 .docx:
- 章节树决定文档结构, 标题用 Word Heading 样式(导航窗格/TOC 域可用),
  正文按模板样式应用字体、字号、行距、缩进
- 模板规定的 page_break_before 生成分页
- 封面元信息字段渲染在首页
- 目录节插入 TOC 域(Word 中右键更新域即得目录), 页脚带页码
- 图片/表格题注自动编号: 图 {章}.{序} / 表 {章}.{序}

输出全新文件, 不修改任何已有文档。
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.models.project import ParagraphBlock, WritingProject
from app.models.template import Align, PaperTemplate, SectionType, TextStyle

_IMAGE_WIDTH_CM = 14.0

# 章节类型 → Word 内置标题样式
_HEADING_STYLE = {
    SectionType.HEADING1: "Heading 1",
    SectionType.HEADING2: "Heading 2",
    SectionType.HEADING3: "Heading 3",
}


class _Numbering:
    """图表自动编号状态: 章号 + 每章内图/表计数。"""

    def __init__(self) -> None:
        self.chapter = 0
        self.figure = 0
        self.table = 0

    def next_chapter(self) -> None:
        self.chapter += 1
        self.figure = 0
        self.table = 0

    def next_figure(self) -> str:
        self.figure += 1
        return f"{self.chapter}.{self.figure}"

    def next_table(self) -> str:
        self.table += 1
        return f"{self.chapter}.{self.table}"


_CAPTION_PREFIX_RE = re.compile(r"^(图|表)\s*\d+(\.\d+)*\s*")


def _strip_caption_number(caption: str) -> str:
    """去掉旧数据里手填的 '图 1.1' 前缀, 由系统重新编号。"""
    return _CAPTION_PREFIX_RE.sub("", caption).strip()


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


def _add_text_paragraph(doc: Document, text: str, style: TextStyle,
                        word_style: str | None = None) -> None:
    """添加文本段落; word_style 指定时使用内置 Word 样式(如 Heading 1)。"""
    par = doc.add_paragraph()
    if word_style and word_style in doc.styles:
        par.style = doc.styles[word_style]
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


def _add_toc_field(doc: Document) -> None:
    """目录节: 插入 TOC 域, Word 打开后右键 → 更新域 生成目录。"""
    settings = doc.settings._element
    update_fields = settings.find(qn("w:updateFields"))
    if update_fields is None:
        update_fields = OxmlElement("w:updateFields")
        settings.append(update_fields)
    update_fields.set(qn("w:val"), "true")

    par = doc.add_paragraph()
    run = par.add_run()
    r = run._r
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    hint = OxmlElement("w:t")
    hint.text = "在 Word 中右键此处 → 更新域, 生成目录"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r.append(fld_begin)
    r.append(instr)
    r.append(fld_sep)
    r.append(hint)
    r.append(fld_end)


def _add_page_number_footer(doc: Document) -> None:
    """页脚: 居中页码域 '第 N 页'。"""
    footer = doc.sections[0].footer
    par = footer.paragraphs[0]
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = par.add_run("第 ")
    run.font.size = Pt(10.5)
    # PAGE 域
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    r_el = OxmlElement("w:r")
    t_el = OxmlElement("w:t")
    t_el.text = "1"
    r_el.append(t_el)
    fld.append(r_el)
    par._p.append(fld)
    run2 = par.add_run(" 页")
    run2.font.size = Pt(10.5)


def export_project_to_docx(project: WritingProject, template: PaperTemplate, out_path: str) -> None:
    """把项目按模板导出为 .docx (全新文件)。"""
    doc = Document()

    # 页面设置: A4 + 常规页边距
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.54)
    section.left_margin = section.right_margin = Cm(3.17)
    _add_page_number_footer(doc)

    # 索引: section key -> ProjectSection
    proj_map = {s.key: s for s in project.sections}
    numbering = _Numbering()

    # 封面
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

        if node.type is SectionType.COVER:
            return  # 封面已处理

        if node.type is SectionType.TOC:
            style = _style_for(template, stype, node_style)
            if style.page_break_before:
                doc.add_page_break()
            _add_text_paragraph(doc, node.title, style)
            _add_toc_field(doc)
            return

        if node.type in (SectionType.ABSTRACT, SectionType.ENGLISH_ABSTRACT,
                         SectionType.KEYWORDS, SectionType.ENGLISH_KEYWORDS):
            title_style = _style_for(template, stype, node_style)
            if title_style.page_break_before:
                doc.add_page_break()
            _add_text_paragraph(doc, node.title, title_style)
            body_style = _style_for(template, stype, node_style)
            for par in (proj_sec.paragraphs if proj_sec else []):
                _export_paragraph(doc, par, body_style, template, numbering)
            for child in node.children:
                walk(child)
            return

        if node.type in (SectionType.HEADING1, SectionType.HEADING2, SectionType.HEADING3):
            title = proj_sec.title if proj_sec else node.title
            style = _style_for(template, stype, node_style)
            if style.page_break_before:
                doc.add_page_break()
            if node.type is SectionType.HEADING1:
                numbering.next_chapter()
            _add_text_paragraph(doc, title, style,
                                word_style=_HEADING_STYLE[node.type])
            body_style = _style_for(template, "body", None)
            for par in (proj_sec.paragraphs if proj_sec else []):
                _export_paragraph(doc, par, body_style, template, numbering)
        elif node.type in (SectionType.BODY, SectionType.REFERENCES,
                           SectionType.ACKNOWLEDGEMENT, SectionType.APPENDIX):
            style = _style_for(template, stype, node_style)
            if style.page_break_before:
                doc.add_page_break()
            if proj_sec and proj_sec.title:
                # 参考文献/致谢等章节标题也作为一级标题, 进入目录
                word_style = "Heading 1" if node.type in (
                    SectionType.REFERENCES, SectionType.ACKNOWLEDGEMENT,
                    SectionType.APPENDIX) else None
                if node.type is SectionType.REFERENCES:
                    numbering.next_chapter()
                _add_text_paragraph(doc, proj_sec.title, style, word_style=word_style)
            for par in (proj_sec.paragraphs if proj_sec else []):
                _export_paragraph(doc, par, style, template, numbering)

        for child in node.children:
            walk(child)

    for root in template.sections:
        walk(root)

    doc.save(out_path)


def _export_paragraph(doc: Document, par: ParagraphBlock, style: TextStyle,
                      template: PaperTemplate | None = None,
                      numbering: _Numbering | None = None) -> None:
    """导出单个内容块: 文本段落 / 图片(带题注) / 表格(带题注)。"""
    if par.type.value == "image" and par.image and par.image.url:
        _export_image(doc, par.image, template, numbering)
        return
    if par.type.value == "table" and par.table:
        _export_table(doc, par.table, template, numbering)
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


def _export_image(doc: Document, img, template: PaperTemplate | None,
                  numbering: _Numbering | None = None) -> None:
    """渲染图片 + 自动编号图注。"""
    from app.models.template import PaperTemplate as _PT

    tpl = template if isinstance(template, _PT) else None
    cap_style = _style_for(tpl, "figure_caption", None) if tpl else TextStyle()

    # 图片居中
    local_path = _resolve_image_path(img.url)
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if local_path.exists():
        par.add_run().add_picture(str(local_path), width=Cm(_IMAGE_WIDTH_CM))
    else:
        par.add_run(f"[图片缺失: {img.url}]")

    # 自动编号图注
    num = numbering.next_figure() if numbering else "?"
    caption = _strip_caption_number(img.caption or "")
    cap = doc.add_paragraph()
    _apply_paragraph_format(cap, cap_style)
    run = cap.add_run(f"图 {num} {caption}".rstrip())
    _apply_run_font(run, cap_style)


def _export_table(doc: Document, tbl, template: PaperTemplate | None,
                  numbering: _Numbering | None = None) -> None:
    """渲染表格 + 自动编号表注(表注在上方)。"""
    from app.models.template import PaperTemplate as _PT

    tpl = template if isinstance(template, _PT) else None
    cell_style = _style_for(tpl, "table_cell", None) if tpl else TextStyle()
    cap_style = _style_for(tpl, "table_caption", None) if tpl else TextStyle()

    # 自动编号表注在上方
    num = numbering.next_table() if numbering else "?"
    caption = _strip_caption_number(tbl.caption or "")
    cap = doc.add_paragraph()
    _apply_paragraph_format(cap, cap_style)
    run = cap.add_run(f"表 {num} {caption}".rstrip())
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
