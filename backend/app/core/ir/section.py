"""Section IR — docx 中一个 w:sectPr 节的不可变表示。

包含页面尺寸、页边距、方向。一个 docx 可以有多个节（不同章节用不同页面设置）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    """一个节（页面设置 + 边距）。"""
    page_width_cm: float = 0.0
    page_height_cm: float = 0.0
    margin_top_cm: float = 0.0
    margin_bottom_cm: float = 0.0
    margin_left_cm: float = 0.0
    margin_right_cm: float = 0.0
    header_distance_cm: float = 0.0
    footer_distance_cm: float = 0.0
    orientation: str = "portrait"   # "portrait" / "landscape"

    def to_dict(self) -> dict:
        """Sprint 2 evaluator 用 — jmespath 消费。"""
        return {
            "page_width_cm": self.page_width_cm,
            "page_height_cm": self.page_height_cm,
            "margin_top_cm": self.margin_top_cm,
            "margin_bottom_cm": self.margin_bottom_cm,
            "margin_left_cm": self.margin_left_cm,
            "margin_right_cm": self.margin_right_cm,
            "header_distance_cm": self.header_distance_cm,
            "footer_distance_cm": self.footer_distance_cm,
            "orientation": self.orientation,
        }

