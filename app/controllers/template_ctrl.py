"""Controller for template CRUD and structure operations."""

from __future__ import annotations

from pathlib import Path

from app.models.template import SectionNode, Template
from app.services.template_parser import load_template, save_template


class TemplateController:
    @staticmethod
    def create_new(name: str = "新模板") -> Template:
        return Template(name=name)

    @staticmethod
    def open(file_path: str | Path) -> Template:
        return load_template(file_path)

    @staticmethod
    def save(template: Template, file_path: str | Path) -> str:
        save_template(template, file_path)
        return str(file_path)

    @staticmethod
    def add_section(template: Template, parent_id: str = "", label: str = "新小节") -> SectionNode:
        node = SectionNode(id=_generate_id(), label=label, type="section")
        if parent_id:
            parent = template.find_section(parent_id)
            if parent:
                parent.subsections.append(node)
        else:
            template.structure.append(node)
        return node

    @staticmethod
    def remove_section(template: Template, section_id: str) -> bool:
        def remove_from(nodes: list[SectionNode]) -> bool:
            for index, node in enumerate(nodes):
                if node.id == section_id:
                    nodes.pop(index)
                    return True
                if node.subsections and remove_from(node.subsections):
                    return True
            return False

        return remove_from(template.structure)

    @staticmethod
    def move_section(template: Template, section_id: str, direction: int) -> bool:
        def move_within(nodes: list[SectionNode]) -> bool:
            for index, node in enumerate(nodes):
                if node.id == section_id:
                    target = index + direction
                    if 0 <= target < len(nodes):
                        nodes[index], nodes[target] = nodes[target], nodes[index]
                        return True
                    return False
                if node.subsections and move_within(node.subsections):
                    return True
            return False

        return move_within(template.structure)


_id_counter = 0


def _generate_id() -> str:
    global _id_counter
    _id_counter += 1
    return f"sec_{_id_counter}"
