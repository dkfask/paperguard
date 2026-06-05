"""Workspace for built-in help and resources."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget


class HelpWorkspaceView(QWidget):
    open_demo_requested = pyqtSignal()
    open_sample_template_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("资源与帮助")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        browser = QTextBrowser()
        browser.setHtml(
            """
            <h3>推荐使用顺序</h3>
            <ol>
              <li>教师进入模板中心，创建、导入或修改论文模板。</li>
              <li>学生从模板创建项目，在项目写作区逐章补充内容。</li>
              <li>进入检查与导出，修复问题后生成 DOCX。</li>
            </ol>
            <h3>内置资源</h3>
            <ul>
              <li>本科毕业论文模板</li>
              <li>示例学生项目</li>
              <li>演示导出参考文档</li>
            </ul>
            <h3>当前支持</h3>
            <p>支持基础富文本、列表、引用、图片、表格、目录和章节标题导出；复杂页内布局、批注和交叉引用暂未纳入本阶段范围。</p>
            """
        )
        layout.addWidget(browser, 1)

        btn_demo = QPushButton("打开示例项目")
        btn_demo.clicked.connect(self.open_demo_requested.emit)
        layout.addWidget(btn_demo)

        btn_template = QPushButton("加载内置模板")
        btn_template.clicked.connect(self.open_sample_template_requested.emit)
        layout.addWidget(btn_template)
