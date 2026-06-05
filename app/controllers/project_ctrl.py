"""Controller for student thesis projects."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.project import Project
from app.models.template import Template
from app.services.content_blocks import section_content_from_html


PROJECT_EXTENSION = ".pprj"


class ProjectController:
    """Project workflow logic."""

    def __init__(self):
        self.project = Project()
        self.template: Template | None = None

    def set_template(self, template: Template, reset_project: bool = False):
        self.template = template
        if reset_project:
            self.project = Project()
        self.project.template_name = template.name
        self.project.template_path = template.file_path or ""
        self.project.template_version = template.version
        self.ensure_template_sections()

    def ensure_template_sections(self):
        if not self.template:
            return
        for _, node, _ in self.template.flatten_structure():
            if not node.auto_generate:
                self.project.get_content(node.id)

    def new_from_template(self, template: Template) -> Project:
        self.project = Project()
        self.set_template(template)
        return self.project

    def open_project(self, file_path: str | Path) -> Project:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"项目文件不存在: {file_path}")
        self.project = Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
        self.project.file_path = str(path)
        return self.project

    def save_project(self, file_path: str | Path | None = None) -> str:
        path = file_path or self.project.file_path
        if not path:
            raise ValueError("未指定保存路径")
        path = Path(path)
        if path.suffix.lower() != PROJECT_EXTENSION:
            path = path.with_suffix(PROJECT_EXTENSION)
        path.write_text(
            json.dumps(self.project.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.project.file_path = str(path)
        return str(path)

    def update_content(self, section_id: str, html: str):
        self.project.sections[section_id] = section_content_from_html(section_id, html)

    def set_metadata(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self.project, key):
                setattr(self.project, key, value)
