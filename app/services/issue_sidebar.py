"""IssueSidebar — 实时检查 issue 列表（Grammarly 风格侧边栏）。

Sprint 1.5 实时约束核心 UI 组件。展示 ``QtIssue[]`` 列表，每行带：
- 严重度图标（首字母: E/W/I）
- 错误信息 message
- 位置 location_text
- 整行按严重度上底色

Signals:
    issue_activated(QtIssue)  用户点击某一行时发（侧边栏让主视图滚动到对应段）
    fix_requested(QtIssue)    用户点击底部"修复"按钮时发（基于当前选中行）

设计要点：
- QListWidget 存 row（每行 QListWidgetItem，UserRole 存 QtIssue 引用）。
- 修复按钮单一：底部"修复"按钮（点当前选中行）。hover/selection-based 多按钮
  V1 不做（V1.1 引入 row-level inline button）。
- 颜色映射：error=#FFCDD2 / warning=#FFF9C4 / info=#BBDEFB（Material 浅色系）。
- 全主线程（与 ContentEditor 同事件循环），无 background worker。

Public API:
    IssueSidebar            QWidget 子类
    update_issues(issues)   全量替换列表（清空 + 重建）
    clear()                 清空列表
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.reporters.pyqt_reporter import QtIssue


# Severity → 浅色底色（Material 100 系）
_SEVERITY_BG: dict[str, str] = {
    "error": "#FFCDD2",
    "warning": "#FFF9C4",
    "info": "#BBDEFB",
}

# Severity → 图标字符（E/W/I）+ 中文 label
_SEVERITY_ICON: dict[str, str] = {
    "error": "E",
    "warning": "W",
    "info": "I",
}


class IssueSidebar(QWidget):
    """实时检查 issue 列表侧边栏。

    Signals:
        issue_activated(QtIssue): 用户点击某一行时发出
        fix_requested(QtIssue):   用户点击"修复"按钮时发出（基于当前选中行）
    """

    issue_activated = pyqtSignal(QtIssue)
    fix_requested = pyqtSignal(QtIssue)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._issues: list[QtIssue] = []
        self._list = QListWidget(self)
        self._fix_button = QPushButton("修复", self)
        # 修复按钮默认 disabled — 选中行后启用
        self._fix_button.setEnabled(False)

        # 信号连接
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._fix_button.clicked.connect(self._on_fix_button_clicked)

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._list)
        layout.addWidget(self._fix_button)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def update_issues(self, issues: list[QtIssue]) -> None:
        """全量替换列表：清空 + 按入参顺序重建（保留 QtIssue 引用）。"""
        self._list.clear()
        self._issues = list(issues)
        for issue in self._issues:
            item = QListWidgetItem(self._format_row(issue))
            color_hex = _SEVERITY_BG.get(issue.severity)
            if color_hex is not None:
                item.setBackground(QBrush(QColor(color_hex)))
            # UserRole 存 QtIssue 引用（信号 emit 时取回）
            item.setData(Qt.UserRole, issue)
            self._list.addItem(item)
        # 列表变化后，修复按钮可能从有选中 → 无选中
        self._on_selection_changed()

    def clear(self) -> None:
        """清空列表。"""
        self._list.clear()
        self._issues = []
        self._fix_button.setEnabled(False)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _format_row(issue: QtIssue) -> str:
        """行文本格式: ``[E] message @ location_text``。"""
        icon = _SEVERITY_ICON.get(issue.severity, "?")
        location = issue.location_text or ""
        if location:
            return f"[{icon}] {issue.message} @ {location}"
        return f"[{icon}] {issue.message}"

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """用户点击行 → 发出 issue_activated。"""
        issue = item.data(Qt.UserRole)
        if isinstance(issue, QtIssue):
            self.issue_activated.emit(issue)

    def _on_selection_changed(self) -> None:
        """选中状态变化 → 同步修复按钮 enabled 状态。"""
        has_selection = self._list.currentItem() is not None
        self._fix_button.setEnabled(has_selection)

    def _on_fix_button_clicked(self) -> None:
        """用户点修复按钮 → 发出 fix_requested（取当前选中行）。"""
        item = self._list.currentItem()
        if item is None:
            return
        issue = item.data(Qt.UserRole)
        if isinstance(issue, QtIssue):
            self.fix_requested.emit(issue)


__all__ = ["IssueSidebar"]
