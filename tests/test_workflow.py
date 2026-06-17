from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from shutil import copyfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from app.controllers.export_ctrl import ExportController
from app.controllers.project_ctrl import ProjectController
from app.controllers.review_ctrl import ReviewController
from app.controllers.workspace_ctrl import WorkspaceController
from app.models.template import Template
from app.services.demo_seed_service import DemoSeedService
from app.services.format_validator import validate_project
from app.services.template_parser import load_template, save_template
from app.services.word_template_importer import import_word_template
from app.views.content_editor import ContentEditor
from app.views.dashboard_view import DashboardView
from app.views.main_window import MainWindow
from app.views.review_export_view import ReviewExportView
from app.views.template_editor import TemplateEditor
from app.views.template_workspace_view import TemplateWorkspaceView
from app.views.tree_panel import TreePanel


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn2Xl0AAAAASUVORK5CYII="
)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp_dir.name)
        self.template_path = self.workdir / "template.ptpl"
        template = Template(name="测试模板", version="2.0")
        save_template(template, self.template_path)
        self.template = load_template(self.template_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_image(self) -> Path:
        image_path = self.workdir / "tiny.png"
        image_path.write_bytes(PNG_1X1)
        return image_path

    def _fill_cover_metadata(self, controller: ProjectController):
        controller.set_metadata(
            title="测试论文",
            author="张三",
            student_id="20240001",
            major="计算机科学与技术",
            supervisor="李老师",
        )

    def _build_word_template_document(self, output_path: Path):
        document = Document()
        section = document.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Cm(29.7)
        section.page_height = Cm(21.0)
        section.top_margin = Cm(2.8)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3.0)
        section.right_margin = Cm(2.2)
        section.header_distance = Cm(1.4)
        section.footer_distance = Cm(1.6)
        section.header.paragraphs[0].text = "学院本科毕业论文"
        section.footer.paragraphs[0].text = "第 1 页"

        title_style = document.styles["Title"]
        title_style.font.name = "Times New Roman"
        title_style.font.size = Pt(22)
        title_style.font.bold = True
        title_style.font.color.rgb = RGBColor.from_string("222222")
        self._set_east_asia_font(title_style, "黑体")

        heading_1 = document.styles["Heading 1"]
        heading_1.font.name = "Arial"
        heading_1.font.size = Pt(16)
        heading_1.font.bold = True
        heading_1.font.color.rgb = RGBColor.from_string("112233")
        heading_1.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        heading_1.paragraph_format.line_spacing = 1.8
        heading_1.paragraph_format.space_before = Pt(12)
        heading_1.paragraph_format.space_after = Pt(6)
        heading_1.paragraph_format.first_line_indent = Cm(0.74)
        self._set_east_asia_font(heading_1, "黑体")

        body_style = document.styles["Normal"]
        body_style.font.name = "Calibri"
        body_style.font.size = Pt(12)
        body_style.font.italic = True
        body_style.font.color.rgb = RGBColor.from_string("445566")
        body_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        body_style.paragraph_format.line_spacing = 1.5
        body_style.paragraph_format.first_line_indent = Cm(0.74)
        self._set_east_asia_font(body_style, "宋体")

        document.save(output_path)

    def _set_east_asia_font(self, style, font_name: str):
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), font_name)

    def _select_style_row(self, editor: TemplateEditor, style_id: str) -> int:
        for row in range(editor.style_list.count()):
            if editor.style_list.item(row).text() == style_id:
                editor.style_list.setCurrentRow(row)
                return row
        self.fail(f"未找到样式: {style_id}")

    def test_project_restore_keeps_content_when_template_is_reloaded(self):
        controller = ProjectController()
        controller.new_from_template(self.template)
        controller.update_content("abstract_cn", "<p>已有内容</p>")
        project_path = self.workdir / "project.pprj"
        controller.save_project(project_path)

        window = MainWindow()
        self.addCleanup(window.close)
        window.project_ctrl.open_project(project_path)
        restored = window._restore_template_for_project()

        self.assertTrue(restored)
        self.assertIn("已有内容", window.project_ctrl.project.get_content("abstract_cn").content_html)
        self.assertEqual(window.nav_list.count(), len(WorkspaceController.WORKSPACES))

    def test_docx_generator_does_not_repeat_subsections(self):
        controller = ProjectController()
        project = controller.new_from_template(self.template)
        self._fill_cover_metadata(controller)
        controller.update_content("chapter_1", "<p>章节内容</p>")
        controller.update_content("ch1_sec1", "<p>小节内容</p>")

        output = self.workdir / "paper.docx"
        ExportController.export_to_docx(self.template, project, output)
        doc = Document(output)
        texts = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]

        self.assertEqual(texts.count("1.1 研究背景"), 1)

    def test_save_reopen_and_export_preserves_image_and_table_blocks(self):
        controller = ProjectController()
        project = controller.new_from_template(self.template)
        self._fill_cover_metadata(controller)
        image_path = self._create_image()
        html = (
            f'<p>正文前</p><p><img src="{image_path.as_uri()}" style="max-width: 500px;" /></p>'
            '<table border="1"><tr><td>A</td><td>B</td></tr></table><p>正文后</p>'
        )
        controller.update_content("chapter_1", html)
        project_path = self.workdir / "project.pprj"
        controller.save_project(project_path)

        reopened = ProjectController()
        reopened.open_project(project_path)
        blocks = reopened.project.get_content("chapter_1").blocks
        self.assertTrue(any(getattr(block, "block_type", "") == "image" for block in blocks))
        self.assertTrue(any(getattr(block, "block_type", "") == "table" for block in blocks))

        output = self.workdir / "rich.docx"
        ExportController.export_to_docx(self.template, reopened.project, output)
        doc = Document(output)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertEqual(len(doc.tables), 1)

    def test_docx_export_preserves_basic_inline_rich_text(self):
        controller = ProjectController()
        project = controller.new_from_template(self.template)
        self._fill_cover_metadata(controller)
        html = (
            '<p align="center">'
            '<span style="font-family:\'Arial\'; color:#cc0000; font-weight:600; font-size:18pt; text-decoration: underline;">粗体</span>'
            '<span style="font-style:italic;">斜体</span>'
            "普通"
            "</p>"
        )
        controller.update_content("chapter_1", html)

        output = self.workdir / "styled.docx"
        ExportController.export_to_docx(self.template, project, output)
        doc = Document(output)

        target = next(para for para in doc.paragraphs if "粗体斜体普通" in para.text)
        self.assertEqual(target.alignment, WD_ALIGN_PARAGRAPH.CENTER)
        self.assertTrue(target.runs[0].bold)
        self.assertTrue(target.runs[0].underline)
        self.assertEqual(target.runs[0].font.size.pt, 18.0)
        self.assertEqual(target.runs[0].font.name, "Arial")
        self.assertEqual(str(target.runs[0].font.color.rgb), "CC0000")
        self.assertTrue(target.runs[1].italic)

    def test_docx_export_supports_lists_and_blockquotes(self):
        controller = ProjectController()
        project = controller.new_from_template(self.template)
        self._fill_cover_metadata(controller)
        html = (
            '<ul><li><span style="color:#008000;">要点一</span></li><li>要点二</li></ul>'
            "<blockquote><p>引用段落</p></blockquote>"
            "<ol><li>步骤一</li><li>步骤二</li></ol>"
        )
        controller.update_content("chapter_1", html)

        output = self.workdir / "lists.docx"
        ExportController.export_to_docx(self.template, project, output)
        doc = Document(output)

        bullet = next(para for para in doc.paragraphs if para.text.strip() == "要点一")
        quote = next(para for para in doc.paragraphs if para.text.strip() == "引用段落")
        numbered = next(para for para in doc.paragraphs if para.text.strip() == "步骤一")

        self.assertEqual(bullet.style.name, "List Bullet")
        self.assertEqual(str(bullet.runs[0].font.color.rgb), "008000")
        self.assertGreater(float(quote.paragraph_format.left_indent.cm), 0.0)
        self.assertEqual(numbered.style.name, "List Number")

    def test_required_section_empty_is_error_and_blocks_export(self):
        controller = ProjectController()
        project = controller.new_from_template(self.template)
        self._fill_cover_metadata(controller)

        issues = validate_project(project, self.template)
        self.assertTrue(
            any(
                issue.section_id == "chapter_1"
                and issue.severity == "error"
                and issue.code == "section.required.empty"
                for issue in issues
            )
        )

        window = MainWindow()
        self.addCleanup(window.close)
        window._apply_template(self.template, reset_project=False)
        window.project_ctrl.project = project

        with patch("app.views.main_window.QMessageBox.warning") as warning_box, patch(
            "app.views.main_window.QFileDialog.getSaveFileName"
        ) as save_dialog:
            window._export_docx()

        warning_box.assert_called()
        save_dialog.assert_not_called()

    def test_validator_skips_toc_and_uses_cover_metadata_rules(self):
        controller = ProjectController()
        project = controller.new_from_template(self.template)
        issues = validate_project(project, self.template)

        self.assertFalse(any(issue.section_id == "toc" for issue in issues))
        self.assertTrue(any(issue.target == "title" and issue.severity == "error" for issue in issues))

    def test_demo_seed_service_creates_demo_assets(self):
        service = DemoSeedService(base_dir=self.workdir / "demo")
        assets = service.ensure_demo_assets()

        self.assertTrue(Path(assets["template_path"]).exists())
        self.assertTrue(Path(assets["project_path"]).exists())
        self.assertTrue(Path(assets["reference_docx_path"]).exists())

    def test_template_editor_initial_selection_is_synced(self):
        editor = TemplateEditor(template=Template(name="编辑测试"))
        self.addCleanup(editor.close)

        self.assertEqual(editor.style_list.currentRow(), 0)
        self.assertEqual(
            editor.style_id_label.text(),
            editor.style_list.item(editor.style_list.currentRow()).text(),
        )
        self.assertTrue(editor.struct_label.text())
        self.assertIsNotNone(editor._current_section_id)

    def test_template_editor_persists_multiple_style_changes_and_paper_size(self):
        editor = TemplateEditor(template=Template(name="编辑测试"))
        self.addCleanup(editor.close)

        self._select_style_row(editor, "cover_title")
        editor.font_bold.setChecked(True)

        self._select_style_row(editor, "heading_1")
        editor.font_italic.setChecked(True)

        editor.page_size.setCurrentText("A3")
        editor._save_template()

        self.assertTrue(editor.template.format_config.styles["cover_title"].font.bold)
        self.assertTrue(editor.template.format_config.styles["heading_1"].font.italic)
        self.assertEqual(editor.template.format_config.page_setup.width_cm, 29.7)
        self.assertEqual(editor.template.format_config.page_setup.height_cm, 42.0)

    def test_template_editor_ignores_deleted_structure_items(self):
        editor = TemplateEditor(template=Template(name="缂栬緫娴嬭瘯"))
        self.addCleanup(editor.close)

        stale_item = editor.struct_list.item(0)
        editor.struct_list.clear()

        editor._on_structure_selected(None, stale_item)

        self.assertIsNone(editor._current_section_id)

    def test_content_editor_toggle_format_without_selection_updates_current_char_format(self):
        editor = ContentEditor()
        self.addCleanup(editor.close)

        editor._toggle_format("bold")
        editor._toggle_format("italic")
        editor._toggle_format("underline")

        current_format = editor.editor.currentCharFormat()
        self.assertEqual(current_format.fontWeight(), QFont.Bold)
        self.assertTrue(current_format.fontItalic())
        self.assertTrue(current_format.fontUnderline())

    def test_dashboard_role_badge_can_switch_between_teacher_and_student(self):
        dashboard = DashboardView()
        self.addCleanup(dashboard.close)

        dashboard.set_role("teacher")
        self.assertIn("教师流程", dashboard.role_badge.text())

        dashboard.set_role("student")
        self.assertIn("学生流程", dashboard.role_badge.text())

    def test_review_export_view_shows_last_export_result_summary(self):
        view = ReviewExportView()
        self.addCleanup(view.close)
        summary = ReviewController().build_summary(ProjectController().project, None)

        view.set_context(
            template_name="测试模板",
            project_title="测试项目",
            summary=summary,
            export_filename="测试项目.docx",
            last_export_path=str(self.workdir / "result.docx"),
        )

        self.assertIn("最近导出", view.last_export_label.text())
        self.assertIn("导出已完成", view.result_title.text())

    def test_tree_panel_can_select_top_level_section(self):
        panel = TreePanel()
        self.addCleanup(panel.close)
        panel.load_template(self.template, {})

        panel.select_section("chapter_1")

        current = panel.tree.currentItem()
        self.assertIsNotNone(current)
        self.assertEqual(current.data(0, Qt.UserRole), "chapter_1")

    def test_word_template_import_reads_styles_and_page_setup(self):
        word_path = self.workdir / "import-source.docx"
        self._build_word_template_document(word_path)

        template = import_word_template(word_path)
        page = template.format_config.page_setup
        heading_1 = template.format_config.styles["heading_1"]
        body = template.format_config.styles["body_text"]
        cover = template.format_config.styles["cover_title"]

        self.assertEqual(template.name, "import-source")
        self.assertIsNone(template.file_path)
        self.assertEqual(page.orientation, "landscape")
        self.assertAlmostEqual(page.width_cm, 29.7, places=1)
        self.assertAlmostEqual(page.height_cm, 21.0, places=1)
        self.assertAlmostEqual(page.margin_top_cm, 2.8, places=1)
        self.assertEqual(template.format_config.header_text, "学院本科毕业论文")
        self.assertEqual(template.format_config.footer_text, "第 1 页")
        self.assertEqual(heading_1.font.name_en, "Arial")
        self.assertEqual(heading_1.font.name_cn, "黑体")
        self.assertEqual(heading_1.font.color_hex, "112233")
        self.assertEqual(heading_1.paragraph.alignment, "center")
        self.assertAlmostEqual(heading_1.paragraph.line_spacing, 1.8, places=1)
        self.assertEqual(body.font.name_en, "Calibri")
        self.assertEqual(body.font.name_cn, "宋体")
        self.assertTrue(body.font.italic)
        self.assertEqual(cover.font.name_cn, "黑体")

    def test_word_template_import_builds_structure_from_heading_paragraphs(self):
        word_path = self.workdir / "structured-template.docx"
        document = Document()
        document.add_paragraph("中文摘要", style="Heading 1")
        document.add_paragraph("第1章 绪论", style="Heading 1")
        document.add_paragraph("1.1 研究背景", style="Heading 2")
        document.add_paragraph("1.1.1 研究范围", style="Heading 3")
        document.add_paragraph("参考文献", style="Heading 1")
        document.add_paragraph("致谢", style="Heading 1")
        document.save(word_path)

        template = import_word_template(word_path)

        self.assertEqual([node.label for node in template.structure], ["中文摘要", "第1章 绪论", "参考文献", "致谢"])
        self.assertEqual([node.type for node in template.structure], ["abstract", "chapter", "references", "acknowledgements"])
        chapter = template.structure[1]
        self.assertEqual(chapter.style_id, "heading_1")
        self.assertEqual([node.label for node in chapter.subsections], ["1.1 研究背景"])
        self.assertEqual(chapter.subsections[0].style_id, "heading_2")
        self.assertEqual([node.label for node in chapter.subsections[0].subsections], ["1.1.1 研究范围"])
        self.assertEqual(chapter.subsections[0].subsections[0].style_id, "heading_3")

    def test_word_template_import_supports_dotx(self):
        docx_path = self.workdir / "import-source.docx"
        dotx_path = self.workdir / "import-source.dotx"
        self._build_word_template_document(docx_path)
        copyfile(docx_path, dotx_path)

        template = import_word_template(dotx_path)

        self.assertEqual(template.name, "import-source")
        self.assertEqual(template.format_config.page_setup.orientation, "landscape")

    def test_word_template_import_prefers_specific_body_style_candidates(self):
        word_path = self.workdir / "body-style-priority.docx"
        document = Document()
        normal_style = document.styles["Normal"]
        normal_style.font.size = Pt(12)
        normal_style.font.name = "Calibri"
        self._set_east_asia_font(normal_style, "宋体")

        body_style = document.styles.add_style("Body Text First Indent", WD_STYLE_TYPE.PARAGRAPH)
        body_style.font.size = Pt(10.5)
        body_style.font.name = "Cambria"
        body_style.font.italic = True
        body_style.paragraph_format.line_spacing = 2.0
        self._set_east_asia_font(body_style, "仿宋")
        document.save(word_path)

        template = import_word_template(word_path)
        body = template.format_config.styles["body_text"]

        self.assertEqual(body.font.name_cn, "仿宋")
        self.assertEqual(body.font.name_en, "Cambria")
        self.assertEqual(body.font.size_pt, 10.5)
        self.assertTrue(body.font.italic)
        self.assertEqual(body.paragraph.line_spacing, 2.0)

    def test_main_window_can_import_word_template_as_draft(self):
        word_path = self.workdir / "teacher-template.docx"
        self._build_word_template_document(word_path)

        window = MainWindow()
        self.addCleanup(window.close)

        with patch("app.views.main_window.QMessageBox.information") as info_box, patch(
            "app.views.main_window.QMessageBox.critical"
        ) as error_box:
            window._import_word_template(str(word_path))

        error_box.assert_not_called()
        info_box.assert_called_once()
        self.assertIsNotNone(window.project_ctrl.template)
        self.assertEqual(window.project_ctrl.template.name, "teacher-template")
        self.assertEqual(window.current_template_path, "")
        self.assertTrue(window.is_dirty)

    def test_template_workspace_preview_renders_imported_styles(self):
        word_path = self.workdir / "preview-template.docx"
        self._build_word_template_document(word_path)
        template = import_word_template(word_path)

        view = TemplateWorkspaceView()
        self.addCleanup(view.close)
        view.load_template(template)

        sample_html = view._render_sample_preview()
        rules_html = view._render_rules_summary()

        self.assertIn("font-size: 16pt", sample_html)
        self.assertIn("font-family: '宋体', 'Calibri'", sample_html)
        self.assertIn("text-indent: 0.74cm", sample_html)
        self.assertIn("摘要预览", rules_html)


if __name__ == "__main__":
    unittest.main()
