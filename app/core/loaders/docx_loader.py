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
        WD_PARAGRAPH_ALIGNMENT.JUSTIFY_MED: "justify",
        WD_PARAGRAPH_ALIGNMENT.JUSTIFY_HI: "justify",
        WD_PARAGRAPH_ALIGNMENT.JUSTIFY_LOW: "justify",
        WD_PARAGRAPH_ALIGNMENT.THAI_JUSTIFY: "justify",
        WD_PARAGRAPH_ALIGNMENT.DISTRIBUTE: "distribute",
    }
    return mapping.get(alignment)


def _style_chain(style):
    seen = set()
    while style is not None:
        marker = id(style)
        if marker in seen:
            break
        seen.add(marker)
        yield style
        style = style.base_style


def _paragraph_styles(paragraph):
    if paragraph.style is None:
        return ()
    return tuple(_style_chain(paragraph.style))


def _paragraph_alignment_to_str(paragraph) -> str | None:
    direct = _alignment_to_str(paragraph.alignment)
    if direct is not None:
        return direct
    for style in _paragraph_styles(paragraph):
        alignment = _alignment_to_str(style.paragraph_format.alignment)
        if alignment is not None:
            return alignment
    return None


def _style_paragraph_format(paragraph):
    styles = _paragraph_styles(paragraph)
    if not styles:
        return None
    return styles[0].paragraph_format


def _length_to_pt(length) -> float:
    if length is None:
        return 0.0
    return round(float(length.pt), 3)


def _length_to_cm(length) -> float:
    if length is None:
        return 0.0
    return round(float(length.cm), 3)


def _style_line_spacing(paragraph) -> float:
    for style in _paragraph_styles(paragraph):
        value = style.paragraph_format.line_spacing
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        return _length_to_pt(value)
    return 1.0


def _style_space_before_pt(paragraph) -> float:
    for style in _paragraph_styles(paragraph):
        value = style.paragraph_format.space_before
        if value is not None:
            return _length_to_pt(value)
    return 0.0


def _style_space_after_pt(paragraph) -> float:
    for style in _paragraph_styles(paragraph):
        value = style.paragraph_format.space_after
        if value is not None:
            return _length_to_pt(value)
    return 0.0


def _style_first_line_indent_cm(paragraph) -> float:
    for style in _paragraph_styles(paragraph):
        value = style.paragraph_format.first_line_indent
        if value is not None:
            return _length_to_cm(value)
    return 0.0


def _style_page_break_before(paragraph) -> bool:
    for style in _paragraph_styles(paragraph):
        value = style.paragraph_format.page_break_before
        if value is not None:
            return bool(value)
    return False


def _first_line_indent_to_cm(paragraph) -> float:
    """读首行缩进（twips → cm）。

    优先用 firstLineChars（200 = 2 字符）；fallback 到 firstLine（twips）。
    """
    ppr = paragraph._p.find(qn("w:pPr"))
    if ppr is None:
        return _style_first_line_indent_cm(paragraph)
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        return _style_first_line_indent_cm(paragraph)
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
    return _style_first_line_indent_cm(paragraph)


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


def _style_rpr(paragraph):
    if paragraph is None or paragraph.style is None:
        return None
    return paragraph.style.element.find(qn("w:rPr"))


def _style_rprs(paragraph):
    return tuple(style.element.find(qn("w:rPr")) for style in _paragraph_styles(paragraph))


def _rfonts_from_rpr(rpr) -> tuple[str | None, str | None, str | None, str | None]:
    if rpr is None:
        return None, None, None, None
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        return None, None, None, None
    return (
        rfonts.get(qn("w:eastAsia")),
        rfonts.get(qn("w:ascii")),
        rfonts.get(qn("w:hAnsi")),
        rfonts.get(qn("w:cs")),
    )


def _style_rfonts(paragraph) -> tuple[str | None, str | None, str | None, str | None]:
    font_eastasia = None
    font_ascii = None
    font_hansi = None
    font_cs = None
    for rpr in _style_rprs(paragraph):
        rpr_eastasia, rpr_ascii, rpr_hansi, rpr_cs = _rfonts_from_rpr(rpr)
        font_eastasia = font_eastasia or rpr_eastasia
        font_ascii = font_ascii or rpr_ascii
        font_hansi = font_hansi or rpr_hansi
        font_cs = font_cs or rpr_cs
    return font_eastasia, font_ascii, font_hansi, font_cs


def _font_size_from_rpr(rpr) -> float | None:
    if rpr is None:
        return None
    sz = rpr.find(qn("w:sz"))
    if sz is None:
        return None
    return _half_points_to_pt(sz.get(qn("w:val")))


def _style_font_size_pt(paragraph) -> float | None:
    for rpr in _style_rprs(paragraph):
        value = _font_size_from_rpr(rpr)
        if value is not None:
            return value
    return None


_MISSING = object()


def _bool_property_from_rpr(rpr, tag: str):
    if rpr is None:
        return _MISSING
    element = rpr.find(qn(tag))
    if element is None:
        return _MISSING
    val = element.get(qn("w:val"))
    if val is not None and val.lower() in ("0", "false", "off"):
        return False
    return True


def _underline_from_rpr(rpr):
    if rpr is None:
        return _MISSING
    element = rpr.find(qn("w:u"))
    if element is None:
        return _MISSING
    val = element.get(qn("w:val"))
    if val in (None, "single"):
        return "single"
    if val in ("none", "0", "false"):
        return None
    return val


def _color_from_rpr(rpr):
    if rpr is None:
        return _MISSING
    element = rpr.find(qn("w:color"))
    if element is None:
        return _MISSING
    val = element.get(qn("w:val"))
    if not val or val.lower() == "auto":
        return None
    return val.upper()


def _highlight_from_rpr(rpr):
    if rpr is None:
        return _MISSING
    element = rpr.find(qn("w:highlight"))
    if element is None:
        return _MISSING
    val = element.get(qn("w:val"))
    if val and val.lower() not in ("none", "0", "false"):
        return val.lower()
    return None


def _vert_align_from_rpr(rpr):
    if rpr is None:
        return _MISSING
    element = rpr.find(qn("w:vertAlign"))
    if element is None:
        return _MISSING
    val = element.get(qn("w:val"))
    if val in ("superscript", "subscript"):
        return val
    return None


def _with_style_fallback(direct, inherited, default=None):
    if direct is not _MISSING:
        return direct
    if inherited is not _MISSING:
        return inherited
    return default


def _style_rpr_value(paragraph, reader):
    for rpr in _style_rprs(paragraph):
        value = reader(rpr)
        if value is not _MISSING:
            return value
    return _MISSING


def _load_run(run, paragraph=None) -> Run:
    """docx Run → IR Run。"""
    # 字体：eastAsia / ascii / hAnsi
    rpr = run._r.find(qn("w:rPr"))
    font_eastasia, font_ascii, font_hansi, font_cs = _style_rfonts(paragraph)
    font_size_pt = _style_font_size_pt(paragraph)
    underline = None
    color_hex = None
    vert_align_value = None
    if rpr is not None:
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is not None:
            font_eastasia = rfonts.get(qn("w:eastAsia")) or font_eastasia
            font_ascii = rfonts.get(qn("w:ascii")) or font_ascii
            font_hansi = rfonts.get(qn("w:hAnsi")) or font_hansi
            font_cs = rfonts.get(qn("w:cs")) or font_cs
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

    bold = _with_style_fallback(
        _bool_property_from_rpr(rpr, "w:b"),
        _style_rpr_value(paragraph, lambda style_rpr: _bool_property_from_rpr(style_rpr, "w:b")),
        False,
    )
    italic = _with_style_fallback(
        _bool_property_from_rpr(rpr, "w:i"),
        _style_rpr_value(paragraph, lambda style_rpr: _bool_property_from_rpr(style_rpr, "w:i")),
        False,
    )
    underline = _with_style_fallback(_underline_from_rpr(rpr), _style_rpr_value(paragraph, _underline_from_rpr))
    color_hex = _with_style_fallback(_color_from_rpr(rpr), _style_rpr_value(paragraph, _color_from_rpr))
    highlight_value = _with_style_fallback(_highlight_from_rpr(rpr), _style_rpr_value(paragraph, _highlight_from_rpr))
    vert_align_value = _with_style_fallback(_vert_align_from_rpr(rpr), _style_rpr_value(paragraph, _vert_align_from_rpr))

    return Run(
        text=run.text or "",
        font_eastasia=font_eastasia,
        font_ascii=font_ascii,
        font_hansi=font_hansi,
        font_cs=font_cs,
        font_size_pt=font_size_pt,
        bold=bold,
        italic=italic,
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
    runs = tuple(_load_run(r, paragraph) for r in paragraph.runs)

    # 行距
    ppr = paragraph._p.find(qn("w:pPr"))
    line = None
    line_rule = None
    space_before = None
    space_after = None
    is_page_break_before = None
    if ppr is not None:
        page_break_before = ppr.find(qn("w:pageBreakBefore"))
        if page_break_before is not None:
            val = page_break_before.get(qn("w:val"))
            is_page_break_before = not (val and val.lower() in ("0", "false", "off"))
        spacing = ppr.find(qn("w:spacing"))
        if spacing is not None:
            line = spacing.get(qn("w:line"))
            line_rule = spacing.get(qn("w:lineRule"))
            sb = spacing.get(qn("w:before"))
            sa = spacing.get(qn("w:after"))
            if sb is not None:
                try:
                    space_before = int(sb) / 20  # twips/20 = pt
                except (TypeError, ValueError):
                    space_before = 0
            if sa is not None:
                try:
                    space_after = int(sa) / 20
                except (TypeError, ValueError):
                    space_after = 0

    line_spacing = _line_spacing_to_float(line, line_rule) if line is not None else _style_line_spacing(paragraph)
    first_line_indent_cm = _first_line_indent_to_cm(paragraph)
    alignment = _paragraph_alignment_to_str(paragraph)
    if space_before is None:
        space_before = _style_space_before_pt(paragraph)
    if space_after is None:
        space_after = _style_space_after_pt(paragraph)
    if is_page_break_before is None:
        is_page_break_before = _style_page_break_before(paragraph)

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


def _iter_block_paragraphs(parent):
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table as DocxTable
    from docx.table import _Cell
    from docx.text.paragraph import Paragraph as DocxParagraph

    if isinstance(parent, DocxDocumentType):
        parent_element = parent.element.body
    elif isinstance(parent, _Cell):
        parent_element = parent._tc
    else:
        parent_element = parent._element

    for child in parent_element.iterchildren():
        if isinstance(child, CT_P):
            yield DocxParagraph(child, parent)
        elif isinstance(child, CT_Tbl):
            table = DocxTable(child, parent)
            seen_cells = set()
            for row in table.rows:
                for cell in row.cells:
                    marker = id(cell._tc)
                    if marker in seen_cells:
                        continue
                    seen_cells.add(marker)
                    yield from _iter_block_paragraphs(cell)


def _load_header_footer_paragraphs(sections, attr: str) -> tuple[Paragraph, ...]:
    paragraphs: list[Paragraph] = []
    seen = set()
    for section in sections:
        container = getattr(section, attr)
        for paragraph in _iter_block_paragraphs(container):
            marker = id(paragraph._p)
            if marker in seen:
                continue
            seen.add(marker)
            if not (paragraph.text or "").strip():
                continue
            paragraphs.append(_load_paragraph(paragraph))
    return tuple(paragraphs)


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
    paragraphs = tuple(_load_paragraph(p) for p in _iter_block_paragraphs(docx))
    header_paragraphs = _load_header_footer_paragraphs(docx.sections, "header")
    footer_paragraphs = _load_header_footer_paragraphs(docx.sections, "footer")
    metadata = _load_metadata(docx)

    return Document(
        sections=sections,
        paragraphs=paragraphs,
        metadata=metadata,
        header_paragraphs=header_paragraphs,
        footer_paragraphs=footer_paragraphs,
    )


__all__ = ["load_docx"]
