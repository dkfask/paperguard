"""Read-only, version-bound admission checks for uploaded Word templates."""

from io import BytesIO
from pathlib import Path
import zipfile

from docx import Document
from lxml import etree
import pytest

from app.models.template import CoverBlock, CoverField, PaperTemplate, SectionType, TemplateSection, TextStyle
from app.services.projects import default_template
from app.services.template_admission import assess_paper_template, assess_template_definition


def _docx_bytes(*, table=False, header="", equation=False):
    doc = Document()
    doc.add_paragraph("论文模板正文")
    if table:
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "封面信息"
    if header:
        doc.sections[0].header.paragraphs[0].text = header
    output = BytesIO()
    doc.save(output)
    data = output.getvalue()
    if not equation:
        return data
    archive = zipfile.ZipFile(BytesIO(data))
    files = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(files["word/document.xml"])
    paragraph = root.xpath(".//w:p", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})[0]
    math = etree.SubElement(paragraph, "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")
    run = etree.SubElement(math, "{http://schemas.openxmlformats.org/officeDocument/2006/math}r")
    text = etree.SubElement(run, "{http://schemas.openxmlformats.org/officeDocument/2006/math}t")
    text.text = "x"
    files["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    rebuilt = BytesIO()
    with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as target:
        for name, value in files.items():
            target.writestr(name, value)
    return rebuilt.getvalue()


def _replace_docx_part(data: bytes, part_name: str, replacement: bytes) -> bytes:
    rebuilt = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, replacement if name == part_name else source.read(name))
    return rebuilt.getvalue()


def test_complete_gb7713_template_is_assessable_but_requires_office_review():
    report = assess_paper_template(default_template(), None)
    assert report["status"] == "office_review_required"
    assert report["blockers"] == []
    assert report["can_use_for_draft"] is True
    assert report["can_claim_compliant_delivery"] is False
    assert len(report["auto_verifiable_constraints"]) == len(TextStyle.model_fields)
    assert {item["code"] for item in report["manual_checks"]} == {"office_pagination", "office_toc_refresh"}


def test_assessment_hash_binds_template_definition_version_and_source_bytes():
    template = default_template().model_copy(update={"version": "7"})
    source_a = _docx_bytes()
    source_b = _docx_bytes(header="不同来源")
    first = assess_paper_template(template, source_a)
    same = assess_paper_template(template, source_a)
    changed_definition = template.model_copy(update={
        "styles": {**template.styles, "body": template.styles["body"].model_copy(update={"size_pt": 13})},
    })
    changed_version = template.model_copy(update={"version": "8"})
    assert first["assessment_hash"] == same["assessment_hash"]
    assert first["source_docx_sha256"] != assess_paper_template(template, source_b)["source_docx_sha256"]
    assert first["assessment_hash"] != assess_paper_template(changed_definition, source_a)["assessment_hash"]
    assert first["assessment_hash"] != assess_paper_template(changed_version, source_a)["assessment_hash"]
    assert first["structure_inventory"]["source_sha256"] == first["source_docx_sha256"]
    assert first["structure_inventory"]["status"] == "inventory_only"
    assert first["structure_inventory"]["formal_admission"] is False
    assert first["structure_inventory"]["part_count"] > 0


def test_cover_table_and_source_docx_table_block_template_use():
    template = default_template().model_copy(update={"cover_blocks": [CoverBlock(kind="table")]})
    cover_report = assess_paper_template(template, None)
    source_report = assess_paper_template(default_template(), _docx_bytes(table=True))
    assert cover_report["status"] == "blocked"
    assert any(item["code"] == "cover_table" for item in cover_report["blockers"])
    assert source_report["status"] == "blocked"
    assert any(item["code"] == "source_table_not_mapped" for item in source_report["blockers"])


def test_source_xml_equations_and_unmapped_headers_are_blocked():
    xml_report = assess_template_definition(
        template_id="candidate", version="1",
        definition={"source_docx": "", "styles": {}, "blocks": [{"table": {"source_xml": "<w:tbl/>"}}]},
        source_data=None,
    )
    equation_report = assess_paper_template(default_template(), _docx_bytes(equation=True))
    header_report = assess_paper_template(default_template(), _docx_bytes(header="院系页眉"))
    assert any(item["code"] == "source_xml_table" for item in xml_report["blockers"])
    assert any(item["code"] == "source_equation_omml" for item in equation_report["blockers"])
    assert any(item["code"] == "unmapped_header" for item in header_report["blockers"])


def test_unknown_explicit_style_property_is_blocked():
    template = default_template().model_copy(update={
        "styles": {**default_template().styles, "body": TextStyle(extra_shadow="heavy")},
    })
    report = assess_paper_template(template, None)
    assert any(item["code"] == "unsupported_style_property" and item["path"].endswith("extra_shadow") for item in report["blockers"])


@pytest.mark.parametrize(
    ("location", "expected_path"),
    [
        ("section", "definition.sections[0].style.color"),
        ("nested_section", "definition.sections[0].children[0].style.color"),
        ("cover_block", "definition.cover_blocks[0].style.color"),
        ("cover_label", "definition.cover_blocks[0].fields[0].label_style.color"),
        ("cover_value", "definition.cover_blocks[0].fields[0].value_style.color"),
    ],
)
def test_unverifiable_nested_export_styles_are_blocked(location, expected_path):
    invalid = TextStyle(color="NOTRGB")
    template = default_template()
    if location == "section":
        sections = [TemplateSection(key="h", type=SectionType.HEADING1, title="Title", style=invalid)]
        template = template.model_copy(update={"sections": sections})
    elif location == "nested_section":
        sections = [TemplateSection(
            key="h", type=SectionType.HEADING1, title="Title",
            children=[TemplateSection(key="body", type=SectionType.BODY, title="Body", style=invalid)],
        )]
        template = template.model_copy(update={"sections": sections})
    elif location == "cover_block":
        template = template.model_copy(update={"cover_blocks": [CoverBlock(kind="text", text="Title", style=invalid)]})
    else:
        field = CoverField(label="字段：", label_style=invalid if location == "cover_label" else None,
                           value_style=invalid if location == "cover_value" else None)
        template = template.model_copy(update={"cover_blocks": [CoverBlock(kind="field", fields=[field])]})

    report = assess_paper_template(template, None)
    assert report["status"] == "blocked"
    assert any(item["code"] == "style_color" and item["path"] == expected_path for item in report["blockers"])


def test_supported_styles_at_all_export_positions_remain_admissible():
    supported = TextStyle(color="A1B2C3")
    template = default_template().model_copy(update={
        "sections": [TemplateSection(
            key="h", type=SectionType.HEADING1, title="Title",
            children=[TemplateSection(key="body", type=SectionType.BODY, title="Body", style=supported)],
        )],
        "cover_blocks": [
            CoverBlock(kind="text", text="Title", style=supported),
            CoverBlock(kind="field", fields=[CoverField(label="姓名：", label_style=supported, value_style=supported)]),
        ],
    })
    report = assess_paper_template(template, None)
    assert report["status"] == "office_review_required"
    assert report["blockers"] == []


def test_generic_template_definition_nested_style_is_blocked():
    report = assess_template_definition(
        template_id="generic-nested-style", version="1", source_data=None,
        definition={
            "styles": {}, "cover_blocks": [],
            "sections": [{"key": "h", "style": {"color": "NOTRGB"}, "children": []}],
        },
    )
    assert any(item["code"] == "style_color" and item["path"] == "definition.sections[0].style.color" for item in report["blockers"])


def test_source_docx_dtd_and_external_entity_are_rejected_without_expansion():
    source = _docx_bytes()
    xml = b'''<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>'''
    malicious = _replace_docx_part(source, "word/document.xml", xml)
    report = assess_paper_template(default_template(), malicious)
    assert report["status"] == "blocked"
    assert any(item["code"] == "source_unreadable" for item in report["blockers"])


def test_inventory_checks_xml_parts_outside_word_directory():
    source = _docx_bytes()
    malicious = _replace_docx_part(source, "docProps/core.xml", b'''<!DOCTYPE core [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><core>&xxe;</core>''')
    report = assess_paper_template(default_template(), malicious)
    assert report["can_use_for_draft"] is False
    assert any(item["code"] == "source_inventory_unreadable" for item in report["blockers"])
    assert report["structure_inventory"] is None
    assert "root:" not in str(report)


def test_source_docx_zip_ratio_limit_is_applied_during_assessment():
    source = _docx_bytes()
    suspicious = _replace_docx_part(source, "word/document.xml", b"A" * 100_000)
    report = assess_paper_template(default_template(), suspicious)
    assert report["status"] == "blocked"
    assert any(item["code"] == "source_unreadable" for item in report["blockers"])


@pytest.mark.parametrize("kind", ["drawingml_chart", "extended_chart", "chart_part"])
def test_office_chart_templates_are_rejected_before_writing(kind):
    source = _docx_bytes()
    rebuilt = BytesIO()
    with zipfile.ZipFile(BytesIO(source)) as archive, zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as target:
        for name in archive.namelist():
            data = archive.read(name)
            if name == "word/document.xml" and kind != "chart_part":
                root = etree.fromstring(data)
                paragraph = root.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
                chart_namespace = "http://schemas.openxmlformats.org/drawingml/2006/chart" if kind == "drawingml_chart" else "http://schemas.microsoft.com/office/drawing/2014/chartex"
                etree.SubElement(paragraph, f"{{{chart_namespace}}}chart")
                data = etree.tostring(root)
            target.writestr(name, data)
        if kind == "chart_part":
            target.writestr("word/charts/chart1.xml", b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/>')

    report = assess_paper_template(default_template(), rebuilt.getvalue())

    assert report["status"] == "blocked"
    assert report["can_use_for_draft"] is False
    assert any(item["code"] == "source_chart_not_mapped" for item in report["blockers"])


@pytest.mark.parametrize("unsupported", ["no_anchors", "legacy_adapter", "bookmark"])
def test_static_objects_stay_blocked_without_supported_placeholder_checker(unsupported):
    from app.services.template_engine import parse_docx_template

    source = Path(__file__).parent / "fixtures" / "templates" / "generic-anchored.docx"
    candidate = parse_docx_template(
        source, candidate_id="anchor-admission", template_id="anchor-admission", filename=source.name,
    )
    definition = candidate.definition.model_copy(deep=True)
    if unsupported == "no_anchors":
        definition.anchors = []
    elif unsupported == "legacy_adapter":
        definition.export_strategy = "legacy_adapter"
    else:
        definition.anchors[0].kind = "bookmark"
    report = assess_template_definition(
        template_id=candidate.template_id, version=candidate.version,
        definition=definition, source_data=source.read_bytes(),
    )
    assert not report["can_use_for_draft"]
    assert {"source_table_not_mapped", "unmapped_header", "unmapped_footer"}.issubset(
        {item["code"] for item in report["blockers"]},
    )


def test_placeholder_mapping_does_not_clear_unsupported_equations():
    from app.domain.documents.schemas import TemplateAnchor, TemplateDefinition

    definition = TemplateDefinition(anchors=[TemplateAnchor(key="name", selector="{{name}}", kind="placeholder")])
    report = assess_template_definition(
        template_id="unsupported-equation", version="1", definition=definition,
        source_data=_docx_bytes(table=True, header="页眉", equation=True),
    )
    assert not report["can_use_for_draft"]
    assert any(item["code"] == "source_equation_omml" for item in report["blockers"])


def test_placeholder_preservation_does_not_approve_dynamic_footer_fields():
    from app.services.template_engine import parse_docx_template

    path = Path(__file__).parent / "fixtures" / "templates" / "generic-anchored.docx"
    candidate = parse_docx_template(path, candidate_id="dynamic-field", template_id="dynamic-field", filename=path.name)
    data = path.read_bytes()
    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/footer1.xml"))
    paragraph = root.find("{%s}p" % "http://schemas.openxmlformats.org/wordprocessingml/2006/main")
    etree.SubElement(paragraph, "{%s}fldSimple" % "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                     {"{%s}instr" % "http://schemas.openxmlformats.org/wordprocessingml/2006/main": "PAGE"})
    changed = _replace_docx_part(data, "word/footer1.xml", etree.tostring(root))
    report = assess_template_definition(
        template_id=candidate.template_id, version=candidate.version,
        definition=candidate.definition, source_data=changed,
    )
    assert not report["can_use_for_draft"]
    assert any(item["code"] == "unmapped_footer" for item in report["blockers"])
