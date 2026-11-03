"""Cover field labels in a separate table cell must remain visible."""

from __future__ import annotations

import hashlib

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from lxml import etree

from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import qn
from app.models.template import PaperTemplate
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.source_template_profile import ConfirmWritingProfile, compile_profile
from tests.test_source_template_profile import inspect, master, rendered


def _recompile(master):
    source, previous, project, _ = master
    specification = extract_writing_specification(source)
    template = PaperTemplate(
        id=previous.id, name="封面标签可见性测试", source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)["styles"],
        meta_fields=previous.meta_fields,
    )
    confirmation = ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True,
    )
    template = compile_profile(template, source, confirmation, actor="teacher-test")
    return source, template, project, confirmation


@pytest.mark.parametrize("property_name", ["vanish", "webHidden"])
@pytest.mark.parametrize("where", ["direct", "inherited"])
def test_hidden_label_in_adjacent_cover_cell_is_rejected(master, tmp_path, property_name, where):
    source = master[0]
    document = Document(source)
    label = document.tables[0].cell(0, 0).paragraphs[0]
    assert label.text == "学生姓名："
    if where == "direct":
        properties = label.runs[0]._r.get_or_add_rPr()
    else:
        style = document.styles.add_style("Hidden Cover Label", WD_STYLE_TYPE.PARAGRAPH)
        properties = style.element.get_or_add_rPr()
        label.style = style
    etree.SubElement(properties, qn("w:" + property_name)).set(qn("w:val"), "1")
    document.save(source)

    case = _recompile(master)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report = inspect(case, rendered(case, tmp_path))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert report["status"] == "fail"
    assert any(item["rule"] == "master.field_label_visibility"
               and item["object"] == "author" and item["status"] == "fail"
               for item in report["checks"])


def test_unrelated_hidden_cover_note_is_preserved_without_blocking(master, tmp_path):
    source = master[0]
    document = Document(source)
    note = document.tables[0].add_row().cells[0].paragraphs[0].add_run("母版内部备注")
    note.font.hidden = True
    document.save(source)

    case = _recompile(master)
    report = inspect(case, rendered(case, tmp_path))
    assert report["status"] == "pass", [item for item in report["checks"] if item["status"] != "pass"]
