"""docx → IR 的加载器 (L1 数据访问层)。

MVP 只读 sections + paragraphs + metadata，**不**读 styles / numbering / headers / footers
（V1.1 再加）。

借鉴自 app.services.word_template_importer（不直接 import 避免 PyQt 依赖）：
- Document(str(source)) 实例化方式
- qn("w:rFonts") / qn("w:eastAsia") 取中日韩字体
- _length_to_cm / _length_to_pt 单位转换
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from docx import Document as DocxDocument
from docx.document import Document as DocxDocumentType
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn

from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
    Section,
)


# 1 cm = 567 twips（1 inch = 1440 twips = 2.54 cm → 1 cm = 1440/2.54 = 566.93）
_TWIPS_PER_CM = 1440.0 / 2.54

# 1 pt = 20 twips（1 inch = 72 pt = 1440 twips → 1 pt = 20 twips）
_TWIPS_PER_PT = 20.0

# 1 行高 240 = single（OOXML 用 1/240 单位）
_LINE_SPACING_BASE = 240.0

# heading style_id 推断 level
_HEADING_STYLE_TO_LEVEL = {
    "Heading 1": 1, "标题 1": 1, "标题1": 1,
    "Heading 2": 2, "标题 2": 2, "标题2": 2,
    "Heading 3": 3, "标题 3": 3, "标题3": 3,
    "Heading 4": 4, "标题 4": 4, "标题4": 4,
    "Heading 5": 5, "标题 5": 5, "标题5": 5,
}


def _twips_to_cm(twips: str | int | None) -> float:
    if twips is None:
        return 0.0
    try:
        return round(int(twips) / _TWIPS_PER_CM, 3)
    except (TypeError, ValueError):
        return 0.0


def _half_points_to_pt(half_points: str | int | None) -> float | None:
    if half_points is None:
        return None
    try:
        return int(half_points) / 2.0
    except (TypeError, ValueError):
        return None


def _line_spacing_to_float(line: str | int | None, line_rule: str | None) -> float:
    """OOXML 行距 → 浮点倍数。"""
    if line is None:
        return 1.0
    try:
        n = int(line)
    except (TypeError, ValueError):
        return 1.0
    rule = (line_rule or "auto").lower()
    if rule == "auto":
        # 单位 240 = 1.0，360 = 1.5
        return round(n / _LINE_SPACING_BASE, 3)
    if rule == "exact":
        # 实际磅数（line / 20 = pt）
        return round(n / 20.0, 3)
    if rule == "atleast":
        return round(n / 20.0, 3)
    return 1.0


def _alignment_to_str(alignment) -> str | None:
    mapping = {
        WD_PARAGRAPH_ALIGNMENT.LEFT: "left",
        WD_PARAGRAPH_ALIGNMENT.CENTER: "center",
        WD_PARAGRAPH_ALIGNMENT.RIGHT: "right",
        WD_PARAGRAPH_ALIGNMENT.JUSTIFY: "justify",
        WD_PARAGRAPH_ALIGNMENT.DISTRIBUTE: "distribute",
    }
    return mapping.get(alignment)


def _paragraph_alignment_to_str(paragraph) -> str | None:
    direct = _alignment_to_str(paragraph.alignment)
    if direct is not None:
        return direct
    if paragraph.style is None:
        return None
    return _alignment_to_str(paragraph.style.paragraph_format.alignment)


def _first_line_indent_to_cm(paragraph) -> float:
    """读首行缩进（twips → cm）。

    优先用 firstLineChars（200 = 2 字符）；fallback 到 firstLine（twips）。
    """
    ppr = paragraph._p.find(qn("w:pPr"))
    if ppr is None:
        return 0.0
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        return 0.0
    # firstLineChars 优先
    chars = ind.get(qn("w:firstLineChars"))
    if chars:
        try:
            # 200 = 2 字符（OOXML 字符单位 50 = 1 字符）；需要字号才知道 cm
            # MVP 简化：1 字符 ≈ 0.42 cm（12pt 宋体）。Sprint V1.1 再精确
            return round(int(chars) / 50.0 * 0.42, 3)
        except (TypeError, ValueError):
            pass
    # fallback firstLine (twips)
    fl = ind.get(qn("w:firstLine"))
    if fl:
        return _twips_to_cm(fl)
    return 0.0


def _load_section(section) -> Section:
    """docx Section → IR Section。"""
    return Section(
        page_width_cm=round(float(section.page_width.cm), 3) if section.page_width else 0.0,
        page_height_cm=round(float(section.page_height.cm), 3) if section.page_height else 0.0,
        margin_top_cm=round(float(section.top_margin.cm), 3) if section.top_margin else 0.0,
        margin_bottom_cm=round(float(section.bottom_margin.cm), 3) if section.bottom_margin else 0.0,
        margin_left_cm=round(float(section.left_margin.cm), 3) if section.left_margin else 0.0,
        margin_right_cm=round(float(section.right_margin.cm), 3) if section.right_margin else 0.0,
        header_distance_cm=round(float(section.header_distance.cm), 3) if section.header_distance else 0.0,
        footer_distance_cm=round(float(section.footer_distance.cm), 3) if section.footer_distance else 0.0,
        orientation=str(section.orientation).split(".")[-1].split(" ")[0].lower()
        if section.orientation else "portrait",
    )


def _load_run(run) -> Run:
    """docx Run → IR Run。"""
    # 字体：eastAsia / ascii / hAnsi
    rpr = run._r.find(qn("w:rPr"))
    font_eastasia = None
    font_ascii = None
    font_hansi = None
    font_size_pt = None
    underline = None
    color_hex = None
    vert_align_value = None
    if rpr is not None:
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is not None:
            font_eastasia = rfonts.get(qn("w:eastAsia"))
            font_ascii = rfonts.get(qn("w:ascii"))
            font_hansi = rfonts.get(qn("w:hAnsi"))
        # 字号：OOXML 存半磅
        sz = rpr.find(qn("w:sz"))
        if sz is not None:
            font_size_pt = _half_points_to_pt(sz.get(qn("w:val")))
        u = rpr.find(qn("w:u"))
        if u is not None:
            val = u.get(qn("w:val"))
            if val in (None, "single"):
                underline = "single"
            elif val not in ("none", "0", "false"):
                underline = val
        color = rpr.find(qn("w:color"))
        if color is not None:
            val = color.get(qn("w:val"))
            if val and val.lower() != "auto":
                color_hex = val.upper()
        highlight = rpr.find(qn("w:highlight"))
        if highlight is not None:
            val = highlight.get(qn("w:val"))
            if val and val.lower() not in ("none", "0", "false"):
                highlight_value = val.lower()
            else:
                highlight_value = None
        else:
            highlight_value = None
        vert_align = rpr.find(qn("w:vertAlign"))
        if vert_align is not None:
            val = vert_align.get(qn("w:val"))
            if val in ("superscript", "subscript"):
                vert_align_value = val
    else:
        highlight_value = None

    return Run(
        text=run.text or "",
        font_eastasia=font_eastasia,
        font_ascii=font_ascii,
        font_hansi=font_hansi,
        font_size_pt=font_size_pt,
        bold=bool(run.bold),
        italic=bool(run.italic),
        underline=underline,
        color_hex=color_hex,
        highlight=highlight_value,
        vert_align=vert_align_value,
    )


def _load_paragraph(paragraph) -> Paragraph:
    """docx Paragraph → IR Paragraph。"""
    # style_id
    style_id = paragraph.style.name if paragraph.style else ""

    # level 从 style_id 推断
    level = _HEADING_STYLE_TO_LEVEL.get(style_id)

    # runs
    runs = tuple(_load_run(r) for r in paragraph.runs)

    # 行距
    ppr = paragraph._p.find(qn("w:pPr"))
    line = None
    line_rule = None
    space_before = 0
    space_after = 0
    is_page_break_before = False
    if ppr is not None:
        is_page_break_before = ppr.find(qn("w:pageBreakBefore")) is not None
        spacing = ppr.find(qn("w:spacing"))
        if spacing is not None:
            line = spacing.get(qn("w:line"))
            line_rule = spacing.get(qn("w:lineRule"))
            sb = spacing.get(qn("w:before"))
            sa = spacing.get(qn("w:after"))
            if sb:
                try:
                    space_before = int(sb) // 20  # twips/20 = pt
                except (TypeError, ValueError):
                    space_before = 0
            if sa:
                try:
                    space_after = int(sa) // 20
                except (TypeError, ValueError):
                    space_after = 0

    line_spacing = _line_spacing_to_float(line, line_rule)
    first_line_indent_cm = _first_line_indent_to_cm(paragraph)
    alignment = _paragraph_alignment_to_str(paragraph)

    return Paragraph(
        style_id=style_id,
        text=paragraph.text or "",
        level=level,
        runs=runs,
        line_spacing=line_spacing,
        space_before_pt=float(space_before),
        space_after_pt=float(space_after),
        first_line_indent_cm=first_line_indent_cm,
        alignment=alignment,
        is_page_break_before=is_page_break_before,
    )


def _load_metadata(doc: DocxDocumentType) -> DocumentMetadata:
    """从 docx 的 core_properties 取元数据。"""
    props = doc.core_properties
    return DocumentMetadata(
        title=props.title or "",
        author=props.author or "",
        created=str(props.created) if props.created else "",
        modified=str(props.modified) if props.modified else "",
        language="zh-CN",   # MVP 简化，不读 language.xml
    )


def load_docx(path: str | Path) -> Document:
    """读 .docx 文件，返回 Document IR。

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件不是有效的 .docx
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"docx 文件不存在: {path}")
    if p.suffix.lower() not in (".docx", ".dotx"):
        raise ValueError(f"仅支持 .docx / .dotx 文件: {path}")

    try:
        docx = DocxDocument(str(p))
    except Exception as e:
        raise ValueError(f"无法解析 docx 文件 {path}: {e}") from e

    sections = tuple(_load_section(s) for s in docx.sections)
    paragraphs = tuple(_load_paragraph(p) for p in docx.paragraphs)
    metadata = _load_metadata(docx)

    return Document(
        sections=sections,
        paragraphs=paragraphs,
        metadata=metadata,
    )


__all__ = ["load_docx"]
