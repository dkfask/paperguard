"""Tree navigation panel for thesis structure."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from app.models.template import SectionNode, Template


STATUS_HINTS = {
    "empty": "○",
    "started": "◐",
    "complete": "●",
    "warning": "!",
    "error": "✖",
}

TYPE_HINTS = {
    "cover": "封",
    "abstract": "摘",
    "toc": "目",
    "chapter": "章",
    "section": "节",
    "references": "引",
    "appendix": "附",
    "acknowledgements": "谢",
}


class TreePanel(QWidget):
    """Tree-based chapter navigation with progress states."""

    section_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.template: Template | None = None
        self.statuses: dict[str, str] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.header = QLabel("论文结构")
        self.header.setStyleSheet("font-weight: bold; padding: 8px; font-size: 14px;")
        layout.addWidget(self.header)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(20)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

    def load_template(self, template: Template, statuses: dict[str, str] | None = None):
        self.template = template
        self.statuses = statuses or {}
        self.tree.clear()
        for node in template.structure:
            self._add_tree_node(None, node)
        self.tree.expandAll()

    def update_statuses(self, statuses: dict[str, str]):
        if self.template is not None:
            self.load_template(self.template, statuses)

    def _add_tree_node(self, parent: QTreeWidgetItem | None, node: SectionNode):
        item = QTreeWidgetItem(parent or self.tree)
        item.setData(0, Qt.UserRole, node.id)
        item.setText(0, self._format_label(node))
        for child in node.subsections:
            self._add_tree_node(item, child)

    def _format_label(self, node: SectionNode) -> str:
        status = STATUS_HINTS.get(self.statuses.get(node.id, "empty"), "○")
        prefix = TYPE_HINTS.get(node.type, "节")
        label = f"{status} [{prefix}] {node.label}"
        if node.required:
            label += " *"
        return label

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        section_id = item.data(0, Qt.UserRole)
        if section_id:
            self.section_selected.emit(section_id)

    def select_section(self, section_id: str):
        def walk(parent=None):
            items = (
                [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
                if parent is None
                else [parent.child(i) for i in range(parent.childCount())]
            )
            for item in items:
                if item and item.data(0, Qt.UserRole) == section_id:
                    self.tree.setCurrentItem(item)
                    return True
                if item and walk(item):
                    return True
            return False

        walk()
