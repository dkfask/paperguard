import pytest

from app.services.output_acceptance import (
    CONTRACT_VERSION,
    apply_1101_acceptance_contract,
    output_report_is_acceptable,
)


def _report(statuses, rule="master.effective_format"):
    checks = [
        {
            "rule": rule,
            "object": f"paragraph:{index}",
            "unit": f"paragraph:{index}#paragraph.space_after",
            "status": status,
        }
        for index, status in enumerate(statuses)
    ]
    return {
        "source_profile_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "checks": checks,
        "check_count": len(checks),
        "passed_count": statuses.count("pass"),
        "unsupported": [],
    }


def test_1101_acceptance_requires_strictly_more_than_ninety_percent():
    exact = apply_1101_acceptance_contract(_report(["pass"] * 9 + ["fail"]))
    above = apply_1101_acceptance_contract(_report(["pass"] * 91 + ["fail"] * 9))

    assert exact["format_accuracy"] == {"passed": 9, "denominator": 10, "percentage": 90.0}
    assert exact["acceptance_status"] == "fail"
    assert above["format_accuracy"] == {"passed": 91, "denominator": 100, "percentage": 91.0}
    assert above["acceptance_status"] == "pass"
    assert above["acceptance_contract"] == CONTRACT_VERSION
    assert len({item["check_id"] for item in above["checks"]}) == 100
    assert output_report_is_acceptable(above)


def test_duplicate_check_rows_cannot_inflate_the_ninety_percent_score():
    report = _report(["pass"] * 9 + ["fail"])
    report["checks"].append(dict(report["checks"][0]))
    report["check_count"] += 1
    report["passed_count"] += 1

    result = apply_1101_acceptance_contract(report)

    assert result["format_accuracy"]["percentage"] > 90.0
    assert result["acceptance_status"] == "fail"
    assert "检查单元身份重复" in result["hard_blockers"]
    assert not output_report_is_acceptable(result)


def test_1101_acceptance_keeps_structure_unknown_and_bad_evidence_as_hard_blocks():
    report = _report(["pass"] * 99 + ["fail"])
    report["checks"][-1]["rule"] = "master.content.order_and_presence"
    report["passed_count"] = 99
    result = apply_1101_acceptance_contract(report)

    assert result["format_accuracy"]["percentage"] == 99.0
    assert result["acceptance_status"] == "fail"
    assert result["hard_blockers"]
    assert not output_report_is_acceptable(result)


def test_1101_acceptance_blocks_unverifiable_or_inconsistent_report_rows():
    report = _report(["pass"] * 95 + ["unverifiable"] * 5)
    report["passed_count"] = 95
    result = apply_1101_acceptance_contract(report)
    assert result["format_accuracy"] == {"passed": 95, "denominator": 100, "percentage": 95.0}
    assert result["acceptance_status"] == "fail"
    assert any(item.endswith(":unverifiable") for item in result["hard_blockers"])
    assert not output_report_is_acceptable(result)
    assert len(result["unverifiable_checks"]) == 5
    assert result["rule_accuracy"]["master.effective_format"]["unverifiable"] == 5

    result["format_accuracy"]["denominator"] = 1
    assert not output_report_is_acceptable(result)

    inconsistent = _report(["pass"] * 95 + ["fail"] * 5)
    inconsistent["passed_count"] = 96
    result = apply_1101_acceptance_contract(inconsistent)
    assert result["acceptance_status"] == "fail"
    assert "检查汇总与明细不一致" in result["hard_blockers"]


def test_layout_evidence_limitations_are_hard_blockers():
    report = _report(["pass"] * 99 + ["fail"])
    report["layout_evidence_limitations"] = ["Word 回执缺少表格逐行页码"]

    result = apply_1101_acceptance_contract(report)

    assert result["format_accuracy"]["percentage"] == 99.0
    assert result["acceptance_status"] == "fail"
    assert any(item.startswith("布局证据不完整:") for item in result["hard_blockers"])
    assert not output_report_is_acceptable(result)


@pytest.mark.parametrize("rule", [
    "master.inline_vertical_alignment",
    "master.highlight_color_distinct",
    "master.digit_heading_anchor",
    "master.table_row_height",
    "master.table_style_row_height",
    "master.table_row_pagination",
    "master.text_color_visibility",
])
@pytest.mark.parametrize("status", ["fail", "unverifiable"])
def test_1101_hard_blockers_cannot_be_hidden_by_a_score_above_ninety_percent(rule, status):
    report = _report(["pass"] * 99 + [status])
    report["checks"][-1]["rule"] = rule
    report["passed_count"] = 99

    result = apply_1101_acceptance_contract(report)

    assert result["format_accuracy"]["percentage"] == 99.0
    assert result["acceptance_status"] == "fail"
    assert result["checks"][-1]["classification"] == "hard_block"
    assert result["hard_blockers"]
    assert not output_report_is_acceptable(result)


def test_1101_report_rejects_unproven_exclusions_and_tampered_rule_scores():
    excluded = _report(["pass"] * 100)
    excluded["excluded_checks"] = [{"rule": "master.effective_format", "object": "p1"}]
    result = apply_1101_acceptance_contract(excluded)
    assert result["acceptance_status"] == "fail"
    assert "排除检查项缺少本版本支持的适用性证明" in result["hard_blockers"]

    report = apply_1101_acceptance_contract(_report(["pass"] * 100))
    report["rule_accuracy"]["master.effective_format"]["denominator"] = 1
    assert not output_report_is_acceptable(report)


def test_only_explicitly_mapped_independent_units_can_use_the_tolerance():
    unmapped = _report(["pass"] * 99 + ["fail"])
    unmapped["checks"][-1].pop("unit")
    unmapped["passed_count"] = 99
    result = apply_1101_acceptance_contract(unmapped)
    assert result["format_accuracy"]["percentage"] == 99.0
    assert result["checks"][-1]["classification"] == "hard_block"
    assert result["acceptance_status"] == "fail"

    table_border = _report(["pass"] * 99 + ["fail"])
    table_border["checks"][-1]["rule"] = "master.table_borders"
    table_border["passed_count"] = 99
    result = apply_1101_acceptance_contract(table_border)
    assert result["checks"][-1]["classification"] == "hard_block"
    assert result["acceptance_status"] == "fail"


def test_authenticated_check_manifest_blocks_removed_rows_and_old_contracts():
    report = apply_1101_acceptance_contract(_report(["pass"] * 100))
    assert output_report_is_acceptable(report)

    report["checks"].pop()
    report["check_count"] -= 1
    report["passed_count"] -= 1
    assert not output_report_is_acceptable(report)

    legacy = apply_1101_acceptance_contract(_report(["pass"] * 100))
    legacy["acceptance_contract"] = "1101-output-acceptance/3"
    assert not output_report_is_acceptable(legacy)

    stripped = apply_1101_acceptance_contract(_report(["pass"] * 100))
    stripped.pop("source_profile_sha256")
    stripped.pop("acceptance_manifest")
    stripped.pop("acceptance_signature")
    assert not output_report_is_acceptable(stripped)


def test_authenticated_check_manifest_blocks_changed_results():
    failed = apply_1101_acceptance_contract(_report(["pass"] * 99 + ["fail"]))
    altered = apply_1101_acceptance_contract(_report(["pass"] * 100))
    # A storage-side modification can rewrite counts and diagnostics too; it
    # must not be able to reuse the original signature with changed results.
    altered["acceptance_manifest"] = failed["acceptance_manifest"]
    altered["acceptance_signature"] = failed["acceptance_signature"]

    assert not output_report_is_acceptable(altered)

    passed = apply_1101_acceptance_contract(_report(["pass"] * 100))
    passed["checks"][0]["actual"] = "altered evidence"
    assert not output_report_is_acceptable(passed)


@pytest.mark.parametrize("field", ["unsupported", "layout_evidence_limitations"])
def test_authenticated_check_manifest_blocks_removed_limitations(field):
    original = _report(["pass"] * 100)
    original[field] = ["missing evidence"]
    failed = apply_1101_acceptance_contract(original)
    altered = apply_1101_acceptance_contract(_report(["pass"] * 100))
    altered["acceptance_manifest"] = failed["acceptance_manifest"]
    altered["acceptance_signature"] = failed["acceptance_signature"]

    assert not output_report_is_acceptable(altered)
