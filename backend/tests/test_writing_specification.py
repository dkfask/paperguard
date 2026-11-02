"""Source-bound extraction: mixed formatting, units, roles and API persistence."""

from __future__ import annotations

import hashlib
import copy
import json
import uuid
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt, RGBColor
from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image

from app.core.extract.effective_format import EffectiveFormatResolver
from app.core.extract.specification import extract_writing_specification, render_specification_markdown
from app.core.extract.xml_utils import qn
from app.main import app
from app.services.template_engine import parse_docx_template
from app.services.template_source import resolve_template_source, save_template_source


def _normal_document() -> Document:
    doc = Document()
    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'
    normal.font.size = Pt(12)
    normal.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '宋体')
    return doc


def _save(doc: Document, tmp_path: Path, name: str = 'source.docx') -> Path:
    path = tmp_path / name
    doc.save(path)
    return path


def _rewrite_part(path: Path, part: str, transform) -> None:
    with zipfile.ZipFile(path) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    parts[part] = transform(parts[part])
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)


def test_source_is_immutable_and_extraction_is_deterministic(tmp_path):
    doc = _normal_document()
    doc.add_heading('第一章 测试', level=1)
    doc.add_paragraph('正文内容。')
    path = _save(doc, tmp_path)
    original = path.read_bytes()
    first = extract_writing_specification(path)
    second = extract_writing_specification(path)
    assert first == second
    assert path.read_bytes() == original
    assert first.source_sha256 == hashlib.sha256(original).hexdigest()
    payload = first.model_dump(mode='json', exclude={'specification_sha256'})
    assert first.specification_sha256 == hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert all(r.decision == 'pending' and r.enforcement == 'suggestion' for r in first.rules)


def test_all_runs_inheritance_and_character_styles_are_evidence(tmp_path):
    doc = _normal_document()
    doc.styles.add_style('Unused misleading style', WD_STYLE_TYPE.PARAGRAPH).font.size = Pt(55)
    derived = doc.styles.add_style('DerivedBody', WD_STYLE_TYPE.PARAGRAPH)
    derived.base_style = doc.styles['Normal']
    derived.font.bold = True
    char = doc.styles.add_style('EmphasisPart', WD_STYLE_TYPE.CHARACTER)
    char.font.italic = True
    p = doc.add_paragraph(style=derived)
    p.add_run('正文正常文字，继承十二磅。')
    mixed = p.add_run('特殊标注')
    mixed.style = char
    mixed.font.size = Pt(16)
    mixed.font.bold = False
    source = _save(doc, tmp_path)
    result = extract_writing_specification(source)
    evidence = next(n for n in result.nodes if n.text.startswith('正文正常'))
    runs = evidence.details['runs']
    assert runs[0]['properties']['font.size'] == {'value': 12, 'unit': 'pt'}
    assert runs[0]['properties']['font.bold'] is True
    assert runs[1]['properties']['font.bold'] is False
    assert runs[1]['properties']['font.italic'] is True
    assert evidence.properties['font.eastAsia'] == '宋体'
    assert 'font.size' in evidence.details['mixed_properties']
    assert any(r.property == 'font.size' and r.confidence == 'conflict' for r in result.rules)
    assert not any(r.expected == {'value': 55, 'unit': 'pt'} for r in result.rules)
    assert any('word/styles.xml:' in origin for origin in runs[0]['property_sources']['font.size'])


def test_style_toggles_and_direct_false_have_distinct_semantics():
    styles = etree.fromstring(f'''<w:styles xmlns:w="{qn('w:x')[1:].split('}')[0]}">
      <w:style w:type="paragraph" w:styleId="Base"><w:rPr><w:b/></w:rPr></w:style>
      <w:style w:type="paragraph" w:styleId="Derived"><w:basedOn w:val="Base"/><w:rPr><w:b/></w:rPr></w:style>
      </w:styles>'''.encode())
    paragraph = etree.fromstring(f'''<w:p xmlns:w="{qn('w:x')[1:].split('}')[0]}"><w:pPr><w:pStyle w:val="Derived"/></w:pPr><w:r><w:t>A</w:t></w:r></w:p>'''.encode())
    _, _, runs, _ = EffectiveFormatResolver(styles).paragraph(paragraph)
    assert runs[0]['properties']['font.bold'] is False


def test_spacing_types_character_indent_and_false_page_control(tmp_path):
    doc = _normal_document()
    p = doc.add_paragraph('正文段落')
    ppr = p._p.get_or_add_pPr()
    spacing = OxmlElement('w:spacing')
    for key, value in {'beforeLines': '100', 'after': '120', 'line': '400', 'lineRule': 'exact'}.items():
        spacing.set(qn('w:' + key), value)
    ppr.append(spacing)
    indent = OxmlElement('w:ind')
    indent.set(qn('w:firstLineChars'), '200')
    ppr.append(indent)
    keep = OxmlElement('w:keepNext')
    keep.set(qn('w:val'), '0')
    ppr.append(keep)
    props = next(n for n in extract_writing_specification(_save(doc, tmp_path)).nodes if n.text == '正文段落').properties
    assert props['paragraph.spacing.before'] == {'value': 1, 'unit': 'line'}
    assert props['paragraph.spacing.after'] == {'value': 6, 'unit': 'pt'}
    assert props['paragraph.spacing.line'] == {'value': 20, 'unit': 'pt', 'kind': 'exact'}
    assert props['paragraph.indent.first_line'] == {'value': 2, 'unit': 'character'}
    assert props['paragraph.keep_next'] is False


def test_character_indents_override_length_indents_for_each_side():
    ns = qn('w:x')[1:].split('}')[0]
    styles = etree.fromstring(f'''<w:styles xmlns:w="{ns}">
      <w:style w:type="paragraph" w:styleId="Normal" w:default="1"/>
      </w:styles>'''.encode())
    paragraph = etree.fromstring(f'''<w:p xmlns:w="{ns}"><w:pPr>
      <w:ind w:left="0" w:leftChars="200" w:right="20" w:rightChars="100"
             w:hanging="0" w:hangingChars="50"/>
      </w:pPr><w:r><w:t>正文</w:t></w:r></w:p>'''.encode())
    props, _, _, _ = EffectiveFormatResolver(styles).paragraph(paragraph)
    assert props['paragraph.indent.left'] == {'value': 2, 'unit': 'character'}
    assert props['paragraph.indent.right'] == {'value': 1, 'unit': 'character'}
    assert props['paragraph.indent.hanging'] == {'value': 0.5, 'unit': 'character'}


def test_rules_stay_in_their_regions_and_roles(tmp_path):
    doc = _normal_document()
    doc.add_heading('摘要', level=1)
    doc.add_paragraph('中文摘要内容。')
    doc.add_heading('第1章 绪论', level=1)
    doc.add_paragraph('正文示例一。')
    doc.add_paragraph('正文示例二。')
    caption = doc.add_paragraph('图1.1 系统结构')
    caption.runs[0].font.size = Pt(10.5)
    doc.add_heading('第2章 实现', level=1)
    p = doc.add_paragraph('另一章正文。')
    p.runs[0].font.size = Pt(14)
    result = extract_writing_specification(_save(doc, tmp_path))
    body_rules = [r for r in result.rules if r.scope['role'] == 'body' and r.property == 'font.size']
    assert len(body_rules) == 2
    assert {r.expected['value'] for r in body_rules} == {12, 14}
    assert len({r.scope['region'] for r in body_rules}) == 2
    assert next(r for r in result.rules if r.scope['role'] == 'figure_caption' and r.property == 'font.size').expected['value'] == 10.5
    assert any(n.role == 'abstract' and n.text == '中文摘要内容。' for n in result.nodes)
    assert not any(r.scope['role'] in {'references', 'appendix'} for r in result.rules)


def test_tables_images_and_unknown_normative_intent(tmp_path):
    doc = _normal_document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '姓名'
    width = table._tbl.tblPr.find(qn('w:tblW'))
    width.set(qn('w:type'), 'pct')
    width.set(qn('w:w'), '5000')
    header = OxmlElement('w:tblHeader')
    table.rows[0]._tr.get_or_add_trPr().append(header)
    image_path = tmp_path / 'image.png'
    Image.new('RGB', (80, 40), 'blue').save(image_path)
    doc.add_picture(str(image_path), width=Inches(1))
    doc.add_picture(str(image_path), width=Inches(2))
    result = extract_writing_specification(_save(doc, tmp_path))
    tables = [n for n in result.nodes if n.role == 'table']
    assert tables[0].properties['table.width'] == {'value': 100, 'unit': 'percent', 'kind': 'preferred'}
    assert tables[0].details['rows'][0]['repeat_header'] is True
    images = [n for n in result.nodes if n.role == 'image']
    assert [n.properties['image.width']['value'] for n in images] == [2.54, 5.08]
    widths = [r for r in result.rules if r.property == 'image.width']
    assert len(widths) == 2 and all(r.confidence == 'single' for r in widths)
    assert len({r.scope['region'] for r in widths}) == 2
    assert all(n.details['assets'][0]['sha256'] == hashlib.sha256(image_path.read_bytes()).hexdigest() for n in images)
    assert any(item.code == 'empty_field_style' for item in result.review_items)
    markdown = render_specification_markdown(result)
    assert '单个实例' in markdown and '不会自动成为' in markdown
    assert result.coverage['automatic_compliance_verified'] is False


def test_sections_preserve_page_numbering_and_header_inheritance(tmp_path):
    doc = _normal_document()
    doc.sections[0].header.paragraphs[0].text = '校徽页眉'
    doc.add_paragraph('前置内容')
    section2 = doc.add_section(WD_SECTION_START.NEW_PAGE)
    section2.left_margin = Inches(1.5)
    page = OxmlElement('w:pgNumType')
    page.set(qn('w:fmt'), 'upperRoman')
    page.set(qn('w:start'), '1')
    section2._sectPr.append(page)
    result = extract_writing_specification(_save(doc, tmp_path))
    sections = [n for n in result.nodes if n.role == 'section']
    assert len(sections) == 2
    assert sections[0].properties['section.header_references'] == sections[1].properties['section.header_references']
    assert sections[1].properties['section.pgNumType'] == {'fmt': 'upperRoman', 'start': '1'}
    assert sections[0].properties['section.pgMar.left'] != sections[1].properties['section.pgMar.left']


def test_explicit_instruction_remains_unapproved_and_html_is_escaped(tmp_path):
    doc = _normal_document()
    doc.add_paragraph('正文使用宋体小四，1.5倍行距。<script>alert(1)</script>')
    result = extract_writing_specification(_save(doc, tmp_path))
    rule = next(r for r in result.rules if r.evidence_kind == 'explicit_instruction')
    assert rule.confidence == 'needs_review' and rule.enforcement == 'suggestion'
    assert '<script>' not in render_specification_markdown(result)
    assert '&lt;script&gt;' in render_specification_markdown(result)


def test_changed_source_invalidates_identifiers_and_digests(tmp_path):
    doc = _normal_document()
    doc.add_paragraph('原正文')
    path = _save(doc, tmp_path)
    first = extract_writing_specification(path)
    doc.add_paragraph('新正文')
    doc.save(path)
    second = extract_writing_specification(path)
    assert first.source_sha256 != second.source_sha256
    assert first.specification_sha256 != second.specification_sha256
    assert not ({n.node_id for n in first.nodes} & {n.node_id for n in second.nodes})


def test_template_candidate_carries_same_specification_as_direct_extraction(tmp_path):
    doc = _normal_document()
    doc.add_heading('1 概述', level=1)
    doc.add_paragraph('普通无占位符模板')
    path = _save(doc, tmp_path)
    candidate = parse_docx_template(path, candidate_id='spec-candidate', template_id='spec-template')
    spec = candidate.definition.extensions['writing_specification']
    assert spec == extract_writing_specification(path).model_dump(mode='json')
    assert candidate.definition.extensions['writing_specification_markdown'].startswith('# 从 Word')


def test_original_storage_option_keeps_every_source_byte(tmp_path):
    doc = _normal_document()
    doc.add_paragraph('保留原母版')
    data = _save(doc, tmp_path).read_bytes()
    key = save_template_source(data, preserve_original=True)
    assert resolve_template_source(key).read_bytes() == data


def test_import_persistence_source_tampering_and_authentication(tmp_path):
    doc = _normal_document()
    doc.add_heading('1 概述', level=1)
    doc.add_paragraph('正文规范示例')
    path = _save(doc, tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/templates/unknown/writing-specification').status_code == 401
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'TestAdminPassword123'})
        assert login.status_code == 200, login.text
        client.headers['Authorization'] = 'Bearer ' + login.json()['token']
        response = client.post('/api/templates/import', files={'file': ('template.docx', path.read_bytes())})
        assert response.status_code == 200, response.text
        imported = response.json()
        spec = imported['writing_specification']
        assert spec['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert spec['source_sha256'] in imported['writing_specification_markdown']
        payload = {**client.get('/api/templates/default-gb7713').json(), 'id': 'spec-' + uuid.uuid4().hex,
                   'name': '规范持久化测试', 'source_docx': imported['source_docx'], 'writing_specification': copy.deepcopy(spec)}
        # Neither a forged observation nor a copied digest can become a trusted source fact.
        payload['writing_specification']['rules'][0]['expected'] = 'forged'
        saved = client.post('/api/templates', json=payload)
        assert saved.status_code == 201, saved.text
        template_id = saved.json()['id']
        fetched = client.get(f'/api/templates/{template_id}/writing-specification')
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()['specification'] == imported['writing_specification']
        source = resolve_template_source(imported['source_docx'])
        with source.open('ab') as stream:
            stream.write(b'changed-source')
        assert client.get(f'/api/templates/{template_id}/writing-specification').status_code == 409


def test_theme_font_resolution_reports_unknown_dependencies():
    ns = qn('w:x')[1:].split('}')[0]
    styles = etree.fromstring(f'<w:styles xmlns:w="{ns}"><w:style w:type="paragraph" w:styleId="Normal" w:default="1"><w:rPr><w:rFonts w:eastAsiaTheme="minorEastAsia"/></w:rPr></w:style></w:styles>'.encode())
    p = etree.fromstring(f'<w:p xmlns:w="{ns}"><w:r><w:t>中文</w:t></w:r></w:p>'.encode())
    resolver = EffectiveFormatResolver(styles)
    _, _, runs, _ = resolver.paragraph(p)
    assert runs[0]['properties']['font.eastAsia'] == {'unresolved_theme': 'minorEastAsia'}
    assert 'unresolved_theme_font' in resolver.issues


def test_cover_and_explanations_do_not_pollute_body_rules(tmp_path):
    doc = _normal_document()
    p = doc.add_paragraph('毕业设计报告')
    p.runs[0].font.size = Pt(24)
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '学生相关信息字体均为三号宋体。'
    doc.add_heading('摘要', level=1)
    doc.add_paragraph('摘要内容。')
    doc.add_heading('1 绪论', level=1)
    doc.add_paragraph('正文内容。')
    result = extract_writing_specification(_save(doc, tmp_path))
    assert next(n for n in result.nodes if n.text == '毕业设计报告').role == 'cover'
    assert next(n for n in result.nodes if n.text.startswith('学生相关信息')).role == 'instruction'
    assert not any(r.scope['role'] == 'body' and r.expected == {'value': 24, 'unit': 'pt'} for r in result.rules)


@pytest.mark.parametrize('prefix', ['（以下略）', '以下略）'])
def test_writing_hints_are_separate_from_the_body_format(tmp_path, prefix):
    doc = _normal_document()
    doc.add_heading('1 绪论', level=1)
    p = doc.add_paragraph(prefix + '这里要包括硬件选型和电路设计')
    p.runs[0].font.color.rgb = RGBColor(255, 0, 0)
    doc.add_paragraph('正文内容应描述本项目的设计。')
    result = extract_writing_specification(_save(doc, tmp_path))
    assert next(n for n in result.nodes if n.text.startswith(prefix)).role == 'instruction'
    assert not any(r.scope['role'] == 'body' and r.expected == 'FF0000' for r in result.rules)
    assert all(r.scope['region_title'] == '1 绪论' for r in result.rules if r.scope['role'] == 'body')


def test_rejects_dtd_in_styles_without_expanding_entities(tmp_path):
    doc = _normal_document()
    doc.add_paragraph('正文')
    path = _save(doc, tmp_path)
    _rewrite_part(path, 'word/styles.xml', lambda data: data.replace(b'?>', b'?><!DOCTYPE styles [<!ENTITY secret SYSTEM "file:///etc/passwd">]>', 1))
    with pytest.raises(ValueError, match='DTD'):
        extract_writing_specification(path)


def test_long_mixed_run_does_not_lose_chinese_font_evidence(tmp_path):
    doc = _normal_document()
    doc.add_paragraph('A' * 180 + '中文')
    result = extract_writing_specification(_save(doc, tmp_path))
    paragraph = next(n for n in result.nodes if n.text.startswith('AAA'))
    assert paragraph.properties['font.eastAsia'] == '宋体'
