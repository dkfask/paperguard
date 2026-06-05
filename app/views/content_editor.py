"""Rich-text editor used in the project writing workspace."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QColor, QFont, QTextCharFormat, QTextListFormat
from PyQt5.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QLabel,
    QMessageBox,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models.project import SectionContent
from app.models.template import SectionNode


class ContentEditor(QWidget):
    """Student-facing editor for one thesis section."""

    content_changed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_section_id: str | None = None
        self.current_node: SectionNode | None = None
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
        ]
        self._setup_ui()
        self.editor.textChanged.connect(self._on_content_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("请选择章节开始编辑")
        self.title_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; padding: 8px; "
            "background-color: #f3f5f7; border-bottom: 1px solid #d0d7de;"
        )
        layout.addWidget(self.title_label)

        toolbar = QToolBar()
        toolbar.setStyleSheet("padding: 6px; spacing: 6px;")

        btn_bold = QToolButton()
        btn_bold.setText("B")
        btn_bold.setFont(QFont("Arial", 10, QFont.Bold))
        btn_bold.clicked.connect(lambda: self._toggle_format("bold"))
        toolbar.addWidget(btn_bold)

        btn_italic = QToolButton()
        btn_italic.setText("I")
        btn_italic.setFont(QFont("Arial", 10))
        btn_italic.font().setItalic(True)
        btn_italic.clicked.connect(lambda: self._toggle_format("italic"))
        toolbar.addWidget(btn_italic)

        btn_underline = QToolButton()
        btn_underline.setText("U")
        btn_underline.clicked.connect(lambda: self._toggle_format("underline"))
        toolbar.addWidget(btn_underline)

        toolbar.addSeparator()

        self.font_family = QFontComboBox()
        toolbar.addWidget(QLabel("字体:"))
        toolbar.addWidget(self.font_family)

        self.font_size = QComboBox()
        for label, _ in self._font_sizes:
            self.font_size.addItem(label)
        self.font_size.setCurrentText("小四")
        self.font_size.currentTextChanged.connect(self._change_font_size)
        toolbar.addWidget(QLabel("字号:"))
        toolbar.addWidget(self.font_size)

        toolbar.addSeparator()

        btn_color = QToolButton()
        btn_color.setText("颜色")
        btn_color.clicked.connect(self._change_text_color)
        toolbar.addWidget(btn_color)

        btn_bullets = QToolButton()
        btn_bullets.setText("项目符号")
        btn_bullets.clicked.connect(lambda: self._insert_list(False))
        toolbar.addWidget(btn_bullets)

        btn_numbers = QToolButton()
        btn_numbers.setText("编号列表")
        btn_numbers.clicked.connect(lambda: self._insert_list(True))
        toolbar.addWidget(btn_numbers)

        btn_quote = QToolButton()
        btn_quote.setText("引用")
        btn_quote.clicked.connect(self._insert_quote)
        toolbar.addWidget(btn_quote)

        toolbar.addSeparator()

        btn_image = QToolButton()
        btn_image.setText("图片")
        btn_image.clicked.connect(self._insert_image)
        toolbar.addWidget(btn_image)

        btn_table = QToolButton()
        btn_table.setText("表格")
        btn_table.clicked.connect(self._insert_table_dialog)
        toolbar.addWidget(btn_table)

        btn_clear = QToolButton()
        btn_clear.setText("清空")
        btn_clear.clicked.connect(self._clear_content)
        toolbar.addWidget(btn_clear)

        layout.addWidget(toolbar)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.setStyleSheet(
            """
            QTextEdit {
                font-size: 12pt;
                padding: 16px;
                background: white;
                border: none;
            }
            """
        )
        layout.addWidget(self.editor)
        self.font_family.currentFontChanged.connect(self._change_font_family)

    def load_content(self, section_id: str, node: SectionNode, content: SectionContent):
        self.current_section_id = section_id
        self.current_node = node
        self.title_label.setText(f"{node.label}{' *' if node.required else ''}")

        self.editor.blockSignals(True)
        self.editor.setHtml(content.content_html if content else "")
        self.editor.blockSignals(False)

    def get_current_html(self) -> str:
        return self.editor.toHtml()

    def _on_content_changed(self):
        if self.current_section_id:
            self.content_changed.emit(self.current_section_id, self.editor.toHtml())

    def _toggle_format(self, format_name: str):
        cursor = self.editor.textCursor()
        current_format = cursor.charFormat()
        text_format = QTextCharFormat()
        if format_name == "bold":
            is_bold = current_format.fontWeight() == QFont.Bold
            text_format.setFontWeight(QFont.Normal if is_bold else QFont.Bold)
        elif format_name == "italic":
            text_format.setFontItalic(not current_format.fontItalic())
        elif format_name == "underline":
            text_format.setFontUnderline(not current_format.fontUnderline())
        self._merge_char_format(text_format)

    def _change_font_size(self, label: str):
        text_format = QTextCharFormat()
        text_format.setFontPointSize(self._font_size_to_pt(label))
        self._merge_char_format(text_format)

    def _change_font_family(self, font: QFont):
        text_format = QTextCharFormat()
        text_format.setFontFamily(font.family())
        self._merge_char_format(text_format)

    def _change_text_color(self):
        color = QColorDialog.getColor(parent=self)
        if not color.isValid():
            return
        text_format = QTextCharFormat()
        text_format.setForeground(color)
        self._merge_char_format(text_format)

    def _merge_char_format(self, text_format: QTextCharFormat):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(text_format)
            self.editor.setTextCursor(cursor)
        self.editor.mergeCurrentCharFormat(text_format)

    def _font_size_to_pt(self, label: str) -> float:
        for name, point_size in self._font_sizes:
            if name == label:
                return point_size
        return 12.0

    def _insert_list(self, ordered: bool):
        cursor = self.editor.textCursor()
        list_format = QTextListFormat()
        list_format.setStyle(
            QTextListFormat.ListDecimal if ordered else QTextListFormat.ListDisc
        )
        cursor.createList(list_format)
        self.editor.setTextCursor(cursor)

    def _insert_quote(self):
        cursor = self.editor.textCursor()
        selected_text = cursor.selectedText().replace("\u2029", "<br>")
        html = (
            f'<blockquote style="margin: 0 0 0 24px; color: #555555;"><p>{selected_text}</p></blockquote>'
            if selected_text
            else '<blockquote style="margin: 0 0 0 24px; color: #555555;"><p>引用内容</p></blockquote>'
        )
        cursor.insertHtml(html)

    def _insert_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif)",
        )
        if path:
            normalized = path.replace("\\", "/")
            self.editor.textCursor().insertHtml(
                f'<p><img src="{normalized}" style="max-width: 500px;" /></p>'
            )

    def _insert_table_dialog(self):
        from PyQt5.QtWidgets import QInputDialog

        rows, ok_rows = QInputDialog.getInt(self, "行数", "请输入行数:", 3, 1, 20)
        if not ok_rows:
            return
        cols, ok_cols = QInputDialog.getInt(self, "列数", "请输入列数:", 3, 1, 10)
        if not ok_cols:
            return

        html_rows = []
        for _ in range(rows):
            cells = "".join(
                '<td style="padding: 6px; border: 1px solid #000;">&nbsp;</td>'
                for _ in range(cols)
            )
            html_rows.append(f"<tr>{cells}</tr>")
        table_html = (
            '<table border="1" style="border-collapse: collapse; width: 100%;">'
            + "".join(html_rows)
            + "</table>"
        )
        self.editor.textCursor().insertHtml(table_html + "<p></p>")

    def _clear_content(self):
        reply = QMessageBox.question(
            self,
            "确认清空",
            "确定要清空当前章节内容吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.editor.clear()
