"""Tests for app.core.ir dataclasses.

Covers: Run / Section / Paragraph / DocumentMetadata / Document / Finding / Location / Fix / Severity.

TDD: 这些测试最初会 RED（找不到 import），Task 1.4 实现后转 GREEN。
"""

import unittest
from dataclasses import FrozenInstanceError

from app.core.ir import (
    Document,
    DocumentMetadata,
    Finding,
    Fix,
    Location,
    Paragraph,
    Run,
    Section,
    Severity,
)


class TestRun(unittest.TestCase):
    def test_minimal_construction(self):
        r = Run(text="hello")
        self.assertEqual(r.text, "hello")
        self.assertIsNone(r.font_eastasia)
        self.assertIsNone(r.font_ascii)
        self.assertIsNone(r.font_size_pt)
        self.assertFalse(r.bold)
        self.assertFalse(r.italic)

    def test_full_construction(self):
        r = Run(
            text="正文",
            font_eastasia="宋体",
            font_ascii="Times New Roman",
            font_size_pt=12.0,
            bold=False,
            italic=False,
        )
        self.assertEqual(r.text, "正文")
        self.assertEqual(r.font_eastasia, "宋体")
        self.assertEqual(r.font_ascii, "Times New Roman")
        self.assertEqual(r.font_size_pt, 12.0)

    def test_frozen(self):
        r = Run(text="x")
        with self.assertRaises(FrozenInstanceError):
            r.text = "y"  # type: ignore[misc]

    def test_equality(self):
        a = Run(text="x", font_eastasia="宋体", font_size_pt=12.0)
        b = Run(text="x", font_eastasia="宋体", font_size_pt=12.0)
        c = Run(text="x", font_eastasia="黑体", font_size_pt=12.0)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class TestSection(unittest.TestCase):
    def test_default_values(self):
        s = Section()
        self.assertEqual(s.page_width_cm, 0.0)
        self.assertEqual(s.page_height_cm, 0.0)
        self.assertEqual(s.orientation, "portrait")

    def test_a4_construction(self):
        s = Section(
            page_width_cm=21.0,
            page_height_cm=29.7,
            margin_top_cm=2.54,
            margin_bottom_cm=2.54,
            margin_left_cm=3.17,
            margin_right_cm=3.17,
            header_distance_cm=1.5,
            footer_distance_cm=1.75,
            orientation="portrait",
        )
        self.assertEqual(s.page_width_cm, 21.0)
        self.assertEqual(s.margin_left_cm, 3.17)

    def test_frozen(self):
        s = Section(page_width_cm=21.0)
        with self.assertRaises(FrozenInstanceError):
            s.page_width_cm = 22.0  # type: ignore[misc]


class TestParagraph(unittest.TestCase):
    def _sample_paragraph(self) -> Paragraph:
        return Paragraph(
            style_id="Body Text",
            text="本文研究的主要内容...",
            level=None,
            runs=(
                Run(text="本文研究的主要内容...", font_eastasia="宋体", font_size_pt=12.0),
            ),
            line_spacing=1.5,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.74,
        )

    def test_construction(self):
        p = self._sample_paragraph()
        self.assertEqual(p.style_id, "Body Text")
        self.assertEqual(p.line_spacing, 1.5)
        self.assertEqual(p.first_line_indent_cm, 0.74)
        self.assertEqual(len(p.runs), 1)
        self.assertEqual(p.runs[0].font_eastasia, "宋体")

    def test_runs_is_tuple(self):
        p = self._sample_paragraph()
        self.assertIsInstance(p.runs, tuple)

    def test_frozen(self):
        p = self._sample_paragraph()
        with self.assertRaises(FrozenInstanceError):
            p.style_id = "Heading 1"  # type: ignore[misc]

    def test_level_for_heading(self):
        p = Paragraph(
            style_id="Heading 1",
            text="1 引言",
            level=1,
            runs=(),
            line_spacing=1.5,
            space_before_pt=0.0,
            space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        self.assertEqual(p.level, 1)

    def test_table_location_fields_default_to_none(self):
        p = self._sample_paragraph()
        self.assertIsNone(p.table_index)
        self.assertIsNone(p.table_row_index)
        self.assertIsNone(p.table_cell_index)

    def test_table_location_fields_are_frozen(self):
        p = Paragraph(
            style_id="Body Text",
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
        self.assertEqual(p.table_index, 0)
        self.assertEqual(p.table_row_index, 1)
        self.assertEqual(p.table_cell_index, 2)
        with self.assertRaises(FrozenInstanceError):
            p.table_index = 1  # type: ignore[misc]

    def test_list_fields_default_to_none(self):
        p = self._sample_paragraph()
        self.assertIsNone(p.list_type)
        self.assertIsNone(p.list_level)

    def test_list_fields_are_frozen(self):
        p = Paragraph(
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
        self.assertEqual(p.list_type, "number")
        self.assertEqual(p.list_level, 1)
        with self.assertRaises(FrozenInstanceError):
            p.list_type = "bullet"  # type: ignore[misc]


class TestDocument(unittest.TestCase):
    def test_construction(self):
        s = Section(page_width_cm=21.0, page_height_cm=29.7)
        p = Paragraph(
            style_id="Body Text", text="x", level=None, runs=(),
            line_spacing=1.5, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        meta = DocumentMetadata(title="示例论文", author="张三")
        doc = Document(sections=(s,), paragraphs=(p,), metadata=meta)
        self.assertEqual(len(doc.sections), 1)
        self.assertEqual(len(doc.paragraphs), 1)
        self.assertEqual(doc.metadata.title, "示例论文")

    def test_sections_and_paragraphs_are_tuples(self):
        doc = Document(
            sections=(),
            paragraphs=(),
            metadata=DocumentMetadata(),
        )
        self.assertIsInstance(doc.sections, tuple)
        self.assertIsInstance(doc.paragraphs, tuple)

    def test_header_and_footer_paragraphs_are_separate_tuples(self):
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
            metadata=DocumentMetadata(),
            header_paragraphs=(header,),
            footer_paragraphs=(footer,),
        )

        self.assertIsInstance(doc.header_paragraphs, tuple)
        self.assertIsInstance(doc.footer_paragraphs, tuple)
        self.assertEqual(doc.header_paragraphs[0].text, "header")
        self.assertEqual(doc.footer_paragraphs[0].text, "footer")


class TestFinding(unittest.TestCase):
    def test_construction_minimal(self):
        f = Finding(
            rule_id="BODY-LINE-SPACING",
            severity=Severity.ERROR,
            message="正文行距必须为 1.5 倍",
            location=Location(),
        )
        self.assertEqual(f.rule_id, "BODY-LINE-SPACING")
        self.assertEqual(f.severity, Severity.ERROR)
        self.assertIsNone(f.fix)

    def test_construction_with_fix(self):
        fix = Fix(action="set", target="paragraph.spacing.line", value="360")
        f = Finding(
            rule_id="BODY-LINE-SPACING",
            severity=Severity.ERROR,
            message="正文行距必须为 1.5 倍",
            location=Location(paragraph_index=3, text_snippet="本文研究..."),
            actual="1.0",
            expected="1.5",
            fix=fix,
        )
        self.assertEqual(f.fix.action, "set")
        self.assertEqual(f.fix.target, "paragraph.spacing.line")
        self.assertEqual(f.fix.value, "360")
        self.assertEqual(f.actual, "1.0")
        self.assertEqual(f.expected, "1.5")

    def test_frozen(self):
        f = Finding(
            rule_id="X", severity=Severity.WARNING, message="y", location=Location()
        )
        with self.assertRaises(FrozenInstanceError):
            f.message = "z"  # type: ignore[misc]


class TestSeverity(unittest.TestCase):
    def test_three_levels(self):
        self.assertEqual(Severity.ERROR.value, "error")
        self.assertEqual(Severity.WARNING.value, "warning")
        self.assertEqual(Severity.INFO.value, "info")


class TestLocation(unittest.TestCase):
    def test_default_values(self):
        loc = Location()
        self.assertIsNone(loc.section)
        self.assertIsNone(loc.paragraph_index)
        self.assertIsNone(loc.text_snippet)


if __name__ == "__main__":
    unittest.main()
