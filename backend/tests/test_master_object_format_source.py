"""Object formatting must come from body examples or the reviewed object style."""
from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document
from docx.shared import Pt
from lxml import etree

from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS, qn
from app.models.template import DEFAULT_STYLES, PaperTemplate, TextStyle
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.source_template_profile import ConfirmWritingProfile, compile_profile, sign_profile, verify_profile, ProfileError
from tests.test_source_template_profile import inspect, master, rendered, rewrite
from tests.test_master_content_visibility import _add_objects


def _prepare(master, *, body_table=False, configured_style=None, configured_caption=None, configured_figure_caption=None, overrides=None):
    source, previous, project, _ = master
    doc = Document(source)
    for row in doc.tables[0].rows:
        row.cells[1].text = '示例值'
        for cell in row.cells:
            for run in cell.paragraphs[0].runs:
                run.font.size = Pt(16)
                run.font.name = 'CoverFont'
                run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '封面字体')
    if body_table:
        doc.add_paragraph('表1.1 正文样表')
        run = doc.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0].add_run('正文表格样例')
        run.font.size = Pt(9)
        run.font.name = 'BodyTableFont'
        run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '仿宋')
    doc.save(source)
    spec = extract_writing_specification(source)
    styles = import_template_styles(source)['styles']
    styles.update({key: DEFAULT_STYLES[key].model_copy(deep=True)
                   for key in ('table_cell', 'table_caption', 'figure_caption')})
    if configured_style is not None:
        styles['table_cell'] = configured_style
    if configured_caption is not None:
        styles['table_caption'] = configured_caption
    if configured_figure_caption is not None:
        styles['figure_caption'] = configured_figure_caption
    template = PaperTemplate(id=previous.id, name='格式来源测试', source_docx=source.name,
        sections=extract_section_tree(source), styles=styles, meta_fields=previous.meta_fields)
    selected_overrides = overrides(spec) if overrides else {}
    request = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True,
        overrides=selected_overrides)
    result = compile_profile(template, source, request, actor='teacher-test')
    return source, result, project, request, spec


@pytest.mark.parametrize('role,block', [('figure_caption', 'visibility-image'), ('table_caption', 'visibility-table')])
def test_caption_alignment_follows_confirmed_profile(master, tmp_path, role, block):
    configured = TextStyle(font_zh='宋体', size_pt=10.5, align='left')
    kwargs = {'configured_figure_caption': configured} if role == 'figure_caption' else {'configured_caption': configured}
    source, template, project, request, _ = _prepare(master, **kwargs)
    assert template.source_execution['object_properties'][role]['paragraph.align'] == 'left'
    case = (source, template, project, request)
    _add_objects(case)
    data = rendered(case, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    paragraphs = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent/w:p[w:r/w:t]',
                            namespaces=NS, tag='pg-block:' + block)
    caption = paragraphs[-1] if role == 'figure_caption' else paragraphs[0]
    assert caption.find(f'{qn("w:pPr")}/{qn("w:jc")}').get(qn('w:val')) == 'left'
    assert inspect(case, data)['status'] == 'pass'

    def corrupt(root):
        paragraphs = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent/w:p[w:r/w:t]',
                                namespaces=NS, tag='pg-block:' + block)
        caption = paragraphs[-1] if role == 'figure_caption' else paragraphs[0]
        caption.find(f'{qn("w:pPr")}/{qn("w:jc")}').set(qn('w:val'), 'center')

    report = inspect(case, rewrite(data, 'word/document.xml', corrupt))
    assert any(item['rule'] == 'master.effective_format' and item['status'] == 'fail'
               for item in report['checks'])


@pytest.mark.parametrize('damage', ['size', 'font', 'alignment'])
def test_extracted_table_caption_rule_is_rendered_and_independently_checked(master, tmp_path, monkeypatch, damage):
    from app.services.policy_format import extract_format_overrides
    from app.services.policy_resolver import apply_requirement_format_overrides

    source, template, project, request, _ = _prepare(
        master, configured_caption=TextStyle(font_zh='黑体', size_pt=16, align='left'),
    )
    monkeypatch.setattr('app.services.template_source.resolve_template_source', lambda _: source)
    guideline = (
        '3.图表及公式格式要求 表：表应有表题，用五号宋体，'
        '与表号之间置一字空格，置于表的上方居中，且表题与表在同一页。'
        '（四）毕业设计报告（论文）结尾部分格式要求'
    )
    snapshot = {'format_overrides': extract_format_overrides(guideline)}
    effective = apply_requirement_format_overrides(template, snapshot)
    case = (source, effective, project, request)
    _add_objects(case)
    data = rendered(case, tmp_path)
    report = inspect(case, data)
    assert report['status'] == 'pass', [item for item in report['checks'] if item['status'] != 'pass']

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    caption = root.xpath(
        './/w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]/w:sdtContent/w:p',
        namespaces=NS,
    )[0]
    run = caption.xpath('.//w:r[w:t]', namespaces=NS)[0]
    assert run.find(f'{qn("w:rPr")}/{qn("w:sz")}').get(qn('w:val')) == '21'
    assert run.find(f'{qn("w:rPr")}/{qn("w:rFonts")}').get(qn('w:eastAsia')) == '宋体'
    assert caption.find(f'{qn("w:pPr")}/{qn("w:jc")}').get(qn('w:val')) == 'center'

    def corrupt(root):
        caption = root.xpath(
            './/w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]/w:sdtContent/w:p',
            namespaces=NS,
        )[0]
        if damage == 'alignment':
            caption.find(f'{qn("w:pPr")}/{qn("w:jc")}').set(qn('w:val'), 'left')
        else:
            run = caption.xpath('.//w:r[w:t]', namespaces=NS)[0]
            if damage == 'size':
                run.find(f'{qn("w:rPr")}/{qn("w:sz")}').set(qn('w:val'), '32')
            else:
                run.find(f'{qn("w:rPr")}/{qn("w:rFonts")}').set(qn('w:eastAsia'), '黑体')

    changed = inspect(case, rewrite(data, 'word/document.xml', corrupt))
    assert any(item['rule'] == 'master.effective_format' and item['status'] == 'fail'
               for item in changed['checks'])


@pytest.mark.parametrize('role,block,label', [
    ('figure_caption', 'visibility-image', '图'),
    ('table_caption', 'visibility-table', '表'),
])
def test_number_title_gap_from_guideline_is_rendered_and_checked(master, tmp_path, monkeypatch, role, block, label):
    from app.services.policy_format import extract_format_overrides
    from app.services.policy_resolver import apply_requirement_format_overrides

    source, template, project, request, _ = _prepare(master)
    monkeypatch.setattr('app.services.template_source.resolve_template_source', lambda _: source)
    guideline = ('3.图表及公式格式要求 插图：图号与图题文字间置一字空格。'
                 '图题用五号宋体。表：表应有表题，用五号宋体，'
                 '与表号之间置一字空格。'
                 '（四）毕业设计报告（论文）结尾部分格式要求')
    effective = apply_requirement_format_overrides(template, {'format_overrides': extract_format_overrides(guideline)})
    case = (source, effective, project, request)
    _add_objects(case)
    data = rendered(case, tmp_path)
    report = inspect(case, data)
    assert report['status'] == 'pass', [item for item in report['checks'] if item['status'] != 'pass']

    def caption(root):
        nodes = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent/w:p[w:r/w:t]',
                           namespaces=NS, tag='pg-block:' + block)
        return nodes[-1] if role == 'figure_caption' else nodes[0]

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    text = ''.join(caption(root).xpath('.//w:t/text()', namespaces=NS))
    assert text.startswith(label + ' ') and '\u3000' in text

    def corrupt(root):
        text_node = caption(root).xpath('.//w:t[contains(., "　")]', namespaces=NS)[0]
        text_node.text = text_node.text.replace('\u3000', ' ')

    altered = inspect(case, rewrite(data, 'word/document.xml', corrupt))
    assert altered['status'] == 'fail'
    assert any(item['rule'] == 'master.' + role and item['status'] == 'fail'
               for item in altered['checks'])


@pytest.mark.parametrize('role,block', [('figure_caption', 'visibility-image'), ('table_caption', 'visibility-table')])
@pytest.mark.parametrize('damage', ['scaled', 'tracking', 'missing', 'duplicate'])
def test_one_character_gap_rejects_invalid_geometry(master, tmp_path, monkeypatch, role, block, damage):
    from app.services.policy_resolver import apply_requirement_format_overrides

    source, template, project, request, _ = _prepare(master)
    monkeypatch.setattr('app.services.template_source.resolve_template_source', lambda _: source)
    effective = apply_requirement_format_overrides(template, {'format_overrides': {
        role: {'number_title_gap': 'one_character'},
    }})
    case = (source, effective, project, request)
    _add_objects(case)
    data = rendered(case, tmp_path)

    def corrupt(root):
        run = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent/w:p/w:r[w:t="　"]',
                         namespaces=NS, tag='pg-block:' + block)[0]
        rpr = run.find(qn('w:rPr'))
        if damage == 'scaled':
            rpr.find(qn('w:w')).set(qn('w:val'), '50')
        elif damage == 'tracking':
            rpr.find(qn('w:spacing')).set(qn('w:val'), '20')
        elif damage == 'missing':
            rpr.remove(rpr.find(qn('w:w')))
        else:
            etree.SubElement(rpr, qn('w:w')).set(qn('w:val'), '100')

    altered = inspect(case, rewrite(data, 'word/document.xml', corrupt))
    assert altered['status'] == 'fail'


def test_cover_table_does_not_supply_body_table_format(master, tmp_path):
    source, template, project, request, spec = _prepare(master)
    cover_cells = [n for n in spec.nodes if n.role == 'table_text' and n.text.strip()]
    assert cover_cells and all(n.details.get('in_cover') for n in cover_cells)
    props = template.source_execution['object_properties']
    for role in ('table_text', 'table_caption', 'figure_caption'):
        assert props[role]['font.size'] == {'value': 10.5, 'unit': 'pt'}
        assert props[role]['font.eastAsia'] == '宋体'
    assert any(field['properties'].get('font.size') == {'value': 16, 'unit': 'pt'}
               for field in template.source_execution['fields'])

    case = (source, template, project, request)
    _add_objects(case)
    data = rendered(case, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    for block in ('visibility-image', 'visibility-table'):
        runs = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]//w:r[w:t]',
                          namespaces=NS, tag='pg-block:' + block)
        assert runs
        for run in runs:
            rpr = run.find(qn('w:rPr'))
            assert rpr.find(qn('w:sz')).get(qn('w:val')) == '21'
            assert rpr.find(qn('w:rFonts')).get(qn('w:eastAsia')) == '宋体'
    report = inspect(case, data)
    assert report['status'] == 'pass', [item for item in report['checks'] if item['status'] != 'pass']

    def corrupt(root):
        run = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl//w:r[w:t]', namespaces=NS)[0]
        run.find(f'{qn("w:rPr")}/{qn("w:sz")}').set(qn('w:val'), '32')
    report = inspect(case, rewrite(data, 'word/document.xml', corrupt))
    assert any(item['rule'] == 'master.effective_format' and item['status'] == 'fail'
               for item in report['checks'])


def test_body_table_example_remains_authoritative(master):
    _, template, _, _, spec = _prepare(master, body_table=True)
    body = next(n for n in spec.nodes if n.role == 'table_text' and n.text == '正文表格样例')
    assert not body.details.get('in_cover')
    props = template.source_execution['object_properties']['table_text']
    assert props['font.size'] == {'value': 9, 'unit': 'pt'}
    assert props['font.eastAsia'] == '仿宋'


def test_reviewed_object_style_is_used_when_no_body_sample(master):
    style = TextStyle(font_zh='楷体', font_en='Arial', size_pt=11)
    _, template, _, _, _ = _prepare(master, configured_style=style)
    props = template.source_execution['object_properties']['table_text']
    assert props['font.size'] == {'value': 11, 'unit': 'pt'}
    assert props['font.eastAsia'] == '楷体'
    assert props['font.ascii'] == 'Arial'


def test_body_table_rule_override_wins_over_sample(master):
    def choose(spec):
        body = next(n for n in spec.nodes if n.role == 'table_text' and n.text == '正文表格样例')
        rule = next(r for r in spec.rules if r.scope['region'] == body.region and r.scope['role'] == 'table_text' and r.property == 'font.size')
        return {rule.id: {'value': 11, 'unit': 'pt'}}
    _, template, _, _, _ = _prepare(master, body_table=True, overrides=choose)
    props = template.source_execution['object_properties']['table_text']
    assert props['font.size'] == {'value': 11, 'unit': 'pt'}
    assert props['font.eastAsia'] == '仿宋'


def test_old_execution_profile_needs_reconfirmation(master):
    source, template, _, _, _ = _prepare(master)
    old = template.model_copy(deep=True)
    old.source_execution = sign_profile({**old.source_execution, 'compiler_version': 'word-master/5'})
    with pytest.raises(ProfileError, match='版本已更新'):
        verify_profile(old, source.read_bytes())


def test_configured_table_font_applies_to_mixed_text_and_is_checked(master, tmp_path):
    """A school can require one font for both Chinese and Latin table text."""
    style = TextStyle(font_zh='宋体', font_en='宋体', size_pt=10.5)
    source, template, project, request, _ = _prepare(master, configured_style=style)
    case = (source, template, project, request)
    _add_objects(case)
    table = next(block.table for block in project.sections[-1].paragraphs if block.id == 'visibility-table')
    table.cells[0][0] = '传感器DHT11'
    data = rendered(case, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    runs = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl//w:r[w:t]', namespaces=NS)
    assert runs
    for run in runs:
        rpr = run.find(qn('w:rPr'))
        fonts = rpr.find(qn('w:rFonts'))
        assert fonts.get(qn('w:eastAsia')) == '宋体'
        assert fonts.get(qn('w:ascii')) == '宋体'
        assert fonts.get(qn('w:hAnsi')) == '宋体'
        assert rpr.find(qn('w:sz')).get(qn('w:val')) == '21'
    report = inspect(case, data)
    assert report['status'] == 'pass', [item for item in report['checks'] if item['status'] != 'pass']

    def corrupt(root):
        run = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:visibility-table"]//w:tbl//w:r[w:t]', namespaces=NS)[0]
        run.find(f'{qn("w:rPr")}/{qn("w:rFonts")}').set(qn('w:ascii'), 'Arial')
    report = inspect(case, rewrite(data, 'word/document.xml', corrupt))
    assert any(item['rule'] == 'master.effective_format' and item['status'] == 'fail'
               for item in report['checks'])
