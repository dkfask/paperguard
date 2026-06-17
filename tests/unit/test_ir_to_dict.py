"""Tests for IR dataclass to_dict() — Sprint 2 prereq for jmespath consumption.

jmespath 只吃 dict / list，不能直接吃 frozen dataclass。Sprint 2 evaluator 在跑
``jmespath.compile(scope).search(doc.to_dict())`` 前必须把 IR 转成纯嵌套 dict。

每个 frozen dataclass 加 ``to_dict() -> dict`` 方法，字段名与原 dataclass attribute
一致（用 ``line_spacing`` 不用 ``lineSpacing``，与 IR v1.1 约定保持点分分组）。

TDD: 先写 6 个 RED 测试，再让 4 个 dataclass GREEN（每个加 ~10 LOC to_dict()）。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import jmespath

from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
    Section,
)


class TestRunToDict(unittest.TestCase):
    """Run.to_dict() 应导出全部 12 个字段。"""

    def test_run_to_dict_includes_all_fields(self):
        run = Run(
            text="正文",
            font_eastasia="宋体",
            font_ascii="Times New Roman",
            font_size_pt=12.0,
            bold=True,
            italic=False,
        )
        d = run.to_dict()
        self.assertEqual(d["text"], "正文")
        self.assertEqual(d["font_eastasia"], "宋体")
        self.assertEqual(d["font_ascii"], "Times New Roman")
        self.assertEqual(d["font_size_pt"], 12.0)
        self.assertTrue(d["bold"])
        self.assertFalse(d["italic"])
        # 默认字段也要存在（jmespath {var: "font_hansi"} 要拿到 None 而不是 KeyError）
        self.assertIn("font_hansi", d)
        self.assertIn("underline", d)
        self.assertIn("color_hex", d)


class TestParagraphToDict(unittest.TestCase):
    """Paragraph.to_dict() 必须把 runs 嵌套展开为 list[dict]，方便 jmespath runs[0]."""

    def test_paragraph_to_dict_nests_runs(self):
        run = Run(text="hi", font_eastasia="宋体", font_size_pt=12.0)
        para = Paragraph(
            style_id="Body Text",
            text="hi",
            level=None,
            runs=(run,),
            line_spacing=1.5,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.74,
        )
        d = para.to_dict()
        self.assertEqual(d["style_id"], "Body Text")
        self.assertEqual(d["line_spacing"], 1.5)
        self.assertEqual(d["first_line_indent_cm"], 0.74)
        self.assertIsInstance(d["runs"], list)
        self.assertEqual(len(d["runs"]), 1)
        # 嵌套 run 已是 dict（不能是 Run 对象，否则 jmespath 无法访问）
        self.assertIsInstance(d["runs"][0], dict)
        self.assertEqual(d["runs"][0]["text"], "hi")
        self.assertEqual(d["runs"][0]["font_eastasia"], "宋体")

    def test_paragraph_to_dict_includes_table_location(self):
        para = Paragraph(
            style_id="Table Text",
            text="cell",
            level=None,
            runs=(Run(text="cell"),),
            line_spacing=1.0,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.0,
            table_index=0,
            table_row_index=1,
            table_cell_index=2,
        )
        d = para.to_dict()
        self.assertEqual(d["table_index"], 0)
        self.assertEqual(d["table_row_index"], 1)
        self.assertEqual(d["table_cell_index"], 2)

    def test_paragraph_to_dict_includes_list_fields(self):
        para = Paragraph(
            style_id="List Number",
            text="item",
            level=None,
            runs=(Run(text="item"),),
            line_spacing=1.0,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.0,
            list_type="number",
            list_level=1,
        )
        d = para.to_dict()
        self.assertEqual(d["list_type"], "number")
        self.assertEqual(d["list_level"], 1)


class TestSectionToDict(unittest.TestCase):
    """Section.to_dict() 应导出全部 9 个 margin/page 字段。"""

    def test_section_to_dict_includes_margins(self):
        sec = Section(
            page_width_cm=21.0,
            page_height_cm=29.7,
            margin_top_cm=2.54,
            margin_bottom_cm=2.54,
            margin_left_cm=3.17,
            margin_right_cm=3.17,
            orientation="portrait",
        )
        d = sec.to_dict()
        self.assertEqual(d["page_width_cm"], 21.0)
        self.assertEqual(d["margin_top_cm"], 2.54)
        self.assertEqual(d["margin_left_cm"], 3.17)
        self.assertEqual(d["orientation"], "portrait")


class TestDocumentToDict(unittest.TestCase):
    """Document.to_dict() 必须把 sections / paragraphs / metadata 全部展开。"""

    def test_document_to_dict_includes_paragraphs_and_sections(self):
        para = Paragraph(
            style_id="Body Text", text="hi", level=None, runs=(),
            line_spacing=1.5, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.74,
        )
        sec = Section(page_width_cm=21.0, page_height_cm=29.7)
        meta = DocumentMetadata(title="示例", author="测试")
        doc = Document(sections=(sec,), paragraphs=(para,), metadata=meta)

        d = doc.to_dict()
        self.assertIsInstance(d["sections"], list)
        self.assertIsInstance(d["paragraphs"], list)
        self.assertEqual(len(d["paragraphs"]), 1)
        self.assertEqual(len(d["sections"]), 1)
        # metadata 嵌套展开
        self.assertEqual(d["metadata"]["title"], "示例")
        self.assertEqual(d["metadata"]["author"], "测试")
        # 嵌套类型必须是 dict（jmespath 要求）
        self.assertIsInstance(d["paragraphs"][0], dict)
        self.assertIsInstance(d["sections"][0], dict)
        self.assertIsInstance(d["metadata"], dict)

    def test_document_to_dict_includes_header_and_footer_paragraphs(self):
        header = Paragraph(
            style_id="Header", text="header", level=None, runs=(),
            line_spacing=1.0, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        footer = Paragraph(
            style_id="Footer", text="footer", level=None, runs=(),
            line_spacing=1.0, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        doc = Document(
            sections=(),
            paragraphs=(),
            metadata=DocumentMetadata(title="test"),
            header_paragraphs=(header,),
            footer_paragraphs=(footer,),
        )

        d = doc.to_dict()

        self.assertEqual(d["header_paragraphs"][0]["text"], "header")
        self.assertEqual(d["footer_paragraphs"][0]["text"], "footer")


class TestToDictIsJmespathSearchable(unittest.TestCase):
    """**集成验证**：to_dict() 的结果必须可被 jmespath filter（Sprint 2 evaluator 关键依赖）。

    这个测试是 Sprint 2 evaluator.evaluate_scope() 实现可行的前置条件。
    若失败，整个 YAML 引擎 scope filter 设计需重新评估。
    """

    def test_jmespath_can_filter_paragraphs_by_style(self):
        p1 = Paragraph(
            style_id="Body Text", text="body", level=None, runs=(),
            line_spacing=1.5, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.74,
        )
        p2 = Paragraph(
            style_id="Heading 1", text="title", level=1, runs=(),
            line_spacing=1.0, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        doc = Document(
            sections=(), paragraphs=(p1, p2),
            metadata=DocumentMetadata(title="test"),
        )
        d = doc.to_dict()
        filtered = jmespath.search("paragraphs[?style_id == 'Body Text']", d)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["style_id"], "Body Text")

    def test_jmespath_can_filter_runs_by_font(self):
        run1 = Run(text="a", font_eastasia="宋体", font_size_pt=12.0)
        run2 = Run(text="b", font_eastasia="黑体", font_size_pt=18.0)
        para = Paragraph(
            style_id="Mixed", text="ab", level=None, runs=(run1, run2),
            line_spacing=1.5, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        d = para.to_dict()
        filtered = jmespath.search("runs[?font_eastasia == '宋体']", d)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["text"], "a")


if __name__ == "__main__":
    unittest.main()
