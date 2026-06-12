"""Helpers for converting between editor HTML and structured content blocks."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.models.project import ImageBlock, ListBlock, ParagraphBlock, SectionContent, TableBlock


BLOCK_PATTERN = re.compile(
    r"(<table[\s\S]*?</table>|<img\b[^>]*>|<blockquote[\s\S]*?</blockquote>|<ul[\s\S]*?</ul>|<ol[\s\S]*?</ol>|<p[\s\S]*?</p>|<h[1-6][\s\S]*?</h[1-6]>)",
    re.IGNORECASE,
)
BODY_PATTERN = re.compile(r"<body[^>]*>([\s\S]*?)</body>", re.IGNORECASE)
SRC_PATTERN = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
ROW_PATTERN = re.compile(r"<tr[\s\S]*?</tr>", re.IGNORECASE)
CELL_PATTERN = re.compile(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", re.IGNORECASE)
LIST_ITEM_PATTERN = re.compile(r"<li[^>]*>([\s\S]*?)</li>", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass
class RichTextRun:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_size_pt: float | None = None
    font_name: str | None = None
    color_hex: str | None = None
    highlight: str | None = None
    vert_align: str | None = None


@dataclass
class ParsedParagraph:
    alignment: str | None
    runs: list[RichTextRun]
    quote_level: int = 0
    line_spacing: float | None = None
    first_line_indent_cm: float | None = None


def section_content_from_html(section_id: str, editor_html: str) -> SectionContent:
    return SectionContent(section_id=section_id, blocks=blocks_from_html(editor_html))


def legacy_section_content_to_blocks(
    content_html: str,
    legacy_images: list[dict],
    legacy_tables: list[dict],
) -> list[ParagraphBlock | ImageBlock | TableBlock | ListBlock]:
    blocks = blocks_from_html(content_html)

    for item in legacy_images:
        blocks.append(
            ImageBlock(
                path=item.get("path", ""),
                caption=item.get("caption", ""),
                width_cm=float(item.get("width_cm", 12.0)),
                alignment=item.get("alignment", "center"),
            )
        )

    for item in legacy_tables:
        data = item.get("data") or [["", ""], ["", ""]]
        blocks.append(
            TableBlock(
                rows=int(item.get("rows", len(data) or 2)),
                cols=int(item.get("cols", len(data[0]) if data else 2)),
                data=[[str(cell) for cell in row] for row in data],
                caption=item.get("caption", ""),
                html=build_table_html(data),
            )
        )

    return blocks


def blocks_from_html(editor_html: str) -> list[ParagraphBlock | ImageBlock | TableBlock | ListBlock]:
    body_html = extract_body_html(editor_html)
    matches = list(BLOCK_PATTERN.finditer(body_html))

    if not matches:
        text = clean_text(body_html)
        if text:
            return [ParagraphBlock(html=f"<p>{html.escape(text)}</p>")]
        return []

    blocks: list[ParagraphBlock | ImageBlock | TableBlock | ListBlock] = []
    for match in matches:
        snippet = match.group(0).strip()
        lower = snippet.lower()
        if lower.startswith("<img"):
            image_path = image_src_to_path(snippet)
            blocks.append(ImageBlock(path=image_path))
        elif lower.startswith("<table"):
            table_data = parse_table_html(snippet)
            rows = len(table_data) or 2
            cols = len(table_data[0]) if table_data else 2
            blocks.append(TableBlock(rows=rows, cols=cols, data=table_data, html=snippet))
        elif lower.startswith("<ul") or lower.startswith("<ol"):
            ordered, items_html = parse_list_html(snippet)
            if items_html:
                blocks.append(ListBlock(ordered=ordered, items_html=items_html, html=snippet))
        else:
            if "<img" in lower and not clean_text(snippet):
                image_path = image_src_to_path(snippet)
                blocks.append(ImageBlock(path=image_path))
                continue
            if clean_text(snippet):
                blocks.append(ParagraphBlock(html=snippet))

    return blocks


def blocks_to_editor_html(blocks: list[ParagraphBlock | ImageBlock | TableBlock | ListBlock]) -> str:
    html_parts: list[str] = []
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            if block.html.strip():
                html_parts.append(block.html)
        elif isinstance(block, ImageBlock):
            if block.path:
                src = path_to_image_src(block.path)
                html_parts.append(f'<p><img src="{html.escape(src)}" style="max-width: 500px;" /></p>')
        elif isinstance(block, TableBlock):
            html_parts.append(block.html.strip() or build_table_html(block.data))
        elif isinstance(block, ListBlock):
            html_parts.append(block.html.strip() or build_list_html(block.ordered, block.items_html))
    return "\n".join(part for part in html_parts if part.strip())


def blocks_have_meaningful_content(blocks: list[ParagraphBlock | ImageBlock | TableBlock | ListBlock]) -> bool:
    for block in blocks:
        if isinstance(block, ParagraphBlock) and clean_text(block.html):
            return True
        if isinstance(block, ImageBlock) and block.path.strip():
            return True
        if isinstance(block, TableBlock) and any(cell.strip() for row in block.data for cell in row):
            return True
        if isinstance(block, ListBlock) and any(clean_text(item) for item in block.items_html):
            return True
    return False


def parse_paragraph_html(paragraph_html: str) -> ParsedParagraph:
    parser = _ParagraphHtmlParser()
    parser.feed(paragraph_html)
    parser.close()
    return ParsedParagraph(
        alignment=parser.alignment,
        runs=parser.runs,
        quote_level=parser.max_quote_level,
        line_spacing=parser.line_spacing,
        first_line_indent_cm=parser.first_line_indent_cm,
    )


def extract_body_html(editor_html: str) -> str:
    match = BODY_PATTERN.search(editor_html or "")
    return match.group(1) if match else (editor_html or "")


def image_src_to_path(img_html: str) -> str:
    match = SRC_PATTERN.search(img_html)
    if not match:
        return ""
    src = html.unescape(match.group(1))
    if src.startswith("file:///"):
        parsed = urlparse(src)
        path = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return path
    return src


def path_to_image_src(path: str) -> str:
    return Path(path).as_uri()


def parse_table_html(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in ROW_PATTERN.findall(table_html):
        row: list[str] = []
        for cell_html in CELL_PATTERN.findall(row_html):
            row.append(clean_text(cell_html))
        if row:
            rows.append(row)
    return rows or [["", ""], ["", ""]]


def parse_list_html(list_html: str) -> tuple[bool, list[str]]:
    ordered = list_html.lstrip().lower().startswith("<ol")
    items = []
    for item_html in LIST_ITEM_PATTERN.findall(list_html):
        snippet = item_html.strip()
        if snippet and not re.match(r"^<(p|div|blockquote|h[1-6])\b", snippet, flags=re.IGNORECASE):
            snippet = f"<p>{snippet}</p>"
        items.append(snippet)
    return ordered, items


def build_table_html(data: list[list[str]]) -> str:
    rows = []
    for row in data:
        cells = "".join(
            f'<td style="padding: 6px; border: 1px solid #000;">{html.escape(cell or "") or "&nbsp;"}</td>'
            for cell in row
        )
        rows.append(f"<tr>{cells}</tr>")
    return (
        '<table border="1" style="border-collapse: collapse; width: 100%;">'
        + "".join(rows)
        + "</table>"
    )


def build_list_html(ordered: bool, items_html: list[str]) -> str:
    tag = "ol" if ordered else "ul"
    items = []
    for item in items_html:
        inner = item.strip()
        if inner.lower().startswith("<p") and inner.lower().endswith("</p>"):
            inner = re.sub(r"^<p[^>]*>|</p>$", "", inner, flags=re.IGNORECASE)
        items.append(f"<li>{inner}</li>")
    return f"<{tag}>" + "".join(items) + f"</{tag}>"


def clean_text(source_html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", source_html, flags=re.IGNORECASE)
    text = TAG_PATTERN.sub("", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def _parse_css(style_text: str) -> dict[str, str]:
    styles: dict[str, str] = {}
    for item in style_text.split(";"):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        styles[key.strip().lower()] = value.strip()
    return styles


def _merge_style(parent: dict, tag: str, attrs: dict[str, str]) -> dict:
    style = dict(parent)
    css = _parse_css(attrs.get("style", ""))

    if tag in {"b", "strong"}:
        style["bold"] = True
    if tag in {"i", "em"}:
        style["italic"] = True
    if tag == "u":
        style["underline"] = True

    if css.get("font-weight") in {"bold", "600", "700", "800", "900"}:
        style["bold"] = True
    if css.get("font-style") == "italic":
        style["italic"] = True
    if "underline" in css.get("text-decoration", "").lower():
        style["underline"] = True
    if "font-size" in css:
        match = re.search(r"([0-9.]+)\s*pt", css["font-size"], flags=re.IGNORECASE)
        if match:
            style["font_size_pt"] = float(match.group(1))
    if "font-family" in css:
        families = [part.strip().strip("'\"") for part in css["font-family"].split(",") if part.strip()]
        if families:
            style["font_name"] = families[0]
    if "color" in css:
        color_hex = normalize_color(css["color"])
        if color_hex:
            style["color_hex"] = color_hex
    if "background-color" in css:
        style["highlight"] = normalize_highlight(css["background-color"])
    if "vertical-align" in css:
        style["vert_align"] = normalize_vert_align(css["vertical-align"])
    if tag == "font":
        color_hex = normalize_color(attrs.get("color", ""))
        if color_hex:
            style["color_hex"] = color_hex
        if attrs.get("face"):
            style["font_name"] = attrs["face"].strip().strip("'\"")

    return style


def _parse_line_height(raw_value: str | None) -> float | None:
    if not raw_value:
        return None
    value = raw_value.strip().strip(";")
    percent_match = re.fullmatch(r"([0-9.]+)\s*%", value)
    if percent_match:
        try:
            return round(float(percent_match.group(1)) / 100.0, 3)
        except ValueError:
            return None
    number_match = re.fullmatch(r"([0-9.]+)", value)
    if number_match:
        try:
            return float(number_match.group(1))
        except ValueError:
            return None
    return None


def _parse_text_indent_cm(raw_value: str | None) -> float | None:
    if not raw_value:
        return None
    value = raw_value.strip().strip(";")
    cm_match = re.fullmatch(r"([0-9.]+)\s*cm", value, flags=re.IGNORECASE)
    if cm_match:
        try:
            return float(cm_match.group(1))
        except ValueError:
            return None
    return None


def normalize_color(raw_color: str) -> str | None:
    value = raw_color.strip().strip(";")
    if not value:
        return None
    if value.startswith("#"):
        value = value[1:]
        if len(value) == 3:
            value = "".join(char * 2 for char in value)
        if len(value) == 6:
            return value.upper()
        return None
    rgb_match = re.match(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)",
        value,
        flags=re.IGNORECASE,
    )
    if rgb_match:
        parts = [max(0, min(255, int(group))) for group in rgb_match.groups()]
        return "".join(f"{part:02X}" for part in parts)
    named = {
        "black": "000000",
        "white": "FFFFFF",
        "red": "FF0000",
        "green": "008000",
        "blue": "0000FF",
        "yellow": "FFFF00",
        "gray": "808080",
        "grey": "808080",
        "orange": "FFA500",
        "purple": "800080",
    }
    return named.get(value.lower())


def normalize_highlight(raw_color: str) -> str | None:
    value = raw_color.strip().strip(";").lower()
    if value in {"", "none", "transparent", "inherit", "initial", "unset"}:
        return None

    named = {
        "black",
        "blue",
        "cyan",
        "green",
        "magenta",
        "red",
        "yellow",
        "darkblue",
        "darkcyan",
        "darkgreen",
        "darkmagenta",
        "darkred",
        "darkyellow",
        "lightgray",
        "darkgray",
    }
    if value in named:
        return value
    if value in {"grey", "gray"}:
        return "lightgray"

    color_hex = normalize_color(value)
    return {
        "000000": "black",
        "0000FF": "blue",
        "00FFFF": "cyan",
        "00FF00": "green",
        "FF00FF": "magenta",
        "FF0000": "red",
        "FFFF00": "yellow",
        "808080": "lightgray",
    }.get(color_hex or "")


def normalize_vert_align(raw_value: str) -> str | None:
    value = raw_value.strip().strip(";").lower()
    if value in {"", "baseline", "none", "inherit", "initial", "unset"}:
        return None
    if value in {"super", "superscript"}:
        return "superscript"
    if value in {"sub", "subscript"}:
        return "subscript"
    return None


class _ParagraphHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.runs: list[RichTextRun] = []
        self.alignment: str | None = None
        self.line_spacing: float | None = None
        self.first_line_indent_cm: float | None = None
        self.quote_level = 0
        self.max_quote_level = 0
        self._style_stack: list[dict] = [{}]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_map = {key.lower(): value or "" for key, value in attrs}
        lower_tag = tag.lower()

        if lower_tag in {"p", "div"}:
            css = _parse_css(attr_map.get("style", ""))
            self.alignment = attr_map.get("align") or css.get("text-align")
            self.line_spacing = _parse_line_height(css.get("line-height"))
            self.first_line_indent_cm = _parse_text_indent_cm(css.get("text-indent"))
        if lower_tag == "blockquote":
            self.quote_level += 1
            self.max_quote_level = max(self.max_quote_level, self.quote_level)

        if lower_tag == "br":
            self._append_text("\n")
            return

        self._style_stack.append(_merge_style(self._style_stack[-1], lower_tag, attr_map))

    def handle_endtag(self, tag: str):
        if tag.lower() == "br":
            return
        if tag.lower() == "blockquote" and self.quote_level > 0:
            self.quote_level -= 1
        if len(self._style_stack) > 1:
            self._style_stack.pop()

    def handle_data(self, data: str):
        if data:
            self._append_text(data)

    def _append_text(self, text: str):
        current = self._style_stack[-1]
        run = RichTextRun(
            text=text,
            bold=bool(current.get("bold", False)),
            italic=bool(current.get("italic", False)),
            underline=bool(current.get("underline", False)),
            font_size_pt=current.get("font_size_pt"),
            font_name=current.get("font_name"),
            color_hex=current.get("color_hex"),
            highlight=current.get("highlight"),
            vert_align=current.get("vert_align"),
        )
        if self.runs and self._same_style(self.runs[-1], run):
            self.runs[-1].text += run.text
        else:
            self.runs.append(run)

    @staticmethod
    def _same_style(left: RichTextRun, right: RichTextRun) -> bool:
        return (
            left.bold == right.bold
            and left.italic == right.italic
            and left.underline == right.underline
            and left.font_size_pt == right.font_size_pt
            and left.font_name == right.font_name
            and left.color_hex == right.color_hex
            and left.highlight == right.highlight
            and left.vert_align == right.vert_align
        )
