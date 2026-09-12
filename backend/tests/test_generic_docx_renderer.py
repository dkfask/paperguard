"""Tests for the generic-to-legacy DOCX rendering adapter."""

from pathlib import Path

from docx import Document

from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentProject,
    DocumentTemplate,
    TemplateDefinition,
    TemplateSection,
    TemplateVersion,
)
from app.domain.documents.specs import SpecVersion
from app.domain.documents.schemas import ContentBlock
from app.services.generic_docx_renderer import render_document_project_to_docx


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "minimal.docx"


def test_generic_project_is_rendered_by_existing_docx_exporter(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))

    definition = TemplateDefinition(
        source_docx="source.docx",
        sections=[TemplateSection(key="intro", title="Introduction", kind="content")],
    )
    template = DocumentTemplate(
        id="generic-template",
        document_type_id="application",
        name="Application Template",
        current_version_id="generic-template@1",
    )
    template_version = TemplateVersion(
        id="generic-template@1",
        template_id=template.id,
        version="1",
        status="published",
        definition=definition,
        source_asset_id="source-1",
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
    )
    package = DocumentPackage(
        id="package-1",
        document_type_id=template.document_type_id,
        template_version_id=template_version.id,
        spec_version_id="spec@1",
        renderer_version=template_version.renderer_version,
        rule_engine_version=template_version.rule_engine_version,
        status="published",
    )
    spec_version = SpecVersion(
        id="spec@1",
        spec_set_id="spec",
        version="1",
        status="published",
    )
    project = DocumentProject(
        id="project-1",
        package_id=package.id,
        document_type_id=package.document_type_id,
        template_version_id=package.template_version_id,
        spec_version_id=package.spec_version_id,
        renderer_version=package.renderer_version,
        rule_engine_version=package.rule_engine_version,
        owner_id="user-1",
        title="Filled Application",
        sections={"intro": [ContentBlock(id="block-1", text="Application body") ]},
    )

    output = tmp_path / "output.docx"
    render_document_project_to_docx(
        project,
        package,
        template,
        template_version,
        spec_version,
        output,
    )

    document = Document(output)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    assert "Introduction" in paragraphs
    assert "Application body" in paragraphs
    assert output.stat().st_size > 0
