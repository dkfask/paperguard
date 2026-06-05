"""Dashboard workspace for first-run guidance and recent tasks."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.services.recent_items_service import RecentItem


class DashboardView(QWidget):
    create_template_requested = pyqtSignal()
    create_project_requested = pyqtSignal()
    open_demo_requested = pyqtSignal()
    open_recent_template_requested = pyqtSignal(str)
    open_recent_project_requested = pyqtSignal(str)
    role_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_role = "student"
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        title_column = QVBoxLayout()
        self.header = QLabel("论文模板规范软件工作台")
        self.header.setStyleSheet("font-size: 24px; font-weight: bold;")
        title_column.addWidget(self.header)
        self.subtitle = QLabel("从这里进入教师模板流或学生写作流，也可以直接打开示例项目完成演示。")
        self.subtitle.setStyleSheet("color: #555; font-size: 13px;")
        title_column.addWidget(self.subtitle)
        header_row.addLayout(title_column, 1)

        self.role_badge = QLabel()
        header_row.addWidget(self.role_badge, alignment=Qt.AlignTop)
        layout.addLayout(header_row)

        cards = QHBoxLayout()
        cards.addWidget(self._build_role_card("我是教师", "设计论文结构、样式和页面规范，再发布模板。", "teacher"))
        cards.addWidget(self._build_role_card("我是学生", "从模板创建论文项目，逐章填写内容并检查导出。", "student"))
        layout.addLayout(cards)

        action_row = QHBoxLayout()
        self.btn_create_template = QPushButton("创建模板")
        self.btn_create_template.clicked.connect(self.create_template_requested.emit)
        action_row.addWidget(self.btn_create_template)

        self.btn_create_project = QPushButton("创建论文项目")
        self.btn_create_project.clicked.connect(self.create_project_requested.emit)
        action_row.addWidget(self.btn_create_project)

        self.btn_open_demo = QPushButton("打开示例项目")
        self.btn_open_demo.clicked.connect(self.open_demo_requested.emit)
        action_row.addWidget(self.btn_open_demo)
        action_row.addStretch()
        layout.addLayout(action_row)

        content_row = QHBoxLayout()

        recent_column = QVBoxLayout()
        recent_templates_label = QLabel("最近模板")
        recent_templates_label.setStyleSheet("font-weight: bold;")
        recent_column.addWidget(recent_templates_label)
        self.recent_templates = QListWidget()
        self.recent_templates.itemDoubleClicked.connect(
            lambda item: self.open_recent_template_requested.emit(item.data(Qt.UserRole))
        )
        recent_column.addWidget(self.recent_templates)

        recent_projects_label = QLabel("最近项目")
        recent_projects_label.setStyleSheet("font-weight: bold;")
        recent_column.addWidget(recent_projects_label)
        self.recent_projects = QListWidget()
        self.recent_projects.itemDoubleClicked.connect(
            lambda item: self.open_recent_project_requested.emit(item.data(Qt.UserRole))
        )
        recent_column.addWidget(self.recent_projects)
        content_row.addLayout(recent_column, 1)

        guide_column = QVBoxLayout()
        guide_label = QLabel("演示与帮助")
        guide_label.setStyleSheet("font-weight: bold;")
        guide_column.addWidget(guide_label)
        self.guide = QTextBrowser()
        self.guide.setOpenExternalLinks(False)
        guide_column.addWidget(self.guide)
        content_row.addLayout(guide_column, 1)

        layout.addLayout(content_row, 1)
        self.set_role("student")
        self._set_default_guide()

    def _build_role_card(self, title: str, desc: str, role: str) -> QWidget:
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(
            "QFrame { background: white; border: 1px solid #d8d8d8; border-radius: 8px; }"
        )
        layout = QVBoxLayout(card)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title_label)
        desc_label = QLabel(desc)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #666;")
        layout.addWidget(desc_label)
        button = QPushButton("进入该角色流程")
        button.clicked.connect(lambda: self.role_selected.emit(role))
        layout.addWidget(button)
        layout.addStretch()
        return card

    def set_role(self, role: str):
        self.current_role = role
        if role == "teacher":
            self.role_badge.setText("当前推荐入口：教师流程")
            self.role_badge.setStyleSheet(
                "background:#eef6ea; color:#245c1d; padding:8px 12px; border-radius:14px; font-weight:bold;"
            )
        else:
            self.role_badge.setText("当前推荐入口：学生流程")
            self.role_badge.setStyleSheet(
                "background:#f5f0e6; color:#7a4b00; padding:8px 12px; border-radius:14px; font-weight:bold;"
            )

    def set_recent_items(
        self,
        templates: list[RecentItem],
        projects: list[RecentItem],
        has_history: bool,
    ):
        self._populate_list(self.recent_templates, templates, empty_text="还没有最近模板")
        self._populate_list(self.recent_projects, projects, empty_text="还没有最近项目")
        if has_history:
            self._set_default_guide()
        else:
            self.guide.setHtml(
                """
                <h3>欢迎第一次使用</h3>
                <p>建议先从“打开示例项目”开始，1 分钟内即可体验模板、写作、检查和导出的完整链路。</p>
                <ol>
                  <li>点击“打开示例项目”</li>
                  <li>切换到“项目写作”，修改摘要或第一章内容</li>
                  <li>切换到“检查与导出”，查看问题并生成 DOCX</li>
                </ol>
                """
            )

    def _populate_list(self, widget: QListWidget, items: list[RecentItem], empty_text: str):
        widget.clear()
        if not items:
            empty_item = QListWidgetItem(empty_text)
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            widget.addItem(empty_item)
            return
        for item in items:
            label = item.title
            if item.subtitle:
                label += f"\n{item.subtitle}"
            if item.last_opened_at:
                label += f"\n最近打开：{item.last_opened_at}"
            list_item = QListWidgetItem(label)
            list_item.setData(Qt.UserRole, item.path)
            widget.addItem(list_item)

    def _set_default_guide(self):
        self.guide.setHtml(
            """
            <h3>3 步完成一次演示</h3>
            <ol>
              <li>打开示例项目，查看已准备好的本科毕业论文模板。</li>
              <li>在项目写作区填写摘要和第一章内容。</li>
              <li>切换到检查与导出，执行校验并导出 DOCX。</li>
            </ol>
            <p>双击最近模板或最近项目，也可以快速回到上次工作。</p>
            """
        )
