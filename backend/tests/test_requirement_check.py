"""文档级自动检查器的规则覆盖。"""

import pytest

from app.models.project import ImageBlock, ParagraphBlock, ProjectSection, ReferenceEntry, ReferenceLanguageFact, ReferenceYearFact, TableBlock, WritingProject
from app.models.template import MetaField
from app.services.project_check import check_project
from app.services.projects import default_template
from app.services.policy_parser import items_from_markdown
from app.services.requirement_check import check_requirements


def _project() -> WritingProject:
    return WritingProject(
        id="project-check",
        template_id="template-check",
        sections=[
            ProjectSection(
                key="abstract", type="abstract", title="摘要",
                paragraphs=[ParagraphBlock(id="abstract-p1", text="摘要内容")],
            ),
            ProjectSection(
                key="keywords", type="keywords", title="关键词",
                paragraphs=[ParagraphBlock(id="keywords-p1", text="关键词：系统；规范")],
            ),
            ProjectSection(
                key="references", type="references", title="参考文献",
                paragraphs=[
                    ParagraphBlock(id="ref-p1", text="作者. 研究. 2025."),
                    ParagraphBlock(id="ref-p2", text="作者. 旧研究. 2019."),
                ],
            ),
        ],
    )


def test_document_checkers_return_findings_and_manual_items():
    snapshot = {
        "resolved_items": [
            {
                "id": "abstract-min", "type": "auto", "target": "abstract",
                "checker": "abstract.word_count", "params": {"min": 10},
                "severity": "error", "enforce": "hard", "description": "摘要过短",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "keywords-min", "type": "auto", "target": "keywords",
                "checker": "keywords.count", "params": {"min": 3},
                "severity": "warning", "enforce": "soft", "description": "关键词不足",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "recent-min", "type": "auto", "target": "references",
                "checker": "references.recent_count", "params": {"years": 3, "min": 2, "current_year": 2025},
                "severity": "warning", "enforce": "soft", "description": "近年文献不足",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "manual-quality", "type": "manual", "target": "document",
                "checker": "", "params": {}, "severity": "info", "enforce": "soft",
                "description": "人工确认论证质量", "source": "教师要求", "source_version": "2",
            },
        ],
    }
    findings, not_checked = check_requirements(_project(), snapshot)
    assert {finding["requirement_item_id"] for finding in findings} == {
        "abstract-min", "keywords-min", "recent-min",
    }
    assert findings[0]["source"] == "学校规范"
    assert not_checked[0]["id"] == "manual-quality"
    assert not_checked[0]["reason"] == "manual"


def test_format_classification_does_not_skip_automatic_checker():
    from app.services.policy_resolver import hydrate_snapshot_format_overrides

    project = _project()
    snapshot = {
        "format_overrides": {"body": {"font_zh": "宋体", "size_pt": 12}},
        "resolved_items": [
            {
                "id": "mixed-rule", "type": "auto", "target": "abstract",
                "checker": "abstract.word_count", "params": {"min": 10},
                "severity": "error", "enforce": "hard",
                "description": "摘要不少于十字，字号遵循模板",
            },
            {
                "id": "future-rule", "type": "auto", "target": "document",
                "checker": "future.unsupported", "params": {},
                "severity": "error", "enforce": "hard",
                "description": "图中字样字号满足规范",
            },
            {
                "id": "format-only", "type": "manual", "target": "body",
                "checker": "", "params": {}, "severity": "warning",
                "enforce": "soft", "description": "正文小四号宋体",
            },
        ],
    }
    snapshot["policy_sets"] = [{"items": snapshot["resolved_items"]}]
    snapshot = hydrate_snapshot_format_overrides(snapshot)
    assert set(snapshot["format_item_ids"]) == {"mixed-rule", "future-rule", "format-only"}

    findings, not_checked = check_requirements(project, snapshot)

    assert [item["requirement_item_id"] for item in findings] == ["mixed-rule"]
    assert [(item["id"], item["reason"], item["enforce"]) for item in not_checked] == [
        ("mixed-rule", "format_rule_unverified", "hard"),
        ("future-rule", "unsupported_checker", "hard"),
        ("format-only", "format_rule_unverified", "soft"),
    ]

    project.requirement_snapshot = {**snapshot, "rule_pack_selection_version": 1}
    report = check_project(project, default_template())
    assert {item["requirement_item_id"] for item in report["findings"]} == {"mixed-rule"}
    assert [item["id"] for item in report["not_checked"]] == ["mixed-rule", "future-rule", "format-only"]
    assert not report["applied"]

    from app.api.workflow import _gate

    assert any(item["key"] == "automatic_unverified" for item in _gate({"check_report": report}))


@pytest.mark.parametrize("enforce", ["hard", "soft"])
def test_word_count_does_not_claim_compound_font_rule_is_verified(enforce):
    from app.api.projects import _current_export_requirement_checks
    from app.api.workflow import _gate

    project = _project()
    project.requirement_snapshot = {
        "rule_pack_selection_version": 1,
        # Legacy snapshots can lack format_item_ids entirely.
        "resolved_items": [{
            "id": "combined", "type": "auto", "target": "abstract",
            "checker": "abstract.word_count", "params": {"min": 1},
            "severity": "error", "enforce": enforce,
            "description": "摘要不少于1字，正文使用黑体",
        }],
    }
    template = default_template()
    report = check_project(project, template)
    assert not any(item.get("requirement_item_id") == "combined" for item in report["findings"])
    assert [(item["id"], item["reason"]) for item in report["not_checked"]] == [
        ("combined", "format_rule_unverified"),
    ]
    assert bool(_current_export_requirement_checks(project, template)) == (enforce == "hard")
    assert any(item["key"] == "automatic_unverified" for item in _gate({"check_report": report})) == (enforce == "hard")


def test_plain_word_count_rule_remains_verifiable():
    snapshot = {"resolved_items": [{
        "id": "count", "type": "auto", "target": "abstract",
        "checker": "abstract.word_count", "params": {"min": 1},
        "severity": "error", "enforce": "hard", "description": "摘要不少于1字",
    }]}
    findings, not_checked = check_requirements(_project(), snapshot, default_template())
    assert not findings
    assert not not_checked


@pytest.mark.parametrize("checker,target,params,description", [
    ("section.exists", "abstract", {}, "摘要必须存在，正文使用黑体"),
    ("section.table_required", "abstract", {"min": 0}, "摘要表格不少于零个，正文使用黑体"),
    ("keywords.count", "keywords", {"min": 1}, "关键词不少于1个，关键词使用黑体"),
    ("cover.field_present", "title", {}, "封面必须填写题目，题目使用黑体"),
    ("cover.fields_present", "cover", {"field_keys": ["title"]}, "封面必须填写题目，题目使用黑体"),
    ("references.list_facts", "references", {"required": False}, "文献事实完整，文献使用黑体"),
    ("references.count", "references", {"min": 1}, "文献不少于1条，文献使用黑体"),
    ("references.recent_count", "references", {"min": 0}, "近期文献不少于0条，文献使用黑体"),
    ("references.recent_ratio", "references", {"min": 0}, "近期文献比例不少于零，文献使用黑体"),
    ("references.recent_language_summary", "references", {}, "文献统计提示，文献使用黑体"),
    ("references.format", "references", {}, "文献包含序号和年份，文献使用黑体"),
    ("ai.usage_declared", "document", {}, "声明AI使用情况，正文使用黑体"),
    ("chapter.page_break", "abstract", {}, "章节另起一页，标题使用黑体"),
])
def test_auto_checker_keeps_additional_format_unverified(checker, target, params, description):
    project = _project()
    project.meta["title"] = "测试标题"
    template = default_template()
    template.meta_fields = [MetaField(key="title", label="题目")]
    rule = {"id": "mixed", "type": "auto", "checker": checker, "target": target,
            "params": params, "severity": "error", "enforce": "hard", "description": description}
    _, unchecked = check_requirements(project, {"resolved_items": [rule]}, template)
    assert any(item["id"] == "mixed" and item["reason"] == "format_rule_unverified" for item in unchecked)


def test_page_break_rule_does_not_mark_its_own_pagination_unverified():
    rule = {"id": "break", "type": "auto", "checker": "chapter.page_break", "target": "abstract",
            "params": {}, "severity": "error", "enforce": "hard", "description": "摘要另起一页"}
    _, unchecked = check_requirements(_project(), {"resolved_items": [rule], "format_item_ids": ["break"]}, default_template())
    assert not unchecked


def test_english_content_words_do_not_create_format_blocker():
    from app.services.policy_resolver import hydrate_snapshot_format_overrides

    rule = {"id": "english-count", "type": "auto", "checker": "abstract.word_count", "target": "abstract",
            "params": {"min": 1}, "severity": "error", "enforce": "hard",
            "description": "Abstract description and caption must contain at least one word"}
    snapshot = hydrate_snapshot_format_overrides({"resolved_items": [rule], "policy_sets": [{"items": [rule]}]})
    findings, unchecked = check_requirements(_project(), snapshot, default_template())
    assert not snapshot["format_item_ids"]
    assert not findings
    assert not unchecked


def test_legacy_english_unit_false_positive_is_rechecked_against_original_rule():
    rule = {"id": "legacy", "type": "auto", "checker": "abstract.word_count", "target": "abstract",
            "params": {"min": 1}, "severity": "error", "enforce": "hard",
            "description": "Abstract description and caption must contain at least one word"}
    snapshot = {"resolved_items": [rule], "policy_sets": [{"items": [rule]}], "format_item_ids": ["legacy"]}
    findings, unchecked = check_requirements(_project(), snapshot, default_template())
    assert not findings
    assert not unchecked


@pytest.mark.parametrize("source", [
    [],
    [{"items": [{"id": "legacy", "params": {}, "description": "摘要不少于1字，正文必须使用黑体"}]}],
    [{"items": [{"id": "legacy", "params": {"format_override": True},
                 "description": "Abstract description and caption must contain at least one word"}]}],
])
def test_legacy_classification_is_retained_if_original_format_cannot_be_excluded(source):
    rule = {"id": "legacy", "type": "auto", "checker": "abstract.word_count", "target": "abstract",
            "params": {"min": 1}, "severity": "error", "enforce": "hard",
            "description": "Abstract description and caption must contain at least one word"}
    snapshot = {"resolved_items": [rule], "policy_sets": source, "format_item_ids": ["legacy"]}
    _, unchecked = check_requirements(_project(), snapshot, default_template())
    assert any(item["id"] == "legacy" and item["reason"] == "format_rule_unverified" for item in unchecked)


@pytest.mark.parametrize("source_kind", ["policy", "template"])
def test_legacy_merged_auto_format_rule_is_not_silently_lost(source_kind):
    plain = {"id": "plain", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
             "params": {"min": 1}, "enforce": "hard", "severity": "error", "description": "摘要不少于1字"}
    hidden = {**plain, "id": "hidden", "description": "摘要不少于1字，正文使用黑体"}
    snapshot = {"resolved_items": [plain], "format_item_ids": ["hidden"]}
    if source_kind == "policy":
        snapshot["policy_sets"] = [{"id": "policy", "name": "学校规范", "version": "1", "items": [plain, hidden]}]
    else:
        snapshot["template_instruction_items"] = [plain, hidden]
    findings, unchecked = check_requirements(_project(), snapshot, default_template())
    assert not findings
    assert any(item["reason"] == "format_rule_unverified" and item["enforce"] == "hard"
               and item["description"] == hidden["description"] for item in unchecked)


def test_legacy_merged_pagination_only_does_not_create_format_blocker():
    plain = {"id": "plain", "type": "auto", "target": "abstract", "checker": "chapter.page_break",
             "params": {}, "enforce": "hard", "severity": "error", "description": "摘要另起一页"}
    hidden = {**plain, "id": "hidden", "description": "摘要章前分页"}
    snapshot = {"resolved_items": [plain], "policy_sets": [{"id": "policy", "items": [plain, hidden]}]}
    _, unchecked = check_requirements(_project(), snapshot, default_template())
    assert not unchecked


def test_legacy_merged_manual_format_with_duplicate_id_is_recovered():
    from app.services.policy_resolver import apply_requirement_format_overrides

    font = {"id": "format", "type": "manual", "target": "body", "checker": "", "params": {"format_override": True},
            "enforce": "hard", "severity": "error", "description": "正文中文字体为黑体"}
    border = {**font, "target": "table", "params": {"format_override": True},
              "description": "表格边框必须为黑色"}
    snapshot = {"resolved_items": [font], "format_item_ids": ["format"],
                "policy_sets": [{"name": "学校", "items": [font]}, {"name": "专业", "items": [border]}],
                "format_overrides": {"body": {"font_zh": "黑体"}}, "rule_pack_selection_version": 1}
    project = _project()
    template = apply_requirement_format_overrides(default_template(), snapshot)
    _, unchecked = check_requirements(project, snapshot, template)
    assert any(item["description"] == border["description"] and item["enforce"] == "hard"
               and item["reason"] == "format_rule_unverified" for item in unchecked)


def test_duplicate_id_with_both_descriptions_resolved_does_not_recover_extra_copy():
    font = {"id": "format", "type": "manual", "target": "body", "checker": "", "params": {"format_override": True},
            "enforce": "hard", "severity": "error", "description": "正文中文字体为黑体"}
    border = {**font, "target": "table", "description": "表格边框必须为黑色"}
    snapshot = {"resolved_items": [font, border], "format_item_ids": ["format"],
                "policy_sets": [{"name": "学校", "items": [font]}, {"name": "专业", "items": [border]}]}
    _, unchecked = check_requirements(_project(), snapshot, default_template())
    assert not any(item["id"].startswith("merged-format-") for item in unchecked)


def test_legacy_duplicate_id_and_description_with_different_target_is_recovered():
    first = {"id": "format", "type": "manual", "target": "body", "checker": "",
             "params": {"format_override": True}, "enforce": "hard", "severity": "error",
             "description": "必须使用黑体"}
    second = {**first, "target": "figure_caption"}
    snapshot = {"resolved_items": [first], "format_item_ids": ["format"],
                "policy_sets": [{"name": "学校", "items": [first]}, {"name": "专业", "items": [second]}]}
    _, unchecked = check_requirements(_project(), snapshot, default_template())
    assert any(item["id"].startswith("merged-format-") and item["source"] == "专业" for item in unchecked)


def test_legacy_auto_rule_with_lost_format_marker_is_recovered():
    resolved = {"id": "shared", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
                "params": {"min": 1}, "enforce": "hard", "severity": "error", "description": "摘要不少于1字"}
    original = {**resolved, "params": {"min": 1, "format_override": True}}
    snapshot = {"resolved_items": [resolved], "policy_sets": [{"name": "学校", "items": [original]}]}
    _, unchecked = check_requirements(_project(), snapshot, default_template())
    assert any(item["reason"] == "format_rule_unverified" and item["enforce"] == "hard" for item in unchecked)


def test_format_id_collision_does_not_turn_unrelated_auto_rule_into_format_rule():
    from app.services.policy_resolver import apply_requirement_format_overrides

    font = {"id": "shared", "type": "manual", "target": "body", "checker": "",
            "params": {"format_override": True}, "enforce": "hard", "severity": "error",
            "description": "正文中文字体为黑体"}
    count = {"id": "shared", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
             "params": {"min": 1}, "enforce": "hard", "severity": "error", "description": "摘要不少于1字"}
    snapshot = {"resolved_items": [font, count], "format_item_ids": ["shared"],
                "policy_sets": [{"items": [font]}, {"items": [count]}],
                "format_overrides": {"body": {"font_zh": "黑体"}}}
    _, unchecked = check_requirements(_project(), snapshot,
                                      apply_requirement_format_overrides(default_template(), snapshot))
    assert not unchecked
    # An explicit marker on the automatic rule remains authoritative even
    # when a different rule shares the same ID.
    count["params"]["format_override"] = True
    _, unchecked = check_requirements(_project(), snapshot,
                                      apply_requirement_format_overrides(default_template(), snapshot))
    assert any(item["description"] == count["description"] and item["reason"] == "format_rule_unverified"
               for item in unchecked)


def test_source_rule_target_mapping_does_not_create_spurious_recovery():
    original = {"id": "format", "type": "manual", "target": "ch1", "checker": "",
                "params": {"format_override": True}, "enforce": "hard", "severity": "error",
                "description": "必须使用黑体"}
    resolved = {**original, "target": "section-1"}
    snapshot = {"resolved_items": [resolved], "format_item_ids": ["format"],
                "policy_sets": [{"name": "学校", "items": [original]}],
                "manuscript_target_mapping": {"section_key_map": {"ch1": "section-1"},
                                              "source_section_keys": ["ch1"]}}
    _, unchecked = check_requirements(_project(), snapshot, default_template())
    assert not any(item["id"].startswith("merged-format-") for item in unchecked)


def test_hard_format_classification_does_not_prove_a_compound_requirement():
    from app.api.workflow import _gate
    from app.services.policy_resolver import hydrate_snapshot_format_overrides

    rule = {
        "id": "table-border", "type": "manual", "target": "document", "checker": "",
        "params": {}, "severity": "error", "enforce": "hard",
        "description": "表格边框必须为黑色，字号遵守规范",
    }
    snapshot = hydrate_snapshot_format_overrides({
        "policy_sets": [{"items": [rule]}], "resolved_items": [rule],
        "format_overrides": {"body": {"size_pt": 12}}, "rule_pack_selection_version": 1,
    })
    project = _project()
    project.requirement_snapshot = snapshot
    report = check_project(project, default_template())

    assert [(item["id"], item["reason"]) for item in report["not_checked"]] == [
        ("table-border", "format_rule_unverified"),
    ]
    assert not report["applied"]
    assert any(item["key"] == "automatic_unverified" for item in _gate({"check_report": report}))


def test_soft_unmapped_format_rule_is_unverified_without_becoming_hard():
    from app.api.workflow import _gate
    from app.services.policy_resolver import hydrate_snapshot_format_overrides

    rule = {"id": "soft-border", "type": "manual", "target": "document", "checker": "",
            "params": {}, "severity": "warning", "enforce": "soft",
            "description": "表格边框为黑色，字号遵守规范"}
    project = _project()
    project.requirement_snapshot = hydrate_snapshot_format_overrides({
        "policy_sets": [{"items": [rule]}], "resolved_items": [rule],
        "format_overrides": {"body": {"size_pt": 12}}, "rule_pack_selection_version": 1,
    })
    report = check_project(project, default_template())
    assert [(item["id"], item["reason"], item["enforce"]) for item in report["not_checked"]] == [
        ("soft-border", "format_rule_unverified", "soft"),
    ]
    assert not report["applied"]
    assert not any(item["key"] == "automatic_unverified" for item in _gate({"check_report": report}))


@pytest.mark.parametrize("enforce", ["hard", "soft"])
def test_simple_body_font_requires_matching_effective_style(enforce):
    from app.services.policy_resolver import apply_requirement_format_overrides, hydrate_snapshot_format_overrides

    rule = {
        "id": "body-font", "type": "manual", "target": "body", "checker": "",
        "params": {}, "severity": "error", "enforce": enforce,
        "description": "正文中文字体为黑体",
    }
    snapshot = hydrate_snapshot_format_overrides({
        "policy_sets": [{"items": [rule]}], "resolved_items": [rule],
        "format_overrides": {"body": {"font_zh": "黑体"}}, "rule_pack_selection_version": 1,
    })
    project = _project()
    project.requirement_snapshot = snapshot
    base = default_template()
    mismatch = check_project(project, base)
    assert [(item["id"], item["reason"]) for item in mismatch["not_checked"]] == [
        ("body-font", "format_rule_unverified"),
    ]
    effective = apply_requirement_format_overrides(base, snapshot)
    verified = check_project(project, effective)
    assert not verified["not_checked"]
    assert [item["id"] for item in verified["applied"]] == ["body-font"]


@pytest.mark.parametrize("changes", [
    {"description": "正文中文字体为黑体，段前距12磅"},
    {"target": "figure_caption"},
    {"params": {"format_override": True, "min": 10}},
    {"description": "正文中文字体为宋体"},
])
def test_simple_body_font_does_not_certify_extra_or_mismatched_requirements(changes):
    from app.services.policy_resolver import apply_requirement_format_overrides, hydrate_snapshot_format_overrides

    rule = {"id": "font", "type": "manual", "target": "body", "checker": "", "params": {},
            "severity": "error", "enforce": "hard", "description": "正文中文字体为黑体", **changes}
    snapshot = hydrate_snapshot_format_overrides({
        "policy_sets": [{"items": [rule]}], "resolved_items": [rule],
        "format_overrides": {"body": {"font_zh": "黑体"}}, "rule_pack_selection_version": 1,
    })
    project = _project()
    project.requirement_snapshot = snapshot
    template = apply_requirement_format_overrides(default_template(), snapshot)
    report = check_project(project, template)
    assert [(item["id"], item["reason"]) for item in report["not_checked"]] == [
        ("font", "format_rule_unverified"),
    ]
    assert not report["applied"]


@pytest.mark.parametrize("description", [
    "图中标注符号和文字的字号不大于图题字号，建议五号",
    "图片内文字的字体大小不得大于图题",
    "插图中的标注文字字号不能超过图题",
])
def test_image_internal_typography_is_not_reported_as_applied_format(description):
    from app.api.workflow import _gate
    from app.services.policy_resolver import hydrate_snapshot_format_overrides

    image_rule = {
        "id": "image-font", "type": "manual", "target": "document", "checker": "",
        "params": {"format_override": True}, "severity": "error", "enforce": "hard",
        "description": description,
    }
    caption_rule = {
        "id": "caption-font", "type": "manual", "target": "figure_caption", "checker": "",
        "params": {"format_override": True}, "severity": "warning", "enforce": "soft",
        "description": "图题字号五号，居中",
    }
    items = [image_rule, caption_rule]
    snapshot = hydrate_snapshot_format_overrides({
        "policy_sets": [{"items": items}], "resolved_items": items,
        "format_item_ids": ["image-font"],  # Already-classified legacy snapshots must also be safe.
        "format_overrides": {"figure_caption": {"size_pt": 10.5}},
        "rule_pack_selection_version": 1,
    })
    project = _project()
    project.requirement_snapshot = snapshot

    report = check_project(project, default_template())

    assert [item["id"] for item in report["not_checked"]] == ["image-font", "caption-font"]
    assert report["not_checked"][0]["reason"] == "image_typography_unverified"
    assert report["not_checked"][1]["reason"] == "format_rule_unverified"
    assert not report["applied"]
    assert any(item["key"] == "automatic_unverified" for item in _gate({"check_report": report}))


def test_imported_image_font_limit_remains_unverified_and_hard_in_snapshot(tmp_path):
    from app.api.workflow import _gate
    from app.db import Store
    from app.models.policy import RequirementSet
    from app.services.policy_import import normalize_ai_policy
    from app.services.policy_resolver import build_requirement_snapshot

    policy, _ = normalize_ai_policy(
        '{"items":[]}', 'guide.pdf', '图中标注符号文字字号不大于图题的字号，建议五号字体。',
    )
    item = next(item for item in policy['items']
                if item['params'].get('image_typography_constraint') == 'caption_font_size_upper_bound')
    # A display-name change must not hide the structured requirement.
    item['description'] = '图中文字上限'
    store = Store(tmp_path / 'policy.db')
    store.save_requirement_set(RequirementSet(
        id='image-policy', name='image policy', status='published', items=[item],
        format_overrides={'figure_caption': {'size_pt': 10.5}},
    ))
    snapshot = build_requirement_snapshot(store, default_template())
    assert snapshot['resolved_items'][0]['params'] == item['params']
    project = _project()
    project.sections[0].paragraphs.append(ParagraphBlock(id='image', type='image', image=ImageBlock(url='/media/test.png')))
    project.requirement_snapshot = snapshot
    report = check_project(project, default_template())
    assert [(entry['id'], entry['reason'], entry['enforce']) for entry in report['not_checked']] == [
        (item['id'], 'image_typography_unverified', 'hard'),
    ]
    assert not any(entry['id'] == item['id'] for entry in report['applied'])
    assert any(entry['key'] == 'automatic_unverified' for entry in _gate({'check_report': report}))


def test_imported_image_limit_does_not_block_plain_document_without_images():
    from app.api.workflow import _gate
    from app.services.policy_import import normalize_ai_policy

    policy, _ = normalize_ai_policy(
        '{"items":[]}', 'guide.pdf', '图中标注符号文字字号不大于图题的字号，建议五号字体。',
    )
    item = next(item for item in policy['items']
                if item['params'].get('image_typography_constraint') == 'caption_font_size_upper_bound')
    project = _project()
    project.requirement_snapshot = {'resolved_items': [item], 'rule_pack_selection_version': 1}
    template = default_template()
    report = check_project(project, template)
    assert not report['not_checked']
    assert not any(entry['key'] == 'automatic_unverified' for entry in _gate({'check_report': report}))

    # A source master or imported table XML may retain drawings that are not
    # represented as image blocks; absence of an image block alone is no proof.
    template.source_docx = 'master.docx'
    assert check_requirements(project, project.requirement_snapshot, template)[1][0]['reason'] == 'image_typography_unverified'
    template.source_docx = ''
    project.sections[0].paragraphs.append(ParagraphBlock(
        id='table', type='table', table=TableBlock(source_xml='<w:tbl/>'),
    ))
    assert check_requirements(project, project.requirement_snapshot, template)[1][0]['reason'] == 'image_typography_unverified'


def test_image_limit_applicability_does_not_skip_an_automatic_checker():
    item = {'id': 'future', 'type': 'auto', 'target': 'document',
            'checker': 'future.unsupported',
            'params': {'image_typography_constraint': 'caption_font_size_upper_bound'},
            'enforce': 'hard', 'severity': 'error', 'description': 'future rule'}
    _, unchecked = check_requirements(_project(), {'resolved_items': [item]}, default_template())
    assert [(entry['id'], entry['reason'], entry['enforce']) for entry in unchecked] == [
        ('future', 'unsupported_checker', 'hard'),
    ]


def test_legacy_snapshot_recovers_image_limit_from_frozen_policy_text_without_mutation():
    import copy
    from app.api.workflow import _gate

    project = _project()
    project.sections[0].paragraphs.append(ParagraphBlock(id='image', type='image', image=ImageBlock(url='/media/test.png')))
    snapshot = {'rule_pack_selection_version': 1, 'content_hash': 'frozen-policy-hash',
                'policy_sets': [{'id': 'legacy-policy', 'name': '学校规范', 'version': '1',
                    'markdown_content': '图题用五号宋体。图中标注符号文字字号不大于图题的字号，建议五号字体。',
                    'items': []}], 'resolved_items': []}
    original = copy.deepcopy(snapshot)
    project.requirement_snapshot = snapshot
    report = check_project(project, default_template())
    assert [(entry['reason'], entry['enforce'], entry['source']) for entry in report['not_checked']] == [
        ('image_typography_unverified', 'hard', '学校规范')]
    assert any(entry['key'] == 'automatic_unverified' for entry in _gate({'check_report': report}))
    assert project.requirement_snapshot == original


def test_legacy_image_limit_recovery_preserves_unrelated_and_optional_policies():
    project = _project()
    for text in ('建议图中标注符号文字字号不大于图题的字号。', '正文宋体小四号。'):
        snapshot = {'policy_sets': [{'id': 'unrelated', 'markdown_content': text}], 'resolved_items': []}
        assert check_requirements(project, snapshot, default_template())[1] == []
    snapshot = {'policy_sets': [{'id': 'with-rule', 'markdown_content':
        '图中标注符号文字字号不大于图题的字号，建议五号字体。'}], 'resolved_items': []}
    assert check_requirements(project, snapshot, default_template())[1] == []
    template = default_template()
    template.source_docx = 'master.docx'
    unchecked = check_requirements(project, snapshot, template)[1]
    assert len(unchecked) == 1 and unchecked[0]['enforce'] == 'hard'


def test_legacy_image_limit_recovery_does_not_duplicate_existing_hard_rule():
    from app.services.policy_import import normalize_ai_policy
    text = '图中标注符号文字字号不大于图题的字号，建议五号字体。'
    policy, _ = normalize_ai_policy('{"items":[]}', 'guide.pdf', text)
    item = next(item for item in policy['items'] if item['params'].get('image_typography_constraint'))
    project = _project()
    project.sections[0].paragraphs.append(ParagraphBlock(id='image', type='image', image=ImageBlock(url='/media/test.png')))
    snapshot = {'policy_sets': [{'id': 'current', 'markdown_content': text}], 'resolved_items': [item]}
    unchecked = check_requirements(project, snapshot, default_template())[1]
    assert len(unchecked) == 1 and unchecked[0]['id'] == item['id']


def test_legacy_image_limit_recovered_from_frozen_item_when_markdown_was_omitted():
    project = _project()
    project.sections[0].paragraphs.append(ParagraphBlock(id='image', type='image', image=ImageBlock(url='/media/test.png')))
    item = {'id': 'old-source', 'type': 'manual', 'checker': '', 'severity': 'warning',
            'enforce': 'soft', 'params': {'format_override': True},
            'description': '原文格式要求：图中标注符号文字字号不大于图题的字号，建议五号字体。'}
    snapshot = {'policy_sets': [{'id': 'legacy', 'name': '学校规范', 'version': '1', 'items': [item]}],
                'resolved_items': [item], 'format_item_ids': ['old-source']}
    _, unchecked = check_requirements(project, snapshot, default_template())
    assert any(entry['enforce'] == 'hard' and entry['reason'] == 'image_typography_unverified'
               and entry['source'] == '学校规范' for entry in unchecked)


def test_legacy_attachment_recovers_frozen_rule_without_mutating_snapshot(tmp_path, monkeypatch):
    import copy
    import hashlib
    from app.api import policies

    monkeypatch.setattr(policies, '_DOCUMENT_DIR', tmp_path)
    data = '图题用五号宋体。图中标注符号文字字号不大于图题的字号，建议五号字体。'.encode()
    (tmp_path / 'legacy').mkdir()
    (tmp_path / 'legacy' / 'guide.txt').write_bytes(data)
    snapshot = {'policy_sets': [{'id': 'legacy', 'name': '学校规范', 'version': '1',
        'markdown_content': '', 'items': [], 'documents': [{'id': 'guide', 'storage_key': 'guide.txt',
        'filename': 'guide.txt', 'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}]}],
        'resolved_items': []}
    original = copy.deepcopy(snapshot)
    project = _project()
    project.sections[0].paragraphs.append(ParagraphBlock(id='image', type='image', image=ImageBlock(url='/media/test.png')))
    _, unchecked = check_requirements(project, snapshot, default_template())
    assert [(x['reason'], x['enforce'], x['source']) for x in unchecked] == [
        ('image_typography_unverified', 'hard', '学校规范')]
    assert snapshot == original


def test_policy_attachment_uses_configured_data_dir_when_default_path_is_active(tmp_path, monkeypatch):
    import hashlib
    from app.api import policies
    from app.services.requirement_check import _frozen_attachment_text

    monkeypatch.delenv('PAPERGUARD_DB_PATH', raising=False)
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'persistent-data'))
    monkeypatch.setattr(policies, '_DOCUMENT_DIR', policies._DEFAULT_DOCUMENT_DIR)
    data = '图题用五号宋体。'.encode()
    attachment_dir = tmp_path / 'persistent-data' / 'policy-documents' / 'legacy'
    attachment_dir.mkdir(parents=True)
    (attachment_dir / 'guide.txt').write_bytes(data)
    document = {'storage_key': 'guide.txt',
                'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}

    assert _frozen_attachment_text('legacy', document) == '图题用五号宋体。'


def test_policy_attachment_falls_back_to_legacy_root_only_when_checksum_matches(tmp_path, monkeypatch):
    import hashlib
    from app.api import policies
    from app.services.requirement_check import _frozen_attachment_text

    legacy_root = tmp_path / 'legacy-policy-documents'
    data_root = tmp_path / 'persistent-data'
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(data_root))
    monkeypatch.setattr(policies, '_DEFAULT_DOCUMENT_DIR', legacy_root)
    monkeypatch.setattr(policies, '_DOCUMENT_DIR', legacy_root)
    data = '旧版本规范附件仍可恢复读取。'.encode()
    attachment_dir = legacy_root / 'legacy'
    attachment_dir.mkdir(parents=True)
    (attachment_dir / 'guide.txt').write_bytes(data)
    document = {'storage_key': 'guide.txt',
                'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}

    assert _frozen_attachment_text('legacy', document) == '旧版本规范附件仍可恢复读取。'
    document['checksum'] = 'sha256:' + ('0' * 64)
    assert _frozen_attachment_text('legacy', document) is None


@pytest.mark.parametrize('failure', ['missing', 'mismatch', 'bad_key', 'bad_policy', 'symlink',
                                   'policy_symlink', 'checksum_missing', 'oversized', 'malformed'])
def test_legacy_attachment_unavailable_never_silently_passes_with_images(tmp_path, monkeypatch, failure):
    import hashlib
    from app.api import policies

    monkeypatch.setattr(policies, '_DOCUMENT_DIR', tmp_path / 'documents')
    data = b'plain policy text'
    directory = policies._DOCUMENT_DIR / 'legacy'
    directory.mkdir(parents=True)
    path = directory / 'guide.txt'
    path.write_bytes(data)
    document = {'id': 'guide', 'storage_key': 'guide.txt', 'filename': 'guide.txt',
                'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}
    policy = {'id': 'legacy', 'name': '学校规范', 'version': '1', 'documents': [document]}
    if failure == 'missing':
        path.unlink()
    elif failure == 'mismatch':
        path.write_bytes(b'changed')
    elif failure == 'bad_key':
        document['storage_key'] = '../outside.txt'
    elif failure == 'bad_policy':
        policy['id'] = '../outside'
    elif failure == 'symlink':
        path.unlink()
        target = tmp_path / 'outside.txt'
        target.write_bytes(data)
        path.symlink_to(target)
    elif failure == 'policy_symlink':
        path.unlink()
        directory.rmdir()
        target = tmp_path / 'outside'
        target.mkdir()
        (target / 'guide.txt').write_bytes(data)
        directory.symlink_to(target, target_is_directory=True)
    elif failure == 'checksum_missing':
        document['checksum'] = ''
    elif failure == 'oversized':
        monkeypatch.setattr(policies, '_MAX_DOCUMENT_BYTES', len(data) - 1)
    else:
        path.write_bytes(b'not a pdf')
        document.update(storage_key='guide.pdf', filename='guide.pdf',
                        checksum='sha256:' + hashlib.sha256(b'not a pdf').hexdigest())
        path.rename(directory / 'guide.pdf')
    snapshot = {'policy_sets': [policy], 'resolved_items': []}
    project = _project()
    project.sections[0].paragraphs.append(ParagraphBlock(id='image', type='image', image=ImageBlock(url='/media/test.png')))
    _, unchecked = check_requirements(project, snapshot, default_template())
    assert len(unchecked) == 1
    assert unchecked[0]['reason'] == 'policy_attachment_unverified'
    assert unchecked[0]['enforce'] == 'hard'
    assert str(tmp_path) not in unchecked[0]['description']


def test_legacy_attachment_unrelated_text_and_no_image_do_not_block(tmp_path, monkeypatch):
    import hashlib
    from app.api import policies

    monkeypatch.setattr(policies, '_DOCUMENT_DIR', tmp_path)
    data = '正文宋体小四号。'.encode()
    (tmp_path / 'legacy').mkdir()
    (tmp_path / 'legacy' / 'guide.txt').write_bytes(data)
    snapshot = {'policy_sets': [{'id': 'legacy', 'documents': [{'id': 'guide', 'storage_key': 'guide.txt',
        'filename': 'guide.txt', 'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}]}], 'resolved_items': []}
    project = _project()
    project.sections[0].paragraphs.append(ParagraphBlock(id='image', type='image', image=ImageBlock(url='/media/test.png')))
    assert check_requirements(project, snapshot, default_template())[1] == []
    (tmp_path / 'legacy' / 'guide.txt').unlink()
    unchecked = check_requirements(_project(), snapshot, default_template())[1]
    assert [(item['reason'], item['enforce']) for item in unchecked] == [('policy_attachment_unverified', 'hard')]


@pytest.mark.parametrize('description', ['正文必须使用黑体。', '正文中文字体必须使用黑体。'])
def test_unmapped_mandatory_body_font_from_frozen_attachment_is_not_silent(tmp_path, monkeypatch, description):
    import hashlib
    from app.api import policies
    from app.api.projects import _current_export_requirement_checks

    monkeypatch.setattr(policies, '_DOCUMENT_DIR', tmp_path)
    data = description.encode()
    (tmp_path / 'legacy').mkdir()
    (tmp_path / 'legacy' / 'guide.txt').write_bytes(data)
    snapshot = {'policy_sets': [{'id': 'legacy', 'documents': [{'id': 'guide', 'storage_key': 'guide.txt',
        'filename': 'guide.txt', 'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}]}], 'resolved_items': []}
    project = _project()
    project.requirement_snapshot = snapshot
    template = default_template()
    template.styles['body'].font_zh = '宋体'
    _, unchecked = check_requirements(project, snapshot, template)
    assert [(item['reason'], item['enforce']) for item in unchecked] == [('format_rule_unverified', 'hard')]
    assert _current_export_requirement_checks(project, template)


@pytest.mark.parametrize('description', [
    '建议正文使用黑体。', '正文不得使用黑体。', '例如：正文必须使用黑体。',
    '图中文字必须使用黑体。',
])
def test_body_font_recovery_does_not_guess_other_clauses(tmp_path, monkeypatch, description):
    import hashlib
    from app.api import policies

    monkeypatch.setattr(policies, '_DOCUMENT_DIR', tmp_path)
    data = description.encode()
    (tmp_path / 'legacy').mkdir()
    (tmp_path / 'legacy' / 'guide.txt').write_bytes(data)
    snapshot = {'policy_sets': [{'id': 'legacy', 'documents': [{'id': 'guide', 'storage_key': 'guide.txt',
        'filename': 'guide.txt', 'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}]}], 'resolved_items': []}
    assert check_requirements(_project(), snapshot, default_template())[1] == []


def test_compound_body_requirement_is_not_reduced_to_font_only():
    snapshot = {'policy_sets': [{'id': 'legacy', 'markdown_content':
        '正文必须使用黑体、并且每段不少于100字。'}], 'resolved_items': []}
    unchecked = check_requirements(_project(), snapshot, default_template())[1]
    assert [(item['reason'], item['enforce']) for item in unchecked] == [('format_rule_unverified', 'hard')]
    assert '每段不少于100字' in unchecked[0]['description']


@pytest.mark.parametrize('resolved', [[], [{'id': 'original', 'type': 'manual', 'enforce': 'soft',
                                            'description': '其他建议'}]])
def test_unmapped_legacy_policy_item_still_recovers_mandatory_body_font(resolved):
    snapshot = {'policy_sets': [{'id': 'legacy', 'items': [
        {'id': 'original', 'description': '正文必须使用黑体。'}]}], 'resolved_items': resolved}
    unchecked = check_requirements(_project(), snapshot, default_template())[1]
    assert any(item['reason'] == 'format_rule_unverified' and item['enforce'] == 'hard'
               and '正文必须使用黑体' in item['description'] for item in unchecked)


def test_recovered_explicit_chinese_font_uses_verified_effective_style():
    from app.models.template import TemplateSection, TextStyle
    from app.services.policy_resolver import apply_requirement_format_overrides

    snapshot = {'policy_sets': [{'id': 'legacy', 'markdown_content': '正文中文字体必须使用黑体。'}],
        'resolved_items': [], 'format_overrides': {'body': {'font_zh': '黑体'}}}
    base = default_template()
    assert check_requirements(_project(), snapshot, base)[1][0]['reason'] == 'format_rule_unverified'
    effective = apply_requirement_format_overrides(base, snapshot)
    assert check_requirements(_project(), snapshot, effective)[1] == []
    effective.sections.append(TemplateSection(key='outer', type='heading1', title='章', children=[
        TemplateSection(key='custom-body', type='body', title='正文', style=TextStyle(font_zh='宋体'))]))
    assert check_requirements(_project(), snapshot, effective)[1][0]['reason'] == 'format_rule_unverified'
    effective.sections[-1].children[0].style.font_zh = '黑体'
    # The unqualified wording might include Latin text and must not be
    # certified by the east-Asian font setting alone.
    snapshot['policy_sets'][0]['markdown_content'] = '正文必须使用黑体。'
    assert check_requirements(_project(), snapshot, effective)[1][0]['reason'] == 'format_rule_unverified'


def test_existing_image_rule_does_not_hide_another_attachment(tmp_path, monkeypatch):
    import hashlib
    from app.api import policies

    monkeypatch.setattr(policies, '_DOCUMENT_DIR', tmp_path)
    data = '正文必须使用黑体。'.encode()
    (tmp_path / 'legacy').mkdir()
    (tmp_path / 'legacy' / 'guide.txt').write_bytes(data)
    image_rule = {'id': 'figure', 'type': 'manual', 'checker': '', 'target': 'document',
        'params': {'image_typography_constraint': 'caption_font_size_upper_bound'},
        'enforce': 'hard', 'severity': 'error', 'description': '图中文字字号要求'}
    snapshot = {'resolved_items': [image_rule], 'policy_sets': [{'id': 'legacy', 'documents': [
        {'id': 'guide', 'storage_key': 'guide.txt', 'checksum': 'sha256:' + hashlib.sha256(data).hexdigest()}]}]}
    unchecked = check_requirements(_project(), snapshot, default_template())[1]
    assert [(item['reason'], item['enforce']) for item in unchecked] == [('format_rule_unverified', 'hard')]


def test_required_section_is_checked_for_presence_and_content():
    project = _project()
    findings, _ = check_requirements(project, {
        "resolved_items": [{
            "id": "method", "type": "auto", "target": "methodology",
            "checker": "section.exists", "params": {}, "severity": "error",
            "enforce": "hard", "description": "必须包含研究方法", "source": "专业规范",
        }],
    })
    assert findings[0]["section_key"] == "methodology"
    assert findings[0]["actual"] == 0


def test_reference_format_checker_and_chapter_page_break_checker():
    template = default_template().model_copy(deep=True)
    template.styles["heading1"].page_break_before = False
    malformed = _project()
    malformed.sections[-1].paragraphs[0].text = "作者. 缺少年份的文献."
    findings, not_checked = check_requirements(malformed, {
        "resolved_items": [
            {
                "id": "reference-format", "type": "auto", "target": "references",
                "checker": "references.format", "params": {}, "severity": "warning",
                "enforce": "soft", "description": "参考文献应符合著录格式",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "chapter-break", "type": "auto", "target": "ch1",
                "checker": "chapter.page_break", "params": {}, "severity": "error",
                "enforce": "hard", "description": "一级章节应章前分页",
                "source": "学校规范", "source_version": "1",
            },
        ],
    }, template)
    assert {finding["requirement_item_id"] for finding in findings} == {
        "reference-format", "chapter-break",
    }
    assert [(item["id"], item["reason"]) for item in not_checked] == [
        ("reference-format", "reference_standard_unverified"),
    ]

    project = _project().model_copy(deep=True)
    project.sections[-1].paragraphs[0].text = "[1] 作者. 研究. 2025."
    project.sections[-1].paragraphs[1].text = "[2] 作者. 旧研究. 2019."
    findings, _ = check_requirements(project, {
        "resolved_items": [{
            "id": "reference-format", "type": "auto", "target": "references",
            "checker": "references.format", "params": {}, "severity": "warning",
            "enforce": "soft", "description": "参考文献应符合著录格式",
        }],
    }, template)
    assert findings == []

    # Generated numbering satisfies the prefix requirement; malformed source
    # metadata such as a missing year above must still be reported.
    project.sections[-1].paragraphs[0].text = "作者. 研究. 2025."
    findings, _ = check_requirements(project, {"resolved_items": [{
        "id": "reference-format", "type": "auto", "target": "references",
        "checker": "references.format", "params": {}, "severity": "warning", "enforce": "soft",
    }]}, template)
    assert findings == []


@pytest.mark.parametrize("params", [
    {"format_check": "apa"},
    {"format_check": "number_and_year", "punctuation": "gbt"},
    {"prefix_pattern": "("},
])
def test_reference_format_does_not_claim_unsupported_snapshot_parameters(params):
    project = _project()
    project.sections[-1].paragraphs[0].text = "[1] 作者. 研究. 2025."
    project.sections[-1].paragraphs[1].text = "[2] 作者. 研究. 2024."
    rule = {"id": "reference-format-unknown", "type": "auto", "target": "references",
            "checker": "references.format", "params": params,
            "severity": "error", "enforce": "hard", "description": "文献格式检查"}
    _, unchecked = check_requirements(project, {"resolved_items": [rule]}, default_template())
    assert any(item["id"] == rule["id"] and item["reason"] == "unsupported_checker_parameters"
               for item in unchecked)


@pytest.mark.parametrize("description", [
    "参考文献应符合 APA 7 格式",
    "参考文献应符合著录格式",
    "参考文献应遵循 IEEE 标准",
    "参考文献须符合 GB/T 7714 著录规则",
])
@pytest.mark.parametrize("params", [{}, {"format_check": "number_and_year"}])
def test_number_year_check_does_not_claim_broad_reference_standard(description, params):
    project = _project()
    project.sections[-1].paragraphs[0].text = "[1] 作者. 研究. 2025."
    project.sections[-1].paragraphs[1].text = "[2] 作者. 研究. 2024."
    rule = {"id": "broad-reference", "type": "auto", "target": "references",
            "checker": "references.format", "params": params,
            "severity": "error", "enforce": "hard", "description": description}
    _, unchecked = check_requirements(project, {"resolved_items": [rule]}, default_template())
    assert any(item["id"] == rule["id"] and item["reason"] == "reference_standard_unverified"
               for item in unchecked)


@pytest.mark.parametrize("enforce", ["hard", "soft"])
def test_broad_reference_standard_retains_gate_and_number_year_failures(enforce):
    from app.api.projects import _current_export_requirement_checks
    from app.api.workflow import _gate

    project = _project()
    project.sections[-1].paragraphs[0].text = "缺少年份"
    project.requirement_snapshot = {"rule_pack_selection_version": 1, "resolved_items": [{
        "id": "broad-reference", "type": "auto", "target": "references",
        "checker": "references.format", "params": {"format_check": "number_and_year"},
        "severity": "error", "enforce": enforce, "description": "参考文献应符合 APA 7 格式",
    }]}
    template = default_template()
    report = check_project(project, template)
    assert any(item.get("requirement_item_id") == "broad-reference" for item in report["findings"])
    assert any(item["id"] == "broad-reference" and item["reason"] == "reference_standard_unverified"
               for item in report["not_checked"])
    assert bool(_current_export_requirement_checks(project, template)) == (enforce == "hard")
    assert any(item["key"] == "automatic_unverified" for item in _gate({"check_report": report})) == (enforce == "hard")


@pytest.mark.parametrize("params", [{}, {"format_check": "number_and_year"}])
def test_reference_format_keeps_supported_number_and_year_check(params):
    project = _project()
    project.sections[-1].paragraphs[0].text = "[1] 作者. 研究. 2025."
    project.sections[-1].paragraphs[1].text = "[2] 作者. 研究. 2024."
    rule = {"id": "number-year", "type": "auto", "target": "references",
            "checker": "references.format", "params": params,
            "severity": "error", "enforce": "hard", "description": "参考文献需有序号和年份"}
    assert check_requirements(project, {"resolved_items": [rule]}, default_template()) == ([], [])
    project.sections[-1].paragraphs[1].text = "缺少年份的文献"
    findings, unchecked = check_requirements(project, {"resolved_items": [rule]}, default_template())
    assert any(item["requirement_item_id"] == rule["id"] for item in findings)
    assert not unchecked


@pytest.mark.parametrize("enforce", ["hard", "soft"])
def test_unsupported_reference_format_keeps_its_delivery_gate(enforce):
    from app.api.projects import _current_export_requirement_checks
    from app.api.workflow import _gate

    project = _project()
    project.requirement_snapshot = {"rule_pack_selection_version": 1, "resolved_items": [{
        "id": "unknown-standard", "type": "auto", "target": "references",
        "checker": "references.format", "params": {"format_check": "apa"},
        "severity": "error", "enforce": enforce, "description": "文献格式检查",
    }]}
    template = default_template()
    report = check_project(project, template)
    assert any(item["id"] == "unknown-standard" and item["reason"] == "unsupported_checker_parameters"
               for item in report["not_checked"])
    assert bool(_current_export_requirement_checks(project, template)) == (enforce == "hard")
    assert any(item["key"] == "automatic_unverified" for item in _gate({"check_report": report})) == (enforce == "hard")


def test_cover_fields_checker_uses_declared_template_keys_and_reports_each_missing_field():
    template = default_template().model_copy(deep=True)
    template.meta_fields = [MetaField(key="title", label="题目"), MetaField(key="author", label="姓名")]
    project = _project()
    project.meta = {"title": "合成题目", "author": "  ", "not_a_field": "不可代替姓名"}
    item = {"id": "cover-fields", "type": "auto", "target": "cover", "checker": "cover.fields_present",
            "params": {"field_keys": ["title", "author"]}, "severity": "error", "enforce": "hard"}
    findings, not_checked = check_requirements(project, {"resolved_items": [item]}, template)
    assert not not_checked
    assert len(findings) == 1 and findings[0]["actual"] == ["姓名"]
    project.meta["author"] = "测试学生"
    assert check_requirements(project, {"resolved_items": [item]}, template) == ([], [])
    item["params"]["field_keys"].append("department")
    findings, not_checked = check_requirements(project, {"resolved_items": [item]}, template)
    assert not findings and not_checked[0]["reason"] == "cover_field_mapping_missing"
    assert check_requirements(project, {"resolved_items": [item]}, None)[1][0]["reason"] == "cover_field_mapping_missing"


def test_reference_list_checker_validates_facts_and_stable_numbers():
    project = _project()
    section = project.sections[-1]
    section.paragraphs = [
        ParagraphBlock(id="one", reference_number=1, reference=ReferenceEntry(
            kind="journal", authors="张三", title="研究", source="示例期刊", year="2024",
            original_pasted_text="[8] 张三. 研究[J]. 示例期刊, 2024.")),
        ParagraphBlock(id="two", reference_number=2, reference=ReferenceEntry(
            kind="journal", authors="李四", title="另一个研究", source="示例期刊", year="2025")),
    ]
    item = {"id": "reference-facts", "type": "auto", "target": "references", "checker": "references.list_facts",
            "params": {"required": True}, "severity": "error", "enforce": "hard"}
    assert check_requirements(project, {"resolved_items": [item]}) == ([], [])
    section.paragraphs[1].reference.year = ""
    section.paragraphs[1].reference_number = 3
    findings, not_checked = check_requirements(project, {"resolved_items": [item]})
    assert not not_checked
    assert any("缺少年份" in result["message"] for result in findings)
    assert not any("编号为 [3]" in result["message"] for result in findings)
    section.paragraphs[1] = ParagraphBlock(id="legacy", text="未结构化的旧文献")
    findings, _ = check_requirements(project, {"resolved_items": [item]})
    assert any("尚未录入结构化文献事实" in result["message"] for result in findings)


@pytest.mark.parametrize('numbering', [None, 'sequential'])
def test_optional_reference_rule_accepts_empty_list_and_checks_present_entries(numbering):
    project = _project()
    project.sections[-1].paragraphs = []
    params = {'required': False}
    if numbering:
        params['numbering'] = numbering
    item = {'id': 'optional-references', 'type': 'auto', 'target': 'references',
            'checker': 'references.list_facts', 'params': params, 'severity': 'error', 'enforce': 'hard'}
    assert check_requirements(project, {'resolved_items': [item]}) == ([], [])
    project.sections[-1].paragraphs = [ParagraphBlock(id='missing-facts', text='未结构化的旧文献')]
    findings, not_checked = check_requirements(project, {'resolved_items': [item]})
    assert not not_checked
    assert any('尚未录入结构化文献事实' in finding['message'] for finding in findings)


def test_reference_list_checker_enforces_sequential_square_numbers_and_confirmed_language_order():
    project = _project()
    section = project.sections[-1]
    confirmed_zh = ReferenceLanguageFact(languages=["zh"], status="confirmed", confirmed_by="student", confirmed_at="2026-11-05T00:00:00Z")
    confirmed_en = ReferenceLanguageFact(languages=["en"], status="confirmed", confirmed_by="student", confirmed_at="2026-11-05T00:00:00Z")
    section.paragraphs = [
        ParagraphBlock(id="zh", reference_number=1, reference=ReferenceEntry(
            kind="journal", authors="张三", title="中文研究", source="中文期刊", year="2024",
            language_fact=confirmed_zh)),
        ParagraphBlock(id="en", reference_number=2, reference=ReferenceEntry(
            kind="journal", authors="Smith", title="English Study", source="Journal", year="2025",
            language_fact=confirmed_en)),
    ]
    item = {"id": "pilot-references", "type": "auto", "target": "references", "checker": "references.list_facts",
            "params": {"required": True, "numbering": "sequential", "bracket_style": "square",
                        "language_order": "zh_before_en"}, "severity": "error", "enforce": "hard"}

    assert check_requirements(project, {"resolved_items": [item]}) == ([], [])

    section.paragraphs.reverse()
    findings, not_checked = check_requirements(project, {"resolved_items": [item]})
    assert not not_checked
    assert any("违反中文在前、英文在后的顺序" in result["message"] for result in findings)
    assert any("编号不连续" in result["message"] for result in findings)

    section.paragraphs[1].reference.language_fact = ReferenceLanguageFact(languages=["en"])
    findings, _ = check_requirements(project, {"resolved_items": [item]})
    assert any("语种未确认" in result["message"] for result in findings)


def test_square_brackets_do_not_require_sequential_reference_numbers():
    project = _project()
    project.sections[-1].paragraphs = [ParagraphBlock(id='preserved', reference_number=2,
        reference=ReferenceEntry(kind='journal', authors='张三', title='研究', source='示例期刊', year='2024'))]
    item = {'id': 'square-only', 'type': 'auto', 'target': 'references', 'checker': 'references.list_facts',
            'params': {'bracket_style': 'square'}, 'severity': 'error', 'enforce': 'hard'}
    assert check_requirements(project, {'resolved_items': [item]}) == ([], [])
    item['params']['numbering'] = 'sequential'
    findings, not_checked = check_requirements(project, {'resolved_items': [item]})
    assert not not_checked
    assert any('编号不连续' in finding['message'] for finding in findings)
    assert not any('方括号' in finding['message'] for finding in findings)


@pytest.mark.parametrize("legacy_params", [
    {"min": 0, "max": 35},
    {"fields": ["题目", "学院", "系别", "专业", "班级", "姓名", "学号", "指导教师", "提交日期"]},
])
def test_exact_legacy_1101_cover_rule_uses_automatic_field_check_and_still_requires_template_mapping(legacy_params):
    project = _project()
    project.meta = {
        "title": "测试题目", "college": "测试学院", "department": "测试系",
        "major": "测试专业", "class_name": "测试班级", "author": "测试学生",
        "student_id": "20260001", "advisor": "测试教师", "date": "2026年5月",
    }
    template = default_template().model_copy(deep=True)
    field_keys = ["title", "college", "department", "major", "class_name", "author", "student_id", "advisor", "date"]
    template.meta_fields = [MetaField(key=key, label=key) for key in field_keys]
    legacy = {
        "id": "cover_required_fields", "type": "manual", "target": "cover", "checker": "",
        "params": legacy_params,
        "description": "封面须包含题目、学生所在学院、系别、专业、班级、姓名、学号、指导教师、提交日期",
        "severity": "error", "enforce": "hard",
    }

    assert check_requirements(project, {"resolved_items": [legacy]}, template) == ([], [])

    project.meta["department"] = ""
    findings, not_checked = check_requirements(project, {"resolved_items": [legacy]}, template)
    assert not not_checked
    assert any("department" in result["message"] for result in findings)
    project.meta["department"] = "测试系"

    template.meta_fields = [field for field in template.meta_fields if field.key != "department"]
    findings, not_checked = check_requirements(project, {"resolved_items": [legacy]}, template)
    assert findings == []
    assert not_checked[0]["reason"] == "cover_field_mapping_missing"
    assert not_checked[0]["enforce"] == "hard"


def test_legacy_1101_cover_rule_does_not_guess_changed_fields():
    item = {
        "id": "cover_required_fields", "type": "manual", "target": "cover", "checker": "",
        "params": {"fields": ["题目", "学院"]},
        "description": "封面须包含题目、学生所在学院、系别、专业、班级、姓名、学号、指导教师、提交日期",
        "severity": "error", "enforce": "hard",
    }
    findings, not_checked = check_requirements(_project(), {"resolved_items": [item]}, default_template())
    assert not findings
    assert len(not_checked) == 1 and not_checked[0]["type"] == "manual"


def test_exact_legacy_1101_reference_rule_checks_order_and_preserves_fail_closed_near_misses():
    project = _project()
    section = project.sections[-1]
    section.paragraphs = [
        ParagraphBlock(id="zh", reference_number=1, reference=ReferenceEntry(
            kind="journal", authors="张三", title="中文研究", source="中文期刊", year="2024",
            language_fact=ReferenceLanguageFact(
                languages=["zh"], status="confirmed", confirmed_by="student", confirmed_at="2026-11-05T00:00:00Z",
            ),
        )),
        ParagraphBlock(id="en", reference_number=2, reference=ReferenceEntry(
            kind="journal", authors="Smith", title="English Study", source="Journal", year="2025",
            language_fact=ReferenceLanguageFact(
                languages=["en"], status="confirmed", confirmed_by="student", confirmed_at="2026-11-05T00:00:00Z",
            ),
        )),
    ]
    legacy = {
        "id": "references_format", "type": "manual", "target": "references", "checker": "",
        "params": {
            "order": "sequential", "bracket": "square", "style": "顺序编码制",
            "language_order": "中文在前，英文在后",
        },
        "description": "参考文献按引用或参考先后顺序编号，中文文献在前英文文献在后，序号用[1][2]...格式",
        "severity": "error", "enforce": "hard",
    }

    assert check_requirements(project, {"resolved_items": [legacy]}) == ([], [])

    section.paragraphs[1].reference.language_fact.status = "unconfirmed"
    findings, not_checked = check_requirements(project, {"resolved_items": [legacy]})
    assert not not_checked
    assert any("语种未确认" in result["message"] for result in findings)

    section.paragraphs[1].reference.language_fact.status = "confirmed"
    section.paragraphs.reverse()
    findings, not_checked = check_requirements(project, {"resolved_items": [legacy]})
    assert not not_checked
    assert any("编号不连续" in result["message"] for result in findings)
    assert any("违反中文在前、英文在后的顺序" in result["message"] for result in findings)

    legacy["params"]["style"] = "未确认的制度"
    findings, not_checked = check_requirements(project, {"resolved_items": [legacy]})
    assert findings == []
    assert not_checked[0]["reason"] == "manual"
    assert not_checked[0]["enforce"] == "hard"


def test_reference_list_checker_keeps_invalid_parameters_unchecked():
    project = _project()
    item = {"id": "bad-reference-rule", "type": "auto", "target": "references", "checker": "references.list_facts",
            "params": {"numbering": "sort-by-author"}, "severity": "error", "enforce": "hard"}

    findings, not_checked = check_requirements(project, {"resolved_items": [item]})

    assert findings == []
    assert len(not_checked) == 1
    assert not_checked[0]["reason"] == "unsupported_checker_parameters"
    assert not_checked[0]["enforce"] == "hard"


def test_markdown_frontmatter_maps_p0_checker_aliases():
    items = items_from_markdown("""---
rules:
  - id: refs-format
    type: reference_format
    target: references
  - id: chapter-break
    type: chapter_page_break
    target: ch1
  - id: cover-fields
    type: cover_required_fields
    target: cover
    params:
      field_keys: [title, college]
  - id: references-list
    type: references_list_facts
    target: references
    params:
      required: true
      numbering: sequential
      bracket_style: square
      language_order: zh_before_en
---
""")
    assert [item.checker for item in items] == [
        "references.format", "chapter.page_break", "cover.fields_present", "references.list_facts",
    ]
    assert items[2].params["field_keys"] == ["title", "college"]
    assert items[3].params["language_order"] == "zh_before_en"


def _quality_reference(index: int, year: str, languages=("zh",), confirmed=True) -> ParagraphBlock:
    fact = ReferenceLanguageFact(languages=list(languages), status="confirmed" if confirmed else "unconfirmed",
                                 confirmed_by="student" if confirmed else "", confirmed_at="2026-01-01" if confirmed else "")
    year_fact = ReferenceYearFact(value=year, status="confirmed", confirmed_by="student", confirmed_at="2026-01-01")
    return ParagraphBlock(id=f"ref-{index}", reference=ReferenceEntry(kind="journal", title=f"Source {index}", year=year, language_fact=fact, year_fact=year_fact))


def _quality_check(project, template):
    item = {"id": "quality", "type": "auto", "target": "references", "checker": "references.recent_language_summary",
            "params": {"defense_date_field_key": "date"}, "severity": "error", "enforce": "hard"}
    findings, not_checked = check_requirements(project, {"resolved_items": [item]}, template)
    assert not not_checked and len(findings) == 1
    assert findings[0]["severity"] == "info" and findings[0]["enforce"] == "soft"
    return findings[0]


def test_reference_quality_requires_explicit_date_binding_and_valid_date():
    project = _project()
    template = default_template().model_copy(deep=True)
    template.meta_fields = [MetaField(key="date", label="日期")]
    for value in ("", "2025或2026", "2026-02-30"):
        project.meta["date"] = value
        result = _quality_check(project, template)
        assert result["actual"]["status"] == "window_unknown"
        assert result["actual"]["recent_confirmed"] is None
    project.meta["date"] = "2026年5月"
    template.meta_fields = [MetaField(key="other", label="日期")]
    result = _quality_check(project, template)
    assert result["actual"]["status"] == "window_unknown"
    assert result["actual"]["reasons"] == ["模板未明确绑定答辩日期字段"]


def test_reference_quality_uses_window_and_confirmed_unmixed_language_only():
    project = _project()
    project.meta["date"] = "2026年5月"
    template = default_template().model_copy(deep=True)
    template.meta_fields = [MetaField(key="date", label="日期")]
    references = project.sections[-1]
    references.paragraphs = [_quality_reference(i, "2024" if i == 0 else "2026", ("en",) if i < 5 else ("zh",)) for i in range(15)]
    references.paragraphs += [_quality_reference(15, "2023", ("en",)), _quality_reference(16, "2027", ("en",)),
                               _quality_reference(17, "2025", ("en", "zh")), _quality_reference(18, "2025", ("en",), False),
                               ParagraphBlock(id="legacy", text="English title 2025")]
    result = _quality_check(project, template)["actual"]
    assert result["window"] == [2024, 2026]
    assert (result["total"], result["recent_confirmed"], result["english_confirmed"]) == (20, 17, 5)
    assert (result["unknown_year"], result["unknown_language_in_window"], result["status"]) == (1, 2, "met")
    project.meta["date"] = "2027-05-10"
    result = _quality_check(project, template)["actual"]
    assert result["window"] == [2025, 2027] and result["english_confirmed"] == 5


def test_reference_quality_unknowns_only_allow_a_decision_when_bounds_are_conclusive():
    project = _project()
    project.meta["date"] = "2026-05-01"
    template = default_template().model_copy(deep=True)
    template.meta_fields = [MetaField(key="date", label="日期")]
    references = project.sections[-1]
    references.paragraphs = [_quality_reference(i, "2026", ("en",) if i < 5 else ("zh",)) for i in range(14)]
    references.paragraphs.append(_quality_reference(14, ""))
    assert _quality_check(project, template)["actual"]["status"] == "incomplete"
    references.paragraphs.pop(13)
    assert _quality_check(project, template)["actual"]["status"] == "not_met"
    references.paragraphs = [_quality_reference(i, "2026", ("en",) if i < 4 else ("zh",)) for i in range(15)]
    references.paragraphs[4] = _quality_reference(4, "2026", ("en", "zh"))
    assert _quality_check(project, template)["actual"]["status"] == "incomplete"


def test_reference_quality_is_separate_from_delivery_failures():
    project = _project()
    project.meta["date"] = "2026-05-01"
    template = default_template().model_copy(deep=True)
    template.meta_fields = [MetaField(key="date", label="日期")]
    project.requirement_snapshot = {"resolved_items": [{"id": "quality", "type": "auto", "target": "references",
        "checker": "references.recent_language_summary", "params": {"defense_date_field_key": "date"},
        "severity": "error", "enforce": "hard"}]}
    report = check_project(project, template)
    assert len(report["quality_advisories"]) == 1
    assert not any(item["checker"] == "references.recent_language_summary" for item in report["findings"])
    assert report["summary"]["hard_failures"] == 0


def test_reference_quality_does_not_treat_an_unconfirmed_year_as_known():
    project = _project()
    project.meta["date"] = "2026-05-01"
    template = default_template().model_copy(deep=True)
    template.meta_fields = [MetaField(key="date", label="日期")]
    references = project.sections[-1]
    references.paragraphs = [
        ParagraphBlock(id=f"ref-{i}", reference=ReferenceEntry(
            kind="journal", title=f"Source {i}", year="2026",
            language_fact=ReferenceLanguageFact(
                languages=["en"] if i < 5 else ["zh"], status="confirmed",
                confirmed_by="student", confirmed_at="2026-01-01",
            ),
        )) for i in range(15)
    ]
    result = _quality_check(project, template)["actual"]
    assert result["recent_confirmed"] == 0
    assert result["english_confirmed"] == 0
    assert result["unknown_year"] == 15
    assert result["status"] == "incomplete"


def test_reference_facts_check_preserves_stable_numbers_after_deletion():
    from app.services.references import reconcile_reference_numbers

    project = _project()
    section = project.sections[-1]
    section.paragraphs = [
        ParagraphBlock(id="ref-one", reference_number=1, reference=ReferenceEntry(
            kind="journal", authors="甲", title="题名甲", source="期刊", year="2024")),
        ParagraphBlock(id="ref-two", reference_number=2, reference=ReferenceEntry(
            kind="journal", authors="乙", title="题名乙", source="期刊", year="2024")),
    ]
    updated = project.model_copy(deep=True)
    updated.sections[-1].paragraphs.pop(0)
    reconcile_reference_numbers(project, updated)
    assert updated.sections[-1].paragraphs[0].reference_number == 2
    item = {"id": "reference-facts", "type": "auto", "target": "references", "checker": "references.list_facts",
            "params": {"required": True}, "severity": "error", "enforce": "hard"}
    assert check_requirements(updated, {"resolved_items": [item]}) == ([], [])
