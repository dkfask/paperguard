"""Rich-text editor used in the project writing workspace."""

from __future__ import annotations

import logging

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

from app.core.reporters.pyqt_reporter import QtIssue
from app.models.project import Project, SectionContent
from app.models.template import SectionNode
from app.services.format_highlighter import FormatHighlighter
from app.services.issue_sidebar import IssueSidebar
from app.services.live_format_checker import LiveFormatChecker
from app.services.live_mutator import LiveMutator

logger = logging.getLogger(__name__)


class ContentEditor(QWidget):
    """Student-facing editor for one thesis section.

    Sprint 1.5 在 ``__init__`` 末尾追加实时检查集成：
    - ``self.checker``     ``LiveFormatChecker`` — QObject + debounce + Qt signal
    - ``self.mutator``     ``LiveMutator``       — 把 QtIssue 对应的 Fix 写回 Project
    - ``self.highlighter`` ``FormatHighlighter`` — 段落红色波浪下划线
    - ``self.sidebar``     ``IssueSidebar``      — Grammarly 风格侧边栏（暴露给上层挂载）

    信号流：
        editor.textChanged → checker.request_check
        checker.issues_found → _on_issues
        checker.no_issues    → _on_clean
        checker.check_failed → _on_check_failed
        sidebar.fix_requested → _on_fix_requested

    Project 注入：
        ContentEditor 不强依赖 Project（默认 ``Project()``）。上层在加载论文时调
        ``set_project(project)`` 即可让实时检查生效。
    """

    content_changed = pyqtSignal(str, str)

    def __init__(self, parent=None, project: Project | None = None):
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
        # ------------------------------------------------------------------
        # Sprint 1.5: 实时约束集成
        # ------------------------------------------------------------------
        # 默认空 Project，避免 LiveFormatChecker 在没有 project 时崩溃
        # （它会读 ``project.sections``）。上层调 ``set_project`` 时重建 checker。
        self._project: Project = project if project is not None else Project()
        # Sidebar / Highlighter 与 project 无关，可以提前建
        self.sidebar = IssueSidebar(self)
        self.highlighter = FormatHighlighter(self.editor)
        # 状态条（底部一行，替代 StatusBar 简化集成）
        self._status_label = QLabel("尚未开始检查")
        self._status_label.setStyleSheet(
            "padding: 4px 10px; background: #f3f5f7; "
            "border-top: 1px solid #d0d7de; color: #444;"
        )
        self._status_label.setMaximumHeight(28)
        self.layout().addWidget(self._status_label)
        # Sidebar 信号 → 触发本编辑器修复动作
        self.sidebar.fix_requested.connect(self._on_fix_requested)
        # Checker / Mutator 需要 project；project 可能为 None（默认空 Project）
        # 上面用 ``Project()`` 保证下面构造不崩
        self.checker: LiveFormatChecker = LiveFormatChecker(self._project, parent=self)
        self.mutator: LiveMutator = LiveMutator(self._project, checker=self.checker)
        # textChanged → request_check（debounced by checker 内置 QTimer）
        self.editor.textChanged.connect(self.checker.request_check)
        # Qt signal → 本编辑器 slot
        self.checker.issues_found.connect(self._on_issues)
        self.checker.no_issues.connect(self._on_clean)
        self.checker.check_failed.connect(self._on_check_failed)

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
        # Sprint 1.5: 切到新 section → 通知 checker 下一轮检查哪个 section
        if self.checker is not None:
            self.checker.set_section_id(section_id)
            # 手动触发一次检查（切 section 不一定伴随 textChanged）
            self.checker.request_check()

    def get_current_html(self) -> str:
        return self.editor.toHtml()

    # ------------------------------------------------------------------
    # Sprint 1.5: 实时约束公共 API
    # ------------------------------------------------------------------
    def get_sidebar(self) -> IssueSidebar:
        """返回实时检查侧边栏（供上层 ``ProjectWorkspaceView`` 挂载到 QTabWidget）。"""
        return self.sidebar

    def set_project(self, project: Project | None) -> None:
        """注入或更新 Project（用于实时检查）。

        - 重建 ``LiveFormatChecker`` / ``LiveMutator``，绑定新 project。
        - 保留 ``IssueSidebar`` / ``FormatHighlighter``（与 project 无关）。
        - 若当前已选中 section，立即把 section_id 同步给新 checker。
        - 触发一次检查，让新 project 立即看到 issues。
        """
        self._project = project if project is not None else Project()

        # 解绑旧 checker 信号（避免 stale signal 干扰）
        try:
            self.editor.textChanged.disconnect(self.checker.request_check)
        except (TypeError, RuntimeError):
            pass
        try:
            self.checker.issues_found.disconnect(self._on_issues)
        except (TypeError, RuntimeError):
            pass
        try:
            self.checker.no_issues.disconnect(self._on_clean)
        except (TypeError, RuntimeError):
            pass
        try:
            self.checker.check_failed.disconnect(self._on_check_failed)
        except (TypeError, RuntimeError):
            pass

        # 重建 checker / mutator
        self.checker = LiveFormatChecker(self._project, parent=self)
        self.mutator = LiveMutator(self._project, checker=self.checker)
        # 重新接线
        self.editor.textChanged.connect(self.checker.request_check)
        self.checker.issues_found.connect(self._on_issues)
        self.checker.no_issues.connect(self._on_clean)
        self.checker.check_failed.connect(self._on_check_failed)
        # 当前 section 同步给新 checker
        if self.current_section_id is not None:
            self.checker.set_section_id(self.current_section_id)
        # 立刻检查一次（让新 project 看到结果）
        self.checker.request_check()

    # ------------------------------------------------------------------
    # Sprint 1.5: Qt signal slots
    # ------------------------------------------------------------------
    def _on_issues(self, issues: list) -> None:
        """``checker.issues_found`` 槽：刷新 sidebar + 画波浪线 + 状态栏。"""
        # issues 可能是 list[QtIssue]（type 注解宽容化避免循环 import）
        self.sidebar.update_issues(issues)
        self.highlighter.mark(issues)
        count = len(issues)
        # 状态条：⚠ N 个格式问题
        self._status_label.setText(f"⚠ {count} 个格式问题")
        self._status_label.setStyleSheet(
            "padding: 4px 10px; background: #fff4e5; "
            "border-top: 1px solid #d97706; color: #92400e;"
        )

    def _on_clean(self) -> None:
        """``checker.no_issues`` 槽：清空 sidebar + 高亮 + 状态条。"""
        self.sidebar.clear()
        self.highlighter.clear()
        self._status_label.setText("✓ 格式合规")
        self._status_label.setStyleSheet(
            "padding: 4px 10px; background: #ecfdf5; "
            "border-top: 1px solid #059669; color: #065f46;"
        )

    def _on_check_failed(self, msg: str) -> None:
        """``checker.check_failed`` 槽：失败容忍 — 状态条提示 + 后端日志。"""
        logger.warning("ContentEditor 实时检查失败: %s", msg)
        self._status_label.setText("⚠ 检查失败")
        self._status_label.setStyleSheet(
            "padding: 4px 10px; background: #fee2e2; "
            "border-top: 1px solid #b91c1c; color: #7f1d1d;"
        )

    def _on_fix_requested(self, issue) -> None:
        """``sidebar.fix_requested`` 槽：应用修复 + 重载 editor + 触发新检查。

        关键点（plan Must-fix #1.3）：
            editor.textChanged 会被 ``self.editor.setHtml`` 触发，
            立即重入一个 check（"修一次触发两次 check"）。
            解决方案：用 ``editor.blockSignals(True/False)`` 包裹 apply_fix + setHtml，
            末尾再手动 ``self.checker.request_check()`` 触发一次（受 debounce 控制）。
        """
        if not isinstance(issue, QtIssue):
            # 防御性：非 QtIssue 一律拒绝
            return
        if self.mutator is None or self.checker is None:
            return
        if not self.mutator.can_apply(issue):
            logger.info("ContentEditor 无法应用修复: rule_id=%s", issue.rule_id)
            return

        # 关键：屏蔽 editor.textChanged 避免 reentrant check
        self.editor.blockSignals(True)
        try:
            ok = self.mutator.apply_fix(issue)
            if not ok:
                logger.info("ContentEditor apply_fix 返回 False: %s", issue.rule_id)
                return
            # 重载当前 section 的 HTML（让 editor 看到 blocks 修改）
            if self.current_section_id is not None and self._project is not None:
                section = self._project.get_content(self.current_section_id)
                self.editor.setHtml(section.content_html)
        finally:
            self.editor.blockSignals(False)
        # 手动触发新一轮 check（受 checker 内置 debounce 控制）
        self.checker.request_check()

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
