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


def test_create_project_instantiates_sections():
    resp = client.post("/api/projects", json={
        "template_id": "default-gb7713",
        "title": "我的论文",
    })
    assert resp.status_code == 201
    proj = resp.json()
    assert proj["title"] == "我的论文"
    keys = [s["key"] for s in proj["sections"]]
    # 封面由 meta 管理, 不在章节列表; 其余结构应实例化
    assert "cover" not in keys
    assert "abstract" in keys and "ch1" in keys and "ch1_1" in keys
    # 摘要应有段落占位
    abstract = next(s for s in proj["sections"] if s["key"] == "abstract")
    assert abstract["paragraphs"]
    # 元信息字段已初始化
    assert "author" in proj["meta"]
    return proj["id"]


def test_update_section_content():
    proj_id = test_create_project_instantiates_sections()
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


def test_update_meta():
    proj_id = test_create_project_instantiates_sections()
    resp = client.put(f"/api/projects/{proj_id}/meta", json={
        "meta": {"author": "张三", "title": "基于XX的研究"},
    })
    assert resp.status_code == 200
    assert resp.json()["meta"]["author"] == "张三"


def test_export_docx():
    proj_id = test_create_project_instantiates_sections()
    client.put(f"/api/projects/{proj_id}/section/abstract", json={
        "title": "摘要",
        "paragraphs": [{"id": "p1", "text": "这是摘要内容。"}],
    })
    client.put(f"/api/projects/{proj_id}/meta", json={
        "meta": {"author": "张三", "title": "论文题目"},
    })
    resp = client.post(f"/api/projects/{proj_id}/export")
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


def test_import_docx_template_detects_styles():
    """上传 docx 应识别出标题/正文的字体字号等样式。"""
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
    assert h1["size_pt"] == 14.0          # 28 half-points / 2
    assert h1["bold"] is True
    assert "body" in data["styles"]
    assert data["style_names"], "应识别到至少一个 Word 样式"
