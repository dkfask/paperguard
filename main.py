#!/usr/bin/env python3
"""Desktop entrypoint for the thesis template specification tool."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from app.views.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("论文模板规范软件")
    app.setOrganizationName("ThesisTools")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(
        """
        QMainWindow {
            background-color: #f5f7fa;
        }
        QListWidget, QTreeWidget, QTextBrowser, QTextEdit, QLineEdit {
            background-color: white;
            border: 1px solid #d8dee4;
            border-radius: 6px;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #d8dee4;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 14px;
            background-color: white;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }
        QPushButton {
            background-color: #f7faff;
            border: 1px solid #c7d2e0;
            border-radius: 6px;
            padding: 6px 12px;
        }
        QPushButton:hover {
            background-color: #e9f2ff;
        }
        QMenuBar, QStatusBar, QToolBar {
            background-color: white;
        }
        """
    )

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
