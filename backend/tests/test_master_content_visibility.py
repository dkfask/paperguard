"""Visibility of student-owned content in a template-based Word export."""
from __future__ import annotations

import pytest
from lxml import etree
from PIL import Image

from app.core.extract.xml_utils import NS, qn
from app.models.project import ImageBlock, ParagraphBlock, TableBlock
from app.services.document_assets import document_upload_dir
from tests.test_source_template_profile import inspect, master, rendered, rewrite


def _add_objects(master):
    _, _, project, _ = master
    directory = document_upload_dir()
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / (project.id + '-visibility.png')
    Image.new('RGB', (40, 20), 'blue').save(image)
    project.sections[-1].paragraphs.extend([
        ParagraphBlock(id='visibility-image', type='image',
                       image=ImageBlock(url='/media/uploads/' + image.name, caption='图片标题', width_cm=4)),
        ParagraphBlock(id='visibility-table', type='table',
                       table=TableBlock(rows=1, cols=1, cells=[['表格单元格']], caption='表格标题')),
    ])


def _target(root, master, target):
    project = master[2]
    block = project.sections[-1].paragraphs[0].id
    selector = {
        'body': ('pg-block:' + block, './/w:r[w:t]'),
        'field': ('pg-field:title', './/w:r[w:t]'),
        'image': ('pg-block:visibility-image', './/w:r[w:drawing]'),
        'figure_caption': ('pg-block:visibility-image', './/w:r[w:t]'),
        'table_cell': ('pg-block:visibility-table', './/w:tbl//w:r[w:t]'),
        'table_caption': ('pg-block:visibility-table', './w:p/w:r[w:t]'),
    }
    tag, run_path = selector[target]
    contents = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent', namespaces=NS, tag=tag)
    assert len(contents) == 1
    runs = contents[0].xpath(run_path, namespaces=NS)
    assert runs
    return runs[0], tag.removeprefix('pg-block:').removeprefix('pg-field:')


@pytest.mark.parametrize('target', ['body', 'field', 'image', 'figure_caption', 'table_cell', 'table_caption'])
@pytest.mark.parametrize('property_name', ['vanish', 'webHidden'])
def test_generated_content_hidden_by_direct_format_is_detected(master, tmp_path, target, property_name):
    _add_objects(master)
    data = rendered(master, tmp_path)
    baseline = inspect(master, data)
    assert baseline['status'] == 'pass'
    assert baseline['visibility_check_version'] == 'word-run-visibility/1'
    object_id = None

    def corrupt(root):
        nonlocal object_id
        run, object_id = _target(root, master, target)
        rpr = run.find(qn('w:rPr'))
        assert rpr is not None
        nodes = rpr.findall(qn('w:' + property_name))
        if not nodes:
            nodes = [etree.SubElement(rpr, qn('w:' + property_name))]
        nodes[0].set(qn('w:val'), '1')

    report = inspect(master, rewrite(data, 'word/document.xml', corrupt))
    assert any(check['rule'] == 'master.content_visibility' and check['object'] == object_id
               and check['status'] == 'fail' for check in report['checks'])


@pytest.mark.parametrize('property_name', ['vanish', 'webHidden'])
def test_missing_direct_visibility_does_not_hide_style_inheritance(master, tmp_path, property_name):
    """Old files may omit a direct override, so inherited hiding must be resolved."""
    from app.services.master_docx_check import _run_content_visible
    from app.core.extract.xml_utils import load_part
    import io
    import zipfile

    data = rendered(master, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        styles = load_part(package, 'word/styles.xml')
        document = load_part(package, 'word/document.xml')
    run, _ = _target(document, master, 'body')
    rpr = run.find(qn('w:rPr'))
    for element in rpr.findall(qn('w:' + property_name)):
        rpr.remove(element)
    normal = styles.xpath('.//w:style[@w:styleId="Normal"]/w:rPr', namespaces=NS)[0]
    etree.SubElement(normal, qn('w:' + property_name))
    assert not _run_content_visible(run, styles)
    off = etree.SubElement(rpr, qn('w:' + property_name))
    off.set(qn('w:val'), '0')
    assert _run_content_visible(run, styles)


@pytest.mark.parametrize('property_name', ['vanish', 'webHidden'])
@pytest.mark.parametrize('bad_value', ['1', 'not-a-boolean', 'duplicate'])
def test_hidden_or_ambiguous_direct_visibility_is_rejected(master, tmp_path, property_name, bad_value):
    data = rendered(master, tmp_path)
    object_id = None

    def corrupt(root):
        nonlocal object_id
        run, object_id = _target(root, master, 'body')
        rpr = run.find(qn('w:rPr'))
        node = rpr.find(qn('w:' + property_name))
        assert node is not None
        if bad_value == 'duplicate':
            etree.SubElement(rpr, qn('w:' + property_name)).set(qn('w:val'), '0')
        else:
            node.set(qn('w:val'), bad_value)

    report = inspect(master, rewrite(data, 'word/document.xml', corrupt))
    assert any(check['rule'] == 'master.content_visibility' and check['object'] == object_id
               and check['status'] == 'fail' for check in report['checks'])


def test_hidden_inheritance_is_overridden_for_filled_content(master, tmp_path):
    from docx import Document
    from app.core.extract.specification import extract_writing_specification
    from app.models.template import PaperTemplate
    from app.services.import_template import extract_section_tree, import_template_styles
    from app.services.source_template_profile import ConfirmWritingProfile, compile_profile

    source, old_template, project, _ = master
    document = Document(source)
    document.styles['Normal'].font.hidden = True
    document.save(source)
    spec = extract_writing_specification(source)
    draft = PaperTemplate(id=old_template.id, name='继承隐藏样式的母版', source_docx=source.name,
                          sections=extract_section_tree(source), styles=import_template_styles(source)['styles'],
                          meta_fields=old_template.meta_fields)
    confirmation = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True)
    template = compile_profile(draft, source, confirmation, actor='teacher-test')
    master = (source, template, project, confirmation)
    _add_objects(master)
    output = rendered(master, tmp_path)
    report = inspect(master, output)
    visibility = [check for check in report['checks'] if check['rule'] == 'master.content_visibility']
    assert visibility and all(check['status'] == 'pass' for check in visibility)
    # Fixed source headings and field labels are protected rather than rewritten;
    # inherited hiding in those parts must be reported until the source is adapted.
    assert report['status'] == 'fail'
    assert any(check['rule'] in {'master.heading_visibility', 'master.field_label_visibility'}
               and check['status'] == 'fail' for check in report['checks'])
    for target in ('body', 'field', 'image', 'figure_caption', 'table_cell', 'table_caption'):
        object_id = None

        def remove_override(root):
            nonlocal object_id
            run, object_id = _target(root, master, target)
            rpr = run.find(qn('w:rPr'))
            rpr.remove(rpr.find(qn('w:vanish')))

        hidden = inspect(master, rewrite(output, 'word/document.xml', remove_override))
        assert any(check['rule'] == 'master.content_visibility' and check['object'] == object_id
                   and check['status'] == 'fail' for check in hidden['checks']), target


def test_preexisting_output_without_direct_overrides_remains_valid_when_visible(master, tmp_path):
    """Older output need not be regenerated solely to add explicit off values."""
    data = rendered(master, tmp_path)

    def remove_overrides(root):
        for content in root.xpath(
            ".//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,'pg-field:') "
            "or starts-with(w:sdtPr/w:tag/@w:val,'pg-block:')]/w:sdtContent", namespaces=NS
        ):
            for rpr in content.findall('.//' + qn('w:rPr')):
                for name in ('vanish', 'webHidden'):
                    for node in rpr.findall(qn('w:' + name)):
                        rpr.remove(node)

    report = inspect(master, rewrite(data, 'word/document.xml', remove_overrides))
    assert report['status'] == 'pass', [check for check in report['checks'] if check['status'] != 'pass']


def test_inherited_styles_follow_word_toggle_semantics():
    from app.services.master_docx_check import _run_content_visible

    styles = etree.fromstring(f'''<w:styles xmlns:w="{NS['w']}">
      <w:docDefaults><w:rPrDefault><w:rPr/></w:rPrDefault></w:docDefaults>
      <w:style w:type="paragraph" w:default="1" w:styleId="Base">
        <w:rPr><w:vanish/><w:webHidden/></w:rPr>
      </w:style>
      <w:style w:type="paragraph" w:styleId="Child"><w:basedOn w:val="Base"/>
        <w:rPr><w:vanish/><w:webHidden w:val="0"/></w:rPr>
      </w:style>
      <w:style w:type="character" w:styleId="Character"><w:rPr><w:vanish/></w:rPr></w:style>
    </w:styles>''')
    paragraph = etree.fromstring(f'''<w:p xmlns:w="{NS['w']}">
      <w:pPr><w:pStyle w:val="Child"/></w:pPr><w:r><w:rPr/><w:t>text</w:t></w:r>
    </w:p>''')
    run = paragraph.find(qn('w:r'))
    assert _run_content_visible(run, styles)
    style = etree.SubElement(run.find(qn('w:rPr')), qn('w:rStyle'))
    style.set(qn('w:val'), 'Character')
    assert not _run_content_visible(run, styles)
    etree.SubElement(run.find(qn('w:rPr')), qn('w:vanish')).set(qn('w:val'), '0')
    assert _run_content_visible(run, styles)


def test_conditional_table_visibility_requires_direct_override():
    from app.services.master_docx_check import _run_content_visible

    styles = etree.fromstring(f'''<w:styles xmlns:w="{NS['w']}">
      <w:style w:type="paragraph" w:default="1" w:styleId="Normal"/>
      <w:style w:type="table" w:default="1" w:styleId="TableNormal">
        <w:tblStylePr w:type="firstRow"><w:rPr><w:vanish/></w:rPr></w:tblStylePr>
      </w:style>
    </w:styles>''')
    table = etree.fromstring(f'''<w:tbl xmlns:w="{NS['w']}"><w:tblPr/><w:tr><w:tc><w:p>
      <w:r><w:rPr/><w:t>cell</w:t></w:r>
    </w:p></w:tc></w:tr></w:tbl>''')
    run = table.find('.//' + qn('w:r'))
    assert not _run_content_visible(run, styles)
    etree.SubElement(run.find(qn('w:rPr')), qn('w:vanish')).set(qn('w:val'), '0')
    assert _run_content_visible(run, styles)


@pytest.mark.parametrize('target,rule', [
    ('heading', 'master.heading_visibility'),
    ('cover_label', 'master.field_label_visibility'),
])
def test_protected_heading_and_cover_label_cannot_be_hidden(master, tmp_path, target, rule):
    data = rendered(master, tmp_path)
    assert inspect(master, data)['status'] == 'pass'

    def corrupt(root):
        if target == 'heading':
            heading = next(item for item in master[1].source_execution['headings'] if item['kind'] != 'toc')
            paragraph = root.xpath('.//w:bookmarkStart[@w:name=$name]/ancestor::w:p[1]',
                                   namespaces=NS, name=heading['bookmark_name'])[0]
            run = paragraph.xpath('./w:r[w:t]', namespaces=NS)[0]
        else:
            value = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-field:title"]', namespaces=NS)[0]
            paragraph = value.xpath('ancestor::w:p[1]', namespaces=NS)[0]
            run = paragraph.xpath('./w:r[w:t]', namespaces=NS)[0]
        rpr = run.find(qn('w:rPr'))
        if rpr is None:
            rpr = etree.Element(qn('w:rPr'))
            run.insert(0, rpr)
        element = rpr.find(qn('w:vanish'))
        if element is None:
            element = etree.SubElement(rpr, qn('w:vanish'))
        element.set(qn('w:val'), '1')

    report = inspect(master, rewrite(data, 'word/document.xml', corrupt))
    assert any(check['rule'] == rule and check['status'] == 'fail' for check in report['checks'])


@pytest.mark.parametrize('damage', ['missing_base_id', 'missing_base_style', 'style_cycle', 'invalid_default'])
def test_ambiguous_style_visibility_requires_explicit_override(damage):
    from app.services.master_docx_check import _run_content_visible

    styles = etree.fromstring(f'''<w:styles xmlns:w="{NS['w']}">
      <w:style w:type="paragraph" w:default="1" w:styleId="Normal"/>
    </w:styles>''')
    style = styles.find(qn('w:style'))
    if damage == 'invalid_default':
        style.set(qn('w:default'), 'invalid')
    else:
        base = etree.SubElement(style, qn('w:basedOn'))
        if damage == 'missing_base_style':
            base.set(qn('w:val'), 'Missing')
        elif damage == 'style_cycle':
            base.set(qn('w:val'), 'Normal')
    paragraph = etree.fromstring(f'''<w:p xmlns:w="{NS['w']}">
      <w:r><w:rPr/><w:t>text</w:t></w:r>
    </w:p>''')
    run = paragraph.find(qn('w:r'))
    assert not _run_content_visible(run, styles)
    properties = run.find(qn('w:rPr'))
    for name in ('vanish', 'webHidden'):
        etree.SubElement(properties, qn('w:' + name)).set(qn('w:val'), '0')
    assert _run_content_visible(run, styles)
