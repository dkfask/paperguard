"""Export controller for DOCX generation."""

from __future__ import annotations

from pathlib import Path

from app.models.project import Project
from app.models.template import Template
from app.services.docx_generator import DocxGenerator


class ExportController:
    """Handle user-facing export requests."""

    @staticmethod
    def export_to_docx(template: Template, project: Project, output_path: str | Path) -> str:
        generator = DocxGenerator(template, project)
        return generator.generate(str(output_path))

    @staticmethod
    def get_default_filename(project: Project) -> str:
        title = project.title.strip() or "论文"
        return f"{title}.docx"
