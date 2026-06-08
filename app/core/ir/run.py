"""Run IR — docx 中一个 w:r 节点的不可变表示。

字段命名遵循 v1.1 修订的"点分属性名"约定（`font.eastAsia` 等），
序列化形式与 OOXML 原始 key 对齐。

详细字段含义参考：
- https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.runproperties
- https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.runfonts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Run:
    """一个文本片段（含其格式属性）。"""
    text: str
    font_eastasia: str | None = None   # 中文字体 (w:rFonts w:eastAsia)
    font_ascii: str | None = None      # 西文字体 (w:rFonts w:ascii)
    font_hansi: str | None = None      # 半欧亚字体 (w:rFonts w:hAnsi)
    font_cs: str | None = None         # 复杂脚本字体
    font_size_pt: float | None = None  # 字号（OOXML 存半磅，解析时除 2）
    bold: bool = False
    italic: bool = False
    underline: str | None = None       # 'single' / 'double' / None
    color_hex: str | None = None       # 6 位 hex 不带 '#'，如 "000000"
    highlight: str | None = None
    vert_align: str | None = None      # 'superscript' / 'subscript'

    def to_dict(self) -> dict:
        """Sprint 2 evaluator 用 — jmespath 只吃 dict，需把 frozen dataclass 展开。"""
        return {
            "text": self.text,
            "font_eastasia": self.font_eastasia,
            "font_ascii": self.font_ascii,
            "font_hansi": self.font_hansi,
            "font_cs": self.font_cs,
            "font_size_pt": self.font_size_pt,
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "color_hex": self.color_hex,
            "highlight": self.highlight,
            "vert_align": self.vert_align,
        }

