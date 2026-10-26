import copy
import io
import json
import shutil
import zipfile

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from lxml import etree

from app.core.extract.xml_utils import NS, qn
from app.models.project import WritingProject
from app.services import office_layout
from app.services.master_docx_check import _valid_toc_page_reference
from app.services.office_layout import _font_name
from app.services.source_template_profile import ProfileError


def test_office_pdf_font_names_normalize_known_macos_cjk_fallbacks():
    assert _font_name('/ABCDEF+STSongti-SC-Regular') == 'simsun'
    assert _font_name('STHeitiSC-Medium') == 'simhei'
    assert _font_name('TimesNewRomanPSMT') == 'timesnewroman'


@pytest.fixture
def toc_package(tmp_path):
    doc=Document()
    doc.styles.add_style('TOC 1',WD_STYLE_TYPE.PARAGRAPH)
    row=doc.add_paragraph(style='TOC 1')
    row.add_run('第1章 绪论')
    number=row.add_run();number.add_tab();number.add_text('8');number.bold=True
    heading=doc.add_heading('第1章 绪论',1)
    bookmark=etree.Element(qn('w:bookmarkStart'))
    bookmark.set(qn('w:id'),'1');bookmark.set(qn('w:name'),'PGHchapter1')
    heading._p.insert(1,bookmark)
    end=etree.SubElement(heading._p,qn('w:bookmarkEnd'));end.set(qn('w:id'),'1')
    doc.add_paragraph('学生正文。')
    path=tmp_path/'toc.docx';doc.save(path)
    with zipfile.ZipFile(path) as archive:
        parts={name:archive.read(name) for name in archive.namelist()}
    profile={'toc_active':True,'toc_style_ids':['TOC1'],'toc_style_by_level':{'1':'TOC1'},
        'toc_entries':[{'target_key':'ch1','target_title':'第1章 绪论','level':1}],
        'headings':[{'key':'ch1','title':'第1章 绪论','kind':'heading1','bookmark_name':'PGHchapter1'}],
        'fields':[],'profile_sha256':'a'*64}
    return path,parts,profile


def test_refreshed_toc_preserves_styles_sections_and_cached_values_with_live_page_references(toc_package):
    path,parts,profile=toc_package
    before=copy.deepcopy(parts)
    rows,_=office_layout._read_toc(path,profile)
    original_row=etree.tostring(rows[0]['paragraph'])
    office_layout._replace_toc_rows(parts,rows,profile)
    root=etree.fromstring(parts['word/document.xml'])
    row=root.find('.//'+qn('w:hyperlink')).getparent()
    assert _valid_toc_page_reference(row,'PGHchapter1','8')
    assert ''.join(row.xpath('.//w:t/text()',namespaces=NS))=='第1章 绪论8'
    assert row.xpath('.//w:fldSimple/w:r/w:rPr/w:b',namespaces=NS)
    assert row.xpath('./w:hyperlink/@w:anchor',namespaces=NS)==['PGHchapter1']
    assert row.find('.//'+qn('w:tab')) is not None
    assert not row.xpath('.//w:fldSimple//w:tab',namespaces=NS)
    assert etree.tostring(rows[0]['paragraph'])==original_row
    assert root.xpath('.//w:sectPr',namespaces=NS)[0].attrib==etree.fromstring(before['word/document.xml']).xpath('.//w:sectPr',namespaces=NS)[0].attrib
    settings=etree.fromstring(parts['word/settings.xml'])
    assert settings.xpath('./w:updateFields/@w:val',namespaces=NS)==['false']
    assert {k for k in parts if parts[k]!=before[k]}=={'word/document.xml','word/settings.xml'}


@pytest.mark.parametrize('change',['static','wrong_bookmark','locked','dirty','wrong_cache','duplicate','external_field','field_tab'])
def test_independent_check_rejects_unusable_or_misdirected_directory_page_references(toc_package,change):
    path,parts,profile=toc_package
    rows,_=office_layout._read_toc(path,profile)
    office_layout._replace_toc_rows(parts,rows,profile)
    root=etree.fromstring(parts['word/document.xml'])
    row=root.find('.//'+qn('w:hyperlink')).getparent()
    field=row.find('.//'+qn('w:fldSimple'))
    if change=='static':
        parent=field.getparent();index=parent.index(field)
        for child in list(field):parent.insert(index,child);index+=1
        parent.remove(field)
    elif change=='wrong_bookmark':field.set(qn('w:instr'),' PAGEREF PGHwrong \\h ')
    elif change=='locked':field.set(qn('w:fldLock'),'true')
    elif change=='dirty':field.set(qn('w:dirty'),'true')
    elif change=='wrong_cache':field.find('.//'+qn('w:t')).text='99'
    elif change=='duplicate':field.getparent().append(copy.deepcopy(field))
    elif change=='field_tab':field.find(qn('w:r')).insert(0,etree.Element(qn('w:tab')))
    else:field.set(qn('w:instr'),' INCLUDETEXT https://example.invalid/document ')
    assert not _valid_toc_page_reference(row,'PGHchapter1','8')


def test_second_layout_pass_does_not_duplicate_page_fields_or_mutate_delivered_parts(toc_package,tmp_path):
    path,parts,profile=toc_package
    rows,_=office_layout._read_toc(path,profile)
    office_layout._replace_toc_rows(parts,rows,profile)
    first=copy.deepcopy(parts)
    temporary=office_layout._layout_input_parts(parts,profile)
    assert parts==first
    updated=tmp_path/'updated.docx'
    with zipfile.ZipFile(updated,'w') as archive:
        for name,data in temporary.items():archive.writestr(name,data)
    rows,_=office_layout._read_toc(updated,profile)
    office_layout._replace_toc_rows(parts,rows,profile)
    root=etree.fromstring(parts['word/document.xml'])
    assert len(root.findall('.//'+qn('w:fldSimple')))==1
    assert not root.findall('.//'+qn('w:instrText'))


def test_layout_receipt_binds_live_reference_output_and_rejects_old_static_format(toc_package,tmp_path,monkeypatch):
    path,parts,profile=toc_package
    def roundtrip(input_path,directory):
        directory.mkdir()
        shutil.copyfile(input_path,directory/'updated.docx')
        (directory/'before.pdf').write_bytes(b'fixture measured layout')
        (directory/'after.pdf').write_bytes(b'fixture measured layout')
    monkeypatch.setattr(office_layout,'_office_roundtrip',roundtrip)
    monkeypatch.setattr(office_layout,'_pdf_state',lambda p:{'page_count':9,'destinations':[['绪论',8]],'fonts':[]})
    project=WritingProject(id='test-live-toc',template_id='test-master')
    office_layout.refresh_master_layout(path,project,profile)
    receipt=office_layout.verify_layout_receipt(path.read_bytes(),project,profile)
    assert receipt['version']=='office-layout/4'
    assert receipt['page_reference_mode']=='bookmark_pageref'
    with zipfile.ZipFile(path) as archive:parts={n:archive.read(n) for n in archive.namelist()}
    # Even a correctly signed old receipt must no longer validate as this exporter.
    receipt.pop('signature',None);receipt['version']='office-layout/3'
    receipt['signature']=office_layout._mac(receipt)
    parts[office_layout.RECEIPT_PART]=json.dumps(receipt).encode()
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for name,data in parts.items():archive.writestr(name,data)
    with pytest.raises(ProfileError,match='排版证据与当前文件'):
        office_layout.verify_layout_receipt(buffer.getvalue(),project,profile)
