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
from tests.test_docx_import_inventory import _smartart_source


@pytest.mark.parametrize(("size", "result"), [(900, "pass"), (2200, "fail")])
def test_frozen_master_preserves_smartart_and_checks_its_actual_label_size(master, tmp_path, size, result):
    from app.api.projects import _apply_imported_docx, _parse_par
    from app.services.docx_import_inventory import reconcile_applied_docx_import

    _, template, project, _ = master
    project.template_snapshot = template.model_dump(mode="json")
    imported = import_docx_project(_smartart_source(size))
    merge = _apply_imported_docx(project, imported)
    applied = reconcile_applied_docx_import(imported, project, merge["source_section_mapping"])
    assert applied["status"] == "complete", applied["unresolved"]
    section = next(section for section in project.sections if any(block.type == "image" for block in section.paragraphs))
    index = next(i for i, block in enumerate(section.paragraphs) if block.type == "image")
    block = section.paragraphs[index]
    posted = block.model_dump(mode="json")
    posted["image"]["caption"] = "调整后的图题"
    section.paragraphs[index] = _parse_par(posted, project.id, block)
    assert section.paragraphs[index].image.smartart == block.image.smartart
    data = rendered(master, tmp_path)
    report = inspect(master, data)
    assert report["status"] == result, [c for c in report["checks"] if c["status"] == "fail"]
    evidence = next(c for c in report["checks"] if c["rule"] == "master.smartart_label_size")
    assert evidence["actual"] == size / 100
    assert evidence["status"] == result
    assert f"/figure:1.1/block:{block.id}" in evidence["object"]
    if result == "fail":
        from app.services.docx_output_check import safe_output_check_diagnostics
        diag = safe_output_check_diagnostics(report)
        assert any(
            item["rule"] == "master.smartart_label_size"
            and "/figure:1.1/" in item["object"]
            for item in diag["findings"]
        )


def test_smartart_preserves_resolved_source_theme_colors_and_fonts_across_different_master_theme(master, tmp_path):
    from app.api.projects import _apply_imported_docx
    from app.services.docx_smartart import append_inline_smartart

    _, template, project, _ = master
    project.template_snapshot = template.model_dump(mode="json")
    imported = import_docx_project(_smartart_source(
        900, accent="E06666", scheme_ref="accent1", minor_latin="Georgia",
        major_ea="黑体", east_asia_lang="zh-CN", theme_font_ref="minor", style_matrix_line_w=50800,
    ))
    _apply_imported_docx(project, imported)

    data = rendered(master, tmp_path)
    report = inspect(master, data)
    assert report["status"] == "pass", [c for c in report["checks"] if c["status"] == "fail"]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        diagram_colors = [archive.read(name).decode() for name in archive.namelist()
                          if name.startswith("word/diagrams/pg-") and name.endswith("-cs.xml")]
        diagram_data = [archive.read(name).decode() for name in archive.namelist()
                        if name.startswith("word/diagrams/pg-") and name.endswith("-dm.xml")]
        diagram_drawing = [archive.read(name).decode() for name in archive.namelist()
                           if name.startswith("word/diagrams/pg-") and name.endswith("-dr.xml")]
    assert len(diagram_colors) == 1
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    cs_root = etree.fromstring(diagram_colors[0].encode())
    assert [el.get("val") for el in cs_root.iter(f"{{{a_ns}}}srgbClr")] == ["E06666"]
    assert "schemeClr" not in diagram_colors[0]
    dm_root = etree.fromstring(diagram_data[0].encode())
    assert [el.get("typeface") for el in dm_root.iter(f"{{{a_ns}}}latin")] == ["Georgia"]
    dr_root = etree.fromstring(diagram_drawing[0].encode())
    assert dr_root.find(f".//{{{a_ns}}}ln").get("w") == "50800"
    assert all(el.get("idx") == "0" for el in dr_root.iter(f"{{{a_ns}}}lnRef"))

    block = next(b for s in project.sections for b in s.paragraphs if b.type == "image")
    unresolved_color = dict(block.image.smartart)
    unresolved_color["cs"] = unresolved_color["cs"].replace("srgbClr", "schemeClr").replace('val="E06666"', 'val="accent1"')
    with pytest.raises(ValueError, match="主题色"):
        append_inline_smartart(
            unresolved_color, {}, etree.Element("Relationships"), etree.Element("Types"), "tok", 5.0, 2.5,
        )
    unresolved_font = dict(block.image.smartart)
    unresolved_font["dm"] = unresolved_font["dm"].replace('typeface="Georgia"', 'typeface="+mn-lt"')
    with pytest.raises(ValueError, match="主题字体"):
        append_inline_smartart(
            unresolved_font, {}, etree.Element("Relationships"), etree.Element("Types"), "tok", 5.0, 2.5,
        )
    unresolved_style = dict(block.image.smartart)
    unresolved_style["dr"] = unresolved_style["dr"].replace('lnRef idx="0"', 'lnRef idx="2"')
    with pytest.raises(ValueError, match="主题样式"):
        append_inline_smartart(
            unresolved_style, {}, etree.Element("Relationships"), etree.Element("Types"), "tok", 5.0, 2.5,
        )


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
    report = inspect(master, rewrite(data, 'word/document.xml', remove_borders))
    border_check = next(item for item in report['checks']
                        if item['rule'] == 'master.table_borders'
                        and item['object'] == 'visibility-table')
    assert border_check['status'] == 'fail'
    assert border_check['check_id'] in report['failed_checks']
    assert report['format_accuracy']['percentage'] > 90.0
    assert report['status'] == 'fail'
    assert report['hard_blockers']


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


@pytest.mark.parametrize('damage', ['duplicate_set', 'duplicate_edge', 'theme_color', 'cell_override', 'cell_unknown_attribute', 'logical_edge'])
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
        elif damage == 'cell_override':
            tcpr = table.find('.//' + qn('w:tcPr'))
            border = etree.SubElement(tcpr, qn('w:tcBorders'))
            etree.SubElement(border, qn('w:bottom')).set(qn('w:val'), 'single')
        else:
            tcpr = table.find('.//' + qn('w:tcPr'))
            etree.SubElement(tcpr, qn('w:tcBorders')).set(qn('w:unknown'), '1')

    report = inspect(master, rewrite(data, 'word/document.xml', corrupt))
    assert any(c['rule'] == 'master.table_borders' and c['object'] == 'visibility-table'
               and c['status'] == 'fail' for c in report['checks'])


def test_empty_cell_borders_preserve_table_rules_and_pass_inspection(master, tmp_path):
    from app.services.table_borders import observe_table_borders

    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    table.style = 'Table Grid'
    expected = observe_table_borders(table)
    etree.SubElement(table.cell(0, 0)._tc.get_or_add_tcPr(), qn('w:tcBorders'))
    assert observe_table_borders(table) == expected

    _add_objects(master)
    data = rendered(master, tmp_path)
    def add_empty(root):
        table = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl', namespaces=NS)[0]
        etree.SubElement(table.find('.//' + qn('w:tcPr')), qn('w:tcBorders'))
    report = inspect(master, rewrite(data, 'word/document.xml', add_empty))
    assert any(c['rule'] == 'master.table_borders' and c['object'] == 'visibility-table'
               and c['status'] == 'pass' for c in report['checks'])


@pytest.mark.parametrize('kind', ['edge', 'attribute', 'text'])
def test_cell_border_overrides_remain_unsupported(kind):
    from app.services.table_borders import observe_table_borders

    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    borders = etree.SubElement(table.cell(0, 0)._tc.get_or_add_tcPr(), qn('w:tcBorders'))
    if kind == 'edge':
        etree.SubElement(borders, qn('w:bottom')).set(qn('w:val'), 'single')
    elif kind == 'attribute':
        borders.set(qn('w:unknown'), '1')
    else:
        borders.text = 'unexpected'
    with pytest.raises(ValueError, match='单元格独立边框'):
        observe_table_borders(table)


@pytest.mark.parametrize('edge', ['start', 'end'])
@pytest.mark.parametrize('layer', ['table', 'style'])
def test_logical_side_borders_block_unverified_master_profile(master, tmp_path, edge, layer):
    from docx.oxml import OxmlElement
    from tests.test_master_cover_label_visibility import _recompile
    from app.services.source_template_profile import ProfileError, sign_profile

    source, previous, _, _ = master
    doc = Document(source)
    doc.add_paragraph('表1.1 正文表示例')
    table = doc.add_table(rows=1, cols=1)
    if layer == 'table':
        pr = table._tbl.tblPr
    else:
        from docx.enum.style import WD_STYLE_TYPE
        style = doc.styles.add_style('LogicalBorderStyle', WD_STYLE_TYPE.TABLE)
        table.style = style
        pr = OxmlElement('w:tblPr')
        style.element.append(pr)
    borders = OxmlElement('w:tblBorders')
    logical = OxmlElement('w:' + edge)
    logical.set(qn('w:val'), 'single')
    borders.append(logical)
    pr.append(borders)
    doc.save(source)

    with pytest.raises(ProfileError, match='逻辑方向'):
        _recompile(master)

    # A profile approved with an older parser must also be rechecked against
    # its digest-bound source before rendering or artifact verification.
    import hashlib
    from app.services.source_template_profile import verify_profile
    legacy = previous.model_copy(deep=True)
    profile = dict(legacy.source_execution)
    profile['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    legacy.source_execution = sign_profile(profile)
    with pytest.raises(ProfileError, match='逻辑方向'):
        verify_profile(legacy, source.read_bytes())


def test_import_reports_logical_table_border_as_unsupported(tmp_path):
    from docx.oxml import OxmlElement
    document = Document()
    document.add_heading('第1章 正文', 1)
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = '数据'
    borders = OxmlElement('w:tblBorders')
    start = OxmlElement('w:start')
    start.set(qn('w:val'), 'single')
    borders.append(start)
    table._tbl.tblPr.append(borders)
    source = tmp_path / 'logical.docx'
    document.save(source)
    blocks = import_docx_project(source.read_bytes())['sections'][0]['paragraphs']
    imported = next(block for block in blocks if block.get('type') == 'table')
    assert '逻辑方向' in imported['_border_error']
    assert imported['table']['source_xml']


@pytest.mark.parametrize(('size', 'expected_fail'), [(900, False), (2200, True)])
def test_smartart_typography_is_evaluated_by_requirement_check(master, monkeypatch, size, expected_fail):
    from app.api.projects import _apply_imported_docx
    from app.services.requirement_check import check_requirements

    source, template, project, _ = master
    monkeypatch.setattr('app.services.template_source.resolve_template_source', lambda _: source)
    project.template_snapshot = template.model_dump(mode='json')
    imported = import_docx_project(_smartart_source(size))
    _apply_imported_docx(project, imported)
    block = next(b for s in project.sections for b in s.paragraphs if b.type == 'image')
    block.image.caption = '图 1.1 系统架构图'
    rule = {
        'id': 'image-font', 'type': 'manual', 'checker': '', 'target': 'document',
        'params': {'image_typography_constraint': 'caption_font_size_upper_bound'},
        'severity': 'error', 'enforce': 'hard',
        'description': '图中标注符号文字字号不大于图题的字号',
        'source': '撰写规范',
    }
    findings, not_checked = check_requirements(project, {'resolved_items': [rule]}, template)
    assert not_checked == []
    if expected_fail:
        assert len(findings) == 1
        finding = findings[0]
        assert finding['enforce'] == 'hard'
        assert finding['severity'] == 'error'
        assert '图 1.1' in finding['message']
        assert '系统架构图' in finding['message']
        assert '22pt' in finding['message']
        assert finding['location']['block_id'] == block.id
        assert finding['section_key']
    else:
        assert findings == []


def test_mixed_smartart_and_raster_preserves_both_findings_and_pending_review(master, monkeypatch):
    from app.api.projects import _apply_imported_docx
    from app.models.project import ImageBlock, ParagraphBlock
    from app.services.requirement_check import check_requirements

    source, template, project, _ = master
    monkeypatch.setattr('app.services.template_source.resolve_template_source', lambda _: source)
    project.template_snapshot = template.model_dump(mode='json')
    imported = import_docx_project(_smartart_source(2200))
    _apply_imported_docx(project, imported)
    target_section = next(s for s in project.sections if any(b.type == 'image' for b in s.paragraphs))
    target_section.paragraphs.append(ParagraphBlock(
        id='raster-2', type='image', image=ImageBlock(url='/media/uploads/shot.png', caption='实测截图'),
    ))
    rule = {
        'id': 'image-font', 'type': 'manual', 'checker': '', 'target': 'document',
        'params': {'image_typography_constraint': 'caption_font_size_upper_bound'},
        'severity': 'error', 'enforce': 'hard',
        'description': '图中标注符号文字字号不大于图题的字号',
        'source': '撰写规范',
    }
    findings, not_checked = check_requirements(project, {'resolved_items': [rule]}, template)
    assert len(findings) == 1
    assert '图 1.1' in findings[0]['message']
    assert [(item['id'], item['reason'], item['enforce']) for item in not_checked] == [
        ('image-font', 'image_typography_unverified', 'hard'),
    ]


def test_red_example_prose_and_abstract_do_not_export_red_text(tmp_path):
    from app.core.export.docx_exporter import export_project_to_docx
    from app.models.project import ParagraphBlock, ProjectSection, WritingProject
    from app.models.template import MetaField
    from app.services.docx_output_check import inspect_docx_output
    from app.services.source_template_profile import flatten

    doc = Document()
    normal = doc.styles['Normal']
    normal.font.size = Pt(12)
    normal.font.name = 'Times New Roman'
    normal.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '宋体')
    normal.paragraph_format.line_spacing = 1.5
    doc.add_paragraph('题目：示例题目')
    doc.add_heading('摘要', 1)
    red_abstract = doc.add_paragraph('（示例，请按实际课题改写）' + '中文摘要示例内容。' * 18)
    red_abstract.runs[0].font.color.rgb = RGBColor(255, 0, 0)
    doc.add_paragraph('（中文摘要应写明研究方法、研究内容和主要结果或结论。）')
    doc.add_heading('Abstract', 1)
    red_en_abstract = doc.add_paragraph('(Example only. Please rewrite it for your own project.) ' + 'Sample text. ' * 18)
    red_en_abstract.runs[0].font.color.rgb = RGBColor(255, 0, 0)
    doc.add_heading('第1章 绪论', 1)
    for _ in range(2):
        red_body = doc.add_paragraph('（示例，请按实际课题改写）' + '红字正文示例内容。' * 20)
        red_body.runs[0].font.color.rgb = RGBColor(255, 0, 0)
    doc.add_paragraph('正常默认颜色正文段落内容。' * 8)
    red_sect_hint = doc.add_paragraph('（说明：分节符占位示例红字提示）')
    red_sect_hint.runs[0].font.color.rgb = RGBColor(255, 0, 0)
    from docx.oxml import OxmlElement
    ppr = red_sect_hint._p.get_or_add_pPr()
    ppr_rpr = OxmlElement('w:rPr')
    ppr_color = OxmlElement('w:color')
    ppr_color.set(qn('w:val'), 'FF0000')
    ppr_rpr.append(ppr_color)
    ppr.append(ppr_rpr)
    doc.add_section()
    doc.add_heading('第2章 结论', 1)
    doc.add_paragraph('正常默认颜色结论正文段落内容。' * 8)

    source = tmp_path / 'red-examples.docx'
    doc.save(source)
    spec = extract_writing_specification(source)
    draft = PaperTemplate(
        id='red-example-master', name='red-example', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'],
        meta_fields=[MetaField(key='title', label='题目')],
    )
    template = compile_profile(
        draft, source,
        ConfirmWritingProfile(
            expected_specification_sha256=spec.specification_sha256,
            accept_recommended=True, accept_new_object_defaults=True, remove_template_instructions=True,
        ),
        actor='teacher',
    )
    assert 'font.color' not in template.source_execution['body_properties']
    for heading in template.source_execution['headings']:
        assert 'font.color' not in heading['properties']

    project = WritingProject(
        id='red-example-proj', template_id=template.id, meta={'title': '正式论文题目'},
        sections=[
            ProjectSection(key=n.key, type=n.type, title=n.title,
                           paragraphs=[ParagraphBlock(id=f'{n.key}-p', text='学生正式正文内容。')])
            for n in flatten(template.sections) if n.type.value not in {'cover', 'toc'}
        ],
    )
    output = tmp_path / 'exported.docx'
    export_project_to_docx(project, template, str(output), template_path=str(source))
    data = output.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    colors = [el.get(qn('w:val'), '').upper() for el in root.findall('.//' + qn('w:color'))]
    assert 'FF0000' not in colors
    report = inspect_docx_output(data, project, template, source_template_bytes=source.read_bytes())
    assert report['status'] == 'pass', [c for c in report['checks'] if c['status'] != 'pass']


def test_bold_table_header_row_does_not_make_table_body_cells_bold(master, tmp_path):
    from app.core.extract.effective_format import EffectiveFormatResolver
    from tests.test_master_cover_label_visibility import _recompile

    doc = Document(master[0])
    doc.add_paragraph('表1.1 系统功能与稳定性测试记录表')
    sample = doc.add_table(rows=2, cols=2)
    sample.style = 'Table Grid'
    for col_idx, header_text in enumerate(('测试功能模块与名称（详细表头文字）', '预期设计目标与指标说明（详细表头文字）')):
        run = sample.cell(0, col_idx).paragraphs[0].add_run(header_text)
        run.font.bold = True
    sample.cell(1, 0).text = '采集'
    sample.cell(1, 1).text = '正常'
    doc.save(master[0])

    case = _recompile(master)
    table_props = case[1].source_execution['object_properties']['table_text']
    assert table_props['font.bold'] is False

    _add_objects(case)
    data = rendered(case, tmp_path)
    report = inspect(case, data)
    assert report['status'] == 'pass', [c for c in report['checks'] if c['status'] != 'pass']
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        styles = etree.fromstring(archive.read('word/styles.xml'))
    resolver = EffectiveFormatResolver(styles)
    tables = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl', namespaces=NS)
    assert len(tables) == 1
    for p in tables[0].findall('.//' + qn('w:p')):
        _, _, runs, _ = resolver.paragraph(p)
        assert runs and all(run['properties'].get('font.bold') is False for run in runs)


def test_generated_table_rows_set_cant_split_and_inspector_rejects_missing(master, tmp_path):
    _add_objects(master)
    data = rendered(master, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    tables = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl', namespaces=NS)
    assert len(tables) == 1
    rows = tables[0].findall(qn('w:tr'))
    assert rows and all(row.find(f'{qn("w:trPr")}/{qn("w:cantSplit")}') is not None for row in rows)
    assert inspect(master, data)['status'] == 'pass'

    def remove_cant_split(root):
        table = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl', namespaces=NS)[0]
        for node in table.findall(f'.//{qn("w:trPr")}/{qn("w:cantSplit")}'):
            node.getparent().remove(node)

    report = inspect(master, rewrite(data, 'word/document.xml', remove_cant_split))
    split_check = next(item for item in report['checks']
                       if item['rule'] == 'master.table_row_cant_split'
                       and item['object'] == 'visibility-table')
    assert split_check['status'] == 'fail'
    assert split_check['classification'] == 'hard_block'
    assert report['status'] == 'fail'

    def disable_cant_split(root):
        table = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl', namespaces=NS)[0]
        node = table.find(f'.//{qn("w:trPr")}/{qn("w:cantSplit")}')
        node.set(qn('w:val'), '0')

    disabled = inspect(master, rewrite(data, 'word/document.xml', disable_cant_split))
    assert any(item['rule'] == 'master.table_row_cant_split' and item['status'] == 'fail'
               for item in disabled['checks'])
