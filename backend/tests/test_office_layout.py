import copy
import io
import json
import hashlib
import platform
import shutil
import zipfile

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt, RGBColor
from lxml import etree

from app.core.extract.xml_utils import NS, qn
from app.models.project import ImageBlock, ParagraphBlock, ProjectSection, TableBlock, WritingProject
from app.services import office_layout
from app.services.master_docx_check import _valid_toc_page_reference
from app.services.office_layout import _font_name
from app.services.source_template_profile import ProfileError


@pytest.fixture(autouse=True)
def simulated_word_pagination(monkeypatch):
    """Offline tests simulate Word; real Word is checked separately."""
    from app.services.word_native_layout import _WORD_SCRIPT, _directory_references, _page_value

    def measure(path, deadline, extra_bookmarks=None):
        rows = []
        for name, cached in _directory_references(path.read_bytes()):
            logical = _page_value(cached)
            rows.append({"bookmark": name, "cached_page": cached, "physical_page": logical,
                         "logical_page": logical, "matches": True})
        positions = {row['bookmark']: row for row in rows}
        extra = [{"bookmark": name,
                  "physical_page": positions.get(name, {}).get('physical_page', 1),
                  "logical_page": positions.get(name, {}).get('logical_page', 1)}
                 for name in (extra_bookmarks or [])]
        return {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "word_version": "16.113.3", "macos_version": platform.mac_ver()[0],
                "machine": platform.machine(), "probe_sha256": hashlib.sha256(_WORD_SCRIPT.encode()).hexdigest(),
                "page_count": max([1] + [row["physical_page"] for row in rows]),
                "pagination_stable": True, "toc_matches": True, "toc": rows,
                "extra_bookmarks": extra}

    monkeypatch.setattr(office_layout, "_measure_native_layout", measure)


def test_office_pdf_font_names_do_not_treat_substitutes_as_required_fonts():
    assert _font_name('宋体') == _font_name('/ABCDEF+SimSun')
    assert _font_name('黑体') == _font_name('/ABCDEF+SimHei')
    assert _font_name('/ABCDEF+STSongti-SC-Regular') != _font_name('宋体')
    assert _font_name('STHeitiSC-Medium') != _font_name('黑体')
    assert _font_name('TimesNewRomanPSMT') == 'timesnewroman'


@pytest.mark.parametrize(('blocks', 'expected'), [
    ([ParagraphBlock(id='figure', type='image', image=ImageBlock(caption=''))],
     {'FigureCJK', 'FigureLatin'}),
    ([ParagraphBlock(id='table', type='table', table=TableBlock(
        rows=1, cols=2, cells=[['数据', '42']], caption=''))],
     {'TableCaptionCJK', 'TableCaptionLatin', 'TableTextCJK', 'TableTextLatin'}),
    ([ParagraphBlock(id='empty-table', type='table', table=TableBlock(
        rows=1, cols=1, cells=[['']], caption=''))],
     {'TableCaptionCJK', 'TableCaptionLatin'}),
    ([ParagraphBlock(id='body', text='正文')], set()),
])
def test_required_fonts_include_only_rendered_object_text(blocks, expected):
    project = WritingProject(id='object-fonts', template_id='master', sections=[
        ProjectSection(key='ch1', type='heading1', title='第一章', paragraphs=blocks),
    ])
    profile = {
        'headings': [{'key': 'ch1', 'kind': 'heading1', 'properties': {}}],
        'fields': [],
        'object_properties': {
            'figure_caption': {'font.eastAsia': 'FigureCJK', 'font.ascii': 'FigureLatin'},
            'table_caption': {'font.eastAsia': 'TableCaptionCJK', 'font.ascii': 'TableCaptionLatin'},
            'table_text': {'font.eastAsia': 'TableTextCJK', 'font.ascii': 'TableTextLatin'},
        },
    }
    assert office_layout._required_fonts(project, profile, {}) == expected


@pytest.mark.parametrize(('block', 'object_properties'), [
    (ParagraphBlock(id='figure', type='image', image=ImageBlock()), None),
    (ParagraphBlock(id='figure', type='image', image=ImageBlock()), {'figure_caption': None}),
    (ParagraphBlock(id='table', type='table', table=TableBlock(rows=1, cols=1, cells=[['x']])),
     {'table_caption': {}, 'table_text': None}),
])
def test_required_fonts_reject_missing_object_styles(block, object_properties):
    project = WritingProject(id='object-fonts', template_id='master', sections=[
        ProjectSection(key='ch1', type='heading1', title='第一章', paragraphs=[block]),
    ])
    profile = {'headings': [{'key': 'ch1', 'kind': 'heading1', 'properties': {}}],
               'fields': [], 'object_properties': object_properties}
    with pytest.raises(ProfileError, match='图表样式缺失'):
        office_layout._required_fonts(project, profile, {})


def test_required_layout_bookmarks_cover_each_content_object_and_caption():
    from app.services.master_layout_anchors import block_bookmark_name, required_layout_bookmarks

    project = WritingProject(id='objects', template_id='master', sections=[
        ProjectSection(key='ch1', type='heading1', title='第一章', paragraphs=[
            ParagraphBlock(id='p1', text='正文'),
            ParagraphBlock(id='fig1', type='image', image=ImageBlock(url='/media/uploads/objects-image.png', caption='图片')),
            ParagraphBlock(id='tab1', type='table', table=TableBlock(rows=1, cols=1, cells=[['A']], caption='表格')),
        ]),
    ])
    profile = {'headings': [{'key': 'ch1', 'kind': 'heading1', 'bookmark_name': 'PGHchapter'}]}
    assert required_layout_bookmarks(project, profile) == [
        'PGHchapter', block_bookmark_name('p1', 'start'), block_bookmark_name('p1', 'end'),
        block_bookmark_name('fig1', 'start'), block_bookmark_name('fig1', 'end'), block_bookmark_name('fig1', 'caption'),
        block_bookmark_name('tab1', 'start'), block_bookmark_name('tab1', 'end'), block_bookmark_name('tab1', 'caption'),
    ]


@pytest.mark.parametrize(('kind', 'start', 'end', 'caption', 'valid'), [
    ('image', 4, 4, 4, True), ('image', 4, 4, 5, False), ('image', 4, 5, 4, False),
    ('table', 4, 6, 4, True), ('table', 4, 6, 3, False), ('table', 4, 3, 4, False),
    ('image', 4, 4, None, False), ('image', 4, 4, True, False),
])
def test_object_and_caption_pages_are_checked_from_word_evidence(kind, start, end, caption, valid):
    from app.services.master_docx_check import _object_page_relation
    from app.services.master_layout_anchors import block_bookmark_name

    block = ParagraphBlock(id='object', type=kind)
    receipt = {'word_native': {'extra_bookmarks': [
        {'bookmark': block_bookmark_name(block.id, role), 'physical_page': page}
        for role, page in [('start', start), ('end', end), ('caption', caption)]]}}
    assert _object_page_relation(block, receipt)[0] is valid


@pytest.mark.parametrize(("heading_page", "content_page", "valid"), [
    (4, 4, True), (4, 5, False), (4, None, False), (4, True, False),
])
def test_heading_page_check_requires_word_evidence_for_following_content(heading_page, content_page, valid):
    from app.services.master_docx_check import _heading_page_relation
    from app.services.master_layout_anchors import block_bookmark_name

    heading = {"bookmark_name": "PGHchapter"}
    section = ProjectSection(key="ch1", type="heading1", title="第一章", paragraphs=[
        ParagraphBlock(id="blank", text="  "), ParagraphBlock(id="body", text="正文")])
    receipt = {"word_native": {"extra_bookmarks": [
        {"bookmark": "PGHchapter", "physical_page": heading_page},
        {"bookmark": block_bookmark_name("body", "start"), "physical_page": content_page},
    ]}}
    assert _heading_page_relation(heading, section, None, receipt)[0] is valid


def test_heading_page_check_uses_table_caption_or_next_heading():
    from app.services.master_docx_check import _heading_page_relation
    from app.services.master_layout_anchors import block_bookmark_name

    heading = {"bookmark_name": "PGHchapter"}
    following = {"bookmark_name": "PGHnext"}
    table = ParagraphBlock(id="table", type="table", table=TableBlock(
        rows=1, cols=1, cells=[["内容"]], caption="表题"))
    section = ProjectSection(key="ch1", type="heading1", title="第一章", paragraphs=[table])
    receipt = {"word_native": {"extra_bookmarks": [
        {"bookmark": "PGHchapter", "physical_page": 4},
        {"bookmark": "PGHnext", "physical_page": 5},
        {"bookmark": block_bookmark_name("table", "caption"), "physical_page": 4},
        {"bookmark": block_bookmark_name("table", "start"), "physical_page": 5},
    ]}}
    assert _heading_page_relation(heading, section, following, receipt)[0] is True
    section.paragraphs = []
    assert _heading_page_relation(heading, section, following, receipt)[0] is False
    assert _heading_page_relation(heading, section, None, receipt)[0] is None


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
        'toc_appearance_by_level': {'1': {'label': {'color': 'auto', 'underline': 'none'},
                                           'leader': {'color': 'auto', 'underline': 'none'},
                                           'page': {'color': 'auto', 'underline': 'none'}}},
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
    assert receipt['version']==office_layout.VERSION
    assert receipt['page_reference_mode']=='bookmark_pageref'
    with zipfile.ZipFile(path) as archive:parts={n:archive.read(n) for n in archive.namelist()}
    # Even a correctly signed old receipt must no longer validate as this exporter.
    receipt.pop('signature',None);receipt['version']='office-layout/9'
    receipt['signature']=office_layout._mac(receipt)
    parts[office_layout.RECEIPT_PART]=json.dumps(receipt).encode()
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for name,data in parts.items():archive.writestr(name,data)
    with pytest.raises(ProfileError,match='排版证据与当前文件'):
        office_layout.verify_layout_receipt(buffer.getvalue(),project,profile)


@pytest.mark.parametrize('damage', [
    'missing', 'duplicate', 'wrong_name', 'out_of_range', 'bool_page', 'toc_disagrees',
    'removed_anchor', 'duplicate_anchor',
])
def test_layout_receipt_rejects_invalid_content_page_evidence(toc_package, monkeypatch, damage):
    from app.services.master_layout_anchors import block_bookmark_name, required_layout_bookmarks

    path, parts, profile = toc_package
    root = etree.fromstring(parts['word/document.xml'])
    paragraph = root.xpath('.//w:p[w:r/w:t="学生正文。"]', namespaces=NS)[0]
    for number, role in enumerate(('start', 'end'), 20):
        start = etree.SubElement(paragraph, qn('w:bookmarkStart'))
        start.set(qn('w:id'), str(number))
        start.set(qn('w:name'), block_bookmark_name('body', role))
        etree.SubElement(paragraph, qn('w:bookmarkEnd')).set(qn('w:id'), str(number))
    parts['word/document.xml'] = etree.tostring(root)
    with zipfile.ZipFile(path, 'w') as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    project = WritingProject(id='content-pages', template_id='test-master', sections=[
        ProjectSection(key='ch1', type='heading1', title='第1章 绪论',
                       paragraphs=[ParagraphBlock(id='body', text='学生正文。')])])
    profile['headings'][0]['properties'] = {}

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda _: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    office_layout.refresh_master_layout(path, project, profile)
    original = path.read_bytes()
    receipt = office_layout.verify_layout_receipt(original, project, profile)
    assert [row['bookmark'] for row in receipt['word_native']['extra_bookmarks']] == required_layout_bookmarks(project, profile)
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    receipt.pop('signature', None)
    rows = receipt['word_native']['extra_bookmarks']
    if damage == 'missing':
        rows.pop()
    elif damage == 'duplicate':
        rows[-1] = dict(rows[-2])
    elif damage == 'wrong_name':
        rows[-1]['bookmark'] = 'PGBwrong'
    elif damage == 'out_of_range':
        rows[-1]['physical_page'] = receipt['page_count'] + 1
    elif damage == 'bool_page':
        rows[-1]['physical_page'] = True
    elif damage == 'toc_disagrees':
        rows[0]['logical_page'] -= 1
    else:
        document = etree.fromstring(parts['word/document.xml'])
        target = document.xpath('.//w:bookmarkStart[@w:name=$name]', namespaces=NS,
                                name=block_bookmark_name('body', 'end'))[0]
        if damage == 'removed_anchor':
            target.getparent().remove(target)
        else:
            target.getparent().append(copy.deepcopy(target))
        parts['word/document.xml'] = etree.tostring(document)
        manifest = office_layout.digest({name: hashlib.sha256(payload).hexdigest()
                                         for name, payload in sorted(parts.items())
                                         if name != office_layout.RECEIPT_PART})
        receipt['document_manifest_sha256'] = manifest
        receipt['word_native']['manifest_sha256'] = manifest
    receipt['signature'] = office_layout._mac(receipt)
    parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    with pytest.raises(ProfileError, match='布局分页证据|书签分页证据|布局书签'):
        office_layout.verify_layout_receipt(output.getvalue(), project, profile)


def test_changed_final_content_pages_do_not_publish_the_output(toc_package, monkeypatch):
    path, _, profile = toc_package
    original = path.read_bytes()
    project = WritingProject(id='changed-content-pages', template_id='test-master')

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda _: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    simulated = office_layout._measure_native_layout
    calls = []

    def final_changed(candidate, deadline, extra_bookmarks=None):
        result = simulated(candidate, deadline, extra_bookmarks)
        calls.append(candidate)
        if candidate.name == 'word-final-candidate.docx':
            result['extra_bookmarks'][0]['physical_page'] -= 1
        return result

    monkeypatch.setattr(office_layout, '_measure_native_layout', final_changed)
    with pytest.raises(ProfileError, match='最终 Word 文件与分页证据不一致'):
        office_layout.refresh_master_layout(path, project, profile)
    assert path.read_bytes() == original


def test_layout_requires_pdf_destination_evidence(toc_package, tmp_path, monkeypatch):
    path, _, profile = toc_package

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture measured layout')
        (directory / 'after.pdf').write_bytes(b'fixture measured layout')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda p: {'page_count': 9, 'destinations': [], 'fonts': []})
    project = WritingProject(id='test-missing-pdf-destination', template_id='test-master')
    with pytest.raises(ProfileError, match='目录目标页定位'):
        office_layout.refresh_master_layout(path, project, profile)


def test_layout_receipt_rejects_reordered_pdf_destinations(toc_package, monkeypatch):
    path, _, profile = toc_package
    project = WritingProject(id='test-reordered-pdf-destination', template_id='test-master')

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture measured layout')
        (directory / 'after.pdf').write_bytes(b'fixture measured layout')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda p: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    office_layout.refresh_master_layout(path, project, profile)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    receipt = json.loads(parts[office_layout.RECEIPT_PART])
    receipt.pop('signature', None)
    receipt['pdf_destinations'][0]['key'] = 'wrong-key'
    receipt['signature'] = office_layout._mac(receipt)
    parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with pytest.raises(ProfileError, match='目标与目录行顺序'):
        office_layout.verify_layout_receipt(buffer.getvalue(), project, profile)


@pytest.mark.parametrize('substituted_file', ['before.pdf', 'after.pdf'])
def test_layout_rejects_substituted_font_before_or_after_refresh(toc_package, monkeypatch, substituted_file):
    path, _, profile = toc_package

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    def pdf_state(path):
        font = '/ABCDEF+STSongti-SC-Regular' if path.name == substituted_file else '/ABCDEF+SimSun'
        return {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': [font]}

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state', pdf_state)
    monkeypatch.setattr(office_layout, '_required_fonts', lambda project, profile, parts: {'宋体'})
    project = WritingProject(id='test-font-substitution', template_id='test-master')
    with pytest.raises(ProfileError, match='排版结果未使用所需字体'):
        office_layout.refresh_master_layout(path, project, profile)


@pytest.mark.parametrize(
    'number_format,number_source,available_fonts,missing_level_text,expected_error',
    [
        ('decimal', 'paragraph', ['/ABCDEF+SimSun'], False, '排版结果未使用所需字体'),
        ('decimal', 'paragraph', ['/ABCDEF+TimesNewRomanPSMT'], False, None),
        ('decimal', 'style', ['/ABCDEF+TimesNewRomanPSMT'], False, None),
        ('decimal', 'disabled', ['/ABCDEF+SimSun'], False, None),
        ('decimal', 'missing-id', ['/ABCDEF+TimesNewRomanPSMT'], False, '标题自动编号缺少可核验的编号定义或层级'),
        ('lowerRoman', 'paragraph', ['/ABCDEF+SimSun'], False, None),
        ('decimal', 'paragraph', ['/ABCDEF+TimesNewRomanPSMT'], True, '标题自动编号缺少可核验的显示格式'),
    ],
)
def test_layout_checks_fonts_for_automatic_heading_numbers_without_other_digits(
        toc_package, monkeypatch, number_format, number_source, available_fonts, missing_level_text, expected_error):
    from app.services.master_layout_anchors import block_bookmark_name

    path, parts, profile = toc_package
    root = etree.fromstring(parts['word/document.xml'])
    body = root.xpath('.//w:p[w:r/w:t="学生正文。"]', namespaces=NS)[0]
    for index, role in enumerate(('start', 'end'), 20):
        anchor = etree.SubElement(body, qn('w:bookmarkStart'))
        anchor.set(qn('w:id'), str(index))
        anchor.set(qn('w:name'), block_bookmark_name('body', role))
        etree.SubElement(body, qn('w:bookmarkEnd')).set(qn('w:id'), str(index))
    heading = root.xpath('.//w:p[w:bookmarkStart/@w:name="PGHchapter1"]', namespaces=NS)[0]
    heading.xpath('.//w:t', namespaces=NS)[0].text = '绪论'
    ppr = heading.find(qn('w:pPr'))
    if number_source == 'style':
        styles = etree.fromstring(parts['word/styles.xml'])
        style_id = ppr.find(qn('w:pStyle')).get(qn('w:val'))
        style = styles.xpath('./w:style[@w:styleId=$value]', namespaces=NS, value=style_id)[0]
        number_ppr = style.find(qn('w:pPr'))
        if number_ppr is None:
            number_ppr = etree.SubElement(style, qn('w:pPr'))
    else:
        number_ppr = ppr
    numpr = etree.SubElement(number_ppr, qn('w:numPr'))
    etree.SubElement(numpr, qn('w:ilvl')).set(qn('w:val'), '0')
    number_id = etree.SubElement(numpr, qn('w:numId'))
    if number_source != 'missing-id':
        number_id.set(qn('w:val'), '0' if number_source == 'disabled' else '997')
    parts['word/document.xml'] = etree.tostring(root)
    if number_source == 'style':
        parts['word/styles.xml'] = etree.tostring(styles)
    numbering = etree.fromstring(parts['word/numbering.xml'])
    abstract = etree.SubElement(numbering, qn('w:abstractNum'))
    abstract.set(qn('w:abstractNumId'), '997')
    level = etree.SubElement(abstract, qn('w:lvl'))
    level.set(qn('w:ilvl'), '0')
    etree.SubElement(level, qn('w:numFmt')).set(qn('w:val'), number_format)
    if not missing_level_text:
        etree.SubElement(level, qn('w:lvlText')).set(qn('w:val'), '%1')
    number = etree.SubElement(numbering, qn('w:num'))
    number.set(qn('w:numId'), '997')
    etree.SubElement(number, qn('w:abstractNumId')).set(qn('w:val'), '997')
    parts['word/numbering.xml'] = etree.tostring(numbering)
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in parts.items():
            archive.writestr(name, data)

    profile['headings'][0].update(title='绪论', properties={})
    profile['digit_font_rule'] = {'font': 'Times New Roman'}
    project = WritingProject(id='automatic-number-font', template_id='test-master', sections=[
        ProjectSection(key='ch1', type='heading1', title='绪论',
                       paragraphs=[ParagraphBlock(id='body', text='正文')])])

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda path: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': available_fonts})
    if expected_error:
        with pytest.raises(ProfileError, match=expected_error):
            office_layout.refresh_master_layout(path, project, profile)
    else:
        office_layout.refresh_master_layout(path, project, profile)


def test_word_page_mismatch_is_corrected_and_final_package_is_remeasured(toc_package, monkeypatch):
    from app.services.word_native_layout import _directory_references
    path, _, profile = toc_package
    project = WritingProject(id='word-correction', template_id='test-master')

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda _: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    simulated = office_layout._measure_native_layout
    measurements = []

    def different_engine(candidate, deadline, extra_bookmarks=None):
        result = simulated(candidate, deadline, extra_bookmarks)
        row = result['toc'][0]
        row['physical_page'] = 7
        row['logical_page'] = 7
        row['matches'] = row['cached_page'] == '7'
        for item in result['extra_bookmarks']:
            if item['bookmark'] == row['bookmark']:
                item['physical_page'] = item['logical_page'] = 7
        result['toc_matches'] = row['matches']
        measurements.append((row['cached_page'], result['source_sha256'], list(extra_bookmarks or [])))
        return result

    monkeypatch.setattr(office_layout, '_measure_native_layout', different_engine)
    office_layout.refresh_master_layout(path, project, profile)
    receipt = office_layout.verify_layout_receipt(path.read_bytes(), project, profile)
    assert _directory_references(path.read_bytes()) == [('PGHchapter1', '7')]
    # An inconsistent TOC is corrected before any full content measurement.
    assert [item[0] for item in measurements] == ['8', '7', '7', '7']
    assert measurements[0][2] == measurements[1][2] == []
    assert measurements[2][2] == measurements[3][2] and measurements[2][2]
    assert measurements[2][1] == measurements[1][1]
    assert receipt['toc'][0]['page'] == '7'
    assert receipt['word_native']['toc'][0]['logical_page'] == 7
    assert measurements[-1][1] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_word_or_nonconverging_pages_do_not_create_delivery_evidence(toc_package, monkeypatch):
    path, _, profile = toc_package
    project = WritingProject(id='word-unavailable', template_id='test-master')

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda _: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    monkeypatch.setattr(office_layout, '_measure_native_layout',
                        lambda *_: (_ for _ in ()).throw(ProfileError('Word 排版组件不可用')))
    with pytest.raises(ProfileError, match='Word 排版组件不可用'):
        office_layout.refresh_master_layout(path, project, profile)
    with zipfile.ZipFile(path) as archive:
        assert office_layout.RECEIPT_PART not in archive.namelist()


def test_signed_receipt_without_valid_word_measurement_is_rejected(toc_package, monkeypatch):
    path, _, profile = toc_package
    project = WritingProject(id='word-missing-evidence', template_id='test-master')

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda _: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    office_layout.refresh_master_layout(path, project, profile)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    receipt = json.loads(parts[office_layout.RECEIPT_PART])
    receipt.pop('signature')
    receipt['word_native']['toc'][0]['logical_page'] = 7
    receipt['signature'] = office_layout._mac(receipt)
    parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    with pytest.raises(ProfileError, match='原生分页与当前目录不一致'):
        office_layout.verify_layout_receipt(output.getvalue(), project, profile)


def test_last_word_measurement_failure_never_exposes_a_signed_output(toc_package, monkeypatch):
    path, _, profile = toc_package
    original = path.read_bytes()
    project = WritingProject(id='final-word-mismatch', template_id='test-master')

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda _: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    simulated = office_layout._measure_native_layout
    calls = []

    def final_changed(candidate, deadline, extra_bookmarks=None):
        result = simulated(candidate, deadline, extra_bookmarks)
        calls.append(candidate)
        if candidate.name == 'word-final-candidate.docx':
            result['page_count'] += 1
        return result

    monkeypatch.setattr(office_layout, '_measure_native_layout', final_changed)
    with pytest.raises(ProfileError, match='最终 Word 文件与分页证据不一致'):
        office_layout.refresh_master_layout(path, project, profile)
    assert path.read_bytes() == original
    with zipfile.ZipFile(path) as archive:
        assert office_layout.RECEIPT_PART not in archive.namelist()


def test_preliminary_layout_rounds_share_export_deadline(toc_package, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.services import export_time_budget

    path, _, profile = toc_package
    original = path.read_bytes()
    elapsed = {"seconds": 0.0}
    rounds = []
    monkeypatch.setattr(export_time_budget, "time", SimpleNamespace(monotonic=lambda: elapsed["seconds"]))

    def roundtrip(input_path, directory):
        rounds.append(directory.name)
        directory.mkdir()
        shutil.copyfile(input_path, directory / "updated.docx")
        elapsed["seconds"] += 6

    monkeypatch.setattr(office_layout, "_office_roundtrip", roundtrip)
    monkeypatch.setattr(office_layout, "_pdf_state", lambda _: {
        "page_count": 9, "destinations": [["绪论", 8]], "fonts": [],
    })
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    project = WritingProject(id="bounded-layout", template_id="test-master")

    with pytest.raises(export_time_budget.ExportBudgetExceeded) as error:
        with export_time_budget.export_budget(deadline):
            office_layout.refresh_master_layout(path, project, profile)

    assert error.value.reason == "total"
    assert rounds == ["round-0", "round-1"]
    assert path.read_bytes() == original


def _wrap_directory_in_control(parts):
    """Build a Word-style content control around the existing fixture row."""
    result = dict(parts)
    root = etree.fromstring(result["word/document.xml"])
    body = root.find(qn("w:body"))
    paragraph = next(p for p in body.findall(qn("w:p"))
                     if p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}") is not None
                     and p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}").get(qn("w:val")) == "TOC1")
    position = body.index(paragraph)
    body.remove(paragraph)
    control = etree.Element(qn("w:sdt"))
    props = etree.SubElement(control, qn("w:sdtPr"))
    etree.SubElement(props, qn("w:tag")).set(qn("w:val"), "pg-toc:placeholder")
    content = etree.SubElement(control, qn("w:sdtContent"))
    content.append(paragraph)
    body.insert(position, control)
    result["word/document.xml"] = etree.tostring(root)
    return result


def _archive_parts(parts):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return data.getvalue()


def test_word_directory_rows_use_original_styles_and_only_copy_confirmed_toc(toc_package, tmp_path):
    _, parts, profile = toc_package
    original = _wrap_directory_in_control(parts)
    updated = copy.deepcopy(original)
    root = etree.fromstring(updated["word/document.xml"])
    content = root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val='pg-toc:placeholder']/w:sdtContent", namespaces=NS)[0]
    assert len(content) == 1
    # Word may rewrite other source parts. Only the directory rows may be used.
    updated["word/styles.xml"] = b'<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
    updated["word/document.xml"] = etree.tostring(root)
    before = copy.deepcopy(original)
    rows = office_layout._word_generated_toc_rows(original, _archive_parts(updated), profile, tmp_path / "toc.docx")
    assert [(row["key"], row["style_id"], row["page"]) for row in rows] == [("ch1", "TOC1", "8")]
    assert original == before


@pytest.mark.parametrize("damage", ["duplicate_control", "duplicate_row", "external_link", "object", "wrong_heading"])
def test_word_directory_rows_reject_unconfirmed_or_unsafe_structures(toc_package, tmp_path, damage):
    _, parts, profile = toc_package
    original = _wrap_directory_in_control(parts)
    updated = copy.deepcopy(original)
    root = etree.fromstring(updated["word/document.xml"])
    control = root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val='pg-toc:placeholder']", namespaces=NS)[0]
    content = control.find(qn("w:sdtContent"))
    paragraph = content.find(qn("w:p"))
    if damage == "duplicate_control":
        control.getparent().append(copy.deepcopy(control))
    elif damage == "duplicate_row":
        content.append(copy.deepcopy(paragraph))
    elif damage == "external_link":
        etree.SubElement(paragraph, qn("w:hyperlink")).set(qn("r:id"), "rIdExternal")
    elif damage == "object":
        etree.SubElement(paragraph, qn("w:object"))
    else:
        paragraph.find(".//" + qn("w:t")).text = "未确认的章节"
    updated["word/document.xml"] = etree.tostring(root)
    with pytest.raises(ProfileError):
        office_layout._word_generated_toc_rows(original, _archive_parts(updated), profile, tmp_path / "toc.docx")


@pytest.fixture
def native_word_directory(toc_package, monkeypatch):
    from app.services import word_native_layout, word_optional_pdf

    path, parts, profile = toc_package
    original = _wrap_directory_in_control(parts)
    path.write_bytes(_archive_parts(original))
    profile = {**profile, "toc_font_requirements": ["宋体"]}
    project = WritingProject(id="native-toc", template_id="test-master")
    monkeypatch.setenv("PAPERGUARD_NATIVE_WORD_TOC", "true")
    monkeypatch.setattr(word_native_layout, "refresh_word_toc", lambda source, **_kw: source.read_bytes())
    monkeypatch.setattr(office_layout, "_office_roundtrip",
                        lambda *_: pytest.fail("native TOC must not invoke LibreOffice"))

    def inventory(data, _directory, _deadline):
        return {
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "script_sha256": hashlib.sha256(word_optional_pdf._SCRIPT.encode()).hexdigest(),
            "observations": [
                {"pdf_sha256": str(index) * 64, "page_count": 8,
                 "word_version": "16.113.3", "fonts": ["/ABCDEF+SimSun"]}
                for index in (1, 2)
            ],
        }

    monkeypatch.setattr(office_layout, "_native_word_font_inventory", inventory)
    return path, profile, project


def test_native_word_directory_produces_versioned_receipt_without_libreoffice(native_word_directory):
    path, profile, project = native_word_directory
    office_layout.refresh_master_layout(path, project, profile)
    receipt = office_layout.verify_layout_receipt(path.read_bytes(), project, profile)
    assert receipt["version"] == office_layout.NATIVE_TOC_VERSION
    assert receipt["toc_preparation_source"] == "word_native"
    assert [row["key"] for row in receipt["toc"]] == ["ch1"]
    assert receipt["word_font_inventory"]["source_sha256"] == receipt["word_native"]["measured_sha256"]


@pytest.mark.parametrize("damage", ["toc_script", "font_source", "font_pdf", "font_missing", "font_pages"])
def test_native_word_receipt_rejects_stale_or_invalid_evidence(native_word_directory, damage):
    path, profile, project = native_word_directory
    office_layout.refresh_master_layout(path, project, profile)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    receipt = json.loads(parts[office_layout.RECEIPT_PART])
    receipt.pop("signature")
    if damage == "toc_script":
        receipt["toc_script_sha256"] = "0" * 64
    elif damage == "font_source":
        receipt["word_font_inventory"]["source_sha256"] = "0" * 64
    elif damage == "font_pdf":
        receipt["word_font_inventory"]["observations"][0]["pdf_sha256"] = "invalid"
    elif damage == "font_missing":
        receipt["word_font_inventory"]["observations"][0]["fonts"] = ["Cambria"]
    else:
        receipt["word_font_inventory"]["observations"][0]["page_count"] += 1
    receipt["signature"] = office_layout._mac(receipt, office_layout.NATIVE_TOC_VERSION)
    parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
    with pytest.raises(ProfileError):
        office_layout.verify_layout_receipt(_archive_parts(parts), project, profile)


def test_native_word_directory_failure_leaves_source_unchanged(native_word_directory, monkeypatch):
    from app.services import word_native_layout

    path, profile, project = native_word_directory
    original = path.read_bytes()
    monkeypatch.setattr(word_native_layout, "refresh_word_toc", lambda *_a, **_kw:
                        (_ for _ in ()).throw(word_native_layout.WordNativeLayoutError("unavailable")))
    with pytest.raises(ProfileError, match="Word 目录更新失败"):
        office_layout.refresh_master_layout(path, project, profile)
    assert path.read_bytes() == original


def test_native_word_font_inventory_rejects_pdf_from_another_word_file(tmp_path, monkeypatch):
    from app.services import word_optional_pdf
    import time

    monkeypatch.setattr(word_optional_pdf, "create_optional_pdf", lambda _data, **_kw:
                        (b"%PDF-test", {"source_sha256": "0" * 64, "pdf_sha256": "1" * 64}))
    with pytest.raises(ProfileError, match="与原文件不匹配"):
        office_layout._native_word_font_inventory(b"word-data", tmp_path, time.monotonic() + 10)


@pytest.fixture
def table_row_layout(toc_package, monkeypatch):
    from app.services.master_layout_anchors import block_bookmark_name
    from app.services.word_native_layout import _WORD_TABLE_ROWS_SCRIPT

    path, parts, profile = toc_package
    root = etree.fromstring(parts['word/document.xml'])
    body = root.find(qn('w:body'))
    caption = etree.Element(qn('w:p'))
    table_doc = Document()
    table = table_doc.add_table(rows=3, cols=1)._tbl
    for index, (role, parent) in enumerate((('caption', caption),
                                           ('start', table.findall(qn('w:tr'))[0].find('.//' + qn('w:p'))),
                                           ('end', table.findall(qn('w:tr'))[-1].find('.//' + qn('w:p')))), 50):
        marker = etree.SubElement(parent, qn('w:bookmarkStart'))
        marker.set(qn('w:id'), str(index))
        marker.set(qn('w:name'), block_bookmark_name('table-rows', role))
        etree.SubElement(parent, qn('w:bookmarkEnd')).set(qn('w:id'), str(index))
    body.insert(len(body) - 1, caption)
    body.insert(len(body) - 1, table)
    parts['word/document.xml'] = etree.tostring(root)
    path.write_bytes(_archive_parts(parts))
    project = WritingProject(id='table-row-pages', template_id='test-master', sections=[
        ProjectSection(key='ch1', type='heading1', title='第1章 绪论', paragraphs=[
            ParagraphBlock(id='table-rows', type='table',
                           table=TableBlock(rows=3, cols=1, cells=[['a'], ['b'], ['c']], caption='table'))])])
    profile['headings'][0]['properties'] = {}
    profile['object_properties'] = {'figure_caption': {}, 'table_caption': {}, 'table_text': {}}

    def roundtrip(input_path, directory):
        directory.mkdir()
        shutil.copyfile(input_path, directory / 'updated.docx')
        (directory / 'before.pdf').write_bytes(b'fixture')
        (directory / 'after.pdf').write_bytes(b'fixture')

    monkeypatch.setattr(office_layout, '_office_roundtrip', roundtrip)
    monkeypatch.setattr(office_layout, '_pdf_state',
                        lambda _: {'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': []})
    simulated = office_layout._measure_native_layout

    def pages(candidate, deadline, extra_bookmarks=None):
        result = simulated(candidate, deadline, extra_bookmarks)
        for item in result['extra_bookmarks']:
            if item['bookmark'] in (block_bookmark_name('table-rows', 'start'),
                                    block_bookmark_name('table-rows', 'caption')):
                item['physical_page'] = item['logical_page'] = 7
            elif item['bookmark'] == block_bookmark_name('table-rows', 'end'):
                item['physical_page'] = item['logical_page'] = 8
        return result

    monkeypatch.setattr(office_layout, '_measure_native_layout', pages)
    calls = []

    def row_pages(candidate, deadline, targets):
        calls.append(candidate)
        assert targets == [{'bookmark': block_bookmark_name('table-rows', 'start'),
                            'end_bookmark': block_bookmark_name('table-rows', 'end'), 'row_count': 3}]
        return {'scope': 'word_table_row_locations_only',
                'source_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
                'word_version': '16.113.3', 'macos_version': platform.mac_ver()[0],
                'machine': platform.machine(),
                'probe_sha256': hashlib.sha256(_WORD_TABLE_ROWS_SCRIPT.encode()).hexdigest(),
                'page_count': 8, 'pagination_stable': True,
                'tables': [{'bookmark': targets[0]['bookmark'], 'rows': [
                    {'row': 1, 'start_page': 7, 'end_page': 7},
                    {'row': 2, 'start_page': 7, 'end_page': 7},
                    {'row': 3, 'start_page': 8, 'end_page': 8}]}]}

    monkeypatch.setattr(office_layout, '_measure_native_table_rows', row_pages)
    return path, profile, project, calls


@pytest.mark.parametrize('role', ['table_caption', 'table_text'])
def test_layout_rejects_a_missing_font_used_only_in_a_table(table_row_layout, role):
    path, profile, project, _ = table_row_layout
    profile['object_properties'][role] = {'font.ascii': 'TableOnlyFont'}
    original = path.read_bytes()
    with pytest.raises(ProfileError, match='排版结果未使用所需字体.*TableOnlyFont'):
        office_layout.refresh_master_layout(path, project, profile)
    assert path.read_bytes() == original


def test_layout_rejects_a_missing_font_used_only_in_a_figure_caption(table_row_layout):
    path, profile, project, _ = table_row_layout
    project.sections[0].paragraphs = [ParagraphBlock(id='figure', type='image', image=ImageBlock())]
    profile['object_properties']['figure_caption'] = {'font.eastAsia': 'FigureOnlyFont'}
    original = path.read_bytes()
    with pytest.raises(ProfileError, match='排版结果未使用所需字体.*FigureOnlyFont'):
        office_layout.refresh_master_layout(path, project, profile)
    assert path.read_bytes() == original


def test_previous_layout_receipt_is_rechecked_against_current_object_fonts(table_row_layout, monkeypatch):
    path, profile, project, _ = table_row_layout
    profile['object_properties']['table_caption'] = {'font.ascii': 'TableOnlyFont'}
    with monkeypatch.context() as old:
        old.setattr(office_layout, '_required_fonts', lambda *_args: set())
        office_layout.refresh_master_layout(path, project, profile)
    original = path.read_bytes()
    with pytest.raises(ProfileError, match='排版证据缺少所需字体'):
        office_layout.verify_layout_receipt(original, project, profile)
    assert path.read_bytes() == original


@pytest.mark.parametrize('legacy', [False, True])
def test_layout_receipt_accepts_present_object_fonts(table_row_layout, monkeypatch, legacy):
    path, profile, project, _ = table_row_layout
    profile['object_properties']['table_caption'] = {'font.ascii': 'TableOnlyFont'}
    monkeypatch.setattr(office_layout, '_pdf_state', lambda _: {
        'page_count': 9, 'destinations': [['绪论', 8]], 'fonts': ['/ABCDEF+TableOnlyFont'],
    })
    office_layout.refresh_master_layout(path, project, profile)
    payload = path.read_bytes()
    if legacy:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        receipt = json.loads(parts[office_layout.RECEIPT_PART])
        receipt.pop('signature')
        receipt['version'] = office_layout.LEGACY_VERSION
        del receipt['word_native']['table_rows']
        receipt['signature'] = office_layout._mac(receipt, receipt['version'])
        parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
        payload = _archive_parts(parts)
    assert office_layout.verify_layout_receipt(payload, project, profile)['version'] == (
        office_layout.LEGACY_VERSION if legacy else office_layout.VERSION)


def test_cross_page_table_row_evidence_is_signed_and_final_document_is_remeasured(table_row_layout):
    from app.services.master_docx_check import _table_row_page_relation

    path, profile, project, calls = table_row_layout
    office_layout.refresh_master_layout(path, project, profile)
    receipt = office_layout.verify_layout_receipt(path.read_bytes(), project, profile)
    assert receipt['version'] == office_layout.VERSION
    assert len(calls) == 2 and calls[0] != calls[1]
    evidence = receipt['word_native']['table_rows']
    assert evidence['manifest_sha256'] == receipt['document_manifest_sha256']
    assert [r['start_page'] for r in evidence['tables'][0]['rows']] == [7, 7, 8]
    assert _table_row_page_relation(project.sections[0].paragraphs[0], receipt)[0] is True


@pytest.mark.parametrize('damage', ['missing', 'reordered', 'wrong_page', 'wrong_boundary',
                                    'wrong_source', 'wrong_probe', 'no_evidence'])
def test_signed_table_row_receipt_rejects_incomplete_or_mismatched_evidence(table_row_layout, damage):
    path, profile, project, _ = table_row_layout
    office_layout.refresh_master_layout(path, project, profile)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    receipt = json.loads(parts[office_layout.RECEIPT_PART])
    receipt.pop('signature')
    evidence = receipt['word_native']['table_rows']
    rows = evidence['tables'][0]['rows']
    if damage == 'missing':
        rows.pop()
    elif damage == 'reordered':
        rows[0], rows[1] = rows[1], rows[0]
    elif damage == 'wrong_page':
        rows[-1]['end_page'] = 9
    elif damage == 'wrong_boundary':
        rows[0]['start_page'] = rows[0]['end_page'] = 6
    elif damage == 'wrong_source':
        evidence['source_sha256'] = '0' * 64
    elif damage == 'wrong_probe':
        evidence['probe_sha256'] = '0' * 64
    else:
        del receipt['word_native']['table_rows']
    receipt['signature'] = office_layout._mac(receipt, receipt['version'])
    parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
    with pytest.raises(ProfileError, match='表格分页证据'):
        office_layout.verify_layout_receipt(_archive_parts(parts), project, profile)


def test_changed_final_table_pages_do_not_publish_the_output(table_row_layout, monkeypatch):
    path, profile, project, calls = table_row_layout
    original = path.read_bytes()
    simulated = office_layout._measure_native_table_rows

    def changed(candidate, deadline, targets):
        result = simulated(candidate, deadline, targets)
        if len(calls) == 2:
            result['tables'][0]['rows'][1]['start_page'] = 8
            result['tables'][0]['rows'][1]['end_page'] = 8
        return result

    monkeypatch.setattr(office_layout, '_measure_native_table_rows', changed)
    with pytest.raises(ProfileError, match='最终 Word 文件与表格分页证据不一致'):
        office_layout.refresh_master_layout(path, project, profile)
    assert path.read_bytes() == original


def test_legacy_table_receipt_remains_readable_without_inventing_row_evidence(table_row_layout):
    path, profile, project, _ = table_row_layout
    office_layout.refresh_master_layout(path, project, profile)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    receipt = json.loads(parts[office_layout.RECEIPT_PART])
    receipt.pop('signature')
    receipt['version'] = office_layout.LEGACY_VERSION
    del receipt['word_native']['table_rows']
    receipt['signature'] = office_layout._mac(receipt, receipt['version'])
    parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
    old = office_layout.verify_layout_receipt(_archive_parts(parts), project, profile)
    assert old['version'] == 'office-layout/10'
    assert 'table_rows' not in old['word_native']


def test_legacy_native_toc_receipt_remains_readable(native_word_directory):
    path, profile, project = native_word_directory
    office_layout.refresh_master_layout(path, project, profile)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    receipt = json.loads(parts[office_layout.RECEIPT_PART])
    receipt.pop('signature')
    receipt['version'] = office_layout.LEGACY_NATIVE_TOC_VERSION
    del receipt['word_native']['table_rows']
    receipt['signature'] = office_layout._mac(receipt, receipt['version'])
    parts[office_layout.RECEIPT_PART] = json.dumps(receipt).encode()
    old = office_layout.verify_layout_receipt(_archive_parts(parts), project, profile)
    assert old['version'] == 'office-layout/11'
    assert 'table_rows' not in old['word_native']


@pytest.mark.parametrize('damage', ['missing', 'reordered', 'wrong_boundary', 'invalid_type', 'outside'])
def test_independent_row_page_check_rejects_invalid_locations(table_row_layout, damage):
    from app.services.master_docx_check import _table_row_page_relation

    path, profile, project, _ = table_row_layout
    office_layout.refresh_master_layout(path, project, profile)
    receipt = office_layout.verify_layout_receipt(path.read_bytes(), project, profile)
    rows = receipt['word_native']['table_rows']['tables'][0]['rows']
    if damage == 'missing':
        rows.pop()
    elif damage == 'reordered':
        rows[0], rows[1] = rows[1], rows[0]
    elif damage == 'wrong_boundary':
        rows[0]['start_page'] = rows[0]['end_page'] = 6
    elif damage == 'invalid_type':
        rows[1]['start_page'] = True
    else:
        rows[-1]['end_page'] = receipt['page_count'] + 1
    assert _table_row_page_relation(project.sections[0].paragraphs[0], receipt)[0] is False


def test_table_row_layout_budget_covers_slow_candidate_and_final_measurements(table_row_layout, monkeypatch):
    from types import SimpleNamespace

    path, profile, project, calls = table_row_layout
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(office_layout, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(office_layout, 'remaining_export_seconds', lambda maximum: maximum)
    original_page_probe = office_layout._measure_native_layout
    original_row_probe = office_layout._measure_native_table_rows

    def delayed_page_probe(candidate, deadline, extra_bookmarks=None):
        result = original_page_probe(candidate, deadline, extra_bookmarks)
        clock.now += 70 if candidate.name == 'word-layout-candidate.docx' else 50
        return result

    def delayed_row_probe(candidate, deadline, targets):
        if deadline - clock.now < 1:
            raise ProfileError('Word 表格分页超过总时限')
        result = original_row_probe(candidate, deadline, targets)
        clock.now += 60
        return result

    monkeypatch.setattr(office_layout, '_measure_native_layout', delayed_page_probe)
    monkeypatch.setattr(office_layout, '_measure_native_table_rows', delayed_row_probe)
    office_layout.refresh_master_layout(path, project, profile)
    assert len(calls) == 2
    assert office_layout.verify_layout_receipt(path.read_bytes(), project, profile)['version'] == office_layout.VERSION


@pytest.mark.parametrize(('label_color', 'label_underline', 'page_color', 'page_underline'), [
    ('auto', 'none', 'auto', 'none'),
    ('A13652', 'double', '226688', 'single'),
])
def test_directory_links_follow_each_template_appearance(toc_package, label_color, label_underline,
                                                        page_color, page_underline):
    from app.services.toc_appearance import source_toc_appearance, toc_appearance_matches

    path, _, profile = toc_package
    source = Document(path)
    row = next(paragraph for paragraph in source.paragraphs if paragraph.style.name == 'TOC 1')
    for run, color, underline in zip(row.runs, (label_color, page_color),
                                      (label_underline, page_underline)):
        rpr = run._r.get_or_add_rPr()
        etree.SubElement(rpr, qn('w:color')).set(qn('w:val'), color)
        etree.SubElement(rpr, qn('w:u')).set(qn('w:val'), underline)
    source.save(path)
    expected = source_toc_appearance(path.read_bytes(), profile)
    profile['toc_appearance_by_level'] = expected
    assert expected['1'] == {
        'label': {'color': label_color, 'underline': label_underline},
        'leader': {'color': page_color, 'underline': page_underline},
        'page': {'color': page_color, 'underline': page_underline},
    }

    # Simulate Word adding its blue, underlined hyperlink character style.
    updated = Document(path)
    character = updated.styles.add_style('Simulated Hyperlink', WD_STYLE_TYPE.CHARACTER)
    character.font.color.rgb = RGBColor.from_string('0000FF')
    character.font.underline = True
    character.font.name = 'Tahoma'
    character.font.size = Pt(9)
    for run in next(p for p in updated.paragraphs if p.style.name == 'TOC 1').runs:
        run.style = character
        rpr = run._r.get_or_add_rPr()
        for name in ('color', 'u'):
            for item in rpr.findall(qn('w:' + name)):
                rpr.remove(item)
    updated.save(path)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    styles_before = parts['word/styles.xml']
    rows, _ = office_layout._read_toc(path, profile)
    office_layout._replace_toc_rows(parts, rows, profile)
    root = etree.fromstring(parts['word/document.xml'])
    result = root.find('.//' + qn('w:hyperlink')).getparent()
    styles = etree.fromstring(parts['word/styles.xml'])
    assert toc_appearance_matches(result, styles, expected['1'])
    assert result.xpath('./w:hyperlink/@w:anchor', namespaces=NS) == ['PGHchapter1']
    assert _valid_toc_page_reference(result, 'PGHchapter1', '8')
    assert parts['word/styles.xml'] == styles_before
    assert not result.xpath('.//w:rPr/w:rStyle', namespaces=NS)
    label = result.xpath('.//w:r[w:t[contains(., "绪论")]]', namespaces=NS)[0]
    assert label.xpath('./w:rPr/w:rFonts/@w:ascii', namespaces=NS) == ['Tahoma']
    assert label.xpath('./w:rPr/w:sz/@w:val', namespaces=NS) == ['18']


def test_directory_character_style_inheritance_and_whitespace_are_preserved(toc_package):
    from app.core.extract.effective_format import EffectiveFormatResolver
    from app.services.toc_appearance import apply_toc_appearance, toc_appearance_matches

    path, _, profile = toc_package
    doc = Document(path)
    doc.styles['TOC 1'].font.bold = True
    base = doc.styles.add_style('TOC character base', WD_STYLE_TYPE.CHARACTER)
    base.font.name = 'Tahoma'
    base.font.size = Pt(9)
    link = doc.styles.add_style('TOC generated link', WD_STYLE_TYPE.CHARACTER)
    link.base_style = base
    link.font.bold = True  # A style toggle over bold TOC 1 yields regular text.
    link.font.color.rgb = RGBColor.from_string('0000FF')
    link.font.underline = True
    row = next(p for p in doc.paragraphs if p.style.name == 'TOC 1')
    label, number = row.runs
    label.style = link
    fonts = label._r.get_or_add_rPr().find(qn('w:rFonts'))
    if fonts is None:
        fonts = etree.SubElement(label._r.get_or_add_rPr(), qn('w:rFonts'))
    fonts.set(qn('w:eastAsia'), '黑体')
    gap = row.add_run(' ')
    gap.style = link
    row._p.insert(row._p.index(number._r), gap._r)
    doc.save(path)

    rows, _ = office_layout._read_toc(path, profile)
    original = rows[0]['paragraph']
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    styles = etree.fromstring(parts['word/styles.xml'])
    resolver = EffectiveFormatResolver(styles)
    before = resolver.paragraph(original, include_whitespace=True)[2]
    original_values = next(run['properties'] for run in before if '绪论' in run['text'])

    office_layout._replace_toc_rows(parts, rows, profile)
    root = etree.fromstring(parts['word/document.xml'])
    result = root.find('.//' + qn('w:hyperlink')).getparent()
    after = resolver.paragraph(result, include_whitespace=True)[2]
    actual_values = next(run['properties'] for run in after if '绪论' in run['text'])
    for key in ('font.ascii', 'font.hAnsi', 'font.eastAsia', 'font.size', 'font.bold'):
        assert actual_values[key] == original_values[key]
    assert actual_values['font.bold'] is False
    assert actual_values['font.eastAsia'] == '黑体'
    space = next(run for run in result.findall('.//' + qn('w:r'))
                 if ''.join(run.itertext(qn('w:t'))) == ' ')
    assert space.find(qn('w:rPr') + '/' + qn('w:rStyle')) is None
    assert space.xpath('./w:rPr/w:color/@w:val', namespaces=NS) == ['auto']
    assert space.xpath('./w:rPr/w:u/@w:val', namespaces=NS) == ['none']
    assert toc_appearance_matches(result, styles, profile['toc_appearance_by_level']['1'])
    original_result = etree.tostring(result)
    apply_toc_appearance(result, profile['toc_appearance_by_level']['1'], styles)
    assert etree.tostring(result) == original_result


def test_directory_checker_rejects_character_style_even_with_direct_auto_color(toc_package):
    from app.services.toc_appearance import toc_appearance_matches

    path, parts, profile = toc_package
    rows, _ = office_layout._read_toc(path, profile)
    office_layout._replace_toc_rows(parts, rows, profile)
    root = etree.fromstring(parts['word/document.xml'])
    result = root.find('.//' + qn('w:hyperlink')).getparent()
    styles = etree.fromstring(parts['word/styles.xml'])
    expected = profile['toc_appearance_by_level']['1']
    assert toc_appearance_matches(result, styles, expected)
    rpr = result.xpath('.//w:r[w:t]/w:rPr', namespaces=NS)[0]
    reference = etree.Element(qn('w:rStyle'))
    reference.set(qn('w:val'), 'DefaultParagraphFont')
    rpr.insert(0, reference)
    assert not toc_appearance_matches(result, styles, expected)


def test_directory_direct_font_overrides_inherited_theme(toc_package):
    path, _, profile = toc_package
    doc = Document(path)
    link = doc.styles.add_style('TOC theme link', WD_STYLE_TYPE.CHARACTER)
    fonts = etree.SubElement(link.element.get_or_add_rPr(), qn('w:rFonts'))
    fonts.set(qn('w:asciiTheme'), 'minorHAnsi')
    row = next(p for p in doc.paragraphs if p.style.name == 'TOC 1')
    label = row.runs[0]
    label.style = link
    direct = etree.SubElement(label._r.get_or_add_rPr(), qn('w:rFonts'))
    direct.set(qn('w:ascii'), 'Times New Roman')
    doc.save(path)
    rows, _ = office_layout._read_toc(path, profile)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    office_layout._replace_toc_rows(parts, rows, profile)
    root = etree.fromstring(parts['word/document.xml'])
    result = root.xpath('.//w:hyperlink//w:r[w:t[contains(., "绪论")]]/w:rPr/w:rFonts', namespaces=NS)[0]
    assert result.get(qn('w:ascii')) == 'Times New Roman'
    assert result.get(qn('w:asciiTheme')) is None


@pytest.mark.parametrize(('role', 'damage', 'value'), [
    ('label', 'color', '0000FF'),
    ('label', 'u', 'single'),
    ('page', 'color', 'FF0000'),
    ('page', 'u', 'double'),
    ('leader', 'color', '0000FF'),
    ('leader', 'u', 'single'),
])
def test_directory_appearance_rejects_tampered_color_or_underline(toc_package, role, damage, value):
    from app.services.toc_appearance import toc_appearance_matches

    path, parts, profile = toc_package
    rows, _ = office_layout._read_toc(path, profile)
    office_layout._replace_toc_rows(parts, rows, profile)
    root = etree.fromstring(parts['word/document.xml'])
    result = root.find('.//' + qn('w:hyperlink')).getparent()
    styles = etree.fromstring(parts['word/styles.xml'])
    expected = profile['toc_appearance_by_level']['1']
    assert toc_appearance_matches(result, styles, expected)
    runs = [run for run in result.findall('.//' + qn('w:r'))
            if ''.join(run.itertext(qn('w:t'))).strip()]
    target = (next(run for run in result.findall('.//' + qn('w:r')) if run.find(qn('w:tab')) is not None)
              if role == 'leader' else runs[0 if role == 'label' else -1])
    target.find(qn('w:rPr') + '/' + qn('w:' + damage)).set(qn('w:val'), value)
    assert not toc_appearance_matches(result, styles, expected)


def test_directory_source_appearance_rejects_conflicting_or_unresolved_samples(toc_package):
    from app.services.toc_appearance import source_toc_appearance

    path, _, profile = toc_package
    doc = Document(path)
    row = next(paragraph for paragraph in doc.paragraphs if paragraph.style.name == 'TOC 1')
    conflicting = doc.add_paragraph(style='TOC 1')
    run = conflicting.add_run('第2章 对比')
    etree.SubElement(run._r.get_or_add_rPr(), qn('w:color')).set(qn('w:val'), 'FF0000')
    conflicting.add_run('9')
    doc.save(path)
    with pytest.raises(ProfileError, match='缺少一致样例'):
        source_toc_appearance(path.read_bytes(), profile)
    conflicting._p.getparent().remove(conflicting._p)
    color = etree.SubElement(row.runs[0]._r.get_or_add_rPr(), qn('w:color'))
    color.set(qn('w:val'), '0000FF')
    color.set(qn('w:themeColor'), 'hyperlink')
    doc.save(path)
    with pytest.raises(ProfileError, match='无法可靠解析'):
        source_toc_appearance(path.read_bytes(), profile)


def test_office_pdf_font_names_normalize_known_macos_cjk_fallbacks():
    # A substituted font must not be accepted as the required original font.
    assert _font_name('/ABCDEF+STSongti-SC-Regular') == 'stsongtisc'
    assert _font_name('STHeitiSC-Medium') == 'stheitiscmedium'
    assert _font_name('TimesNewRomanPSMT') == 'timesnewroman'
