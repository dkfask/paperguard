"""Approved ordinary-Word masters: publication, fidelity and delivery negatives."""
import copy
import io
from pathlib import Path
import uuid
import zipfile

import pytest
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.oxml import OxmlElement
from docx.shared import Pt
from docx.enum.style import WD_STYLE_TYPE
from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image

from app.core.extract.effective_format import EffectiveFormatResolver
from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS,load_part,qn
from app.core.export.docx_exporter import export_project_to_docx
from app.main import app
from app.models.project import WritingProject,ProjectSection,ParagraphBlock,ImageBlock,TableBlock
from app.models.template import PaperTemplate,MetaField
from app.services.docx_output_check import DocxOutputCheckError,inspect_docx_output,require_output_check_report,verify_report_binding
from app.services.document_assets import document_upload_dir
from app.services.import_template import extract_section_tree,import_template_styles
from app.services.source_template_profile import ConfirmWritingProfile,ProfileError,compile_profile,flatten,trial_profile,verify_profile
from app.services.source_template_safety import audit_source_package


@pytest.fixture
def master(tmp_path):
    doc=Document()
    normal=doc.styles['Normal']
    normal.font.size=Pt(12)
    normal.font.name='Times New Roman'
    normal.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'宋体')
    normal.paragraph_format.line_spacing=1.5
    doc.add_paragraph('毕业设计报告')
    doc.add_paragraph('题目：原示例题目')
    table=doc.add_table(rows=2,cols=2)
    for row,label in zip(table.rows,['学生姓名：','学生学号：']):
        row.cells[0].text=label
        row.cells[1].text=''
    doc.sections[0].header.paragraphs[0].text='固定校名与页眉'
    footer=doc.sections[0].footer.paragraphs[0]
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
    doc.add_section(WD_SECTION_START.NEW_PAGE)
    doc.add_heading('摘要',1);doc.add_paragraph('摘要正文示例内容。')
    doc.add_heading('第1章 概述',1);doc.add_paragraph('这是源模板的示例正文，不应残留在学生成品中。')
    doc.add_heading('1.1 研究内容',2);doc.add_paragraph('这里要包括：请替换说明。')
    path=tmp_path/'master.docx';doc.save(path)
    spec=extract_writing_specification(path)
    template=PaperTemplate(id='tpl-'+uuid.uuid4().hex,name='普通母版',source_docx='local.docx',
        sections=extract_section_tree(path),styles=import_template_styles(path)['styles'],
        meta_fields=[MetaField(key='title',label='题目'),MetaField(key='author',label='姓名'),MetaField(key='student_id',label='学号')])
    confirmation=ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,accept_recommended=True,
        remove_template_instructions=True,accept_new_object_defaults=True)
    published=compile_profile(template,path,confirmation,actor='teacher-test')
    project=WritingProject(id='proj-'+uuid.uuid4().hex,template_id=template.id,
        meta={'title':'学生新题目','author':'测试学生','student_id':'LOCAL-001'},
        sections=[ProjectSection(key=n.key,type=n.type,title=n.title,paragraphs=[ParagraphBlock(id=n.key+'-p',text='学生正文\nEnglish\tcontent')]) for n in flatten(published.sections) if n.type.value not in {'cover','toc'}])
    return path,published,project,confirmation


def rendered(master,tmp_path):
    source,template,project,_=master
    path=tmp_path/'output.docx'
    export_project_to_docx(project,template,str(path),template_path=str(source))
    return path.read_bytes()


def inspect(master,data):
    source,template,project,_=master
    return inspect_docx_output(data,project,template,source_template_bytes=source.read_bytes())


@pytest.fixture
def keyword_master(tmp_path):
    doc = Document()
    doc.add_heading('摘要', 1)
    doc.add_paragraph('摘要正文示例。')
    doc.add_paragraph('关键词：模板；系统')
    doc.add_heading('Abstract', 1)
    doc.add_paragraph('Abstract body example.')
    doc.add_paragraph('Key Words: template; system')
    doc.add_heading('第1章 绪论', 1)
    doc.add_paragraph('正文示例，需要用于提取正文格式。' * 8)
    source = tmp_path / 'keyword-master.docx'
    doc.save(source)
    specification = extract_writing_specification(source)
    draft = PaperTemplate(id='keyword-master', name='关键词模板', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'])
    base = dict(expected_specification_sha256=specification.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True)
    initial = compile_profile(draft, source, ConfirmWritingProfile(**base), actor='teacher-test')
    headings = {h['kind']: h for h in initial.source_execution['headings']}
    labels = {
        headings['keywords']['key']: {'text': '关键词', 'separator': '：', 'bold': True},
        headings['english_keywords']['key']: {'text': 'Key words', 'separator': ': ', 'bold': None},
    }
    template = compile_profile(draft, source, ConfirmWritingProfile(**base, inline_label_overrides=labels), actor='teacher-test')
    project = WritingProject(id='keyword-project', template_id=template.id,
        sections=[ProjectSection(key=n.key, type=n.type, title=n.title,
            paragraphs=[ParagraphBlock(id=n.key + '-p', text='Template Example' if n.type.value == 'english_keywords' else '学生内容')])
            for n in flatten(template.sections) if n.type.value not in {'cover', 'toc'}])
    return source, template, project, base, labels, draft


def test_confirmed_keyword_labels_are_rendered_and_independently_checked(keyword_master, tmp_path):
    source, template, project, _, labels, _ = keyword_master
    data = rendered((source, template, project, None), tmp_path)
    report = inspect((source, template, project, None), data)
    assert report['status'] == 'pass', [(c['rule'], c['object'], c['actual']) for c in report['checks'] if c['status'] != 'pass']
    assert report['inspector_version'] == 'docx-output-check/6-master'
    assert verify_report_binding(report, data)
    for old_version in ('docx-output-check/2', 'docx-output-check/4', 'docx-output-check/5-master'):
        old_report = {**report, 'inspector_version': old_version}
        assert not verify_report_binding(old_report, data)
    no_profile = dict(report)
    no_profile.pop('source_profile_sha256')
    assert not verify_report_binding(no_profile, data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    text = ''.join(root.xpath('.//w:t/text()', namespaces=NS))
    assert 'Key words: Template Example' in text
    assert 'Key Words:' not in text
    assert '关键词：学生内容' in text
    assert {c['object'] for c in report['checks'] if c['rule'] == 'master.inline_label' and c['status'] == 'pass'} == set(labels)
    assert any(c['rule'] == 'master.inline_label_bold' and c['status'] == 'pass' for c in report['checks'])

    def change_label(root):
        run = root.xpath('.//w:t[text()="Key words"]', namespaces=NS)[0]
        run.text = 'Key Words'
    altered = rewrite(data, 'word/document.xml', change_label)
    result = inspect((source, template, project, None), altered)
    assert result['status'] == 'fail'
    assert any(c['rule'] == 'master.inline_label' and c['status'] == 'fail' for c in result['checks'])

    def unbold_colon(root):
        run = root.xpath('.//w:t[text()="关键词"]/../following-sibling::w:r[w:t="："]', namespaces=NS)[0]
        run.find('./' + qn('w:rPr') + '/' + qn('w:b')).set(qn('w:val'), '0')
    altered = rewrite(data, 'word/document.xml', unbold_colon)
    result = inspect((source, template, project, None), altered)
    assert result['status'] == 'fail'
    assert any(c['rule'] == 'master.inline_label_bold' and c['status'] == 'fail' for c in result['checks'])


@pytest.mark.parametrize('labels', [
    {'unknown': {'text': 'Key words', 'separator': ': '}},
    {'english_keywords': {'text': 'Key: words', 'separator': ': '}},
    {'english_keywords': {'text': 'Key words', 'separator': ' / '}},
])
def test_keyword_label_overrides_must_bind_a_valid_inline_region(keyword_master, labels):
    source, _, _, base, _, draft = keyword_master
    with pytest.raises(ProfileError, match='关键词标签'):
        compile_profile(draft, source, ConfirmWritingProfile(**base, inline_label_overrides=labels), actor='teacher-test')


def test_keyword_label_publication_must_match_the_preview(keyword_master):
    source, _, _, _, labels, draft = keyword_master
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'TestAdminPassword123'})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['token']
        imported = client.post('/api/templates/import', files={'file': ('source.docx', source.read_bytes())})
        assert imported.status_code == 200, imported.text
        payload = draft.model_dump(mode='json')
        payload['source_docx'] = imported.json()['source_docx']
        saved = client.post('/api/templates', json=payload)
        assert saved.status_code == 201, saved.text
        template = saved.json()
        base = '/api/templates/' + template['id']
        confirmation = {'expected_specification_sha256': template['writing_specification']['specification_sha256'],
            'accept_recommended': True, 'remove_template_instructions': True, 'accept_new_object_defaults': True,
            'inline_label_overrides': labels}
        preview = client.post(base + '/writing-profile/propose', json=confirmation)
        assert preview.status_code == 200, preview.text
        confirmation['proposal_id'] = preview.json()['proposal_id']
        changed = copy.deepcopy(confirmation)
        changed['inline_label_overrides'][next(iter(labels))]['bold'] = False
        rejected = client.post(base + '/publish-writing-profile', json=changed)
        assert rejected.status_code == 409
        assert '关键词标签与预览不同' in rejected.text
        published = client.post(base + '/publish-writing-profile', json=confirmation)
        assert published.status_code == 200, published.text
        assert published.json()['template']['source_execution']['inline_label_overrides'] == labels


def test_manuscript_template_inherits_confirmed_keyword_labels(keyword_master):
    from app.services.docx_project_import import import_docx_project
    from app.services.manuscript_template import derive_manuscript_template

    source, _, _, base, labels, draft = keyword_master
    parent = compile_profile(draft, source, ConfirmWritingProfile(
        **base, inline_label_overrides=labels, preserve_manuscript_structure=True), actor='teacher-test')
    manuscript = Document()
    manuscript.add_heading('第1章 自定义论文章节', 1)
    manuscript.add_paragraph('论文原有正文。')
    raw = io.BytesIO()
    manuscript.save(raw)
    derived, _ = derive_manuscript_template(parent, source, import_docx_project(raw.getvalue()), actor='student-test')
    headings = {h['kind']: h for h in derived.source_execution['headings']}
    assert headings['english_keywords']['display_label'] == 'Key words'
    assert headings['keywords']['title_properties']['font.bold'] is True
    assert headings['keywords']['separator_properties']['font.bold'] is True
    assert derived.source_execution['inline_label_overrides'][headings['english_keywords']['key']]['text'] == 'Key words'


def rewrite(data,part,mutate):
    buffer=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source,zipfile.ZipFile(buffer,'w') as output:
        for name in source.namelist():
            payload=source.read(name)
            if name==part:
                root=etree.fromstring(payload);mutate(root);payload=etree.tostring(root)
            output.writestr(name,payload)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ('namespace', 'element', 'finding'),
    [
        ('http://schemas.openxmlformats.org/officeDocument/2006/math', 'oMath', 'source_equation_omml'),
        ('http://schemas.openxmlformats.org/drawingml/2006/chart', 'chart', 'source_chart_not_mapped'),
    ],
)
def test_signed_master_profile_does_not_erase_unsupported_source_findings(master, tmp_path, namespace, element, finding):
    from app.services.template_admission import assess_paper_template, assess_template_definition

    original, template, _, _ = master
    def add_unsupported(root):
        paragraph = root.xpath('.//w:body/w:p', namespaces=NS)[0]
        etree.SubElement(paragraph, f'{{{namespace}}}{element}')

    source = tmp_path / 'unsupported-master.docx'
    source.write_bytes(rewrite(original.read_bytes(), 'word/document.xml', add_unsupported))
    specification = extract_writing_specification(source)
    confirmation = ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256,
        accept_recommended=True, remove_template_instructions=True,
        accept_new_object_defaults=True,
    )
    signed = compile_profile(template, source, confirmation, actor='teacher-test')
    verify_profile(signed, source.read_bytes())

    raw = signed.model_dump(mode='json')
    definition = {
        'source_docx': raw['source_docx'], 'sections': raw['sections'],
        'styles': raw['styles'], 'fields': raw['meta_fields'],
        'page_layout': raw['page_layout'], 'allow_structure_edit': raw['allow_structure_edit'],
        'extensions': {'source_execution': raw['source_execution']},
    }
    reports = [
        assess_paper_template(signed, source.read_bytes()),
        assess_template_definition(template_id=signed.id, version=signed.version,
                                   definition=definition, source_data=source.read_bytes()),
    ]
    for report in reports:
        assert report['status'] == 'blocked'
        assert report['can_use_for_draft'] is False
        assert any(item['code'] == finding for item in report['blockers'])


def test_signed_master_can_be_used_for_draft_but_retains_unverified_source_findings(master):
    from app.services.template_admission import assess_paper_template

    source, template, _, _ = master
    report = assess_paper_template(template, source.read_bytes())
    assert report['can_use_for_draft'] is True
    assert report['can_claim_compliant_delivery'] is False
    assert {'source_table_not_mapped', 'unmapped_header', 'source_section_layouts'} <= {
        item['code'] for item in report['unverified_findings']
    }


def _complete_layout_review_case():
    from app.services.office_layout import NATIVE_TOC_VERSION

    receipt = {
        'version': NATIVE_TOC_VERSION, 'pagination_source': 'word_native',
        'toc_preparation_source': 'word_native', 'state_stable': True,
    }
    checks = [{'rule': 'master.layout_receipt', 'status': 'pass'},
              {'rule': 'master.heading_pagination', 'status': 'pass'}]
    report = {'checks': checks, 'check_count': 2, 'passed_count': 2,
              'status': 'pass', 'unsupported': []}
    return report, receipt


def test_verified_current_word_layout_releases_only_blanket_review_gate():
    from app.services.master_docx_check import _apply_master_layout_review_policy
    from app.services.export_jobs import bind_requirement_manual_checks
    from app.services.manual_output_review import review_required

    report, receipt = _complete_layout_review_case()
    _apply_master_layout_review_policy(report, receipt)
    assert not review_required(report)
    assert report['manual_checks'] == []
    assert report['visual_evidence_limitations']
    # A separate requirement that is not automatically verifiable must still block.
    assert review_required(bind_requirement_manual_checks(report, ['未验证的模板规则']))


@pytest.mark.parametrize('damage', [
    'missing', 'legacy', 'wrong_engine', 'wrong_preparation', 'unstable',
    'missing_layout_check', 'layout_check_failed', 'other_check_failed',
    'missing_checks', 'wrong_count', 'unsupported', 'incomplete_layout',
])
def test_master_review_gate_retains_unverified_or_failed_evidence(damage):
    from app.services.master_docx_check import _apply_master_layout_review_policy
    from app.services.manual_output_review import review_required

    report, receipt = _complete_layout_review_case()
    if damage == 'missing':
        receipt = None
    elif damage == 'legacy':
        receipt['version'] = 'office-layout/11'
    elif damage == 'wrong_engine':
        receipt['pagination_source'] = 'unknown'
    elif damage == 'wrong_preparation':
        receipt['toc_preparation_source'] = 'unknown'
    elif damage == 'unstable':
        receipt['state_stable'] = False
    elif damage == 'missing_layout_check':
        report['checks'][0]['rule'] = 'master.other'
    elif damage == 'layout_check_failed':
        report['checks'][0]['status'] = 'fail'
    elif damage == 'other_check_failed':
        report['checks'][1]['status'] = 'fail'
    elif damage == 'missing_checks':
        report['checks'] = []
    elif damage == 'wrong_count':
        report['passed_count'] = 1
    elif damage == 'unsupported':
        report['unsupported'] = ['unsupported object']
    else:
        report['layout_evidence_limitations'] = ['missing table rows']
    _apply_master_layout_review_policy(report, receipt)
    assert review_required(report)
    assert report['manual_checks']
    assert 'visual_evidence_limitations' not in report


def test_normal_and_long_content_keep_master_and_have_bound_reports(master,tmp_path):
    source,template,project,_=master
    original=source.read_bytes()
    reports=trial_profile(template,source)
    assert all(r['status']=='pass' for r in reports.values())
    data=rendered(master,tmp_path);report=inspect(master,data)
    assert report['status']=='pass',[(c['rule'],c['object']) for c in report['checks'] if c['status']!='pass']
    assert report['source_profile_sha256']==template.source_execution['profile_sha256']
    assert report['office_review_required'] is True
    require_output_check_report(report)
    with zipfile.ZipFile(source) as before,zipfile.ZipFile(io.BytesIO(data)) as after:
        assert before.read('word/header1.xml')==after.read('word/header1.xml')
        assert before.read('word/footer1.xml')==after.read('word/footer1.xml')
        xml=after.read('word/document.xml').decode()
        assert '学生新题目' in xml and '原示例题目' not in xml
        assert '不应残留' not in xml and '这里要包括' not in xml
        root=etree.fromstring(after.read('word/document.xml'))
        appearance='{http://schemas.microsoft.com/office/word/2012/wordml}'
        controls=root.xpath('.//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,"pg-")]',namespaces=NS)
        assert controls
        for control in controls:
            setting=control.find(qn('w:sdtPr')+'/'+appearance+'appearance')
            assert setting is not None and setting.get(appearance+'val')=='hidden'
    assert source.read_bytes()==original


def _body_page_master(tmp_path, *, extra_footer_text=False):
    doc = Document()
    doc.add_paragraph('论文封面')
    doc.add_section(WD_SECTION_START.NEW_PAGE)
    doc.add_heading('摘要', 1)
    doc.add_paragraph('摘要内容。')
    front = doc.sections[1].footer
    front.is_linked_to_previous = False
    field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), ' PAGE ')
    run = OxmlElement('w:r'); text = OxmlElement('w:t'); text.text = 'I'; run.append(text); field.append(run)
    front.paragraphs[0]._p.append(field)
    doc.add_section(WD_SECTION_START.NEW_PAGE)
    doc.add_heading('第1章 绪论', 1)
    doc.add_paragraph('具有足够长度的正文示例，用于建立此模板正文格式。' * 4)
    doc.add_section(WD_SECTION_START.NEW_PAGE)
    doc.add_heading('致谢', 1)
    doc.add_paragraph('致谢正文。')
    last = doc.sections[-1].footer
    last.is_linked_to_previous = False
    if extra_footer_text:
        last.paragraphs[0].text = '不得覆盖的页脚内容'
    source = tmp_path / 'page-master.docx'
    doc.save(source)
    spec = extract_writing_specification(source)
    template = PaperTemplate(id='page-master', name='页码模板', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'])
    request = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True,
        body_page_number={'font': 'Times New Roman', 'size_pt': 9})
    return source, template, request


def test_body_page_number_preserves_front_matter_and_fills_last_section(tmp_path):
    source, template, request = _body_page_master(tmp_path)
    published = compile_profile(template, source, request, actor='teacher-test')
    project = WritingProject(id='page-project', template_id=template.id,
        sections=[ProjectSection(key=n.key, type=n.type, title=n.title,
            paragraphs=[ParagraphBlock(id=n.key+'-p', text='学生正文。')])
            for n in flatten(published.sections) if n.type.value not in {'cover', 'toc'}])
    output = tmp_path / 'page-output.docx'
    export_project_to_docx(project, published, str(output), template_path=str(source))
    data = output.read_bytes()
    report = inspect_docx_output(data, project, published, source_template_bytes=source.read_bytes())
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']
    assert next(c for c in report['checks'] if c['rule'] == 'master.body_page_footer')['status'] == 'pass'
    before, after = Document(source), Document(io.BytesIO(data))
    assert after.sections[1].footer._element.xml == before.sections[1].footer._element.xml
    assert all(section.footer.part.partname == '/word/footerPGBody.xml' for section in after.sections[2:])
    assert after.sections[-1].footer._element.xpath('.//w:fldSimple/@w:instr') == [' PAGE ']
    assert after.sections[-1].footer._element.xpath('.//w:rFonts/@w:ascii') == ['Times New Roman']
    assert after.sections[-1].footer._element.xpath('.//w:sz/@w:val') == ['18']

    tampered = rewrite(data, 'word/footerPGBody.xml',
        lambda root: root.xpath('.//w:rFonts', namespaces=NS)[0].set(qn('w:ascii'), '宋体'))
    invalid = inspect_docx_output(tampered, project, published, source_template_bytes=source.read_bytes())
    assert invalid['status'] == 'fail'
    assert next(c for c in invalid['checks'] if c['rule'] == 'master.body_page_footer')['status'] == 'fail'


def test_body_page_number_refuses_to_replace_other_footer_content(tmp_path):
    source, template, request = _body_page_master(tmp_path, extra_footer_text=True)
    with pytest.raises(ProfileError, match='页脚含其他内容'):
        compile_profile(template, source, request, actor='teacher-test')


def test_manuscript_template_inherits_confirmed_body_page_number(tmp_path):
    from app.services.docx_project_import import import_docx_project
    from app.services.manuscript_template import derive_manuscript_template

    source, template, request = _body_page_master(tmp_path)
    parent = compile_profile(template, source, request.model_copy(update={'preserve_manuscript_structure': True}), actor='teacher-test')
    manuscript = Document()
    manuscript.add_heading('第1章 自定义论文章节', 1)
    manuscript.add_paragraph('论文正文。')
    raw = io.BytesIO(); manuscript.save(raw)
    derived, _ = derive_manuscript_template(parent, source, import_docx_project(raw.getvalue()), actor='student-test')
    assert derived.source_execution['body_page_number'] == {'font': 'Times New Roman', 'size_pt': 9}
    assert derived.source_execution['body_page_number_policy']['settings'] == parent.source_execution['body_page_number']


def test_body_page_number_publication_must_match_preview(tmp_path):
    source, draft, request = _body_page_master(tmp_path)
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'TestAdminPassword123'})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['token']
        imported = client.post('/api/templates/import', files={'file': ('source.docx', source.read_bytes())})
        assert imported.status_code == 200, imported.text
        payload = draft.model_dump(mode='json'); payload['source_docx'] = imported.json()['source_docx']
        saved = client.post('/api/templates', json=payload)
        assert saved.status_code == 201, saved.text
        template = saved.json()
        confirmation = request.model_dump(mode='json')
        confirmation['expected_specification_sha256'] = template['writing_specification']['specification_sha256']
        base = '/api/templates/' + template['id']
        preview = client.post(base + '/writing-profile/propose', json=confirmation)
        assert preview.status_code == 200, preview.text
        confirmation['proposal_id'] = preview.json()['proposal_id']
        changed = copy.deepcopy(confirmation); changed['body_page_number']['size_pt'] = 10
        rejected = client.post(base + '/publish-writing-profile', json=changed)
        assert rejected.status_code == 409
        assert '正文页码设置与预览不同' in rejected.text
        published = client.post(base + '/publish-writing-profile', json=confirmation)
        assert published.status_code == 200, published.text

def test_repeated_cover_field_labels_are_rejected_instead_of_overwriting_later_match(tmp_path):
    doc = Document()
    doc.add_paragraph('毕业设计报告')
    doc.add_paragraph('题目：示例题目')
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '学生学号：'
    table.cell(0, 1).text = ''
    table.cell(1, 0).text = '学生学号：'
    table.cell(1, 1).text = ''
    doc.add_heading('第1章 概述', 1)
    doc.add_paragraph('这是具有足够长度的正文示例，用于识别常规正文格式，并验证模板字段映射不会静默选错位置。' * 3)
    source = tmp_path / 'duplicate-cover-field.docx'
    doc.save(source)
    template = PaperTemplate(id='duplicate-field', name='重复字段模板', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'],
        meta_fields=[MetaField(key='student_id', label='学号')])
    spec = extract_writing_specification(source)
    request = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True)

    with pytest.raises(ProfileError, match='封面字段存在多个候选位置：学号（2处）'):
        compile_profile(template, source, request, actor='teacher-test')

def test_multiple_cover_fields_cannot_share_the_same_value_cell(tmp_path):
    doc = Document()
    doc.add_paragraph('毕业设计报告')
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '班级：'
    table.cell(0, 1).text = ''
    doc.add_heading('第1章 概述', 1)
    doc.add_paragraph('这是具有足够长度的正文示例，用于识别常规正文格式，并验证多个封面字段必须有不同的映射位置。' * 3)
    source = tmp_path / 'shared-cover-field.docx'
    doc.save(source)
    template = PaperTemplate(id='shared-field', name='共享字段模板', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'],
        meta_fields=[MetaField(key='class_name', label='班级'), MetaField(key='custom_class_name', label='班级')])
    spec = extract_writing_specification(source)
    request = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True)

    with pytest.raises(ProfileError, match='多个封面字段映射到了同一位置：class_name、custom_class_name'):
        compile_profile(template, source, request, actor='teacher-test')


@pytest.mark.parametrize('mutation',['text','font','section','header','field','duplicate','relationship','configuration','visible-control'])
def test_corrupted_outputs_cannot_pass(master,tmp_path,mutation):
    data=rendered(master,tmp_path)
    def alter(root):
        if mutation=='text':
            root.xpath('.//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,"pg-block:")]//w:t',namespaces=NS)[0].text='篡改'
        elif mutation=='font':
            root.xpath('.//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,"pg-block:")]//w:sz',namespaces=NS)[0].set(qn('w:val'),'40')
        elif mutation=='section':
            root.find('.//'+qn('w:pgMar')).set(qn('w:left'),'600')
        elif mutation=='field':
            root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-field:author"]//w:t',namespaces=NS)[0].text='另一个学生'
        elif mutation=='duplicate':
            control=root.xpath('.//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,"pg-block:")]',namespaces=NS)[0]
            control.getparent().append(copy.deepcopy(control))
        elif mutation=='relationship':
            root[0].set('Target','changed.xml')
        elif mutation=='configuration':
            root.append(OxmlElement('w:mirrorMargins'))
        elif mutation=='visible-control':
            control=root.xpath('.//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,"pg-block:")]',namespaces=NS)[0]
            pr=control.find(qn('w:sdtPr'));appearance='{http://schemas.microsoft.com/office/word/2012/wordml}'
            setting=pr.find(appearance+'appearance')
            if setting is None:setting=etree.SubElement(pr,appearance+'appearance')
            setting.set(appearance+'val','boundingBox')
        else:
            root.find('.//'+qn('w:t')).text='页眉被替换'
    part={'header':'word/header1.xml','relationship':'word/_rels/document.xml.rels','configuration':'word/settings.xml'}.get(mutation,'word/document.xml')
    report=inspect(master,rewrite(data,part,alter))
    assert report['status']=='fail'
    with pytest.raises(Exception):require_output_check_report(report)


def test_profile_and_source_changes_require_new_confirmation(master):
    source,template,project,request=master
    changed=template.model_copy(deep=True);changed.source_execution['headings'][0]['properties']['font.size']={'value':20,'unit':'pt'}
    with pytest.raises(ProfileError):verify_profile(changed,source.read_bytes())
    changed=template.model_copy(deep=True);changed.styles['body'].size_pt=20
    with pytest.raises(ProfileError):verify_profile(changed,source.read_bytes())
    with pytest.raises(ProfileError):verify_profile(template,source.read_bytes()+b'changed')
    with pytest.raises(ProfileError):compile_profile(template,source,request.model_copy(update={'expected_specification_sha256':'old'}),actor='teacher-test')


def test_unmatched_toc_requires_explicit_removal_confirmation(master,tmp_path):
    source,template,_,request=master
    original=source.read_bytes()
    doc=Document(source)
    toc_style=doc.styles.add_style('TOC 1',WD_STYLE_TYPE.PARAGRAPH)
    field=doc.add_paragraph('目 录')
    toc_field=OxmlElement('w:fldSimple');toc_field.set(qn('w:instr'),'TOC \\o "1-3" \\h');field._p.append(toc_field)
    doc.add_paragraph('不存在的附录 8',style=toc_style)
    path=tmp_path/'stale-toc.docx';doc.save(path)
    spec=extract_writing_specification(path)
    confirmation=ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True,remove_template_instructions=True,accept_new_object_defaults=True)
    with pytest.raises(ProfileError,match='无对应正文'):
        compile_profile(template.model_copy(update={'source_docx':'stale-toc.docx'}),path,confirmation,actor='teacher-test')
    approved=compile_profile(template.model_copy(update={'source_docx':'stale-toc.docx'}),path,
        confirmation.model_copy(update={'remove_unmatched_toc_entries':True}),actor='teacher-test')
    assert approved.source_execution['removed_toc_entries']==['不存在的附录']
    assert approved.source_execution['toc_entries']==[]
    assert approved.source_execution['toc_active'] is True
    assert approved.source_execution['toc_style_by_level']=={1:'TOC1'}
    assert approved.source_execution['toc_appearance_by_level']['1'] == {
        'label': {'color': 'auto', 'underline': 'none'},
        'leader': {'color': 'auto', 'underline': 'none'},
        'page': {'color': 'auto', 'underline': 'none'}}
    # A previously signed profile is repaired from its bound source on read,
    # without mutating or forcing re-publication of the stored profile.
    from app.services.source_template_profile import sign_profile
    legacy = approved.model_copy(deep=True)
    legacy.source_execution.pop('toc_appearance_by_level')
    legacy.source_execution = sign_profile(legacy.source_execution)
    assert verify_profile(legacy, path.read_bytes())['toc_appearance_by_level'] == approved.source_execution['toc_appearance_by_level']
    assert 'toc_appearance_by_level' not in legacy.source_execution
    with zipfile.ZipFile(path) as archive:
        from app.core.extract.xml_utils import load_part
        root=load_part(archive,'word/document.xml')
    from app.services.master_docx_renderer import prepare_source
    prepared=prepare_source(root,approved.source_execution)
    assert not any('不存在的附录' in ''.join(node.itertext()) for node in prepared.findall('.//'+qn('w:p')))
    assert any(''.join(node.xpath('./w:r/w:t/text()',namespaces=NS))=='目 录' for node in prepared.findall('.//'+qn('w:p')))
    refreshed=Document(path)
    for paragraph in list(refreshed.paragraphs):
        if '不存在的附录' in paragraph.text or paragraph._p.xpath('.//w:fldSimple'):
            paragraph._p.getparent().remove(paragraph._p)
    expected_heading=next(h for h in approved.source_execution['headings'] if h['kind']=='heading1')
    row=refreshed.add_paragraph(style='TOC 1')
    hidden=row.add_run(expected_heading['title']);hidden.font.hidden=True
    row.add_run().add_tab();row.add_run('6')
    # Office moves the following section boundary into the last index row.
    # Copying it back would duplicate the preserved source boundary.
    row._p.get_or_add_pPr().append(OxmlElement('w:sectPr'))
    updated=tmp_path/'updated.docx';refreshed.save(updated)
    from app.services.office_layout import _read_toc,_replace_toc_rows
    rows,_=_read_toc(updated,approved.source_execution)
    assert [(r['key'],r['page'],r['bookmark']) for r in rows]==[(expected_heading['key'],'6',expected_heading['bookmark_name'])]
    with zipfile.ZipFile(path) as archive:
        parts={name:archive.read(name) for name in archive.namelist()}
    _replace_toc_rows(parts,rows,approved.source_execution)
    rebuilt=etree.fromstring(parts['word/document.xml'])
    toc_rows=[p for p in rebuilt.findall('.//'+qn('w:p')) if p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}") is not None and p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}").get(qn('w:val'))=='TOC1']
    assert len(toc_rows)==1
    assert ''.join(toc_rows[0].xpath('.//w:t/text()',namespaces=NS))==expected_heading['title']+'6'
    assert toc_rows[0].xpath('./w:hyperlink/@w:anchor',namespaces=NS)==[expected_heading['bookmark_name']]
    assert not toc_rows[0].xpath('./w:pPr/w:sectPr',namespaces=NS)
    for run in toc_rows[0].xpath('.//w:r[w:t]',namespaces=NS):
        assert run.xpath('./w:rPr/w:vanish/@w:val',namespaces=NS)==['0']
        assert run.xpath('./w:rPr/w:webHidden/@w:val',namespaces=NS)==['0']
    assert source.read_bytes()==original


def test_toc_field_controls_are_removed_but_cached_title_and_page_survive():
    from app.services.office_layout import _remove_toc_field_controls

    root=etree.Element(qn('w:body'))
    def run(parent):
        node=etree.SubElement(parent,qn('w:r'))
        return node
    first=etree.SubElement(root,qn('w:p'))
    begin=run(first);etree.SubElement(begin,qn('w:fldChar')).set(qn('w:fldCharType'),'begin')
    instruction=run(first);text=etree.SubElement(instruction,qn('w:instrText'));text.text=' TOC \\o "1-3" \\h '
    separate=run(first);etree.SubElement(separate,qn('w:fldChar')).set(qn('w:fldCharType'),'separate')
    title=run(first);etree.SubElement(title,qn('w:t')).text='第1章 绪论'
    second=etree.SubElement(root,qn('w:p'))
    nested_begin=run(second);etree.SubElement(nested_begin,qn('w:fldChar')).set(qn('w:fldCharType'),'begin')
    nested_instruction=run(second);page_instruction=etree.SubElement(nested_instruction,qn('w:instrText'));page_instruction.text=' PAGEREF chapter1 \\h '
    nested_separate=run(second);etree.SubElement(nested_separate,qn('w:fldChar')).set(qn('w:fldCharType'),'separate')
    page=run(second);etree.SubElement(page,qn('w:t')).text='7'
    nested_end=run(second);etree.SubElement(nested_end,qn('w:fldChar')).set(qn('w:fldCharType'),'end')
    third=etree.SubElement(root,qn('w:p'))
    third_ppr=etree.SubElement(third,qn('w:pPr'));third_style=etree.SubElement(third_ppr,qn('w:pStyle'));third_style.set(qn('w:val'),'TOC1')
    outer_end=run(third);etree.SubElement(outer_end,qn('w:fldChar')).set(qn('w:fldCharType'),'end')
    dangling=etree.SubElement(root,qn('w:p'))
    dangling_ppr=etree.SubElement(dangling,qn('w:pPr'))
    etree.SubElement(dangling_ppr,qn('w:sectPr'))
    dangling_run=run(dangling);etree.SubElement(dangling_run,qn('w:fldChar')).set(qn('w:fldCharType'),'end')

    _remove_toc_field_controls(root,['TOC1'])

    assert ''.join(root.xpath('.//w:t/text()',namespaces=NS))=='第1章 绪论7'
    assert not root.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple',namespaces=NS)
    paragraphs=root.findall(qn('w:p'))
    assert paragraphs==[first,second,dangling]
    assert dangling.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None


def test_source_toc_state_normalizes_layout_tabs_around_cached_labels():
    from app.services.office_layout import _source_toc_state

    root=etree.Element(qn('w:document'))
    body=etree.SubElement(root,qn('w:body'))
    paragraph=etree.SubElement(body,qn('w:p'))
    ppr=etree.SubElement(paragraph,qn('w:pPr'))
    style=etree.SubElement(ppr,qn('w:pStyle'));style.set(qn('w:val'),'TOC1')
    hyperlink=etree.SubElement(paragraph,qn('w:hyperlink'));hyperlink.set(qn('w:anchor'),'chapter-one')
    for value in ('\t\t第1章 绪论','\t','5'):
        text_run=etree.SubElement(hyperlink,qn('w:r'))
        text=etree.SubElement(text_run,qn('w:t'));text.text=value
    profile={
        'toc_style_ids':['TOC1'],
        'toc_style_by_level':{1:'TOC1'},
        'headings':[{'kind':'heading1','key':'chapter-one','title':'第1章 绪论','bookmark_name':'chapter-one'}],
    }

    assert _source_toc_state(root,profile)==[{
        'key':'chapter-one','target_title':'第1章 绪论','label':'第1章 绪论','page':'5',
        'level':1,'style_id':'TOC1','bookmark':'chapter-one',
    }]


def test_static_toc_is_refreshed_again_without_mutating_the_delivered_parts():
    from app.services.office_layout import _layout_input_parts, _remove_toc_field_controls
    body=etree.Element(qn('w:body'))
    for text in ('第一章 测试\t2','第二章 测试\t4'):
        p=etree.SubElement(body,qn('w:p'));pr=etree.SubElement(p,qn('w:pPr'))
        etree.SubElement(pr,qn('w:pStyle')).set(qn('w:val'),'TOC1')
        etree.SubElement(etree.SubElement(p,qn('w:r')),qn('w:t')).text=text
    root=etree.Element(qn('w:document'));root.append(body)
    original=etree.tostring(root);parts={'word/document.xml':original,'unchanged':b'fixed'}
    result=_layout_input_parts(parts,{'toc_style_ids':['TOC1']})
    assert parts['word/document.xml']==original
    assert result['unchanged']==b'fixed'
    refreshed=etree.fromstring(result['word/document.xml'])
    assert refreshed.xpath('.//w:fldChar/@w:fldCharType',namespaces=NS)==['begin','separate','end']
    assert refreshed.xpath('.//w:instrText/text()',namespaces=NS)==[' TOC \\o "1-3" \\h \\z \\u ']
    _remove_toc_field_controls(refreshed.find(qn('w:body')),['TOC1'])
    assert refreshed.xpath('.//w:t/text()',namespaces=NS)==['第一章 测试\t2','第二章 测试\t4']


def test_uploaded_master_safety_rejects_macros_external_fields_and_links(master,tmp_path):
    source,_,_,_=master
    safe=source.read_bytes()
    audit_source_package(safe)
    macro=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(safe)) as before,zipfile.ZipFile(macro,'w') as after:
        for name in before.namelist():after.writestr(name,before.read(name))
        after.writestr('word/vbaProject.bin',b'not-executed')
    with pytest.raises(ProfileError,match='宏或可执行'):
        audit_source_package(macro.getvalue())
    malicious=rewrite(safe,'word/document.xml',lambda root: root.find('.//'+qn('w:p')).append(
        OxmlElement('w:instrText')))
    with zipfile.ZipFile(io.BytesIO(malicious)) as archive:
        xml=load_part(archive,'word/document.xml')
        xml.find('.//'+qn('w:instrText')).text=' INCLUDETEXT "https://example.invalid/data" '
        # Rebuild with the external field instruction in place.
        rebuilt=io.BytesIO()
        with zipfile.ZipFile(rebuilt,'w') as output:
            for name in archive.namelist():output.writestr(name,etree.tostring(xml) if name=='word/document.xml' else archive.read(name))
    with pytest.raises(ProfileError,match='外部数据域'):
        audit_source_package(rebuilt.getvalue())


def test_owned_image_table_and_emphasis_survive_and_are_checked(master,tmp_path):
    _,_,project,_=master
    root=document_upload_dir();root.mkdir(parents=True,exist_ok=True)
    path=root/(project.id+'-sample.png');Image.new('RGB',(400,200),'blue').save(path)
    project.sections[-1].paragraphs += [
        ParagraphBlock(id='img',type='image',image=ImageBlock(url='/media/uploads/'+path.name,caption='系统示意',width_cm=4)),
        ParagraphBlock(id='tbl',type='table',table=TableBlock(rows=3,cols=2,cells=[['列一','列二'],['甲','A'],['乙','B']],caption='实验结果')),
    ]
    data=rendered(master,tmp_path);report=inspect(master,data)
    assert report['status']=='pass',[(c['rule'],c['object']) for c in report['checks'] if c['status']!='pass']
    assert any(c['rule']=='master.image_geometry' for c in report['checks'])
    # Imported Word images can carry explicit display extents that differ from
    # the embedded bitmap ratio; the renderer must preserve that source geometry
    # and fit it as a pair instead of rejecting the whole manuscript.
    project.sections[-1].paragraphs[-2].image.height_cm = 1
    project.sections[-1].paragraphs[-2].image.width_cm = 4
    mismatched = rendered(master,tmp_path)
    assert inspect(master,mismatched)['status']=='pass'
    bad=rewrite(data,'word/document.xml',lambda r:r.xpath('//*[local-name()="extent"]')[0].set('cx','100000'))
    assert inspect(master,bad)['status']=='fail'
    project.sections[-1].paragraphs[-2].image.url='/media/uploads/another-project-sample.png'
    with pytest.raises(ProfileError):rendered(master,tmp_path)


@pytest.mark.parametrize(("crop_nodes", "expected"), [
    pytest.param([], "pass", id="absent"),
    pytest.param([{"l": "0", "t": "0", "r": "0", "b": "0"}], "pass", id="zero"),
    pytest.param([{"l": "50000"}], "fail", id="left"),
    pytest.param([{"t": "1000"}], "fail", id="top"),
    pytest.param([{"r": "2000"}], "fail", id="right"),
    pytest.param([{"b": "-100"}], "fail", id="bottom"),
    pytest.param([{"l": "invalid"}], "fail", id="malformed"),
    pytest.param([{}, {}], "fail", id="duplicate"),
])
def test_master_image_source_crop_is_detected(master, tmp_path, crop_nodes, expected):
    """An unchanged picture asset and outer dimensions cannot prove an uncropped image."""
    _, _, project, _ = master
    uploads = document_upload_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    image = uploads / (project.id + "-crop.png")
    Image.new("RGB", (400, 200), "blue").save(image)
    project.sections[-1].paragraphs.append(ParagraphBlock(
        id="crop-test", type="image", image=ImageBlock(
            url="/media/uploads/" + image.name, caption="图片", width_cm=4)))
    data = rendered(master, tmp_path)

    def apply_crop(root):
        ns = {**NS, "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture"}
        controls = root.xpath('.//w:sdt[w:sdtPr/w:tag[@w:val="pg-block:crop-test"]]/w:sdtContent', namespaces=ns)
        assert len(controls) == 1
        fills = controls[0].xpath('.//pic:blipFill', namespaces=ns)
        assert len(fills) == 1
        for attrs in crop_nodes:
            crop = etree.Element('{http://schemas.openxmlformats.org/drawingml/2006/main}srcRect')
            for key, value in attrs.items():
                crop.set(key, value)
            fills[0].insert(1, crop)

    if crop_nodes:
        data = rewrite(data, 'word/document.xml', apply_crop)
    report = inspect(master, data)
    assert report["status"] == expected, report["checks"]
    crop_check = next(check for check in report["checks"]
                      if check["rule"] == "master.image_source_crop" and check["object"] == "crop-test")
    assert crop_check["status"] == expected


@pytest.mark.parametrize(("mode", "attributes", "expected"), [
    pytest.param("unchanged", {}, "pass", id="normal"),
    pytest.param("stretch", {"l": "0", "t": "0", "r": "0", "b": "0"}, "pass", id="zero"),
    pytest.param("stretch", {"l": "25000"}, "fail", id="inset"),
    pytest.param("stretch", {"r": "-1000"}, "fail", id="outset"),
    pytest.param("stretch", {"l": "bad"}, "fail", id="malformed"),
    pytest.param("duplicate", {}, "fail", id="duplicate"),
    pytest.param("tile", {}, "fail", id="tile"),
])
def test_master_image_fill_geometry_is_detected(master, tmp_path, mode, attributes, expected):
    """Picture-fill offsets and tiling can alter the image despite identical outer extents."""
    _, _, project, _ = master
    uploads = document_upload_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    image = uploads / (project.id + "-fill.png")
    Image.new("RGB", (400, 200), "blue").save(image)
    project.sections[-1].paragraphs.append(ParagraphBlock(
        id="fill-test", type="image", image=ImageBlock(
            url="/media/uploads/" + image.name, caption="图片", width_cm=4)))
    data = rendered(master, tmp_path)

    def change_fill(root):
        ns = {**NS, "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
              "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        fills = root.xpath('.//w:sdt[w:sdtPr/w:tag[@w:val="pg-block:fill-test"]]/w:sdtContent//pic:blipFill', namespaces=ns)
        assert len(fills) == 1
        stretch = fills[0].find('{http://schemas.openxmlformats.org/drawingml/2006/main}stretch')
        assert stretch is not None
        if mode == "tile":
            fills[0].remove(stretch)
            etree.SubElement(fills[0], '{http://schemas.openxmlformats.org/drawingml/2006/main}tile')
            return
        rectangles = stretch.findall('{http://schemas.openxmlformats.org/drawingml/2006/main}fillRect')
        assert len(rectangles) == 1
        for key, value in attributes.items():
            rectangles[0].set(key, value)
        if mode == "duplicate":
            etree.SubElement(stretch, '{http://schemas.openxmlformats.org/drawingml/2006/main}fillRect')

    if mode != "unchanged":
        data = rewrite(data, 'word/document.xml', change_fill)
    report = inspect(master, data)
    assert report["status"] == expected, report["checks"]
    fill_check = next(check for check in report["checks"]
                      if check["rule"] == "master.image_fill_geometry" and check["object"] == "fill-test")
    assert fill_check["status"] == expected


@pytest.mark.parametrize(("damage", "expected"), [
    pytest.param("none", "pass", id="normal"),
    pytest.param("explicit_identity", "pass", id="explicit_identity"),
    pytest.param("inner_extent", "fail", id="inner_extent"),
    pytest.param("offset", "fail", id="offset"),
    pytest.param("rotation", "fail", id="rotation"),
    pytest.param("flip", "fail", id="flip"),
    pytest.param("ellipse", "fail", id="ellipse"),
    pytest.param("duplicate_transform", "fail", id="duplicate_transform"),
    pytest.param("missing_inner_extent", "fail", id="missing_inner_extent"),
    pytest.param("floating", "fail", id="floating"),
])
def test_master_image_shape_geometry_is_detected(master, tmp_path, damage, expected):
    """The picture transform must agree with its outer inline dimensions."""
    _, _, project, _ = master
    uploads = document_upload_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    image = uploads / (project.id + "-shape.png")
    Image.new("RGB", (400, 200), "blue").save(image)
    project.sections[-1].paragraphs.append(ParagraphBlock(
        id="shape-test", type="image", image=ImageBlock(
            url="/media/uploads/" + image.name, caption="图片", width_cm=4)))
    data = rendered(master, tmp_path)

    def change_shape(root):
        ns = {**NS, "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
              "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
              "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}
        controls = root.xpath('.//w:sdt[w:sdtPr/w:tag[@w:val="pg-block:shape-test"]]/w:sdtContent', namespaces=ns)
        assert len(controls) == 1
        shape = controls[0].xpath('.//pic:spPr', namespaces=ns)[0]
        transform = shape.find('{http://schemas.openxmlformats.org/drawingml/2006/main}xfrm')
        extent = transform.find('{http://schemas.openxmlformats.org/drawingml/2006/main}ext')
        offset = transform.find('{http://schemas.openxmlformats.org/drawingml/2006/main}off')
        if damage == "explicit_identity":
            transform.set("rot", "0")
            transform.set("flipH", "0")
            transform.set("flipV", "false")
        elif damage == "inner_extent":
            extent.set("cx", str(int(extent.get("cx")) // 2))
        elif damage == "offset":
            offset.set("x", "1000")
        elif damage == "rotation":
            transform.set("rot", "5400000")
        elif damage == "flip":
            transform.set("flipH", "1")
        elif damage == "ellipse":
            shape.find('{http://schemas.openxmlformats.org/drawingml/2006/main}prstGeom').set("prst", "ellipse")
        elif damage == "duplicate_transform":
            shape.insert(0, copy.deepcopy(transform))
        elif damage == "missing_inner_extent":
            transform.remove(extent)
        elif damage == "floating":
            inline = controls[0].xpath('.//wp:inline', namespaces=ns)[0]
            inline.tag = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}anchor'

    if damage != "none":
        data = rewrite(data, 'word/document.xml', change_shape)
    report = inspect(master, data)
    assert report["status"] == expected, report["checks"]
    shape_check = next(check for check in report["checks"]
                       if check["rule"] == "master.image_shape_geometry" and check["object"] == "shape-test")
    assert shape_check["status"] == expected


@pytest.mark.parametrize(("effect", "expected"), [
    pytest.param("none", "pass", id="normal"),
    pytest.param("alpha_asset", "pass", id="alpha_asset"),
    pytest.param("empty_effect_list", "pass", id="empty_effect_list"),
    pytest.param("external_link", "fail", id="external_link"),
    pytest.param("transparent", "fail", id="transparent"),
    pytest.param("grayscale", "fail", id="grayscale"),
    pytest.param("blur", "fail", id="blur"),
    pytest.param("unknown_blip_extension", "fail", id="unknown_blip_extension"),
])
def test_master_image_effects_are_checked(master, tmp_path, effect, expected):
    """Unrecognized effects cannot silently change or hide a generated image."""
    _, _, project, _ = master
    uploads = document_upload_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    image = uploads / (project.id + "-effect.png")
    if effect == "alpha_asset":
        Image.new("RGBA", (400, 200), (0, 0, 255, 128)).save(image)
    else:
        Image.new("RGB", (400, 200), "blue").save(image)
    project.sections[-1].paragraphs.append(ParagraphBlock(
        id="effect-test", type="image", image=ImageBlock(
            url="/media/uploads/" + image.name, caption="图片", width_cm=4)))
    data = rendered(master, tmp_path)

    def change_effect(root):
        ns = {**NS, "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
              "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        control = root.xpath('.//w:sdt[w:sdtPr/w:tag[@w:val="pg-block:effect-test"]]/w:sdtContent', namespaces=ns)[0]
        if effect == "external_link":
            blip = control.xpath('.//pic:blipFill/a:blip', namespaces=ns)[0]
            blip.set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}link', 'rIdUnknown')
        elif effect in {"transparent", "grayscale", "unknown_blip_extension"}:
            blip = control.xpath('.//pic:blipFill/a:blip', namespaces=ns)[0]
            element = {"transparent": "alphaModFix", "grayscale": "grayscl",
                       "unknown_blip_extension": "extLst"}[effect]
            child = etree.SubElement(blip, '{http://schemas.openxmlformats.org/drawingml/2006/main}' + element)
            if effect == "transparent":
                child.set("amt", "0")
        else:
            shape = control.xpath('.//pic:spPr', namespaces=ns)[0]
            effects = etree.SubElement(shape, '{http://schemas.openxmlformats.org/drawingml/2006/main}effectLst')
            if effect == "blur":
                etree.SubElement(effects, '{http://schemas.openxmlformats.org/drawingml/2006/main}blur').set("rad", "10000")

    if effect not in {"none", "alpha_asset"}:
        data = rewrite(data, 'word/document.xml', change_effect)
    report = inspect(master, data)
    assert report["status"] == expected, report["checks"]
    effect_check = next(check for check in report["checks"]
                        if check["rule"] == "master.image_effects" and check["object"] == "effect-test")
    assert effect_check["status"] == expected


@pytest.mark.parametrize(("first_headers", "second_headers", "expected"), [
    ((None,), (), "pass"),
    (("1",), (), "pass"),
    (("true",), (), "pass"),
    (("on",), (), "pass"),
    (("1",), ("0",), "pass"),
    (("1",), ("false",), "pass"),
    (("1",), ("off",), "pass"),
    ((), (), "fail"),
    (("0",), (), "fail"),
    (("false",), (), "fail"),
    (("off",), (), "fail"),
    (("unexpected",), (), "fail"),
    ((), ("1",), "fail"),
    (("0",), ("1",), "fail"),
    (("1",), ("1",), "fail"),
    (("1",), ("unexpected",), "fail"),
    (("1", "1"), (), "fail"),
    (("1",), ("0", "0"), "fail"),
])
def test_master_table_repeat_header_requires_one_enabled_first_row(
        master, tmp_path, first_headers, second_headers, expected):
    """Inspect actual emitted table flags, including explicit off and malformed rows."""
    _, _, project, _ = master
    project.sections[-1].paragraphs.append(ParagraphBlock(
        id="table-header-test", type="table", table=TableBlock(
            rows=2, cols=1, cells=[["表头"], ["数据"]], caption="表格")))
    data = rendered(master, tmp_path)

    def edit_table(root):
        tables = root.xpath(
            './/w:sdt[w:sdtPr/w:tag[@w:val="pg-block:table-header-test"]]/w:sdtContent/w:tbl',
            namespaces=NS,
        )
        assert len(tables) == 1
        rows = tables[0].findall(qn("w:tr"))
        assert len(rows) == 2
        for row, values in zip(rows, (first_headers, second_headers)):
            properties = row.find(qn("w:trPr"))
            if properties is None and values:
                properties = OxmlElement("w:trPr")
                row.insert(0, properties)
            if properties is None:
                continue
            for old in properties.findall(qn("w:tblHeader")):
                properties.remove(old)
            for value in values:
                header = OxmlElement("w:tblHeader")
                if value is not None:
                    header.set(qn("w:val"), value)
                properties.append(header)

    report = inspect(master, rewrite(data, "word/document.xml", edit_table))
    checks = [item for item in report["checks"]
              if item["rule"] == "master.table_header" and item["object"] == "table-header-test"]
    assert len(checks) == 1
    assert checks[0]["status"] == expected
    if expected == "pass":
        assert report["status"] == "pass"
    else:
        with pytest.raises(DocxOutputCheckError):
            require_output_check_report(report)


@pytest.mark.parametrize(("height_rule", "height_value", "expected"), [
    (None, None, "pass"),
    ("auto", "0", "pass"),
    ("atLeast", "240", "pass"),
    ("exact", "120", "fail"),
    ("exact", "2400", "fail"),
    ("unknown", "240", "fail"),
    ("atLeast", "-1", "fail"),
    ("atLeast", None, "fail"),
])
def test_master_table_rejects_fixed_or_invalid_direct_row_height(master, tmp_path, height_rule, height_value, expected):
    """Fixed or invalid direct row heights cannot be accepted as complete content."""
    _, _, project, _ = master
    project.sections[-1].paragraphs.append(ParagraphBlock(
        id="table-height-test", type="table", table=TableBlock(
            rows=2, cols=1, cells=[["表头"], ["需要完整显示的较长单元格内容"]], caption="表格")))
    data = rendered(master, tmp_path)

    def edit_table(root):
        rows = root.xpath(
            './/w:sdt[w:sdtPr/w:tag[@w:val="pg-block:table-height-test"]]/w:sdtContent/w:tbl/w:tr',
            namespaces=NS,
        )
        assert len(rows) == 2
        if height_rule is None:
            return
        properties = rows[1].find(qn("w:trPr"))
        if properties is None:
            properties = OxmlElement("w:trPr")
            rows[1].insert(0, properties)
        height = OxmlElement("w:trHeight")
        height.set(qn("w:hRule"), height_rule)
        if height_value is not None:
            height.set(qn("w:val"), height_value)
        properties.append(height)

    report = inspect(master, rewrite(data, "word/document.xml", edit_table))
    if expected == "fail":
        assert report["status"] == "fail", "unsafe row height passed the output inspector"
    checks = [item for item in report["checks"]
              if item["rule"] == "master.table_row_height" and item["object"] == "table-height-test"]
    assert len(checks) == 1
    assert checks[0]["status"] == expected
    if expected == "pass":
        assert report["status"] == "pass"
    else:
        with pytest.raises(DocxOutputCheckError):
            require_output_check_report(report)


@pytest.mark.parametrize(("height_specs", "duplicate_properties", "expected"), [
    ([{}], False, False),
    ([{"val": "240"}], False, True),
    ([{"hRule": "auto"}], False, False),
    ([{"hRule": "atLeast", "val": "31680"}], False, True),
    ([{"hRule": "atLeast", "val": "31681"}], False, False),
    ([{"hRule": "auto", "val": "0"}], False, True),
    ([{"hRule": "atLeast", "val": "240"}], False, True),
    ([{"hRule": "exact", "val": "240"}], False, False),
    ([{"hRule": "atLeast", "val": "１２"}], False, False),
    ([{"hRule": "auto", "val": "1000000000"}], False, False),
    ([{"hRule": "auto", "val": "0"}, {"hRule": "auto", "val": "0"}], False, False),
    ([], True, False),
])
def test_direct_row_height_check_rejects_ambiguous_properties(height_specs, duplicate_properties, expected):
    from app.services.master_docx_check import _table_direct_row_heights_allow_growth

    table = OxmlElement("w:tbl")
    row = OxmlElement("w:tr")
    table.append(row)
    properties = OxmlElement("w:trPr")
    row.append(properties)
    if duplicate_properties:
        row.append(OxmlElement("w:trPr"))
    for attrs in height_specs:
        height = OxmlElement("w:trHeight")
        for name, value in attrs.items():
            height.set(qn("w:" + name), value)
        properties.append(height)
    assert _table_direct_row_heights_allow_growth(table) is expected


@pytest.mark.parametrize(("rule", "value", "expected"), [
    ("exact", "120", "fail"),
    ("atLeast", "240", "pass"),
    (None, None, "pass"),
])
def test_master_table_checks_conditional_height_in_default_style(master, tmp_path, rule, value, expected):
    """A generated table inherits the source's default style even without tblStyle."""
    source, template, project, confirmation = master

    def edit_styles(root):
        default = root.xpath('//w:style[@w:type="table"][@w:default="1"]', namespaces=NS)[0]
        conditional = OxmlElement("w:tblStylePr")
        conditional.set(qn("w:type"), "firstRow")
        if rule is not None:
            properties = OxmlElement("w:trPr")
            height = OxmlElement("w:trHeight")
            height.set(qn("w:hRule"), rule)
            height.set(qn("w:val"), value)
            properties.append(height)
            conditional.append(properties)
        default.append(conditional)

    source.write_bytes(rewrite(source.read_bytes(), "word/styles.xml", edit_styles))
    specification = extract_writing_specification(source)
    confirmation = confirmation.model_copy(update={"expected_specification_sha256": specification.specification_sha256})
    template = compile_profile(template, source, confirmation, actor="teacher-test")
    project.sections[-1].paragraphs.append(ParagraphBlock(
        id="style-height-table", type="table", table=TableBlock(
            rows=2, cols=1, cells=[["Alpha\nBeta\nGamma"], ["其他内容"]], caption="表格")))
    fixture = (source, template, project, confirmation)
    data = rendered(fixture, tmp_path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    assert not root.xpath(
        './/w:sdt[w:sdtPr/w:tag[@w:val="pg-block:style-height-table"]]//w:tblPr/w:tblStyle',
        namespaces=NS,
    )
    report = inspect(fixture, data)
    checks = [item for item in report["checks"]
              if item["rule"] == "master.table_style_row_height" and item["object"] == "style-height-table"]
    assert len(checks) == 1 and checks[0]["status"] == expected
    assert report["status"] == expected
    if expected == "fail":
        with pytest.raises(DocxOutputCheckError):
            require_output_check_report(report)


@pytest.mark.parametrize(("case", "expected"), [
    ("default_safe", True),
    ("conditional_without_height", True),
    ("no_default_or_heights", True),
    ("default_fixed", False),
    ("explicit_fixed", False),
    ("ancestor_fixed", False),
    ("missing_style", False),
    ("cycle", False),
    ("duplicate_style", False),
    ("duplicate_default", False),
    ("duplicate_height", False),
    ("unknown_rule", False),
    ("no_default_with_height", False),
])
def test_table_style_height_resolution_is_bounded_and_conservative(case, expected):
    from app.services.master_docx_check import _table_style_row_heights_allow_growth

    styles = OxmlElement("w:styles")
    table = OxmlElement("w:tbl")
    table_properties = OxmlElement("w:tblPr")
    table.append(table_properties)

    def add_style(name, *, default=False, parent=None):
        style = OxmlElement("w:style")
        style.set(qn("w:type"), "table")
        style.set(qn("w:styleId"), name)
        if default:
            style.set(qn("w:default"), "1")
        if parent:
            base = OxmlElement("w:basedOn")
            base.set(qn("w:val"), parent)
            style.append(base)
        styles.append(style)
        return style

    def add_conditional(style, rule="atLeast", *, duplicate=False):
        conditional = OxmlElement("w:tblStylePr")
        conditional.set(qn("w:type"), "firstRow")
        if rule is not None:
            properties = OxmlElement("w:trPr")
            for _ in range(2 if duplicate else 1):
                height = OxmlElement("w:trHeight")
                height.set(qn("w:val"), "120")
                height.set(qn("w:hRule"), rule)
                properties.append(height)
            conditional.append(properties)
        style.append(conditional)

    has_default = case not in {"no_default_or_heights", "no_default_with_height"}
    base = add_style("Base", default=has_default)
    if case in {"default_safe", "no_default_with_height"}:
        add_conditional(base)
    elif case == "conditional_without_height":
        add_conditional(base, None)
    elif case == "default_fixed":
        add_conditional(base, "exact")
    elif case == "duplicate_height":
        add_conditional(base, duplicate=True)
    elif case == "unknown_rule":
        add_conditional(base, "unknown")
    elif case == "duplicate_default":
        add_style("Other", default=True)

    if case in {"explicit_fixed", "ancestor_fixed", "missing_style", "cycle", "duplicate_style"}:
        reference = OxmlElement("w:tblStyle")
        reference.set(qn("w:val"), "Child")
        table_properties.append(reference)
        if case == "explicit_fixed":
            add_conditional(add_style("Child"), "exact")
        elif case == "ancestor_fixed":
            add_style("Child", parent="Parent")
            add_conditional(add_style("Parent"), "exact")
        elif case == "cycle":
            add_style("Child", parent="Parent")
            add_style("Parent", parent="Child")
        elif case == "duplicate_style":
            add_style("Child")
            add_style("Child")

    assert _table_style_row_heights_allow_growth(table, styles) is expected


@pytest.mark.parametrize(("figure_caption_page", "table_caption_page", "heading_content_page", "failed_rule"), [
    (3, 4, 3, None),
    (4, 4, 3, "master.figure_pagination"),
    (3, 3, 3, "master.table_caption_pagination"),
    (3, 4, 4, "master.heading_pagination"),
])
def test_master_inspector_consumes_native_object_pages(
        master, tmp_path, monkeypatch, figure_caption_page, table_caption_page, heading_content_page, failed_rule):
    """Exercise the inspector after the receipt verifier has authenticated layout evidence."""
    from app.services import master_docx_check, office_layout
    from app.services.master_layout_anchors import block_bookmark_name

    _, template, project, _ = master
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(tmp_path / "data"))
    uploads = document_upload_dir()
    uploads.mkdir(parents=True)
    image = uploads / (project.id + "-figure.png")
    Image.new("RGB", (100, 50), "blue").save(image)
    project.sections[-1].paragraphs += [
        ParagraphBlock(id="layout-figure", type="image", image=ImageBlock(
            url="/media/uploads/" + image.name, caption="示意图")),
        ParagraphBlock(id="layout-table", type="table", table=TableBlock(
            rows=1, cols=1, cells=[["数据"]], caption="结果表")),
    ]
    data = rendered(master, tmp_path)
    profile = copy.deepcopy(template.source_execution)
    profile["toc_active"] = True
    pages = {
        "layout-figure": {"start": 3, "end": 3, "caption": figure_caption_page},
        "layout-table": {"start": 4, "end": 5, "caption": table_caption_page},
    }
    rows = [{"bookmark": block_bookmark_name(block_id, role), "physical_page": page}
            for block_id, roles in pages.items() for role, page in roles.items()]
    target_section = project.sections[-1]
    target_heading = next(item for item in profile["headings"] if item["key"] == target_section.key)
    rows.extend([
        {"bookmark": target_heading["bookmark_name"], "physical_page": 3},
        {"bookmark": block_bookmark_name(target_section.paragraphs[0].id, "start"),
         "physical_page": heading_content_page},
    ])
    receipt = {"toc": [], "word_native": {"extra_bookmarks": rows}}
    monkeypatch.setattr(master_docx_check, "verify_profile", lambda *_: profile)
    monkeypatch.setattr(office_layout, "verify_layout_receipt", lambda *_: receipt)
    report = inspect(master, data)
    wanted = {"master.figure_pagination", "master.table_caption_pagination", "master.heading_pagination"}
    checks = {c["rule"]: c["status"] for c in report["checks"]
              if c["rule"] in wanted and (c["rule"] != "master.heading_pagination" or c["object"] == target_section.key)}
    assert checks == {rule: "fail" if failed_rule == rule else "pass" for rule in wanted}
    if failed_rule:
        with pytest.raises(DocxOutputCheckError) as failure:
            require_output_check_report(report)
        assert any(item["rule"] == failed_rule and item["status"] == "fail"
                   for item in failure.value.report["checks"])



@pytest.mark.parametrize(("actual_gap", "expected_gap", "expected_status"), [
    (" ", " ", "pass"),
    ("\t", "\t", "pass"),
    ("", "", "pass"),
    ("", " ", "fail"),
    (" ", "", "fail"),
    ("\t", "", "fail"),
    ("\t", " ", "fail"),
    ("  ", " ", "fail"),
])
def test_master_inspector_preserves_toc_run_boundary_whitespace(
        master, tmp_path, monkeypatch, actual_gap, expected_gap, expected_status):
    """A TOC must match its authenticated label, including internal whitespace."""
    from app.services import master_docx_check, office_layout

    _, template, project, _ = master
    data = rendered(master, tmp_path)
    profile = copy.deepcopy(template.source_execution)
    target = next(item for item in profile["headings"] if item["title"] == "第1章 概述")
    profile.update(toc_active=True, toc_style_ids=["TOC1"], toc_style_by_level={"1": "TOC1"})
    receipt = {"toc": [{"key": target["key"], "label": "第1章" + expected_gap + "概述",
                        "page": "3", "style_id": "TOC1", "level": 1,
                        "bookmark": target["bookmark_name"]}], "word_native": {}}
    monkeypatch.setattr(master_docx_check, "verify_profile", lambda *_: profile)
    monkeypatch.setattr(office_layout, "verify_layout_receipt", lambda *_: receipt)

    def insert_toc(root):
        body = root.find(qn("w:body"))
        paragraph = etree.Element(qn("w:p"))
        ppr = etree.SubElement(paragraph, qn("w:pPr"))
        etree.SubElement(ppr, qn("w:pStyle")).set(qn("w:val"), "TOC1")
        hyperlink = etree.SubElement(paragraph, qn("w:hyperlink"))
        hyperlink.set(qn("w:anchor"), target["bookmark_name"])

        def add_run(parent, text=None, tab=False):
            run = etree.SubElement(parent, qn("w:r"))
            properties = etree.SubElement(run, qn("w:rPr"))
            for name in ("vanish", "webHidden"):
                etree.SubElement(properties, qn("w:" + name)).set(qn("w:val"), "0")
            if tab:
                etree.SubElement(run, qn("w:tab"))
            if text is not None:
                node = etree.SubElement(run, qn("w:t"))
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                node.text = text

        add_run(hyperlink, text="第1章")
        if actual_gap:
            add_run(hyperlink, text=actual_gap if actual_gap != "\t" else None,
                    tab=actual_gap == "\t")
        add_run(hyperlink, text="概述")
        add_run(hyperlink, tab=True)
        page = etree.SubElement(hyperlink, qn("w:fldSimple"))
        page.set(qn("w:instr"), " PAGEREF " + target["bookmark_name"] + " \\h ")
        page.set(qn("w:dirty"), "false")
        add_run(page, text="3")
        body.insert(0, paragraph)

    changed = rewrite(data, "word/document.xml", insert_toc)
    result = inspect(master, changed)
    checks = [item for item in result["checks"] if item["rule"] == "master.toc_row"]
    assert len(checks) == 1
    assert checks[0]["status"] == expected_status


def test_master_numbering_counts_untitled_objects_across_sections_and_chapters(master, tmp_path, monkeypatch):
    source, old_template, _, confirmation = master
    doc = Document(source)
    doc.add_heading('第2章 实现', 1)
    doc.add_paragraph('第二章正文示例。' * 8)
    doc.save(source)
    template = PaperTemplate(id=old_template.id, name=old_template.name, source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'],
        meta_fields=old_template.meta_fields)
    confirmation.expected_specification_sha256 = extract_writing_specification(source).specification_sha256
    template = compile_profile(template, source, confirmation, actor='teacher-test')
    project = WritingProject(id='numbering-project', template_id=template.id,
        meta={'title': '测试题目', 'author': '测试学生', 'student_id': 'LOCAL-001'},
        sections=[ProjectSection(key=n.key, type=n.type, title=n.title) for n in flatten(template.sections)
                  if n.type.value not in {'cover', 'toc'}])
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'data'))
    upload = document_upload_dir()
    upload.mkdir(parents=True)
    image = upload / (project.id + '-figure.png')
    Image.new('RGB', (40, 20), 'blue').save(image)

    def figure(ident, caption):
        return ParagraphBlock(id=ident, type='image', image=ImageBlock(
            url='/media/uploads/' + image.name, caption=caption))

    def table(ident, caption):
        return ParagraphBlock(id=ident, type='table', table=TableBlock(
            rows=1, cols=1, cells=[['内容']], caption=caption))

    by_title = {section.title: section for section in project.sections}
    by_title['第1章 概述'].paragraphs.extend([figure('img-empty', ''), table('tbl-empty', '')])
    by_title['1.1 研究内容'].paragraphs.extend([
        figure('img-next', '图 9.9 系统示意'), table('tbl-next', '表 9.9 实验结果')])
    by_title['第2章 实现'].paragraphs.extend([figure('img-ch2', '新章示意'), table('tbl-ch2', '新章结果')])
    output = tmp_path / 'numbered.docx'
    export_project_to_docx(project, template, str(output), template_path=str(source))
    data = output.read_bytes()

    def caption_text(block_id):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = load_part(archive, 'word/document.xml')
        content = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent',
            namespaces=NS, tag='pg-block:' + block_id)[0]
        return [''.join(node.xpath('.//w:t/text()', namespaces=NS)) for node in content if node.tag == qn('w:p')]

    assert caption_text('img-empty')[-1] == '图 1.1'
    assert caption_text('tbl-empty')[0] == '表 1.1'
    assert caption_text('img-next')[-1] == '图 1.2 系统示意'
    assert caption_text('tbl-next')[0] == '表 1.2 实验结果'
    assert caption_text('img-ch2')[-1] == '图 2.1 新章示意'
    assert caption_text('tbl-ch2')[0] == '表 2.1 新章结果'
    report = inspect_docx_output(data, project, template, source_template_bytes=source.read_bytes())
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']

    for block_id, expected_rule in [('img-next', 'master.figure_caption'), ('tbl-next', 'master.table_caption')]:
        def corrupt(root):
            content = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent',
                namespaces=NS, tag='pg-block:' + block_id)[0]
            text = next(node for node in content.xpath('.//w:t', namespaces=NS) if '1.2' in node.text)
            text.text = text.text.replace('1.2', '1.1')

        bad = rewrite(data, 'word/document.xml', corrupt)
        result = inspect_docx_output(bad, project, template, source_template_bytes=source.read_bytes())
        assert any(c['rule'] == expected_rule and c['object'] == block_id and c['status'] == 'fail'
                   for c in result['checks'])


def test_master_layout_bookmarks_cover_blocks_objects_and_captions(master, tmp_path, monkeypatch):
    from app.services.master_layout_anchors import block_bookmark_name

    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'data'))
    _, _, project, _ = master
    uploads = document_upload_dir()
    uploads.mkdir(parents=True)
    image = uploads / (project.id + '-figure.png')
    Image.new('RGB', (40, 20), 'blue').save(image)
    project.sections[-1].paragraphs.extend([
        ParagraphBlock(id='figure', type='image', image=ImageBlock(url='/media/uploads/' + image.name, caption='示意图')),
        ParagraphBlock(id='table', type='table', table=TableBlock(rows=2, cols=2,
            cells=[['表头1', '表头2'], ['数据1', '数据2']], caption='结果表')),
    ])
    data = rendered(master, tmp_path)
    report = inspect(master, data)
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    for block in [block for section in project.sections for block in section.paragraphs]:
        roles = ['start', 'end'] + (['caption'] if block.id in {'figure', 'table'} else [])
        for role in roles:
            name = block_bookmark_name(block.id, role)
            marks = root.xpath('.//w:bookmarkStart[@w:name=$name]', namespaces=NS, name=name)
            assert len(marks) == 1 and len(name) <= 40
            assert marks[0].getnext().tag == qn('w:bookmarkEnd')
            assert marks[0].getnext().get(qn('w:id')) == marks[0].get(qn('w:id'))
            assert marks[0].xpath('ancestor::w:sdt[w:sdtPr/w:tag/@w:val=$tag]',
                namespaces=NS, tag='pg-block:' + block.id)
    assert len([c for c in report['checks'] if c['rule'] == 'master.layout_anchor' and c['status'] == 'pass']) == 2 * sum(
        len(section.paragraphs) for section in project.sections) + 2


def test_master_layout_bookmarks_allow_word_boundary_relocation(master, tmp_path, monkeypatch):
    from app.services.master_layout_anchors import block_bookmark_name

    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'data'))
    _, _, project, _ = master
    uploads = document_upload_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    image = uploads / (project.id + '-figure.png')
    Image.new('RGB', (40, 20), 'blue').save(image)
    project.sections[-1].paragraphs.extend([
        ParagraphBlock(id='figure', type='image', image=ImageBlock(url='/media/uploads/' + image.name, caption='示意图')),
        ParagraphBlock(id='table', type='table', table=TableBlock(rows=1, cols=1, cells=[['数据']], caption='结果表')),
    ])
    first_id = project.sections[0].paragraphs[0].id

    def relocate(root):
        def move(block_id, role, target, *, after=False):
            start = root.xpath('.//w:bookmarkStart[@w:name=$name]', namespaces=NS,
                name=block_bookmark_name(block_id, role))[0]
            end = start.getnext()
            start.getparent().remove(start); end.getparent().remove(end)
            if after:
                target.append(start); target.append(end)
            else:
                parent = target.getparent(); position = parent.index(target)
                parent.insert(position, start); parent.insert(position + 1, end)

        first_control = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]', namespaces=NS,
            tag='pg-block:' + first_id)[0]
        region_control = first_control.xpath('ancestor::w:sdt[starts-with(w:sdtPr/w:tag/@w:val,"pg-region:")]', namespaces=NS)[0]
        move(first_id, 'start', region_control)
        move(first_id, 'end', first_control.find(qn('w:sdtContent')), after=True)
        for block_id, role in [('figure', 'start'), ('table', 'caption')]:
            control = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]', namespaces=NS,
                tag='pg-block:' + block_id)[0]
            move(block_id, role, control)
        table_content = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:table"]/w:sdtContent', namespaces=NS)[0]
        move('table', 'end', table_content, after=True)

    report = inspect(master, rewrite(rendered(master, tmp_path), 'word/document.xml', relocate))
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']


def test_master_layout_bookmark_cannot_cross_a_rendered_page_break(master, tmp_path):
    from app.services.master_layout_anchors import block_bookmark_name

    _, _, project, _ = master
    block_id = project.sections[-1].paragraphs[0].id

    def insert_break(root):
        start = root.xpath('.//w:bookmarkStart[@w:name=$name]', namespaces=NS,
            name=block_bookmark_name(block_id, 'start'))[0]
        run = OxmlElement('w:r')
        run.append(OxmlElement('w:lastRenderedPageBreak'))
        start.getparent().insert(start.getparent().index(start), run)

    report = inspect(master, rewrite(rendered(master, tmp_path), 'word/document.xml', insert_break))
    assert any(c['rule'] == 'master.layout_anchor' and c['status'] == 'fail' for c in report['checks'])


@pytest.mark.parametrize('damage', ['missing_start', 'missing_end', 'duplicate_name', 'duplicate_id', 'wrong_location', 'stray_anchor'])
def test_master_layout_bookmarks_reject_missing_duplicate_and_moved_marks(master, tmp_path, damage):
    from app.services.master_layout_anchors import block_bookmark_name

    _, _, project, _ = master
    block_id = project.sections[-1].paragraphs[0].id
    data = rendered(master, tmp_path)
    name = block_bookmark_name(block_id, 'start')

    def corrupt(root):
        start = root.xpath('.//w:bookmarkStart[@w:name=$name]', namespaces=NS, name=name)[0]
        end = start.getnext()
        if damage == 'missing_start':
            start.getparent().remove(start)
        elif damage == 'missing_end':
            end.getparent().remove(end)
        elif damage == 'duplicate_name':
            target = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]//w:p', namespaces=NS, tag='pg-block:' + block_id)[0]
            target.append(copy.deepcopy(start)); target.append(copy.deepcopy(end))
        elif damage == 'duplicate_id':
            other = next(item for item in root.findall('.//' + qn('w:bookmarkStart')) if item is not start)
            other.set(qn('w:id'), start.get(qn('w:id')))
        elif damage == 'wrong_location':
            parent = start.getparent()
            parent.remove(start); parent.remove(end)
            parent.append(start); parent.append(end)
        else:
            extra_start = OxmlElement('w:bookmarkStart')
            extra_start.set(qn('w:name'), block_bookmark_name('not-a-project-block', 'start'))
            extra_start.set(qn('w:id'), '999999')
            extra_end = OxmlElement('w:bookmarkEnd'); extra_end.set(qn('w:id'), '999999')
            start.getparent().append(extra_start); start.getparent().append(extra_end)

    result = inspect(master, rewrite(data, 'word/document.xml', corrupt))
    assert result['status'] == 'fail'
    expected = 'master.layout_anchor_inventory' if damage == 'stray_anchor' else 'master.layout_anchor'
    assert any(c['rule'] == expected and c['status'] == 'fail' for c in result['checks'])


def test_master_layout_bookmarks_reject_duplicate_content_ids_across_sections(master, tmp_path):
    _, _, project, _ = master
    project.sections[-1].paragraphs[0].id = project.sections[0].paragraphs[0].id
    with pytest.raises(ProfileError, match='内容块 ID 重复'):
        rendered(master, tmp_path)


def test_master_layout_bookmarks_reject_duplicate_heading_name_in_dynamic_content(master, tmp_path):
    source, template, project, _ = master
    data = rendered(master, tmp_path)
    heading = next(item for item in template.source_execution['headings'] if item['kind'] != 'toc')

    def duplicate(root):
        mark = root.xpath('.//w:bookmarkStart[@w:name=$name]', namespaces=NS, name=heading['bookmark_name'])[0]
        target = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]//w:p',
            namespaces=NS, tag='pg-block:' + project.sections[-1].paragraphs[0].id)[0]
        target.append(copy.deepcopy(mark))

    report = inspect_docx_output(rewrite(data, 'word/document.xml', duplicate), project, template,
        source_template_bytes=source.read_bytes())
    assert report['status'] == 'fail'
    assert any(c['rule'] == 'master.heading_anchor_unique' and c['status'] == 'fail' for c in report['checks'])


def test_master_accepts_word_omitting_default_zero_paragraph_values(master, tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'data'))
    _, _, project, _ = master
    uploads = document_upload_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    image = uploads / (project.id + '-figure.png')
    Image.new('RGB', (40, 20), 'blue').save(image)
    project.sections[-1].paragraphs.extend([
        ParagraphBlock(id='figure', type='image', image=ImageBlock(url='/media/uploads/' + image.name, caption='示意图')),
        ParagraphBlock(id='table', type='table', table=TableBlock(rows=1, cols=1, cells=[['数据']], caption='结果表')),
    ])

    def omit_zero_values(root):
        for ppr in root.xpath('.//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,"pg-block:")]//w:pPr', namespaces=NS):
            spacing = ppr.find(qn('w:spacing'))
            if spacing is not None and spacing.get(qn('w:before')) == '0':
                del spacing.attrib[qn('w:before')]
            indent = ppr.find(qn('w:ind'))
            if indent is not None:
                for attr in ('firstLineChars', 'left', 'right'):
                    if indent.get(qn('w:' + attr)) == '0':
                        del indent.attrib[qn('w:' + attr)]
                if not indent.attrib:
                    ppr.remove(indent)

    report = inspect(master, rewrite(rendered(master, tmp_path), 'word/document.xml', omit_zero_values))
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']


@pytest.mark.parametrize('damage', ['automatic_spacing', 'hanging_chars', 'left_chars', 'unknown_style', 'invalid_zero'])
def test_master_rejects_ambiguous_or_changed_default_paragraph_values(master, tmp_path, damage):
    _, _, project, _ = master
    block_id = project.sections[-1].paragraphs[0].id

    def corrupt(root):
        paragraph = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]//w:p', namespaces=NS,
            tag='pg-block:' + block_id)[0]
        ppr = paragraph.find(qn('w:pPr'))
        spacing = ppr.find(qn('w:spacing'))
        indent = ppr.find(qn('w:ind'))
        if damage == 'automatic_spacing':
            spacing.set(qn('w:beforeAutospacing'), '1')
        elif damage == 'hanging_chars':
            indent.set(qn('w:hangingChars'), '200')
        elif damage == 'left_chars':
            indent.set(qn('w:leftChars'), '200')
        elif damage == 'unknown_style':
            style = OxmlElement('w:pStyle'); style.set(qn('w:val'), 'MissingParagraphStyle')
            ppr.insert(0, style)
        else:
            spacing.set(qn('w:before'), 'invalid')

    report = inspect(master, rewrite(rendered(master, tmp_path), 'word/document.xml', corrupt))
    assert any(c['rule'] == 'master.effective_format' and c['object'] == block_id and c['status'] == 'fail'
               for c in report['checks'])


def test_master_rich_text_and_decoration_tampering_are_checked(master, tmp_path):
    _, _, project, _ = master
    project.sections[-1].paragraphs = [ParagraphBlock(
        id='rich-text', text='H2O x2 颜色', inline_runs=[
            {'text': 'H'}, {'text': '2', 'vert_align': 'subscript', 'underline': True},
            {'text': 'O '}, {'text': 'x'},
            {'text': '2', 'vert_align': 'superscript', 'bold': True, 'highlight': 'yellow'},
            {'text': ' '}, {'text': '颜色', 'italic': True},
        ],
    )]
    data = rendered(master, tmp_path)
    report = inspect(master, data)
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    runs = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:rich-text"]//w:r', namespaces=NS)
    assert runs[1].find(qn('w:rPr')+'/'+qn('w:vertAlign')).get(qn('w:val')) == 'subscript'
    assert runs[4].find(qn('w:rPr')+'/'+qn('w:vertAlign')).get(qn('w:val')) == 'superscript'
    assert runs[4].find(qn('w:rPr')+'/'+qn('w:highlight')).get(qn('w:val')) == 'yellow'

    def remove_subscript(root):
        run = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:rich-text"]//w:r', namespaces=NS)[1]
        rpr = run.find(qn('w:rPr'))
        rpr.remove(rpr.find(qn('w:vertAlign')))

    damaged = rewrite(data, 'word/document.xml', remove_subscript)
    damaged_report = inspect(master, damaged)
    assert damaged_report['status'] == 'fail'
    assert any(c['rule'] == 'master.inline_decoration' and c['status'] == 'fail' for c in damaged_report['checks'])


def test_master_rejects_inline_text_that_differs_from_saved_paragraph(master, tmp_path):
    _, _, project, _ = master
    project.sections[-1].paragraphs = [ParagraphBlock(
        id='mismatched-rich-text', text='实际保存的文字', inline_runs=[{'text': '不同的文字'}],
    )]
    with pytest.raises(ProfileError, match='行内格式.*段落文字'):
        rendered(master, tmp_path)


@pytest.mark.parametrize(('run_index', 'property_name'), [
    (1, 'vertAlign'), (1, 'u'), (4, 'b'), (4, 'highlight'), (6, 'i'),
])
def test_master_checks_each_rich_text_decoration(master, tmp_path, run_index, property_name):
    _, _, project, _ = master
    project.sections[-1].paragraphs = [ParagraphBlock(
        id='decorations', text='H2O x2 颜色', inline_runs=[
            {'text': 'H'}, {'text': '2', 'vert_align': 'subscript', 'underline': True},
            {'text': 'O '}, {'text': 'x'},
            {'text': '2', 'vert_align': 'superscript', 'bold': True, 'highlight': 'yellow'},
            {'text': ' '}, {'text': '颜色', 'italic': True},
        ],
    )]
    data = rendered(master, tmp_path)

    def remove_property(root):
        runs = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:decorations"]//w:r', namespaces=NS)
        rpr = runs[run_index].find(qn('w:rPr'))
        rpr.remove(rpr.find(qn('w:' + property_name)))

    report = inspect(master, rewrite(data, 'word/document.xml', remove_property))
    assert any(c['rule'] == 'master.inline_decoration' and c['status'] == 'fail' for c in report['checks'])


def test_master_checks_whitespace_and_accepts_split_runs(master, tmp_path):
    _, _, project, _ = master
    project.sections[-1].paragraphs = [ParagraphBlock(
        id='split-runs', text='甲 乙\t丙\n丁', inline_runs=[
            {'text': '甲'}, {'text': ' ', 'bold': True},
            {'text': '乙\t丙\n丁', 'italic': True},
        ],
    )]
    data = rendered(master, tmp_path)
    assert inspect(master, data)['status'] == 'pass'

    def split_first_run(root):
        paragraph = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:split-runs"]//w:p', namespaces=NS)[0]
        run = paragraph.findall(qn('w:r'))[-1]
        split = copy.deepcopy(run)
        original_text = run.find(qn('w:t'))
        original_text.text = '乙'
        for child in list(run):
            if child.tag in {qn('w:tab'), qn('w:br')} or (child.tag == qn('w:t') and child is not original_text):
                run.remove(child)
        split.remove(split.find(qn('w:t')))
        paragraph.insert(paragraph.index(run) + 1, split)

    split = rewrite(data, 'word/document.xml', split_first_run)
    assert inspect(master, split)['status'] == 'pass'

    def remove_space_bold(root):
        run = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:split-runs"]//w:r', namespaces=NS)[1]
        rpr = run.find(qn('w:rPr'))
        rpr.remove(rpr.find(qn('w:b')))

    report = inspect(master, rewrite(split, 'word/document.xml', remove_space_bold))
    assert any(c['rule'] == 'master.inline_decoration' and c['status'] == 'fail' for c in report['checks'])


def test_master_output_check_rejects_saved_text_and_inline_disagreement(master, tmp_path):
    _, _, project, _ = master
    block = ParagraphBlock(id='changed-text', text='保留原文', inline_runs=[{'text': '保留原文', 'bold': True}])
    project.sections[-1].paragraphs = [block]
    data = rendered(master, tmp_path)
    block.text = '保存的是另一段文字'
    report = inspect(master, data)
    assert any(c['rule'] == 'master.inline_text_consistency' and c['status'] == 'fail' for c in report['checks'])


def test_master_content_keeps_template_inherited_decorations(master, tmp_path):
    source, template, project, _ = master
    document = Document(source)
    normal = document.styles['Normal']
    normal.font.bold = True
    normal.font.underline = True
    normal.font.superscript = True
    document.save(source)
    specification = extract_writing_specification(source)
    draft = template.model_copy(deep=True)
    draft.source_execution = {}
    confirmation = ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True,
    )
    published = compile_profile(draft, source, confirmation, actor='teacher-test')
    heading = next(h for h in published.source_execution['headings'] if h['key'] == project.sections[-1].key)
    assert heading['properties']['font.bold'] is True
    assert heading['properties']['font.underline'] == 'single'
    assert heading['properties']['font.vertical_alignment'] == 'superscript'
    project.sections[-1].paragraphs = [ParagraphBlock(
        id='inherited', text='基准格式', inline_runs=[{'text': '基准'}, {'text': '格式', 'italic': True}],
    )]
    updated = (source, published, project, confirmation)
    data = rendered(updated, tmp_path)
    report = inspect(updated, data)
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']

    def cancel_inherited_bold(root):
        run = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:inherited"]//w:r', namespaces=NS)[0]
        run.find(qn('w:rPr') + '/' + qn('w:b')).set(qn('w:val'), '0')

    report = inspect(updated, rewrite(data, 'word/document.xml', cancel_inherited_bold))
    assert any(c['rule'] == 'master.inline_decoration' and c['status'] == 'fail' for c in report['checks'])


def test_confirmed_digit_font_preserves_mixed_text_and_checks_actual_runs(master, tmp_path):
    source, template, project, _ = master
    document = Document(source)
    document.styles['Normal'].font.name = 'Arial'
    document.add_paragraph('各级标题、正文中的数字全部使用Times New Roman, 字号与文字部分相同')
    document.save(source)
    specification = extract_writing_specification(source)
    rule = next(rule for rule in specification.rules
                if rule.property == 'instruction.text' and '各级标题、正文中的数字' in rule.expected)
    draft = template.model_copy(deep=True)
    draft.source_execution = {}
    request = ConfirmWritingProfile(expected_specification_sha256=specification.specification_sha256,
                                   accept_recommended=True, remove_template_instructions=True,
                                   accept_new_object_defaults=True)
    pending = compile_profile(draft, source, request, actor='teacher-test')
    assert pending.source_execution['digit_font_review']['candidates'][0]['rule_id'] == rule.id
    with pytest.raises(ProfileError, match='数字字体规则尚未确认'):
        verify_profile(pending, source.read_bytes())
    request.confirmed_digit_font_rule_ids = [rule.id]
    published = compile_profile(draft, source, request, actor='teacher-test')
    project.sections[-1].paragraphs = [ParagraphBlock(
        id='mixed-digit', text='中文A12B\n符 3Z',
        inline_runs=[{'text': '中文A12B\n', 'bold': True}, {'text': '符 3Z', 'italic': True}],
    )]
    updated = (source, published, project, request)
    data = rendered(updated, tmp_path)
    report = inspect(updated, data)
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = load_part(archive, 'word/document.xml')
        runs = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:mixed-digit"]//w:r', namespaces=NS)
        texts = [''.join(run.xpath('./w:t/text()', namespaces=NS)) for run in runs]
        assert '12' in texts and '3' in texts and 'Z' in texts
        for text in ('12', '3'):
            run = runs[texts.index(text)]
            assert run.xpath('./w:rPr/w:rFonts/@w:ascii', namespaces=NS) == ['Times New Roman']
            assert run.xpath('./w:rPr/w:rFonts/@w:eastAsia', namespaces=NS) == ['Times New Roman']
        assert runs[texts.index('12')].xpath('./w:rPr/w:b/@w:val', namespaces=NS) == ['1']
        assert runs[texts.index('3')].xpath('./w:rPr/w:i/@w:val', namespaces=NS) == ['1']
        paragraph = runs[texts.index('Z')].getparent()
        resolver = EffectiveFormatResolver(load_part(archive, 'word/styles.xml'),
                                           load_part(archive, 'word/theme/theme1.xml'),
                                           load_part(archive, 'word/settings.xml'))
        _, _, effective, _ = resolver.paragraph(paragraph, include_whitespace=True)
        by_text = {run['text']: run['properties'] for run in effective}
        assert by_text['Z']['font.ascii'] == 'Arial'
        assert by_text['12']['font.size'] == by_text['中文A']['font.size']
        assert by_text['3']['font.size'] == by_text['Z']['font.size']
        assert by_text['12']['font.bold'] == by_text['中文A']['font.bold']
        assert by_text['3']['font.italic'] == by_text['Z']['font.italic']

    def corrupt_digit(root):
        run = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:mixed-digit"]//w:r[w:t="12"]', namespaces=NS)[0]
        run.find(qn('w:rPr') + '/' + qn('w:rFonts')).set(qn('w:ascii'), 'Arial')

    altered = inspect(updated, rewrite(data, 'word/document.xml', corrupt_digit))
    assert any(c['rule'] == 'master.digit_font' and c['status'] == 'fail' for c in altered['checks'])


def test_digit_rule_rejects_unknown_confirmation(master):
    source, template, _, confirmation = master
    draft = template.model_copy(deep=True)
    draft.source_execution = {}
    confirmation.confirmed_digit_font_rule_ids = ['not-a-source-rule']
    with pytest.raises(ProfileError, match='数字字体确认项'):
        compile_profile(draft, source, confirmation, actor='teacher-test')


def test_digit_rule_api_requires_review_and_a_fresh_trial(master):
    source, template, _, _ = master
    document = Document(source)
    document.add_paragraph('各级标题、正文中的数字全部使用Times New Roman, 字号与文字部分相同')
    document.save(source)
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'TestAdminPassword123'})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['token']
        imported = client.post('/api/templates/import', files={'file': ('source.docx', source.read_bytes())})
        assert imported.status_code == 200, imported.text
        payload = template.model_dump(mode='json')
        payload.update(source_docx=imported.json()['source_docx'], source_execution={})
        saved = client.post('/api/templates', json=payload)
        assert saved.status_code == 201, saved.text
        draft = saved.json()
        base = '/api/templates/' + draft['id']
        confirmation = {'expected_specification_sha256': draft['writing_specification']['specification_sha256'],
                        'accept_recommended': True, 'remove_template_instructions': True,
                        'accept_new_object_defaults': True}
        pending = client.post(base + '/writing-profile/propose', json=confirmation)
        assert pending.status_code == 200, pending.text
        pending = pending.json()
        assert pending['trials'] is None
        candidate = pending['template']['source_execution']['digit_font_review']['candidates'][0]
        confirmation['proposal_id'] = pending['proposal_id']
        assert client.post(base + '/publish-writing-profile', json=confirmation).status_code == 409
        confirmation['confirmed_digit_font_rule_ids'] = [candidate['rule_id']]
        assert client.post(base + '/publish-writing-profile', json=confirmation).status_code == 409
        forged = client.post(base + '/writing-profile/propose', json={**confirmation, 'confirmed_digit_font_rule_ids': ['forged']})
        assert forged.status_code == 422
        confirmed = client.post(base + '/writing-profile/propose', json=confirmation)
        assert confirmed.status_code == 200, confirmed.text
        confirmed = confirmed.json()
        assert all(trial['status'] == 'pass' for trial in confirmed['trials'].values())
        confirmation['proposal_id'] = confirmed['proposal_id']
        published = client.post(base + '/publish-writing-profile', json=confirmation)
        assert published.status_code == 200, published.text
        assert published.json()['template']['source_execution']['digit_font_rule']['rule_id'] == candidate['rule_id']


def test_digit_rule_checks_inherited_automatic_heading_numbering():
    from app.services.digit_font_rules import numbering_font_error

    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    paragraph = etree.fromstring(f'<w:p xmlns:w="{ns}"><w:pPr><w:pStyle w:val="h"/></w:pPr><w:r><w:t>标题</w:t></w:r></w:p>')
    styles = etree.fromstring(f'<w:styles xmlns:w="{ns}"><w:style w:type="paragraph" w:styleId="h"><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="8"/></w:numPr></w:pPr><w:rPr><w:sz w:val="30"/><w:b/><w:i/></w:rPr></w:style></w:styles>')
    numbering = etree.fromstring(f'''<w:numbering xmlns:w="{ns}">
      <w:abstractNum w:abstractNumId="1">
        <w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1"/></w:lvl>
        <w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/><w:rPr><w:rFonts w:ascii="Times New Roman" w:hint="default"/></w:rPr></w:lvl>
      </w:abstractNum><w:num w:numId="8"><w:abstractNumId w:val="1"/></w:num>
    </w:numbering>''')
    assert numbering_font_error(paragraph, numbering, styles, 'Times New Roman') == ''
    fonts = numbering.xpath('./w:abstractNum/w:lvl[@w:ilvl="1"]/w:rPr/w:rFonts', namespaces=NS)[0]
    fonts.set(qn('w:ascii'), 'Arial')
    assert '数字字体' in numbering_font_error(paragraph, numbering, styles, 'Times New Roman')
    fonts.set(qn('w:ascii'), 'Times New Roman')
    fonts.set(qn('w:asciiTheme'), 'minorHAnsi')
    assert '数字字体' in numbering_font_error(paragraph, numbering, styles, 'Times New Roman')
    fonts.attrib.pop(qn('w:asciiTheme'))
    number_rpr = fonts.getparent()
    size = etree.SubElement(number_rpr, qn('w:sz'))
    size.set(qn('w:val'), '22')
    assert '字号' in numbering_font_error(paragraph, numbering, styles, 'Times New Roman')
    size.set(qn('w:val'), '30')
    bold = etree.SubElement(number_rpr, qn('w:b'))
    bold.set(qn('w:val'), '0')
    assert '粗体' in numbering_font_error(paragraph, numbering, styles, 'Times New Roman')
    bold.set(qn('w:val'), '1')
    italic = etree.SubElement(number_rpr, qn('w:i'))
    italic.set(qn('w:val'), '0')
    assert '斜体' in numbering_font_error(paragraph, numbering, styles, 'Times New Roman')
    italic.set(qn('w:val'), '1')
    assert numbering_font_error(paragraph, numbering, styles, 'Times New Roman') == ''
    style_size = styles.find('.//' + qn('w:sz'))
    style_size.getparent().remove(style_size)
    size.getparent().remove(size)
    assert '字号' in numbering_font_error(paragraph, numbering, styles, 'Times New Roman')
    assert '缺少' in numbering_font_error(paragraph, None, styles, 'Times New Roman')


def test_publish_api_requires_review_preserves_old_snapshot_and_prevents_forgery(master):
    source,template,_,_=master
    with TestClient(app) as client:
        response=client.post('/api/auth/login',json={'username':'admin','password':'TestAdminPassword123'})
        assert response.status_code==200
        client.headers['Authorization']='Bearer '+response.json()['token']
        imported=client.post('/api/templates/import',files={'file':('source.docx',source.read_bytes())}).json()
        payload=template.model_dump(mode='json')
        payload.update(source_docx=imported['source_docx'])
        saved=client.post('/api/templates',json=payload)
        assert saved.status_code==201,saved.text
        draft=saved.json();assert not draft['source_execution']
        confirmation={'expected_specification_sha256':draft['writing_specification']['specification_sha256']}
        base='/api/templates/'+draft['id']
        assert client.post(base+'/publish-writing-profile',json=confirmation).status_code==409
        proposed=client.post(base+'/writing-profile/propose',json=confirmation)
        assert proposed.status_code==200,proposed.text
        data=proposed.json();confirmation['proposal_id']=data['proposal_id']
        assert client.post(base+'/publish-writing-profile',json=confirmation).status_code==422
        confirmation.update(accept_recommended=True,remove_template_instructions=True,accept_new_object_defaults=True)
        published=client.post(base+'/publish-writing-profile',json=confirmation)
        assert published.status_code==200,published.text
        result=published.json()
        assert result['admission']['can_use_for_draft'] is True
        assert result['template']['version']!=draft['version']
        # Repeated confirmation is idempotent; later draft edits do not mutate frozen JSON.
        assert client.post(base+'/publish-writing-profile',json=confirmation).json()['template']['version']==result['template']['version']
        snapshot=copy.deepcopy(result['template'])
        edited=copy.deepcopy(snapshot);edited['name']='修改后的草稿'
        changed=client.put(base,json=edited)
        assert changed.status_code==200,changed.text
        assert not changed.json()['source_execution']
        assert snapshot['source_execution']['profile_sha256']==result['template']['source_execution']['profile_sha256']


def test_reference_quality_flows_from_word_instruction_to_student_project(tmp_path):
    """Freeze the teacher's source rule and evaluate only confirmed student facts."""
    from tests._helpers import register_fixture

    document = Document()
    document.add_paragraph('题目：模板示例')
    document.add_paragraph('日期：2026年5月')
    document.add_heading('参考文献', 1)
    instruction = '参考文献总体样本：（至少15篇近3年，其中5篇是英文）'
    document.add_paragraph(instruction)
    source = tmp_path / 'quality-master.docx'
    document.save(source)
    template = PaperTemplate(
        id='quality-'+uuid.uuid4().hex, name='文献提示测试模板',
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'],
        meta_fields=[MetaField(key='title', label='题目'), MetaField(key='date', label='日期')],
    )

    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'TestAdminPassword123'})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['token']
        imported = client.post('/api/templates/import', files={'file': ('source.docx', source.read_bytes())})
        assert imported.status_code == 200, imported.text
        payload = template.model_dump(mode='json')
        payload['source_docx'] = imported.json()['source_docx']
        saved = client.post('/api/templates', json=payload)
        assert saved.status_code == 201, saved.text
        draft = saved.json()
        base = '/api/templates/' + draft['id']
        confirmation = {'expected_specification_sha256': draft['writing_specification']['specification_sha256']}
        initial = client.post(base+'/writing-profile/propose', json=confirmation)
        assert initial.status_code == 200, initial.text
        candidates = initial.json()['template']['source_execution']['content_rule_review']['candidates']
        quality = next(item for item in candidates if item['checker'] == 'references.recent_language_summary')
        assert quality['source_text'] == instruction
        confirmation['confirmed_content_rule_ids'] = [quality['rule_id']]
        missing_date = client.post(base+'/writing-profile/propose', json=confirmation)
        assert missing_date.status_code == 422
        confirmation['defense_date_field_key'] = 'date'
        proposed = client.post(base+'/writing-profile/propose', json=confirmation)
        assert proposed.status_code == 200, proposed.text
        confirmation.update(proposal_id=proposed.json()['proposal_id'], accept_recommended=True,
                            remove_template_instructions=True, accept_new_object_defaults=True)
        wrong_binding = client.post(base+'/publish-writing-profile', json={**confirmation, 'defense_date_field_key': 'title'})
        assert wrong_binding.status_code == 409
        published = client.post(base+'/publish-writing-profile', json=confirmation)
        assert published.status_code == 200, published.text
        profile = published.json()['template']['source_execution']
        assert profile['confirmed_content_rule_ids'] == [quality['rule_id']]
        assert profile['defense_date_field_key'] == 'date'

        username = 'quality-' + uuid.uuid4().hex[:10]
        student = register_fixture(client, username, 'FixturePassword123', display_name='测试学生')
        login = client.post('/api/auth/login', json={'username': username, 'password': 'FixturePassword123'})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['token']
        created = client.post('/api/projects', json={'template_id': draft['id'], 'title': '测试论文'})
        assert created.status_code == 201, created.text
        project = created.json()
        assert project['requirement_snapshot']['template_instruction_identity']['profile_sha256'] == profile['profile_sha256']
        project_id = project['id']
        section = next(item for item in project['sections'] if item['type'] == 'references')
        blocks = []
        for index in range(15):
            languages = ['en'] if index < 5 else ['zh']
            blocks.append({'id': f'quality-ref-{index}', 'type': 'paragraph', 'text': '', 'reference': {
                'kind': 'journal', 'authors': f'作者{index}', 'title': f'文献{index}', 'source': '期刊', 'year': '2024',
                'language_fact': {'languages': languages}, 'year_fact': {'value': '2024'},
                'language_confirmation_requested': True, 'year_confirmation_requested': True,
            }})
        blocks.extend([
            {'id': 'mixed', 'type': 'paragraph', 'text': '', 'reference': {
                'kind': 'journal', 'authors': '作者', 'title': '混合语种', 'source': '期刊', 'year': '2025',
                'language_fact': {'languages': ['en', 'zh']}, 'year_fact': {'value': '2025'},
                'language_confirmation_requested': True, 'year_confirmation_requested': True,
            }},
            {'id': 'unknown', 'type': 'paragraph', 'text': '', 'reference': {
                'kind': 'journal', 'authors': '作者', 'title': '待确认年份', 'source': '期刊', 'year': '2025',
            }},
        ])
        saved = client.put(f'/api/projects/{project_id}/section/{section["key"]}',
                           json={'base_revision': project['revision'], 'paragraphs': blocks})
        assert saved.status_code == 200, saved.text
        project = saved.json()
        saved_refs = next(item for item in project['sections'] if item['key'] == section['key'])['paragraphs']
        assert saved_refs[0]['reference']['year_fact']['confirmed_by'] == student['id']
        assert saved_refs[0]['reference']['language_fact']['confirmed_by'] == student['id']

        def advisory():
            response = client.get(f'/api/projects/{project_id}/check')
            assert response.status_code == 200, response.text
            report = response.json()
            assert len(report['quality_advisories']) == 1
            assert not any(item['checker'] == 'references.recent_language_summary' for item in report['findings'])
            return report['quality_advisories'][0]['actual']

        assert advisory()['status'] == 'window_unknown'
        dated = client.put(f'/api/projects/{project_id}/meta',
                           json={'base_revision': project['revision'], 'meta': {'date': '2026年5月'}})
        assert dated.status_code == 200, dated.text
        project = dated.json()
        actual = advisory()
        assert actual['window'] == [2024, 2026]
        assert (actual['total'], actual['recent_confirmed'], actual['english_confirmed']) == (17, 16, 5)
        assert (actual['unknown_year'], actual['unknown_language_in_window'], actual['status']) == (1, 1, 'met')
        dated = client.put(f'/api/projects/{project_id}/meta',
                           json={'base_revision': project['revision'], 'meta': {'date': '2027年5月', 'title': '测试论文'}})
        assert dated.status_code == 200, dated.text
        actual = advisory()
        assert actual['window'] == [2025, 2027]
        assert (actual['recent_confirmed'], actual['english_confirmed'], actual['status']) == (1, 0, 'not_met')
        preview = client.post(f'/api/projects/{project_id}/export-preview')
        assert preview.status_code == 200, preview.text
        assert preview.json()['can_export'] is True
        queued = client.post(f'/api/projects/{project_id}/export-jobs')
        assert queued.status_code == 202, queued.text
        assert queued.json()['status'] == 'queued'

        missing_title = client.put(f'/api/projects/{project_id}/meta',
                                   json={'base_revision': dated.json()['revision'], 'meta': {'title': ''}})
        assert missing_title.status_code == 200, missing_title.text
        blocked = client.post(f'/api/projects/{project_id}/export-jobs?force=true')
        assert blocked.status_code == 409
        assert '封面「题目」未填写' in blocked.json()['detail']['missing']
        dated = client.put(f'/api/projects/{project_id}/meta',
                           json={'base_revision': missing_title.json()['revision'], 'meta': {'date': '2026-02-30', 'title': '测试论文'}})
        assert dated.status_code == 200, dated.text
        assert advisory()['status'] == 'window_unknown'


@pytest.mark.parametrize('kind', ['paper', 'document'])
def test_manual_review_api_binds_candidate_snapshot_source_and_permissions(master, tmp_path, monkeypatch, closed_role_session, kind):
    from app.db import Store
    from app.services.export_jobs import create_export_job, run_project_export
    from app.services.document_render_jobs import create_document_render_job, run_document_render_job, get_document_artifact, artifact_storage
    from app.domain.documents.mappers import paper_template_to_domain
    from app.domain.documents.schemas import DocumentPackage, ContentBlock
    from app.domain.documents.specs import SpecVersion, SpecSet
    from app.services.document_project_engine import instantiate_document_project
    from tests._helpers import register_fixture

    source, template, paper, _ = master
    monkeypatch.setenv('PAPERGUARD_TEMPLATE_DIR', str(tmp_path))
    monkeypatch.setenv('PAPERGUARD_ARTIFACT_DIR', str(tmp_path / 'artifacts'))
    (tmp_path / template.source_docx).write_bytes(source.read_bytes())
    source = tmp_path / template.source_docx
    client = TestClient(app)
    admin = client.post('/api/auth/login', json={'username': 'admin', 'password': 'TestAdminPassword123'}).json()
    headers = {'Authorization': 'Bearer ' + admin['token']}
    password = 'Review-' + uuid.uuid4().hex + '-8a'
    student = register_fixture(client, 'review-' + uuid.uuid4().hex[:10], password)
    login = client.post('/api/auth/login', json={'username': student['username'], 'password': password}).json()
    student_headers = {'Authorization': 'Bearer ' + login['token']}
    store = Store()
    paper.owner_id = student['id']
    paper.template_snapshot = template.model_dump(mode='json')
    paper.template_source_snapshot = template.model_dump(mode='json')
    if kind == 'paper':
        store.save_project(paper)
        record = create_export_job(store, paper, template, force=True, actor_id=student['id'])
        record = run_project_export(store, record['id'])
        record_kind = 'project_export'
        base = f'/api/projects/{paper.id}/export-jobs/{record["id"]}'
        download = base + '/download'
        report = record.get('output_check') or {}
        artifact = None
    else:
        logical, version = paper_template_to_domain(template)
        spec = SpecVersion(id=template.id + ':spec@1', spec_set_id=template.id + ':spec', version='1')
        package = DocumentPackage(id=template.id + ':package', document_type_id='thesis', template_version_id=version.id, spec_version_id=spec.id, renderer_version=version.renderer_version, rule_engine_version=version.rule_engine_version, status='published')
        store.save_document_package(package, logical, version, SpecSet(id=spec.spec_set_id, name='空规范', document_type_id='thesis', current_version_id=spec.id), spec)
        project = instantiate_document_project(package, version.definition, project_id=paper.id, owner_id=student['id'], field_values=paper.meta, title='核查候选', organization_context={})
        project.sections = {section.key: [ContentBlock(id=b.id, text=b.text, order=i) for i, b in enumerate(section.paragraphs)] for section in paper.sections}
        store.save_document_project(project)
        bundle = store.get_document_package_bundle(package.id)
        record = create_document_render_job(store, project, bundle, force=False, actor_id=student['id'])
        record = run_document_render_job(store, record['id'])
        record_kind = 'document_render_job'
        base = f'/api/document-projects/{project.id}/render-jobs/{record["id"]}'
        download = base + '/artifact'
        artifact = get_document_artifact(store, record.get('artifact_id', ''))
        report = artifact.output_check if artifact else {}
    assert record['status'] == 'succeeded', record
    review = client.get(base, headers=headers).json()['manual_review']
    assert review['status'] == 'required'
    assert client.get(download, headers=headers).status_code == 409
    candidate = client.get(base + '/review-candidate', headers=headers)
    if kind == 'paper':
        assert candidate.status_code == 409, candidate.text
        assert candidate.json()['detail']['code'] == 'OUTPUT_CHECK_AUTOMATION_REQUIRED'
        candidate_bytes = Path(record['file_path']).read_bytes()
    else:
        assert candidate.status_code == 200, candidate.text
        assert candidate.headers['x-paperguard-output-sha256'] == report['output_sha256']
        candidate_bytes = candidate.content
    payload = {'output_sha256': review['output_sha256'], 'confirmed_check_ids': [item['id'] for item in review['checklist']]}
    assert client.get(base + '/review-candidate', headers=student_headers).status_code == 403
    assert client.post(base + '/manual-review', headers=student_headers, json=payload).status_code == 403
    closed = closed_role_session(student['id'])
    assert client.get(base + '/review-candidate', headers=closed).status_code == 403
    assert client.post(base + '/manual-review', headers=closed, json=payload).status_code == 403
    store.update_user_role_and_status(student['id'], 'student', 'active')
    assert client.post(base + '/manual-review', headers=headers, json={**payload, 'output_sha256': '0'*64}).status_code == 409
    assert client.post(base + '/manual-review', headers=headers, json={**payload, 'confirmed_check_ids': []}).status_code == 422
    confirmed = client.post(base + '/manual-review', headers=headers, json=payload)
    if kind == 'paper':
        assert confirmed.status_code == 409, confirmed.text
        assert confirmed.json()['detail']['code'] == 'OUTPUT_CHECK_AUTOMATION_REQUIRED'
        # A previously recorded confirmation cannot turn an incomplete automatic check into a pass.
        from app.services.manual_output_review import confirm_review
        legacy_record = store.get_workflow_record(record_kind, record['id'])
        legacy_record['manual_review'] = confirm_review(
            report, output_sha256=payload['output_sha256'],
            confirmed_check_ids=payload['confirmed_check_ids'], reviewer_id='legacy-reviewer',
        )
        store.save_workflow_record(record_kind, legacy_record, expected_revision=legacy_record['revision'])
        view = client.get(base, headers=headers).json()
        assert view['manual_review']['status'] == 'required'
        assert view['output_check_status'] == 'unverifiable'
        assert 'download_url' not in view
        assert client.get(download, headers=headers).status_code == 409
    else:
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()['manual_review']['status'] == 'confirmed'
        assert client.get(download, headers=headers).content == candidate_bytes
    original = copy.deepcopy(store.get_workflow_record(record_kind, record['id']))
    changed = copy.deepcopy(original)
    if kind == 'paper':
        changed['project_snapshot']['title'] = '冻结快照被替换'
    else:
        changed['snapshot']['project']['title'] = '冻结快照被替换'
    saved = store.save_workflow_record(record_kind, changed, expected_revision=original['revision'])
    for endpoint in [download, base + '/review-candidate']:
        assert client.get(endpoint, headers=headers).status_code == 409
    assert client.post(base + '/manual-review', headers=headers, json=payload).status_code == 409
    original['revision'] = saved['revision']
    store.save_workflow_record(record_kind, original, expected_revision=saved['revision'])
    # A stored report cannot suppress required review after independent reinspection.
    report_kind = 'document_artifact' if artifact else record_kind
    report_id = artifact.id if artifact else record['id']
    report_record = store.get_workflow_record(report_kind, report_id)
    report_original = copy.deepcopy(report_record)
    report_record['output_check']['office_review_required'] = False
    report_record['output_check']['manual_checks'] = []
    changed_report = store.save_workflow_record(report_kind, report_record, expected_revision=report_record['revision'])
    assert client.get(download, headers=headers).status_code == 409
    assert client.post(base + '/manual-review', headers=headers, json=payload).status_code == 409
    report_original['revision'] = changed_report['revision']
    store.save_workflow_record(report_kind, report_original, expected_revision=changed_report['revision'])
    source_bytes = source.read_bytes()
    source.write_bytes(source_bytes + b'changed-source')
    assert client.get(download, headers=headers).status_code == 409
    assert client.post(base + '/manual-review', headers=headers, json=payload).status_code == 409
    source.write_bytes(source_bytes)
    if artifact:
        artifact_storage().put_bytes(artifact.storage_key, candidate_bytes + b'changed', artifact.content_type)
    else:
        Path(record['file_path']).write_bytes(candidate_bytes + b'changed')
    assert client.get(download, headers=headers).status_code == 409
    assert client.post(base + '/manual-review', headers=headers, json=payload).status_code == 409


def test_semantic_third_level_heading_uses_real_third_level_master_style(tmp_path):
    from app.core.extract.effective_format import EffectiveFormatResolver

    doc = Document()
    doc.styles['Normal'].font.size = Pt(12)
    doc.styles['Heading 2'].font.size = Pt(15)
    doc.styles['Heading 3'].font.size = Pt(14)
    doc.add_heading('第5章 系统实现', 1)
    doc.add_paragraph('正文样本。' * 30)
    doc.add_heading('5.1 硬件系统实现', 2)
    doc.add_heading('5.1.1 模板三级样例', 3)
    doc.add_paragraph('三级样例正文。' * 20)
    doc.add_heading('5.2 软件系统实现', 2)
    doc.add_heading('5.2.1 环境数据显示功能', 2)
    doc.add_paragraph('保留学生正文。' * 20)
    source = tmp_path / 'semantic-conflict.docx'
    doc.save(source)
    original = source.read_bytes()

    spec = extract_writing_specification(source)
    template = PaperTemplate(id='semantic-conflict', name='层级冲突模板', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'])
    confirmation = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True)
    published = compile_profile(template, source, confirmation, actor='teacher-test')
    heading = next(h for h in published.source_execution['headings'] if h['title'] == '5.2.1 环境数据显示功能')
    assert heading['kind'] == 'heading3'
    assert heading['title_overridden'] is True
    assert heading['title_properties']['font.size'] == {'value': 14.0, 'unit': 'pt'}
    assert heading['title_style_id'] == 'Heading3'

    project = WritingProject(id='semantic-conflict-project', template_id=published.id,
        sections=[ProjectSection(key=n.key, type=n.type, title=n.title,
            paragraphs=[ParagraphBlock(id=n.key + '-p', text='学生正文')])
            for n in flatten(published.sections) if n.type.value not in {'cover', 'toc'}])
    output = tmp_path / 'semantic-output.docx'
    export_project_to_docx(project, published, str(output), template_path=str(source))
    report = inspect_docx_output(output.read_bytes(), project, published, source_template_bytes=original)
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        paragraph = next(p for p in root.xpath('.//w:p', namespaces=NS)
                         if ''.join(p.xpath('.//w:t/text()', namespaces=NS)) == '5.2.1 环境数据显示功能')
        assert paragraph.find('./' + qn('w:pPr') + '/' + qn('w:pStyle')).get(qn('w:val')) == 'Heading3'
        assert paragraph.find('./' + qn('w:pPr') + '/' + qn('w:outlineLvl')).get(qn('w:val')) == '2'
        resolver = EffectiveFormatResolver(etree.fromstring(archive.read('word/styles.xml')))
        runs = resolver.paragraph(paragraph)[2]
        assert runs and all(run['properties']['font.size'] == {'value': 14.0, 'unit': 'pt'} for run in runs)
        parts = {name: archive.read(name) for name in archive.namelist()}
    paragraph.find('./' + qn('w:pPr') + '/' + qn('w:pStyle')).set(qn('w:val'), 'Heading2')
    parts['word/document.xml'] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    changed = io.BytesIO()
    with zipfile.ZipFile(changed, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    assert inspect_docx_output(changed.getvalue(), project, published, source_template_bytes=original)['status'] != 'pass'
    assert source.read_bytes() == original


def test_semantic_heading_conflict_requires_a_real_same_level_sample(tmp_path):
    doc = Document()
    doc.styles['Normal'].font.size = Pt(12)
    doc.add_heading('第1章 绪论', 1)
    doc.add_paragraph('正文样本。' * 30)
    doc.add_heading('1.1 系统设计', 2)
    doc.add_heading('1.1.1 存在冲突的标题', 2)
    doc.add_paragraph('需要系统安全处理。' * 20)
    source = tmp_path / 'no-third-level-example.docx'
    doc.save(source)
    spec = extract_writing_specification(source)
    template = PaperTemplate(id='no-level-example', name='缺少三级样本', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'])
    confirmation = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True)
    with pytest.raises(ProfileError, match='缺少可核验的同级标题样例'):
        compile_profile(template, source, confirmation, actor='teacher-test')


def test_corrected_heading_rebuilds_directory_at_matching_level(tmp_path):
    doc = Document()
    doc.styles['Normal'].font.size = Pt(12)
    doc.styles['Heading 3'].font.size = Pt(14)
    for level in (1, 2, 3):
        doc.styles.add_style(f'TOC {level}', WD_STYLE_TYPE.PARAGRAPH)
    doc.add_heading('目录', 1)
    anchor = doc.add_paragraph()
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), ' TOC \\o "1-3" \\h ')
    anchor._p.append(field)
    for text, level in [('第5章 系统实现\t1', 1), ('5.1 硬件系统实现\t1', 2),
                        ('5.1.1 模板三级样例\t1', 3), ('5.2 软件系统实现\t2', 2),
                        ('5.2.1 环境数据显示功能\t2', 2)]:
        doc.add_paragraph(text, style=f'TOC {level}')
    doc.add_heading('第5章 系统实现', 1)
    doc.add_paragraph('正文样本。' * 30)
    doc.add_heading('5.1 硬件系统实现', 2)
    doc.add_heading('5.1.1 模板三级样例', 3)
    doc.add_paragraph('三级样例正文。' * 20)
    doc.add_heading('5.2 软件系统实现', 2)
    doc.add_heading('5.2.1 环境数据显示功能', 2)
    doc.add_paragraph('保留学生正文。' * 20)
    source = tmp_path / 'semantic-toc.docx'
    doc.save(source)
    spec = extract_writing_specification(source)
    template = PaperTemplate(id='semantic-toc', name='目录层级冲突', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'])
    confirmation = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, remove_template_instructions=True, accept_new_object_defaults=True)
    published = compile_profile(template, source, confirmation, actor='teacher-test')
    heading = next(h for h in published.source_execution['headings'] if h['title'] == '5.2.1 环境数据显示功能')
    entry = next(e for e in published.source_execution['toc_entries'] if e['target_key'] == heading['key'])
    assert entry['level'] == 3
    assert entry['style_id'] == published.source_execution['toc_style_by_level'][3]


@pytest.mark.parametrize(('target', 'property_name', 'value', 'expected_status'), [
    ('label', None, None, 'pass'),
    ('label', 'color', '0000FF', 'fail'),
    ('label', 'u', 'single', 'fail'),
    ('page', 'color', 'FF0000', 'fail'),
    ('page', 'u', 'single', 'fail'),
    ('leader', 'color', '0000FF', 'fail'),
    ('leader', 'u', 'single', 'fail'),
])
def test_master_inspector_checks_directory_color_and_underline(
        master, tmp_path, monkeypatch, target, property_name, value, expected_status):
    from app.services import master_docx_check, office_layout

    _, template, _, _ = master
    data = rendered(master, tmp_path)
    profile = copy.deepcopy(template.source_execution)
    heading = next(item for item in profile['headings'] if item['title'] == '第1章 概述')
    rule = {'label': {'color': 'auto', 'underline': 'none'},
            'leader': {'color': 'auto', 'underline': 'none'},
            'page': {'color': 'auto', 'underline': 'none'}}
    profile.update(toc_active=True, toc_style_ids=['TOC1'], toc_style_by_level={'1': 'TOC1'},
                   toc_appearance_by_level={'1': rule})
    receipt = {'toc': [{'key': heading['key'], 'label': heading['title'], 'page': '3',
                        'style_id': 'TOC1', 'level': 1, 'bookmark': heading['bookmark_name']}],
               'word_native': {}}
    monkeypatch.setattr(master_docx_check, 'verify_profile', lambda *_: profile)
    monkeypatch.setattr(office_layout, 'verify_layout_receipt', lambda *_: receipt)

    def insert_style(root):
        style = etree.SubElement(root, qn('w:style'))
        style.set(qn('w:type'), 'paragraph')
        style.set(qn('w:styleId'), 'TOC1')
        etree.SubElement(style, qn('w:name')).set(qn('w:val'), 'TOC 1')

    def insert_toc(root):
        paragraph = etree.Element(qn('w:p'))
        ppr = etree.SubElement(paragraph, qn('w:pPr'))
        etree.SubElement(ppr, qn('w:pStyle')).set(qn('w:val'), 'TOC1')
        hyperlink = etree.SubElement(paragraph, qn('w:hyperlink'))
        hyperlink.set(qn('w:anchor'), heading['bookmark_name'])
        def add_run(parent, text, role):
            run = etree.SubElement(parent, qn('w:r'))
            rpr = etree.SubElement(run, qn('w:rPr'))
            etree.SubElement(rpr, qn('w:color')).set(qn('w:val'),
                value if target == role and property_name == 'color' else 'auto')
            etree.SubElement(rpr, qn('w:u')).set(qn('w:val'),
                value if target == role and property_name == 'u' else 'none')
            for flag in ('vanish', 'webHidden'):
                etree.SubElement(rpr, qn('w:' + flag)).set(qn('w:val'), '0')
            if role == 'leader':
                etree.SubElement(run, qn('w:tab'))
            else:
                etree.SubElement(run, qn('w:t')).text = text
        add_run(hyperlink, heading['title'], 'label')
        add_run(hyperlink, '', 'leader')
        field = etree.SubElement(hyperlink, qn('w:fldSimple'))
        field.set(qn('w:instr'), ' PAGEREF ' + heading['bookmark_name'] + ' \\h ')
        field.set(qn('w:dirty'), 'false')
        add_run(field, '3', 'page')
        root.find(qn('w:body')).insert(0, paragraph)

    data = rewrite(data, 'word/styles.xml', insert_style)
    data = rewrite(data, 'word/document.xml', insert_toc)
    report = inspect(master, data)
    checks = [item for item in report['checks'] if item['rule'] == 'master.toc_appearance']
    assert len(checks) == 1
    assert checks[0]['status'] == expected_status
    if expected_status == 'fail':
        assert report['status'] == 'fail'


def test_master_output_rejects_heading_line_spacing_changed_from_source(master, tmp_path):
    _, template, _, _ = master
    heading = next(item for item in template.source_execution['headings'] if item['kind'] == 'heading1')

    def change_heading_spacing(root):
        paragraphs = root.xpath('.//w:p[.//w:bookmarkStart[@w:name=$name]]',
                                namespaces=NS, name=heading['bookmark_name'])
        assert len(paragraphs) == 1
        paragraph = paragraphs[0]
        ppr = paragraph.find(qn('w:pPr'))
        if ppr is None:
            ppr = OxmlElement('w:pPr')
            paragraph.insert(0, ppr)
        spacing = ppr.find(qn('w:spacing'))
        if spacing is None:
            spacing = OxmlElement('w:spacing')
            ppr.append(spacing)
        spacing.set(qn('w:line'), '999')
        spacing.set(qn('w:lineRule'), 'auto')

    report = inspect(master, rewrite(rendered(master, tmp_path), 'word/document.xml', change_heading_spacing))
    assert report['status'] == 'fail'
    assert any(check['rule'] == 'master.fixed_body_and_sections' and check['status'] == 'fail'
               for check in report['checks'])
