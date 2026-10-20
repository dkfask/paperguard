"""Template writing gates shared by checking, preview and DOCX export."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._helpers import register_fixture
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import MetaField, PaperTemplate, SectionType, TemplateSection
from app.services.project_validation import validate_project_completeness
from app.services.template_compare import compare_project_to_template


def _template():
    return PaperTemplate(
        id="simple-loop", name="最小格式模板",
        sections=[TemplateSection(key="abstract", type=SectionType.ABSTRACT, title="摘要")],
    )


def test_required_cover_field_is_visible_in_template_check():
    template = PaperTemplate(id="cover-template", name="封面模板", meta_fields=[MetaField(key="title", label="论文题目")])
    project = WritingProject(id="cover-paper", template_id=template.id)
    findings, _ = compare_project_to_template(project, template)
    assert any(f.rule_id.endswith("meta.title.missing") and f.location.section_key == "cover" for f in findings)
    project.meta["title"] = "已填写的论文题目"
    assert compare_project_to_template(project, template)[0] == []


@pytest.mark.parametrize("paragraphs", [[], [ParagraphBlock(id="p1", text="  ")]])
def test_empty_required_section_fails_check_and_export_gate(paragraphs):
    template = _template()
    project = WritingProject(
        id="empty-paper", template_id=template.id,
        sections=[ProjectSection(key="abstract", type="abstract", title="摘要", paragraphs=paragraphs)],
    )
    assert validate_project_completeness(project, template)
    findings, _ = compare_project_to_template(project, template)
    assert any(f.rule_id.endswith("abstract.empty") for f in findings)


@pytest.mark.parametrize("sections", [
    [],
    [ProjectSection(key="abstract", type="abstract", title="摘要", required=False)],
    [ProjectSection(key="abstract", type="cover", title="摘要")],
])
def test_template_required_section_cannot_be_removed_or_made_optional(sections):
    template = _template()
    project = WritingProject(id="changed-outline", template_id=template.id, sections=sections)
    assert validate_project_completeness(project, template)


def test_template_check_rejects_required_section_type_mismatch():
    template = _template()
    project = WritingProject(
        id="mismatched-outline", template_id=template.id,
        sections=[ProjectSection(key="abstract", type="body", title="摘要", paragraphs=[ParagraphBlock(id="p1", text="正文")])],
    )
    findings, _ = compare_project_to_template(project, template)
    mismatch = next(finding for finding in findings if finding.rule_id.endswith("abstract.type-mismatch"))
    assert mismatch.severity.value == "error"
    assert mismatch.actual == "body"
    assert mismatch.expected == "abstract"


def test_heading_can_use_real_child_content_but_not_an_empty_child_title():
    template = PaperTemplate(id="chapter-template", name="章节模板")
    project = WritingProject(id="child-paper", template_id=template.id, sections=[
        ProjectSection(key="ch1", type="heading1", title="第一章"),
        ProjectSection(key="ch1_1", type="heading2", title="1.1 背景", required=False),
    ])
    assert validate_project_completeness(project, template)
    project.sections[1].paragraphs = [ParagraphBlock(id="p1", text="已填写的正文。")]
    assert validate_project_completeness(project, template) == []


def test_student_force_flag_cannot_skip_template_gates():
    client = TestClient(app)
    username = f"simple_loop_{uuid4().hex[:12]}"
    password = f"Local-{uuid4().hex}"
    register_fixture(client, username, password, display_name="格式回归学生")
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
    created = client.post("/api/projects", json={"template_id": "default-gb7713", "title": "格式回归论文"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    preview = client.post(f"/api/projects/{project_id}/export-preview?force=true")
    assert preview.status_code == 200
    assert preview.json()["can_export"] is False
    assert preview.json()["force"] is False
    for route in ("export", "export-jobs"):
        response = client.post(f"/api/projects/{project_id}/{route}?force=true")
        assert response.status_code == 409
        assert response.json()["detail"]["missing"]
