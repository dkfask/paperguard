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
from app.services.manuscript_template import derive_manuscript_template, remap_requirement_snapshot
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
        accept_new_object_defaults=True, remove_template_instructions=True, preserve_manuscript_structure=True,
        cover_pagination={'allowed_blank_offsets': [1], 'require_boundary_new_page': True}), actor='teacher')
    derived, _ = derive_manuscript_template(template, path, imported, actor='student')
    cover = derived.source_execution['cover_pagination']
    assert cover['allowed_blank_offsets'] == [1]
    assert cover['require_boundary_new_page'] is True
    assert cover['source']['source_sha256'] == derived.source_execution['source_sha256']
    assert cover['source']['specification_sha256'] == derived.source_execution['specification_sha256']
    assert cover['boundary_bookmark'] == derived.source_execution['headings'][0]['bookmark_name']
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


@pytest.mark.parametrize(('template_alignment', 'inline_alignment'), [
    ('subscript', 'superscript'), ('superscript', 'subscript'),
])
def test_inline_vertical_alignment_overrides_template_baseline(manuscript_case, tmp_path, template_alignment, inline_alignment):
    from app.models.project import InlineTextRun

    path, _, imported, _ = manuscript_case
    document = Document(path)
    heading = next(paragraph for paragraph in document.paragraphs if paragraph.text == '第1章 模板示例')
    if template_alignment == 'subscript':
        heading.runs[0].font.subscript = True
    else:
        heading.runs[0].font.superscript = True
    document.save(path)
    template = PaperTemplate(id='vertical-alignment-layout', name='上下标版式', source_docx=path.name,
        sections=extract_section_tree(path), styles=import_template_styles(path)['styles'])
    specification = extract_writing_specification(path)
    template = compile_profile(template, path, ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256, accept_recommended=True,
        accept_new_object_defaults=True, remove_template_instructions=True,
        preserve_manuscript_structure=True), actor='teacher')
    derived, payload = derive_manuscript_template(template, path, imported, actor='student')
    source = tmp_path / derived.source_docx
    source.write_bytes(payload)
    project = WritingProject(id='vertical-alignment-project', template_id=derived.id,
        template_snapshot=derived.model_dump(mode='json'))
    _apply_imported_docx(project, imported)
    block = project.sections[0].paragraphs[0]
    block.inline_runs = [InlineTextRun(text=block.text, vert_align=inline_alignment)]
    output = tmp_path / f'{inline_alignment}.docx'

    render_master_docx(project, derived, str(output), source_path=str(source))

    report = inspect_docx_output(output.read_bytes(), project, derived, source_template_bytes=payload)
    assert report['status'] == 'pass', [(c['rule'], c['object'], c['actual']) for c in report['checks'] if c['status'] != 'pass']
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    runs = root.xpath('.//w:sdtContent//w:r[w:t]', namespaces=NS)
    target = next(run for run in runs if ''.join(run.xpath('./w:t/text()', namespaces=NS)) == block.text)
    assert target.xpath('./w:rPr/w:vertAlign/@w:val', namespaces=NS) == [inline_alignment]


def test_master_export_checker_compares_equation_omml_to_project_snapshot(manuscript_case, tmp_path):
    path, template, imported, _ = manuscript_case
    derived, payload = derive_manuscript_template(template, path, imported, actor='student')
    source = tmp_path / derived.source_docx
    source.write_bytes(payload)
    project = WritingProject(id='equation-project', template_id=derived.id,
        template_snapshot=derived.model_dump(mode='json'))
    _apply_imported_docx(project, imported)
    from app.models.project import BlockType, EquationBlock, ParagraphBlock
    project.sections[0].paragraphs = [ParagraphBlock(
        id='equation-1', type=BlockType.EQUATION,
        equation=EquationBlock(omml='<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x</m:t></m:r></m:oMath>'),
    )]
    output = tmp_path / 'equation-output.docx'

    render_master_docx(project, derived, str(output), source_path=str(source))
    report = inspect_docx_output(output.read_bytes(), project, derived, source_template_bytes=payload)
    assert report['status'] == 'pass', [c for c in report['checks'] if c['status'] != 'pass']

    damaged = tmp_path / 'equation-damaged.docx'
    with zipfile.ZipFile(output) as original, zipfile.ZipFile(damaged, 'w', zipfile.ZIP_DEFLATED) as rebuilt:
        for name in original.namelist():
            part = original.read(name)
            if name == 'word/document.xml':
                root = etree.fromstring(part)
                root.find('.//' + qn('m:t')).text = 'y'
                part = etree.tostring(root)
            rebuilt.writestr(name, part)
    damaged_report = inspect_docx_output(damaged.read_bytes(), project, derived, source_template_bytes=payload)
    assert damaged_report['status'] == 'fail'
    assert any(check['rule'] == 'master.equation_omml' and check['status'] == 'fail' for check in damaged_report['checks'])


def test_requirement_upgrade_snapshot_keeps_manuscript_chapter_targets(tmp_path):
    from app.api import projects as projects_module
    from app.models.policy import RequirementSet

    source_key = 'template-results'
    manuscript_key = 'manuscript-results'
    template = PaperTemplate(id='mapped-requirement-template', name='映射规范模板', source_execution={
        'manuscript_section_key_map': {source_key: manuscript_key},
        'manuscript_source_section_keys': [source_key],
    })
    project = WritingProject(id='mapped-requirement-project', template_id=template.id)
    expected_item = {
        'id': 'results-table', 'target': source_key, 'enforce': 'hard', 'severity': 'error',
        'params': {'section_key': source_key, 'section_keys': [source_key]},
    }
    store = Store(tmp_path / 'mapped-requirement.db')
    store.save_requirement_set(RequirementSet(
        id='mapped-policy', name='章节表格规范', status='published', items=[expected_item],
    ))

    snapshot = projects_module._current_requirement_snapshot(project, template, store)

    remapped = snapshot['resolved_items'][0]
    assert remapped['target'] == manuscript_key
    assert remapped['params']['section_key'] == manuscript_key
    assert remapped['params']['section_keys'] == [manuscript_key]
    assert snapshot['content_hash'].startswith('sha256:')


def test_reimport_rebases_previous_chapter_mapping_before_applying_new_keys():
    source_keys = ['ch1', 'ch2']
    original = {
        'resolved_items': [{
            'id': 'required-table', 'target': 'ch1', 'enforce': 'hard', 'severity': 'error',
            'params': {'section_key': 'ch1', 'section_keys': ['ch1', 'ch2']},
        }],
    }
    first = remap_requirement_snapshot(original, {'ch1': 'import-heading-2', 'ch2': 'import-heading-3'}, source_keys)

    second = remap_requirement_snapshot(first, {'ch1': 'import-heading-3', 'ch2': 'import-heading-4'}, source_keys)

    item = second['resolved_items'][0]
    assert item['target'] == 'import-heading-3'
    assert item['params']['section_key'] == 'import-heading-3'
    assert item['params']['section_keys'] == ['import-heading-3', 'import-heading-4']
    assert second['manuscript_target_mapping']['section_key_map'] == {
        'ch1': 'import-heading-3', 'ch2': 'import-heading-4',
    }


@pytest.mark.parametrize('severity', ['error', 'warning', 'info'])
def test_reimport_fails_closed_for_unmapped_hard_chapter(severity):
    original = {
        'resolved_items': [
            {'id': 'hard', 'target': 'ch1', 'enforce': 'hard', 'severity': severity},
        ],
    }
    first = remap_requirement_snapshot(original, {'ch1': 'ch2'}, ['ch1'])

    with pytest.raises(ProfileError, match='缺少对应论文标题'):
        remap_requirement_snapshot(first, {}, ['ch1'])


def test_reimport_relaxes_unmapped_soft_target_to_document():
    original = {'resolved_items': [
        {'id': 'soft', 'target': 'ch1', 'enforce': 'soft', 'severity': 'warning'},
    ]}
    first = remap_requirement_snapshot(original, {'ch1': 'ch2'}, ['ch1'])

    second = remap_requirement_snapshot(first, {}, ['ch1'])

    assert second['resolved_items'][0]['target'] == 'document'


@pytest.mark.parametrize('titles', [
    ['第1章 系统设计', '第2章 系统分析'],
    ['第1章 新增引言', '第2章 系统分析', '第3章 系统设计'],
])
def test_requirement_upgrade_maps_policies_once_before_merging_template_rules(tmp_path, monkeypatch, titles):
    from app.models.policy import RequirementSet
    from app.services.policy_resolver import build_requirement_snapshot

    monkeypatch.setenv('PAPERGUARD_TEMPLATE_DIR', str(tmp_path))
    source = tmp_path / 'upgrade-master.docx'
    document = Document()
    for title in ['第1章 系统分析', '第2章 系统设计']:
        document.add_heading(title, 1)
        document.add_paragraph('原模板正文格式样例。' * 12)
    document.save(source)
    template = PaperTemplate(id='upgrade-master', name='章节升级模板', source_docx=source.name,
        sections=extract_section_tree(source), styles=import_template_styles(source)['styles'])
    specification = extract_writing_specification(source)
    template = compile_profile(template, source, ConfirmWritingProfile(
        expected_specification_sha256=specification.specification_sha256, accept_recommended=True,
        accept_new_object_defaults=True, remove_template_instructions=True,
        preserve_manuscript_structure=True), actor='teacher')
    old_key = template.sections[0].key
    template.source_execution['content_requirements'] = [
        {'id': 'template-table', 'type': 'auto', 'target': old_key, 'checker': 'section.table_required',
         'enforce': 'hard', 'severity': 'error', 'params': {'section_key': old_key, 'min_count': 1}},
        {'id': 'template-manual', 'type': 'manual', 'target': old_key,
         'enforce': 'soft', 'severity': 'warning', 'params': {'section_key': old_key}},
    ]
    template.source_execution['confirmed_content_rule_ids'] = ['template-table']
    for item in template.source_execution['content_requirements']:
        item['source_version'] = template.version
        item['provenance'] = {'rule_id': item['id'],
            'source_sha256': template.source_execution['source_sha256'],
            'specification_sha256': specification.specification_sha256}
    template.source_execution = sign_profile(template.source_execution)
    manuscript = Document()
    for title in titles:
        manuscript.add_heading(title, 1)
        manuscript.add_paragraph('导入论文正文。')
    raw = io.BytesIO(); manuscript.save(raw)
    derived, payload = derive_manuscript_template(template, source, import_docx_project(raw.getvalue()), actor='teacher')
    (tmp_path / derived.source_docx).write_bytes(payload)
    expected_key = derived.source_execution['manuscript_section_key_map'][old_key]
    assert expected_key != old_key
    store = Store(tmp_path / 'upgrade.db')
    store.save_requirement_set(RequirementSet(
        id='upgrade-policy', name='学校表格规范', status='published', items=[{
            'id': 'policy-table', 'target': old_key, 'checker': 'section.table_required',
            'enforce': 'hard', 'severity': 'error', 'params': {'section_key': old_key, 'min_count': 2},
        }],
    ))
    snapshot = build_requirement_snapshot(store, derived)

    def assert_targets(frozen):
        assert len(frozen['resolved_items']) == 2
        for item in frozen['resolved_items'] + frozen['template_instruction_items']:
            assert item['target'] == expected_key
            assert item['params']['section_key'] == expected_key
        merged = next(item for item in frozen['resolved_items'] if item.get('checker'))
        assert merged['params']['min_count'] == 2
        assert len(merged['sources']) == 2
        assert frozen['manuscript_target_mapping']['section_key_map'][old_key] == expected_key

    assert_targets(snapshot)
    actor = {'id': 'upgrade-owner', 'role': 'teacher', 'status': 'active'}
    project = WritingProject(id='upgrade-project', owner_id=actor['id'], template_id=derived.id,
        template_snapshot=derived.model_dump(mode='json'), template_source_snapshot=derived.model_dump(mode='json'),
        requirement_snapshot=snapshot)
    store.save_project(project)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: actor
    try:
        client = TestClient(app)
        preview = client.get(f'/api/projects/{project.id}/requirements/upgrade-preview')
        assert preview.status_code == 200, preview.text
        assert_targets(preview.json()['snapshot'])
        upgraded = client.post(f'/api/projects/{project.id}/requirements/upgrade', json={
            'expected_snapshot_hash': snapshot['content_hash'], 'confirm': True,
        })
        assert upgraded.status_code == 200, upgraded.text
        assert_targets(upgraded.json()['project']['requirement_snapshot'])
        assert_targets(store.get_project(project.id).requirement_snapshot)
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(previous)


def test_mainline_import_preserves_requirement_targets_and_reimport(manuscript_case, tmp_path, monkeypatch):
    from app.core import feature_flags

    monkeypatch.setattr(feature_flags, 'manuscript_import_enabled', lambda: True)
    path, template, _, _ = manuscript_case
    monkeypatch.setenv('PAPERGUARD_TEMPLATE_DIR', str(tmp_path))
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'data'))
    store = Store(tmp_path / 'import-api.db')
    actor = {'id': 'import-owner', 'role': 'student', 'username': 'import-owner'}
    old_heading_key = next(
        heading['key'] for heading in template.source_execution['headings']
        if heading['kind'] == 'heading1' and heading['title'] == '第1章 模板示例'
    )
    project = WritingProject(id='import-api-project', owner_id=actor['id'], template_id=template.id,
        template_snapshot=template.model_dump(mode='json'), template_source_snapshot=template.model_dump(mode='json'),
        requirement_snapshot={
            'content_hash': 'sha256:before-import',
            'format_overrides': {'body': {'size_pt': 14.0}},
            'resolved_items': [{
                'id': 'table-required', 'target': old_heading_key, 'enforce': 'hard', 'severity': 'error',
                'params': {'section_key': old_heading_key, 'section_keys': [old_heading_key]},
            }],
        })
    store.save_project(project)
    manuscript = Document()
    manuscript.add_heading('第1章 新增分析', 1)
    manuscript.add_paragraph('新增章节正文。')
    manuscript.add_heading('第2章 模板示例', 1)
    manuscript.add_heading('2.1 模板二级示例', 2)
    manuscript.add_paragraph('系统功能正文。')
    manuscript.add_heading('2.1.1 模板三级示例', 3)
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
        response = client.post(base, params={
            'apply': True, 'base_revision': store.get_project(project.id).revision,
            'candidate_id': preview.json()['candidate_id'],
            'candidate_sha256': preview.json()['candidate_sha256'],
        })
        assert response.status_code == 200, response.text
        saved = store.get_project(project.id)
        assert saved.layout_template_snapshot['source_docx'] == path.name
        assert saved.template_snapshot['source_docx'] != path.name
        assert [(s.type, s.title) for s in saved.sections if s.type.startswith('heading')] == [
            ('heading1', '第1章 新增分析'), ('heading1', '第2章 模板示例'),
            ('heading2', '2.1 模板二级示例'), ('heading3', '2.1.1 模板三级示例')]
        section_map = saved.template_snapshot['source_execution']['manuscript_section_key_map']
        mapped_key = section_map[old_heading_key]
        assert mapped_key != old_heading_key
        frozen_requirement = saved.requirement_snapshot['resolved_items'][0]
        assert frozen_requirement['target'] == mapped_key
        assert frozen_requirement['params']['section_key'] == mapped_key
        assert frozen_requirement['params']['section_keys'] == [mapped_key]
        assert saved.requirement_snapshot['content_hash'] != 'sha256:before-import'
        assert saved.template_source_snapshot['styles']['body']['size_pt'] != 14.0
        assert saved.template_snapshot['styles']['body']['size_pt'] == 14.0
        assert response.json()['project']['template_snapshot']['styles']['body']['size_pt'] == 14.0

        replacement = Document()
        replacement.add_heading('第1章 新增引言', 1)
        replacement.add_paragraph('引言正文。')
        replacement.add_heading('第2章 新增分析', 1)
        replacement.add_heading('第3章 模板示例', 1)
        replacement.add_heading('3.1 新的二级标题', 2)
        replacement.add_paragraph('替换论文正文。')
        replacement_raw = io.BytesIO(); replacement.save(replacement_raw)
        repreview = client.post(base, files={'file': ('replacement.docx', replacement_raw.getvalue())})
        assert repreview.status_code == 200, repreview.text
        reimported = client.post(base, params={
            'apply': True, 'base_revision': saved.revision,
            'candidate_id': repreview.json()['candidate_id'],
            'candidate_sha256': repreview.json()['candidate_sha256'],
        })
        assert reimported.status_code == 200, reimported.text
        saved = store.get_project(project.id)
        new_map = saved.template_snapshot['source_execution']['manuscript_section_key_map']
        new_mapped_key = new_map[old_heading_key]
        assert new_mapped_key != mapped_key
        reimported_requirement = saved.requirement_snapshot['resolved_items'][0]
        assert reimported_requirement['target'] == new_mapped_key
        assert reimported_requirement['params']['section_key'] == new_mapped_key
        assert reimported_requirement['params']['section_keys'] == [new_mapped_key]

        before = set(tmp_path.glob('tplsrc-manuscript-*.docx'))
        conflict_preview = client.post(base, files={'file': ('paper.docx', raw.getvalue())})
        assert conflict_preview.status_code == 200, conflict_preview.text
        monkeypatch.setattr(store, 'update_project_if_revision_conn', lambda *_: None)
        conflict = client.post(base, params={
            'apply': True, 'base_revision': saved.revision,
            'candidate_id': conflict_preview.json()['candidate_id'],
            'candidate_sha256': conflict_preview.json()['candidate_sha256'],
        })
        assert conflict.status_code == 409, conflict.text
        assert set(tmp_path.glob('tplsrc-manuscript-*.docx')) == before
        assert store.get_project(project.id).template_snapshot['source_docx'] == saved.template_snapshot['source_docx']
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(previous)
