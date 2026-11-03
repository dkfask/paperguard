"""模板 + 写作项目 + docx 导出 API 测试。

覆盖:
- 模板: 列表 / 详情 / 创建 / 更新 / 删除 / 内置模板种子
- 项目: 基于模板创建(章节树实例化) / 章节内容保存 / 元信息 / 导出
"""

from __future__ import annotations

import hashlib
import uuid
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from docx import Document

from app.db import Store
from app.main import app
from app.models.template import Align, PaperTemplate, TextStyle
from app.services.docx_output_check import inspect_docx_output
from app.services.template_source import resolve_template_source, save_template_source
from tests._helpers import register_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

client = TestClient(app)

# 登录内置管理员, 所有 API 请求带 token (TestClient 创建时触发 startup seed)
_login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
assert _login.status_code == 200, _login.text
client.headers.update({"Authorization": f"Bearer {_login.json()['token']}"})


def _render_project_directly_for_renderer_assertion(project_id: str, output_path: Path):
    """Inspect renderer output without treating it as a downloadable API artifact."""
    import docx as docx_lib

    from app.core.export.docx_exporter import export_project_to_docx

    store = Store()
    project = store.get_project(project_id)
    assert project is not None
    if project.template_snapshot:
        template = PaperTemplate.model_validate(project.template_snapshot)
    else:
        template = store.get_template(project.template_id)
    assert template is not None
    export_project_to_docx(
        project,
        template,
        str(output_path),
        template_path=resolve_template_source(template.source_docx),
    )
    return docx_lib.Document(output_path)


@pytest.fixture(scope="module")
def store():
    return Store(":memory:")


def test_default_template_seeded():
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    templates = resp.json()
    ids = [t["id"] for t in templates]
    assert "default-gb7713" in ids, f"内置模板应已种子化: {ids}"


def test_template_admission_is_read_only_and_blocks_unsupported_template_use():
    from app.services.projects import default_template

    template_id = f"admission-{uuid.uuid4().hex[:10]}"
    payload = {
        **default_template().model_dump(mode="json"),
        "id": template_id,
        "name": "含不支持封面表格的模板",
        "cover_blocks": [{"kind": "table", "fields": []}],
    }
    first = client.post("/api/templates/admission/assess", json=payload)
    assert first.status_code == 200, first.text
    modified = {**payload, "description": "修改后定义"}
    second = client.post("/api/templates/admission/assess", json=modified)
    assert second.status_code == 200, second.text
    assert first.json()["assessment_hash"] != second.json()["assessment_hash"]
    assert client.get(f"/api/templates/{template_id}").status_code == 404

    saved = client.post("/api/templates", json=payload)
    assert saved.status_code == 201, saved.text
    listed = next(item for item in client.get("/api/templates").json() if item["id"] == template_id)
    assert listed["admission"]["status"] == "blocked"
    blocked = client.post("/api/projects", json={"template_id": template_id, "title": "不可创建"})
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["code"] == "template_admission_blocked"
    assert any(item["code"] == "cover_table" for item in detail["admission"]["blockers"])


def test_template_detail():
    resp = client.get("/api/templates/default-gb7713")
    assert resp.status_code == 200
    tpl = resp.json()
    assert tpl["name"].startswith("学位论文")
    assert tpl["sections"][0]["type"] == "cover"
    assert "heading1" in tpl["styles"]
    assert any(f["key"] == "author" for f in tpl["meta_fields"])


def test_create_and_update_template():
    resp = client.post("/api/templates", json={
        "id": "test-tpl",
        "name": "测试模板",
        "styles": {},
        "sections": [
            {"key": "cover", "type": "cover", "title": "封面"},
            {"key": "ch1", "type": "heading1", "title": "第一章",
             "children": [{"key": "ch1_1", "type": "heading2", "title": "1.1"}]},
        ],
        "meta_fields": [],
    })
    assert resp.status_code == 201
    # 更新
    resp = client.put("/api/templates/test-tpl", json={
        "id": "test-tpl",
        "name": "测试模板-改",
        "styles": {},
        "sections": [{"key": "cover", "type": "cover", "title": "封面"}],
        "meta_fields": [],
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "测试模板-改"
    # 删除
    assert client.delete("/api/templates/test-tpl").status_code == 204


def test_create_template_generates_id_when_omitted():
    resp = client.post("/api/templates", json={
        "id": "",
        "name": "导入模板测试",
        "styles": {},
        "sections": [{"key": "cover", "type": "cover", "title": "封面"}],
        "meta_fields": [],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"].startswith("tpl-")
def test_bilingual_abstract_keywords_project_sections():
    payload = {
        "id": "bilingual-tpl",
        "name": "双语模板",
        "styles": {},
        "sections": [
            {"key": "cover", "type": "cover", "title": "封面"},
            {"key": "abstract", "type": "abstract", "title": "摘 要"},
            {"key": "english_abstract", "type": "english_abstract", "title": "Abstract"},
            {"key": "keywords", "type": "keywords", "title": "关键词"},
            {"key": "english_keywords", "type": "english_keywords", "title": "Key Words"},
            {"key": "toc", "type": "toc", "title": "目录"},
            {"key": "ch1", "type": "heading1", "title": "第一章"},
        ],
        "meta_fields": [],
    }
    created = client.post("/api/templates", json=payload)
    assert created.status_code == 201, created.text
    project = client.post("/api/projects", json={"template_id": "bilingual-tpl", "title": "双语论文"})
    assert project.status_code == 201, project.text
    keys = [section["key"] for section in project.json()["sections"]]
    assert "abstract" in keys
    assert "english_abstract" in keys
    assert "keywords" in keys
    assert "english_keywords" in keys
    assert "toc" not in keys


def _create_project_instantiates_sections():
    resp = client.post("/api/projects", json={
        "template_id": "default-gb7713", "title": "我的论文",
    })
    assert resp.status_code == 201
    proj = resp.json()
    assert proj["title"] == "我的论文"
    keys = [s["key"] for s in proj["sections"]]
    assert "cover" not in keys
    assert "abstract" in keys and "ch1" in keys and "ch1_1" in keys
    abstract = next(s for s in proj["sections"] if s["key"] == "abstract")
    assert abstract["paragraphs"]
    assert "author" in proj["meta"]
    return proj["id"]


def _create_supported_scope_project() -> tuple[str, str]:
    """Create a real, clean-source template limited to the first-phase profile."""
    suffix = uuid.uuid4().hex[:10]
    source = Document()
    source.sections[0].footer.paragraphs  # Intentionally empty: this sample has no page-number field.
    source_bytes = BytesIO()
    source.save(source_bytes)
    source_name = save_template_source(source_bytes.getvalue())
    template_id = f"supported-export-{suffix}"
    style = TextStyle(
        font_zh="宋体", font_en="Times New Roman", size_pt=12,
        align=Align.JUSTIFY, line_spacing=1.5, first_line_indent_chars=2,
    )
    created_template = client.post("/api/templates", json={
        "id": template_id,
        "name": "第一阶段纯正文支持范围样稿",
        "source_docx": source_name,
        "styles": {"body": style.model_dump(mode="json")},
        "sections": [{"key": "body", "type": "body", "title": "正文"}],
        "meta_fields": [],
    })
    assert created_template.status_code == 201, created_template.text
    created_project = client.post("/api/projects", json={"template_id": template_id, "title": "支持范围样稿"})
    assert created_project.status_code == 201, created_project.text
    project_id = created_project.json()["id"]
    updated = client.put(f"/api/projects/{project_id}/section/body", json={
        "title": "正文",
        "paragraphs": [{"id": "body-sample", "text": "本样稿仅使用第一阶段支持的纯文本正文格式。"}],
    })
    assert updated.status_code == 200, updated.text
    return project_id, template_id


def _create_full_default_gb7713_project(headers: dict[str, str] | None = None) -> str:
    """Populate the actual built-in paper structure, including front matter and body."""
    created = client.post("/api/projects", json={
        "template_id": "default-gb7713",
        "title": "格式一致性研究",
    }, headers=headers)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    meta = client.put(f"/api/projects/{project_id}/meta", json={
        "meta": {
            "school": "示例大学", "title": "格式一致性研究", "author": "张同学",
            "student_id": "20261001", "major": "计算机科学与技术",
            "advisor": "李老师", "date": "2026年10月",
        },
    }, headers=headers)
    assert meta.status_code == 200, meta.text
    contents = {
        "abstract": "本文研究论文格式检查与修复方法。",
        "english_abstract": "This thesis studies document format verification.",
        "keywords": "关键词：论文格式；一致性检查",
        "english_keywords": "Key Words: document format; consistency",
        "ch1": "本章介绍研究背景和目标。",
        "ch1_1": "研究背景说明问题来源。",
        "ch1_2": "相关研究提供方法依据。",
        "ch1_3": "本文工作包括检查、诊断与验证。",
        "ch2": "本章介绍系统采用的技术。",
        "ch2_1": "技术概述说明系统边界。",
        "ch2_2": "关键技术支撑格式检查。",
        "ch3": "本章给出系统设计。",
        "ch4": "本章说明系统实现与测试。",
        "ch5": "本章总结研究并讨论后续工作。",
        "references": "[1] 示例作者. 文档格式研究. 2026.",
        "acknowledgement": "感谢指导教师的帮助。",
    }
    revision = meta.json()["revision"]
    for section in meta.json()["sections"]:
        updated = client.put(f"/api/projects/{project_id}/section/{section['key']}", json={
            "base_revision": revision,
            "title": section["title"],
            "paragraphs": [{"id": f"{section['key']}-sample", "text": contents[section["key"]]}],
        }, headers=headers)
        assert updated.status_code == 200, updated.text
        revision = updated.json()["revision"]
    return project_id


def test_create_project_instantiates_sections():
    """创建项目时应实例化模板章节树。"""
    _create_project_instantiates_sections()


def test_export_preview_reports_revision_hash_and_content_counts():
    project_id = _create_project_instantiates_sections()
    response = client.post(f"/api/projects/{project_id}/export-preview")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["project_id"] == project_id
    assert preview["revision"] >= 0
    assert len(preview["content_hash"]) == 64
    assert preview["counts"]["sections"] > 0
    assert preview["can_export"] is False
    forced = client.post(f"/api/projects/{project_id}/export-preview?force=true")
    assert forced.status_code == 200 and forced.json()["can_export"] is False
    assert forced.json()["missing"] == preview["missing"]


def test_update_section_content():
    proj_id = _create_project_instantiates_sections()
    # 给摘要节写内容
    resp = client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "title": "摘要",
        "paragraphs": [
            {"id": "abs-p1", "text": "本文研究了一个问题。", "bold": False,
             "italic": False, "underline": False},
            {"id": "abs-p2", "text": "实验结果表明有效。", "bold": True,
             "italic": False, "underline": False},
        ],
    })
    assert resp.status_code == 200
    abstract = next(s for s in resp.json()["sections"] if s["key"] == "abstract")
    assert len(abstract["paragraphs"]) == 2
    assert abstract["paragraphs"][1]["bold"] is True


def test_section_revision_conflict_does_not_overwrite():
    """旧编辑器快照不能覆盖已经保存的新内容。"""
    proj_id = _create_project_instantiates_sections()
    initial = client.get(f"/api/projects/{proj_id}").json()
    assert initial["revision"] == 0

    first = client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "base_revision": 0,
        "title": "第一版摘要",
        "paragraphs": [{"id": "p1", "text": "第一版内容"}],
    })
    assert first.status_code == 200
    assert first.json()["revision"] == 1

    stale = client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "base_revision": 0,
        "title": "过期版本",
        "paragraphs": [{"id": "p1", "text": "不应覆盖"}],
    })
    assert stale.status_code == 409
    current = client.get(f"/api/projects/{proj_id}").json()
    abstract = next(s for s in current["sections"] if s["key"] == "abstract")
    assert abstract["title"] == "第一版摘要"
    assert abstract["paragraphs"][0]["text"] == "第一版内容"


def test_project_update_cannot_change_owner_or_template():
    """项目更新 DTO 不允许客户端篡改归属和模板绑定。"""
    proj_id = _create_project_instantiates_sections()
    before = client.get(f"/api/projects/{proj_id}").json()
    resp = client.put(f"/api/projects/{proj_id}", json={
        "title": "新标题",
        "owner_id": "other-user",
        "template_id": "other-template",
        "sections": [],
    })
    assert resp.status_code == 200
    after = resp.json()
    assert after["title"] == "新标题"
    assert after["owner_id"] == before["owner_id"]
    assert after["template_id"] == before["template_id"]
    assert after["sections"] == before["sections"]


def test_update_meta():
    proj_id = _create_project_instantiates_sections()
    resp = client.put(f"/api/projects/{proj_id}/meta", json={
        "meta": {"author": "张三", "title": "基于XX的研究"},
    })
    assert resp.status_code == 200
    assert resp.json()["meta"]["author"] == "张三"


def test_export_docx():
    proj_id = _create_project_instantiates_sections()
    client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "title": "摘要",
        "paragraphs": [{"id": "p1", "text": "这是摘要内容。"}],
    })
    client.put(f"/api/projects/{proj_id}/meta", json={
        "meta": {"author": "张三", "title": "论文题目"},
    })
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["missing"]
    assert any("第二章" in item for item in detail["missing"])
    assert b"PK" not in resp.content


def test_supported_scope_template_sync_export_is_downloadable():
    project_id, template_id = _create_supported_scope_project()
    try:
        response = client.post(f"/api/projects/{project_id}/export?force=true")
        assert response.status_code == 200, response.text
        assert response.content[:2] == b"PK"
        assert response.headers["x-paperguard-output-check"] == "pass"
        assert int(response.headers["x-paperguard-output-check-count"]) > 0
        assert response.headers["x-paperguard-output-sha256"] == hashlib.sha256(response.content).hexdigest()
    finally:
        client.delete(f"/api/templates/{template_id}")


def test_default_gb7713_complete_paper_sync_export_is_downloadable():
    from docx import Document

    project_id = _create_full_default_gb7713_project()
    response = client.post(f"/api/projects/{project_id}/export?force=true")

    assert response.status_code == 200, response.text
    assert response.headers["x-paperguard-output-check"] == "pass"
    assert int(response.headers["x-paperguard-output-check-count"]) >= 100
    document = Document(BytesIO(response.content))
    assert len(document.sections) == 3
    assert "学校名称：示例大学" in [paragraph.text for paragraph in document.paragraphs]
    assert "目录" in [paragraph.text for paragraph in document.paragraphs]
    assert "第一章 绪论" in [paragraph.text for paragraph in document.paragraphs]
    assert "参考文献" in [paragraph.text for paragraph in document.paragraphs]
    assert "TOC" in document.element.body.xml
    assert all("PAGE" in section.footer._element.xml for section in document.sections[1:])


@pytest.mark.parametrize(
    ("mutation", "expected_rule"),
    [
        ("cover_field", "cover.field_content"),
        ("toc_field", "structure.toc_field"),
        ("page_format", "section.page_number_format"),
        ("page_field", "section.page_field"),
        ("page_layout", "page.margin_right_cm"),
        ("heading_style", "structure.heading_semantics"),
    ],
)
def test_default_gb7713_sync_export_blocks_corrupted_static_structure(monkeypatch, mutation, expected_rule):
    from app.api import projects as projects_api
    from docx import Document
    from docx.oxml.ns import qn

    project_id = _create_full_default_gb7713_project()
    real_export = projects_api.export_project_to_docx

    def mutate_output(project, template, out_path, **kwargs):
        real_export(project, template, out_path, **kwargs)
        document = Document(out_path)
        if mutation == "cover_field":
            cover = next(item for item in document.paragraphs if item.text.startswith("学校名称："))
            cover.runs[0].text = "院校名称："
        elif mutation == "toc_field":
            instruction = next(item for item in document.element.body.iter() if item.tag == qn("w:instrText") and "TOC" in (item.text or ""))
            instruction.text = 'TOC \\o "1-2" \\h \\z \\u'
        elif mutation == "page_format":
            document.sections[1]._sectPr.find(qn("w:pgNumType")).set(qn("w:fmt"), "decimal")
        elif mutation == "page_field":
            page_field = next(item for item in document.sections[1].footer._element.iter() if item.tag == qn("w:fldSimple"))
            page_field.getparent().remove(page_field)
        elif mutation == "page_layout":
            document.sections[2]._sectPr.find(qn("w:pgMar")).set(qn("w:right"), "700")
        else:
            next(item for item in document.paragraphs if item.text == "第一章 绪论").style = "Normal"
        document.save(out_path)

    monkeypatch.setattr(projects_api, "export_project_to_docx", mutate_output)
    response = client.post(f"/api/projects/{project_id}/export?force=true")

    assert response.status_code == 409
    assert expected_rule in {item["rule"] for item in response.json()["detail"]["output_check"]["findings"]}
    assert b"PK" not in response.content


def test_sync_export_blocks_second_run_format_mutation_even_with_force(monkeypatch):
    from app.api import projects as projects_api
    from docx import Document

    project_id = _create_full_default_gb7713_project()
    updated = client.put(f"/api/projects/{project_id}/section/ch1", json={
        "title": "第一章",
        "paragraphs": [{"id": "multi-run", "text": "甲乙", "inline_runs": [{"text": "甲"}, {"text": "乙"}]}],
    })
    assert updated.status_code == 200, updated.text
    real_export = projects_api.export_project_to_docx

    def mutate_second_run(project, template, out_path, **kwargs):
        real_export(project, template, out_path, **kwargs)
        document = Document(out_path)
        paragraph = next(item for item in document.paragraphs if item.text == "甲乙")
        paragraph.runs[1].font.name = "Courier New"
        paragraph.runs[1].font.size = 10
        document.save(out_path)

    monkeypatch.setattr(projects_api, "export_project_to_docx", mutate_second_run)
    response = client.post(f"/api/projects/{project_id}/export?force=true")

    assert response.status_code == 409
    diagnostics = response.json()["detail"]["output_check"]
    assert diagnostics["status"] == "fail"
    assert any(item["rule"] in {"format.font_ascii", "format.font_size"} and item["status"] == "fail" for item in diagnostics["findings"])
    assert b"PK" not in response.content


@pytest.mark.parametrize("failure", ["zero-checks", "checker-exception"])
def test_sync_export_blocks_empty_or_crashed_checker(monkeypatch, failure):
    from app.services import docx_output_check

    project_id = _create_full_default_gb7713_project()
    if failure == "zero-checks":
        monkeypatch.setattr(docx_output_check, "inspect_docx_output", lambda *_args, **_kwargs: {
            "status": "pass", "checks": [], "check_count": 0, "passed_count": 0,
        })
    else:
        def crash(*_args, **_kwargs):
            raise RuntimeError("synthetic checker crash")
        monkeypatch.setattr(docx_output_check, "inspect_docx_output", crash)

    response = client.post(f"/api/projects/{project_id}/export?force=true")

    assert response.status_code == 409
    diagnostics = response.json()["detail"]["output_check"]
    assert diagnostics["status"] == "unverifiable"
    assert diagnostics["findings"]
    assert b"PK" not in response.content


def test_sync_export_missing_required_title_and_body_fails_even_with_force():
    project_id = _create_project_instantiates_sections()
    project = Store().get_project(project_id)
    abstract = next(section for section in project.sections if section.key == "abstract")
    abstract.title = ""
    abstract.paragraphs = []
    Store().save_project(project)

    response = client.post(f"/api/projects/{project_id}/export?force=true")

    assert response.status_code == 409
    missing = response.json()["detail"]["missing"]
    assert missing
    assert any("abstract" in item or "摘要" in item for item in missing)
    assert b"PK" not in response.content


def test_export_missing_project_404():
    assert client.post("/api/projects/nonexist/export").status_code == 404


def test_durable_project_export_job_roundtrip(monkeypatch):
    """DOCX export can be queued, recovered by the worker, and downloaded safely."""
    from app.services.export_jobs import EXPORT_DIR
    from app.worker import run_once

    project_id = _create_full_default_gb7713_project()
    template_id = ""
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()
    assert task["status"] == "queued"
    assert task["project_id"] == project_id
    assert "project_snapshot" not in task and "file_path" not in task
    duplicate = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == task["id"]

    job = client.get(f"/api/jobs/{task['job_id']}")
    assert job.status_code == 200
    assert job.json()["resource_type"] == "project_export"
    assert "lease_token" not in job.json()

    result = run_once()
    assert task["job_id"] in result["exports"]["succeeded"]
    completed = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["output_check_status"] == "pass"
    assert completed.json()["output_check_count"] > 0
    assert completed.json()["is_current_for_project"] is True
    assert "download_url" in completed.json()
    saved_export = Store().get_workflow_record("project_export", task["id"])
    assert saved_export["output_check"]["output_sha256"]
    assert saved_export["output_check"]["project_content_hash"] == saved_export["content_hash"]
    downloaded = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content[:2] == b"PK"
    from app.services import word_optional_pdf
    pdf = b"%PDF-1.4 test conversion result"
    pdf_url = f"/api/projects/{project_id}/export-jobs/{task['id']}/download?format=pdf"
    monkeypatch.setattr(word_optional_pdf, "word_pdf_available", lambda: True)
    monkeypatch.setattr(word_optional_pdf, "create_optional_pdf", lambda data: (pdf, {
        "source_sha256": hashlib.sha256(data).hexdigest(), "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
    }))
    optional_pdf = client.get(pdf_url)
    assert optional_pdf.status_code == 200, optional_pdf.text
    assert optional_pdf.content == pdf
    assert optional_pdf.headers["x-paperguard-source-sha256"] == hashlib.sha256(downloaded.content).hexdigest()
    assert optional_pdf.headers["x-paperguard-pdf-sha256"] == hashlib.sha256(pdf).hexdigest()
    assert optional_pdf.headers["cache-control"] == "private, no-store"
    monkeypatch.setattr(word_optional_pdf, "word_pdf_available", lambda: False)
    unavailable_pdf = client.get(pdf_url)
    assert unavailable_pdf.status_code == 503
    assert client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download").status_code == 200
    monkeypatch.setattr(word_optional_pdf, "word_pdf_available", lambda: True)
    output = EXPORT_DIR / f"{task['id']}.docx"
    original_output = output.read_bytes()
    saved_export = Store().get_workflow_record("project_export", task["id"])
    original_report = saved_export["output_check"]

    other_project_id = _create_full_default_gb7713_project()
    changed_other_project = client.put(f"/api/projects/{other_project_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [{"id": "other-body", "text": "这是另一份具有不同内容的合规正文样稿。"}],
    })
    assert changed_other_project.status_code == 200, changed_other_project.text
    other_response = client.post(f"/api/projects/{other_project_id}/export?force=true")
    assert other_response.status_code == 200, other_response.text
    other_project = Store().get_project(other_project_id)
    other_template = PaperTemplate.model_validate(other_project.template_snapshot)
    from app.services.docx_output_check import inspect_docx_output
    other_source = resolve_template_source(other_template.source_docx)
    other_report = inspect_docx_output(
        other_response.content,
        other_project,
        other_template,
        source_template_bytes=other_source.read_bytes() if other_source else None,
    )
    assert other_report["status"] == "pass"
    assert other_report["project_id"] != project_id

    # A valid report from another project cannot authorize this task's artifact.
    saved_export["output_check"] = other_report
    saved_export = Store().save_workflow_record("project_export", saved_export, expected_revision=saved_export["revision"])
    cross_project_report = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert cross_project_report.status_code == 409
    saved_export["output_check"] = original_report
    saved_export = Store().save_workflow_record("project_export", saved_export, expected_revision=saved_export["revision"])

    # Matching bytes from another valid export still fail the stored output digest.
    output.write_bytes(other_response.content)
    cross_project_output = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert cross_project_output.status_code == 409
    output.write_bytes(original_output)

    saved_export = Store().get_workflow_record("project_export", task["id"])
    original_report = saved_export["output_check"]
    original_template_snapshot = saved_export["template_snapshot"]
    saved_export["output_check"] = {**original_report, "project_id": "another-task-project"}
    saved_export = Store().save_workflow_record("project_export", saved_export, expected_revision=saved_export["revision"])
    wrong_task_report = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert wrong_task_report.status_code == 409

    saved_export["output_check"] = {key: value for key, value in original_report.items() if key != "template_hash"}
    saved_export = Store().save_workflow_record("project_export", saved_export, expected_revision=saved_export["revision"])
    missing_identity = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert missing_identity.status_code == 409

    saved_export["output_check"] = original_report
    saved_export["template_snapshot"] = {**saved_export["template_snapshot"], "version": "stale-template-version"}
    saved_export = Store().save_workflow_record("project_export", saved_export, expected_revision=saved_export["revision"])
    changed_snapshot = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert changed_snapshot.status_code == 409
    changed_view = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}").json()
    assert changed_view["is_current_for_project"] is False
    assert "download_url" not in changed_view

    saved_export["template_snapshot"] = original_template_snapshot
    saved_export["output_check"] = original_report
    saved_export = Store().save_workflow_record("project_export", saved_export, expected_revision=saved_export["revision"])
    output.write_bytes(original_output + b"tampered")
    rejected = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert rejected.status_code == 409
    assert client.get(pdf_url).status_code == 409
    output.write_bytes(original_output)
    # The identity/digest negative cases above ran against a current project.
    # Only after restoring valid evidence, verify that editing invalidates it.
    assert client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download").status_code == 200
    edited = client.put(f"/api/projects/{project_id}/section/ch1", json={
        "title": "第一章 绪论", "paragraphs": [{"id": "body-sample", "text": "已修改的当前论文，需要重新导出。"}],
    })
    assert edited.status_code == 200
    historical = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert historical.status_code == 409
    assert client.get(pdf_url).status_code == 409
    assert "论文内容已变化" in historical.text
    stale_views = [client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}").json(),
                   next(item for item in client.get(f"/api/projects/{project_id}/export-jobs").json()
                        if item["id"] == task["id"])]
    for stale_view in stale_views:
        assert stale_view["is_current_for_project"] is False
        assert "download_url" not in stale_view and "review_candidate_url" not in stale_view
    audit = client.get("/api/audit", params={"resource_type": "project_export"})
    assert audit.status_code == 200
    assert {item["action"] for item in audit.json() if item["resource_id"] == task["id"]} >= {
        "project.export.queued", "project.export.succeeded"
    }
    if output.exists():
        output.unlink()
    if template_id:
        client.delete(f"/api/templates/{template_id}")


def test_reference_numbers_remain_stable_through_api_reordering_and_deletion():
    project_id = _create_full_default_gb7713_project()
    project = client.get(f"/api/projects/{project_id}").json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    first = refs["paragraphs"][0]
    second = {"id": "new-reference", "type": "paragraph", "text": "", "reference": {
        "kind": "book", "authors": "测试作者", "title": "第二本文献", "year": "2026",
    }}
    saved = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project["revision"], "paragraphs": [first, second],
    })
    assert saved.status_code == 200, saved.text
    project = saved.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    assert [block["reference_number"] for block in refs["paragraphs"]] == [1, 2]
    saved = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project["revision"], "paragraphs": list(reversed(refs["paragraphs"])),
    })
    assert saved.status_code == 200, saved.text
    project = saved.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    assert [block["reference_number"] for block in refs["paragraphs"]] == [2, 1]
    saved = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project["revision"], "paragraphs": [refs["paragraphs"][0], {
            "id": "third-reference", "text": "第三本文献. 2026.",
        }],
    })
    assert saved.status_code == 200, saved.text
    project = saved.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    assert [block["reference_number"] for block in refs["paragraphs"]] == [2, 3]
    refs["paragraphs"][1]["reference_number"] = 1
    rejected = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project["revision"], "paragraphs": refs["paragraphs"],
    })
    assert rejected.status_code == 422, rejected.text
    assert client.get(f"/api/projects/{project_id}").json()["revision"] == project["revision"]


def test_pasted_reference_round_trip_and_unparsed_export_gate():
    project_id = _create_full_default_gb7713_project()
    project = client.get(f"/api/projects/{project_id}").json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    pasted = {
        "id": "pasted-reference", "type": "paragraph", "text": "", "reference": {
            "kind": "unparsed", "unformatted_text": "[1] 复制的原文", "original_pasted_text": "[1] 复制的原文",
        },
    }
    response = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project["revision"], "paragraphs": [*refs["paragraphs"], pasted],
    })
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    saved = refs["paragraphs"][-1]
    assert saved["reference_number"] == 2
    assert saved["reference"]["original_pasted_text"] == "[1] 复制的原文"

    for path in ("export", "export-jobs"):
        result = client.post(f"/api/projects/{project_id}/{path}?force=true")
        assert result.status_code == 409, result.text
        assert "[2]" in str(result.json())

    saved["reference"].update({"kind": "journal", "unformatted_text": "编辑后的文本", "original_pasted_text": "伪造原文"})
    response = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project["revision"], "paragraphs": refs["paragraphs"],
    })
    assert response.status_code == 200, response.text
    reloaded = client.get(f"/api/projects/{project_id}").json()
    saved = next(section for section in reloaded["sections"] if section["key"] == "references")["paragraphs"][-1]
    assert saved["reference"]["original_pasted_text"] == "[1] 复制的原文"
    assert saved["reference"]["unformatted_text"] == "编辑后的文本"
    saved["reference"].update({"authors": "张三", "title": "题名", "source": "期刊", "year": "2024"})
    refs = next(section for section in reloaded["sections"] if section["key"] == "references")
    response = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": reloaded["revision"], "paragraphs": refs["paragraphs"],
    })
    assert response.status_code == 200, response.text
    for path in ("export", "export-jobs"):
        result = client.post(f"/api/projects/{project_id}/{path}?force=true")
        assert result.status_code == 409, f"unexpected status {result.status_code}"
        assert "自动核验" in str(result.json())


def test_local_recovery_preserves_pasted_reference_original():
    project_id = _create_full_default_gb7713_project()
    project = client.get(f"/api/projects/{project_id}").json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    pasted = {
        "id": "recovered-reference", "type": "paragraph", "text": "", "reference": {
            "kind": "unparsed", "unformatted_text": "[1] 原始粘贴文本", "original_pasted_text": "[1] 原始粘贴文本",
        },
    }
    saved = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project["revision"], "paragraphs": [*refs["paragraphs"], pasted],
    })
    assert saved.status_code == 200, saved.text
    project = saved.json()
    snapshot = {key: deepcopy(project[key]) for key in ("title", "meta", "sections")}
    recovered = next(section for section in snapshot["sections"] if section["key"] == "references")["paragraphs"][-1]
    recovered["reference"].update({"kind": "journal", "title": "恢复的题名", "original_pasted_text": "被覆盖的原文"})

    response = client.post(f"/api/projects/{project_id}/restore-local", json={
        "base_revision": project["revision"], "snapshot": snapshot,
    })
    assert response.status_code == 200, response.text
    project = response.json()
    recovered = next(section for section in project["sections"] if section["key"] == "references")["paragraphs"][-1]
    assert recovered["reference"]["original_pasted_text"] == "[1] 原始粘贴文本"
    assert recovered["reference"]["title"] == "恢复的题名"

    for replacement in ({"reference": None}, {"type": "image", "reference": None, "image": {}}):
        snapshot = {key: deepcopy(project[key]) for key in ("title", "meta", "sections")}
        recovered = next(section for section in snapshot["sections"] if section["key"] == "references")["paragraphs"][-1]
        recovered.update(replacement)
        response = client.post(f"/api/projects/{project_id}/restore-local", json={
            "base_revision": project["revision"], "snapshot": snapshot,
        })
        assert response.status_code == 422, response.text
        assert client.get(f"/api/projects/{project_id}").json()["revision"] == project["revision"]


@pytest.mark.parametrize("restore_local", [False, True], ids=["section-save", "local-recovery"])
def test_legacy_reference_conversion_preserves_server_original(restore_local):
    project_id = _create_full_default_gb7713_project()
    project = client.get(f"/api/projects/{project_id}").json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    legacy = refs["paragraphs"][0]
    original = legacy["text"]
    legacy.update({"text": "", "reference": {
        "kind": "journal", "title": "调整后的题名", "unformatted_text": "本地编辑后的文字",
        "original_pasted_text": "客户端伪造的原文",
    }})

    if restore_local:
        snapshot = {key: deepcopy(project[key]) for key in ("title", "meta", "sections")}
        response = client.post(f"/api/projects/{project_id}/restore-local", json={
            "base_revision": project["revision"], "snapshot": snapshot,
        })
    else:
        response = client.put(f"/api/projects/{project_id}/section/references", json={
            "base_revision": project["revision"], "paragraphs": refs["paragraphs"],
        })
    assert response.status_code == 200, response.text
    reloaded = client.get(f"/api/projects/{project_id}").json()
    reference = next(section for section in reloaded["sections"] if section["key"] == "references")["paragraphs"][0]["reference"]
    assert reference["original_pasted_text"] == original
    assert reference["unformatted_text"] == "本地编辑后的文字"


def test_reference_language_confirmation_is_bound_to_server_actor_and_source():
    project_id = _create_full_default_gb7713_project()
    project = client.get(f"/api/projects/{project_id}").json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    refs["paragraphs"].append({
        "id": "language-ref", "type": "paragraph", "text": "", "reference": {
            "kind": "journal", "authors": "张三", "title": "An English Title", "year": "2024",
            "language_fact": {"languages": ["en"], "evidence": "来源全文", "status": "confirmed",
                              "confirmed_by": "forged", "confirmed_at": "forged"},
        },
    })

    def save(current, paragraphs):
        return client.put(f"/api/projects/{project_id}/section/references", json={
            "base_revision": current["revision"], "paragraphs": paragraphs,
        })

    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    reference = refs["paragraphs"][-1]["reference"]
    assert reference["language_fact"]["status"] == "unconfirmed"
    assert reference["language_fact"]["confirmed_by"] == ""
    reference["language_confirmation_requested"] = True
    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    fact = refs["paragraphs"][-1]["reference"]["language_fact"]
    assert fact["languages"] == ["en"]
    assert fact["status"] == "confirmed"
    assert fact["confirmed_by"] and fact["confirmed_by"] != "forged"
    assert fact["confirmed_at"] and fact["confirmed_at"] != "forged"

    # Old clients do not know the language field and must not erase it.
    refs["paragraphs"][-1]["reference"].pop("language_fact")
    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    assert refs["paragraphs"][-1]["reference"]["language_fact"] == fact

    # A different source cannot reuse an earlier confirmation.
    refs["paragraphs"][-1]["reference"]["title"] = "另一篇文献"
    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    assert refs["paragraphs"][-1]["reference"]["language_fact"]["status"] == "unconfirmed"
    assert refs["paragraphs"][-1]["reference"]["language_fact"]["confirmed_by"] == ""

    snapshot = {key: deepcopy(project[key]) for key in ("title", "meta", "sections")}
    restored = next(section for section in snapshot["sections"] if section["key"] == "references")["paragraphs"][-1]
    restored["reference"]["language_fact"].update({"status": "confirmed", "confirmed_by": "forged", "confirmed_at": "forged"})
    response = client.post(f"/api/projects/{project_id}/restore-local", json={
        "base_revision": project["revision"], "snapshot": snapshot,
    })
    assert response.status_code == 200, response.text
    restored_fact = next(section for section in response.json()["sections"] if section["key"] == "references")["paragraphs"][-1]["reference"]["language_fact"]
    assert restored_fact["status"] == "unconfirmed"
    assert restored_fact["confirmed_by"] == ""


def test_reference_year_confirmation_survives_old_clients_and_rejects_forged_recovery():
    project_id = _create_full_default_gb7713_project()
    project = client.get(f"/api/projects/{project_id}").json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    refs["paragraphs"].append({
        "id": "year-ref", "type": "paragraph", "text": "", "reference": {
            "kind": "journal", "authors": "张三", "title": "题名", "year": "2024",
            "year_fact": {"value": "2024", "evidence": "期刊首页", "status": "confirmed",
                          "confirmed_by": "forged", "confirmed_at": "forged"},
        },
    })

    def save(current, paragraphs):
        return client.put(f"/api/projects/{project_id}/section/references", json={
            "base_revision": current["revision"], "paragraphs": paragraphs,
        })

    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    reference = refs["paragraphs"][-1]["reference"]
    assert reference["year_fact"]["status"] == "unconfirmed"
    reference["year_confirmation_requested"] = True
    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    fact = refs["paragraphs"][-1]["reference"]["year_fact"]
    assert fact["status"] == "confirmed"
    assert fact["confirmed_by"] and fact["confirmed_by"] != "forged"
    assert fact["confirmed_at"] and fact["confirmed_at"] != "forged"

    refs["paragraphs"][-1]["reference"].pop("year_fact")
    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    assert refs["paragraphs"][-1]["reference"]["year_fact"] == fact
    refs["paragraphs"][-1]["reference"]["year"] = "2025"
    response = save(project, refs["paragraphs"])
    assert response.status_code == 200, response.text
    project = response.json()
    refs = next(section for section in project["sections"] if section["key"] == "references")
    assert refs["paragraphs"][-1]["reference"]["year_fact"]["status"] == "unconfirmed"

    snapshot = {key: deepcopy(project[key]) for key in ("title", "meta", "sections")}
    restored = next(section for section in snapshot["sections"] if section["key"] == "references")["paragraphs"][-1]
    restored["reference"]["year_fact"].update({"status": "confirmed", "confirmed_by": "forged", "confirmed_at": "forged"})
    response = client.post(f"/api/projects/{project_id}/restore-local", json={
        "base_revision": project["revision"], "snapshot": snapshot,
    })
    assert response.status_code == 200, response.text
    restored_fact = next(section for section in response.json()["sections"] if section["key"] == "references")["paragraphs"][-1]["reference"]["year_fact"]
    assert restored_fact["status"] == "unconfirmed"
    assert restored_fact["confirmed_by"] == ""


@pytest.mark.parametrize("prefix", ["[8]", "[ 8 ]", "［8］"])
def test_legacy_reference_number_survives_api_save_and_reload(prefix, tmp_path):
    from app.models.project import ParagraphBlock

    project_id = _create_full_default_gb7713_project()
    store = Store()
    project = store.get_project(project_id)
    project.reference_number_owners = {}
    refs = next(section for section in project.sections if section.key == "references")
    refs.paragraphs = [ParagraphBlock(id="legacy-ref", text=f"{prefix} 李四. 论文乙. 2025.")]
    store.save_project(project)
    response = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project.revision,
        "paragraphs": [block.model_dump(mode="json") for block in refs.paragraphs],
    })
    assert response.status_code == 200, response.text
    reloaded = client.get(f"/api/projects/{project_id}")
    assert reloaded.status_code == 200, reloaded.text
    saved = next(section for section in reloaded.json()["sections"] if section["key"] == "references")
    assert saved["paragraphs"][0]["reference_number"] == 8
    assert reloaded.json()["reference_number_owners"] == {"8": "legacy-ref"}
    document = _render_project_directly_for_renderer_assertion(project_id, tmp_path / "legacy.docx")
    assert "[8] 李四. 论文乙. 2025." in [paragraph.text for paragraph in document.paragraphs]


def test_ambiguous_legacy_references_block_saving_and_both_export_endpoints():
    from app.models.project import ParagraphBlock
    project_id = _create_full_default_gb7713_project()
    store = Store()
    project = store.get_project(project_id)
    project.reference_number_owners = {}
    refs = next(section for section in project.sections if section.key == "references")
    refs.paragraphs = [ParagraphBlock(id="old-positional", text="旧文献甲. 2026."),
                       ParagraphBlock(id="old-explicit", text="[1] 旧文献乙. 2026.")]
    store.save_project(project)
    for endpoint in ("export", "export-jobs"):
        rejected = client.post(f"/api/projects/{project_id}/{endpoint}?force=true")
        assert rejected.status_code == 422, rejected.text
        assert "编号 [1]" in rejected.text
    rejected = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": project.revision, "paragraphs": [block.model_dump(mode="json") for block in refs.paragraphs],
    })
    assert rejected.status_code == 422
    assert store.get_project(project_id).revision == project.revision


def test_durable_project_export_unknown_capability_fails_without_publishing():
    from app.services.export_jobs import EXPORT_DIR
    from app.services.export_jobs import run_project_export
    from app.worker import run_once

    project_id = _create_full_default_gb7713_project()
    project = Store().get_project(project_id)
    project.template_source_snapshot["styles"]["body"]["unknown_layout_constraint"] = "unsupported"
    Store().save_project(project)
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()

    result = run_once()

    assert task["job_id"] in {item["job_id"] for item in result["exports"]["failed"]}
    assert task["job_id"] not in result["exports"]["succeeded"]
    completed = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}").json()
    assert completed["status"] == "failed"
    assert "download_url" not in completed
    assert completed["output_check_status"] in {"fail", "unverifiable"}
    assert completed["output_check_diagnostic"]["findings"]
    saved = Store().get_workflow_record("project_export", task["id"])
    assert not saved.get("file_path")
    assert not (EXPORT_DIR / f"{task['id']}.docx").exists()
    assert client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download").status_code == 409
    retried = run_project_export(Store(), task["id"])
    assert retried["status"] == "failed"
    assert retried["output_check_diagnostic"]["findings"]
    assert not retried.get("file_path")
    assert not (EXPORT_DIR / f"{task['id']}.docx").exists()


def test_export_identity_detects_changed_or_missing_template_source(tmp_path, monkeypatch):
    from app.models.project import WritingProject
    from app.services.export_jobs import export_identity, export_job_is_current, export_job_view

    source = tmp_path / "identity-source.docx"
    Document().save(source)
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))
    template = PaperTemplate(id="identity-template", name="身份测试", source_docx=source.name)
    project = WritingProject(id="identity-project", template_id=template.id,
                             template_snapshot=template.model_dump(mode="json"))
    identity = export_identity(project)
    assert identity is not None
    record = {**identity, "id": "export-identity", "status": "succeeded",
              "output_check": {"status": "pass"}}
    assert export_job_is_current(record, identity)
    modified = Document(source)
    modified.core_properties.author = "source changed"
    modified.save(source)
    changed_identity = export_identity(project)
    assert not export_job_is_current(record, changed_identity)
    view = export_job_view(record, current_identity=changed_identity, check_current=True)
    assert view["is_current_for_project"] is False and "download_url" not in view
    source.unlink()
    assert export_identity(project) is None
    assert not export_job_is_current(record, export_identity(project))


def test_paper_job_freezes_source_bytes_before_worker_starts(tmp_path, monkeypatch):
    import hashlib
    from docx import Document
    from app.models.project import WritingProject
    from app.services.export_jobs import create_export_job, run_project_export
    from app.models.template import PaperTemplate
    source = tmp_path / "queued-source.docx"
    Document().save(source)
    monkeypatch.setenv("PAPERGUARD_TEMPLATE_DIR", str(tmp_path))
    template = PaperTemplate(id="queued-source-paper-template", name="源冻结测试", source_docx=source.name)
    project = WritingProject(id="queued-source-paper", template_id=template.id, owner_id="user-admin")
    store = Store()
    record = create_export_job(store, project, template, force=True, actor_id="user-admin")
    assert record["source_template_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    changed = Document(source)
    changed.core_properties.author = "changed after request"
    changed.save(source)
    invoked = []
    monkeypatch.setattr("app.services.export_jobs.export_project_to_docx", lambda *_args, **_kwargs: invoked.append(True))
    result = run_project_export(store, record["id"])
    assert result["status"] == "failed"
    assert not result["file_path"]
    assert not invoked


def test_durable_project_export_failure_can_be_retried(monkeypatch):
    """A failed export returns to queued state through the manager retry path."""
    from app.services import export_jobs
    from app.services.export_jobs import EXPORT_DIR
    from app.worker import run_once

    project_id = _create_full_default_gb7713_project()
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("synthetic export failure")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", fail_export)
    failed_run = run_once()
    assert task["job_id"] in {item["job_id"] for item in failed_run["exports"]["failed"]}
    failed = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}").json()
    assert failed["status"] == "failed"

    retried = client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    output = EXPORT_DIR / f"{task['id']}.docx"
    if output.exists():
        output.unlink()


def test_project_export_manual_retries_preserve_attempt_budget_and_audit(tmp_path, monkeypatch):
    """Retrying a frozen export must not reset or exceed its queue budget."""
    from app.services import export_jobs
    from app.worker import run_once

    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("synthetic export failure")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", fail_export)
    project_id = _create_full_default_gb7713_project()
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()
    store = Store()
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET max_attempts = 2 WHERE id = ?", (task["job_id"],))

    assert task["job_id"] in {item["job_id"] for item in run_once()["exports"]["failed"]}
    assert store.get_job(task["job_id"])["attempt_count"] == 1
    frozen = store.get_workflow_record("project_export", task["id"])["project_snapshot"]

    retried = client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert store.get_job(task["job_id"])["attempt_count"] == 1
    audit = [event for event in store.list_audit_events()
             if event["resource_id"] == task["id"] and event["action"] == "project.export.retry_requested"]
    assert len(audit) == 1

    assert task["job_id"] in {item["job_id"] for item in run_once()["exports"]["failed"]}
    job = store.get_job(task["job_id"])
    assert job["attempt_count"] == 2 and job["state"] == "failed"
    blocked = client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry")
    assert blocked.status_code == 409
    assert "上限" in blocked.text
    saved = store.get_workflow_record("project_export", task["id"])
    assert saved["status"] == "failed" and saved["project_snapshot"] == frozen
    assert not saved.get("file_path")
    assert store.get_job(task["job_id"])["attempt_count"] == 2
    assert client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download").status_code == 409


def test_project_export_retry_rejects_outdated_project_snapshot(tmp_path, monkeypatch):
    from app.services import export_jobs
    from app.worker import run_once

    monkeypatch.setattr(export_jobs, "EXPORT_DIR", tmp_path)

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("synthetic export failure")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", fail_export)
    project_id = _create_full_default_gb7713_project()
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()
    assert task["job_id"] in {item["job_id"] for item in run_once()["exports"]["failed"]}
    changed = client.put(f"/api/projects/{project_id}/section/abstract", json={
        "paragraphs": [{"id": "changed-after-failure", "text": "更新后的摘要内容。"}],
    })
    assert changed.status_code == 200, changed.text

    blocked = client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry")
    assert blocked.status_code == 409
    assert "已变化" in blocked.text
    assert Store().get_workflow_record("project_export", task["id"])["status"] == "failed"


def test_project_owner_can_retry_own_failed_export(monkeypatch):
    """作者本人可以重试自己的失败导出任务，其他学生仍然被拒绝。"""
    from app.services import export_jobs
    from app.services.export_jobs import EXPORT_DIR
    from app.worker import run_once

    suffix = uuid.uuid4().hex[:8]
    owner_name = f"export_owner_{suffix}"
    other_name = f"export_other_{suffix}"
    password = f"ExportOwner-{suffix}-8a"

    for name in (owner_name, other_name):
        register_fixture(client, name, password, display_name="导出作者")

    def _headers(username: str) -> dict[str, str]:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        return {"Authorization": f"Bearer {login.json()['token']}"}

    owner = _headers(owner_name)
    other = _headers(other_name)

    created = client.post("/api/projects", json={
        "template_id": "default-gb7713", "title": "作者自己的论文",
    }, headers=owner)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    # 作者仍可重试自己的导出，但学生必须先完成模板必填项。
    project = created.json()
    meta = {field["key"]: f"回归{field['label']}" for field in project["template_snapshot"]["meta_fields"]}
    assert client.put(f"/api/projects/{project_id}/meta", json={"meta": meta}, headers=owner).status_code == 200
    for section in project["sections"]:
        if section["required"]:
            saved = client.put(f"/api/projects/{project_id}/section/{section['key']}", json={
                "paragraphs": [{"id": f"{section['key']}-p1", "text": "模板要求的回归正文。"}],
            }, headers=owner)
            assert saved.status_code == 200, saved.text

    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true", headers=owner)
    assert queued.status_code == 202, queued.text
    task = queued.json()

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("synthetic owner retry failure")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", fail_export)
    failed_run = run_once()
    assert task["job_id"] in {item["job_id"] for item in failed_run["exports"]["failed"]}

    # 旁路学生看不到该项目，重试必须 404。
    assert client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry", headers=other).status_code == 404

    # 作者本人重试自己的失败任务应放行。
    retried = client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry", headers=owner)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    output = EXPORT_DIR / f"{task['id']}.docx"
    if output.exists():
        output.unlink()


def test_strip_template_comments_returns_docx():
    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        resp = client.post(
            "/api/templates/strip-comments",
            files={"file": ("template.docx", f, "application/octet-stream")},
        )
    assert resp.status_code == 200, resp.text
    assert resp.content[:2] == b"PK"
    assert "template-no-comments.docx" in resp.headers["content-disposition"]


def test_import_detects_styles():
    """上传 docx 应识别出标题/正文的字体字号等样式。

    标题段落带直接格式(18pt)时以直接格式为准 —— 它才是 Word 实际渲染的值;
    样式表里的 heading 1 (14pt) 只作兜底。
    """
    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        resp = client.post(
            "/api/templates/import",
            files={"file": ("gb7713-official.docx", f, "application/octet-stream")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "heading1" in data["styles"], data
    h1 = data["styles"]["heading1"]
    assert h1["size_pt"] == 18.0          # 段落直接格式 36 half-points
    assert h1["bold"] is True
    assert "body" in data["styles"]
    assert data["styles"]["body"]["size_pt"] == 12.0
    assert data["style_names"], "应识别到至少一个 Word 样式"


def test_import_keeps_source_docx_and_meta_fields():
    """导入应留档模板源文件(供导出作排版基底)并识别封面字段。"""
    from app.services.template_source import resolve_template_source

    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        resp = client.post(
            "/api/templates/import",
            files={"file": ("gb7713-official.docx", f, "application/octet-stream")},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source_docx"].endswith(".docx")
    assert resolve_template_source(data["source_docx"]) is not None
    labels = {m["label"] for m in data["meta_fields"]}
    assert "学号" in labels, data["meta_fields"]
    assert "日期" in labels, data["meta_fields"]


def test_template_with_unmapped_source_content_is_blocked_before_project_creation():
    """导入仍展示源模板，但未映射的原始内容不能进入学生写作。"""
    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        cfg = client.post(
            "/api/templates/import",
            files={"file": ("gb7713-official.docx", f, "application/octet-stream")},
        ).json()
    created = client.post("/api/templates", json={
        "id": "", "name": "模板基底测试",
        "sections": cfg["sections"], "styles": cfg["styles"],
        "meta_fields": cfg["meta_fields"], "source_docx": cfg["source_docx"],
    })
    assert created.status_code == 201, created.text
    listed = next(item for item in client.get("/api/templates").json() if item["id"] == created.json()["id"])
    assert listed["admission"]["status"] == "blocked"
    proj = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "基底测试",
    })
    assert proj.status_code == 409, proj.text
    assert proj.json()["detail"]["code"] == "template_admission_blocked"


def test_import_detects_cover_layout_and_blocks_unmapped_table_from_students():
    """导入预览保留封面识别结果，未映射的封面表格不能进入学生写作。"""
    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        cfg = client.post(
            "/api/templates/import",
            files={"file": ("gb7713-official.docx", f, "application/octet-stream")},
        ).json()
    kinds = [b["kind"] for b in cfg["cover_blocks"]]
    assert "text" in kinds, cfg["cover_blocks"]
    assert kinds.count("field") >= 2, cfg["cover_blocks"]
    assert cfg["admission"]["status"] == "blocked"

    created = client.post("/api/templates", json={
        "id": "", "name": "封面版式测试",
        "sections": cfg["sections"], "styles": cfg["styles"],
        "meta_fields": cfg["meta_fields"], "cover_blocks": cfg["cover_blocks"],
        "source_docx": cfg["source_docx"],
    })
    project = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "封面测试",
    })
    assert project.status_code == 409, project.text
    assert any(item["code"] in {"cover_table", "source_table_not_mapped"} for item in project.json()["detail"]["admission"]["blockers"])


def test_import_preserves_cover_labels_but_blocks_unmapped_table_from_students(tmp_path):
    """导入预览保留封面标签，未映射表格仍不得进入学生写作。"""
    import io

    from docx import Document as DocxDocument

    source = DocxDocument()
    source.add_paragraph("示例学院")
    source.add_paragraph("本科毕业设计报告")
    source.add_paragraph("题目：示例论文")
    cover_table = source.add_table(rows=0, cols=2)
    labels = ["学    院：", "专    业：", "班    级：", "学生姓名：", "学生学号：", "指导教师："]
    for label in labels:
        cells = cover_table.add_row().cells
        cells[0].text = label
        cells[1].text = ""
    source.add_paragraph("2026年5月")
    source.add_paragraph("摘  要")
    source_path = tmp_path / "cover-labels.docx"
    source.save(source_path)

    imported = client.post(
        "/api/templates/import",
        files={"file": (source_path.name, source_path.read_bytes(), "application/octet-stream")},
    )
    assert imported.status_code == 200, imported.text
    config = imported.json()
    cover_fields = next(block["fields"] for block in config["cover_blocks"] if block["kind"] == "table")
    assert [field["label"] for field in cover_fields] == labels
    form_labels = {field["key"]: field["label"] for field in config["meta_fields"]}
    assert form_labels["author"] == "姓名"
    assert form_labels["student_id"] == "学号"

    created = client.post("/api/templates", json={
        "id": "", "name": "封面原标签测试",
        "sections": config["sections"], "styles": config["styles"],
        "meta_fields": config["meta_fields"], "cover_blocks": config["cover_blocks"],
        "source_docx": config["source_docx"],
    })
    assert created.status_code == 201, created.text
    listed = next(item for item in client.get("/api/templates").json() if item["id"] == created.json()["id"])
    assert listed["admission"]["status"] == "blocked"
    project = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "示例论文",
    })
    assert project.status_code == 409, project.text
    blocker_codes = {item["code"] for item in project.json()["detail"]["admission"]["blockers"]}
    assert {"cover_table", "source_table_not_mapped"}.issubset(blocker_codes)


def test_switch_project_template_keeps_content(tmp_path):
    """换成另一个模板: 排版/封面换成新模板, 已写内容按章节 key 保留并补全新章节。"""
    base = client.get("/api/templates/default-gb7713").json()
    tpl_a = client.post("/api/templates", json={**base, "id": "", "name": "模板A"}).json()

    styles_b = {k: dict(v) for k, v in tpl_a["styles"].items()}
    styles_b["body"]["size_pt"] = 14.0
    sections_b = [dict(s) for s in tpl_a["sections"]] + [
        {"key": "ch9", "type": "heading1", "title": "第九章 新增章节",
         "required": False, "children": []},
    ]
    tpl_b = client.post("/api/templates", json={
        **base, "id": "", "name": "模板B", "styles": styles_b, "sections": sections_b,
    }).json()

    proj_id = client.post("/api/projects", json={
        "template_id": tpl_a["id"], "title": "换模板测试",
    }).json()["id"]
    client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "title": "摘要", "paragraphs": [{"id": "p1", "text": "已写内容。"}],
    })

    resp = client.post(f"/api/projects/{proj_id}/template", json={"template_id": tpl_b["id"]})
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["template_id"] == tpl_b["id"]
    assert updated["template_name"] == "模板B"
    assert updated["template_snapshot"]["styles"]["body"]["size_pt"] == 14.0
    keys = [s["key"] for s in updated["sections"]]
    assert "ch9" in keys, keys                      # 新模板新增的章节被补上
    abstract = next(s for s in updated["sections"] if s["key"] == "abstract")
    assert abstract["paragraphs"][0]["text"] == "已写内容。"   # 已写内容保留

    # 导出按新模板排版
    exported = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert exported.status_code == 409, exported.text
    assert exported.json()["detail"]["missing"]
    d = _render_project_directly_for_renderer_assertion(proj_id, tmp_path / "switched-template.docx")
    body_para = next(p for p in d.paragraphs if p.text == "已写内容。")
    assert body_para.runs[0].font.size.pt == 14.0


def test_import_extracts_acknowledgement_style(tmp_path):
    """致谢正文要按 acknowledgement 样式解析(含首行缩进), 且红字说明不参与统计。"""
    import docx as docx_lib
    from docx.shared import Pt, RGBColor

    from app.services.import_template import import_template_styles

    doc = docx_lib.Document()
    doc.add_paragraph("致  谢")
    body = doc.add_paragraph()
    body.add_run("衷心感谢导师在毕业设计期间给予的悉心指导。").font.size = Pt(12)
    body.paragraph_format.first_line_indent = Pt(24)      # 2 字符 @ 12pt
    note = doc.add_paragraph("说明：此处填写致谢内容, 字体三号宋体")
    note.runs[0].font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
    path = tmp_path / "ack.docx"
    doc.save(path)

    styles = import_template_styles(path)["styles"]
    assert "acknowledgement" in styles, styles.keys()
    assert styles["acknowledgement"].first_line_indent_chars == 2.0


def test_switch_template_keeps_section_order():
    """换模板不得打乱学生已有的章节顺序(含手动新增的章节)。"""
    base = client.get("/api/templates/default-gb7713").json()
    tpl_a = client.post("/api/templates", json={**base, "id": "", "name": "顺序A"}).json()
    tpl_b = client.post("/api/templates", json={**base, "id": "", "name": "顺序B"}).json()

    proj_id = client.post("/api/projects", json={
        "template_id": tpl_a["id"], "title": "顺序测试",
    }).json()["id"]

    current = client.get(f"/api/projects/{proj_id}").json()
    sections = [{"key": s["key"], "type": s["type"], "title": s["title"],
                 "required": s["required"]} for s in current["sections"]]
    index = next(i for i, s in enumerate(sections) if s["key"] == "ch1") + 1
    sections.insert(index, {"key": "", "type": "heading2",
                            "title": "1.1.1 手动新增", "required": False})
    assert client.put(f"/api/projects/{proj_id}/sections",
                      json={"sections": sections}).status_code == 200

    before = [s["title"] for s in client.get(f"/api/projects/{proj_id}").json()["sections"]]
    assert "1.1.1 手动新增" in before

    resp = client.post(f"/api/projects/{proj_id}/template", json={"template_id": tpl_b["id"]})
    assert resp.status_code == 200, resp.text
    after = [s["title"] for s in resp.json()["sections"]]
    assert after[:len(before)] == before, after          # 顺序原样保留
    assert resp.json()["template_id"] == tpl_b["id"]


def test_refresh_project_template_updates_snapshot():
    """「重新套用模板」把项目冻结的快照换成当前模板, 不动已写内容。"""
    base = client.get("/api/templates/default-gb7713").json()
    created = client.post("/api/templates", json={
        **base, "id": "", "name": "刷新测试模板",
    }).json()
    proj_id = client.post("/api/projects", json={
        "template_id": created["id"], "title": "刷新测试",
    }).json()["id"]
    client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "title": "摘要", "paragraphs": [{"id": "p1", "text": "已写内容。"}],
    })

    updated_styles = {k: dict(v) for k, v in created["styles"].items()}
    updated_styles["body"]["size_pt"] = 13.0
    assert client.put(f"/api/templates/{created['id']}", json={
        **created,
        "styles": updated_styles,
        "meta_fields": created["meta_fields"] + [
            {"key": "college", "label": "学院", "required": False}],
    }).status_code == 200

    resp = client.post(f"/api/projects/{proj_id}/refresh-template")
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["template_snapshot"]["styles"]["body"]["size_pt"] == 13.0
    assert any(f["key"] == "college" for f in updated["template_snapshot"]["meta_fields"])
    assert "college" in updated["meta"]          # 新字段补空值
    assert updated["sections"][0]["paragraphs"][0]["text"] == "已写内容。"


def _make_image_png() -> bytes:
    """生成 1x1 红色 PNG (最小合法 PNG)。"""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )


def test_upload_rejects_fake_image():
    proj_id = _create_project_instantiates_sections()
    resp = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fake.png", b"<html>not image</html>", "image/png")},
    )
    assert resp.status_code == 400


def test_project_image_download_is_resource_scoped():
    """图片下载必须沿项目授权边界判定，不能因角色是教师而跨项目读取。"""
    proj_id = _create_project_instantiates_sections()
    uploaded = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("private.png", _make_image_png(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text

    outsider = register_fixture(client, "image-outsider", "OutsiderPassword123")
    login = client.post("/api/auth/login", json={
        "username": "image-outsider", "password": "OutsiderPassword123",
    })
    assert login.status_code == 200, login.text
    denied = client.get(
        uploaded.json()["url"],
        headers={"Authorization": f"Bearer {login.json()['token']}"},
    )
    assert denied.status_code == 404
    outsider_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.post(f"/api/projects/{proj_id}/export", headers=outsider_headers).status_code == 404
    assert client.get(f"/api/projects/{proj_id}/check", headers=outsider_headers).status_code == 404



    proj_id = _create_project_instantiates_sections()
    resp = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    )
    assert resp.status_code == 201
    url = resp.json()["url"]
    assert url.startswith("/media/uploads/")
    # 静态资源可访问
    resp2 = client.get(url, headers={"Authorization": client.headers["Authorization"]})
    assert resp2.status_code == 200
    assert resp2.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_save_image_and_table_blocks():
    proj_id = _create_project_instantiates_sections()
    img = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    ).json()["url"]
    resp = client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [
            {"id": "p1", "type": "paragraph", "text": "这是正文。"},
            {"id": "img1", "type": "image",
             "image": {"url": img, "caption": "图 1.1 系统架构"}},
            {"id": "tbl1", "type": "table",
             "table": {"rows": 2, "cols": 2,
                       "cells": [["方法", "准确率"], ["A", "95%"], ["B", "90%"]],
                       "caption": "表 1.1 对比结果"}},
        ],
    })
    assert resp.status_code == 200
    sec = next(s for s in resp.json()["sections"] if s["key"] == "ch1")
    assert len(sec["paragraphs"]) == 3
    assert sec["paragraphs"][1]["type"] == "image"
    assert sec["paragraphs"][2]["type"] == "table"


def test_section_save_preserves_image_geometry_and_rejects_invalid_geometry():
    proj_id = _create_project_instantiates_sections()
    image_url = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("figure.png", _make_image_png(), "image/png")},
    ).json()["url"]
    image = {"url": image_url, "caption": "结构图", "width_cm": 12.5,
             "height_cm": 7.5, "alignment": "center"}
    payload = {"paragraphs": [{"id": "img-geometry", "type": "image", "image": image}]}

    response = client.put(f"/api/projects/{proj_id}/section/ch1", json=payload)
    assert response.status_code == 200, response.text
    stored = client.get(f"/api/projects/{proj_id}").json()
    section = next(section for section in stored["sections"] if section["key"] == "ch1")
    assert section["paragraphs"][0]["image"] == image

    payload["paragraphs"][0]["image"]["width_cm"] = -1
    invalid = client.put(f"/api/projects/{proj_id}/section/ch1", json=payload)
    assert invalid.status_code == 422
    assert client.get(f"/api/projects/{proj_id}").json()["revision"] == stored["revision"]


def test_section_save_rejects_inconsistent_inline_runs_without_dropping_formatting():
    proj_id = _create_project_instantiates_sections()
    initial = client.put(f"/api/projects/{proj_id}/section/ch1", json={"paragraphs": [{
        "id": "formatted", "type": "paragraph", "text": "重要内容",
        "inline_runs": [{"text": "重要", "bold": True}, {"text": "内容"}],
    }]})
    assert initial.status_code == 200, initial.text
    project = initial.json()
    response = client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "base_revision": project["revision"], "paragraphs": [{
            "id": "formatted", "type": "paragraph", "text": "重要内容修改",
            "inline_runs": [{"text": "重要", "bold": True}, {"text": "内容"}],
        }],
    })
    assert response.status_code == 422
    saved = client.get(f"/api/projects/{proj_id}").json()
    assert saved["revision"] == project["revision"]
    paragraph = next(section for section in saved["sections"] if section["key"] == "ch1")["paragraphs"][0]
    assert paragraph["text"] == "重要内容"
    assert paragraph["inline_runs"][0]["bold"] is True


def _project_with_imported_table(merged: bool = False):
    from app.models.project import ParagraphBlock

    proj_id = _create_project_instantiates_sections()
    document = Document()
    table = document.add_table(rows=2, cols=2)
    if merged:
        table.cell(0, 0).merge(table.cell(0, 1)).text = "合并表头"
    else:
        table.cell(0, 0).text = "名称"
        table.cell(0, 1).text = "结果"
    table.cell(1, 0).text = "样例"
    table.cell(1, 1).text = "通过"
    cells = [[cell.text for cell in row.cells] for row in table.rows]
    block = ParagraphBlock.model_validate({
        "id": "imported-table", "type": "table", "table": {
            "rows": 2, "cols": 2, "cells": cells, "caption": "原题注",
            "source_xml": table._tbl.xml,
        },
    })
    store = Store()
    project = store.get_project(proj_id)
    section = next(section for section in project.sections if section.key == "ch1")
    section.paragraphs = [block]
    assert store.update_project_if_revision(project, project.revision) is not None
    return proj_id, table._tbl.xml


def test_section_save_keeps_unmodified_server_table_xml_and_ignores_client_xml():
    proj_id, original_xml = _project_with_imported_table(merged=True)
    project = client.get(f"/api/projects/{proj_id}").json()
    section = next(section for section in project["sections"] if section["key"] == "ch1")
    section["paragraphs"][0]["table"].update(caption="新题注", source_xml="<w:tbl>客户端伪造</w:tbl>")
    response = client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "base_revision": project["revision"], "paragraphs": section["paragraphs"],
    })
    assert response.status_code == 200, response.text
    stored = next(item for item in response.json()["sections"] if item["key"] == "ch1")
    assert stored["paragraphs"][0]["table"]["source_xml"] == original_xml
    assert stored["paragraphs"][0]["table"]["caption"] == "新题注"


def test_section_save_rebuilds_edited_plain_table_and_rejects_new_client_xml(tmp_path):
    proj_id, _ = _project_with_imported_table()
    project = client.get(f"/api/projects/{proj_id}").json()
    section = next(section for section in project["sections"] if section["key"] == "ch1")
    section["paragraphs"][0]["table"]["cells"][1][1] = "已修改"
    section["paragraphs"].append({
        "id": "new-table", "type": "table", "table": {
            "rows": 1, "cols": 1, "cells": [["真实内容"]],
            "source_xml": "<w:tbl>客户端伪造</w:tbl>",
        },
    })
    response = client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "base_revision": project["revision"], "paragraphs": section["paragraphs"],
    })
    assert response.status_code == 200, response.text
    stored = next(item for item in response.json()["sections"] if item["key"] == "ch1")
    assert [block["table"]["source_xml"] for block in stored["paragraphs"]] == ["", ""]
    rendered = _render_project_directly_for_renderer_assertion(proj_id, tmp_path / "updated-table.docx")
    assert rendered.tables[0].cell(1, 1).text == "已修改"
    assert rendered.tables[1].cell(0, 0).text == "真实内容"


def test_section_save_blocks_edit_of_complex_imported_table_without_changing_project():
    proj_id, original_xml = _project_with_imported_table(merged=True)
    project = client.get(f"/api/projects/{proj_id}").json()
    section = next(section for section in project["sections"] if section["key"] == "ch1")
    section["paragraphs"][0]["table"]["cells"][1][1] = "不能静默破坏合并结构"
    response = client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "base_revision": project["revision"], "paragraphs": section["paragraphs"],
    })
    assert response.status_code == 409
    assert "表格" in str(response.json()["detail"])
    stored = client.get(f"/api/projects/{proj_id}").json()
    saved_table = next(item for item in stored["sections"] if item["key"] == "ch1")["paragraphs"][0]["table"]
    assert stored["revision"] == project["revision"]
    assert saved_table["cells"][1][1] == "通过"
    assert saved_table["source_xml"] == original_xml


@pytest.mark.parametrize("kind", ["nested_table", "drawing", "field", "unknown", "multiple_paragraphs"])
def test_plain_table_classifier_rejects_unrepresentable_cell_contents(kind):
    from docx.oxml import OxmlElement
    from app.api.projects import _plain_table_can_be_rebuilt

    document = Document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = "保留文字"
    if kind == "nested_table":
        cell.add_table(rows=1, cols=1).cell(0, 0).text = "嵌套内容"
    elif kind == "multiple_paragraphs":
        cell.add_paragraph("第二段")
    else:
        run = cell.paragraphs[0].runs[0]._r
        run.append(OxmlElement({"drawing": "w:drawing", "field": "w:fldChar", "unknown": "w:unknown"}[kind]))

    assert not _plain_table_can_be_rebuilt(table._tbl.xml)


@pytest.mark.parametrize("source_xml", ["<w:tbl>", "<!DOCTYPE x><x/>", "<x/>"])
def test_plain_table_classifier_rejects_invalid_xml(source_xml):
    from app.api.projects import _plain_table_can_be_rebuilt

    assert not _plain_table_can_be_rebuilt(source_xml)


def test_renderer_docx_with_image_and_table(tmp_path):
    """完整论文样稿中的普通表格与内嵌图片经过核验后可下载。"""

    proj_id = _create_full_default_gb7713_project()
    img = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    ).json()["url"]
    project = client.get(f"/api/projects/{proj_id}").json()
    ch1 = next(section for section in project["sections"] if section["key"] == "ch1")
    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "base_revision": project["revision"],
        "title": ch1["title"],
        "paragraphs": [
            *ch1["paragraphs"],
            {"id": "img1", "type": "image",
             "image": {"url": img, "caption": "图 1.1 系统架构"}},
            {"id": "tbl1", "type": "table",
             "table": {"rows": 2, "cols": 2,
                       "cells": [["方法", "准确率"], ["A", "95%"]],
                       "caption": "表 1.1 对比结果"}},
        ],
    })
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-paperguard-output-check"] == "pass"
    assert int(resp.headers["x-paperguard-output-check-count"]) > 0
    d = Document(BytesIO(resp.content))
    texts = [p.text for p in d.paragraphs]
    assert "图 1.1 系统架构" in texts
    assert "表 1.1 对比结果" in texts
    # 表格存在且有数据
    assert len(d.tables) == 1
    assert d.tables[0].cell(0, 0).text == "方法"
    assert d.tables[0].cell(1, 1).text == "95%"
    # 图片存在: 检查内嵌图片数量 (inline shapes)
    assert len(d.inline_shapes) == 1

    saved = Store().get_project(proj_id)
    assert saved is not None
    template = PaperTemplate.model_validate(saved.template_snapshot)
    mutated = Document(BytesIO(resp.content))
    mutated.tables[0].cell(1, 1).text = "94%"
    bad_table = BytesIO()
    mutated.save(bad_table)
    table_report = inspect_docx_output(bad_table.getvalue(), saved, template)
    assert table_report["status"] == "fail"
    assert any(item["rule"] == "content.table_cell" and item["status"] == "fail" for item in table_report["checks"])

    mutated = Document(BytesIO(resp.content))
    mutated.inline_shapes[0].width = 20 * 360000
    bad_image = BytesIO()
    mutated.save(bad_image)
    image_report = inspect_docx_output(bad_image.getvalue(), saved, template)
    assert image_report["status"] == "fail"
    assert any(item["rule"] == "format.image_dimensions" and item["status"] == "fail" for item in image_report["checks"])


def test_docx_imported_image_renders_from_configured_data_dir_but_is_not_published(tmp_path, monkeypatch):
    """Imported images resolve for rendering while unsupported image exports stay gated."""
    import io
    from docx import Document as DocxDocument
    from docx.shared import Cm

    from app.services.document_assets import document_upload_dir
    from app.core import feature_flags

    monkeypatch.setattr(feature_flags, "manuscript_import_enabled", lambda: True)
    data_dir = tmp_path / "paperguard-data"
    monkeypatch.setenv("PAPERGUARD_DATA_DIR", str(data_dir))
    project_id = _create_project_instantiates_sections()

    manuscript = DocxDocument()
    manuscript.add_heading("第一章 绪论", level=1)
    manuscript.add_paragraph("本章正文。")
    image_paragraph = manuscript.add_paragraph()
    image_paragraph.add_run().add_picture(io.BytesIO(_make_image_png()), width=Cm(2))
    source = io.BytesIO()
    manuscript.save(source)
    payload = source.getvalue()

    preview = client.post(
        f"/api/projects/{project_id}/import-docx",
        files={"file": ("manuscript.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["stats"]["images"] == 1

    applied = client.post(
        f"/api/projects/{project_id}/import-docx",
        params={"apply": "true", "base_revision": preview.json()["project_revision"],
                "candidate_id": preview.json()["candidate_id"], "candidate_sha256": preview.json()["candidate_sha256"]},
    )
    assert applied.status_code == 200, applied.text
    image_block = next(
        block
        for section in applied.json()["project"]["sections"]
        for block in section["paragraphs"]
        if block["type"] == "image"
    )
    image_path = document_upload_dir() / image_block["image"]["url"].rsplit("/", 1)[-1]
    assert image_path.is_file()

    exported = client.post(f"/api/projects/{project_id}/export?force=true")
    assert exported.status_code == 409, exported.text
    assert exported.json()["detail"]["missing"]
    document = _render_project_directly_for_renderer_assertion(
        project_id, tmp_path / "imported-image.docx",
    )
    assert len(document.inline_shapes) == 1
    assert not any("[图片缺失:" in paragraph.text for paragraph in document.paragraphs)


def test_removed_image_block_waits_for_orphan_cleanup():
    """删除图片块后先保留文件, 由孤儿回收统一清理。"""
    from pathlib import Path

    proj_id = _create_project_instantiates_sections()
    img = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    ).json()["url"]
    fname = img.rsplit("/", 1)[-1]

    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章", "paragraphs": [
            {"id": "img1", "type": "image", "image": {"url": img, "caption": "图 1"}},
        ],
    })
    from app.services.document_assets import document_upload_dir
    uploads = document_upload_dir()
    assert (uploads / fname).exists(), "图片文件应已上传"

    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章", "paragraphs": [
            {"id": "p1", "text": "删掉图片后的正文"},
        ],
    })
    assert (uploads / fname).exists(), "章节保存不应立即删除图片"

    from app.services.image_cleanup import cleanup_orphan_images
    cleanup_orphan_images(Store())
    assert not (uploads / fname).exists(), "孤儿回收后应清理无引用图片"


def test_delete_project_moves_to_trash_and_preserves_images():
    """删除草稿进入回收站，恢复前保留图片引用。"""
    from pathlib import Path

    proj_id = _create_project_instantiates_sections()
    img = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    ).json()["url"]
    fname = img.rsplit("/", 1)[-1]
    from app.services.document_assets import document_upload_dir
    uploads = document_upload_dir()

    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章", "paragraphs": [
            {"id": "img1", "type": "image", "image": {"url": img, "caption": "图 1"}},
        ],
    })
    assert (uploads / fname).exists()

    assert client.delete(f"/api/projects/{proj_id}").status_code == 204
    assert client.get(f"/api/projects/{proj_id}").status_code == 404
    trash = client.get("/api/projects/trash").json()
    assert any(item["id"] == proj_id for item in trash)
    assert (uploads / fname).exists(), "回收站项目仍需保留图片引用"
    assert client.post(f"/api/projects/{proj_id}/restore").status_code == 200
    assert client.get(f"/api/projects/{proj_id}").status_code == 200
    audits = client.get("/api/audit-events").json()
    assert {item["action"] for item in audits if item["resource_id"] == proj_id} >= {"project.soft_delete", "project.restore"}


def test_deleted_project_read_only_endpoints_are_hidden():
    """Trash projects must not leak through report, check, or export reads."""
    proj_id = _create_project_instantiates_sections()
    assert client.delete(f"/api/projects/{proj_id}").status_code == 204
    for method, path in (
        ("get", f"/api/projects/{proj_id}/check"),
        ("get", f"/api/projects/{proj_id}/template-check"),
        ("get", f"/api/projects/{proj_id}/check-runs"),
        ("get", f"/api/projects/{proj_id}/compliance-report"),
        ("post", f"/api/projects/{proj_id}/export?force=true"),
        ("post", f"/api/projects/{proj_id}/export-jobs?force=true"),
        ("get", f"/api/projects/{proj_id}/export-jobs"),
    ):
        assert getattr(client, method)(path).status_code == 404


def test_cleanup_orphan_images():
    """cleanup_orphan_images 删除未被引用的文件, 保留被引用的。"""
    from pathlib import Path

    from app.db import Store
    from app.services.image_cleanup import cleanup_orphan_images

    store = Store()
    from app.services.document_assets import document_upload_dir
    uploads = document_upload_dir()

    # 被引用图片
    proj_id = _create_project_instantiates_sections()
    img = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    ).json()["url"]
    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章", "paragraphs": [
            {"id": "img1", "type": "image", "image": {"url": img, "caption": "图 1"}},
        ],
    })

    # 孤儿文件: 直接写入 uploads 目录, 不关联任何项目
    orphan = uploads / "proj-orphan-test.png"
    orphan.write_bytes(_make_image_png())

    removed = cleanup_orphan_images(store)
    assert removed >= 1, f"应至少清理 1 个孤儿文件, 实际 {removed}"
    assert not orphan.exists(), "孤儿文件应被删除"
    ref_fname = img.rsplit("/", 1)[-1]
    assert (uploads / ref_fname).exists(), "被引用的图片应保留"


def test_export_blocked_when_incomplete():
    """不完整的项目导出应返回 409 与缺失清单。"""
    proj_id = _create_project_instantiates_sections()
    resp = client.post(f"/api/projects/{proj_id}/export")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "missing" in detail
    missing = detail["missing"]
    assert any("封面" in m for m in missing)          # 封面必填字段
    assert any("第一章" in m for m in missing)        # 必填章节


def test_export_validation_accepts_content_in_child_section():
    """一级章节正文写在二级小节时, 不应重复提示一级章节未完成。"""
    proj_id = _create_project_instantiates_sections()
    client.put(f"/api/projects/{proj_id}/section/ch1_1", json={
        "title": "1.1 研究背景与意义",
        "paragraphs": [{"id": "p1", "text": "本节正文内容。"}],
    })
    resp = client.post(f"/api/projects/{proj_id}/export")
    assert resp.status_code == 409
    missing = resp.json()["detail"]["missing"]
    assert not any("第一章" in item for item in missing)


def test_export_force_cannot_bypass_output_check(monkeypatch):
    """完整内容也不能通过 force 绕过损坏成品的检查。"""
    from app.api import projects as projects_api

    proj_id = _create_full_default_gb7713_project()

    def corrupt_output(project, template, out_path, **kwargs):
        Path(out_path).write_bytes(b"invalid-docx")

    monkeypatch.setattr(projects_api, "export_project_to_docx", corrupt_output)
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["output_check"]["findings"]


def test_renderer_docx_has_toc_field_and_page_footer(tmp_path):
    """渲染器保留 TOC / 页码域；未经核验的成品不会由 API 发布。"""

    proj_id = _create_project_instantiates_sections()
    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [{"id": "p1", "type": "paragraph", "text": "正文内容。"}],
    })
    client.put(f"/api/projects/{proj_id}/meta", json={
        "meta": {"school": "XX大学", "title": "论文", "author": "张三",
                 "student_id": "1", "major": "计算机", "advisor": "李四", "date": "2026"},
    })
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["missing"]
    d = _render_project_directly_for_renderer_assertion(proj_id, tmp_path / "toc-footer.docx")

    # TOC 域存在，并请求 Word 打开时自动更新
    xml = d.element.xml
    assert "TOC" in xml and "fldChar" in xml
    assert "updateFields" in d.settings.element.xml

    # 标题使用 Heading 1 样式 (导航/目录可用)
    heading_paras = [p for p in d.paragraphs if p.style and "Heading 1" in (p.style.name or "")]
    assert heading_paras, "第一章标题应使用 Heading 1 样式"

    # 页码页脚存在 (封面节不编号, 正文节带 PAGE 域)
    footers = [s.footer._element.xml for s in d.sections if s.footer]
    assert any("PAGE" in footer for footer in footers), "页脚应包含页码域"


def test_update_project_sections_structure():
    """测试在写作中增删和调整章节结构。"""
    proj_id = _create_project_instantiates_sections()
    # 增加新章节并删除非必要章节
    resp = client.put(f"/api/projects/{proj_id}/sections", json={
        "sections": [
            {"key": "abstract", "type": "abstract", "title": "摘要", "required": True},
            {"key": "ch1", "type": "heading1", "title": "第一章 绪论", "required": True},
            {"key": "ch1_1", "type": "heading2", "title": "1.1 背景", "required": False},
            {"key": "ch1_new", "type": "heading2", "title": "1.2 新增研究方法", "required": False},
            {"key": "references", "type": "references", "title": "参考文献", "required": True},
        ]
    })
    assert resp.status_code == 200
    updated_proj = resp.json()
    keys = [s["key"] for s in updated_proj["sections"]]
    assert "ch1_new" in keys
    assert "ch1_1" in keys
    assert "references" in keys


def test_renderer_captions_auto_numbered(tmp_path):
    """渲染器自动编号图表题注，API 仍拦截未纳入完整核验的图片。"""

    proj_id = _create_project_instantiates_sections()
    img = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    ).json()["url"]
    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [
            {"id": "p1", "type": "paragraph", "text": "正文。"},
            {"id": "img1", "type": "image",
             "image": {"url": img, "caption": "系统架构"}},   # 无手填编号
            {"id": "tbl1", "type": "table",
             "table": {"rows": 2, "cols": 2,
                       "cells": [["方法", "准确率"], ["A", "95%"]],
                       "caption": "表 9.9 对比结果"}},          # 旧数据手填编号
        ],
    })
    client.put(f"/api/projects/{proj_id}/section/ch2", json={
        "title": "第二章 相关技术",
        "paragraphs": [{"id": "img2", "type": "image",
                        "image": {"url": img, "caption": "架构图"}}],
    })
    client.put(f"/api/projects/{proj_id}/meta", json={
        "meta": {"school": "XX大学", "title": "论文", "author": "张三",
                 "student_id": "1", "major": "计算机", "advisor": "李四", "date": "2026"},
    })
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["missing"]
    d = _render_project_directly_for_renderer_assertion(proj_id, tmp_path / "captions.docx")
    texts = [p.text for p in d.paragraphs]
    assert "图 1.1 系统架构" in texts, texts
    assert "表 1.1 对比结果" in texts, texts   # 手填 9.9 被替换为 1.1
    assert "图 2.1 架构图" in texts, texts      # 第二章重新计数


def test_project_template_check_uses_snapshot_and_context():
    proj_id = _create_project_instantiates_sections()
    resp = client.get(f"/api/projects/{proj_id}/template-check")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["context"]["mode"] == "project-template"
    assert data["context"]["template_name"]
    assert "summary" in data and "findings" in data
    assert not any(".section.cover." in finding["rule_id"] or ".section.toc." in finding["rule_id"] for finding in data["findings"])


def test_check_project_realtime():
    """写作项目实时检查: 正文加粗违规应被检出并定位到章节。"""
    proj_id = _create_project_instantiates_sections()
    # 正文段落加粗 (规则要求正文不加粗)
    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [{"id": "p1", "type": "paragraph", "text": "不应加粗的正文。",
                        "bold": True, "italic": False, "underline": False}],
    })
    resp = client.get(f"/api/projects/{proj_id}/check?pack=GB-T-7713-1-2006")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data and "findings" in data
    ids = {f["rule_id"] for f in data["findings"]}
    assert ids, "应检出至少一条问题"
    assert any(f["section_key"] == "ch1" for f in data["findings"]), data["findings"]


def test_fix_project_repairs_writing_findings_and_checks_revision():
    """一键修复正文格式问题后持久化，并拒绝旧 revision 覆盖。"""
    proj_id = _create_project_instantiates_sections()
    update = client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [{"id": "p1", "type": "paragraph", "text": "不应加粗的正文。",
                        "bold": True, "italic": False, "underline": False}],
    })
    assert update.status_code == 200
    assert update.json()["revision"] == 1

    resp = client.post(f"/api/projects/{proj_id}/fix", json={"base_revision": 1})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["fixed_count"] >= 1
    assert data["project"]["revision"] == 2
    ch1 = next(section for section in data["project"]["sections"] if section["key"] == "ch1")
    assert ch1["paragraphs"][0]["bold"] is False
    assert not any(f["rule_id"] == "PG-BODY-NO-BOLD" for f in data["report"]["findings"])

    persisted = client.get(f"/api/projects/{proj_id}")
    assert persisted.status_code == 200
    assert persisted.json()["revision"] == 2
    assert next(section for section in persisted.json()["sections"] if section["key"] == "ch1")["paragraphs"][0]["bold"] is False

    stale = client.post(f"/api/projects/{proj_id}/fix", json={"base_revision": 1})
    assert stale.status_code == 409
    assert client.get(f"/api/projects/{proj_id}").json()["revision"] == 2


def test_fix_project_repairs_bound_template_styles_and_checks_again():
    """一键修复模板样式问题, 保存模板快照并让复检结果消失。"""
    base = client.get("/api/templates/default-gb7713").json()
    styles = {key: dict(value) for key, value in base["styles"].items()}
    styles["body"].update({
        "font_zh": "仿宋",
        "font_en": "Arial",
        "size_pt": 10.0,
        "line_spacing": 1.0,
        "space_before_pt": 6.0,
        "space_after_pt": 6.0,
        "first_line_indent_chars": 0.0,
        "align": "left",
    })
    styles["heading1"].update({
        "font_zh": "宋体",
        "size_pt": 12.0,
        "line_spacing": 1.0,
        "page_break_before": False,
    })
    created = client.post("/api/templates", json={
        **base, "id": "", "name": "一键修复样式测试", "styles": styles,
    })
    assert created.status_code == 201, created.text
    proj = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "模板样式修复测试",
    })
    assert proj.status_code == 201, proj.text
    proj_id = proj.json()["id"]
    update = client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [{"id": "p1", "type": "paragraph", "text": "正文样式测试。"}],
    })
    assert update.status_code == 200, update.text

    resp = client.post(f"/api/projects/{proj_id}/fix", json={"base_revision": 1})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["fixed_count"] >= 10, data["fixed_rule_ids"]
    assert data["project"]["revision"] == 2
    assert data["project"]["template_snapshot"]["styles"]["body"]["font_zh"] == "宋体"
    assert data["project"]["template_snapshot"]["styles"]["body"]["size_pt"] == 12.0
    assert data["project"]["template_snapshot"]["styles"]["body"]["line_spacing"] == 1.5
    assert data["project"]["template_snapshot"]["styles"]["body"]["space_before_pt"] == 0.0
    assert data["project"]["template_snapshot"]["styles"]["body"]["space_after_pt"] == 0.0
    assert data["project"]["template_snapshot"]["styles"]["heading1"]["font_zh"] == "黑体"
    assert data["project"]["template_snapshot"]["styles"]["heading1"]["size_pt"] == 18.0
    assert data["project"]["template_snapshot"]["styles"]["heading1"]["line_spacing"] == 1.5
    assert data["project"]["template_snapshot"]["styles"]["heading1"]["page_break_before"] is True
    assert not data["report"]["findings"], data["report"]["findings"]

    persisted = client.get(f"/api/projects/{proj_id}")
    assert persisted.status_code == 200
    assert persisted.json()["template_snapshot"]["styles"]["body"]["font_zh"] == "宋体"


def test_fix_project_repairs_all_detectable_body_rules_in_custom_section():
    """手动章节中的所有可自动确定格式都应一次修复并通过复检。"""
    base = client.get("/api/templates/default-gb7713").json()
    styles = {key: dict(value) for key, value in base["styles"].items()}
    styles["body"].update({
        "font_zh": "仿宋",
        "font_en": "Arial",
        "size_pt": 10.0,
        "line_spacing": 1.0,
        "space_before_pt": 6.0,
        "space_after_pt": 8.0,
        "first_line_indent_chars": 0.0,
        "align": "left",
        "bold": True,
        "italic": True,
        "underline": True,
        "color": "FF0000",
        "highlight": "yellow",
        "vert_align": "superscript",
    })
    created = client.post("/api/templates", json={
        **base, "id": "", "name": "全量一键修复测试", "styles": styles,
    })
    assert created.status_code == 201, created.text
    proj = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "全量修复测试",
    })
    assert proj.status_code == 201, proj.text
    proj_id = proj.json()["id"]

    current = client.get(f"/api/projects/{proj_id}").json()
    sections = [{"key": section["key"], "type": section["type"],
                 "title": section["title"], "required": section["required"]}
                for section in current["sections"]]
    sections.append({"key": "doc_heading_6_2", "type": "heading2",
                     "title": "6.2 系统功能测试", "required": False})
    structure = client.put(f"/api/projects/{proj_id}/sections", json={"sections": sections})
    assert structure.status_code == 200, structure.text
    update = client.put(f"/api/projects/{proj_id}/section/doc_heading_6_2", json={
        "title": "6.2 系统功能测试",
        "paragraphs": [{"id": "p-6-2", "type": "paragraph", "text": "测试正文。",
                        "bold": True, "italic": True, "underline": True,
                        "highlight": "yellow", "vert_align": "superscript"}],
    })
    assert update.status_code == 200, update.text

    before = client.get(f"/api/projects/{proj_id}/check?pack=GB-T-7713-1-2006")
    assert before.status_code == 200, before.text
    assert any(f["section_key"] == "doc_heading_6_2" for f in before.json()["findings"])
    assert any("行距" in f["message"] for f in before.json()["findings"])
    assert any("首行" in f["message"] for f in before.json()["findings"])
    assert any("两端对齐" in f["message"] for f in before.json()["findings"])

    fixed = client.post(f"/api/projects/{proj_id}/fix", json={
        "base_revision": update.json()["revision"],
    })
    assert fixed.status_code == 200, fixed.text
    data = fixed.json()
    assert data["fixed_count"] >= 15, data["fixed_rule_ids"]
    assert not data["report"]["findings"], data["report"]["findings"]
    body_style = data["project"]["template_snapshot"]["styles"]["body"]
    assert body_style["line_spacing"] == 1.5
    assert body_style["first_line_indent_chars"] == 2.0
    assert body_style["align"] == "justify"
    assert body_style["highlight"] is None
    assert body_style["vert_align"] is None
    custom = next(section for section in data["project"]["sections"]
                  if section["key"] == "doc_heading_6_2")
    assert custom["paragraphs"][0]["bold"] is False
    assert custom["paragraphs"][0]["highlight"] is None
    assert custom["paragraphs"][0]["vert_align"] is None


def test_check_project_abstract_does_not_use_body_rules():
    """摘要不应被 GB/T 正文样式规则误报。"""
    proj_id = _create_project_instantiates_sections()
    client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "title": "摘要",
        "paragraphs": [{"id": "p1", "type": "paragraph", "text": "摘要内容。",
                        "bold": True, "italic": False, "underline": False}],
    })
    resp = client.get(f"/api/projects/{proj_id}/check?pack=GB-T-7713-1-2006")
    assert resp.status_code == 200
    assert not any(f["section_key"] == "abstract" for f in resp.json()["findings"])


def test_check_empty_project_ok():
    """空项目检查不报错, 返回空结果。"""
    proj_id = _create_project_instantiates_sections()
    resp = client.get(f"/api/projects/{proj_id}/check")
    assert resp.status_code == 200
    assert resp.json()["summary"]["total"] >= 0


def test_import_detects_section_tree():
    """导入应识别 Word 标题段落 → 章节树。"""
    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        resp = client.post(
            "/api/templates/import",
            files={"file": ("gb7713-official.docx", f, "application/octet-stream")},
        )
    assert resp.status_code == 200
    data = resp.json()
    sections = data["sections"]
    assert sections, "应识别出章节结构"
    # 导入结果保留摘要等前置结构, 同时包含一级标题和二级标题
    assert any(section["type"] == "abstract" for section in sections)
    heading = next(section for section in sections if section["type"] == "heading1")
    assert heading["required"] is True
    assert heading["title"].strip()
    has_child = any(s["children"] for s in sections if s["type"] == "heading1")
    if has_child:
        parent = next(s for s in sections if s["type"] == "heading1" and s["children"])
        child = parent["children"][0]
        assert child["type"] == "heading2"
        assert child["required"] is False


def test_import_detects_numeric_heading_style_ids():
    """Word 自定义样式 ID 为 1/2/3 时仍应识别章节树。"""
    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        resp = client.post(
            "/api/templates/import",
            files={"file": ("gb7713-official.docx", f, "application/octet-stream")},
        )
    assert resp.status_code == 200
    sections = resp.json()["sections"]
    assert sections
    assert sections[0]["type"] == "abstract"
    assert any(section["type"] == "heading1" for section in sections)
    assert any(section["type"] == "references" for section in sections)



    """登录返回 token, /me 返回用户信息。"""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"
    assert resp.json()["role"] == "admin"


def test_register_and_student_isolation():
    """学生注册后: 只能看到自己的项目, 不能管理模板。"""
    # 注册学生
    register_fixture(client, "stu001", "StudentPass123", display_name="学生一")

    # 学生登录
    login = client.post("/api/auth/login", json={
        "username": "stu001", "password": "StudentPass123",
    })
    assert login.status_code == 200
    student_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    # 学生创建自己的项目
    resp = client.post("/api/projects", json={
        "template_id": "default-gb7713", "title": "学生论文",
    }, headers=student_headers)
    assert resp.status_code == 201
    proj_id = resp.json()["id"]
    assert resp.json()["owner_id"]

    # 学生看不到管理员的项目 (管理员创建过多个)
    mine = client.get("/api/projects", headers=student_headers).json()
    assert all(p["title"] == "学生论文" for p in mine), mine

    # 学生不能创建模板 (403)
    resp = client.post("/api/templates", json={
        "id": "stu-tpl", "name": "x", "styles": {}, "sections": [], "meta_fields": [],
    }, headers=student_headers)
    assert resp.status_code == 403

    # 学生不能访问别人的项目 (用管理员创建的项目 id)
    admin_proj = client.get("/api/projects").json()
    assert admin_proj, "管理员应有项目"
    other = next((p for p in admin_proj if p["id"] != proj_id), None)
    if other:
        resp = client.get(f"/api/projects/{other['id']}", headers=student_headers)
        assert resp.status_code in (403, 404)


def test_unauthorized_requests_rejected():
    """未登录请求应 401。"""
    resp = client.get("/api/projects", headers={"Authorization": ""})
    assert resp.status_code == 401
    resp = client.post("/api/projects", json={"template_id": "default-gb7713"},
                       headers={"Authorization": ""})
    assert resp.status_code == 401


def test_bad_login():
    """错误密码 401, 不存在用户 401。"""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "x12345"})
    assert resp.status_code == 401


def test_large_batch_export_can_queue_without_reading_through_its_own_write_lock(tmp_path, monkeypatch):
    from app.models.project import WritingProject
    from app.services.export_jobs import create_export_job

    store = Store(tmp_path / 'large-export.db')
    store.save_workflow_record('cohort', {'id': 'large-cohort', 'organization_id': 'large-school'})
    # Force cache spilling so a realistic large frozen template obtains an
    # exclusive SQLite lock before the enqueue transaction commits.
    connect = store._connect
    def small_cache():
        conn = connect()
        conn.execute('PRAGMA cache_size=4')
        conn.execute('PRAGMA busy_timeout=50')
        return conn
    monkeypatch.setattr(store, '_connect', small_cache)
    template = PaperTemplate(id='large-export-template', name='大模板', description='母版证据' * 30000)
    project = WritingProject(id='large-export-project', template_id=template.id, owner_id='local-test', organization_context={'batch_id': 'large-cohort'})
    queued = create_export_job(store, project, template, force=False, actor_id='local-test')
    assert queued['status'] == 'queued'
    saved = store.get_workflow_record('project_export', queued['id'])
    assert saved['template_snapshot']['description'] == template.description
    with store._connect() as conn:
        event = conn.execute('SELECT organization_id FROM audit_events WHERE resource_id=?', (queued['id'],)).fetchone()
    assert event['organization_id'] == 'large-school'
    same = create_export_job(store, project, template, force=False, actor_id='local-test')
    assert same['id'] == queued['id']


def test_export_request_limit_cannot_be_bypassed_by_force_and_expired_task_cannot_retry(monkeypatch):
    from app.services.export_jobs import park_expired_project_exports

    monkeypatch.setenv("PAPERGUARD_EXPORT_REQUEST_LIMIT", "2")
    project_id = _create_full_default_gb7713_project()
    store = Store()
    previous_id = None
    for force in ("false", "true"):
        response = client.post(f"/api/projects/{project_id}/export-jobs?force={force}")
        assert response.status_code == 202, response.text
        task = response.json()
        if previous_id:
            assert task["previous_export_id"] == previous_id
        record = store.get_workflow_record("project_export", task["id"])
        record["deadline_at"] = "2000-01-01T00:00:00+00:00"
        assert store.save_workflow_record("project_export", record, expected_revision=record["revision"])
        park_expired_project_exports(store)
        retry = client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry")
        assert retry.status_code == 409
        previous_id = task["id"]

    blocked = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert blocked.status_code == 409
    assert "多次导出失败" in blocked.text


def test_student_can_request_new_export_after_queue_record_is_lost(monkeypatch):
    """The actual owner API recovers an orphan without altering the frozen input."""
    suffix = uuid.uuid4().hex[:8]
    username, password = f"export_recovery_{suffix}", f"ExportRecover-{suffix}-8a"
    register_fixture(client, username, password, display_name="导出恢复学生")
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    monkeypatch.setenv("PAPERGUARD_EXPORT_REQUEST_LIMIT", "3")
    project_id = _create_full_default_gb7713_project(headers=headers)
    path = f"/api/projects/{project_id}/export-jobs"
    first = client.post(path, headers=headers)
    assert first.status_code == 202, first.text
    original = first.json()
    store = Store()
    frozen = store.get_workflow_record("project_export", original["id"])["project_snapshot"]
    with store.workflow_transaction() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (original["job_id"],))

    replacement = client.post(path, headers=headers)

    assert replacement.status_code == 202, replacement.text
    assert replacement.json()["id"] != original["id"]
    assert replacement.json()["previous_export_id"] == original["id"]
    saved = store.get_workflow_record("project_export", original["id"])
    assert saved["status"] == "failed" and saved["error_code"] == "PROJECT_EXPORT_QUEUE_STATE_MISSING"
    assert saved["project_snapshot"] == frozen and not saved["file_path"]
    assert store.get_job(replacement.json()["job_id"])["state"] == "queued"


def test_export_api_keeps_missing_queue_failure_when_force_hits_request_limit(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_EXPORT_REQUEST_LIMIT", "1")
    project_id = _create_full_default_gb7713_project()
    path = f"/api/projects/{project_id}/export-jobs"
    first = client.post(path)
    assert first.status_code == 202, first.text
    original = first.json()
    store = Store()
    frozen = store.get_workflow_record("project_export", original["id"])["project_snapshot"]
    with store.workflow_transaction() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (original["job_id"],))

    blocked = client.post(path + "?force=true")

    assert blocked.status_code == 409 and "多次导出失败" in blocked.text
    saved = store.get_workflow_record("project_export", original["id"])
    assert saved["status"] == "failed" and saved["error_code"] == "PROJECT_EXPORT_QUEUE_STATE_MISSING"
    assert saved["project_snapshot"] == frozen
    assert len(client.get(path).json()) == 1


def test_export_api_does_not_modify_another_projects_live_queue(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("PAPERGUARD_EXPORT_REQUEST_LIMIT", "3")
    other_project = _create_full_default_gb7713_project()
    current_project = _create_full_default_gb7713_project()
    other = client.post(f"/api/projects/{other_project}/export-jobs").json()
    path = f"/api/projects/{current_project}/export-jobs"
    original = client.post(path).json()
    store = Store()
    lease_until = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(timespec="seconds")
    with store.workflow_transaction() as conn:
        conn.execute("UPDATE jobs SET state = 'running', lease_until = ?, lease_token = ?, leased_by = ? WHERE id = ?",
                     (lease_until, "other-live-token", "other-worker", other["job_id"]))
    record = store.get_workflow_record("project_export", original["id"])
    record["job_id"] = other["job_id"]
    assert store.save_workflow_record("project_export", record, expected_revision=record["revision"])
    before = store.get_job(other["job_id"])

    replacement = client.post(path)

    assert replacement.status_code == 202, replacement.text
    assert replacement.json()["previous_export_id"] == original["id"]
    assert store.get_workflow_record("project_export", original["id"])["error_code"] == "PROJECT_EXPORT_QUEUE_STATE_MISSING"
    assert store.get_job(other["job_id"]) == before


def test_title_hierarchy_blocks_formal_exports_until_fixed_even_with_force():
    project_id = _create_full_default_gb7713_project()
    original = client.get(f'/api/projects/{project_id}').json()
    sections = [{key: section[key] for key in ('key', 'type', 'title', 'required')}
                for section in original['sections']]
    for section in sections:
        if section['key'] in {'ch1_1', 'ch1_2'}:
            section['type'] = 'heading3'
    saved = client.put(f'/api/projects/{project_id}/sections', json={
        'base_revision': original['revision'], 'sections': sections,
    })
    assert saved.status_code == 200, saved.text
    assert next(s for s in saved.json()['sections'] if s['key'] == 'ch1_1')['paragraphs'] == next(
        s for s in original['sections'] if s['key'] == 'ch1_1')['paragraphs']
    preview = client.post(f'/api/projects/{project_id}/export-preview?force=true')
    assert preview.status_code == 200
    assert not preview.json()['can_export']
    assert sum('三级标题' in issue for issue in preview.json()['missing']) == 2
    for route in ('export', 'export-jobs'):
        result = client.post(f'/api/projects/{project_id}/{route}?force=true')
        assert result.status_code == 409
        assert sum('三级标题' in issue for issue in result.json()['detail']['missing']) == 2
    next(item for item in sections if item['key'] == 'ch1_1')['type'] = 'heading2'
    repaired = client.put(f'/api/projects/{project_id}/sections', json={
        'base_revision': saved.json()['revision'], 'sections': sections,
    })
    assert repaired.status_code == 200, repaired.text
    preview = client.post(f'/api/projects/{project_id}/export-preview?force=true')
    assert preview.json()['can_export'], preview.text
    result = client.post(f'/api/projects/{project_id}/export?force=true')
    assert result.status_code == 200, result.text


def test_queued_and_legacy_exports_recheck_the_frozen_heading_hierarchy(tmp_path, monkeypatch):
    from app.services import export_jobs, project_validation
    from app.services.export_jobs import create_export_job, run_project_export

    monkeypatch.setattr(export_jobs, 'EXPORT_DIR', tmp_path)
    project_id = _create_full_default_gb7713_project()
    store = Store()
    project = store.get_project(project_id)
    next(section for section in project.sections if section.key == 'ch1_1').type = 'heading3'
    store.save_project(project)
    template = PaperTemplate.model_validate(project.template_snapshot)
    job = create_export_job(store, project, template, force=True, actor_id='user-admin')
    with monkeypatch.context() as patch:
        patch.setattr(project_validation, 'validate_heading_hierarchy', lambda _sections: [])
        old_result = run_project_export(store, job['id'])
    assert old_result['status'] == 'succeeded', old_result
    download = client.get(f'/api/projects/{project_id}/export-jobs/{job["id"]}/download')
    assert download.status_code == 409
    assert any('三级标题' in issue for issue in download.json()['detail']['missing'])

    another_id = _create_full_default_gb7713_project()
    another = store.get_project(another_id)
    next(section for section in another.sections if section.key == 'ch1_1').type = 'heading3'
    store.save_project(another)
    another_template = PaperTemplate.model_validate(another.template_snapshot)
    queued = create_export_job(store, another, another_template, force=True, actor_id='user-admin')
    invoked = []
    monkeypatch.setattr(export_jobs, 'export_project_to_docx', lambda *_args, **_kwargs: invoked.append(True))
    result = run_project_export(store, queued['id'])
    assert result['status'] == 'failed'
    assert not result.get('file_path') and not invoked


def test_stale_failed_export_cannot_be_retried(monkeypatch):
    from app.services import export_jobs
    from app.worker import run_once

    project_id = _create_full_default_gb7713_project()
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("synthetic stale export failure")

    monkeypatch.setattr(export_jobs, "export_project_to_docx", fail_export)
    failed_run = run_once()
    assert task["job_id"] in {item["job_id"] for item in failed_run["exports"]["failed"]}

    project = client.get(f"/api/projects/{project_id}").json()
    section = next(item for item in project["sections"] if item["required"])
    edited = client.put(f"/api/projects/{project_id}/section/{section['key']}", json={
        "paragraphs": [{"id": f"{section['key']}-changed", "text": "已更新论文正文，旧快照不得重试。"}],
    })
    assert edited.status_code == 200, edited.text

    retried = client.post(f"/api/projects/{project_id}/export-jobs/{task['id']}/retry")
    assert retried.status_code == 409
    assert "论文内容已变化" in retried.json()["detail"]


def test_unverified_completed_artifact_is_not_reused_and_active_job_binds_template(tmp_path, monkeypatch):
    from app.models.project import WritingProject
    from app.services import export_jobs
    from app.services.export_jobs import create_export_job

    store = Store(tmp_path / "export-reuse.db")
    output_dir = tmp_path / "exports"
    monkeypatch.setattr(export_jobs, "EXPORT_DIR", output_dir)
    template = PaperTemplate(id="reusable-export-template", name="复用测试模板", version="2")
    project = WritingProject(id="reusable-export-project", template_id=template.id, title="版本一")
    queued = create_export_job(store, project, template, force=True, actor_id="teacher")
    output_dir.mkdir()
    data = b"unverified-docx-bytes"
    output = output_dir / f"{queued['id']}.docx"
    output.write_bytes(data)
    queued.update({"status": "succeeded", "file_path": str(output), "output_check": {
        "status": "pass", "office_review_required": True,
        "output_sha256": hashlib.sha256(data).hexdigest(),
    }, "manual_review": {"status": "confirmed"}})
    queued = store.save_workflow_record("project_export", queued, expected_revision=queued["revision"])

    # Legacy manual confirmation cannot make an unverifiable artifact reusable.
    next_job = create_export_job(store, project, template, force=True, actor_id="teacher")
    assert next_job["id"] != queued["id"] and next_job["status"] == "queued"
    output.write_bytes(data + b"tampered")
    assert create_export_job(store, project, template, force=True, actor_id="teacher")["id"] == next_job["id"]

    changed_template = template.model_copy(update={"name": "不同版本的模板"})
    after_template_change = create_export_job(store, project, changed_template, force=True, actor_id="teacher")
    assert after_template_change["id"] not in {queued["id"], next_job["id"]}
    project.title = "版本二"
    after_content_change = create_export_job(store, project, template, force=True, actor_id="teacher")
    assert after_content_change["id"] not in {queued["id"], next_job["id"], after_template_change["id"]}
    assert after_content_change["content_hash"] != queued["content_hash"]


def test_signed_master_policy_overrides_roundtrip_project_export_and_archive(tmp_path, monkeypatch):
    import json
    import zipfile
    from lxml import etree

    from app.api import workflow as workflow_api
    from app.core.extract.xml_utils import qn
    from app.core.extract.specification import extract_writing_specification
    from app.services.import_template import extract_section_tree, import_template_styles
    from app.services.project_versions import project_content_hash
    from app.services.source_template_profile import ConfirmWritingProfile

    suffix = uuid.uuid4().hex[:10]
    policy_id = f"master-policy-{suffix}"
    template_id = f"master-policy-template-{suffix}"
    source_name = ""
    policy_created = False
    source = Document()
    source.add_heading("第1章 正文", 1)
    source.add_paragraph("母版中的正文样例用于确认可写区域和格式。" * 12)
    source_buffer = BytesIO()
    source.save(source_buffer)

    try:
        from app.services.template_source import delete_template_source

        source_name = save_template_source(source_buffer.getvalue(), preserve_original=True)
        source_path = resolve_template_source(source_name)
        assert source_path is not None
        template = PaperTemplate(
            id=template_id,
            name="规范覆盖签名母版",
            source_docx=source_name,
            sections=extract_section_tree(source_path),
            styles=import_template_styles(source_path)["styles"],
        )
        specification = extract_writing_specification(source_path)
        confirmation = ConfirmWritingProfile(
            expected_specification_sha256=specification.specification_sha256,
            accept_recommended=True,
            accept_new_object_defaults=True,
            remove_template_instructions=True,
            preserve_manuscript_structure=True,
        )

        policy = client.post("/api/policies", json={
            "id": policy_id,
            "family_id": policy_id,
            "name": "母版成品格式覆盖测试",
            "level": "school",
            "scope": "global",
            "status": "draft",
            "version": "1",
            "format_overrides": {
                "body": {"size_pt": 14.0},
                "heading1": {"size_pt": 18.0},
            },
            "layout_overrides": {"left_margin_cm": 2.0},
            "items": [],
        })
        assert policy.status_code == 201, policy.text
        policy_created = True
        published = client.post(f"/api/policies/{policy_id}/publish")
        assert published.status_code == 200, published.text

        template.requirement_set_ids = [policy_id]
        created_template = client.post("/api/templates", json=template.model_dump(mode="json"))
        assert created_template.status_code == 201, created_template.text
        proposal = client.post(
            f"/api/templates/{template_id}/writing-profile/propose",
            json=confirmation.model_dump(mode="json"),
        )
        assert proposal.status_code == 200, proposal.text
        confirmation = confirmation.model_copy(update={"proposal_id": proposal.json()["proposal_id"]})
        published = client.post(
            f"/api/templates/{template_id}/publish-writing-profile",
            json=confirmation.model_dump(mode="json"),
        )
        assert published.status_code == 200, published.text
        created = client.post("/api/projects", json={"template_id": template_id, "title": "冻结规范往返样稿"})
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]

        loaded = client.get(f"/api/projects/{project_id}")
        assert loaded.status_code == 200, loaded.text
        project = loaded.json()
        assert project["template_snapshot"]["styles"]["body"]["size_pt"] == 14.0
        assert project["template_source_snapshot"]["styles"]["body"]["size_pt"] != 14.0
        assert project["template_snapshot"]["page_layout"]["left_margin_cm"] == 2.0
        assert project["template_snapshot"]["source_execution"]["signature"]
        section_layout = source.sections[0]
        expected_width = (section_layout.page_width.twips - round(2.0 * 567)
                          - section_layout.right_margin.twips) / 567
        for heading in project["template_snapshot"]["source_execution"]["headings"]:
            assert heading["page_box_cm"][0] == pytest.approx(expected_width)

        section = next(item for item in project["sections"] if item["type"] == "heading1")
        saved = client.put(f"/api/projects/{project_id}/section/{section['key']}", json={
            "base_revision": project["revision"],
            "title": section["title"],
            "paragraphs": [{"id": "policy-body", "text": "学生正文用于核对冻结规范样式。"}],
        })
        assert saved.status_code == 200, saved.text

        queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
        assert queued.status_code == 202, queued.text
        task = queued.json()
        from app.worker import run_once

        worker_result = run_once()
        completed = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}")
        assert completed.status_code == 200, completed.text
        assert task["job_id"] in worker_result["exports"]["succeeded"], completed.json().get("output_check_diagnostic")
        assert completed.json()["output_check_status"] == "unverifiable"
        assert client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download").status_code == 409

        # The remaining assertions isolate policy formatting and archive plumbing.
        # Pagination approval is mocked here and separately tested by the actual gate.
        from app.services import master_docx_check
        monkeypatch.setattr(master_docx_check, "_apply_master_layout_review_policy",
                            lambda report, _receipt: report.update(office_review_required=False, manual_checks=[]))
        queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
        assert queued.status_code == 202, queued.text
        task = queued.json()
        worker_result = run_once()
        completed = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}")
        assert task["job_id"] in worker_result["exports"]["succeeded"], completed.json().get("output_check_diagnostic")
        assert completed.json()["output_check_status"] == "pass"
        downloaded = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
        assert downloaded.status_code == 200, downloaded.text

        def assert_policy_format(data: bytes) -> None:
            with zipfile.ZipFile(BytesIO(data)) as package:
                root = etree.fromstring(package.read("word/document.xml"))
            paragraphs = root.findall(".//" + qn("w:p"))
            body_paragraph = next(
                paragraph for paragraph in paragraphs
                if "学生正文用于核对冻结规范样式。" in "".join(
                    text.text or "" for text in paragraph.findall(".//" + qn("w:t"))
                )
            )
            body_sizes = [run.find(qn("w:rPr")).find(qn("w:sz")).get(qn("w:val"))
                          for run in body_paragraph.findall(".//" + qn("w:r"))
                          if run.find(qn("w:rPr")) is not None and run.find(qn("w:rPr")).find(qn("w:sz")) is not None]
            assert body_sizes and set(body_sizes) == {"28"}
            heading_paragraph = next(
                paragraph for paragraph in paragraphs
                if "第1章 正文" in "".join(
                    text.text or "" for text in paragraph.findall(".//" + qn("w:t"))
                )
            )
            heading_sizes = [run.find(qn("w:rPr")).find(qn("w:sz")).get(qn("w:val"))
                             for run in heading_paragraph.findall(".//" + qn("w:r"))
                             if run.find(qn("w:rPr")) is not None and run.find(qn("w:rPr")).find(qn("w:sz")) is not None]
            assert heading_sizes and set(heading_sizes) == {"36"}
            section_properties = root.findall(".//" + qn("w:sectPr"))
            assert section_properties
            assert all(
                int(section.find(qn("w:pgMar")).get(qn("w:left"))) == round(2.0 * 567)
                for section in section_properties
            )

        assert_policy_format(downloaded.content)

        store = Store()
        project = store.get_project(project_id)
        version_id = f"master-policy-version-{suffix}"
        submission_id = f"master-policy-submission-{suffix}"
        participant_id = f"master-policy-participant-{suffix}"
        cohort_id = f"master-policy-cohort-{suffix}"
        archive_id = f"master-policy-archive-{suffix}"
        from app.services.project_versions import project_attachment_refs
        candidate_id = f"candidate-{uuid.uuid4().hex[:12]}"
        candidate_path = tmp_path / "candidate.docx"
        monkeypatch.setattr(workflow_api, "_candidate_artifact_path", lambda _candidate_id: candidate_path)
        candidate_path.write_bytes(downloaded.content)
        version = {
            "id": version_id, "project_id": project.id,
            "content_hash": project_content_hash(project),
            "content_snapshot": project.model_dump(mode="json"),
            "attachment_refs": project_attachment_refs(project),
        }
        store.save_workflow_record("project_version", version)
        stored_export = store.get_workflow_record("project_export", task["id"])
        store.save_workflow_record("candidate", {
            "id": candidate_id, "project_version_id": version_id, "project_id": project.id,
            "content_hash": version["content_hash"], "participant_id": participant_id,
            "artifact": {"docx_sha256": hashlib.sha256(downloaded.content).hexdigest(),
                         "output_check": stored_export["output_check"]},
        })
        store.save_workflow_record("submission", {
            "id": submission_id,
            "project_version_id": version_id,
            "participant_id": participant_id,
            "candidate_id": candidate_id,
            "submission_number": 1,
            "stage_config_id": "test-stage",
            "stage_config_snapshot": {},
        })
        admin_id = project.owner_id
        store.save_workflow_record("cohort", {"id": cohort_id, "organization_id": ""})
        store.save_workflow_record("participant", {
            "id": participant_id,
            "cohort_id": cohort_id,
            "student_id": admin_id,
            "state": "pending_archive",
        })
        monkeypatch.setattr(workflow_api, "_archive_dir", lambda: tmp_path / "archives")
        archive_job_id = f"archive-job-{suffix}"
        store.save_workflow_record("archive", {
            "id": archive_id,
            "job_id": archive_job_id,
            "submission_id": submission_id,
            "participant_id": participant_id,
            "source_template_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "status": "queued",
        })
        with store.workflow_transaction() as conn:
            store.create_job_conn(conn, job_id=archive_job_id, job_type="archive.generate",
                                  resource_type="archive", resource_id=archive_id)
        job = next(item for item in store.claim_jobs(job_type="archive.generate", consumer_id="policy-test", limit=50)
                   if item["id"] == archive_job_id)
        archived = workflow_api._run_archive(store, archive_id, actor_id=admin_id, lease_token=job["lease_token"])
        assert archived["status"] == "succeeded", archived
        with zipfile.ZipFile(archived["file_path"]) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            requirements = json.loads(bundle.read("requirements.json"))
            archived_template = json.loads(bundle.read("template.json"))
            final_docx = bundle.read("final.docx")
        assert requirements["format_overrides"]["body"]["size_pt"] == 14.0
        assert manifest["requirements_hash"] == workflow_api._hash(requirements)
        assert archived_template["source_execution"]["policy_snapshot_sha256"]
        assert_policy_format(final_docx)
    finally:
        if policy_created:
            client.post(f"/api/policies/{policy_id}/archive")
        if source_name:
            delete_template_source(source_name)


@pytest.mark.parametrize("endpoint", ["export", "export-jobs"])
def test_export_preflight_reports_unmappable_reference_format_even_with_force(endpoint):
    project_id = _create_full_default_gb7713_project()
    current = client.get(f"/api/projects/{project_id}").json()
    response = client.put(f"/api/projects/{project_id}/section/references", json={
        "base_revision": current["revision"], "title": "参考文献",
        "paragraphs": [{
            "id": "references-sample", "text": "[1] 旧作者. 旧题名",
            "inline_runs": [{"text": "[1] 旧作者. "}, {"text": "旧题名", "italic": True}],
            "reference": {"kind": "journal", "authors": "新作者", "title": "新题名",
                          "source": "期刊", "year": "2025"},
        }],
    })
    assert response.status_code == 200, response.text
    result = client.post(f"/api/projects/{project_id}/{endpoint}?force=true")
    assert result.status_code == 409, result.text
    assert any("参考文献第 1 条" in item and "行内格式" in item
               for item in result.json()["detail"]["missing"])


def test_legacy_table_export_snapshot_remains_downloadable():
    from app.worker import run_once

    project_id = _create_full_default_gb7713_project()
    current = client.get(f"/api/projects/{project_id}").json()
    chapter = next(section for section in current["sections"] if section["key"] == "ch1")
    response = client.put(f"/api/projects/{project_id}/section/ch1", json={
        "base_revision": current["revision"], "title": chapter["title"],
        "paragraphs": [*chapter["paragraphs"], {"id": "old-table", "type": "table", "table": {
            "rows": 1, "cols": 1, "cells": [["样例数据"]], "caption": "样例表",
        }}],
    })
    assert response.status_code == 200, response.text
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()
    result = run_once()
    assert task["job_id"] in result["exports"]["succeeded"]
    record = Store().get_workflow_record("project_export", task["id"])
    snapshot_chapter = next(section for section in record["project_snapshot"]["sections"] if section["key"] == "ch1")
    table = next(block["table"] for block in snapshot_chapter["paragraphs"] if block.get("type") == "table")
    assert table.pop("borders") is None
    Store().save_workflow_record("project_export", record)
    downloaded = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content.startswith(b"PK")


def test_approved_student_can_download_existing_export_but_not_edit():
    from app.api.templates import get_store
    from app.auth import current_user
    from app.worker import run_once

    project_id = _create_full_default_gb7713_project()
    store = Store()
    project = store.get_project(project_id)
    student = {"id": "readonly-export-student", "role": "student", "status": "active"}
    project.owner_id = student["id"]
    store.save_project(project)
    queued = client.post(f"/api/projects/{project_id}/export-jobs?force=true")
    assert queued.status_code == 202, queued.text
    task = queued.json()
    assert task["job_id"] in run_once()["exports"]["succeeded"]
    store.save_workflow_record("cohort", {"id": "readonly-export-cohort", "status": "open"})
    store.save_workflow_record("participant", {"id": "readonly-export-participant", "project_id": project_id,
        "student_id": student["id"], "cohort_id": "readonly-export-cohort", "state": "approved"})
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: student
    try:
        path = f"/api/projects/{project_id}"
        capabilities = client.get(path + "/capabilities")
        assert capabilities.status_code == 200, capabilities.text
        assert capabilities.json()["formal_delivery_enabled"] is True
        jobs = client.get(path + "/export-jobs")
        assert jobs.status_code == 200 and any(job["id"] == task["id"] for job in jobs.json())
        downloaded = client.get(path + f"/export-jobs/{task['id']}/download")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content.startswith(b"PK")
        assert client.put(path + "/meta", json={"base_revision": project.revision, "meta": {}}).status_code == 409
        app.dependency_overrides[current_user] = lambda: {**student, "id": "another-student"}
        assert client.get(path + "/capabilities").status_code == 403
        assert client.get(path + f"/export-jobs/{task['id']}/download").status_code == 404
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(current_user, None)
