"""Import page setup and styles from Word templates."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Length

from app.models.format_config import FormatConfig, StyleConfig
from app.models.template import SectionNode, Template, default_structure


SUPPORTED_WORD_TEMPLATE_EXTENSIONS = {".docx", ".dotx"}

ALIGNMENT_REVERSE_MAP = {
    WD_ALIGN_PARAGRAPH.LEFT: "left",
    WD_ALIGN_PARAGRAPH.CENTER: "center",
    WD_ALIGN_PARAGRAPH.RIGHT: "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
}

STYLE_SOURCE_MAP = {
    "cover_title": ["Title", "标题", "封面标题", "论文题目"],
    "heading_1": ["Heading 1", "标题 1", "标题1"],
    "heading_2": ["Heading 2", "标题 2", "标题2"],
    "heading_3": ["Heading 3", "标题 3", "标题3"],
    "body_text": ["正文缩进2字符", "Body Text First Indent", "正文", "Normal", "Body Text"],
}


def import_word_template(path: str | Path) -> Template:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Word 模板文件不存在: {source}")
    if source.suffix.lower() not in SUPPORTED_WORD_TEMPLATE_EXTENSIONS:
        raise ValueError("仅支持导入 .docx 和 .dotx 文件。")

    try:
        document = Document(str(source))
    except Exception as error:  # pragma: no cover - depends on third-party parser failures
        raise ValueError(f"无法解析 Word 模板：{error}") from error

    template = Template(
        name=source.stem,
        description=f"导入自 Word 模板：{source.name}",
        format_config=FormatConfig(),
        structure=default_structure(),
        file_path=None,
    )

    _import_page_setup(document, template)
    _import_header_footer(document, template)
    _import_styles(document, template)
    _import_structure(document, template)
    return template


def _import_page_setup(document: Document, template: Template):
    section = document.sections[0]
    page = template.format_config.page_setup
    page.width_cm = _length_to_cm(section.page_width)
    page.height_cm = _length_to_cm(section.page_height)
    page.margin_top_cm = _length_to_cm(section.top_margin)
    page.margin_bottom_cm = _length_to_cm(section.bottom_margin)
    page.margin_left_cm = _length_to_cm(section.left_margin)
    page.margin_right_cm = _length_to_cm(section.right_margin)
    page.header_margin_cm = _length_to_cm(section.header_distance)
    page.footer_margin_cm = _length_to_cm(section.footer_distance)
    page.orientation = "landscape" if section.orientation == WD_ORIENT.LANDSCAPE else "portrait"

    matched_size = None
    for paper_size, (width_cm, height_cm) in {
        "A4": (21.0, 29.7),
        "A3": (29.7, 42.0),
        "Letter": (21.59, 27.94),
        "B5": (17.6, 25.0),
    }.items():
        dims = sorted((round(width_cm, 1), round(height_cm, 1)))
        current = sorted((round(page.width_cm, 1), round(page.height_cm, 1)))
        if current == dims:
            matched_size = paper_size
            break
    page.paper_size = matched_size or page.paper_size


def _import_header_footer(document: Document, template: Template):
    section = document.sections[0]
    template.format_config.header_text = _collect_text(section.header.paragraphs)
    template.format_config.footer_text = _collect_text(section.footer.paragraphs)


def _import_styles(document: Document, template: Template):
    for internal_style_id, candidates in STYLE_SOURCE_MAP.items():
        source_style = _find_style(document, candidates)
        if source_style is None:
            continue
        current = template.format_config.styles.get(internal_style_id, StyleConfig(style_id=internal_style_id))
        template.format_config.styles[internal_style_id] = _style_from_word(source_style, current, internal_style_id)


def _import_structure(document: Document, template: Template):
    imported = _structure_from_heading_paragraphs(document)
    if imported:
        template.structure = imported


def _structure_from_heading_paragraphs(document: Document) -> list[SectionNode]:
    roots: list[SectionNode] = []
    stack: list[SectionNode | None] = [None, None, None]
    counters = {1: 0, 2: 0, 3: 0}

    for paragraph in document.paragraphs:
        label = paragraph.text.strip()
        if not label:
            continue
        level = _heading_level(paragraph)
        if level is None:
            continue

        counters[level] += 1
        for deeper in range(level + 1, 4):
            counters[deeper] = 0
            stack[deeper - 1] = None

        node = SectionNode(
            id=_section_id(label, level, counters),
            label=label,
            type=_section_type(label, level),
            required=True,
            auto_generate=_section_type(label, level) == "toc",
            style_id=f"heading_{level}",
        )

        parent = _nearest_parent(stack, level)
        if parent is None:
            roots.append(node)
        else:
            parent.subsections.append(node)
        stack[level - 1] = node

    return roots


def _heading_level(paragraph) -> int | None:
    style = paragraph.style
    tokens = [
        getattr(style, "name", "") or "",
        getattr(style, "style_id", "") or "",
    ]
    normalized = [_normalize_style_token(token) for token in tokens]
    for level in (1, 2, 3):
        candidates = {
            f"heading{level}",
            f"标题{level}",
        }
        if any(token in candidates for token in normalized):
            return level
    return None


def _nearest_parent(stack: list[SectionNode | None], level: int) -> SectionNode | None:
    for index in range(level - 2, -1, -1):
        if stack[index] is not None:
            return stack[index]
    return None


def _section_type(label: str, level: int) -> str:
    normalized = label.replace(" ", "")
    if "摘要" in normalized:
        return "abstract"
    if "目录" in normalized:
        return "toc"
    if "参考文献" in normalized:
        return "references"
    if "致谢" in normalized:
        return "acknowledgements"
    if "附录" in normalized:
        return "appendix"
    return "chapter" if level == 1 else "section"


def _section_id(label: str, level: int, counters: dict[int, int]) -> str:
    section_type = _section_type(label, level)
    if section_type in {"abstract", "toc", "references", "acknowledgements", "appendix"}:
        return _special_section_id(section_type, label, counters[level])
    parts = [str(counters[index]) for index in range(1, level + 1) if counters[index]]
    return "imported_" + "_".join(parts)


def _special_section_id(section_type: str, label: str, ordinal: int) -> str:
    if section_type == "abstract":
        return "abstract_en" if "英文" in label or "English" in label else "abstract_cn"
    if section_type == "appendix":
        return f"appendix_{ordinal}"
    return section_type


def _find_style(document: Document, candidates: list[str]):
    normalized_styles = {}
    for style in document.styles:
        name = getattr(style, "name", "") or ""
        style_id = getattr(style, "style_id", "") or ""
        for token in (name, style_id):
            normalized = _normalize_style_token(token)
            if normalized:
                normalized_styles.setdefault(normalized, style)
    fallback = None
    for candidate in candidates:
        matched = normalized_styles.get(_normalize_style_token(candidate))
        if matched is None:
            continue
        fallback = fallback or matched
        if _style_has_custom_formatting(matched):
            return matched
    return fallback


def _normalize_style_token(value: str) -> str:
    return value.lower().replace(" ", "").strip()


def _style_has_custom_formatting(style) -> bool:
    font = style.font
    paragraph = style.paragraph_format
    if any(
        [
            font.name,
            font.size,
            font.bold is not None,
            font.italic is not None,
            _extract_east_asia_font(style),
            _extract_color_hex(style),
        ]
    ):
        return True
    return any(
        [
            paragraph.alignment is not None,
            paragraph.line_spacing is not None,
            paragraph.space_before is not None,
            paragraph.space_after is not None,
            paragraph.first_line_indent is not None,
            paragraph.left_indent is not None,
            paragraph.right_indent is not None,
        ]
    )


def _style_from_word(source_style, fallback: StyleConfig, style_id: str) -> StyleConfig:
    imported = StyleConfig(style_id=style_id)
    imported.font.name_en = source_style.font.name or fallback.font.name_en
    imported.font.name_cn = _extract_east_asia_font(source_style) or fallback.font.name_cn
    imported.font.size_pt = float(source_style.font.size.pt) if source_style.font.size else fallback.font.size_pt
    imported.font.bold = source_style.font.bold if source_style.font.bold is not None else fallback.font.bold
    imported.font.italic = source_style.font.italic if source_style.font.italic is not None else fallback.font.italic
    imported.font.color_hex = _extract_color_hex(source_style) or fallback.font.color_hex

    paragraph = source_style.paragraph_format
    imported.paragraph.alignment = ALIGNMENT_REVERSE_MAP.get(paragraph.alignment, fallback.paragraph.alignment)
    imported.paragraph.line_spacing = _extract_line_spacing(paragraph.line_spacing, imported.font.size_pt, fallback.paragraph.line_spacing)
    imported.paragraph.space_before_pt = _length_to_pt(paragraph.space_before) if paragraph.space_before else fallback.paragraph.space_before_pt
    imported.paragraph.space_after_pt = _length_to_pt(paragraph.space_after) if paragraph.space_after else fallback.paragraph.space_after_pt
    imported.paragraph.first_line_indent_cm = _length_to_cm(paragraph.first_line_indent) if paragraph.first_line_indent else fallback.paragraph.first_line_indent_cm
    imported.paragraph.left_indent_cm = _length_to_cm(paragraph.left_indent) if paragraph.left_indent else fallback.paragraph.left_indent_cm
    imported.paragraph.right_indent_cm = _length_to_cm(paragraph.right_indent) if paragraph.right_indent else fallback.paragraph.right_indent_cm
    return imported


def _extract_east_asia_font(style) -> str | None:
    rpr = style.element.find(qn("w:rPr"))
    if rpr is None:
        return None
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        return None
    return rfonts.get(qn("w:eastAsia"))


def _extract_color_hex(style) -> str | None:
    color = style.font.color
    rgb = getattr(color, "rgb", None)
    return str(rgb) if rgb else None


def _extract_line_spacing(value, font_size_pt: float, fallback: float) -> float:
    if value is None:
        return fallback
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    if hasattr(value, "pt") and font_size_pt:
        return round(max(value.pt / font_size_pt, 1.0), 2)
    return fallback


def _collect_text(paragraphs) -> str:
    return "\n".join(
        paragraph.text.strip()
        for paragraph in paragraphs
        if paragraph.text and paragraph.text.strip()
    )


def _length_to_cm(length: Length | None) -> float:
    return round(float(length.cm), 2) if length is not None else 0.0


def _length_to_pt(length: Length | None) -> float:
    return round(float(length.pt), 2) if length is not None else 0.0
