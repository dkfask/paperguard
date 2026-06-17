"""Workspace for validation and DOCX export."""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.controllers.review_ctrl import ReviewSummary


class ReviewExportView(QWidget):
    jump_to_target_requested = pyqtSignal(str)
    export_requested = pyqtSignal(str)
    validate_requested = pyqtSignal()
    docx_import_requested = pyqtSignal()
    docx_check_requested = pyqtSignal()
    docx_fix_requested = pyqtSignal()
    docx_fix_one_requested = pyqtSignal(int)
    docx_export_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        left = QVBoxLayout()
        left.addWidget(QLabel("检查结果"))
        self.issue_scroll = QScrollArea()
        self.issue_scroll.setWidgetResizable(True)
        self.issue_container = QWidget()
        self.issue_layout = QVBoxLayout(self.issue_container)
        self.issue_scroll.setWidget(self.issue_container)
        left.addWidget(self.issue_scroll, 1)
        layout.addLayout(left, 2)

        right = QVBoxLayout()
        right.addWidget(QLabel("导出配置"))
        self.summary = QLabel("请先在项目写作区填写论文内容，再执行检查。")
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)

        self.filename = QLineEdit()
        self.filename.setPlaceholderText("导出文件名")
        right.addWidget(self.filename)

        self.last_export_label = QLabel("尚未导出")
        self.last_export_label.setWordWrap(True)
        right.addWidget(self.last_export_label)

        self.notes = QLabel("存在 error 时将阻止导出；仅有 warning 时，允许用户确认后继续导出。")
        self.notes.setWordWrap(True)
        right.addWidget(self.notes)

        self.result_panel = QFrame()
        self.result_panel.setFrameShape(QFrame.StyledPanel)
        result_layout = QVBoxLayout(self.result_panel)
        self.result_title = QLabel("导出完成后将在这里显示结果摘要")
        self.result_title.setStyleSheet("font-weight: bold;")
        result_layout.addWidget(self.result_title)
        self.result_body = QLabel("当前支持基础富文本、列表、引用、图片、表格、章节标题和目录导出。")
        self.result_body.setWordWrap(True)
        result_layout.addWidget(self.result_body)
        right.addWidget(self.result_panel)

        self.docx_panel = QFrame()
        self.docx_panel.setFrameShape(QFrame.StyledPanel)
        docx_layout = QVBoxLayout(self.docx_panel)

        docx_title = QLabel("DOCX 批改")
        docx_title.setStyleSheet("font-weight: bold;")
        docx_layout.addWidget(docx_title)

        self.docx_pack_combo = QComboBox()
        self.docx_pack_combo.addItems([
            "GB-T-7713-1-2006",
            "GB-T-7714-1-2015",
            "SIM-UNIV-generic",
        ])
        docx_layout.addWidget(self.docx_pack_combo)

        self.docx_source_label = QLabel("尚未导入 DOCX")
        self.docx_source_label.setWordWrap(True)
        docx_layout.addWidget(self.docx_source_label)

        self.docx_status_label = QLabel("导入后可检查、自动修复并导出新 DOCX。")
        self.docx_status_label.setWordWrap(True)
        docx_layout.addWidget(self.docx_status_label)

        self.docx_issue_scroll = QScrollArea()
        self.docx_issue_scroll.setWidgetResizable(True)
        self.docx_issue_container = QWidget()
        self.docx_issue_layout = QVBoxLayout(self.docx_issue_container)
        self.docx_issue_scroll.setWidget(self.docx_issue_container)
        self.docx_issue_scroll.setMinimumHeight(120)
        docx_layout.addWidget(self.docx_issue_scroll)
        self.docx_issue_buttons: list[QPushButton] = []
        self.docx_issue_labels: list[QLabel] = []

        self.docx_export_name = QLineEdit()
        self.docx_export_name.setPlaceholderText("修复后文件名")
        docx_layout.addWidget(self.docx_export_name)

        self.docx_export_label = QLabel("尚未导出修复文件")
        self.docx_export_label.setWordWrap(True)
        docx_layout.addWidget(self.docx_export_label)

        docx_button_row = QHBoxLayout()
        self.btn_docx_import = QPushButton("导入")
        self.btn_docx_import.clicked.connect(self.docx_import_requested.emit)
        docx_button_row.addWidget(self.btn_docx_import)

        self.btn_docx_check = QPushButton("检查")
        self.btn_docx_check.clicked.connect(self.docx_check_requested.emit)
        docx_button_row.addWidget(self.btn_docx_check)

        self.btn_docx_fix = QPushButton("修复")
        self.btn_docx_fix.clicked.connect(self.docx_fix_requested.emit)
        docx_button_row.addWidget(self.btn_docx_fix)

        self.btn_docx_export = QPushButton("导出")
        self.btn_docx_export.clicked.connect(
            lambda: self.docx_export_requested.emit(self.docx_export_name.text().strip())
        )
        docx_button_row.addWidget(self.btn_docx_export)
        docx_layout.addLayout(docx_button_row)
        right.addWidget(self.docx_panel)

        btn_validate = QPushButton("重新检查")
        btn_validate.clicked.connect(self.validate_requested.emit)
        right.addWidget(btn_validate)

        btn_export = QPushButton("导出 DOCX")
        btn_export.clicked.connect(lambda: self.export_requested.emit(self.filename.text().strip()))
        right.addWidget(btn_export)
        right.addStretch()
        layout.addLayout(right, 1)
        self.set_docx_review_state()

    def selected_docx_pack(self) -> str:
        return self.docx_pack_combo.currentText()

    def set_docx_review_state(
        self,
        *,
        source_path: str = "",
        status: str = "导入后可检查、自动修复并导出新 DOCX。",
        export_path: str = "",
        can_check: bool = False,
        can_fix: bool = False,
        can_export: bool = False,
        findings: list | None = None,
    ) -> None:
        self.docx_source_label.setText(f"当前文件：{source_path}" if source_path else "尚未导入 DOCX")
        self.docx_status_label.setText(status)
        self.docx_export_label.setText(f"最近导出修复文件：{export_path}" if export_path else "尚未导出修复文件")
        self.btn_docx_check.setEnabled(can_check)
        self.btn_docx_fix.setEnabled(can_fix)
        self.btn_docx_export.setEnabled(can_export)
        self._render_docx_findings(findings or [])

    def _render_docx_findings(self, findings: list) -> None:
        while self.docx_issue_layout.count():
            item = self.docx_issue_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.docx_issue_buttons = []
        self.docx_issue_labels = []

        if not findings:
            label = QLabel("暂无 DOCX 格式问题。")
            label.setWordWrap(True)
            self.docx_issue_layout.addWidget(label)
            self.docx_issue_layout.addStretch()
            return

        for index, finding in enumerate(findings):
            card = QFrame()
            card.setFrameShape(QFrame.StyledPanel)
            card_layout = QVBoxLayout(card)

            title = QLabel(f"{finding.rule_id}：{finding.message}")
            title.setWordWrap(True)
            title.setStyleSheet("font-weight: bold;")
            card_layout.addWidget(title)
            self.docx_issue_labels.append(title)

            para_index = finding.location.paragraph_index
            location_text = "未定位" if para_index is None else f"第 {para_index + 1} 段"
            location = QLabel(f"位置：{location_text}")
            location.setWordWrap(True)
            card_layout.addWidget(location)

            if finding.fix is not None:
                fix_text = QLabel(f"修复：{finding.fix.action} {finding.fix.target}")
                fix_text.setWordWrap(True)
                card_layout.addWidget(fix_text)

            button = QPushButton("修复此项")
            button.setEnabled(finding.fix is not None)
            button.clicked.connect(lambda _, item_index=index: self.docx_fix_one_requested.emit(item_index))
            card_layout.addWidget(button)
            self.docx_issue_buttons.append(button)
            self.docx_issue_layout.addWidget(card)

        self.docx_issue_layout.addStretch()

    def set_context(
        self,
        template_name: str,
        project_title: str,
        summary: ReviewSummary,
        export_filename: str,
        last_export_path: str = "",
    ):
        self.filename.setText(export_filename)
        self.summary.setText(
            f"模板：{template_name or '未加载'}\n"
            f"项目：{project_title or '未命名论文'}\n"
            f"必须修复：{len(summary.errors)} 项，建议修复：{len(summary.warnings)} 项"
        )

        if last_export_path:
            self.last_export_label.setText(
                f"最近导出：{last_export_path}\n时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            self.result_title.setText("最近一次导出已完成")
            self.result_body.setText(
                "可以直接在 Word 中打开导出文档继续检查版式。如果刚刚还有 warning，请优先回到左侧按建议修复。"
            )
        else:
            self.last_export_label.setText("尚未导出")
            self.result_title.setText("导出前说明")
            self.result_body.setText(
                "建议先完成检查并修复所有必须问题，再导出 DOCX。导出后这里会显示最近结果和保真范围提示。"
            )
        self._render_issues(summary)

    def _render_issues(self, summary: ReviewSummary):
        while self.issue_layout.count():
            item = self.issue_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not summary.issues:
            passed = QLabel("检查通过，没有发现需要处理的问题。")
            passed.setWordWrap(True)
            self.issue_layout.addWidget(passed)
            self.issue_layout.addStretch()
            return

        groups = [("必须修复", summary.errors), ("建议修复", summary.warnings)]
        for group_title, issues in groups:
            if not issues:
                continue
            title = QLabel(group_title)
            title.setStyleSheet("font-size: 16px; font-weight: bold;")
            self.issue_layout.addWidget(title)
            for issue in issues:
                card = QFrame()
                card.setFrameShape(QFrame.StyledPanel)
                card_layout = QVBoxLayout(card)
                message = QLabel(issue.message)
                message.setStyleSheet("font-weight: bold;")
                message.setWordWrap(True)
                card_layout.addWidget(message)

                target_text = issue.section_id or issue.target or "未定位"
                location = QLabel(f"位置：{target_text}")
                location.setWordWrap(True)
                card_layout.addWidget(location)

                if issue.fix_hint:
                    hint = QLabel(f"修复建议：{issue.fix_hint}")
                    hint.setWordWrap(True)
                    card_layout.addWidget(hint)

                btn = QPushButton("定位并处理")
                btn.clicked.connect(
                    lambda _, target=issue.target or issue.section_id: self.jump_to_target_requested.emit(target)
                )
                card_layout.addWidget(btn)
                self.issue_layout.addWidget(card)

        self.issue_layout.addStretch()
