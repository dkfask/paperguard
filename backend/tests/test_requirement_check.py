"""文档级自动检查器的规则覆盖。"""

from app.models.project import ParagraphBlock, ProjectSection, ReferenceEntry, ReferenceLanguageFact, ReferenceYearFact, WritingProject
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
    assert not not_checked

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


def test_reference_list_checker_validates_facts_and_contiguous_list_order():
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
    assert any("编号为 [3]" in result["message"] for result in findings)
    section.paragraphs[1] = ParagraphBlock(id="legacy", text="未结构化的旧文献")
    findings, _ = check_requirements(project, {"resolved_items": [item]})
    assert any("尚未录入结构化文献事实" in result["message"] for result in findings)


def test_markdown_frontmatter_maps_p0_checker_aliases():
    items = items_from_markdown("""---
rules:
  - id: refs-format
    type: reference_format
    target: references
  - id: chapter-break
    type: chapter_page_break
    target: ch1
---
""")
    assert [item.checker for item in items] == ["references.format", "chapter.page_break"]


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
