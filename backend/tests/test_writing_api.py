"""模板 + 写作项目 + docx 导出 API 测试。

覆盖:
- 模板: 列表 / 详情 / 创建 / 更新 / 删除 / 内置模板种子
- 项目: 基于模板创建(章节树实例化) / 章节内容保存 / 元信息 / 导出
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

client = TestClient(app)

# 登录内置管理员, 所有 API 请求带 token (TestClient 创建时触发 startup seed)
_login = client.post("/api/auth/login", json={"username": "admin", "password": "TestAdminPassword123"})
assert _login.status_code == 200, _login.text
client.headers.update({"Authorization": f"Bearer {_login.json()['token']}"})


@pytest.fixture(scope="module")
def store():
    return Store(":memory:")


def test_default_template_seeded():
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    templates = resp.json()
    ids = [t["id"] for t in templates]
    assert "default-gb7713" in ids, f"内置模板应已种子化: {ids}"


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
    assert forced.status_code == 200 and forced.json()["can_export"] is True


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
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"  # docx 是 zip

    # 验证导出的 docx 可重新加载, 内容存在
    import docx as docx_lib
    import io
    d = docx_lib.Document(io.BytesIO(resp.content))
    texts = [p.text for p in d.paragraphs]
    assert any("摘要" in t for t in texts), texts[:10]
    assert any("这是摘要内容" in t for t in texts)
    assert any("论文题目" in t for t in texts)


def test_export_missing_project_404():
    assert client.post("/api/projects/nonexist/export").status_code == 404


def test_durable_project_export_job_roundtrip():
    """DOCX export can be queued, recovered by the worker, and downloaded safely."""
    from app.services.export_jobs import EXPORT_DIR
    from app.worker import run_once

    project_id = _create_project_instantiates_sections()
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
    downloaded = client.get(f"/api/projects/{project_id}/export-jobs/{task['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content[:2] == b"PK"
    audit = client.get("/api/audit", params={"resource_type": "project_export"})
    assert audit.status_code == 200
    assert {item["action"] for item in audit.json() if item["resource_id"] == task["id"]} >= {
        "project.export.queued", "project.export.succeeded"
    }
    output = EXPORT_DIR / f"{task['id']}.docx"
    if output.exists():
        output.unlink()


def test_durable_project_export_failure_can_be_retried(monkeypatch):
    """A failed export returns to queued state through the manager retry path."""
    from app.services import export_jobs
    from app.services.export_jobs import EXPORT_DIR
    from app.worker import run_once

    project_id = _create_project_instantiates_sections()
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


def test_export_uses_template_docx_as_base():
    """导出以模板 docx 为基底: 页面设置与样式表来自模板, 而非 python-docx 默认值。"""
    import io

    import docx as docx_lib

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
    proj = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "基底测试",
    })
    proj_id = proj.json()["id"]
    client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "title": "摘要", "paragraphs": [{"id": "p1", "text": "正文内容。"}],
    })

    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 200, resp.text
    d = docx_lib.Document(io.BytesIO(resp.content))
    section = d.sections[0]
    # 模板是 Letter (12240x15840) + 1797 左右边距; 回退的空白文档是 A4 + 1797/1440
    assert abs(section.page_width.twips - 12240) <= 2, section.page_width.twips
    assert abs(section.page_height.twips - 15840) <= 2, section.page_height.twips
    assert abs(section.left_margin.twips - 1797) <= 2
    # 样式表来自模板 (python-docx 默认样式表里没有 Heading1 这个 styleId)
    style_ids = {s.style_id for s in d.styles}
    assert "Heading1" in style_ids, sorted(style_ids)[:20]


def test_import_detects_cover_layout_and_export_renders_it():
    """封面版式(静态标题行 + 字段行)应被识别, 导出按版式渲染而非逐行 meta。"""
    import io

    import docx as docx_lib

    fixture = FIXTURES / "templates" / "gb7713-official.docx"
    with fixture.open("rb") as f:
        cfg = client.post(
            "/api/templates/import",
            files={"file": ("gb7713-official.docx", f, "application/octet-stream")},
        ).json()
    kinds = [b["kind"] for b in cfg["cover_blocks"]]
    assert "text" in kinds, cfg["cover_blocks"]
    assert kinds.count("field") >= 2, cfg["cover_blocks"]

    created = client.post("/api/templates", json={
        "id": "", "name": "封面版式测试",
        "sections": cfg["sections"], "styles": cfg["styles"],
        "meta_fields": cfg["meta_fields"], "cover_blocks": cfg["cover_blocks"],
        "source_docx": cfg["source_docx"],
    })
    proj_id = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "封面测试",
    }).json()["id"]

    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 200, resp.text
    d = docx_lib.Document(io.BytesIO(resp.content))
    texts = [p.text for p in d.paragraphs]
    # 静态标题行按模板原样渲染
    assert "学位论文（GB/T 7713.1-2006）" in texts, texts[:8]
    # 字段行按"标签："渲染(值为空)
    assert any(t.startswith("学号：") for t in texts), texts[:8]
    # 封面/前置/正文分节
    assert len(d.sections) >= 2


def test_import_preserves_cover_labels_in_export_and_keeps_form_labels_canonical(tmp_path):
    """导入模板应保留封面标签原文，同时继续给填写表单提供简洁字段名。"""
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
    project = client.post("/api/projects", json={
        "template_id": created.json()["id"], "title": "示例论文",
    })
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    updated = client.put(f"/api/projects/{project_id}/meta", json={
        "meta": {
            "title": "示例论文", "college": "测试学院", "major": "计算机科学与技术",
            "class_name": "测试班级", "author": "测试学生", "student_id": "TEST-ID-001",
            "advisor": "指导教师甲 指导教师乙", "date": "2026年5月",
        },
    })
    assert updated.status_code == 200, updated.text

    exported = client.post(f"/api/projects/{project_id}/export?force=true")
    assert exported.status_code == 200, exported.text
    document = DocxDocument(io.BytesIO(exported.content))
    exported_table = document.tables[0]
    assert [row.cells[0].text for row in exported_table.rows] == labels
    assert [row.cells[1].text for row in exported_table.rows] == [
        "测试学院", "计算机科学与技术", "测试班级", "测试学生", "TEST-ID-001", "指导教师甲 指导教师乙",
    ]


def test_switch_project_template_keeps_content():
    """换成另一个模板: 排版/封面换成新模板, 已写内容按章节 key 保留并补全新章节。"""
    import io

    import docx as docx_lib

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
    assert exported.status_code == 200, exported.text
    d = docx_lib.Document(io.BytesIO(exported.content))
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

    outsider = client.post("/api/auth/register", json={
        "username": "image-outsider", "password": "OutsiderPassword123", "role": "student",
    })
    assert outsider.status_code == 201, outsider.text
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


def test_export_docx_with_image_and_table():
    """导出的 docx 应包含图片、表格和题注。"""
    import io
    import docx as docx_lib

    proj_id = _create_project_instantiates_sections()
    img = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fig1.png", _make_image_png(), "image/png")},
    ).json()["url"]
    client.put(f"/api/projects/{proj_id}/section/ch1", json={
        "title": "第一章 绪论",
        "paragraphs": [
            {"id": "p1", "type": "paragraph", "text": "正文内容。"},
            {"id": "img1", "type": "image",
             "image": {"url": img, "caption": "图 1.1 系统架构"}},
            {"id": "tbl1", "type": "table",
             "table": {"rows": 2, "cols": 2,
                       "cells": [["方法", "准确率"], ["A", "95%"]],
                       "caption": "表 1.1 对比结果"}},
        ],
    })
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 200
    d = docx_lib.Document(io.BytesIO(resp.content))
    texts = [p.text for p in d.paragraphs]
    assert "图 1.1 系统架构" in texts
    assert "表 1.1 对比结果" in texts
    # 表格存在且有数据
    assert len(d.tables) == 1
    assert d.tables[0].cell(0, 0).text == "方法"
    assert d.tables[0].cell(1, 1).text == "95%"
    # 图片存在: 检查内嵌图片数量 (inline shapes)
    assert len(d.inline_shapes) == 1


def test_docx_imported_image_exports_from_configured_data_dir(tmp_path, monkeypatch):
    """DOCX import and export must resolve image assets from the same configured data directory."""
    import io
    from docx import Document as DocxDocument
    from docx.shared import Cm

    from app.services.document_assets import document_upload_dir

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
        params={"apply": "true", "base_revision": preview.json()["project_revision"]},
        files={"file": ("manuscript.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
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
    assert exported.status_code == 200, exported.text
    document = DocxDocument(io.BytesIO(exported.content))
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
    uploads = Path(__file__).resolve().parents[1] / "data" / "uploads"
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
    uploads = Path(__file__).resolve().parents[1] / "data" / "uploads"

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
    uploads = Path(__file__).resolve().parents[1] / "data" / "uploads"

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


def test_export_force_skips_validation():
    """force=true 跳过必填校验。"""
    proj_id = _create_project_instantiates_sections()
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"


def test_export_docx_has_toc_field_and_page_footer():
    """导出文档应包含 TOC 域和页脚页码域。"""
    import io
    import docx as docx_lib

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
    assert resp.status_code == 200
    d = docx_lib.Document(io.BytesIO(resp.content))

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


def test_captions_auto_numbered():
    """图表题注自动编号: 图 1.1 / 表 1.1, 手填编号会被替换。"""
    import io
    import docx as docx_lib

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
    assert resp.status_code == 200
    d = docx_lib.Document(io.BytesIO(resp.content))
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
    resp = client.post("/api/auth/register", json={
        "username": "stu001", "password": "StudentPass123", "display_name": "学生一",
    })
    assert resp.status_code == 201

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
