"""Source-only layout facts must survive independently of importer output."""
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree
import pytest

from app.services.docx_import_layout import inventory_docx_layout


def _save(document):
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _replace(data, part, value):
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, "w") as target:
        for name in source.namelist():
            target.writestr(name, value if name == part else source.read(name))
    return output.getvalue()


def _simple(paragraph, instruction, cache):
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = cache
    run.append(text)
    field.append(run)
    paragraph._p.append(field)
    return field


def _control(paragraph, kind):
    field = OxmlElement("w:fldChar")
    field.set(qn("w:fldCharType"), kind)
    paragraph.add_run()._r.append(field)


def _instruction(paragraph, text):
    node = OxmlElement("w:instrText")
    node.text = text
    paragraph.add_run()._r.append(node)


def test_sections_references_parts_and_source_authority_are_observations():
    document = Document()
    document.sections[0].header.paragraphs[0].text = "源学校页眉"
    document.sections[0].footer.paragraphs[0].text = "源页脚"
    document.add_paragraph("原论文")
    document.add_section(WD_SECTION_START.NEW_PAGE)
    data = _save(document)
    result = inventory_docx_layout(data)
    assert result["source_sha256"] == sha256(data).hexdigest()
    assert result["summary"]["section_count"] == 2
    assert all(section["active_location"] for section in result["sections"])
    assert all(reference["resolved"] for section in result["sections"] for reference in section["references"])
    assert {part["text"] for part in result["header_footer_parts"]} == {"源学校页眉", "源页脚"}
    assert all(part["sha256"] and part["referenced"] for part in result["header_footer_parts"])
    assert any(prop["tag"] == qn("w:pgSz") for prop in result["sections"][0]["properties"])
    assert result["authority"] == "source_observation_only"
    assert {item["action"] for item in result["dispositions"]} == {"preserve", "convert"}
    assert all(item["applied"] is False for item in result["dispositions"])
    assert result["status"] == "observed"
    assert inventory_docx_layout(data) == result


def test_split_complex_toc_and_nested_pageref_include_caches_without_final_authority():
    document = Document()
    paragraph = document.add_paragraph()
    _control(paragraph, "begin")
    _instruction(paragraph, " TO")
    _instruction(paragraph, 'C \\o "1-3" ')
    _control(paragraph, "separate")
    paragraph.add_run("绪论\t")
    row = document.add_paragraph()
    _control(row, "begin")
    _instruction(row, " PAGEREF _Toc123 \\h ")
    _control(row, "separate")
    row.add_run("4")
    _control(row, "end")
    _control(row, "end")
    result = inventory_docx_layout(_save(document))
    toc, pageref = result["fields"]
    assert toc["field_type"] == "TOC" and toc["cached_text"] == "绪论\t4"
    assert pageref["field_type"] == "PAGEREF" and pageref["cached_text"] == "4"
    assert pageref["depth"] == 1
    assert all(item["complete"] and item["supported"] for item in result["fields"])
    assert all(item["cache_authoritative"] is False for item in result["fields"])
    assert sum(item["action"] == "exclude" for item in result["dispositions"]) == 2
    assert result["unresolved"] == []


def test_simple_seq_and_footer_page_are_both_inventoried():
    document = Document()
    _simple(document.add_paragraph("图"), "SEQ 图3. \\* ARABIC", "27")
    _simple(document.sections[0].footer.paragraphs[0], " PAGE ", "9")
    result = inventory_docx_layout(_save(document))
    assert result["summary"]["field_types"] == {"PAGE": 1, "SEQ": 1}
    assert {item["cached_text"] for item in result["fields"]} == {"27", "9"}
    assert next(item for item in result["fields"] if item["field_type"] == "PAGE")["part"].startswith("word/footer")
    assert result["status"] == "observed"


@pytest.mark.parametrize("mode", ["unclosed", "orphan", "duplicate_separate", "unknown"])
def test_unsupported_or_malformed_fields_remain_unresolved(mode):
    document = Document()
    paragraph = document.add_paragraph()
    if mode == "unknown":
        _simple(paragraph, "INCLUDETEXT secret.docx", "旧缓存")
    elif mode == "orphan":
        _control(paragraph, "end")
        _instruction(paragraph, " PAGE ")
    else:
        _control(paragraph, "begin")
        _instruction(paragraph, " PAGE ")
        if mode == "duplicate_separate":
            _control(paragraph, "separate")
            _control(paragraph, "separate")
            _control(paragraph, "end")
    result = inventory_docx_layout(_save(document))
    assert result["status"] == "needs_review"
    assert any(item["kind"] == "field" for item in result["unresolved"])
    assert not any(item["kind"] == "field" and item["action"] == "convert" for item in result["dispositions"])


def test_missing_or_external_header_relation_is_not_accepted():
    document = Document()
    document.sections[0].header.paragraphs[0].text = "页眉仍在包中"
    data = _save(document)
    with ZipFile(BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
    relation = next(node for node in root if node.get("Type", "").endswith("/header"))
    relation.set("TargetMode", "External")
    result = inventory_docx_layout(_replace(data, "word/_rels/document.xml.rels", etree.tostring(root)))
    assert result["sections"][0]["references"][0]["resolved"] is False
    assert any(item["kind"] == "header_footer_reference" for item in result["unresolved"])
    assert result["header_footer_parts"][0]["referenced"] is False
    assert result["header_footer_parts"][0]["text"] == "页眉仍在包中"


def test_unknown_section_property_and_embedded_header_object_are_reported():
    document = Document()
    document.sections[0]._sectPr.append(etree.Element("{urn:test}mystery"))
    document.sections[0].header.paragraphs[0]._p.append(OxmlElement("w:object"))
    result = inventory_docx_layout(_save(document))
    assert {"section_property", "header_footer_structure"} <= {item["kind"] for item in result["unresolved"]}
    assert result["status"] == "needs_review"


def test_malformed_header_part_is_digest_bound_and_unresolved():
    document = Document()
    document.sections[0].header.paragraphs[0].text = "页眉"
    data = _save(document)
    result = inventory_docx_layout(_replace(data, "word/header1.xml", b"<bad"))
    assert result["header_footer_parts"][0]["sha256"] == sha256(b"<bad").hexdigest()
    assert any(item["kind"] == "header_footer_part" for item in result["unresolved"])


def test_unfamiliar_header_wrapper_is_not_flattened_into_a_success_claim():
    document = Document()
    header = document.sections[0].header
    node = etree.Element("{urn:unknown}container")
    node.append(OxmlElement("w:p"))
    header._element.append(node)
    result = inventory_docx_layout(_save(document))
    assert result["status"] == "needs_review"
    assert any(item["kind"] == "header_footer_structure" for item in result["unresolved"])
