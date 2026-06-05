"""Template editor dialog."""

from __future__ import annotations

import re

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.template import SECTION_TYPES, SectionNode, Template


class TemplateEditor(QDialog):
    """Dialog used by teachers to edit templates."""

    template_saved = pyqtSignal(Template)

    def __init__(self, template: Template | None = None, parent=None, embedded: bool = False):
        super().__init__(parent)
        self.template = template or Template()
        self.embedded = embedded
        self._font_sizes = [
            ("初号", 42),
            ("小初", 36),
            ("一号", 26),
            ("小一", 24),
            ("二号", 22),
            ("小二", 18),
            ("三号", 16),
            ("小三", 15),
            ("四号", 14),
            ("小四", 12),
            ("五号", 10.5),
            ("小五", 9),
            ("六号", 7.5),
            ("小六", 6.5),
        ]
        self._current_style_id: str | None = None
        self._current_section_id: str | None = None
        self.setWindowTitle("模板编辑器")
        self.resize(980, 700)
        self._setup_ui()
        self._load_template()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_info_tab(), "基本信息")
        self.tabs.addTab(self._create_page_setup_tab(), "页面设置")
        self.tabs.addTab(self._create_styles_tab(), "格式样式")
        self.tabs.addTab(self._create_structure_tab(), "论文结构")
        layout.addWidget(self.tabs)

        buttons = QHBoxLayout()
        buttons.addStretch()
        btn_save = QPushButton("应用修改" if self.embedded else "保存模板")
        btn_save.clicked.connect(self._save_template)
        buttons.addWidget(btn_save)
        if not self.embedded:
            btn_cancel = QPushButton("取消")
            btn_cancel.clicked.connect(self.reject)
            buttons.addWidget(btn_cancel)
        layout.addLayout(buttons)

    def _create_info_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.edit_name = QLineEdit()
        form.addRow("模板名称:", self.edit_name)
        self.edit_author = QLineEdit()
        form.addRow("作者:", self.edit_author)
        self.edit_desc = QTextEdit()
        self.edit_desc.setMaximumHeight(120)
        form.addRow("描述:", self.edit_desc)
        return widget

    def _create_page_setup_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self.page_size = QComboBox()
        self.page_size.addItems(["A4", "A3", "Letter", "B5"])
        form.addRow("纸张大小:", self.page_size)

        self.page_orientation = QComboBox()
        self.page_orientation.addItems(["纵向", "横向"])
        form.addRow("页面方向:", self.page_orientation)

        self.margin_top = self._create_margin_spinbox()
        form.addRow("上边距:", self.margin_top)
        self.margin_bottom = self._create_margin_spinbox()
        form.addRow("下边距:", self.margin_bottom)
        self.margin_left = self._create_margin_spinbox()
        form.addRow("左边距:", self.margin_left)
        self.margin_right = self._create_margin_spinbox()
        form.addRow("右边距:", self.margin_right)
        return widget

    def _create_styles_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)

        self.style_list = QListWidget()
        self.style_list.setMaximumWidth(180)
        self.style_list.currentRowChanged.connect(self._on_style_selected)
        layout.addWidget(self.style_list)

        form_widget = QWidget()
        form = QFormLayout(form_widget)

        self.style_id_label = QLabel()
        form.addRow("样式:", self.style_id_label)

        self.font_cn = QComboBox()
        self.font_cn.addItems(["宋体", "黑体", "楷体", "仿宋", "微软雅黑"])
        form.addRow("中文字体:", self.font_cn)

        self.font_en = QComboBox()
        self.font_en.addItems(["Times New Roman", "Arial", "Calibri", "Cambria"])
        form.addRow("英文字体:", self.font_en)

        self.font_size = QComboBox()
        for label, _ in self._font_sizes:
            self.font_size.addItem(label)
        form.addRow("字号:", self.font_size)

        self.font_bold = QCheckBox("加粗")
        form.addRow("", self.font_bold)

        self.font_italic = QCheckBox("斜体")
        form.addRow("", self.font_italic)

        self.alignment = QComboBox()
        self.alignment.addItems(["left", "center", "right", "justify"])
        form.addRow("对齐:", self.alignment)

        self.line_spacing = QDoubleSpinBox()
        self.line_spacing.setRange(1.0, 3.0)
        self.line_spacing.setSingleStep(0.1)
        form.addRow("行距:", self.line_spacing)

        self.space_before = QDoubleSpinBox()
        self.space_before.setRange(0, 100)
        self.space_before.setSuffix(" pt")
        form.addRow("段前距:", self.space_before)

        self.space_after = QDoubleSpinBox()
        self.space_after.setRange(0, 100)
        self.space_after.setSuffix(" pt")
        form.addRow("段后距:", self.space_after)

        self.first_indent = QDoubleSpinBox()
        self.first_indent.setRange(0, 5)
        self.first_indent.setSuffix(" cm")
        form.addRow("首行缩进:", self.first_indent)

        self.style_preview = QTextEdit()
        self.style_preview.setReadOnly(True)
        self.style_preview.setMaximumHeight(180)
        form.addRow("预览:", self.style_preview)

        for widget_item in [
            self.font_cn,
            self.font_en,
            self.font_size,
            self.font_bold,
            self.font_italic,
            self.alignment,
            self.line_spacing,
            self.space_before,
            self.space_after,
            self.first_indent,
        ]:
            signal = getattr(widget_item, "currentTextChanged", None)
            if signal:
                signal.connect(self._update_style_preview)
                continue
            signal = getattr(widget_item, "valueChanged", None)
            if signal:
                signal.connect(self._update_style_preview)
                continue
            signal = getattr(widget_item, "toggled", None)
            if signal:
                signal.connect(self._update_style_preview)

        layout.addWidget(form_widget)
        return widget

    def _create_structure_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        self.struct_list = QListWidget()
        self.struct_list.currentItemChanged.connect(self._on_structure_selected)
        left_layout.addWidget(self.struct_list)

        button_row = QHBoxLayout()
        btn_add_chapter = QPushButton("+ 添加章节")
        btn_add_chapter.clicked.connect(self._add_chapter)
        button_row.addWidget(btn_add_chapter)
        btn_add_section = QPushButton("+ 添加小节")
        btn_add_section.clicked.connect(self._add_section)
        button_row.addWidget(btn_add_section)
        left_layout.addLayout(button_row)

        move_row = QHBoxLayout()
        btn_remove = QPushButton("删除选中")
        btn_remove.clicked.connect(self._remove_section)
        move_row.addWidget(btn_remove)
        btn_up = QPushButton("上移")
        btn_up.clicked.connect(self._move_up)
        move_row.addWidget(btn_up)
        btn_down = QPushButton("下移")
        btn_down.clicked.connect(self._move_down)
        move_row.addWidget(btn_down)
        left_layout.addLayout(move_row)

        detail_panel = QWidget()
        detail_form = QFormLayout(detail_panel)

        self.struct_label = QLineEdit()
        detail_form.addRow("名称:", self.struct_label)

        self.struct_type = QComboBox()
        self.struct_type.addItems(SECTION_TYPES)
        detail_form.addRow("类型:", self.struct_type)

        self.struct_required = QCheckBox("必填")
        detail_form.addRow("", self.struct_required)

        self.struct_auto_generate = QCheckBox("自动生成")
        detail_form.addRow("", self.struct_auto_generate)

        self.struct_style = QComboBox()
        detail_form.addRow("关联样式:", self.struct_style)

        self.struct_desc = QTextEdit()
        self.struct_desc.setMaximumHeight(180)
        detail_form.addRow("填写说明:", self.struct_desc)

        splitter.addWidget(left_panel)
        splitter.addWidget(detail_panel)
        splitter.setSizes([360, 420])
        return widget

    def _create_margin_spinbox(self) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(0, 10)
        spinbox.setSuffix(" cm")
        return spinbox

    def _load_template(self):
        self.style_list.blockSignals(True)
        self.struct_list.blockSignals(True)

        self.edit_name.setText(self.template.name)
        self.edit_author.setText(self.template.author)
        self.edit_desc.setPlainText(self.template.description)

        page = self.template.format_config.page_setup
        self.page_size.setCurrentText(page.paper_size)
        self.page_orientation.setCurrentText("纵向" if page.orientation == "portrait" else "横向")
        self.margin_top.setValue(page.margin_top_cm)
        self.margin_bottom.setValue(page.margin_bottom_cm)
        self.margin_left.setValue(page.margin_left_cm)
        self.margin_right.setValue(page.margin_right_cm)

        self.style_list.clear()
        for style_id in self.template.format_config.styles:
            self.style_list.addItem(style_id)

        self.struct_style.clear()
        self.struct_style.addItem("")
        for style_id in self.template.format_config.styles:
            self.struct_style.addItem(style_id)

        self._load_structure()

        self.style_list.blockSignals(False)
        self.struct_list.blockSignals(False)

        if self.style_list.count():
            self.style_list.setCurrentRow(0)
            self._on_style_selected(0)
        if self.struct_list.count():
            self.struct_list.setCurrentRow(0)
            self._on_structure_selected(self.struct_list.item(0), None)

    def _load_structure(self):
        self.struct_list.clear()
        for _, node, depth in self.template.flatten_structure():
            indent = "  " * depth
            prefix = "*" if node.required else ""
            item = QListWidgetItem(f"{indent}{prefix}{node.label} [{node.type}]")
            item.setData(Qt.UserRole, node.id)
            self.struct_list.addItem(item)

    def _on_style_selected(self, row: int):
        if self._current_style_id:
            self._store_style_form(self._current_style_id)
        if row < 0:
            self._current_style_id = None
            return

        style_ids = list(self.template.format_config.styles.keys())
        if row >= len(style_ids):
            return

        style_id = style_ids[row]
        config = self.template.format_config.styles[style_id]
        self._current_style_id = style_id

        self.style_id_label.setText(style_id)
        self.font_cn.setCurrentText(config.font.name_cn)
        self.font_en.setCurrentText(config.font.name_en)
        self.font_size.setCurrentText(self._closest_font_size_label(config.font.size_pt))
        self.font_bold.setChecked(config.font.bold)
        self.font_italic.setChecked(config.font.italic)
        self.alignment.setCurrentText(config.paragraph.alignment)
        self.line_spacing.setValue(config.paragraph.line_spacing)
        self.space_before.setValue(config.paragraph.space_before_pt)
        self.space_after.setValue(config.paragraph.space_after_pt)
        self.first_indent.setValue(config.paragraph.first_line_indent_cm)
        self._update_style_preview()

    def _store_style_form(self, style_id: str):
        if style_id not in self.template.format_config.styles:
            return
        config = self.template.format_config.styles[style_id]
        config.font.name_cn = self.font_cn.currentText()
        config.font.name_en = self.font_en.currentText()
        config.font.size_pt = self._font_size_to_pt(self.font_size.currentText())
        config.font.bold = self.font_bold.isChecked()
        config.font.italic = self.font_italic.isChecked()
        config.paragraph.alignment = self.alignment.currentText()
        config.paragraph.line_spacing = self.line_spacing.value()
        config.paragraph.space_before_pt = self.space_before.value()
        config.paragraph.space_after_pt = self.space_after.value()
        config.paragraph.first_line_indent_cm = self.first_indent.value()

    def _update_style_preview(self):
        font = QFont(self.font_cn.currentText(), int(self._font_size_to_pt(self.font_size.currentText())))
        font.setBold(self.font_bold.isChecked())
        font.setItalic(self.font_italic.isChecked())
        self.style_preview.setFont(font)
        self.style_preview.setAlignment(
            {
                "left": Qt.AlignLeft,
                "center": Qt.AlignCenter,
                "right": Qt.AlignRight,
                "justify": Qt.AlignJustify,
            }.get(self.alignment.currentText(), Qt.AlignLeft)
        )
        self.style_preview.setPlainText(
            "第1章 绪论\n\n这是一个预览段落，用来展示当前字体、字号、对齐方式和基础样式效果。"
        )

    def _on_structure_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None):
        previous_id = self._item_user_role(previous) or self._current_section_id
        if previous_id:
            self._apply_structure_form(previous_id)

        if current is None:
            self._current_section_id = None
            return

        section_id = self._item_user_role(current)
        if not section_id:
            self._current_section_id = None
            return
        node = self.template.find_section(section_id)
        if not node:
            return

        self._current_section_id = section_id
        self.struct_label.setText(node.label)
        self.struct_type.setCurrentText(node.type)
        self.struct_required.setChecked(node.required)
        self.struct_auto_generate.setChecked(node.auto_generate)
        self.struct_style.setCurrentText(node.style_id)
        self.struct_desc.setPlainText(node.description)

    def _store_structure_form(self, section_id: str):
        self._apply_structure_form(section_id)
        self._load_structure_preserve(section_id)

    def _apply_structure_form(self, section_id: str):
        node = self.template.find_section(section_id)
        if not node:
            return
        node.label = self.struct_label.text().strip() or node.label
        node.type = self.struct_type.currentText()
        node.required = self.struct_required.isChecked()
        node.auto_generate = self.struct_auto_generate.isChecked()
        node.style_id = self.struct_style.currentText() or "body_text"
        node.description = self.struct_desc.toPlainText().strip()

    def _load_structure_preserve(self, section_id: str | None):
        self.struct_list.blockSignals(True)
        self._load_structure()
        self.struct_list.blockSignals(False)

        if section_id:
            for index in range(self.struct_list.count()):
                item = self.struct_list.item(index)
                if item.data(Qt.UserRole) == section_id:
                    self.struct_list.setCurrentRow(index)
                    self._on_structure_selected(item, None)
                    break

    def _item_user_role(self, item: QListWidgetItem | None) -> str | None:
        if item is None:
            return None
        try:
            return item.data(Qt.UserRole)
        except RuntimeError:
            return None

    def _save_template(self):
        if self._current_style_id:
            self._store_style_form(self._current_style_id)
        if self._current_section_id:
            self._store_structure_form(self._current_section_id)

        self.template.name = self.edit_name.text().strip()
        self.template.author = self.edit_author.text().strip()
        self.template.description = self.edit_desc.toPlainText().strip()

        if not self.template.name:
            QMessageBox.warning(self, "提示", "请输入模板名称。")
            return

        page = self.template.format_config.page_setup
        page.apply_paper_size(self.page_size.currentText())
        page.orientation = "portrait" if self.page_orientation.currentText() == "纵向" else "landscape"
        page.margin_top_cm = self.margin_top.value()
        page.margin_bottom_cm = self.margin_bottom.value()
        page.margin_left_cm = self.margin_left.value()
        page.margin_right_cm = self.margin_right.value()

        self.template_saved.emit(self.template)
        if not self.embedded:
            self.accept()

    def set_template(self, template: Template):
        self.template = template
        self._current_style_id = None
        self._current_section_id = None
        self._load_template()

    def _add_chapter(self):
        chapter_count = sum(1 for node in self.template.structure if node.type == "chapter") + 1
        new_node = SectionNode(
            id=self._next_section_id("chapter"),
            label=f"第{chapter_count}章 新章节",
            type="chapter",
            required=True,
            style_id="heading_1",
        )
        self.template.structure.append(new_node)
        self._load_structure_preserve(new_node.id)

    def _add_section(self):
        current = self.struct_list.currentItem()
        if current is None:
            QMessageBox.warning(self, "提示", "请先选中一个章节或小节。")
            return

        current_id = self._item_user_role(current)
        if not current_id:
            return
        node = self.template.find_section(current_id)
        if node is None:
            return

        if node.type == "section":
            parent = self.template.find_parent_section(node.id)
        elif node.type in {"chapter", "appendix"}:
            parent = node
        else:
            parent = self.template.find_parent_section(node.id)

        if parent is None:
            QMessageBox.warning(self, "提示", "当前选中项下不能添加小节，请先选中章节。")
            return

        new_index = len(parent.subsections) + 1
        label_prefix = self._section_prefix_for_parent(parent)
        new_node = SectionNode(
            id=self._next_section_id(parent.id),
            label=f"{label_prefix}.{new_index} 新小节",
            type="section",
            style_id="heading_2",
        )
        parent.subsections.append(new_node)
        self._load_structure_preserve(new_node.id)

    def _section_prefix_for_parent(self, parent: SectionNode) -> str:
        match = re.search(r"第(\d+)章", parent.label)
        if match:
            return match.group(1)
        match = re.search(r"^(\d+)", parent.label)
        if match:
            return match.group(1)
        return str(len([node for node in self.template.structure if node.type == "chapter"]))

    def _remove_section(self):
        item = self.struct_list.currentItem()
        if item is None:
            return
        section_id = self._item_user_role(item)
        if not section_id:
            return
        from app.controllers.template_ctrl import TemplateController

        if TemplateController.remove_section(self.template, section_id):
            self._load_structure_preserve(None)

    def _move_up(self):
        self._move_section(-1)

    def _move_down(self):
        self._move_section(1)

    def _move_section(self, direction: int):
        item = self.struct_list.currentItem()
        if item is None:
            return
        section_id = self._item_user_role(item)
        if not section_id:
            return
        from app.controllers.template_ctrl import TemplateController

        if TemplateController.move_section(self.template, section_id, direction):
            self._load_structure_preserve(section_id)

    def _font_size_to_pt(self, label: str) -> float:
        for name, size in self._font_sizes:
            if name == label:
                return size
        return 12.0

    def _closest_font_size_label(self, point_size: float) -> str:
        return min(self._font_sizes, key=lambda item: abs(item[1] - point_size))[0]

    def _next_section_id(self, prefix: str) -> str:
        existing = {node.id for _, node, _ in self.template.flatten_structure()}
        index = 1
        while f"{prefix}_{index}" in existing:
            index += 1
        return f"{prefix}_{index}"
