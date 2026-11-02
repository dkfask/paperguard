import pytest

from app.api import projects as projects_api
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import MetaField, PaperTemplate, SectionType, TemplateSection, TextStyle
from app.services.project_check import check_project


def _project_and_template():
    template = PaperTemplate(
        id="preflight-template", name="预检测试",
        meta_fields=[MetaField(key="title", label="论文题目", required=True)],
        sections=[
            TemplateSection(key="cover", type=SectionType.COVER, title="封面"),
            TemplateSection(key="body", type=SectionType.BODY, title="正文"),
        ],
    )
    project = WritingProject(
        id="preflight-project", template_id=template.id, meta={"title": "已填写题目"},
        sections=[ProjectSection(key="body", type="body", title="正文", paragraphs=[ParagraphBlock(id="p1", text="内容")])],
    )
    return project, template


def test_manual_hard_requirements_require_artifact_review_but_do_not_block_candidate(monkeypatch):
    project, template = _project_and_template()
    monkeypatch.setattr("app.services.project_check.check_project", lambda *_args, **_kwargs: {
        "findings": [],
        "not_checked": [{"source": "写作规范", "description": "参考文献排序需核查", "enforce": "hard"}],
    })

    blocking, manual_checks = projects_api._export_preflight(project, template, object(), "teacher-1")

    assert blocking == []
    assert manual_checks == ["写作规范: 参考文献排序需核查"]


def test_missing_cover_field_remains_a_preflight_blocker(monkeypatch):
    project, template = _project_and_template()
    project.meta["title"] = ""
    monkeypatch.setattr("app.services.project_check.check_project", lambda *_args, **_kwargs: {"findings": [], "not_checked": []})

    blocking, manual_checks = projects_api._export_preflight(project, template, object(), "teacher-1")

    assert blocking == ["封面「论文题目」未填写"]
    assert manual_checks == []


@pytest.mark.parametrize(
    ("date", "reference_count", "status"),
    [("", 1, "window_unknown"), ("2026年5月", 1, "not_met"), ("2026年5月", 15, "incomplete")],
)
def test_reference_quality_advisory_does_not_block_actual_export_preflight(date, reference_count, status):
    project, template = _project_and_template()
    template.styles["body"] = TextStyle()
    template.meta_fields.append(MetaField(key="date", label="答辩日期", required=False))
    project.meta["date"] = date
    project.sections.append(ProjectSection(
        key="references", type="references", title="参考文献", required=False,
        paragraphs=[ParagraphBlock(id=f"ref-{index}", text=f"待确认的文献 {index}")
                    for index in range(reference_count)],
    ))
    project.requirement_snapshot = {"resolved_items": [{
        "id": "quality", "type": "auto", "target": "references",
        "checker": "references.recent_language_summary",
        "params": {"defense_date_field_key": "date"},
        "severity": "error", "enforce": "hard",
    }]}

    report = check_project(project, template)
    assert report["quality_advisories"][0]["actual"]["status"] == status
    assert projects_api._export_preflight(project, template, None, "student-1") == ([], [])

    project.meta["title"] = ""
    blocking, _ = projects_api._export_preflight(project, template, None, "student-1")
    assert blocking == ["封面「论文题目」未填写"]
