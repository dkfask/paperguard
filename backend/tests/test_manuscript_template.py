import copy
import io
import zipfile

import pytest
from docx import Document
from docx.shared import Pt
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from lxml import etree
from fastapi.testclient import TestClient
from app.auth import current_user
from app.api.templates import get_store
from app.db import Store
from app.main import app

from app.api.projects import _apply_imported_docx
from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS, qn
from app.models.project import WritingProject
from app.models.template import PaperTemplate
from app.services.docx_output_check import inspect_docx_output
from app.services.docx_project_import import import_docx_project
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.manuscript_template import derive_manuscript_template
from app.services.master_docx_renderer import render_master_docx
from app.services.source_template_profile import ConfirmWritingProfile, ProfileError, compile_profile, flatten, sign_profile
from app.services.project_versions import project_content_hash
from app.services.office_layout import _read_toc


@pytest.fixture
def manuscript_case(tmp_path):
    doc = Document()
    doc.styles['Normal'].font.size = Pt(12)
    doc.sections[0].header.paragraphs[0].text = '模板固定页眉'
    for title, level in [('第1章 模板示例', 1), ('1.1 模板二级示例', 2), ('1.1.1 模板三级示例', 3), ('参考文献', 1)]:
        doc.add_heading(title, level)
        doc.add_paragraph('用于提取正文段落的格式样本。' * 12)
    path = tmp_path / 'layout.docx'
    doc.save(path)
    template = PaperTemplate(id='layout-example', name='版式模板', source_docx=path.name,
        sections=extract_section_tree(path), styles=import_template_styles(path)['styles'])
    spec = extract_writing_specification(path)
    template = compile_profile(template, path, ConfirmWritingProfile(
        expected_specification_sha256=spec.specification_sha256, accept_recommended=True,
        accept_new_object_defaults=True, remove_template_instructions=True,
        preserve_manuscript_structure=True), actor='teacher')
    manuscript = Document()
    headings = [('第1章 系统分析', 1), ('1.1 系统功能分析', 2), ('1.1.1 数据采集功能分析', 3),
                ('1.1.2 数据查询功能分析', 3), ('1.2 系统流程分析', 2), ('1.2.1 系统流程图', 3), ('参考文献', 1)]
    for index, (title, level) in enumerate(headings):
        manuscript.add_heading(title, level)
        manuscript.add_paragraph(f'原论文内容-{index}。')
    raw = io.BytesIO(); manuscript.save(raw)
    return path, template, import_docx_project(raw.getvalue()), headings


def test_complete_source_tree_keeps_parents_content_order_and_layout_package(manuscript_case, tmp_path):
    path, template, imported, headings = manuscript_case
    original = path.read_bytes()
    original_snapshot = template.model_dump(mode='json')
    derived, payload = derive_manuscript_template(template, path, imported, actor='student')
    source = tmp_path / derived.source_docx; source.write_bytes(payload)
    assert path.read_bytes() == original
    assert template.model_dump(mode='json') == original_snapshot
    assert [(n.title, n.type.value) for n in flatten(derived.sections)] == [(t, 'references' if t == '参考文献' else f'heading{l}') for t, l in headings]
    chapter = derived.sections[0]
    assert [n.type.value for n in chapter.children] == ['heading2', 'heading2']
    assert [n.title for n in chapter.children[0].children] == ['1.1.1 数据采集功能分析', '1.1.2 数据查询功能分析']
    with zipfile.ZipFile(path) as before, zipfile.ZipFile(io.BytesIO(payload)) as after:
        assert set(before.namelist()) == set(after.namelist())
        assert all(before.read(n) == after.read(n) for n in before.namelist() if n != 'word/document.xml')
    project = WritingProject(id='manuscript-project', template_id=template.id, template_snapshot=derived.model_dump(mode='json'))
    merged = _apply_imported_docx(project, copy.deepcopy(imported))
    assert merged['flattened_section_count'] == 0
    assert [s.title for s in project.sections] == [t for t, _ in headings]
    assert [s.paragraphs[0].text for s in project.sections] == [f'原论文内容-{i}。' for i in range(len(headings))]
    output = tmp_path / 'output.docx'
    render_master_docx(project, derived, str(output), source_path=str(source))
    report = inspect_docx_output(output.read_bytes(), project, derived, source_template_bytes=payload)
    assert report['status'] == 'pass', [(c['rule'], c['object']) for c in report['checks'] if c['status'] != 'pass']
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    direct = root.xpath('./w:body/w:p//w:t/text()', namespaces=NS)
    assert [t for t in direct if t in {h[0] for h in headings}] == [t for t, _ in headings]
    assert '模板三级示例' not in ''.join(root.xpath('.//w:t/text()', namespaces=NS))


def test_manuscript_tree_requires_teacher_policy_and_valid_hierarchy(manuscript_case):
    path, template, imported, _ = manuscript_case
    profile = dict(template.source_execution); profile['preserve_manuscript_structure'] = False
    blocked = template.model_copy(deep=True); blocked.source_execution = sign_profile(profile)
    with pytest.raises(ProfileError, match='未确认'):
        derive_manuscript_template(blocked, path, imported, actor='student')
    invalid = copy.deepcopy(imported); invalid['sections'][1]['type'] = 'heading3'
    with pytest.raises(ProfileError, match='跳级'):
        derive_manuscript_template(template, path, invalid, actor='student')


def test_every_imported_heading_is_a_required_directory_target(manuscript_case, tmp_path):
    path, template, imported, _ = manuscript_case
    doc = Document(path)
    for index, (title, level) in enumerate([('第1章 模板示例', 1), ('1.1 模板二级示例', 2), ('1.1.1 模板三级示例', 3), ('参考文献', 1)]):
        name = f'TOC {level}'
        if name not in doc.styles: doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        paragraph = doc.add_paragraph(style=name)
        paragraph.add_run(title); paragraph.add_run('\t'); paragraph.add_run('1')
        if index == 0:
            field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'TOC \\o "1-3" \\h \\z \\u')
            paragraph._p.append(field)
        doc._element.body.insert(index, paragraph._p)
    doc.save(path)
    template.sections = [type(template.sections[0]).model_validate(n) for n in extract_section_tree(path)]
    specification = extract_writing_specification(path)
    template = compile_profile(template, path, ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256, accept_recommended=True,
        accept_new_object_defaults=True, remove_template_instructions=True, preserve_manuscript_structure=True), actor='teacher')
    derived, _ = derive_manuscript_template(template, path, imported, actor='student')
    entries = derived.source_execution['toc_entries']
    assert [e['target_title'] for e in entries] == [s['title'] for s in imported['sections']]
    assert [e['level'] for e in entries] == [1, 2, 3, 3, 2, 3, 1]
    incomplete = Document()
    for name in ('TOC 1', 'TOC 2', 'TOC 3'): incomplete.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    for entry in entries[:-1]:
        p = incomplete.add_paragraph(style=f"TOC {entry['level']}")
        p.add_run(entry['target_title']); p.add_run('\t'); p.add_run('1')
    missing_path = tmp_path / 'missing-directory-target.docx'; incomplete.save(missing_path)
    with pytest.raises(ProfileError, match='缺少已确认章节'):
        _read_toc(missing_path, derived.source_execution)


def test_source_order_changes_are_rejected_and_legacy_hashes_stay_stable(manuscript_case, tmp_path):
    path, template, imported, _ = manuscript_case
    derived, payload = derive_manuscript_template(template, path, imported, actor='student')
    source = tmp_path / derived.source_docx; source.write_bytes(payload)
    project = WritingProject(id='source-order', template_id=template.id, template_snapshot=derived.model_dump(mode='json'))
    _apply_imported_docx(project, imported)
    legacy = project.model_dump(mode='json'); legacy.pop('layout_template_snapshot')
    assert project_content_hash(project) == project_content_hash(legacy)
    project.sections[1], project.sections[2] = project.sections[2], project.sections[1]
    with pytest.raises(ProfileError, match='章节顺序'):
        render_master_docx(project, derived, str(tmp_path / 'bad-order.docx'), source_path=str(source))


def test_import_api_preserves_the_tree_and_cleans_only_failed_instance_sources(manuscript_case, tmp_path, monkeypatch):
    from app.core import feature_flags

    monkeypatch.setattr(feature_flags, 'manuscript_import_enabled', lambda: True)
    path, template, _, _ = manuscript_case
    monkeypatch.setenv('PAPERGUARD_TEMPLATE_DIR', str(tmp_path))
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'data'))
    store = Store(tmp_path / 'import-api.db')
    actor = {'id': 'import-owner', 'role': 'student', 'username': 'import-owner'}
    project = WritingProject(id='import-api-project', owner_id=actor['id'], template_id=template.id,
        template_snapshot=template.model_dump(mode='json'), template_source_snapshot=template.model_dump(mode='json'))
    store.save_project(project)
    manuscript = Document()
    manuscript.add_heading('第1章 系统分析', 1)
    manuscript.add_heading('1.1 系统功能分析', 2)
    manuscript.add_paragraph('系统功能正文。')
    manuscript.add_heading('1.1.1 数据采集功能分析', 3)
    manuscript.add_paragraph('三级节正文。')
    raw = io.BytesIO(); manuscript.save(raw)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: actor
    try:
        client = TestClient(app)
        base = '/api/projects/import-api-project/import-docx'
        preview = client.post(base, files={'file': ('paper.docx', raw.getvalue())})
        assert preview.status_code == 200, preview.text
        assert preview.json()['structure_mode'] == 'manuscript'
        assert not list(tmp_path.glob('tplsrc-manuscript-*.docx'))
        response = client.post(base, params={'apply': True, 'base_revision': store.get_project(project.id).revision,
            'candidate_id': preview.json()['candidate_id'], 'candidate_sha256': preview.json()['candidate_sha256']})
        assert response.status_code == 200, response.text
        saved = store.get_project(project.id)
        assert saved.layout_template_snapshot['source_docx'] == path.name
        assert saved.template_snapshot['source_docx'] != path.name
        assert [(s.type, s.title) for s in saved.sections if s.type.startswith('heading')] == [
            ('heading1', '第1章 系统分析'), ('heading2', '1.1 系统功能分析'), ('heading3', '1.1.1 数据采集功能分析')]
        before = set(tmp_path.glob('tplsrc-manuscript-*.docx'))
        retry = client.post(base, files={'file': ('paper.docx', raw.getvalue())})
        assert retry.status_code == 200, retry.text
        monkeypatch.setattr(store, 'update_project_if_revision_conn', lambda *_: None)
        conflict = client.post(base, params={'apply': True, 'base_revision': saved.revision,
            'candidate_id': retry.json()['candidate_id'], 'candidate_sha256': retry.json()['candidate_sha256']})
        assert conflict.status_code == 409, conflict.text
        assert set(tmp_path.glob('tplsrc-manuscript-*.docx')) == before
        assert store.get_project(project.id).template_snapshot['source_docx'] == saved.template_snapshot['source_docx']
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(previous)
