"""Tests for the generic DOCX renderer and its legacy compatibility adapter."""

import hashlib
import zipfile
from pathlib import Path

from docx import Document

from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentProject,
    DocumentTemplate,
    TemplateAsset,
    TemplateDefinition,
    TemplateSection,
    TemplateVersion,
)
from app.domain.documents.specs import SpecVersion
from app.domain.documents.schemas import ContentBlock
from app.services.generic_docx_renderer import render_document_project_to_docx
from app.services.template_engine import parse_docx_template


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "minimal.docx"
ANCHORED_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "generic-anchored.docx"


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


def test_anchored_fixture_replaces_dynamic_content_and_preserves_static_package(tmp_path, monkeypatch) -> None:
    source = tmp_path / ANCHORED_FIXTURE.name
    source.write_bytes(ANCHORED_FIXTURE.read_bytes())
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))
    source_bytes = source.read_bytes()
    asset = TemplateAsset(
        id="anchored-source",
        storage_key=source.name,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(source_bytes),
        checksum=hashlib.sha256(source_bytes).hexdigest(),
        purpose="template-source",
        scan_status="validated",
    )
    candidate = parse_docx_template(
        source,
        candidate_id="anchored-candidate",
        template_id="anchored-template",
        filename=source.name,
        source_asset=asset,
    )
    assert {anchor.key for anchor in candidate.definition.anchors} >= {"summary", "category", "body"}
    assert any(section.key == "body" for section in candidate.definition.sections)

    template = DocumentTemplate(
        id="anchored-template",
        document_type_id="application",
        name="Anchored application",
        current_version_id="anchored-template@1",
    )
    version = TemplateVersion(
        id="anchored-template@1",
        template_id=template.id,
        version="1",
        status="published",
        definition=candidate.definition,
        source_asset_id=asset.id,
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
    )
    package = DocumentPackage(
        id="anchored-package",
        document_type_id=template.document_type_id,
        template_version_id=version.id,
        spec_version_id="spec@1",
        renderer_version=version.renderer_version,
        rule_engine_version=version.rule_engine_version,
        status="published",
    )
    project = DocumentProject(
        id="anchored-project",
        package_id=package.id,
        document_type_id=package.document_type_id,
        template_version_id=version.id,
        spec_version_id=package.spec_version_id,
        renderer_version=package.renderer_version,
        rule_engine_version=package.rule_engine_version,
        owner_id="user-1",
        title="Anchored output",
        field_values={"summary": "字段摘要", "category": "A"},
        sections={"body": [ContentBlock(id="body-1", text="正文内容") ]},
    )

    output = tmp_path / "anchored-output.docx"
    render_document_project_to_docx(project, package, template, version, None, output)

    with zipfile.ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        header_xml = archive.read("word/header1.xml").decode("utf-8")
        footer_xml = archive.read("word/footer1.xml").decode("utf-8")
        names = set(archive.namelist())
    assert "字段摘要" in document_xml
    assert "正文内容" in document_xml
    assert "静态模板内容：此段必须在导出文件中保留。" in document_xml
    assert "PaperGuard fixture" in document_xml
    assert "{{summary}}" not in document_xml
    assert "{{section:body}}" not in document_xml
    assert "固定页眉：PaperGuard 通用模板" in header_xml
    assert "固定页脚：模板版本 1" in footer_xml
    assert "word/styles.xml" in names
    assert "word/numbering.xml" in names
