"""Generated content must not silently change the template's text color."""
from __future__ import annotations

import pytest
from lxml import etree

from app.core.extract.xml_utils import NS, qn
from tests.test_master_content_visibility import _add_objects, _target
from tests.test_source_template_profile import inspect, master, rendered, rewrite


@pytest.mark.parametrize('target', ['body', 'field', 'figure_caption', 'table_cell', 'table_caption'])
def test_unrequested_white_text_is_detected_when_template_has_no_color_rule(master, tmp_path, target):
    _add_objects(master)
    data = rendered(master, tmp_path)
    assert inspect(master, data)['status'] == 'pass'

    def alter(root):
        run, _ = _target(root, master, target)
        rpr = run.find(qn('w:rPr'))
        assert rpr is not None
        colors = rpr.findall(qn('w:color'))
        color = colors[0] if colors else etree.SubElement(rpr, qn('w:color'))
        color.set(qn('w:val'), 'FFFFFF')

    report = inspect(master, rewrite(data, 'word/document.xml', alter))
    assert any(item['rule'] == 'master.effective_format' and item['status'] == 'fail'
               for item in report['checks'])
    assert report['status'] == 'fail'


def test_explicit_template_color_is_preserved_and_checked(master, tmp_path):
    from docx import Document
    from docx.shared import RGBColor
    from tests.test_master_cover_label_visibility import _recompile

    source = master[0]
    doc = Document(source)
    doc.styles['Normal'].font.color.rgb = RGBColor(0, 34, 136)
    doc.save(source)
    case = _recompile(master)
    data = rendered(case, tmp_path)
    report = inspect(case, data)
    assert report['status'] == 'pass', [item for item in report['checks'] if item['status'] != 'pass']

    def alter(root):
        run, _ = _target(root, case, 'body')
        color = run.find(f"{qn('w:rPr')}/{qn('w:color')}")
        assert color is not None
        color.set(qn('w:val'), 'FFFFFF')

    report = inspect(case, rewrite(data, 'word/document.xml', alter))
    assert report['status'] == 'fail'
    assert any(item['rule'] == 'master.effective_format' and item['status'] == 'fail'
               for item in report['checks'])
    assert any(item['rule'] == 'master.text_color_visibility' and item['status'] == 'fail'
               for item in report['checks'])


@pytest.mark.parametrize(('alteration', 'expected_status'), [
    ('automatic', 'fail'),
    ('theme_override', 'fail'),
    ('duplicate', 'fail'),
])
def test_visible_color_mismatch_remains_a_hard_format_block(
        master, tmp_path, alteration, expected_status):
    from docx import Document
    from docx.shared import RGBColor
    from tests.test_master_cover_label_visibility import _recompile

    source = master[0]
    doc = Document(source)
    doc.styles['Normal'].font.color.rgb = RGBColor(0, 34, 136)
    doc.save(source)
    case = _recompile(master)
    data = rendered(case, tmp_path)
    assert inspect(case, data)['status'] == 'pass'

    def alter(root):
        run, _ = _target(root, case, 'body')
        rpr = run.find(qn('w:rPr'))
        color = rpr.find(qn('w:color'))
        assert color is not None
        if alteration == 'automatic':
            color.set(qn('w:val'), 'auto')
        elif alteration == 'theme_override':
            color.set(qn('w:themeColor'), 'background1')
        else:
            etree.SubElement(rpr, qn('w:color')).set(qn('w:val'), 'FFFFFF')

    report = inspect(case, rewrite(data, 'word/document.xml', alter))
    assert report['status'] == expected_status
    assert any(item['rule'] == 'master.effective_format' and item['status'] == 'fail'
               for item in report['checks'])
    visibility = next(item for item in report['checks']
                      if item['rule'] == 'master.text_color_visibility'
                      and item['object'] == case[2].sections[-1].paragraphs[0].id)
    assert visibility['status'] == ('pass' if alteration == 'automatic' else 'unverifiable')
    assert report['hard_blockers']


def test_equivalent_direct_automatic_color_is_allowed_when_template_has_no_color_rule(master, tmp_path):
    data = rendered(master, tmp_path)
    assert inspect(master, data)['status'] == 'pass'

    def alter(root):
        run, _ = _target(root, master, 'body')
        rpr = run.find(qn('w:rPr'))
        assert not rpr.findall(qn('w:color'))
        etree.SubElement(rpr, qn('w:color')).set(qn('w:val'), 'auto')

    report = inspect(master, rewrite(data, 'word/document.xml', alter))
    assert report['status'] == 'pass', [item for item in report['checks'] if item['status'] != 'pass']


def test_white_text_on_black_highlight_is_visible_on_white_page(master, tmp_path):
    from app.models.project import InlineTextRun

    block = master[2].sections[-1].paragraphs[0]
    block.text = "高亮文字"
    block.inline_runs = [InlineTextRun(text="高亮文字", highlight="black")]
    data = rendered(master, tmp_path)

    def alter(root):
        run, _ = _target(root, master, "body")
        rpr = run.find(qn('w:rPr'))
        color = rpr.find(qn('w:color'))
        if color is None:
            color = etree.SubElement(rpr, qn('w:color'))
        color.set(qn('w:val'), 'FFFFFF')

    report = inspect(master, rewrite(data, 'word/document.xml', alter))
    visibility = next(item for item in report['checks']
                      if item['rule'] == 'master.text_color_visibility'
                      and item['object'] == block.id)
    assert visibility['status'] == 'pass'
    contrast = next(item for item in report['checks']
                    if item['rule'] == 'master.highlight_color_distinct'
                    and item['object'] == block.id)
    assert contrast['status'] == 'pass'
    assert report['status'] == 'fail'  # The direct color still violates the template rule.


def test_white_text_without_highlight_remains_blocked_on_white_page(master, tmp_path):
    case = master
    data = rendered(case, tmp_path)

    def alter(root):
        run, _ = _target(root, case, 'body')
        rpr = run.find(qn('w:rPr'))
        color = rpr.find(qn('w:color'))
        if color is None:
            color = etree.SubElement(rpr, qn('w:color'))
        color.set(qn('w:val'), 'FFFFFF')

    report = inspect(case, rewrite(data, 'word/document.xml', alter))
    visibility = next(item for item in report['checks']
                      if item['rule'] == 'master.text_color_visibility'
                      and item['object'] == case[2].sections[-1].paragraphs[0].id)
    assert visibility['status'] == 'fail'
    assert report['status'] == 'fail'
    assert any(item.startswith('master.text_color_visibility:') for item in report['hard_blockers'])


@pytest.mark.parametrize('white_text', [False, True])
def test_old_frozen_candidate_and_archive_are_rechecked_for_text_color(master, tmp_path, monkeypatch, white_text):
    """Previously passing reports cannot bypass the current color check."""
    import copy
    import hashlib
    import json
    import zipfile

    from app.api import workflow
    from app.db import Store
    from app.services.docx_output_check import DocxOutputCheckError, output_check_identity, verify_report_binding
    from app.services.project_versions import project_attachment_refs, project_content_hash

    source, template, project, _ = master
    project.template_snapshot = template.model_dump(mode='json')
    data = rendered(master, tmp_path)
    if white_text:
        def alter(root):
            run, _ = _target(root, master, 'body')
            rpr = run.find(qn('w:rPr'))
            color = rpr.find(qn('w:color'))
            if color is None:
                color = etree.SubElement(rpr, qn('w:color'))
            color.set(qn('w:val'), 'FFFFFF')
        data = rewrite(data, 'word/document.xml', alter)
    report = inspect(master, data)
    assert report['status'] == ('fail' if white_text else 'pass')

    # Model v9, which predates the separate hard visibility check and score contract.
    old_report = copy.deepcopy(report)
    old_report['checks'] = [check for check in old_report['checks']
                            if check['rule'] != 'master.text_color_visibility']
    for check in old_report['checks']:
        if check['status'] != 'pass':
            assert check['rule'] == 'master.effective_format'
            check['status'] = 'pass'
    old_report['status'] = 'pass'
    old_report['check_count'] = old_report['passed_count'] = len(old_report['checks'])
    old_report['inspector_version'] = 'docx-output-check/9-master'
    old_report.pop('text_color_visibility_check_version', None)
    for key in ('acceptance_contract', 'format_accuracy', 'rule_accuracy', 'failed_checks',
                'unverifiable_checks', 'excluded_checks', 'hard_blockers', 'acceptance_status'):
        old_report.pop(key, None)
    for check in old_report['checks']:
        check.pop('check_id', None)
        check.pop('classification', None)
    assert not verify_report_binding(old_report, data)
    assert verify_report_binding(old_report, data, allow_previous_inspector=True)

    artifact = tmp_path / 'old-color.docx'
    artifact.write_bytes(data)
    monkeypatch.setattr(workflow, '_candidate_artifact_path', lambda _candidate_id: artifact)
    monkeypatch.setattr(workflow, 'resolve_template_source', lambda _name: source)
    version = {'id': 'old-color-version', 'project_id': project.id,
               'content_hash': project_content_hash(project),
               'content_snapshot': project.model_dump(mode='json'),
               'attachment_refs': project_attachment_refs(project)}
    candidate = {'id': 'old-color-candidate', 'project_id': project.id,
                 'project_version_id': version['id'], 'content_hash': version['content_hash'],
                 'artifact': {'docx_sha256': hashlib.sha256(data).hexdigest(), 'output_check': old_report}}
    if white_text:
        with pytest.raises(DocxOutputCheckError):
            workflow._verified_candidate_artifact(None, candidate, version)
    else:
        checked, _ = workflow._verified_candidate_artifact(None, candidate, version)
        assert checked == data

    store = Store(tmp_path / 'old-color.db')
    submission = {'id': 'old-color-submission', 'project_version_id': version['id'],
                  'candidate_id': candidate['id']}
    for kind, record in (('project_version', version), ('candidate', candidate), ('submission', submission)):
        assert store.save_workflow_record(kind, record)
    manifest = {'archive_id': 'old-color-archive', 'submission_id': submission['id'],
                'output_check': old_report, 'output_check_identity': output_check_identity(old_report),
                'files': [{'name': 'final.docx', 'sha256': hashlib.sha256(data).hexdigest(),
                           'size_bytes': len(data)}]}
    archive_path = tmp_path / 'old-color-archive.zip'
    with zipfile.ZipFile(archive_path, 'w') as bundle:
        bundle.writestr('final.docx', data)
        bundle.writestr('manifest.json', json.dumps(manifest))
    archive = {'id': manifest['archive_id'], 'status': 'succeeded', 'submission_id': submission['id'],
               'file_path': str(archive_path), 'manifest_hash': workflow._hash(manifest),
               'source_template_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    monkeypatch.setattr(workflow, '_archive_dir', lambda: tmp_path)
    if white_text:
        with pytest.raises(DocxOutputCheckError):
            workflow._verified_archive_output(store, archive['id'], archive)
    else:
        _, checked, _ = workflow._verified_archive_output(store, archive['id'], archive)
        assert checked == data
