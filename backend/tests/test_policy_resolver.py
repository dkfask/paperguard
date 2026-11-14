"""Regression tests for requirement merging across policy sources."""

from app.services.policy_resolver import _merge_items


def test_unchecked_requirements_with_different_meanings_are_not_merged():
    items = [
        {"id": "thesis", "type": "manual", "target": "body", "checker": "",
         "params": {"min": 8000}, "enforce": "soft", "severity": "warning",
         "description": "论文正文不少于8000字", "source_detail": {"id": "pilot"}},
        {"id": "report", "type": "manual", "target": "body", "checker": "",
         "params": {"min": 6000}, "enforce": "soft", "severity": "warning",
         "description": "报告正文不少于6000字", "source_detail": {"id": "pilot"}},
        {"id": "heading", "type": "manual", "target": "body", "checker": "",
         "params": {"max": 25}, "enforce": "soft", "severity": "warning",
         "description": "每章标题不宜超过25个汉字", "source_detail": {"id": "pilot"}},
    ]

    resolved, conflicts = _merge_items(items)

    assert [(item["id"], item["params"]) for item in resolved] == [
        ("thesis", {"min": 8000}), ("report", {"min": 6000}), ("heading", {"max": 25})
    ]
    assert conflicts == []


def test_matching_automatic_checks_still_combine_stricter_thresholds():
    items = [
        {"id": "school", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
         "params": {"min": 100}, "enforce": "soft", "severity": "warning",
         "description": "摘要不少于100字", "source_detail": {"id": "school"}},
        {"id": "major", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
         "params": {"min": 200}, "enforce": "hard", "severity": "error",
         "description": "摘要不少于200字", "source_detail": {"id": "major"}},
    ]

    resolved, conflicts = _merge_items(items)

    assert len(resolved) == 1
    assert resolved[0]["params"]["min"] == 200
    assert items[0]["params"] == {"min": 100}
    assert resolved[0]["enforce"] == "hard"
    assert [source["id"] for source in resolved[0]["sources"]] == ["school", "major"]
    assert len(conflicts) == 1 and conflicts[0]["kind"] == "threshold_merge"


def test_compound_auto_format_rule_survives_merging_in_either_order():
    plain = {"id": "plain", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
             "params": {"min": 1}, "enforce": "hard", "severity": "error",
             "description": "摘要不少于1字", "source_detail": {"id": "school"}}
    compound = {**plain, "id": "compound", "description": "摘要不少于1字，正文使用黑体",
                "source_detail": {"id": "major"}}
    for items in ([plain, compound], [compound, plain]):
        resolved, _ = _merge_items(items)
        assert {item["id"] for item in resolved} == {"plain", "compound"}


def test_same_format_rule_id_from_different_policies_keeps_both_requirements():
    font = {"id": "format", "type": "manual", "target": "body", "checker": "",
            "params": {"format_override": True}, "enforce": "hard", "severity": "error",
            "description": "正文中文字体为黑体", "source_detail": {"id": "school"}}
    border = {**font, "target": "table", "description": "表格边框必须为黑色",
              "source_detail": {"id": "major"}}
    for items in ([font, border], [border, font]):
        resolved, _ = _merge_items(items)
        assert [item["description"] for item in resolved] == [item["description"] for item in items]
