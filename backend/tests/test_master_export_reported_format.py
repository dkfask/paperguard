"""Regression cases for source-master formatting and adjacent imported captions."""
from __future__ import annotations
import io
import zipfile
import copy
import pytest
from docx import Document
from docx.shared import Pt, RGBColor
from lxml import etree
from PIL import Image
from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS, qn
from app.models.template import PaperTemplate
from app.services.docx_project_import import import_docx_project
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.source_template_profile import ConfirmWritingProfile, compile_profile
from tests.test_source_template_profile import master, rendered, inspect, rewrite
from tests.test_master_content_visibility import _add_objects


def test_explanatory_heading_color_and_list_examples_do_not_override_body_format(tmp_path):
    doc = Document()
    doc.styles['Normal'].font.size = Pt(12)
    doc.styles['Normal'].paragraph_format.first_line_indent = Pt(24)
    for title, level in [('第1章 系统分析', 1), ('1.1 功能分析', 2)]:
        doc.add_heading(title, level)
        doc.add_paragraph('正文示例说明系统如何处理和记录各种信息。' * 10)
    p = doc.add_heading('1.1.1 数据采集', 3)
    p.add_run('（按实际实物实现功能进行描述）').font.color.rgb = RGBColor(255, 0, 0)
    p = doc.add_paragraph('此段仅用于示意数据采集功能。' * 6)
    p.runs[0].font.size = Pt(10.5)
    for _ in range(4):
        p = doc.add_paragraph('哪些数据由传感器采集？', style='List Paragraph')
        p.runs[0].font.size = Pt(10.5)
        p.paragraph_format.first_line_indent = Pt(0)
    path = tmp_path / 'master.docx'; doc.save(path)
    spec = extract_writing_specification(path)
    title = next(n for n in spec.nodes if n.role == 'heading3')
    assert title.properties.get('font.color') != 'FF0000'
    template = PaperTemplate(id='format-master', name='test', source_docx=path.name,
        sections=extract_section_tree(path), styles=import_template_styles(path)['styles'])
    template = compile_profile(template, path, ConfirmWritingProfile(
        expected_specification_sha256=spec.specification_sha256, accept_recommended=True,
        accept_new_object_defaults=True, remove_template_instructions=True), actor='teacher')
    headings = template.source_execution['headings']
    one = next(h for h in headings if h['kind'] == 'heading1')
    three = next(h for h in headings if h['kind'] == 'heading3')
    assert three['properties']['font.size'] == one['properties']['font.size']
    assert three['properties']['paragraph.indent.first_line'] == one['properties']['paragraph.indent.first_line']


def test_an_explicit_heading_color_is_preserved(tmp_path):
    doc = Document(); heading = doc.add_heading('1.1 真正的蓝色标题', 2)
    heading.runs[0].font.color.rgb = RGBColor(0, 34, 136)
    path = tmp_path / 'color.docx'; doc.save(path)
    item = next(n for n in extract_writing_specification(path).nodes if n.role == 'heading2')
    assert item.properties['font.color'] == '002288'


def test_adjacent_captions_are_bound_to_their_own_objects(tmp_path):
    image = io.BytesIO(); Image.new('RGB', (20, 10), 'white').save(image, format='PNG'); image.seek(0)
    doc = Document(); doc.add_heading('第1章 正文', 1)
    doc.add_paragraph().add_run().add_picture(image)
    doc.add_paragraph('图1.1 数据采集图')
    doc.add_paragraph('表1.1 设备清单')
    doc.add_table(rows=1, cols=1).cell(0, 0).text = '设备'
    doc.add_paragraph('图1.2 这是一段不邻接图片的独立文字')
    path = tmp_path / 'source.docx'; doc.save(path)
    blocks = import_docx_project(path.read_bytes())['sections'][0]['paragraphs']
    picture = next(b for b in blocks if b.get('type') == 'image')
    table = next(b for b in blocks if b.get('type') == 'table')
    assert picture['image']['caption'] == '图1.1 数据采集图'
    assert table['table']['caption'] == '表1.1 设备清单'
    assert sum('图1.1 数据采集图' in b.get('text', '') for b in blocks) == 0
    assert sum('表1.1 设备清单' in b.get('text', '') for b in blocks) == 0
    assert any(b.get('text') == '图1.2 这是一段不邻接图片的独立文字' for b in blocks)


def test_created_tables_have_visible_grid_and_inspector_rejects_missing_lines(master, tmp_path):
    _add_objects(master)
    data = rendered(master, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    generated = root.xpath('.//w:sdt[w:sdtPr/w:tag[starts-with(@w:val, "pg-block:")]]//w:tbl', namespaces=NS)
    assert generated
    borders = generated[0].find(f'{qn("w:tblPr")}/{qn("w:tblBorders")}')
    assert borders is not None
    assert all(borders.find(qn('w:' + edge)) is not None for edge in ('top','bottom','left','right','insideH','insideV'))
    assert inspect(master, data)['status'] == 'pass'
    def remove_borders(root):
        table = root.xpath('.//w:sdt[w:sdtPr/w:tag[starts-with(@w:val, "pg-block:")]]//w:tbl', namespaces=NS)[0]
        node = table.find(f'{qn("w:tblPr")}/{qn("w:tblBorders")}')
        node.getparent().remove(node)
    assert inspect(master, rewrite(data, 'word/document.xml', remove_borders))['status'] == 'fail'


def test_imported_table_borders_survive_safe_rebuild(tmp_path):
    from app.api.projects import _apply_imported_docx
    from app.models.project import WritingProject, ProjectSection
    from app.services.table_borders import EDGES
    doc = Document(); doc.add_heading('第1章 正文', 1)
    doc.add_paragraph('表1.1 设备清单')
    table = doc.add_table(rows=1, cols=1); table.cell(0, 0).text = '设备'
    table.style = 'Table Grid'
    path = tmp_path / 'source-borders.docx'; doc.save(path)
    imported = import_docx_project(path.read_bytes())
    imported_table = next(b for s in imported['sections'] for b in s['paragraphs'] if b.get('type') == 'table')
    assert all(imported_table['table']['borders'][edge]['style'] == 'single' for edge in EDGES)
    project = WritingProject(id='border-import', template_id='t', template_snapshot={'source_execution': {'test': True}, 'sections': []},
        sections=[ProjectSection(key='one', type='heading1', title='第1章 正文')])
    _apply_imported_docx(project, imported)
    saved_table = next(b.table for s in project.sections for b in s.paragraphs if b.type.value == 'table')
    assert not saved_table.source_xml
    assert saved_table.borders.model_dump() == imported_table['table']['borders']


def test_template_explicitly_borderless_table_is_preserved(master, tmp_path):
    from docx.oxml import OxmlElement
    from tests.test_master_cover_label_visibility import _recompile
    doc = Document(master[0])
    doc.add_paragraph('表1.1 正文表示例')
    table = doc.add_table(rows=1, cols=1); table.cell(0, 0).text = '模板内容'
    borders = OxmlElement('w:tblBorders')
    for edge in ('top','bottom','left','right','insideH','insideV'):
        node = OxmlElement('w:' + edge); node.set(qn('w:val'), 'nil'); borders.append(node)
    table._tbl.tblPr.append(borders); doc.save(master[0])
    case = _recompile(master)
    assert all(value['style'] == 'nil' for value in case[1].source_execution['table_borders'].values())
    _add_objects(case)
    data = rendered(case, tmp_path)
    assert inspect(case, data)['status'] == 'pass'
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    tables = root.xpath('.//w:sdt[w:sdtPr/w:tag[starts-with(@w:val, "pg-block:")]]//w:tbl', namespaces=NS)
    generated = tables[0].find(f'{qn("w:tblPr")}/{qn("w:tblBorders")}')
    assert all(node.get(qn('w:val')) == 'nil' for node in generated)


def test_caption_binding_is_included_in_independent_import_inventory(tmp_path):
    from app.services.docx_import_inventory import inventory_docx_source, reconcile_docx_import
    image = io.BytesIO(); Image.new('RGB', (20, 10), 'white').save(image, format='PNG'); image.seek(0)
    doc = Document(); doc.add_heading('第1章 正文', 1)
    doc.add_paragraph().add_run().add_picture(image)
    doc.add_paragraph('图1.1 图片')
    doc.add_paragraph('表1.1 表格')
    doc.add_table(rows=1, cols=1).cell(0, 0).text = '文字'
    data = io.BytesIO(); doc.save(data); source = data.getvalue()
    imported = import_docx_project(source)
    report = reconcile_docx_import(inventory_docx_source(source), imported)
    assert report['status'] == 'complete', report['unresolved']
    image = next(b for s in imported['sections'] for b in s['paragraphs'] if b.get('type') == 'image')
    image['image']['caption'] = '图1.1 已篡改'
    report = reconcile_docx_import(inventory_docx_source(source), imported)
    assert report['status'] == 'incomplete'


def test_imported_borderless_table_remains_borderless(tmp_path):
    from app.services.table_borders import EDGES
    doc = Document(); doc.add_heading('第1章 正文', 1)
    doc.add_table(rows=1, cols=1).cell(0, 0).text = '正文'
    path = tmp_path / 'borderless-source.docx'; doc.save(path)
    blocks = import_docx_project(path.read_bytes())['sections'][0]['paragraphs']
    borders = next(b['table']['borders'] for b in blocks if b.get('type') == 'table')
    assert all(borders[edge]['style'] == 'nil' for edge in EDGES)


@pytest.mark.parametrize('damage', ['left_chars', 'hanging_chars', 'invalid_indent', 'unknown_style'])
def test_image_alignment_rejects_changed_or_unresolvable_indent(master, tmp_path, damage):
    _add_objects(master)
    data = rendered(master, tmp_path)
    assert inspect(master, data)['status'] == 'pass'

    def corrupt(root):
        paragraph = root.xpath(
            './/w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-image"]//w:p[.//w:drawing]',
            namespaces=NS)[0]
        ppr = paragraph.find(qn('w:pPr'))
        indent = ppr.find(qn('w:ind'))
        if damage == 'left_chars':
            indent.set(qn('w:leftChars'), '200')
        elif damage == 'hanging_chars':
            indent.set(qn('w:hangingChars'), '200')
        elif damage == 'invalid_indent':
            indent.set(qn('w:left'), 'invalid')
        else:
            style = etree.SubElement(ppr, qn('w:pStyle'))
            style.set(qn('w:val'), 'MissingImageParagraphStyle')

    report = inspect(master, rewrite(data, 'word/document.xml', corrupt))
    assert any(c['rule'] == 'master.figure_alignment' and c['object'] == 'visibility-image'
               and c['status'] == 'fail' for c in report['checks'])


def test_image_alignment_checks_inherited_indent_when_word_omits_zero(master, tmp_path):
    _add_objects(master)
    data = rendered(master, tmp_path)

    def remove_direct_indent(root):
        paragraph = root.xpath(
            './/w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-image"]//w:p[.//w:drawing]',
            namespaces=NS)[0]
        ppr = paragraph.find(qn('w:pPr'))
        indent = ppr.find(qn('w:ind'))
        for name in ('left', 'leftChars'):
            indent.attrib.pop(qn('w:' + name), None)
        style = ppr.find(qn('w:pStyle'))
        if style is None:
            style = etree.SubElement(ppr, qn('w:pStyle'))
        style.set(qn('w:val'), 'Normal')

    def inherit_nonzero_indent(root):
        normal = root.xpath('.//w:style[@w:styleId="Normal"]', namespaces=NS)[0]
        ppr = normal.find(qn('w:pPr'))
        if ppr is None:
            ppr = etree.SubElement(normal, qn('w:pPr'))
        indent = ppr.find(qn('w:ind'))
        if indent is None:
            indent = etree.SubElement(ppr, qn('w:ind'))
        indent.set(qn('w:leftChars'), '200')

    data = rewrite(data, 'word/document.xml', remove_direct_indent)
    data = rewrite(data, 'word/styles.xml', inherit_nonzero_indent)
    report = inspect(master, data)
    assert any(c['rule'] == 'master.figure_alignment' and c['object'] == 'visibility-image'
               and c['status'] == 'fail' for c in report['checks'])


@pytest.mark.parametrize('damage', ['duplicate_set', 'duplicate_edge', 'theme_color', 'cell_override', 'logical_edge'])
def test_table_border_inspection_rejects_unmodeled_overrides(master, tmp_path, damage):
    _add_objects(master)
    data = rendered(master, tmp_path)
    assert inspect(master, data)['status'] == 'pass'

    def corrupt(root):
        table = root.xpath(
            './/w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl',
            namespaces=NS)[0]
        tblpr = table.find(qn('w:tblPr'))
        borders = tblpr.find(qn('w:tblBorders'))
        if damage == 'duplicate_set':
            tblpr.append(copy.deepcopy(borders))
        elif damage == 'duplicate_edge':
            borders.append(copy.deepcopy(borders.find(qn('w:top'))))
        elif damage == 'theme_color':
            borders.find(qn('w:top')).set(qn('w:themeColor'), 'accent1')
        elif damage == 'logical_edge':
            borders.append(etree.Element(qn('w:start')))
        else:
            tcpr = table.find('.//' + qn('w:tcPr'))
            etree.SubElement(tcpr, qn('w:tcBorders'))

    report = inspect(master, rewrite(data, 'word/document.xml', corrupt))
    assert any(c['rule'] == 'master.table_borders' and c['object'] == 'visibility-table'
               and c['status'] == 'fail' for c in report['checks'])
