"""Workspace for student project writing."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.models.template import Template
from app.views.content_editor import ContentEditor
from app.views.tree_panel import TreePanel


class ProjectWorkspaceView(QWidget):
    create_project_requested = pyqtSignal()
    content_changed = pyqtSignal(str, str)
    metadata_changed = pyqtSignal(dict)
    section_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.template: Template | None = None
        self.project: Project | None = None
        self._highlighted_widget: QLineEdit | None = None
        self._metadata_widgets: dict[str, QLineEdit] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        self.page_title = QLabel("项目写作")
        self.page_title.setStyleSheet("font-size: 20px; font-weight: bold;")
        top_row.addWidget(self.page_title)
        top_row.addStretch()
        self.btn_create_project = QPushButton("从当前模板创建项目")
        self.btn_create_project.clicked.connect(self.create_project_requested.emit)
        top_row.addWidget(self.btn_create_project)
        layout.addLayout(top_row)

        self.progress_label = QLabel("尚未加载模板或项目。")
        self.progress_label.setStyleSheet("color: #555;")
        layout.addWidget(self.progress_label)

        self.empty_hint = QLabel(
            "先在“模板中心”准备模板，再创建论文项目。创建后左侧会显示章节状态，中间用于编辑正文，右侧可查看章节说明和项目信息。"
        )
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setStyleSheet(
            "background:#faf7f2; border:1px solid #eadfcd; padding:10px; border-radius:6px; color:#6a5330;"
        )
        layout.addWidget(self.empty_hint)

        splitter = QSplitter()

        self.tree_panel = TreePanel()
        self.tree_panel.section_selected.connect(self.section_selected.emit)
        splitter.addWidget(self.tree_panel)

        self.content_editor = ContentEditor()
        self.content_editor.content_changed.connect(self.content_changed.emit)
        splitter.addWidget(self.content_editor)

        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self._build_description_tab(), "章节说明")
        self.right_tabs.addTab(self._build_metadata_tab(), "项目信息")
        splitter.addWidget(self.right_tabs)

        splitter.setSizes([250, 620, 300])
        layout.addWidget(splitter, 1)

    def _build_description_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.description_browser = QTextBrowser()
        self.description_browser.setHtml(
            "<p>选择章节后，这里会显示模板中的填写提示、章节类型和完成要求。</p>"
        )
        layout.addWidget(self.description_browser)
        return panel

    def _build_metadata_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        info_group = QGroupBox("论文信息")
        form = QFormLayout(info_group)

        self.meta_title = QLineEdit()
        self.meta_author = QLineEdit()
        self.meta_student_id = QLineEdit()
        self.meta_major = QLineEdit()
        self.meta_supervisor = QLineEdit()

        self._metadata_widgets = {
            "title": self.meta_title,
            "author": self.meta_author,
            "student_id": self.meta_student_id,
            "major": self.meta_major,
            "supervisor": self.meta_supervisor,
        }

        form.addRow("标题:", self.meta_title)
        form.addRow("作者:", self.meta_author)
        form.addRow("学号:", self.meta_student_id)
        form.addRow("专业:", self.meta_major)
        form.addRow("导师:", self.meta_supervisor)

        for widget in self._metadata_widgets.values():
            widget.textChanged.connect(self._emit_metadata_changed)

        layout.addWidget(info_group)
        layout.addStretch()
        return panel

    def set_context(
        self,
        template: Template | None,
        project: Project | None,
        statuses: dict[str, str],
        completed_required: int,
        total_required: int,
        review_text: str,
    ):
        self.template = template
        self.project = project
        has_template = template is not None
        has_project = project is not None and bool(project.template_name or project.file_path or project.sections)

        self.btn_create_project.setEnabled(has_template)
        self.btn_create_project.setToolTip("" if has_template else "请先在模板中心加载或创建模板。")
        self.empty_hint.setVisible(not has_template or not has_project)
        self.content_editor.setEnabled(has_template and has_project)

        if template is not None:
            self.tree_panel.load_template(template, statuses)
        else:
            self.tree_panel.tree.clear()

        if project is not None:
            for key, widget in self._metadata_widgets.items():
                widget.blockSignals(True)
                widget.setText(getattr(project, key, ""))
                widget.blockSignals(False)
        else:
            for widget in self._metadata_widgets.values():
                widget.blockSignals(True)
                widget.clear()
                widget.blockSignals(False)

        self.progress_label.setText(review_text or f"必填章节完成度：{completed_required}/{total_required}")

        if not has_template:
            self.description_browser.setHtml("<p>当前还没有模板。请先前往“模板中心”创建、导入或加载模板。</p>")
        elif not has_project:
            self.description_browser.setHtml("<p>模板已就绪。点击右上角“从当前模板创建项目”即可开始写作。</p>")

    def show_section(self, section_id: str):
        if not self.template or not self.project:
            return
        node = self.template.find_section(section_id)
        if node is None:
            return
        self.tree_panel.select_section(section_id)
        self.content_editor.load_content(section_id, node, self.project.get_content(section_id))
        self.description_browser.setHtml(
            f"""
            <h3>{node.label}</h3>
            <p><b>类型：</b>{node.type}</p>
            <p><b>是否必填：</b>{'是' if node.required else '否'}</p>
            <p><b>是否自动生成：</b>{'是' if node.auto_generate else '否'}</p>
            <p><b>填写提示：</b>{node.description or '暂无章节说明，可直接在中间编辑区填写内容。'}</p>
            """
        )

    def focus_target(self, target: str):
        self._clear_target_highlight()
        widget = self._metadata_widgets.get(target)
        if widget is not None:
            self.right_tabs.setCurrentIndex(1)
            widget.setFocus()
            widget.setStyleSheet("border:2px solid #d97706; background:#fff7ed;")
            self._highlighted_widget = widget
            return
        self.show_section(target)

    def _clear_target_highlight(self):
        if self._highlighted_widget is not None:
            self._highlighted_widget.setStyleSheet("")
            self._highlighted_widget = None

    def _emit_metadata_changed(self):
        self.metadata_changed.emit(
            {
                "title": self.meta_title.text().strip(),
                "author": self.meta_author.text().strip(),
                "student_id": self.meta_student_id.text().strip(),
                "major": self.meta_major.text().strip(),
                "supervisor": self.meta_supervisor.text().strip(),
            }
        )
