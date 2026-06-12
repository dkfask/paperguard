"""Project → IR converter (Sprint 1.5 Must-fix #2).

把 PyQt ``Project`` 模型里某个 section 的 ``ContentBlock[]`` 转成 Sprint 1 的
不可变 ``Document`` IR，供实时约束引擎（``LiveFormatChecker``）消费。

设计要点：
- **零 PyQt 依赖**：只 import ``app.models`` 的 dataclass。
- **只转 ParagraphBlock**：图片 / 表格 / 列表在 MVP 阶段不进 IR（V1 再加）。
- **heading 推断**：从 block 的 leading 标签读 ``<h1>`` ... ``<h6>`` 决定
  ``style_id`` 和 ``level``；其他统一为 ``"Body Text"`` / ``level=None``。
- **字体提取**：复用 ``app.services.content_blocks.parse_paragraph_html``
  解 span/font 标签的 CSS，把 ``font_name`` 映射到 IR 的 ``font_eastasia``。
- **unknown section 抛 ValueError**：让上层尽早 fail-fast。
"""

from __future__ import annotations

import re
from typing import Iterable

from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
    Section,
)
from app.models.project import (
    ParagraphBlock,
    Project,
    SectionContent,
)
from app.services.content_blocks import (
    RichTextRun,
    clean_text,
    parse_paragraph_html,
)


_HEADING_TAG_PATTERN = re.compile(r"^\s*<h([1-6])\b", re.IGNORECASE)

_HEADING_TAG_TO_LEVEL: dict[int, tuple[str, int]] = {
    1: ("Heading 1", 1),
    2: ("Heading 2", 2),
    3: ("Heading 3", 3),
    4: ("Heading 4", 4),
    5: ("Heading 5", 5),
    6: ("Heading 6", 6),
}


def _detect_heading(block_html: str) -> tuple[str, int | None]:
    """从 block HTML 的 leading 标签推断 (style_id, level)。"""
    match = _HEADING_TAG_PATTERN.match(block_html or "")
    if match:
        return _HEADING_TAG_TO_LEVEL[int(match.group(1))]
    return ("Body Text", None)


def _convert_run(rich_run: RichTextRun) -> Run:
    """``RichTextRun``（PyQt 编辑器的中间态）→ ``Run`` IR。"""
    return Run(
        text=rich_run.text,
        font_eastasia=rich_run.font_name,
        font_ascii=rich_run.font_name,
        font_size_pt=rich_run.font_size_pt,
        bold=rich_run.bold,
        italic=rich_run.italic,
        underline="single" if rich_run.underline else None,
        color_hex=rich_run.color_hex,
    )


def _convert_paragraph_block(block: ParagraphBlock) -> Paragraph:
    style_id, level = _detect_heading(block.html)
    parsed = parse_paragraph_html(block.html)
    runs = tuple(_convert_run(r) for r in parsed.runs)
    text = clean_text(block.html)
    return Paragraph(
        style_id=style_id,
        text=text,
        level=level,
        runs=runs,
        # 段落级属性从 HTML 不易精确读，MVP 用安全默认值（避免误报）
        line_spacing=1.0,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.0,
        alignment=parsed.alignment,
    )


def _iter_paragraph_blocks(blocks: Iterable) -> Iterable[ParagraphBlock]:
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            yield block


def project_to_ir(project: Project, section_id: str) -> Document:
    """把 ``Project`` 中指定 section 转成不可变 ``Document`` IR。

    Args:
        project: PyQt 论文项目模型。
        section_id: 要转换的 section key。

    Returns:
        不可变 ``Document``，含元数据 + 一组 ``Paragraph``（仅 text blocks）。

    Raises:
        ValueError: ``section_id`` 不在 ``project.sections`` 中。
    """
    if section_id not in project.sections:
        raise ValueError(
            f"未知的 section_id: {section_id!r}（project 共 "
            f"{len(project.sections)} 个 sections）"
        )

    section: SectionContent = project.sections[section_id]
    paragraphs = tuple(
        _convert_paragraph_block(block) for block in _iter_paragraph_blocks(section.blocks)
    )

    metadata = DocumentMetadata(
        title=project.title,
        author=project.author,
    )

    return Document(
        sections=(Section(),),
        paragraphs=paragraphs,
        metadata=metadata,
    )


__all__ = ["project_to_ir"]
