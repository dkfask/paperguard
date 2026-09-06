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


def test_create_project_instantiates_sections():
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


def test_section_revision_conflict_does_not_overwrite():
    """旧编辑器快照不能覆盖已经保存的新内容。"""
    proj_id = test_create_project_instantiates_sections()
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
    proj_id = test_create_project_instantiates_sections()
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


def _make_image_png() -> bytes:
    """生成 1x1 红色 PNG (最小合法 PNG)。"""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )


def test_upload_rejects_fake_image():
    proj_id = test_create_project_instantiates_sections()
    resp = client.post(
        f"/api/projects/{proj_id}/images",
        files={"file": ("fake.png", b"<html>not image</html>", "image/png")},
    )
    assert resp.status_code == 400



    proj_id = test_create_project_instantiates_sections()
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
    proj_id = test_create_project_instantiates_sections()
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

    proj_id = test_create_project_instantiates_sections()
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


def test_removed_image_block_waits_for_orphan_cleanup():
    """删除图片块后先保留文件, 由孤儿回收统一清理。"""
    from pathlib import Path

    proj_id = test_create_project_instantiates_sections()
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


def test_delete_project_removes_images():
    """删除项目时, 该项目的所有图片文件一并删除。"""
    from pathlib import Path

    proj_id = test_create_project_instantiates_sections()
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
    assert not (uploads / fname).exists(), "删除项目后图片文件应被清理"


def test_cleanup_orphan_images():
    """cleanup_orphan_images 删除未被引用的文件, 保留被引用的。"""
    from pathlib import Path

    from app.db import Store
    from app.services.image_cleanup import cleanup_orphan_images

    store = Store()
    uploads = Path(__file__).resolve().parents[1] / "data" / "uploads"

    # 被引用图片
    proj_id = test_create_project_instantiates_sections()
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
    proj_id = test_create_project_instantiates_sections()
    resp = client.post(f"/api/projects/{proj_id}/export")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "missing" in detail
    missing = detail["missing"]
    assert any("封面" in m for m in missing)          # 封面必填字段
    assert any("第一章" in m for m in missing)        # 必填章节


def test_export_force_skips_validation():
    """force=true 跳过必填校验。"""
    proj_id = test_create_project_instantiates_sections()
    resp = client.post(f"/api/projects/{proj_id}/export?force=true")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"


def test_export_docx_has_toc_field_and_page_footer():
    """导出文档应包含 TOC 域和页脚页码域。"""
    import io
    import docx as docx_lib

    proj_id = test_create_project_instantiates_sections()
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

    # 页脚页码域存在
    footer_xml = d.sections[0].footer._element.xml if d.sections[0].footer else ""
    assert "PAGE" in footer_xml, "页脚应包含页码域"


def test_captions_auto_numbered():
    """图表题注自动编号: 图 1.1 / 表 1.1, 手填编号会被替换。"""
    import io
    import docx as docx_lib

    proj_id = test_create_project_instantiates_sections()
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
    proj_id = test_create_project_instantiates_sections()
    resp = client.get(f"/api/projects/{proj_id}/template-check")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["context"]["mode"] == "project-template"
    assert data["context"]["template_name"]
    assert "summary" in data and "findings" in data
    assert not any(".section.cover." in finding["rule_id"] or ".section.toc." in finding["rule_id"] for finding in data["findings"])


def test_check_project_realtime():
    """写作项目实时检查: 正文加粗违规应被检出并定位到章节。"""
    proj_id = test_create_project_instantiates_sections()
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


def test_check_project_abstract_does_not_use_body_rules():
    """摘要不应被 GB/T 正文样式规则误报。"""
    proj_id = test_create_project_instantiates_sections()
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
    proj_id = test_create_project_instantiates_sections()
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
