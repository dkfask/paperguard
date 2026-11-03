"""Project checks apply only selected packs while preserving frozen legacy packs."""

from app.models.policy import RequirementItem, RequirementSet, RequirementStatus
from app.models.project import ProjectSection, WritingProject
from app.models.template import PaperTemplate, TextStyle
from app.services.policy_resolver import build_requirement_snapshot
from app.services.project_check import check_project


PACK = "GB-T-7713-1-2006"


class PolicyStore:
    def __init__(self, policies=()):
        self.policies = {policy.id: policy for policy in policies}

    def list_requirement_sets(self):
        return list(self.policies.values())

    def get_requirement_set(self, policy_id):
        return self.policies.get(policy_id)


def template_with_single_spaced_heading(**kwargs):
    return PaperTemplate(
        id="single-spaced", name="单倍行距模板",
        styles={"heading1": TextStyle(line_spacing=1.0), "body": TextStyle()},
        **kwargs,
    )


def project_with_snapshot(snapshot):
    return WritingProject(
        id="check-pack", template_id="single-spaced", title="规则适用范围",
        requirement_snapshot=snapshot,
        sections=[ProjectSection(key="chapter", type="heading1", title="第一章 绪论")],
    )


def heading_spacing_findings(project, template, **kwargs):
    report = check_project(project, template, **kwargs)
    return [item for item in report["findings"] if item["rule_id"] == "PG-H1-LINE-SPACING"]


def test_new_snapshot_does_not_add_unselected_pack_and_freezes_empty_selection():
    template = template_with_single_spaced_heading(source_execution={"profile_sha256": "bound-master"})
    snapshot = build_requirement_snapshot(PolicyStore(), template)
    assert snapshot["rule_packs"] == []
    assert snapshot["rule_pack_selection_version"] == 1
    project = project_with_snapshot(snapshot)
    assert heading_spacing_findings(project, template) == []

    # A later template change must not alter an existing project's snapshot.
    template.rule_packs = [PACK]
    assert heading_spacing_findings(project, template) == []
    # An explicit one-off check can still select a pack.
    assert len(heading_spacing_findings(project, template, pack=PACK)) == 1


def test_explicit_template_pack_and_frozen_snapshot_are_enforced():
    template = template_with_single_spaced_heading(rule_packs=[PACK])
    snapshot = build_requirement_snapshot(PolicyStore(), template)
    assert snapshot["rule_packs"] == [PACK]
    project = project_with_snapshot(snapshot)
    template.rule_packs = []
    assert len(heading_spacing_findings(project, template)) == 1


def test_explicit_policy_pack_is_applied_with_empty_template_selection():
    policy = RequirementSet(
        id="policy-pack", name="规范规则包", status=RequirementStatus.PUBLISHED,
        items=[RequirementItem(id="pack-ref", rule_pack_id=PACK)],
    )
    template = template_with_single_spaced_heading(
        source_execution={"profile_sha256": "bound-master"}, requirement_set_ids=[policy.id],
    )
    snapshot = build_requirement_snapshot(PolicyStore([policy]), template)
    assert snapshot["rule_packs"] == []
    assert snapshot["rule_pack_refs"] == [PACK]
    assert len(heading_spacing_findings(project_with_snapshot(snapshot), template)) == 1


def test_new_non_source_template_keeps_default_rule_pack():
    template = template_with_single_spaced_heading()
    snapshot = build_requirement_snapshot(PolicyStore(), template)
    assert snapshot["rule_packs"] == [PACK]
    assert len(heading_spacing_findings(project_with_snapshot(snapshot), template)) == 1


def test_legacy_project_without_pack_snapshot_keeps_previous_fallback():
    template = template_with_single_spaced_heading()
    assert len(heading_spacing_findings(project_with_snapshot({}), template)) == 1
    assert len(heading_spacing_findings(project_with_snapshot({"rule_packs": []}), template)) == 1


def test_stage_rules_add_to_project_selection_and_keep_policy_overrides():
    from app.api.workflow import _project_for_stage_check

    template = template_with_single_spaced_heading(source_execution={"profile_sha256": "bound-master"})
    project = project_with_snapshot(build_requirement_snapshot(PolicyStore(), template))
    config = {
        "id": "stage-v1", "rule_packs": ["SIM-UNIV-generic"],
        "requirement_snapshot": {
            "requirement_set_ids": ["stage-policy"], "policy_sets": [],
            "resolved_items": [{
                "id": "stage-pack", "type": "auto", "rule_pack_id": PACK,
                "rule_pack_params": {"PG-H1-LINE-SPACING": {
                    "when": {"!=": [{"var": "line_spacing"}, 1.0]},
                }},
            }],
        },
    }
    checked = _project_for_stage_check(project, config)
    report = check_project(checked, template)
    assert {source["id"] for source in report["sources"] if source["source_type"] == "rule_pack"} == {
        PACK, "SIM-UNIV-generic",
    }
    assert not any(item["rule_id"] == "PG-H1-LINE-SPACING" for item in report["findings"])
    assert project.requirement_snapshot["rule_pack_refs"] == []
    assert project.requirement_snapshot["rule_pack_overrides"] == {}


def test_stage_pack_is_applied_even_without_stage_policy_sets():
    from app.api.workflow import _project_for_stage_check

    template = template_with_single_spaced_heading(source_execution={"profile_sha256": "bound-master"})
    project = project_with_snapshot(build_requirement_snapshot(PolicyStore(), template))
    checked = _project_for_stage_check(project, {"rule_packs": [PACK]})
    assert len(heading_spacing_findings(checked, template)) == 1
    assert project.requirement_snapshot["rule_pack_refs"] == []
