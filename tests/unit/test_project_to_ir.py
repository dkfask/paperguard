"""Tests for app.core.loaders.project_to_ir.

Covers the converter that turns a PyQt Project's single section into a
Sprint-1 Document IR (frozen dataclasses) for the live format checker.

TDD: 这些测试最初会 RED（找不到 import），实现后转 GREEN。
"""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
    Section,
)
from app.models.project import ParagraphBlock, Project, SectionContent


class TestEmptySection(unittest.TestCase):
    def test_empty_section_returns_empty_document(self):
        # Given a Project with one section that has no blocks
        project = Project(title="空论文", sections={
            "ch1": SectionContent(section_id="ch1", blocks=[]),
        })

        # When converting
        doc = _convert(project, "ch1")

        # Then the document has no paragraphs (sections 仍保留默认页设置)
        self.assertIsInstance(doc, Document)
        self.assertEqual(doc.paragraphs, ())
        # metadata still taken from project title
        self.assertEqual(doc.metadata.title, "空论文")


class TestSingleParagraph(unittest.TestCase):
    def test_single_paragraph_basic(self):
        # Given a project with one paragraph block
        project = Project(title="单段", sections={
            "ch1": SectionContent(section_id="ch1", blocks=[
                ParagraphBlock(html="<p>hello world</p>"),
            ]),
        })

        # When converting
        doc = _convert(project, "ch1")

        # Then the document has exactly one paragraph with the right text
        self.assertEqual(len(doc.paragraphs), 1)
        para = doc.paragraphs[0]
        self.assertIsInstance(para, Paragraph)
        self.assertEqual(para.text, "hello world")
        self.assertEqual(para.style_id, "Body Text")
        self.assertIsNone(para.level)


class TestMultiParagraphHeadings(unittest.TestCase):
    def test_multi_paragraph_with_heading_levels(self):
        # Given a project with one heading + two body paragraphs
        project = Project(title="多段", sections={
            "ch1": SectionContent(section_id="ch1", blocks=[
                ParagraphBlock(html="<h1>1 引言</h1>"),
                ParagraphBlock(html="<p>第一段内容。</p>"),
                ParagraphBlock(html="<h2>1.1 背景</h2>"),
                ParagraphBlock(html="<p>第二段内容。</p>"),
            ]),
        })

        # When converting
        doc = _convert(project, "ch1")

        # Then 4 paragraphs with level 1, None, 2, None
        self.assertEqual(len(doc.paragraphs), 4)
        self.assertEqual(doc.paragraphs[0].level, 1)
        self.assertEqual(doc.paragraphs[0].text, "1 引言")
        self.assertIsNone(doc.paragraphs[1].level)
        self.assertEqual(doc.paragraphs[1].text, "第一段内容。")
        self.assertEqual(doc.paragraphs[2].level, 2)
        self.assertEqual(doc.paragraphs[2].text, "1.1 背景")
        self.assertIsNone(doc.paragraphs[3].level)
        self.assertEqual(doc.paragraphs[3].text, "第二段内容。")


class TestFontExtraction(unittest.TestCase):
    def test_font_extraction_from_runs(self):
        # Given a paragraph with a run that has font-family 宋体
        html = '<p><span style="font-family: 宋体">正文</span></p>'
        project = Project(title="字体", sections={
            "ch1": SectionContent(section_id="ch1", blocks=[
                ParagraphBlock(html=html),
            ]),
        })

        # When converting
        doc = _convert(project, "ch1")

        # Then the paragraph has at least one run with font_eastasia = 宋体
        para = doc.paragraphs[0]
        self.assertGreaterEqual(len(para.runs), 1)
        first_run = para.runs[0]
        self.assertIsInstance(first_run, Run)
        self.assertEqual(first_run.font_eastasia, "宋体")
        self.assertEqual(first_run.text, "正文")

    def test_underline_extraction_from_runs(self):
        html = '<p><u>下划线</u></p>'
        project = Project(title="下划线", sections={
            "ch1": SectionContent(section_id="ch1", blocks=[
                ParagraphBlock(html=html),
            ]),
        })

        doc = _convert(project, "ch1")

        run = doc.paragraphs[0].runs[0]
        self.assertEqual(run.underline, "single")


class TestParagraphFormattingExtraction(unittest.TestCase):
    def test_alignment_extraction_from_paragraph_style(self):
        html = '<p style="text-align: justify;">正文</p>'
        project = Project(title="对齐", sections={
            "ch1": SectionContent(section_id="ch1", blocks=[
                ParagraphBlock(html=html),
            ]),
        })

        doc = _convert(project, "ch1")

        self.assertEqual(doc.paragraphs[0].alignment, "justify")


class TestUnknownSectionId(unittest.TestCase):
    def test_unknown_section_id_raises_valueerror(self):
        # Given a project that has only "ch1"
        project = Project(title="X", sections={
            "ch1": SectionContent(section_id="ch1"),
        })

        # When converting a non-existent section_id
        # Then ValueError is raised
        with self.assertRaises(ValueError):
            _convert(project, "nonexistent-section")


class TestFrozenAfterConversion(unittest.TestCase):
    def test_ir_dataclass_frozen_after_conversion(self):
        # Given a project with one paragraph
        project = Project(title="F", sections={
            "ch1": SectionContent(section_id="ch1", blocks=[
                ParagraphBlock(html="<p>x</p>"),
            ]),
        })

        # When converting
        doc = _convert(project, "ch1")

        # Then both the Document and its Paragraph are frozen
        with self.assertRaises(FrozenInstanceError):
            doc.metadata = DocumentMetadata()  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            doc.paragraphs[0].text = "mutated"  # type: ignore[misc]
        # And the sections tuple is immutable
        self.assertIsInstance(doc.sections, tuple)


# ---------------------------------------------------------------------------
# Helper that imports the converter under test.
# The `project_to_ir` symbol does not exist yet — tests will FAIL TO IMPORT
# (ModuleNotFoundError), which is the desired RED state.
# ---------------------------------------------------------------------------
def _convert(project: Project, section_id: str) -> Document:
    from app.core.loaders.project_to_ir import project_to_ir

    return project_to_ir(project, section_id)


if __name__ == "__main__":
    unittest.main()
