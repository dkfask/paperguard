"""Tests for the generic DOCX renderer and its legacy compatibility adapter."""

import base64
import hashlib
import io
import zipfile
from pathlib import Path

from docx import Document
from docx.shared import Pt
import pytest

from app.domain.documents.schemas import (
    DocumentPackage,
    DocumentProject,
    DocumentTemplate,
    StyleDefinition,
    TemplateAsset,
    TemplateAnchor,
    TemplateField,
    TemplateDefinition,
    TemplateSection,
    TemplateVersion,
)
from app.domain.documents.specs import SpecVersion
from app.domain.documents.schemas import ContentBlock
from app.services.generic_docx_renderer import inspect_document_project_docx_output, render_document_project_to_docx
from app.services.template_engine import parse_docx_template
from app.services.docx_output_check import INSPECTOR_VERSION, verify_report_binding, output_check_identity


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "minimal.docx"
ANCHORED_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "templates" / "generic-anchored.docx"


def _simple_anchor_sample(tmp_path, monkeypatch, *, fixed_objects=False):
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))
    source = tmp_path / "simple-anchor.docx"
    document = Document()
    document.add_paragraph("保留的静态文字")
    if fixed_objects:
        document.add_table(rows=1, cols=1).cell(0, 0).text = "固定表格"
        document.sections[0].header.paragraphs[0].text = "固定页眉"
        document.sections[0].footer.paragraphs[0].text = "固定页脚"
    document.add_paragraph("姓名：{{name}}")
    document.add_paragraph("{{section:body}}")
    document.save(source)
    definition = TemplateDefinition(source_docx=source.name, export_strategy="source_template", fields=[TemplateField(key="name", label="姓名")], sections=[TemplateSection(key="body", title="正文", allow_user_add=True)], anchors=[
        TemplateAnchor(key="name", kind="placeholder", selector="{{name}}"),
        TemplateAnchor(key="body", kind="section_placeholder", selector="{{section:body}}", section_key="body"),
    ])
    template = DocumentTemplate(id="simple-template", name="测试模板", document_type_id="report", current_version_id="simple-template@1")
    version = TemplateVersion(id=template.current_version_id, template_id=template.id, version="1", definition=definition, source_asset_id="source", renderer_version="generic-docx-v1", rule_engine_version="generic-check-v1")
    package = DocumentPackage(id="simple-package", document_type_id="report", template_version_id=version.id, spec_version_id="simple-spec@1", renderer_version=version.renderer_version, rule_engine_version=version.rule_engine_version)
    project = DocumentProject(id="simple-project", owner_id="user-admin", title="锚点中文下载样稿", package_id=package.id, document_type_id="report", template_version_id=version.id, spec_version_id=package.spec_version_id, renderer_version=package.renderer_version, rule_engine_version=package.rule_engine_version, field_values={"name": "本地验收学生"}, sections={"body": [ContentBlock(id="p1", text="第一段正文", order=0), ContentBlock(id="p2", text="第二段正文", order=1)]})
    spec = SpecVersion(id="simple-spec@1", spec_set_id="simple-spec", version="1")
    output = tmp_path / "simple-output.docx"
    render_document_project_to_docx(project, package, template, version, spec, output)
    return output, project, package, template, version, spec


def test_anchored_report_binds_complete_identity_and_output_bytes(tmp_path, monkeypatch):
    output, project, package, template, version, spec = _simple_anchor_sample(tmp_path, monkeypatch)
    data = output.read_bytes()
    report = inspect_document_project_docx_output(data, project, package, template, version, spec, context_kind="document-sync")
    assert report["status"] == "pass"
    assert report["inspector_version"] == INSPECTOR_VERSION
    assert verify_report_binding(report, data)
    assert report["project_id"] == project.id
    assert report["project_revision"] == project.revision
    assert report["template_id"] == template.id
    assert report["template_version"] == version.version
    assert report["source_template_sha256"] == hashlib.sha256((tmp_path / "simple-anchor.docx").read_bytes()).hexdigest()
    changed = project.model_copy(update={"id": "another-project", "field_values": {"name": "另一个学生"}})
    other = inspect_document_project_docx_output(data, changed, package, template, version, spec, context_kind="document-sync")
    assert output_check_identity(report) != output_check_identity(other)
    assert other["status"] == "fail"


@pytest.mark.parametrize("mutation", ["static_style", "field_style", "body_style", "duplicate", "order", "wrong_field_position", "chart"])
def test_anchored_output_rejects_format_order_position_and_chart_mutations(tmp_path, monkeypatch, mutation):
    output, project, package, template, version, spec = _simple_anchor_sample(tmp_path, monkeypatch)
    document = Document(output)
    if mutation.endswith("style"):
        index = {"static_style": 0, "field_style": 1, "body_style": 2}[mutation]
        document.paragraphs[index].runs[0].font.size = Pt(25)
    elif mutation == "duplicate":
        document.add_paragraph("第一段正文")
    elif mutation == "order":
        p1, p2 = document.paragraphs[2:4]
        p1._p.addprevious(p2._p)
    elif mutation == "wrong_field_position":
        document.paragraphs[0].runs[0].text = "本地验收学生"
        document.paragraphs[1].runs[0].text = "姓名：另一个学生"
    else:
        from lxml import etree
        etree.SubElement(document.paragraphs[2]._p, "{http://schemas.openxmlformats.org/drawingml/2006/chart}chart")
    mutated = io.BytesIO()
    document.save(mutated)
    report = inspect_document_project_docx_output(mutated.getvalue(), project, package, template, version, spec, context_kind="document-sync")
    assert report["status"] != "pass", mutation
    assert not verify_report_binding(report, mutated.getvalue())


def test_anchor_sync_and_async_api_deliver_checked_files_and_reject_tampering(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Store
    from app.domain.documents.specs import SpecSet
    from app.services.document_render_jobs import run_document_render_job, get_document_artifact, artifact_storage

    output, project, package, template, version, spec = _simple_anchor_sample(tmp_path, monkeypatch)
    monkeypatch.setenv("PAPERGUARD_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    store = Store()
    spec_set = SpecSet(id=spec.spec_set_id, name="无附加规范", document_type_id="report", current_version_id=spec.id)
    store.save_document_package(package, template, version, spec_set, spec)
    store.save_document_project(project)
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    sync = client.post(f"/api/document-projects/{project.id}/render", headers=headers)
    assert sync.status_code == 200, sync.text
    assert sync.headers["x-paperguard-output-check"] == "pass"
    assert hashlib.sha256(sync.content).hexdigest() == sync.headers["x-paperguard-output-sha256"]
    queued = client.post(f"/api/document-projects/{project.id}/render-jobs", headers=headers)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["id"]
    completed = run_document_render_job(store, job_id)
    assert completed["status"] == "succeeded", completed
    download_url = f"/api/document-projects/{project.id}/render-jobs/{job_id}/artifact"
    downloaded = client.get(download_url, headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert "filename*=UTF-8''" in downloaded.headers["content-disposition"]
    artifact = get_document_artifact(store, completed["artifact_id"])
    assert verify_report_binding(artifact.output_check, downloaded.content)

    # A successful render is bound to the exact project revision that created it.
    # Editing the project must invalidate the old artifact before any file read.
    current_project = store.get_document_project(project.id)
    changed_project = current_project.model_copy(update={"title": "已修改后的项目标题"})
    assert store.update_document_project_if_revision(changed_project, current_project.revision) is not None
    assert client.get(download_url, headers=headers).status_code == 409

    artifact_storage().put_bytes(artifact.storage_key, b"changed", artifact.content_type)
    assert client.get(download_url, headers=headers).status_code == 409

    import app.services.generic_docx_renderer as renderer_service
    import app.services.document_render_jobs as job_service
    real_render = renderer_service.render_document_project_to_docx
    def corrupt_render(*args, **kwargs):
        result = real_render(*args, **kwargs)
        changed = Document(result)
        changed.paragraphs[1].runs[0].font.size = Pt(31)
        changed.save(result)
        return result
    monkeypatch.setattr("app.api.document_projects.render_document_project_to_docx", corrupt_render)
    blocked = client.post(f"/api/document-projects/{project.id}/render?force=true", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["output_check"]["status"] == "fail"
    monkeypatch.setattr(job_service, "render_document_project_to_docx", corrupt_render)
    queued = client.post(f"/api/document-projects/{project.id}/render-jobs?force=true", headers=headers)
    assert queued.status_code == 202
    failed = run_document_render_job(store, queued.json()["id"])
    assert failed["status"] == "failed"
    assert not failed.get("artifact_id")
    assert client.get(f"/api/document-projects/{project.id}/render-jobs/{queued.json()['id']}/artifact", headers=headers).status_code == 409


@pytest.mark.parametrize("mutation", [None, "table_font", "image_size", "image_relationship", "image_duplicate_id"])
def test_multiple_section_anchors_and_static_image_preserve_position(tmp_path, monkeypatch, mutation):
    from PIL import Image
    from lxml import etree
    from app.services.document_assets import document_upload_dir
    output, project, package, template, version, spec = _simple_anchor_sample(tmp_path, monkeypatch)
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    upload_dir = document_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    image = upload_dir / f"{project.id}-figure.png"
    static_image = tmp_path / "static.png"
    Image.new("RGB", (20, 10), "red").save(image)
    Image.new("RGB", (10, 20), "blue").save(static_image)
    source = tmp_path / "simple-anchor.docx"
    document = Document(source)
    document.add_picture(str(static_image))
    document.add_paragraph("{{section:appendix}}")
    document.save(source)
    definition = version.definition.model_copy(update={"sections": [*version.definition.sections, TemplateSection(key="appendix", title="附录")], "anchors": [*version.definition.anchors, TemplateAnchor(key="appendix", kind="section_placeholder", selector="{{section:appendix}}", section_key="appendix")]})
    version = version.model_copy(update={"definition": definition})
    def blocks(prefix):
        return [ContentBlock(id=f"{prefix}-table", kind="table", data={"rows": 1, "cols": 1, "cells": [[prefix]], "caption": prefix}, order=0), ContentBlock(id=f"{prefix}-image", kind="image", data={"url": f"/media/uploads/{image.name}"}, order=1)]
    project = project.model_copy(update={"sections": {"body": blocks("body"), "appendix": blocks("appendix")}})
    render_document_project_to_docx(project, package, template, version, spec, output)
    if mutation:
        doc = Document(output)
        if mutation == "table_font":
            doc.tables[0].cell(0, 0).paragraphs[0].runs[0].font.size = Pt(31)
        elif mutation == "image_size":
            doc.inline_shapes[0].height = 5
        elif mutation == "image_duplicate_id":
            ids = doc.element.xpath(".//wp:docPr")
            ids[0].set("id", ids[1].get("id"))
        else:
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
            blips = doc.element.xpath(".//a:blip")
            blips[0].set(f"{{{ns['r']}}}embed", blips[1].get(f"{{{ns['r']}}}embed"))
        doc.save(output)
    report = inspect_document_project_docx_output(output.read_bytes(), project, package, template, version, spec, context_kind="sync")
    assert report["status"] == ("pass" if mutation is None else "fail"), [item for item in report["checks"] if item["status"] != "pass"]


@pytest.mark.parametrize("override", ["format", "layout", "template_style", "section_style"])
def test_anchored_output_cannot_pass_unapplied_format_overrides(tmp_path, monkeypatch, override):
    output, project, package, template, version, spec = _simple_anchor_sample(tmp_path, monkeypatch)
    if override == "format":
        spec = spec.model_copy(update={"format_overrides": {"body": {"size_pt": 31}}})
    elif override == "layout":
        spec = spec.model_copy(update={"layout_overrides": {"margin_left_cm": 6}})
    elif override == "template_style":
        version = version.model_copy(update={"definition": version.definition.model_copy(update={"styles": {"body": StyleDefinition(size_pt=31)}})})
    else:
        section = version.definition.sections[0].model_copy(update={"style": StyleDefinition(size_pt=31)})
        version = version.model_copy(update={"definition": version.definition.model_copy(update={"sections": [section]})})
    report = inspect_document_project_docx_output(output.read_bytes(), project, package, template, version, spec, context_kind="sync")
    assert report["status"] == "unverifiable"
    assert report["unsupported"]


def test_queued_anchor_render_rejects_source_replaced_after_request(tmp_path, monkeypatch):
    from app.db import Store
    from app.services.document_render_jobs import create_document_render_job, run_document_render_job
    output, project, package, template, version, spec = _simple_anchor_sample(tmp_path, monkeypatch)
    monkeypatch.setenv("PAPERGUARD_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    project = project.model_copy(update={"id": "queued-source-change"})
    store = Store()
    record = create_document_render_job(store, project, {"package": package, "template": template, "template_version": version, "spec_version": spec}, force=True, actor_id="user-admin")
    source = tmp_path / "simple-anchor.docx"
    changed = Document(source)
    changed.paragraphs[0].text = "请求之后被替换的模板内容"
    changed.save(source)
    result = run_document_render_job(store, record["id"])
    assert result["status"] == "failed"
    assert not result.get("artifact_id")


def test_anchor_report_rejects_source_asset_checksum_mismatch(tmp_path, monkeypatch):
    output, project, package, template, version, spec = _simple_anchor_sample(tmp_path, monkeypatch)
    version = version.model_copy(update={"extensions": {"source_asset": {"checksum": "0" * 64, "storage_key": "simple-anchor.docx"}}})
    report = inspect_document_project_docx_output(output.read_bytes(), project, package, template, version, spec, context_kind="sync")
    assert report["status"] == "fail"
    assert any(item["rule"] == "template.source_checksum" and item["status"] == "fail" for item in report["checks"])


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
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
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
        sections={"body": [
            ContentBlock(id="body-1", text="正文内容"),
            ContentBlock(id="table-1", kind="table", data={"rows": 2, "cols": 2, "cells": [["项目", "结果"], ["温度", "25"]], "caption": "实验数据"}),
            ContentBlock(id="image-1", kind="image", data={"url": "/media/uploads/anchored-project-figure.png", "caption": "实验图"}),
        ]},
    )

    image_path = tmp_path / "data" / "uploads" / "anchored-project-figure.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="))

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

    report = inspect_document_project_docx_output(
        output.read_bytes(), project, package, template, version, None, context_kind="sync"
    )
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]
    assert not report["unsupported"]
    assert any(item["rule"] == "structure.anchor_table_dimensions" and item["status"] == "pass" for item in report["checks"])
    assert any(item["rule"] == "format.anchor_image_dimensions" and item["status"] == "pass" for item in report["checks"])

    mutated = Document(output)
    summary = next(paragraph for paragraph in mutated.paragraphs if "字段摘要" in paragraph.text)
    summary.text = "被篡改的摘要"
    mutated_bytes = io.BytesIO()
    mutated.save(mutated_bytes)
    bad_report = inspect_document_project_docx_output(
        mutated_bytes.getvalue(), project, package, template, version, None, context_kind="sync"
    )
    assert bad_report["status"] == "fail"
    assert any(item["rule"] == "content.anchor_field" and item["status"] == "fail" for item in bad_report["checks"])


@pytest.mark.parametrize("mutation", [None, "header", "footer", "table"])
def test_admitted_anchor_static_objects_are_independently_checked(tmp_path, monkeypatch, mutation):
    from app.services.template_admission import assess_template_definition

    output, project, package, template, version, spec = _simple_anchor_sample(
        tmp_path, monkeypatch, fixed_objects=True,
    )
    admission = assess_template_definition(
        template_id=template.id, version=version.version,
        definition=version.definition,
        source_data=(tmp_path / "simple-anchor.docx").read_bytes(),
    )
    assert admission["can_use_for_draft"]
    assert not admission["can_claim_compliant_delivery"]
    if mutation:
        document = Document(output)
        if mutation == "table":
            document.tables[0].cell(0, 0).width = Pt(24)
        else:
            getattr(document.sections[0], mutation).paragraphs[0].runs[0].font.size = Pt(24)
        document.save(output)
    report = inspect_document_project_docx_output(
        output.read_bytes(), project, package, template, version, spec,
        context_kind="document-sync",
    )
    assert report["status"] == ("fail" if mutation else "pass")
    if mutation == "table":
        assert any(item["rule"] == "template.anchor_story_structure" and item["status"] == "fail"
                   for item in report["checks"])
    elif mutation:
        fixed_part = f"word/{mutation}1.xml"
        assert any(item["rule"] == "template.static_package_preserved" and fixed_part in item["actual"]["changed"]
                   for item in report["checks"])
