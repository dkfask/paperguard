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
    from docx.enum.text import WD_COLOR_INDEX, WD_UNDERLINE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = docx.Document()

    # 段落
    for p in ir.paragraphs:
        para = doc.add_paragraph()
        # style_id
        try:
            if p.style_id and p.style_id in doc.styles:
                para.style = doc.styles[p.style_id]
        except (KeyError, AttributeError):
            pass
        for run in p.runs:
            r = para.add_run(run.text)
            _apply_run_format(r, run, Pt, RGBColor, OxmlElement, qn, WD_COLOR_INDEX, WD_UNDERLINE)

        # paragraph format
        if p.line_spacing:
            para.paragraph_format.line_spacing = p.line_spacing
        para.paragraph_format.space_before = Pt(p.space_before_pt)
        para.paragraph_format.space_after = Pt(p.space_after_pt)
        if p.first_line_indent_cm:
            para.paragraph_format.first_line_indent = Cm(p.first_line_indent_cm)
        para.paragraph_format.page_break_before = p.is_page_break_before

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

    # metadata: title
    if ir.metadata.title:
        doc.core_properties.title = ir.metadata.title

    doc.save(output_path)


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


__all__ = ["write_ir_to_docx"]
