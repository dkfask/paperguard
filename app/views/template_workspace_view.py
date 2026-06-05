"""Workspace for teacher-facing template management."""

from __future__ import annotations

import html
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.models.format_config import StyleConfig
from app.models.template import Template
from app.services.recent_items_service import RecentItem
from app.views.template_editor import TemplateEditor


class TemplateWorkspaceView(QWidget):
    create_template_requested = pyqtSignal()
    import_word_template_requested = pyqtSignal()
    open_template_requested = pyqtSignal(str)
    publish_template_requested = pyqtSignal()
    apply_template_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_template: Template | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        left = QVBoxLayout()
        title = QLabel("模板列表")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        left.addWidget(title)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部模板", "内置模板", "最近使用"])
        left.addWidget(self.filter_combo)

        self.template_list = QListWidget()
        self.template_list.itemDoubleClicked.connect(
            lambda item: self.open_template_requested.emit(item.data(Qt.UserRole))
        )
        left.addWidget(self.template_list, 1)

        btn_row = QHBoxLayout()
        btn_create = QPushButton("新建模板")
        btn_create.clicked.connect(self.create_template_requested.emit)
        btn_row.addWidget(btn_create)

        btn_import = QPushButton("导入 Word 模板")
        btn_import.clicked.connect(self.import_word_template_requested.emit)
        btn_row.addWidget(btn_import)
        left.addLayout(btn_row)

        btn_row_2 = QHBoxLayout()
        btn_open = QPushButton("打开模板")
        btn_open.clicked.connect(self._emit_selected_template)
        btn_row_2.addWidget(btn_open)
        left.addLayout(btn_row_2)
        layout.addLayout(left, 1)

        center = QVBoxLayout()
        self.editor = TemplateEditor(embedded=True)
        self.editor.template_saved.connect(self._on_template_applied)
        center.addWidget(self.editor)

        action_row = QHBoxLayout()
        self.btn_apply = QPushButton("应用模板修改")
        self.btn_apply.clicked.connect(self.editor._save_template)
        action_row.addWidget(self.btn_apply)
        self.btn_publish = QPushButton("发布模板")
        self.btn_publish.clicked.connect(self.publish_template_requested.emit)
        action_row.addWidget(self.btn_publish)
        center.addLayout(action_row)
        layout.addLayout(center, 3)

        right = QVBoxLayout()
        preview_label = QLabel("即时预览")
        preview_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        right.addWidget(preview_label)

        self.preview_tabs = QTabWidget()
        self.structure_preview = QTextBrowser()
        self.sample_preview = QTextBrowser()
        self.rules_preview = QTextBrowser()
        self.preview_tabs.addTab(self.structure_preview, "结构预览")
        self.preview_tabs.addTab(self.sample_preview, "示例效果")
        self.preview_tabs.addTab(self.rules_preview, "规范摘要")
        right.addWidget(self.preview_tabs, 1)
        layout.addLayout(right, 1)

        self._set_empty_preview()

    def set_template_sources(self, builtin_templates: list[Path], recent_templates: list[RecentItem]):
        self.template_list.clear()
        selected = self.filter_combo.currentText()

        if selected == "内置模板":
            for path in builtin_templates:
                item = QListWidgetItem(f"内置：{path.stem}")
                item.setData(Qt.UserRole, str(path))
                self.template_list.addItem(item)
            return

        if selected == "最近使用":
            for recent in recent_templates:
                item = QListWidgetItem(f"{recent.title}\n{recent.path}")
                item.setData(Qt.UserRole, recent.path)
                self.template_list.addItem(item)
            return

        for path in builtin_templates:
            item = QListWidgetItem(f"内置：{path.stem}")
            item.setData(Qt.UserRole, str(path))
            self.template_list.addItem(item)

        for recent in recent_templates:
            item = QListWidgetItem(f"最近：{recent.title}\n{recent.path}")
            item.setData(Qt.UserRole, recent.path)
            self.template_list.addItem(item)

    def load_template(self, template: Template | None):
        self.current_template = template
        if template is None:
            self._set_empty_preview()
            return
        self.editor.set_template(template)
        self.refresh_preview()

    def refresh_preview(self):
        if not self.current_template:
            self._set_empty_preview()
            return

        self.structure_preview.setHtml(self._render_structure_preview())
        self.sample_preview.setHtml(self._render_sample_preview())
        self.rules_preview.setHtml(self._render_rules_summary())

    def _render_structure_preview(self) -> str:
        sections = []
        for _, node, depth in self.current_template.flatten_structure():
            indent = "&nbsp;" * depth * 4
            badges = []
            if node.required:
                badges.append("必填")
            if node.auto_generate:
                badges.append("自动生成")
            badge_text = f"（{' / '.join(badges)}）" if badges else ""
            sections.append(f"{indent}- {node.label} [{node.type}]{badge_text}")
        return (
            f"<h3>{self.current_template.name}</h3>"
            f"<p><b>作者：</b>{self.current_template.author or '未填写'}</p>"
            f"<p><b>描述：</b>{self.current_template.description or '暂无描述'}</p>"
            f"<h4>论文结构</h4><p>{'<br>'.join(sections) or '暂无结构'}</p>"
        )

    def _render_sample_preview(self) -> str:
        styles = self.current_template.format_config.styles
        heading_1 = styles.get("heading_1")
        heading_2 = styles.get("heading_2")
        body = styles.get("body_text")
        cover = styles.get("cover_title")
        return f"""
        <h3>当前模板示例</h3>
        <p>下面的段落会按当前模板样式直接渲染，方便确认 Word 导入后的效果。</p>
        <div style="border: 1px solid #d8dee4; border-radius: 8px; padding: 16px; background: #fbfcfe;">
            <p style="{self._style_to_css(cover)}">毕业设计（论文）题目示例</p>
            <p style="{self._style_to_css(heading_1)}">第1章 绪论</p>
            <p style="{self._style_to_css(heading_2)}">1.1 研究背景</p>
            <p style="{self._style_to_css(body)}">这是正文预览段落，用来确认正文的中文字体、英文字体、字号、缩进和行距是否已经根据导入的 Word 模板更新。</p>
        </div>
        <p><b>已识别样式：</b>{self._style_summary("封面标题", cover)}</p>
        <p>{self._style_summary("一级标题", heading_1)}</p>
        <p>{self._style_summary("二级标题", heading_2)}</p>
        <p>{self._style_summary("正文", body)}</p>
        """

    def _style_to_css(self, style: StyleConfig | None) -> str:
        if style is None:
            return ""
        font_names = []
        for font_name in (style.font.name_cn, style.font.name_en):
            if font_name and font_name not in font_names:
                font_names.append(font_name)
        font_family = ", ".join(f"'{html.escape(name, quote=True)}'" for name in font_names)
        color_hex = style.font.color_hex or "000000"
        if not color_hex.startswith("#"):
            color_hex = f"#{color_hex}"
        return "; ".join(
            [
                f"font-family: {font_family or '\'Times New Roman\''}",
                f"font-size: {style.font.size_pt:g}pt",
                f"font-weight: {'700' if style.font.bold else '400'}",
                f"font-style: {'italic' if style.font.italic else 'normal'}",
                f"color: {color_hex}",
                f"text-align: {style.paragraph.alignment}",
                f"line-height: {style.paragraph.line_spacing:g}",
                f"margin-top: {style.paragraph.space_before_pt:g}pt",
                f"margin-bottom: {style.paragraph.space_after_pt:g}pt",
                f"margin-left: {style.paragraph.left_indent_cm:g}cm",
                f"margin-right: {style.paragraph.right_indent_cm:g}cm",
                f"text-indent: {style.paragraph.first_line_indent_cm:g}cm",
            ]
        )

    def _style_summary(self, label: str, style: StyleConfig | None) -> str:
        if style is None:
            return f"{label}：未识别到对应样式，当前仍显示系统默认值。"
        font_names = " / ".join(
            html.escape(font_name)
            for font_name in (style.font.name_cn, style.font.name_en)
            if font_name
        )
        return (
            f"{label}：{font_names}，{style.font.size_pt:g}pt，"
            f"对齐 {style.paragraph.alignment}，行距 {style.paragraph.line_spacing:g}"
        )

    def _render_rules_summary(self) -> str:
        page = self.current_template.format_config.page_setup
        return f"""
        <h3>页面与规范摘要</h3>
        <p><b>纸张：</b>{page.paper_size}</p>
        <p><b>方向：</b>{'横向' if page.orientation == 'landscape' else '纵向'}</p>
        <p><b>页边距：</b>上 {page.margin_top_cm} cm，下 {page.margin_bottom_cm} cm，左 {page.margin_left_cm} cm，右 {page.margin_right_cm} cm</p>
        <p><b>页眉：</b>{self.current_template.format_config.header_text or '未设置'}</p>
        <p><b>页脚：</b>{self.current_template.format_config.footer_text or '未设置'}</p>
        <p><b>说明：</b>当前模板可用于教师统一定义结构、样式、封面和页面规则，学生项目会继承这些规范。</p>
        <p><b>导入提示：</b>Word 模板导入目前只读取首节页面设置、页眉页脚和常见段落样式，这里显示的是摘要预览，不是原始 Word 页面缩略图。</p>
        """

    def _set_empty_preview(self):
        empty_text = "<p>请选择、导入或创建模板，右侧将显示结构预览、示例效果和规则摘要。</p>"
        self.structure_preview.setHtml(empty_text)
        self.sample_preview.setHtml(empty_text)
        self.rules_preview.setHtml(empty_text)

    def _emit_selected_template(self):
        item = self.template_list.currentItem()
        if item:
            self.open_template_requested.emit(item.data(Qt.UserRole))

    def _on_template_applied(self, template: Template):
        self.current_template = template
        self.refresh_preview()
        self.apply_template_requested.emit()
