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

import copy
import logging
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from lxml import etree

from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import Align, PaperTemplate, SectionType, TextStyle

_IMAGE_WIDTH_CM = 14.0
_IMAGE_MAX_HEIGHT_CM = 13.5

_LOGGER = logging.getLogger("paperguard.export")


def _parse_untrusted_xml(data: bytes):
    """解析用户提交的 XML 片段(公式/表格): 禁止 DOCTYPE、外部实体与网络访问, 阻断 XXE。"""
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("XML 片段不允许包含 DOCTYPE/ENTITY 定义")
    return etree.fromstring(
        data,
        etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False),
    )

_HIGHLIGHT_COLORS = {
    "yellow": WD_COLOR_INDEX.YELLOW,
    "green": WD_COLOR_INDEX.BRIGHT_GREEN,
    "cyan": WD_COLOR_INDEX.TURQUOISE,
    "magenta": WD_COLOR_INDEX.PINK,
    "blue": WD_COLOR_INDEX.BLUE,
    "red": WD_COLOR_INDEX.RED,
    "darkblue": WD_COLOR_INDEX.DARK_BLUE,
    "darkcyan": WD_COLOR_INDEX.TEAL,
    "darkgreen": WD_COLOR_INDEX.GREEN,
    "darkmagenta": WD_COLOR_INDEX.VIOLET,
    "darkred": WD_COLOR_INDEX.DARK_RED,
    "darkyellow": WD_COLOR_INDEX.DARK_YELLOW,
    "lightgray": WD_COLOR_INDEX.GRAY_25,
    "darkgray": WD_COLOR_INDEX.GRAY_50,
    "black": WD_COLOR_INDEX.BLACK,
}

# 章节类型 → Word 内置标题样式
_HEADING_STYLE = {
    SectionType.HEADING1: "Heading 1",
    SectionType.HEADING2: "Heading 2",
    SectionType.HEADING3: "Heading 3",
}

# 前置部分(罗马数字页码) / 正文(阿拉伯数字页码)
_FRONT_TYPES = {
    SectionType.ABSTRACT, SectionType.ENGLISH_ABSTRACT,
    SectionType.KEYWORDS, SectionType.ENGLISH_KEYWORDS,
    SectionType.TOC,
}
_BODY_TYPES = {
    SectionType.HEADING1, SectionType.HEADING2, SectionType.HEADING3,
    SectionType.BODY, SectionType.REFERENCES,
    SectionType.ACKNOWLEDGEMENT, SectionType.APPENDIX,
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
    """设置 run 的中西文字体、字号、字形、颜色。"""
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
    # 颜色: 模板指定则用模板色, 否则显式置黑(避免继承 python-docx 默认标题蓝)
    color = (style.color or "").strip().lstrip("#")
    run.font.color.rgb = RGBColor.from_string(color) if _is_hex_color(color) else RGBColor(0, 0, 0)
    _apply_run_decoration(run, style.highlight, style.vert_align)


def _apply_run_decoration(run, highlight: str | None, vert_align: str | None) -> None:
    """Apply optional highlight and baseline shift to a run."""
    _apply_run_highlight(run, highlight)
    _apply_run_vertical_alignment(run, vert_align)


def _apply_run_highlight(run, highlight: str | None) -> None:
    if highlight:
        run.font.highlight_color = _HIGHLIGHT_COLORS.get(highlight.lower(), WD_COLOR_INDEX.YELLOW)
    else:
        run.font.highlight_color = None


def _apply_run_vertical_alignment(run, vert_align: str | None) -> None:
    if vert_align == "superscript":
        run.font.superscript = True
        run.font.subscript = False
    elif vert_align == "subscript":
        run.font.superscript = False
        run.font.subscript = True
    elif vert_align is None:
        run.font.superscript = False
        run.font.subscript = False


def _is_hex_color(value: str) -> bool:
    return len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value)


def _apply_paragraph_format(par, style: TextStyle) -> None:
    pf = par.paragraph_format
    pf.alignment = _align_to_word(style.align)
    if style.line_spacing_rule in ("exact", "atLeast"):
        pf.line_spacing = Pt(style.line_spacing)      # 固定值/最小值按磅
    else:
        pf.line_spacing = style.line_spacing          # 倍数
    pf.space_before = Pt(style.space_before_pt)
    pf.space_after = Pt(style.space_after_pt)
    if style.hanging_indent_cm:
        pf.first_line_indent = Cm(-style.hanging_indent_cm)
    elif style.first_line_indent_chars:
        pf.first_line_indent = Pt(style.first_line_indent_chars * style.size_pt)
    else:
        pf.first_line_indent = Pt(0)


def _add_text_paragraph(doc: Document, text: str, style: TextStyle,
                        word_style: str | None = None,
                        suppress_inherited_page_break: bool = False) -> None:
    """添加文本段落; word_style 指定时使用内置 Word 样式(如 Heading 1)。"""
    par = doc.add_paragraph()
    if word_style and word_style in doc.styles:
        par.style = doc.styles[word_style]
    _apply_paragraph_format(par, style)
    if suppress_inherited_page_break:
        par.paragraph_format.page_break_before = False
    run = par.add_run(text)
    _apply_run_font(run, style)


def _to_section_type(value: str) -> SectionType:
    """项目章节的类型字符串 → SectionType; 无法识别时按正文处理。"""
    try:
        return SectionType(value)
    except ValueError:
        return SectionType.BODY


def _find_node(nodes, section_type: SectionType):
    """在模板章节树里找第一个指定类型的节点。"""
    for node in nodes:
        if node.type is section_type:
            return node
        found = _find_node(node.children, section_type)
        if found is not None:
            return found
    return None


def _style_for(tpl: PaperTemplate | None, section_type: str,
               node_style: TextStyle | None) -> TextStyle:
    """取模板中该章节的生效样式: 节点覆盖 > 类型默认 > body。"""
    if node_style is not None:
        return node_style
    if tpl is not None:
        return tpl.styles.get(section_type) or tpl.styles["body"]
    return TextStyle()


_KEYWORDS_RE = re.compile(r"^(关键词|关键字)\s*[:：]")
_EN_KEYWORDS_RE = re.compile(r"^(key\s*words?|keywords)\s*[:：]", re.I)


def _keyword_style_for(text: str, tpl: PaperTemplate | None) -> TextStyle | None:
    """写在摘要里的关键词段落 → 模板的关键词样式(没有则返回 None)。"""
    if tpl is None or not text:
        return None
    stripped = text.strip()
    if _KEYWORDS_RE.match(stripped):
        return tpl.styles.get("keywords")
    if _EN_KEYWORDS_RE.match(stripped):
        return tpl.styles.get("english_keywords")
    return None


# ---------------------------------------------------------------------------
# 封面: 按模板识别出的版式渲染(标题行/字段行/信息表), 退化为 meta_fields 逐行
# ---------------------------------------------------------------------------

def _meta_value(field, project: WritingProject) -> str:
    return project.meta.get(field.key, "") if field.key else field.value


def _add_cover_field_paragraph(doc: Document, field, project: WritingProject) -> None:
    """字段行: 标签 + 值 在同一段, 各自用模板里的字体。"""
    value_style = field.value_style or TextStyle()
    par = doc.add_paragraph()
    _apply_paragraph_format(par, value_style)
    if field.label:
        label_run = par.add_run(field.label)
        _apply_run_font(label_run, field.label_style or value_style)
    value_run = par.add_run(_meta_value(field, project))
    _apply_run_font(value_run, value_style)


def _apply_cover_table_borders(table) -> None:
    """封面信息表只保留源模板的横线, 不绘制外框和竖线。"""
    for row_index, row in enumerate(table.rows):
        for cell in row.cells:
            tc_pr = cell._tc.get_or_add_tcPr()
            borders = tc_pr.find(qn("w:tcBorders"))
            if borders is None:
                borders = OxmlElement("w:tcBorders")
                tc_pr.append(borders)
            for edge in ("top", "bottom", "left", "right", "insideH", "insideV"):
                old = borders.find(qn(f"w:{edge}"))
                if old is not None:
                    borders.remove(old)
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "4")
            bottom.set(qn("w:space"), "0")
            bottom.set(qn("w:color"), "auto")
            borders.append(bottom)
            if row_index > 0:
                top = OxmlElement("w:top")
                top.set(qn("w:val"), "single")
                top.set(qn("w:sz"), "4")
                top.set(qn("w:space"), "0")
                top.set(qn("w:color"), "auto")
                borders.append(top)


def _add_cover_table(doc: Document, block, project: WritingProject) -> None:
    """封面信息表: 标签列 + 取值列, 无边框, 居中。"""
    rows = block.fields
    table = doc.add_table(rows=len(rows), cols=2)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _apply_cover_table_borders(table)

    widths = [Cm(w) for w in block.column_widths_cm[:2]]
    if len(widths) == 2:
        grid = table._tbl.find(qn("w:tblGrid"))
        if grid is not None:
            for col, width in zip(grid.findall(qn("w:gridCol")), widths):
                col.set(qn("w:w"), str(int(width.twips)))
        for row in table.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = width

    for index, field in enumerate(rows):
        value_style = field.value_style or TextStyle()
        for column, text, style in (
            (0, field.label, field.label_style or value_style),
            (1, _meta_value(field, project), value_style),
        ):
            cell = table.cell(index, column)
            par = cell.paragraphs[0]
            _apply_paragraph_format(par, style)
            run = par.add_run(text)
            _apply_run_font(run, style)


def _render_cover(doc: Document, template: PaperTemplate, project: WritingProject) -> int:
    """封面: 优先按模板识别的版式, 没有版式时退化为 meta_fields 逐行。

    返回写入的元素个数(为 0 表示模板没有封面, 不要单独分节)。
    """
    if not template.cover_blocks:
        for field in template.meta_fields:
            label = field.label
            value = project.meta.get(field.key, "")
            _add_text_paragraph(doc, f"{label}：{value}", _style_for(template, "cover_meta", None))
        return len(template.meta_fields)

    count = 0
    for block in template.cover_blocks:
        if block.kind == "spacer":
            par = doc.add_paragraph()
            _apply_paragraph_format(par, block.style or TextStyle())
        elif block.kind == "text":
            _add_text_paragraph(doc, block.text, block.style or TextStyle())
        elif block.kind == "field" and block.fields:
            _add_cover_field_paragraph(doc, block.fields[0], project)
        elif block.kind == "table" and block.fields:
            _add_cover_table(doc, block, project)
        else:
            continue
        count += 1
    return count


# ---------------------------------------------------------------------------
# 分节与页码: 封面不编号, 前置部分(摘要/目录)罗马数字, 正文阿拉伯数字
# ---------------------------------------------------------------------------

def _start_section(doc: Document, page_number_format: str | None = None,
                   suppress_previous_footer: bool = False):
    """在当前位置开新节, 返回新节对象。

    python-docx 的 add_section() 把页眉页脚引用留给上一节, 新节靠"继承"获得。
    封面要求不显示页码时: 给封面挂一个空页脚, 并把页码页脚显式还给新节,
    否则整篇都会继承到空页脚而没有页码。
    """
    carried = [copy.deepcopy(ref) for ref in
               doc.sections[-1]._sectPr.findall(qn("w:footerReference"))]
    doc.add_section()
    previous, section = doc.sections[-2], doc.sections[-1]

    if suppress_previous_footer:
        _set_empty_footer(previous)
        for ref in carried:
            section._sectPr.append(ref)

    if page_number_format:
        sect_pr = section._sectPr
        pg_num = sect_pr.find(qn("w:pgNumType"))
        if pg_num is None:
            pg_num = OxmlElement("w:pgNumType")
            sect_pr.append(pg_num)
        pg_num.set(qn("w:fmt"), page_number_format)
        pg_num.set(qn("w:start"), "1")
    return section


def _set_page_number_format(section, page_number_format: str) -> None:
    """设置当前节的页码格式并从 1 开始。"""
    sect_pr = section._sectPr
    pg_num = sect_pr.find(qn("w:pgNumType"))
    if pg_num is None:
        pg_num = OxmlElement("w:pgNumType")
        sect_pr.append(pg_num)
    pg_num.set(qn("w:fmt"), page_number_format)
    pg_num.set(qn("w:start"), "1")


def _set_empty_footer(section) -> None:
    """给该节挂一个空页脚(封面不显示页码)。"""
    for ref in section._sectPr.findall(qn("w:footerReference")):
        section._sectPr.remove(ref)
    section.footer.paragraphs   # 触发 python-docx 创建空页脚部件并挂上引用


def _add_toc_field(doc: Document) -> None:
    """目录节: 插入 TOC 域, 打开 Word 时自动更新生成目录。"""
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
    fld_begin.set(qn("w:dirty"), "true")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    result = OxmlElement("w:t")
    result.text = "请右键更新目录"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r.append(fld_begin)
    r.append(instr)
    r.append(fld_sep)
    r.append(result)
    r.append(fld_end)


def _add_page_number_footer(doc: Document, section=None) -> None:
    """页脚: 居中页码域 '第 N 页'。"""
    footer = (section or doc.sections[0]).footer
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


def _has_page_field(doc: Document, rel_id: str | None) -> bool:
    """该页脚部件是否含页码域。"""
    if not rel_id:
        return False
    try:
        return b"PAGE" in doc.part.related_parts[rel_id].blob
    except KeyError:
        return False


def _inherit_section_refs(doc: Document, sect_prs: list) -> None:
    """把前面节的页眉页脚引用补给最后一节。

    模板的正文节通常不写 headerReference, 靠 Word 的"链接到前一节"继承;
    导出合并为单节后没有"前一节", 必须显式补上, 否则页眉页脚全丢。
    """
    final = sect_prs[-1]

    # 页眉: 缺哪个类型就往前找
    present_headers = {el.get(qn("w:type")) or "default"
                       for el in final.findall(qn("w:headerReference"))}
    for prev in reversed(sect_prs[:-1]):
        for el in prev.findall(qn("w:headerReference")):
            ref_type = el.get(qn("w:type")) or "default"
            if ref_type not in present_headers:
                final.append(copy.deepcopy(el))
                present_headers.add(ref_type)

    # 页脚: 末节自带空页脚(无页码域)时, 换成前面真正带页码域的页脚,
    # 否则整篇没有页码 —— 模板正文本身也是靠继承拿到页码的
    footers = final.findall(qn("w:footerReference"))
    if footers and not any(_has_page_field(doc, el.get(qn("r:id"))) for el in footers):
        for prev in reversed(sect_prs[:-1]):
            replacement = next(
                (el for el in prev.findall(qn("w:footerReference"))
                 if _has_page_field(doc, el.get(qn("r:id")))), None)
            if replacement is not None:
                for el in footers:
                    final.remove(el)
                final.append(copy.deepcopy(replacement))
                break


def _clear_body(doc: Document) -> None:
    """清空正文内容, 保留源文档首节的页面与页眉页脚设置。"""
    body = doc.element.body
    sect_prs = list(body.iter(qn("w:sectPr")))
    if sect_prs:
        # 旧实现使用最后一节的 sectPr, 会把正文页眉页脚错误地带到封面。
        # 首节是清空后保留下来的封面节, 后续阶段再按源文档布局补回对应节。
        first = copy.deepcopy(sect_prs[0])
        final = sect_prs[-1]
        for child in list(final):
            final.remove(child)
        for child in first:
            final.append(copy.deepcopy(child))
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            continue
        body.remove(child)


def _open_base_document(template_path: str | Path | None) -> tuple[Document, bool, list]:
    """打开排版基底: 优先用模板 docx, 缺失/损坏时回退空白文档。

    返回 (文档, 是否模板基底, 源文档各节设置)。模板基底沿用模板的
    styles.xml、页边距、页眉页脚与主题字体, 不再硬编码页面设置。
    """
    if template_path:
        path = Path(template_path)
        if path.is_file() and path.suffix.lower() == ".docx":
            try:
                doc = Document(str(path))
                section_layouts = [copy.deepcopy(section._sectPr)
                                   for section in doc.sections]
                _clear_body(doc)
                return doc, True, section_layouts
            except Exception as exc:
                _LOGGER.warning("模板基底不可用, 回退空白文档: %s (%s)", path, exc)
    return Document(), False, []


def _apply_section_layout(section, layout) -> None:
    """把源文档节的页面/页眉/页脚设置复制到新节。"""
    if layout is None:
        return
    sect_pr = section._sectPr
    for child in list(sect_pr):
        sect_pr.remove(child)
    for child in layout:
        sect_pr.append(copy.deepcopy(child))


def _apply_policy_layout(section, layout: dict[str, float] | None) -> None:
    """Apply page constraints parsed from the active writing standard."""
    if not layout:
        return
    attributes = {
        "page_width_cm": "page_width",
        "page_height_cm": "page_height",
        "top_margin_cm": "top_margin",
        "bottom_margin_cm": "bottom_margin",
        "left_margin_cm": "left_margin",
        "right_margin_cm": "right_margin",
        "header_distance_cm": "header_distance",
        "footer_distance_cm": "footer_distance",
    }
    for key, attribute in attributes.items():
        value = layout.get(key)
        if value is None:
            continue
        try:
            setattr(section, attribute, Cm(float(value)))
        except (TypeError, ValueError):
            continue


def _layout_has_page_field(doc: Document, layout) -> bool:
    """源文档节是否引用了带 PAGE 域的页脚。"""
    if layout is None:
        return False
    return any(
        _has_page_field(doc, ref.get(qn("r:id")))
        for ref in layout.findall(qn("w:footerReference"))
    )


def _find_phase_layouts(doc: Document, layouts: list) -> tuple[object | None, object | None]:
    """从源模板中找前置页和正文页的代表性节设置。"""
    front = None
    body = None
    page_layouts = [layout for layout in layouts[1:]
                    if _layout_has_page_field(doc, layout)]
    for layout in page_layouts:
        pg_num = layout.find(qn("w:pgNumType"))
        fmt = pg_num.get(qn("w:fmt")) if pg_num is not None else None
        if front is None and fmt == "upperRoman":
            front = layout
        elif body is None and (fmt != "upperRoman" or len(page_layouts) > 1):
            body = layout
    if front is None and page_layouts:
        front = page_layouts[0]
    if body is None and len(page_layouts) > 1:
        body = page_layouts[1]
    return front, body


def export_project_to_docx(project: WritingProject, template: PaperTemplate, out_path: str,
                           template_path: str | Path | None = None) -> None:
    """把项目按模板导出为 .docx (全新文件)。

    template_path 指向导入的模板 docx 时, 以它为基底排版。
    """
    doc, from_template, source_layouts = _open_base_document(template_path)

    if not from_template:
        # 回退: A4 + 常规页边距
        section = doc.sections[0]
        section.page_width, section.page_height = Cm(21.0), Cm(29.7)
        section.top_margin = section.bottom_margin = Cm(2.54)
        section.left_margin = section.right_margin = Cm(3.17)

    _apply_policy_layout(doc.sections[0], template.page_layout)

    # 无源模板时保留旧行为: 先创建一个页脚, 后续分节继承它。
    if not from_template and not doc.sections[0]._sectPr.findall(qn("w:footerReference")):
        _add_page_number_footer(doc)

    front_layout, body_layout = _find_phase_layouts(doc, source_layouts)

    # 模板里各章节的样式覆盖(key -> TextStyle), 以及章节类型转换表
    node_styles: dict[str, TextStyle | None] = {}

    def collect_node_styles(nodes) -> None:
        for node in nodes:
            node_styles[node.key] = node.style
            collect_node_styles(node.children)

    collect_node_styles(template.sections)
    numbering = _Numbering()
    toc_state = {"emitted": False}
    toc_node = _find_node(template.sections, SectionType.TOC)

    def emit_toc(title: str, style: TextStyle,
                 section_started: bool = False) -> None:
        if toc_state["emitted"]:
            return
        if style.page_break_before and not section_started:
            doc.add_page_break()
        _add_text_paragraph(doc, title, style)
        _add_toc_field(doc)
        toc_state["emitted"] = True

    def ensure_toc() -> None:
        """项目里没有目录节点时, 在前置部分末尾补上目录域。"""
        if toc_state["emitted"] or toc_node is None:
            return
        emit_toc(toc_node.title, _style_for(template, "toc", toc_node.style))

    # 封面: 单独一节, 不显示页码
    cover_rendered = _render_cover(doc, template, project)

    # 模板没有封面内容时不分节(否则第一页会空白)
    state = {"front_started": not cover_rendered, "body_started": not cover_rendered}

    def ensure_section(node_type: SectionType) -> bool:
        """封面/前置部分/正文各自成节, 页码格式按论文惯例。"""
        is_front = node_type in _FRONT_TYPES
        is_body = node_type in _BODY_TYPES
        started = False
        if not state["front_started"] and not state["body_started"]:
            if is_front:
                section = _start_section(doc, suppress_previous_footer=True)
                _apply_section_layout(section, front_layout)
                _apply_policy_layout(section, template.page_layout)
                _set_page_number_format(section, "upperRoman")
                if not section._sectPr.findall(qn("w:footerReference")):
                    _add_page_number_footer(doc, section)
                state["front_started"] = True
                started = True
            elif is_body:
                ensure_toc()
                section = _start_section(doc, suppress_previous_footer=True)
                _apply_section_layout(section, body_layout)
                _apply_policy_layout(section, template.page_layout)
                _set_page_number_format(section, "decimal")
                if not section._sectPr.findall(qn("w:footerReference")):
                    _add_page_number_footer(doc, section)
                state["body_started"] = True
                started = True
            return started
        if is_body and not state["body_started"]:
            had_front = state["front_started"]
            ensure_toc()
            section = _start_section(doc)
            _apply_section_layout(section, body_layout)
            _apply_policy_layout(section, template.page_layout)
            _set_page_number_format(section, "decimal")
            if not had_front and not section._sectPr.findall(qn("w:footerReference")):
                _add_page_number_footer(doc, section)
            state["body_started"] = True
            started = True
        return started

    def emit(proj_sec: ProjectSection) -> None:
        """按项目实际章节顺序渲染一节(写作界面看到的顺序就是导出顺序)。"""
        section_type = _to_section_type(proj_sec.type)
        node_style = node_styles.get(proj_sec.key)
        stype = section_type.value
        section_started = ensure_section(section_type)

        if section_type is SectionType.COVER:
            return  # 封面已处理

        style = _style_for(template, stype, node_style)

        if section_type is SectionType.TOC:
            emit_toc(proj_sec.title, style, section_started=section_started)
            return

        if section_type in (SectionType.ABSTRACT, SectionType.ENGLISH_ABSTRACT):
            if style.page_break_before and not section_started:
                doc.add_page_break()
            _add_text_paragraph(doc, proj_sec.title, style)
            # 摘要正文使用独立样式; 规范中的摘要约束优先于模板默认正文样式。
            body_style_key = "abstract_body" if section_type is SectionType.ABSTRACT else "english_abstract_body"
            body_style = _style_for(template, body_style_key, None)
            for par in proj_sec.paragraphs:
                # 关键词段落写在摘要里时, 按模板的关键词样式排版
                par_style = _keyword_style_for(par.text, template) or body_style
                _export_paragraph(doc, par, par_style, template, numbering)
            return

        if section_type in (SectionType.KEYWORDS, SectionType.ENGLISH_KEYWORDS):
            # 关键词属于正文元信息段落，绝不使用标题样式进入目录，仅作为常规段落呈现
            prefix = "关键词：" if section_type is SectionType.KEYWORDS else "Key Words: "
            for par in proj_sec.paragraphs:
                if not (par.text and par.text.strip()):
                    continue
                text = par.text.strip()
                if not text.startswith(prefix) and not text.startswith("关键词") \
                        and not text.lower().startswith("key words"):
                    text = f"{prefix}{text}"
                p_clone = ParagraphBlock(id=par.id, type=par.type, text=text,
                                         bold=par.bold, italic=par.italic, underline=par.underline,
                                         highlight=par.highlight, vert_align=par.vert_align)
                _export_paragraph(doc, p_clone, style, template, numbering)
            return

        if section_type in (SectionType.HEADING1, SectionType.HEADING2, SectionType.HEADING3):
            if style.page_break_before and not section_started:
                doc.add_page_break()
            if section_type is SectionType.HEADING1:
                numbering.next_chapter()
            _add_text_paragraph(doc, proj_sec.title, style,
                                word_style=_HEADING_STYLE[section_type],
                                suppress_inherited_page_break=(
                                    section_type is SectionType.HEADING1))
            body_style = _style_for(template, "body", None)
            for par in proj_sec.paragraphs:
                _export_paragraph(doc, par, body_style, template, numbering)
            return

        # BODY / REFERENCES / ACKNOWLEDGEMENT / APPENDIX
        if style.page_break_before and not section_started and not proj_sec.title:
            doc.add_page_break()
        if proj_sec.title:
            # 参考文献/致谢等章节标题也作为一级标题, 进入目录
            word_style = "Heading 1" if section_type in (
                SectionType.REFERENCES, SectionType.ACKNOWLEDGEMENT,
                SectionType.APPENDIX) else None
            if section_type is SectionType.REFERENCES:
                numbering.next_chapter()
            # 这类章节的 style 是正文条目样式, 标题需借一级标题样式呈现
            title_style = _style_for(template, "heading1", None) if word_style else style
            if title_style.page_break_before and not section_started:
                doc.add_page_break()
            _add_text_paragraph(doc, proj_sec.title, title_style,
                                word_style=word_style,
                                suppress_inherited_page_break=bool(word_style))
        for par in proj_sec.paragraphs:
            _export_paragraph(doc, par, style, template, numbering)

    for proj_sec in project.sections:
        emit(proj_sec)

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
    if par.type.value == "equation" and par.equation and par.equation.omml:
        p = doc.add_paragraph()
        _apply_paragraph_format(p, style)
        try:
            node = _parse_untrusted_xml(par.equation.omml.encode("utf-8"))
            if etree.QName(node).localname != "oMath":
                raise ValueError("公式节点类型不正确")
            p._p.append(node)
        except (etree.XMLSyntaxError, ValueError):
            fallback = p.add_run(par.equation.alt_text or "[公式无法渲染]")
            _apply_run_font(fallback, style)
        return
    if not par.text:
        return
    p = doc.add_paragraph()
    _apply_paragraph_format(p, style)
    run = p.add_run(par.text)
    _apply_run_font(run, style)
    # 行内格式只"加"不"减": 模板样式里的加粗/斜体/下划线要保留
    if par.bold:
        run.font.bold = True
    if par.italic:
        run.font.italic = True
    if par.underline:
        run.font.underline = True
    if par.highlight is not None:
        _apply_run_highlight(run, par.highlight)
    if par.vert_align is not None:
        _apply_run_vertical_alignment(run, par.vert_align)


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
        shape = par.add_run().add_picture(str(local_path), width=Cm(_IMAGE_WIDTH_CM))
        if shape.height > Cm(_IMAGE_MAX_HEIGHT_CM):
            scale = Cm(_IMAGE_MAX_HEIGHT_CM) / shape.height
            shape.height = Cm(_IMAGE_MAX_HEIGHT_CM)
            shape.width = int(shape.width * scale)
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

    if tbl.source_xml:
        try:
            source = _parse_untrusted_xml(tbl.source_xml.encode("utf-8"))
            if etree.QName(source).localname != "tbl":
                raise ValueError("表格节点类型不正确")
            body = doc._body._element
            body.insert(max(0, len(body) - 1), source)
            return
        except (etree.XMLSyntaxError, ValueError):
            _LOGGER.warning("复杂表格原始 XML 无法回写，将使用结构化单元格", exc_info=True)

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
    import os

    base = Path(os.environ.get(
        "PAPERGUARD_DATA_DIR",
        str(Path(__file__).resolve().parent.parent.parent.parent / "data"),
    )) / "uploads"
    name = url.rsplit("/", 1)[-1]
    return base / name
