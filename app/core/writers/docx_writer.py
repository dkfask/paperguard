"""DocxWriter — 把 IR Document 写回 .docx 文件（不破坏原文件）。

设计契约（plan §3.5）：
- write_ir_to_docx(ir: Document, output_path: str) -> None
- 用 python-docx 高层 API
- 不破坏原文件（必须传 output_path 显式指定）
- 段落：style_id / 第一个 run（text + font_size + font_eastasia）
- 段落格式：line_spacing
- 节：page_width/height / margin 4 方向
- metadata：title 写入 core_properties
"""

from __future__ import annotations

from app.core.ir import Document


def write_ir_to_docx(ir: Document, output_path: str) -> None:
    """把 IR Document 写为 output_path (不破坏原文件)。"""
    import docx
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_COLOR_INDEX, WD_PARAGRAPH_ALIGNMENT, WD_UNDERLINE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = docx.Document()

    # 段落
    for p in ir.paragraphs:
        para = doc.add_paragraph()
        _write_paragraph(
            para, p, doc, Pt, Cm, RGBColor, OxmlElement, qn,
            WD_COLOR_INDEX, WD_UNDERLINE, WD_PARAGRAPH_ALIGNMENT,
        )

    # sections（用第一个节或新建）
    if ir.sections:
        s = ir.sections[0]
        if s.page_width_cm:
            doc.sections[0].page_width = Cm(s.page_width_cm)
        if s.page_height_cm:
            doc.sections[0].page_height = Cm(s.page_height_cm)
        if s.margin_top_cm:
            doc.sections[0].top_margin = Cm(s.margin_top_cm)
        if s.margin_bottom_cm:
            doc.sections[0].bottom_margin = Cm(s.margin_bottom_cm)
        if s.margin_left_cm:
            doc.sections[0].left_margin = Cm(s.margin_left_cm)
        if s.margin_right_cm:
            doc.sections[0].right_margin = Cm(s.margin_right_cm)
        if s.header_distance_cm:
            doc.sections[0].header_distance = Cm(s.header_distance_cm)
        if s.footer_distance_cm:
            doc.sections[0].footer_distance = Cm(s.footer_distance_cm)
        if s.orientation == "landscape":
            doc.sections[0].orientation = WD_ORIENT.LANDSCAPE
        elif s.orientation == "portrait":
            doc.sections[0].orientation = WD_ORIENT.PORTRAIT

    # metadata: title
    if ir.metadata.title:
        doc.core_properties.title = ir.metadata.title

    if ir.header_paragraphs:
        _write_container_paragraphs(
            doc.sections[0].header, ir.header_paragraphs, doc, Pt, Cm,
            RGBColor, OxmlElement, qn, WD_COLOR_INDEX, WD_UNDERLINE,
            WD_PARAGRAPH_ALIGNMENT,
        )
    if ir.footer_paragraphs:
        _write_container_paragraphs(
            doc.sections[0].footer, ir.footer_paragraphs, doc, Pt, Cm,
            RGBColor, OxmlElement, qn, WD_COLOR_INDEX, WD_UNDERLINE,
            WD_PARAGRAPH_ALIGNMENT,
        )

    doc.save(output_path)


def _write_container_paragraphs(
    container,
    paragraphs,
    doc,
    Pt,
    Cm,
    RGBColor,
    OxmlElement,
    qn,
    WD_COLOR_INDEX,
    WD_UNDERLINE,
    WD_PARAGRAPH_ALIGNMENT,
) -> None:
    for i, p in enumerate(paragraphs):
        if i == 0 and container.paragraphs:
            para = container.paragraphs[0]
            para.clear()
        else:
            para = container.add_paragraph()
        _write_paragraph(
            para, p, doc, Pt, Cm, RGBColor, OxmlElement, qn,
            WD_COLOR_INDEX, WD_UNDERLINE, WD_PARAGRAPH_ALIGNMENT,
        )


def _write_paragraph(
    para,
    p,
    doc,
    Pt,
    Cm,
    RGBColor,
    OxmlElement,
    qn,
    WD_COLOR_INDEX,
    WD_UNDERLINE,
    WD_PARAGRAPH_ALIGNMENT,
) -> None:
    _apply_paragraph_style(para, p, doc)
    for run in p.runs:
        r = para.add_run(run.text)
        _apply_run_format(r, run, Pt, RGBColor, OxmlElement, qn, WD_COLOR_INDEX, WD_UNDERLINE)

    if p.line_spacing:
        para.paragraph_format.line_spacing = p.line_spacing
    para.paragraph_format.space_before = Pt(p.space_before_pt)
    para.paragraph_format.space_after = Pt(p.space_after_pt)
    if p.first_line_indent_cm:
        para.paragraph_format.first_line_indent = Cm(p.first_line_indent_cm)
    if p.alignment:
        para.alignment = _paragraph_alignment(p.alignment, WD_PARAGRAPH_ALIGNMENT)
    para.paragraph_format.page_break_before = p.is_page_break_before


def _apply_run_format(r, run, Pt, RGBColor, OxmlElement, qn, WD_COLOR_INDEX, WD_UNDERLINE) -> None:
    """Apply Run IR formatting to a python-docx run."""
    if run.font_size_pt is not None:
        r.font.size = Pt(run.font_size_pt)

    if run.font_ascii:
        r.font.name = run.font_ascii
    elif run.font_hansi:
        r.font.name = run.font_hansi
    elif run.font_eastasia:
        r.font.name = run.font_eastasia

    if run.font_eastasia or run.font_ascii or run.font_hansi or run.font_cs:
        rpr = r._r.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        if run.font_eastasia:
            rfonts.set(qn("w:eastAsia"), run.font_eastasia)
        if run.font_ascii:
            rfonts.set(qn("w:ascii"), run.font_ascii)
        if run.font_hansi:
            rfonts.set(qn("w:hAnsi"), run.font_hansi)
        if run.font_cs:
            rfonts.set(qn("w:cs"), run.font_cs)

    r.bold = run.bold
    r.italic = run.italic
    if run.underline == "single":
        r.underline = True
    elif run.underline == "double":
        r.underline = WD_UNDERLINE.DOUBLE
    elif run.underline is None:
        r.underline = False
    else:
        r.underline = True

    if run.color_hex:
        r.font.color.rgb = RGBColor.from_string(run.color_hex.lstrip("#"))

    if run.highlight:
        highlight = run.highlight.lower()
        color_map = {
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
        r.font.highlight_color = color_map.get(highlight, WD_COLOR_INDEX.YELLOW)
    else:
        r.font.highlight_color = None

    if run.vert_align == "superscript":
        r.font.superscript = True
    elif run.vert_align == "subscript":
        r.font.subscript = True
    elif run.vert_align is None:
        r.font.superscript = False
        r.font.subscript = False


def _paragraph_alignment(value: str, WD_PARAGRAPH_ALIGNMENT):
    alignment = value.lower()
    mapping = {
        "left": WD_PARAGRAPH_ALIGNMENT.LEFT,
        "center": WD_PARAGRAPH_ALIGNMENT.CENTER,
        "right": WD_PARAGRAPH_ALIGNMENT.RIGHT,
        "justify": WD_PARAGRAPH_ALIGNMENT.JUSTIFY,
        "distribute": WD_PARAGRAPH_ALIGNMENT.DISTRIBUTE,
    }
    return mapping.get(alignment, WD_PARAGRAPH_ALIGNMENT.LEFT)


def _apply_paragraph_style(para, p, doc) -> None:
    """Apply heading level first, then fall back to explicit style_id."""
    candidates: list[str] = []
    if p.level is not None:
        candidates.append(f"Heading {p.level}")
    if p.style_id:
        candidates.append(p.style_id)

    for style_name in candidates:
        try:
            if style_name in doc.styles:
                para.style = doc.styles[style_name]
                return
        except (KeyError, AttributeError):
            continue


__all__ = ["write_ir_to_docx"]
