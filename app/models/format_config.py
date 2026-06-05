"""Format configuration data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


PAPER_SIZE_PRESETS: dict[str, tuple[float, float]] = {
    "A4": (21.0, 29.7),
    "A3": (29.7, 42.0),
    "Letter": (21.59, 27.94),
    "B5": (17.6, 25.0),
}


@dataclass
class FontConfig:
    name_cn: str = "宋体"
    name_en: str = "Times New Roman"
    size_pt: float = 12.0
    bold: bool = False
    italic: bool = False
    color_hex: str = "000000"


@dataclass
class ParagraphConfig:
    alignment: str = "justify"
    line_spacing: float = 1.5
    space_before_pt: float = 0
    space_after_pt: float = 0
    first_line_indent_cm: float = 0.74
    left_indent_cm: float = 0
    right_indent_cm: float = 0


@dataclass
class PageSetup:
    paper_size: str = "A4"
    width_cm: float = 21.0
    height_cm: float = 29.7
    margin_top_cm: float = 2.54
    margin_bottom_cm: float = 2.54
    margin_left_cm: float = 3.17
    margin_right_cm: float = 3.17
    header_margin_cm: float = 1.5
    footer_margin_cm: float = 1.75
    orientation: str = "portrait"

    def apply_paper_size(self, paper_size: str):
        self.paper_size = paper_size
        size = PAPER_SIZE_PRESETS.get(paper_size)
        if size:
            self.width_cm, self.height_cm = size


@dataclass
class StyleConfig:
    font: FontConfig = field(default_factory=FontConfig)
    paragraph: ParagraphConfig = field(default_factory=ParagraphConfig)
    style_id: str = ""
    based_on: Optional[str] = None

    @classmethod
    def heading_1(cls) -> "StyleConfig":
        return cls(
            style_id="heading_1",
            font=FontConfig(name_cn="黑体", name_en="Times New Roman", size_pt=16.0, bold=True),
            paragraph=ParagraphConfig(
                alignment="center",
                line_spacing=1.5,
                space_before_pt=12,
                space_after_pt=6,
                first_line_indent_cm=0,
            ),
        )

    @classmethod
    def heading_2(cls) -> "StyleConfig":
        return cls(
            style_id="heading_2",
            font=FontConfig(name_cn="黑体", name_en="Times New Roman", size_pt=14.0, bold=True),
            paragraph=ParagraphConfig(
                alignment="left",
                line_spacing=1.5,
                space_before_pt=8,
                space_after_pt=4,
                first_line_indent_cm=0,
            ),
        )

    @classmethod
    def heading_3(cls) -> "StyleConfig":
        return cls(
            style_id="heading_3",
            font=FontConfig(name_cn="黑体", name_en="Times New Roman", size_pt=13.0, bold=True),
            paragraph=ParagraphConfig(
                alignment="left",
                line_spacing=1.5,
                space_before_pt=6,
                space_after_pt=3,
                first_line_indent_cm=0,
            ),
        )

    @classmethod
    def body_text(cls) -> "StyleConfig":
        return cls(
            style_id="body_text",
            font=FontConfig(name_cn="宋体", name_en="Times New Roman", size_pt=12.0),
            paragraph=ParagraphConfig(
                alignment="justify",
                line_spacing=1.5,
                first_line_indent_cm=0.74,
            ),
        )

    @classmethod
    def cover_title(cls) -> "StyleConfig":
        return cls(
            style_id="cover_title",
            font=FontConfig(name_cn="黑体", name_en="Times New Roman", size_pt=22.0, bold=True),
            paragraph=ParagraphConfig(
                alignment="center",
                line_spacing=2.0,
                first_line_indent_cm=0,
            ),
        )

    def to_dict(self) -> dict:
        return {
            "style_id": self.style_id,
            "font": {
                "name_cn": self.font.name_cn,
                "name_en": self.font.name_en,
                "size_pt": self.font.size_pt,
                "bold": self.font.bold,
                "italic": self.font.italic,
                "color_hex": self.font.color_hex,
            },
            "paragraph": {
                "alignment": self.paragraph.alignment,
                "line_spacing": self.paragraph.line_spacing,
                "space_before_pt": self.paragraph.space_before_pt,
                "space_after_pt": self.paragraph.space_after_pt,
                "first_line_indent_cm": self.paragraph.first_line_indent_cm,
                "left_indent_cm": self.paragraph.left_indent_cm,
                "right_indent_cm": self.paragraph.right_indent_cm,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StyleConfig":
        font_data = data.get("font", {})
        para_data = data.get("paragraph", {})
        return cls(
            style_id=data.get("style_id", ""),
            font=FontConfig(
                name_cn=font_data.get("name_cn", "宋体"),
                name_en=font_data.get("name_en", "Times New Roman"),
                size_pt=font_data.get("size_pt", 12.0),
                bold=font_data.get("bold", False),
                italic=font_data.get("italic", False),
                color_hex=font_data.get("color_hex", "000000"),
            ),
            paragraph=ParagraphConfig(
                alignment=para_data.get("alignment", "justify"),
                line_spacing=para_data.get("line_spacing", 1.5),
                space_before_pt=para_data.get("space_before_pt", 0),
                space_after_pt=para_data.get("space_after_pt", 0),
                first_line_indent_cm=para_data.get("first_line_indent_cm", 0.74),
                left_indent_cm=para_data.get("left_indent_cm", 0),
                right_indent_cm=para_data.get("right_indent_cm", 0),
            ),
        )


@dataclass
class FormatConfig:
    page_setup: PageSetup = field(default_factory=PageSetup)
    styles: dict[str, StyleConfig] = field(
        default_factory=lambda: {
            "heading_1": StyleConfig.heading_1(),
            "heading_2": StyleConfig.heading_2(),
            "heading_3": StyleConfig.heading_3(),
            "body_text": StyleConfig.body_text(),
            "cover_title": StyleConfig.cover_title(),
        }
    )
    header_text: str = ""
    footer_text: str = ""
    page_number_start: int = 1
    cover_page_number: bool = False
    toc_include: bool = True

    def to_dict(self) -> dict:
        return {
            "page_setup": {
                "paper_size": self.page_setup.paper_size,
                "width_cm": self.page_setup.width_cm,
                "height_cm": self.page_setup.height_cm,
                "margin_top_cm": self.page_setup.margin_top_cm,
                "margin_bottom_cm": self.page_setup.margin_bottom_cm,
                "margin_left_cm": self.page_setup.margin_left_cm,
                "margin_right_cm": self.page_setup.margin_right_cm,
                "header_margin_cm": self.page_setup.header_margin_cm,
                "footer_margin_cm": self.page_setup.footer_margin_cm,
                "orientation": self.page_setup.orientation,
            },
            "styles": {key: value.to_dict() for key, value in self.styles.items()},
            "header_text": self.header_text,
            "footer_text": self.footer_text,
            "page_number_start": self.page_number_start,
            "cover_page_number": self.cover_page_number,
            "toc_include": self.toc_include,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FormatConfig":
        page_data = data.get("page_setup", {})
        page_setup = PageSetup(
            paper_size=page_data.get("paper_size", "A4"),
            width_cm=page_data.get("width_cm", 21.0),
            height_cm=page_data.get("height_cm", 29.7),
            margin_top_cm=page_data.get("margin_top_cm", 2.54),
            margin_bottom_cm=page_data.get("margin_bottom_cm", 2.54),
            margin_left_cm=page_data.get("margin_left_cm", 3.17),
            margin_right_cm=page_data.get("margin_right_cm", 3.17),
            header_margin_cm=page_data.get("header_margin_cm", 1.5),
            footer_margin_cm=page_data.get("footer_margin_cm", 1.75),
            orientation=page_data.get("orientation", "portrait"),
        )
        styles = {
            key: StyleConfig.from_dict(value)
            for key, value in data.get("styles", {}).items()
        } or {
            "heading_1": StyleConfig.heading_1(),
            "heading_2": StyleConfig.heading_2(),
            "heading_3": StyleConfig.heading_3(),
            "body_text": StyleConfig.body_text(),
            "cover_title": StyleConfig.cover_title(),
        }
        return cls(
            page_setup=page_setup,
            styles=styles,
            header_text=data.get("header_text", ""),
            footer_text=data.get("footer_text", ""),
            page_number_start=data.get("page_number_start", 1),
            cover_page_number=data.get("cover_page_number", False),
            toc_include=data.get("toc_include", True),
        )
