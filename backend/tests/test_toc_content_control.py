"""Word automatic directories live in a body-level Table of Contents control.

Body-level TOC paragraphs keep the previous rebuild path. A directory inside
``w:body/w:sdt/w:sdtContent`` is rebuilt inside that control, once.
"""
import io
import uuid
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.shared import Pt
from lxml import etree

from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS, load_part, qn
from app.models.template import MetaField, PaperTemplate
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.master_docx_renderer import prepare_source
from app.services.office_layout import _layout_input_parts, _remove_toc_field_controls, _replace_toc_rows, _word_generated_toc_rows
from app.services.server_layout import _server_layout_input_parts
from app.services.source_template_profile import ConfirmWritingProfile, ProfileError, compile_profile


def _run(parent, *children):
    run = OxmlElement("w:r")
    for child in children:
        run.append(child)
    parent.append(run)
    return run


def _fld(kind):
    node = OxmlElement("w:fldChar")
    node.set(qn("w:fldCharType"), kind)
    return node


def _instruction(text):
    node = OxmlElement("w:instrText")
    node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    node.text = text
    return node


def _text(value):
    node = OxmlElement("w:t")
    node.text = value
    return node


def _gallery(paragraphs, gallery="Table of Contents"):
    control = OxmlElement("w:sdt")
    props = OxmlElement("w:sdtPr")
    part = OxmlElement("w:docPartObj")
    name = OxmlElement("w:docPartGallery")
    name.set(qn("w:val"), gallery)
    part.append(name)
    part.append(OxmlElement("w:docPartUnique"))
    props.append(part)
    control.append(props)
    content = OxmlElement("w:sdtContent")
    for paragraph in paragraphs:
        content.append(paragraph)
    control.append(content)
    return control


def _toc_row(document, style, label, page, *, field=False):
    paragraph = document.add_paragraph(style=style)
    node = paragraph._p
    if field:
        _run(node, _fld("begin"))
        _run(node, _instruction(' TOC \\o "1-3" \\u \\h '))
        _run(node, _fld("separate"))
    _run(node, _text(label))
    _run(node, OxmlElement("w:tab"))
    _run(node, _text(page))
    if field:
        _run(node, _fld("end"))
    return node


def _base_document(tmp_path: Path) -> Document:
    document = Document()
    normal = document.styles["Normal"]
    normal.font.size = Pt(12)
    normal.font.name = "Times New Roman"
    normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "宋体")
    normal.paragraph_format.line_spacing = 1.5
    document.add_paragraph("毕业设计报告")
    document.add_paragraph("题目：原示例题目")
    table = document.add_table(rows=2, cols=2)
    for row, label in zip(table.rows, ["学生姓名：", "学生学号："]):
        row.cells[0].text = label
        row.cells[1].text = ""
    document.sections[0].header.paragraphs[0].text = "固定校名与页眉"
    footer = document.sections[0].footer.paragraphs[0]
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    document.add_section(WD_SECTION_START.NEW_PAGE)
    document.add_heading("摘要", 1)
    document.add_paragraph("摘要正文示例内容。")
    document.add_heading("第1章 概述", 1)
    document.add_paragraph("这是源模板的示例正文，不应残留在学生成品中。")
    document.add_heading("1.1 研究内容", 2)
    document.add_paragraph("这里要包括：请替换说明。")
    document.styles.add_style("TOC 1", WD_STYLE_TYPE.PARAGRAPH)
    document.styles.add_style("TOC 2", WD_STYLE_TYPE.PARAGRAPH)
    return document


def _append_directory(document: Document, *, gallery: str | None):
    title = document.add_paragraph("目  录")
    first = _toc_row(document, "TOC 1", "第1章 概述", "3", field=True)
    second = _toc_row(document, "TOC 2", "1.1 研究内容", "4")
    if gallery is None:
        return
    parent = first.getparent()
    index = parent.index(first)
    parent.remove(first)
    parent.remove(second)
    parent.insert(index, _gallery([first, second], gallery))
    assert title._p.getnext().tag == qn("w:sdt")


def _compile(tmp_path: Path, document: Document, name: str):
    path = tmp_path / name
    document.save(path)
    spec = extract_writing_specification(path)
    template = PaperTemplate(
        id="tpl-" + uuid.uuid4().hex,
        name="目录控件母版",
        source_docx=name,
        sections=extract_section_tree(path),
        styles=import_template_styles(path)["styles"],
        meta_fields=[
            MetaField(key="title", label="题目"),
            MetaField(key="author", label="姓名"),
            MetaField(key="student_id", label="学号"),
        ],
    )
    request = ConfirmWritingProfile(
        expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True,
        remove_template_instructions=True,
        accept_new_object_defaults=True,
        remove_unmatched_toc_entries=True,
    )
    return path, compile_profile(template, path, request, actor="teacher-test")


def _parts(path: Path, root) -> dict[str, bytes]:
    payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    data = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(data, "w") as output:
        for name in source.namelist():
            output.writestr(name, payload if name == "word/document.xml" else source.read(name))
    with zipfile.ZipFile(data) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _galleries(root):
    return root.xpath(
        './/w:sdt[w:sdtPr/w:docPartObj/w:docPartGallery/@w:val="Table of Contents"]',
        namespaces=NS,
    )


def _placeholders(root):
    return root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-toc:placeholder"]', namespaces=NS)


def _toc_instructions(root):
    values = [text for text in root.xpath(".//w:instrText/text()", namespaces=NS) if text.strip().upper().startswith("TOC")]
    values.extend(
        text for text in root.xpath(".//w:fldSimple/@w:instr", namespaces=NS)
        if str(text).strip().upper().startswith("TOC")
    )
    return values


def _visible(paragraph):
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))


def test_body_toc_paragraphs_stay_direct_children(tmp_path):
    document = _base_document(tmp_path)
    _append_directory(document, gallery=None)
    path, approved = _compile(tmp_path, document, "body-toc.docx")
    profile = approved.source_execution
    assert profile["toc_active"] is True
    assert profile["toc_container_path"].endswith("/w:body")
    assert {entry["source_label"] for entry in profile["toc_entries"]} == {"第1章 概述", "1.1 研究内容"}

    with zipfile.ZipFile(path) as archive:
        root = load_part(archive, "word/document.xml")
    prepared = prepare_source(root, profile)
    placeholders = _placeholders(prepared)
    assert len(placeholders) == 1
    assert placeholders[0].getparent().tag == qn("w:body")
    assert _galleries(prepared) == []
    assert len(_toc_instructions(prepared)) == 1
    assert any(_visible(paragraph) == "目  录" for paragraph in prepared.findall(".//" + qn("w:p")))


def test_toc_inside_body_content_control_is_rebuilt_once(tmp_path):
    document = _base_document(tmp_path)
    _append_directory(document, gallery="Table of Contents")
    path, approved = _compile(tmp_path, document, "sdt-toc.docx")
    profile = approved.source_execution
    assert profile["toc_active"] is True
    assert profile["toc_container_path"].endswith("/w:sdt/w:sdtContent")
    assert [entry["source_label"] for entry in profile["toc_entries"]] == ["第1章 概述", "1.1 研究内容"]
    assert all("/w:sdt/w:sdtContent/" in path for path in profile["toc_range_paths"])

    with zipfile.ZipFile(path) as archive:
        root = load_part(archive, "word/document.xml")
    prepared = prepare_source(root, profile)
    galleries = _galleries(prepared)
    placeholders = _placeholders(prepared)
    assert len(galleries) == 1
    assert len(placeholders) == 1
    assert placeholders[0].getparent() is galleries[0].find(qn("w:sdtContent"))
    assert placeholders[0].getparent().getparent() is galleries[0]
    assert galleries[0].getparent().tag == qn("w:body")
    assert _toc_instructions(prepared) == [placeholders[0].xpath(".//w:fldSimple/@w:instr", namespaces=NS)[0]]
    title = next(paragraph for paragraph in prepared.findall(qn("w:body") + "/" + qn("w:p")) if _visible(paragraph) == "目  录")
    assert title.getnext() is galleries[0]
    assert "第1章 概述" not in "".join(galleries[0].itertext())

    parts = _parts(path, prepared)
    before = {name: data for name, data in parts.items()}
    unwrapped = _server_layout_input_parts(parts, profile)
    assert parts == before
    probe = etree.fromstring(unwrapped["word/document.xml"])
    probe_galleries = _galleries(probe)
    assert len(probe_galleries) == 1 and _placeholders(probe) == []
    assert len(_toc_instructions(probe)) == 1
    assert probe_galleries[0].xpath(".//w:instrText", namespaces=NS)
    assert not probe.xpath(
        './w:body/w:p//w:instrText[contains(., "TOC")]',
        namespaces=NS,
    )
    assert any(_visible(paragraph) == "目  录" and paragraph.getparent().tag == qn("w:body") for paragraph in probe.findall(".//" + qn("w:p")))

    heading = next(item for item in profile["headings"] if item["title"] == "第1章 概述")
    row = etree.Element(qn("w:p"))
    props = etree.SubElement(row, qn("w:pPr"))
    etree.SubElement(props, qn("w:pStyle")).set(qn("w:val"), "TOC1")
    link = etree.SubElement(row, qn("w:hyperlink"))
    link.set(qn("w:anchor"), heading["bookmark_name"])
    _run(link, _text("第1章 概述"))
    _run(link, OxmlElement("w:tab"))
    _run(link, _text("6"))
    _replace_toc_rows(parts, [{
        "paragraph": row,
        "bookmark": heading["bookmark_name"],
        "page": "6",
        "level": 1,
    }], profile)
    rebuilt = etree.fromstring(parts["word/document.xml"])
    rebuilt_galleries = _galleries(rebuilt)
    assert len(rebuilt_galleries) == 1 and _placeholders(rebuilt) == []
    rows = rebuilt_galleries[0].xpath("./w:sdtContent/w:p", namespaces=NS)
    assert len(rows) == 1
    assert _visible(rows[0]) == "第1章 概述6"
    assert _toc_instructions(rebuilt) == []
    outside = [
        paragraph for paragraph in rebuilt.findall(qn("w:body") + "/" + qn("w:p"))
        if paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}") is not None
        and paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}").get(qn("w:val")) in {"TOC1", "TOC2"}
    ]
    assert outside == []
    assert any(_visible(paragraph) == "目  录" for paragraph in rebuilt.findall(qn("w:body") + "/" + qn("w:p")))

    refreshed = _layout_input_parts(parts, profile)
    laid_out = etree.fromstring(refreshed["word/document.xml"])
    assert len(_galleries(laid_out)) == 1
    assert len(_toc_instructions(laid_out)) == 1
    assert laid_out.xpath(
        './/w:sdt[w:sdtPr/w:docPartObj/w:docPartGallery/@w:val="Table of Contents"]//w:instrText',
        namespaces=NS,
    )
    assert not laid_out.xpath('./w:body/w:p[.//w:instrText[contains(., "TOC")]]', namespaces=NS)


@pytest.mark.parametrize("gallery", ["Bibliography", "Table of Figures"])
def test_other_content_controls_still_reject_toc_fields(tmp_path, gallery):
    document = _base_document(tmp_path)
    _append_directory(document, gallery=gallery)
    with pytest.raises(ProfileError, match="目录域必须位于正文主流程"):
        _compile(tmp_path, document, "other-sdt.docx")


def test_toc_field_in_a_table_or_text_box_is_rejected(tmp_path):
    table_document = _base_document(tmp_path)
    _append_directory(table_document, gallery="Table of Contents")
    body = table_document.element.body
    control = body.find(qn("w:sdt"))
    body.remove(control)
    cell = OxmlElement("w:tc")
    cell.append(control)
    row = OxmlElement("w:tr")
    row.append(cell)
    table = OxmlElement("w:tbl")
    table.append(row)
    body.append(table)
    with pytest.raises(ProfileError, match="目录域必须位于正文主流程"):
        _compile(tmp_path, table_document, "table-toc.docx")

    boxed = _base_document(tmp_path)
    _append_directory(boxed, gallery=None)
    paragraph = next(item for item in boxed.element.body.findall(qn("w:p")) if item.find(".//" + qn("w:instrText")) is not None)
    paragraph.getparent().remove(paragraph)
    text_box = OxmlElement("w:txbxContent")
    text_box.append(paragraph)
    boxed.element.body.append(text_box)
    with pytest.raises(ProfileError, match="目录域必须位于正文主流程"):
        _compile(tmp_path, boxed, "textbox-toc.docx")


def test_field_end_beside_a_content_control_directory_is_removed():
    body = etree.Element(qn("w:body"))
    control = etree.SubElement(body, qn("w:sdt"))
    props = etree.SubElement(control, qn("w:sdtPr"))
    part = etree.SubElement(props, qn("w:docPartObj"))
    gallery = etree.SubElement(part, qn("w:docPartGallery"))
    gallery.set(qn("w:val"), "Table of Contents")
    content = etree.SubElement(control, qn("w:sdtContent"))
    row = etree.SubElement(content, qn("w:p"))
    props = etree.SubElement(row, qn("w:pPr"))
    etree.SubElement(props, qn("w:pStyle")).set(qn("w:val"), "TOC1")
    etree.SubElement(etree.SubElement(row, qn("w:r")), qn("w:t")).text = "第1章 概述"
    dangling = etree.SubElement(content, qn("w:p"))
    etree.SubElement(etree.SubElement(dangling, qn("w:r")), qn("w:fldChar")).set(qn("w:fldCharType"), "end")
    unrelated = etree.SubElement(body, qn("w:p"))
    _run(unrelated, _fld("end"))
    _run(unrelated, _text("保留"))

    _remove_toc_field_controls(body, ["TOC1"])

    assert dangling.find(".//" + qn("w:fldChar")) is None
    assert unrelated.find(".//" + qn("w:fldChar")) is not None
    assert _visible(unrelated) == "保留"


def _word_directory_package(tmp_path: Path):
    document = Document()
    document.styles.add_style("TOC 1", WD_STYLE_TYPE.PARAGRAPH)
    row = document.add_paragraph(style="TOC 1")
    row.add_run("第1章 绪论")
    number = row.add_run()
    number.add_tab()
    number.add_text("8")
    heading = document.add_heading("第1章 绪论", 1)
    bookmark = etree.Element(qn("w:bookmarkStart"))
    bookmark.set(qn("w:id"), "1")
    bookmark.set(qn("w:name"), "PGHchapter1")
    heading._p.insert(1, bookmark)
    end = etree.SubElement(heading._p, qn("w:bookmarkEnd"))
    end.set(qn("w:id"), "1")
    path = tmp_path / "word-toc.docx"
    document.save(path)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    profile = {
        "toc_active": True,
        "toc_style_ids": ["TOC1"],
        "toc_style_by_level": {"1": "TOC1"},
        "toc_entries": [{"target_key": "ch1", "target_title": "第1章 绪论", "level": 1}],
        "headings": [{"key": "ch1", "title": "第1章 绪论", "kind": "heading1", "bookmark_name": "PGHchapter1"}],
    }
    return parts, profile


def _nest_placeholder(parts, parent_factory):
    root = etree.fromstring(parts["word/document.xml"])
    body = root.find(qn("w:body"))
    paragraph = next(
        item for item in body.findall(qn("w:p"))
        if item.find(f"{qn('w:pPr')}/{qn('w:pStyle')}") is not None
        and item.find(f"{qn('w:pPr')}/{qn('w:pStyle')}").get(qn("w:val")) == "TOC1"
    )
    index = body.index(paragraph)
    body.remove(paragraph)
    placeholder = etree.Element(qn("w:sdt"))
    props = etree.SubElement(placeholder, qn("w:sdtPr"))
    etree.SubElement(props, qn("w:tag")).set(qn("w:val"), "pg-toc:placeholder")
    content = etree.SubElement(placeholder, qn("w:sdtContent"))
    content.append(paragraph)
    parent_factory(body, index, placeholder)
    result = dict(parts)
    result["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return result


def _archive(parts) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return data.getvalue()


def test_word_export_accepts_placeholder_inside_toc_content_control(tmp_path):
    parts, profile = _word_directory_package(tmp_path)

    def inside_gallery(body, index, placeholder):
        body.insert(index, _gallery([placeholder]))

    original = _nest_placeholder(parts, inside_gallery)
    updated = {name: data for name, data in original.items()}
    rows = _word_generated_toc_rows(original, _archive(updated), profile, tmp_path / "rows.docx")
    assert [(row["key"], row["page"]) for row in rows] == [("ch1", "8")]
    assert original["word/document.xml"] == updated["word/document.xml"]


def test_word_export_rejects_placeholder_moved_into_a_table(tmp_path):
    parts, profile = _word_directory_package(tmp_path)

    def inside_table(body, index, placeholder):
        table = OxmlElement("w:tbl")
        cell = OxmlElement("w:tc")
        cell.append(placeholder)
        row = OxmlElement("w:tr")
        row.append(cell)
        table.append(row)
        body.insert(index, table)

    original = _nest_placeholder(parts, inside_table)
    with pytest.raises(ProfileError, match="不在正文中"):
        _word_generated_toc_rows(original, _archive(original), profile, tmp_path / "rows.docx")
