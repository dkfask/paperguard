"""Project data model for a student's paper draft."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParagraphBlock:
    """A rich-text paragraph stored as HTML."""

    block_type: str = "paragraph"
    html: str = ""

    def to_dict(self) -> dict:
        return {"type": self.block_type, "html": self.html}

    @classmethod
    def from_dict(cls, data: dict) -> "ParagraphBlock":
        return cls(html=data.get("html", ""))


@dataclass
class ImageBlock:
    """An image block."""

    block_type: str = "image"
    path: str = ""
    caption: str = ""
    width_cm: float = 12.0
    alignment: str = "center"

    def to_dict(self) -> dict:
        return {
            "type": self.block_type,
            "path": self.path,
            "caption": self.caption,
            "width_cm": self.width_cm,
            "alignment": self.alignment,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageBlock":
        return cls(
            path=data.get("path", ""),
            caption=data.get("caption", ""),
            width_cm=float(data.get("width_cm", 12.0)),
            alignment=data.get("alignment", "center"),
        )


@dataclass
class TableBlock:
    """A table block."""

    block_type: str = "table"
    rows: int = 2
    cols: int = 2
    data: list[list[str]] = field(default_factory=lambda: [["", ""], ["", ""]])
    caption: str = ""
    html: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.block_type,
            "rows": self.rows,
            "cols": self.cols,
            "data": self.data,
            "caption": self.caption,
            "html": self.html,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TableBlock":
        table_data = data.get("data") or [["", ""], ["", ""]]
        rows = int(data.get("rows", len(table_data) or 2))
        cols = int(data.get("cols", len(table_data[0]) if table_data else 2))
        return cls(
            rows=rows,
            cols=cols,
            data=[[str(cell) for cell in row] for row in table_data],
            caption=data.get("caption", ""),
            html=data.get("html", ""),
        )


@dataclass
class ListBlock:
    """A bullet or numbered list."""

    block_type: str = "list"
    ordered: bool = False
    items_html: list[str] = field(default_factory=list)
    html: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.block_type,
            "ordered": self.ordered,
            "items_html": self.items_html,
            "html": self.html,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ListBlock":
        return cls(
            ordered=bool(data.get("ordered", False)),
            items_html=[str(item) for item in data.get("items_html", [])],
            html=data.get("html", ""),
        )


ContentBlock = ParagraphBlock | ImageBlock | TableBlock | ListBlock


def content_block_from_dict(data: dict) -> ContentBlock:
    block_type = data.get("type", "paragraph")
    if block_type == "image":
        return ImageBlock.from_dict(data)
    if block_type == "table":
        return TableBlock.from_dict(data)
    if block_type == "list":
        return ListBlock.from_dict(data)
    return ParagraphBlock.from_dict(data)


@dataclass
class SectionContent:
    """Structured content for one section."""

    section_id: str
    blocks: list[ContentBlock] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    @property
    def content_html(self) -> str:
        from app.services.content_blocks import blocks_to_editor_html

        return blocks_to_editor_html(self.blocks)

    def has_meaningful_content(self) -> bool:
        from app.services.content_blocks import blocks_have_meaningful_content

        return blocks_have_meaningful_content(self.blocks)

    @classmethod
    def from_dict(cls, data: dict) -> "SectionContent":
        if "blocks" in data:
            blocks = [content_block_from_dict(item) for item in data.get("blocks", [])]
            return cls(section_id=data["section_id"], blocks=blocks)

        from app.services.content_blocks import legacy_section_content_to_blocks

        blocks = legacy_section_content_to_blocks(
            data.get("content_html", ""),
            data.get("images", []),
            data.get("tables", []),
        )
        return cls(section_id=data["section_id"], blocks=blocks)


@dataclass
class Project:
    """A student paper project."""

    template_name: str = ""
    template_path: str = ""
    template_version: str = ""
    title: str = "未命名论文"
    author: str = ""
    student_id: str = ""
    major: str = ""
    supervisor: str = ""
    sections: dict[str, SectionContent] = field(default_factory=dict)
    file_path: Optional[str] = None

    def get_content(self, section_id: str) -> SectionContent:
        if section_id not in self.sections:
            self.sections[section_id] = SectionContent(section_id=section_id)
        return self.sections[section_id]

    def to_dict(self) -> dict:
        return {
            "template_name": self.template_name,
            "template_path": self.template_path,
            "template_version": self.template_version,
            "title": self.title,
            "author": self.author,
            "student_id": self.student_id,
            "major": self.major,
            "supervisor": self.supervisor,
            "sections": {key: value.to_dict() for key, value in self.sections.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(
            template_name=data.get("template_name", ""),
            template_path=data.get("template_path", ""),
            template_version=data.get("template_version", ""),
            title=data.get("title", "未命名论文"),
            author=data.get("author", ""),
            student_id=data.get("student_id", ""),
            major=data.get("major", ""),
            supervisor=data.get("supervisor", ""),
            sections={
                key: SectionContent.from_dict(value)
                for key, value in data.get("sections", {}).items()
            },
        )
