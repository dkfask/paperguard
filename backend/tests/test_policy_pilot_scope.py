"""A school-approved pilot rule must not replace the rule for other batches."""

from app.models.policy import RequirementBinding, RequirementItem, RequirementSet
from app.models.project import ParagraphBlock, ProjectSection, ReferenceEntry, ReferenceLanguageFact, WritingProject
from app.services.policy_resolver import build_requirement_snapshot
from app.services.projects import default_template
from app.services.requirement_check import check_requirements


def test_scoped_school_version_applies_only_to_its_batch_and_keeps_reference_order():
    common = {'type': 'auto', 'target': 'references', 'checker': 'references.list_facts',
              'severity': 'error', 'enforce': 'hard'}
    original = RequirementSet(id='school-v1', family_id='school-family', name='学校规范',
        level='school', status='published', version='1', updated_at='2026-01-01T00:00:00Z',
        items=[RequirementItem(id='references_format', **common,
            params={'required': True, 'numbering': 'sequential', 'bracket_style': 'square', 'language_order': 'zh_before_en'})])
    pilot = RequirementSet(id='school-pilot-v2', family_id='school-family', name='学校规范试点',
        level='school', status='published', version='2', updated_at='2026-02-01T00:00:00Z',
        bindings=[RequirementBinding(id='pilot-binding', scope_type='batch', scope_id='pilot-batch')],
        items=[RequirementItem(id='references_format', **common,
            description='参考文献著录事实和顺序编号',
            params={'required': True, 'numbering': 'sequential', 'bracket_style': 'square'}),
            RequirementItem(id='references_language_advisory', type='auto', target='references_advisory',
                checker='references.list_facts', params={'language_order': 'zh_before_en'},
                description='学校规范的中文文献优先分组仅供参考',
                severity='warning', enforce='soft')])

    class Store:
        def list_requirement_sets(self): return [original, pilot]
        def get_requirement_set(self, key): return {original.id: original, pilot.id: pilot}.get(key)

    template = default_template().model_copy(deep=True)
    template.requirement_set_ids = [original.id]
    pilot_snapshot = build_requirement_snapshot(Store(), template, organization_context={'batch_id': 'pilot-batch'})
    other_snapshot = build_requirement_snapshot(Store(), template, organization_context={'batch_id': 'other-batch'})
    unscoped_snapshot = build_requirement_snapshot(Store(), template)
    assert [p['id'] for p in pilot_snapshot['policy_sets']] == [pilot.id]
    assert [p['id'] for p in other_snapshot['policy_sets']] == [original.id]
    assert [p['id'] for p in unscoped_snapshot['policy_sets']] == [original.id]
    template.requirement_set_ids = [pilot.id]
    explicitly_bound_other = build_requirement_snapshot(Store(), template, organization_context={'batch_id': 'other-batch'})
    assert [p['id'] for p in explicitly_bound_other['policy_sets']] == [original.id]

    def language(code):
        return ReferenceLanguageFact(languages=[code], status='confirmed', confirmed_by='student', confirmed_at='2026-01-01T00:00:00Z')
    project = WritingProject(id='pilot-project', template_id=template.id, sections=[
        ProjectSection(key='references', type='references', title='参考文献', paragraphs=[
            ParagraphBlock(id='english-first', reference_number=1, reference=ReferenceEntry(
                kind='journal', authors='Smith', title='Study', source='Journal', year='2025', language_fact=language('en'))),
            ParagraphBlock(id='chinese-second', reference_number=2, reference=ReferenceEntry(
                kind='journal', authors='张三', title='研究', source='期刊', year='2025', language_fact=language('zh'))),
        ]),
    ])
    findings, unchecked = check_requirements(project, pilot_snapshot, template)
    assert not unchecked
    assert [block.id for block in project.sections[0].paragraphs] == ['english-first', 'chinese-second']
    assert any('中文在前' in finding['message'] and finding['enforce'] == 'soft' for finding in findings)
    assert not any(finding['enforce'] == 'hard' for finding in findings)
    assert any(finding['enforce'] == 'hard' for finding in check_requirements(project, other_snapshot, template)[0])
    project.sections[0].paragraphs[0].reference.language_fact = None
    findings, _ = check_requirements(project, pilot_snapshot, template)
    assert any('语种未确认' in finding['message'] and finding['enforce'] == 'soft' for finding in findings)
    assert not any(finding['enforce'] == 'hard' for finding in findings)
    project.sections[0].paragraphs[0].reference.year = ''
    findings, _ = check_requirements(project, pilot_snapshot, template)
    assert any('缺少年份' in finding['message'] and finding['enforce'] == 'hard' for finding in findings)

    pilot.bindings[0].enabled = False
    disabled_snapshot = build_requirement_snapshot(Store(), template, organization_context={'batch_id': 'pilot-batch'})
    assert [p['id'] for p in disabled_snapshot['policy_sets']] == [original.id]
