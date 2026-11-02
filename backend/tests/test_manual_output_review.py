from app.services.manual_output_review import confirm_review, review_matches, review_view


def test_manual_review_receipt_is_bound_to_output_source_template_and_content_hash() -> None:
    report = {
        "office_review_required": True,
        "manual_checks": ["核对最终分页和目录"],
        "output_sha256": "output-a",
        "project_id": "project-a",
        "project_revision": 3,
        "project_content_hash": "content-a",
        "template_id": "template-a",
        "template_version": "1",
        "template_hash": "template-hash-a",
        "source_template_sha256": "source-a",
    }
    checklist = review_view(report, None)["checklist"]
    receipt = confirm_review(
        report,
        output_sha256="output-a",
        confirmed_check_ids=[item["id"] for item in checklist],
        reviewer_id="teacher-1",
    )

    assert receipt["status"] == "confirmed"
    assert review_matches(report, receipt)
    assert review_view(report, receipt)["status"] == "confirmed"

    changed_output = {**report, "output_sha256": "output-b"}
    assert not review_matches(changed_output, receipt)
    changed_source = {**report, "source_template_sha256": "source-b"}
    assert not review_matches(changed_source, receipt)
    changed_content = {**report, "project_content_hash": "content-b"}
    assert not review_matches(changed_content, receipt)


def test_office_review_without_explicit_checklist_gets_a_required_fallback() -> None:
    report = {"office_review_required": True, "output_sha256": "output-a"}
    checklist = review_view(report, None)["checklist"]
    assert len(checklist) == 1
    assert "Word/WPS" in checklist[0]["description"]


def test_review_confirmation_rejects_partial_or_wrong_output_acknowledgement() -> None:
    report = {
        "office_review_required": True,
        "manual_checks": ["分页", "目录"],
        "output_sha256": "output-a",
    }
    checklist = review_view(report, None)["checklist"]
    try:
        confirm_review(
            report,
            output_sha256="output-other",
            confirmed_check_ids=[checklist[0]["id"], checklist[1]["id"]],
            reviewer_id="teacher-1",
        )
    except ValueError as exc:
        assert "摘要" in str(exc)
    else:
        raise AssertionError("wrong output digest must not receive a human review receipt")

    try:
        confirm_review(
            report,
            output_sha256="output-a",
            confirmed_check_ids=[checklist[0]["id"]],
            reviewer_id="teacher-1",
        )
    except ValueError as exc:
        assert "全部" in str(exc)
    else:
        raise AssertionError("partial checklist must not complete human review")
