"""Reference hanging indentation must survive rendering and independent checks."""
import hashlib
import io
import zipfile

import pytest
from docx import Document
from docx.shared import Pt
from lxml import etree

from app.core.export.docx_exporter import export_project_to_docx
from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS, qn
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import MetaField, PaperTemplate
from app.services.docx_output_check import (DocxOutputCheckError, inspect_docx_output,
    require_docx_output_pass, verify_report_binding)
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.source_template_profile import ConfirmWritingProfile, compile_profile, flatten
from tests.test_source_template_profile import master, rewrite


@pytest.fixture(params=['plain-style', 'inherited-first-line', 'different-reference-size'])
def reference_case(master, tmp_path, request):
    source, _, _, _ = master
    doc = Document(source)
    if request.param == 'inherited-first-line':
        doc.styles['Normal'].paragraph_format.first_line_indent = Pt(24)
    if request.param == 'different-reference-size':
        doc.styles['Normal'].font.size = Pt(16)
    doc.add_heading('参考文献', 1)
    p = doc.add_paragraph('示例文献的长度应足以形成换行。' * 15)
    if request.param == 'different-reference-size':
        p.runs[0].font.size = Pt(10.5)
    ind = p._p.get_or_add_pPr().get_or_add_ind()
    ind.set(qn('w:left'), '480')
    ind.set(qn('w:hangingChars'), '200')
    doc.save(source)
    specification = extract_writing_specification(source)
    draft = PaperTemplate(id='hanging-master', name='参考文献测试', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'],
        meta_fields=[MetaField(key='title', label='题目'), MetaField(key='author', label='姓名'),
                     MetaField(key='student_id', label='学号')])
    template = compile_profile(draft, source, ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True), actor='teacher-test')
    project = WritingProject(id='hanging-project', template_id=template.id,
        meta={'title': '题目', 'author': '学生', 'student_id': 'LOCAL-001'},
        sections=[ProjectSection(key=node.key, type=node.type, title=node.title,
            paragraphs=[ParagraphBlock(id=node.key+'-p', text='DistinctiveReferenceMarker ' + 'Long citation text with consistent English characters. ' * 20)])
            for node in flatten(template.sections) if node.type.value not in {'cover', 'toc'}])
    section = next(node for node in project.sections if node.type == 'references')
    output = tmp_path / 'reference.docx'
    export_project_to_docx(project, template, str(output), template_path=str(source))
    return source, template, project, section.paragraphs[0].id, output.read_bytes()


def test_hanging_indent_emitted_without_competing_first_line_and_rejects_tamper(reference_case):
    source, template, project, block_id, data = reference_case
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    ind = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]//w:pPr/w:ind',
                     namespaces=NS, tag='pg-block:' + block_id)[0]
    assert ind.get(qn('w:hangingChars')) == '200'
    assert ind.get(qn('w:firstLine')) is None
    assert ind.get(qn('w:firstLineChars')) is None
    reference_profile = next(item for item in template.source_execution['headings'] if item['kind'] == 'references')
    size = reference_profile['properties']['font.size']['value']
    style = ind.getparent().find(qn('w:pStyle'))
    assert style is not None and style.get(qn('w:val')) == f'PGIndentBase{round(size * 2)}'
    report = inspect_docx_output(data, project, template, source_template_bytes=source.read_bytes())
    assert report['status'] == 'pass', [(x['rule'], x['object'], x.get('unit')) for x in report['checks'] if x['status'] != 'pass']

    def add_competing_first_line(root):
        node = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]//w:pPr/w:ind',
                          namespaces=NS, tag='pg-block:' + block_id)[0]
        node.set(qn('w:firstLine'), '0')
        node.set(qn('w:firstLineChars'), '0')

    damaged = rewrite(data, 'word/document.xml', add_competing_first_line)
    report = inspect_docx_output(damaged, project, template, source_template_bytes=source.read_bytes())
    assert report['status'] == 'fail'
    assert any(x['rule'] == 'master.effective_format' and x['object'] == block_id
               and x['status'] == 'fail' for x in report['checks'])

    # An old report identifies the same bytes, but delivery must run the new
    # inspector instead of accepting its historical decision.
    old_report = inspect_docx_output(data, project, template, source_template_bytes=source.read_bytes())
    old_report['inspector_version'] = 'docx-output-check/11-master'
    old_report['output_sha256'] = hashlib.sha256(damaged).hexdigest()
    assert verify_report_binding(old_report, damaged, allow_previous_inspector=True)
    assert not verify_report_binding(old_report, damaged)
    with pytest.raises(DocxOutputCheckError):
        require_docx_output_pass(damaged, project, template, source_template_bytes=source.read_bytes())


def test_existing_first_line_is_removed_when_applying_hanging_indent():
    from app.services.master_docx_renderer import apply_properties, text_paragraph
    paragraph = text_paragraph('正文', {'paragraph.indent.first_line': {'value': 2, 'unit': 'character'}})
    apply_properties(paragraph, {'paragraph.indent.hanging': {'value': 2, 'unit': 'character'}})
    indent = paragraph.find(f"{qn('w:pPr')}/{qn('w:ind')}")
    assert indent.get(qn('w:hangingChars')) == '200'
    assert indent.get(qn('w:firstLineChars')) is None


def test_regular_first_line_indent_is_preserved_and_conflicting_rules_are_rejected():
    from app.services.master_docx_renderer import text_paragraph
    from app.services.source_template_profile import ProfileError
    first = {'value': 2, 'unit': 'character'}
    paragraph = text_paragraph('正文', {'paragraph.indent.first_line': first})
    indent = paragraph.find(f"{qn('w:pPr')}/{qn('w:ind')}")
    assert indent.get(qn('w:firstLineChars')) == '200'
    with pytest.raises(ProfileError):
        text_paragraph('正文', {'paragraph.indent.first_line': first,
                                'paragraph.indent.hanging': {'value': 2, 'unit': 'character'}})


def test_direct_hanging_indent_overrides_inherited_first_line_and_rejects_direct_conflict():
    from app.services.master_docx_check import _paragraph_property_matches
    from docx.oxml import OxmlElement
    inherited = OxmlElement('w:pPr')
    inherited_indent = OxmlElement('w:ind')
    inherited_indent.set(qn('w:firstLine'), '480')
    inherited.append(inherited_indent)
    direct = OxmlElement('w:pPr')
    direct_indent = OxmlElement('w:ind')
    direct_indent.set(qn('w:hangingChars'), '200')
    direct.append(direct_indent)
    first = {'value': 0, 'unit': 'character'}
    hanging = {'value': 2, 'unit': 'character'}
    expected = {'paragraph.indent.first_line': first, 'paragraph.indent.hanging': hanging}
    actual = {'paragraph.indent.first_line': {'value': 24, 'unit': 'pt'},
              'paragraph.indent.hanging': hanging}
    assert _paragraph_property_matches('paragraph.indent.first_line', first, actual['paragraph.indent.first_line'], actual, expected, [inherited, direct])
    direct_indent.set(qn('w:firstLine'), '0')
    assert not _paragraph_property_matches('paragraph.indent.first_line', first, actual['paragraph.indent.first_line'], actual, expected, [inherited, direct])
    assert not _paragraph_property_matches('paragraph.indent.hanging', hanging, hanging, actual, expected, [inherited, direct])


def test_resolver_replaces_inherited_hanging_with_direct_first_line():
    from app.core.extract.effective_format import EffectiveFormatResolver
    doc = Document()
    doc.styles['Normal'].paragraph_format.first_line_indent = Pt(-24)
    paragraph = doc.add_paragraph('正文')
    paragraph.paragraph_format.first_line_indent = Pt(12)
    resolver = EffectiveFormatResolver(etree.fromstring(etree.tostring(doc.styles.element)))
    properties, _, _, _ = resolver.paragraph(etree.fromstring(etree.tostring(paragraph._p)))
    assert properties['paragraph.indent.first_line'] == {'value': 12, 'unit': 'pt'}
    assert 'paragraph.indent.hanging' not in properties


def test_resolver_keeps_same_layer_conflict_for_the_inspector():
    from app.core.extract.effective_format import EffectiveFormatResolver
    doc = Document()
    paragraph = doc.add_paragraph('正文')
    indent = paragraph._p.get_or_add_pPr().get_or_add_ind()
    indent.set(qn('w:firstLine'), '0')
    indent.set(qn('w:hangingChars'), '200')
    resolver = EffectiveFormatResolver(etree.fromstring(etree.tostring(doc.styles.element)))
    properties, _, _, _ = resolver.paragraph(etree.fromstring(etree.tostring(paragraph._p)))
    assert properties['paragraph.indent.first_line'] == {'value': 0, 'unit': 'pt'}
    assert properties['paragraph.indent.hanging'] == {'value': 2, 'unit': 'character'}
