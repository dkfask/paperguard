"""Approved ordinary-Word masters: publication, fidelity and delivery negatives."""
import copy
import io
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

from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS,load_part,qn
from app.core.export.docx_exporter import export_project_to_docx
from app.main import app
from app.models.project import WritingProject,ProjectSection,ParagraphBlock,ImageBlock,TableBlock
from app.models.template import PaperTemplate,MetaField
from app.services.docx_output_check import inspect_docx_output,require_output_check_report
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


def rewrite(data,part,mutate):
    buffer=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source,zipfile.ZipFile(buffer,'w') as output:
        for name in source.namelist():
            payload=source.read(name)
            if name==part:
                root=etree.fromstring(payload);mutate(root);payload=etree.tostring(root)
            output.writestr(name,payload)
    return buffer.getvalue()


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
        record = create_export_job(store, paper, template, force=False, actor_id=student['id'])
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
    assert candidate.status_code == 200, candidate.text
    assert candidate.headers['x-paperguard-output-sha256'] == report['output_sha256']
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
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['manual_review']['status'] == 'confirmed'
    assert client.get(download, headers=headers).content == candidate.content
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
        artifact_storage().put_bytes(artifact.storage_key, candidate.content + b'changed', artifact.content_type)
    else:
        from pathlib import Path
        Path(record['file_path']).write_bytes(candidate.content + b'changed')
    assert client.get(download, headers=headers).status_code == 409
    assert client.post(base + '/manual-review', headers=headers, json=payload).status_code == 409
