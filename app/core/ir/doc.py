"""Document + DocumentMetadata — docx 整体的不可变表示。

MVP 阶段 DocumentMetadata 只保留最常用的元数据。后续 V1 加 language、settings 等。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ir.paragraph import Paragraph
from app.core.ir.section import Section


@dataclass(frozen=True)
class DocumentMetadata:
    """文档元数据。MVP 最小集。"""
    title: str = ""
    author: str = ""
    created: str = ""           # ISO 8601 字符串
    modified: str = ""
    language: str = "zh-CN"     # BCP 47


@dataclass(frozen=True)
class Document:
    """整个 docx 文档的不可变 IR。"""
    sections: tuple[Section, ...]
    paragraphs: tuple[Paragraph, ...]
    metadata: DocumentMetadata
