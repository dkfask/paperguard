"""Explicit DOCX anchor extraction and high-fidelity rendering tests."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.domain.documents.schemas import (
    ContentBlock,
    DocumentPackage,
    DocumentProject,
    DocumentTemplate,
    TemplateAsset,
    TemplateVersion,
)
from app.services.docx_anchors import DocxAnchorError, extract_docx_anchors, render_anchored_docx
from app.services.generic_docx_renderer import render_document_project_to_docx
from app.services.template_engine import parse_docx_template


def _content_control(tag_value: str, value: str) -> OxmlElement:
    sdt = OxmlElement("w:sdt")
    properties = OxmlElement("w:sdtPr")
    tag = OxmlElement("w:tag")
    tag.set(qn("w:val"), tag_value)
    properties.append(tag)
    sdt.append(properties)
    content = OxmlElement("w:sdtContent")
    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = value
    run.append(text)
    paragraph.append(run)
    content.append(paragraph)
    sdt.append(content)
    return sdt


def _merge_field(paragraph, name: str, result: str) -> None:
    begin = OxmlElement("w:r")
    begin_char = OxmlElement("w:fldChar")
    begin_char.set(qn("w:fldCharType"), "begin")
    begin.append(begin_char)
    instruction = OxmlElement("w:r")
    instruction_text = OxmlElement("w:instrText")
    instruction_text.set(qn("xml:space"), "preserve")
    instruction_text.text = f" MERGEFIELD {name} \\* MERGEFORMAT "
    instruction.append(instruction_text)
    separate = OxmlElement("w:r")
    separate_char = OxmlElement("w:fldChar")
    separate_char.set(qn("w:fldCharType"), "separate")
    separate.append(separate_char)
    value = OxmlElement("w:r")
    value_text = OxmlElement("w:t")
    value_text.text = result
    value.append(value_text)
    end = OxmlElement("w:r")
    end_char = OxmlElement("w:fldChar")
    end_char.set(qn("w:fldCharType"), "end")
    end.append(end_char)
    for child in (begin, instruction, separate, value, end):
        paragraph._p.append(child)


def _source_docx(path: Path) -> None:
    document = Document()
    document.sections[0].header.paragraphs[0].add_run("Header {{header_code}}")
    document.add_paragraph("保留的版式文字")

    placeholder = document.add_paragraph()
    placeholder.add_run("申请人：{{")
    placeholder.add_run("name}}")
    document.add_paragraph("{{section:summary}}")

    bookmark = document.add_paragraph("审核日期：")
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "21")
    start.set(qn("w:name"), "pg_review_date")
    bookmark._p.append(start)
    bookmark.add_run("旧日期")
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "21")
    bookmark._p.append(end)

    merge = document.add_paragraph("合并字段：")
    _merge_field(merge, "department", "旧部门")
    document._body._element.insert(
        len(document._body._element) - 1,
        _content_control("pg:field:department", "旧部门"),
    )
    document.save(path)


def test_extracts_explicit_anchor_kinds_and_stable_selectors(tmp_path: Path) -> None:
    source = tmp_path / "anchored.docx"
    _source_docx(source)

    anchors = extract_docx_anchors(source)
    kinds = {(anchor.kind, anchor.key, anchor.selector) for anchor in anchors}

    assert ("placeholder", "name", "{{name}}") in kinds
    assert ("section_placeholder", "summary", "{{section:summary}}") in kinds
    assert ("bookmark", "review_date", "pg_review_date") in kinds
    assert ("merge_field", "department", "department") in kinds
    assert ("content_control", "department", "pg:field:department") in kinds
    assert all(anchor.part.startswith("word/") for anchor in anchors)


def test_parser_and_renderer_preserve_source_package_while_replacing_anchors(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.docx"
    _source_docx(source)
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))

    data = source.read_bytes()
    asset = TemplateAsset(
        id="source-asset",
        storage_key=source.name,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        purpose="template-source",
        scan_status="validated",
    )
    candidate = parse_docx_template(
        source,
        candidate_id="candidate-1",
        template_id="template-1",
        filename=source.name,
        source_asset=asset,
    )
    assert {field.key for field in candidate.definition.fields} >= {"name", "department", "review_date"}
    assert any(section.key == "summary" for section in candidate.definition.sections)

    template = DocumentTemplate(
        id="template-1",
        document_type_id="application",
        name="Application",
        current_version_id="template-1@1",
    )
    version = TemplateVersion(
        id="template-1@1",
        template_id=template.id,
        version="1",
        status="published",
        definition=candidate.definition,
        source_asset_id=asset.id,
        renderer_version="generic-docx-v1",
        rule_engine_version="generic-check-v1",
    )
    package = DocumentPackage(
        id="package-1",
        document_type_id="application",
        template_version_id=version.id,
        spec_version_id="spec@1",
        renderer_version=version.renderer_version,
        rule_engine_version=version.rule_engine_version,
        status="published",
    )
    project = DocumentProject(
        id="project-1",
        package_id=package.id,
        document_type_id=package.document_type_id,
        template_version_id=version.id,
        spec_version_id=package.spec_version_id,
        renderer_version=package.renderer_version,
        rule_engine_version=package.rule_engine_version,
        owner_id="user-1",
        title="Filled application",
        field_values={
            "name": "李明 {{literal}}",
            "department": "工程学院",
            "review_date": "2026-09-12",
            "header_code": "APP-001",
        },
        sections={"summary": [ContentBlock(id="summary-1", text="申请摘要内容")]},
    )
    output = tmp_path / "output.docx"
    render_document_project_to_docx(project, package, template, version, None, output)

    with zipfile.ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        header_xml = archive.read("word/header1.xml").decode("utf-8")
    assert "保留的版式文字" in document_xml
    assert "申请人：李明 {{literal}}" in document_xml
    assert "申请摘要内容" in document_xml
    assert "2026-09-12" in document_xml
    assert "工程学院" in document_xml
    assert "{{name}}" not in document_xml
    assert "旧部门" not in document_xml
    assert "Header APP-001" in header_xml


def test_section_anchor_renders_structured_blocks_and_embedded_image(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    _source_docx(source)
    image = tmp_path / "figure.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    ))
    output = tmp_path / "rich-output.docx"
    render_anchored_docx(
        source,
        output,
        extract_docx_anchors(source),
        {},
        {
            "summary": [
                ContentBlock(id="table-1", kind="table", data={
                    "rows": 2, "cols": 2, "cells": [["项目", "结果"], ["温度", "25"]], "caption": "实验数据",
                }),
                ContentBlock(id="equation-1", kind="equation", data={
                    "omml": '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x+1</m:t></m:r></m:oMath>',
                    "alt_text": "x+1",
                }),
                ContentBlock(id="image-1", kind="image", data={"url": "/media/uploads/project-1-figure.png", "caption": "实验图"}),
            ],
        },
        image_paths={"image-1": image},
    )

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        document_xml = archive.read("word/document.xml").decode("utf-8")
        relationships = archive.read("word/_rels/document.xml.rels").decode("utf-8")
        content_types = archive.read("[Content_Types].xml").decode("utf-8")
    assert "项目" in document_xml and "25" in document_xml
    assert "<m:oMath" in document_xml and "x+1" in document_xml
    assert "<w:drawing" in document_xml and "实验图" in document_xml
    assert any(name.startswith("word/media/image") and name.endswith(".png") for name in names)
    assert "relationships/image" in relationships
    assert 'Extension="png"' in content_types


def test_rich_section_without_an_anchor_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    _source_docx(source)
    output = tmp_path / "output.docx"

    with pytest.raises(DocxAnchorError, match="没有可用章节锚点"):
        render_anchored_docx(
            source,
            output,
            extract_docx_anchors(source),
            {},
            {"unmapped": [ContentBlock(id="image-1", kind="image", data={"url": "/media/uploads/project-1.png"})]},
        )
