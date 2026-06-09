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
    from docx.shared import Cm, Pt

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
        # 第一个 run
        if p.runs:
            run = p.runs[0]
            r = para.add_run(run.text)
            if run.font_size_pt is not None:
                r.font.size = Pt(run.font_size_pt)
            if run.font_eastasia:
                r.font.name = run.font_eastasia
        # line_spacing
        if p.line_spacing:
            para.paragraph_format.line_spacing = p.line_spacing

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


__all__ = ["write_ir_to_docx"]
