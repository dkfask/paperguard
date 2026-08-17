"""Tests for app.core.reporters.terminal.

构造一个 sample Document（含 5 个 paragraph + 1 个 section），断言输出格式。

TDD: Task 1.7 写测试 RED，Task 1.8 写实现 GREEN。
"""

import unittest

from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
    Section,
)
from app.core.reporters.terminal import TerminalReporter


def _sample_document() -> Document:
    s = Section(
        page_width_cm=21.0, page_height_cm=29.7,
        margin_top_cm=2.54, margin_bottom_cm=2.54,
        margin_left_cm=3.17, margin_right_cm=3.17,
        header_distance_cm=1.5, footer_distance_cm=1.75,
        orientation="portrait",
    )

    paragraphs = (
        Paragraph(
            style_id="Heading 1", text="1 引言", level=1,
            runs=(Run(text="1 引言", font_eastasia="黑体", font_size_pt=18.0),),
            line_spacing=1.5, space_before_pt=24.0, space_after_pt=18.0,
            first_line_indent_cm=0.0,
        ),
        Paragraph(
            style_id="Body Text", text="本文研究的主要内容是...", level=None,
            runs=(Run(text="本文研究的主要内容是...", font_eastasia="宋体", font_size_pt=12.0),),
            line_spacing=1.5, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.74,
        ),
        Paragraph(
            style_id="Body Text", text="", level=None, runs=(),
            line_spacing=1.5, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        ),
        Paragraph(
            style_id="Heading 2", text="1.1 背景", level=2,
            runs=(Run(text="1.1 背景", font_eastasia="黑体", font_size_pt=15.0),),
            line_spacing=1.5, space_before_pt=18.0, space_after_pt=12.0,
            first_line_indent_cm=0.0,
        ),
        Paragraph(
            style_id="Body Text", text="随着...", level=None,
            runs=(Run(text="随着...", font_eastasia="宋体", font_size_pt=12.0),),
            line_spacing=1.5, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.74,
        ),
    )

    return Document(
        sections=(s,),
        paragraphs=paragraphs,
        metadata=DocumentMetadata(title="示例论文", author="张三"),
    )


class TestTerminalReporter(unittest.TestCase):
    def test_returns_string(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        self.assertIsInstance(out, str)

    def test_contains_document_header(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        self.assertIn("Document", out)

    def test_contains_section_count(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        self.assertIn("1 sections", out)

    def test_contains_paragraph_count(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        self.assertIn("5 paragraphs", out)

    def test_contains_a4_dimensions(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        self.assertIn("21.0", out)
        self.assertIn("29.7", out)

    def test_contains_first_paragraph_style_id(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        self.assertIn("Heading 1", out)

    def test_contains_first_paragraph_text(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        self.assertIn("1 引言", out)

    def test_contains_three_paragraphs_in_listing(self):
        r = TerminalReporter()
        out = r.report(_sample_document())
        # 至少应列出前 3 个 paragraph
        self.assertIn("1 引言", out)
        self.assertIn("本文研究", out)
        self.assertIn("1.1 背景", out)


if __name__ == "__main__":
    unittest.main()
