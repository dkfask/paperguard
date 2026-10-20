"""多层级规范 P1/P2 生命周期：组织、附件、版本、豁免与检查留痕。"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import ai as ai_module
from app.db import Store
from app.main import app


client = TestClient(app)


# 需求 AI 辅助现在走 _metered_chat（真实上游调用的唯一计量出口），其第一步是
# 出站 SSRF 守卫，会在**发请求前**解析 DNS 并逐个检查地址。本文件用
# `test.invalid` 这个刻意不可解析的假域名，不给公网解析结果就会在守卫阶段
# 直接 400，测不到本来要验证的「辅助结论写回需求项」逻辑。
#
# 只解决「假域名需要被解析成公网地址」这一件事；守卫自身的判定测试在
# test_outbound_guard.py，那里不能带这个 fixture。
@pytest.fixture(autouse=True)
def _fake_public_dns(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # 解除本文件的速率限制。需求 AI 辅助现在走 _metered_chat，会扣突发配额
    # （修复前这条路径完全不计量）。本文件用共享的 `admin` 账号，全量套件里
    # 多个文件都用它跑 AI，累积后会撞上默认的每分钟 10 次上限，报
    # 429/AI_RATE_LIMITED——那是跨文件污染，不是本文件要验证的行为。
    # 速率/配额语义本身由 test_ai_governance.py 专门覆盖。
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "100000")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "100000")


def _random_password(prefix: str) -> str:
    """随机生成符合策略(≥12 位、含字母数字)的测试口令, 避免静态凭据。"""
    return f"{prefix}-{uuid.uuid4().hex}-A1"


def _login(username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _admin_headers() -> dict[str, str]:
    return _login("admin", "TestAdminPassword123")


def _new_student(suffix: str) -> tuple[str, dict[str, str]]:
    username = f"lifecycle-student-{suffix}"
    password = _random_password("student")
    registered = client.post("/api/auth/register", json={
        "username": username, "password": password, "display_name": "生命周期学生",
    })
    assert registered.status_code == 201, registered.text
    return registered.json()["id"], _login(username, password)


def _policy(headers: dict[str, str], *, policy_id: str, version: str = "1", items: list | None = None, **extra):
    payload = {
        "id": policy_id, "name": f"生命周期规范 {version}", "level": "school",
        "status": "published", "version": version, "items": items or [],
    }
    payload.setdefault("scope_id", f"isolated-{policy_id}")
    payload.update(extra)
    response = client.post("/api/policies", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_organization_binding_is_inherited_by_student_project():
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    school = client.post("/api/organizations", headers=admin, json={"id": f"school-{suffix}", "name": "测试大学", "type": "school"})
    assert school.status_code == 201, school.text
    college = client.post("/api/organizations", headers=admin, json={"id": f"college-{suffix}", "name": "测试学院", "type": "college", "parent_id": f"school-{suffix}"})
    assert college.status_code == 201, college.text
    major = client.post("/api/organizations", headers=admin, json={"id": f"major-{suffix}", "name": "测试专业", "type": "major", "parent_id": f"college-{suffix}"})
    assert major.status_code == 201, major.text

    student_id, student = _new_student(suffix)
    context = {"school_id": f"school-{suffix}", "college_id": f"college-{suffix}", "major_id": f"major-{suffix}"}
    assigned = client.put(f"/api/organizations/users/{student_id}/context", headers=admin, json={"organization_context": context})
    assert assigned.status_code == 200, assigned.text
    policy_id = f"major-policy-{suffix}"
    policy = _policy(admin, policy_id=policy_id, items=[{
        "id": "major-section", "type": "auto", "target": "methodology", "checker": "section.exists",
        "params": {}, "severity": "error", "enforce": "hard", "description": "必须包含研究方法",
    }], level="major", bindings=[{
        "id": f"binding-{suffix}", "scope_type": "major", "scope_id": f"major-{suffix}",
    }])
    project = client.post("/api/projects", headers=student, json={"template_id": "default-gb7713", "title": "组织继承项目"})
    assert project.status_code == 201, project.text
    snapshot = project.json()["requirement_snapshot"]
    assert any(item["id"] == policy["id"] for item in snapshot["policy_sets"])
    assert snapshot["context"]["major_id"] == f"major-{suffix}"


def test_requirement_ai_assist_is_saved_to_matching_item(monkeypatch):
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    policy_id = f"assist-policy-{suffix}"
    item_id = f"assist-manual-{suffix}"
    policy = _policy(admin, policy_id=policy_id, scope_id="", items=[{
        "id": item_id, "type": "manual", "target": "document", "checker": "",
        "params": {}, "severity": "warning", "enforce": "soft", "description": "人工核对论文内容",
    }])
    template = client.get("/api/templates/default-gb7713", headers=admin).json()
    template.update({"id": f"assist-template-{suffix}", "name": "AI 辅助保存模板", "requirement_set_ids": [policy["id"]]})
    assert client.post("/api/templates", headers=admin, json=template).status_code == 201
    project = client.post("/api/projects", headers=admin, json={
        "template_id": template["id"], "title": "AI 辅助保存项目",
    })
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    section = next(section for section in project.json()["sections"] if section["type"] not in ("cover", "toc"))
    content = client.put(f"/api/projects/{project_id}/section/{section['key']}", headers=admin, json={
        "paragraphs": [{"id": "assist-content", "text": "这是一段用于人工规范检查的论文正文。"}],
    })
    assert content.status_code == 200, content.text

    monkeypatch.setattr(
        ai_module,
        "_resolve_cfg",
        lambda store, user: ({
            "base_url": "http://test.invalid/v1",
            "api_key": f"key-{uuid.uuid4().hex}",
            "model": "test-model",
        }, "test"),
    )
    monkeypatch.setattr(
        ai_module,
        "_chat_completion",
        lambda cfg, messages, temperature: "已观察到正文证据；建议人工确认。",
    )
    response = client.post(f"/api/projects/{project_id}/requirements/{item_id}/assist", headers=admin, json={
        "question": "请核对正文内容",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["saved"] is True
    assert result["requirement_item_id"] == item_id
    assert result["suggestion"] == "已观察到正文证据；建议人工确认。"
    assert result["project_revision"] >= content.json()["revision"] + 1

    saved_project = client.get(f"/api/projects/{project_id}", headers=admin).json()
    assert len(saved_project["ai_assist_results"]) == 1
    assert saved_project["ai_assist_results"][0]["requirement_item_id"] == item_id
    assert saved_project["ai_assist_results"][0]["suggestion"] == result["suggestion"]


def test_policy_document_version_and_audit_lifecycle():
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    policy_id = f"doc-policy-{suffix}"
    created = _policy(admin, policy_id=policy_id, status="draft")
    uploaded = client.post(
        f"/api/policies/{policy_id}/documents", headers=admin,
        files={"file": ("guide.md", b"# Writing guide\n", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()["documents"][0]
    assert document["checksum"].startswith("sha256:")
    downloaded = client.get(f"/api/policies/{policy_id}/documents/{document['id']}", headers=admin)
    assert downloaded.status_code == 200
    assert downloaded.content == b"# Writing guide\n"
    assert client.post(f"/api/policies/{policy_id}/publish", headers=admin).status_code == 200

    version = client.post(f"/api/policies/{policy_id}/versions", headers=admin, json={"version": "2", "reason": "年度修订"})
    assert version.status_code == 201, version.text
    version_data = version.json()
    assert version_data["family_id"] == created["id"]
    assert version_data["status"] == "draft"
    assert version_data["documents"]
    assert client.post(f"/api/policies/{version_data['id']}/publish", headers=admin).status_code == 200
    copied = client.get(f"/api/policies/{version_data['id']}/documents/{version_data['documents'][0]['id']}", headers=admin)
    assert copied.status_code == 200
    assert copied.content == b"# Writing guide\n"
    audit = client.get(f"/api/policies/{policy_id}/audit", headers=admin)
    assert audit.status_code == 200
    assert any(item["action"] == "created" for item in audit.json())


def test_waiver_approval_removes_requirement_from_gate_and_records_check_run():
    admin = _admin_headers()
    student_id, student = _new_student(uuid.uuid4().hex[:8])
    suffix = uuid.uuid4().hex[:8]
    policy_id = f"waiver-policy-{suffix}"
    policy = _policy(admin, policy_id=policy_id, items=[{
        "id": "waiver-abstract", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
        "params": {"min": 100}, "severity": "error", "enforce": "hard", "description": "摘要不少于 100 字",
    }])
    template = client.get("/api/templates/default-gb7713", headers=admin).json()
    template.update({"id": f"waiver-template-{suffix}", "name": "豁免测试模板", "requirement_set_ids": [policy_id]})
    assert client.post("/api/templates", headers=admin, json=template).status_code == 201
    project = client.post("/api/projects", headers=student, json={"template_id": template["id"], "title": "豁免项目"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    report = client.get(f"/api/projects/{project_id}/check", headers=student)
    assert report.status_code == 200
    assert report.json()["summary"]["hard_failures"] >= 1
    waiver = client.post(f"/api/projects/{project_id}/requirements/waiver-abstract/waiver", headers=student, json={"reason": "研究对象特殊，导师已确认"})
    assert waiver.status_code == 201, waiver.text
    decision = client.post(f"/api/projects/{project_id}/requirements/waivers/{waiver.json()['id']}/decision", headers=admin, json={"status": "approved", "review_note": "同意特殊情况豁免"})
    assert decision.status_code == 200, decision.text
    checked = client.get(f"/api/projects/{project_id}/check", headers=student)
    assert checked.status_code == 200
    assert checked.json()["summary"]["hard_failures"] == 0
    assert checked.json()["summary"]["waived"] == 1
    runs = client.get(f"/api/projects/{project_id}/check-runs", headers=student)
    assert runs.status_code == 200
    assert runs.json()[0]["project_id"] == project_id
    candidate_id = f"waiver-candidate-{uuid.uuid4().hex[:8]}"
    Store().save_workflow_record("candidate", {
        "id": candidate_id, "project_id": project_id,
        "check_report": {"waived": [{"waiver": {"id": waiver.json()["id"]}}]},
        "revision": 0,
    })
    revoked = client.post(
        f"/api/projects/{project_id}/requirements/waivers/{waiver.json()['id']}/revoke",
        headers=admin,
        json={"reason": "复核发现该特殊情况已不再适用"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["waiver"]["status"] == "revoked"
    assert candidate_id in revoked.json()["affected_candidate_ids"]
    marked = Store().get_workflow_record("candidate", candidate_id)
    assert marked["recheck_required"] is True
    rechecked = client.get(f"/api/projects/{project_id}/check", headers=student)
    assert rechecked.status_code == 200
    assert rechecked.json()["summary"]["hard_failures"] >= 1
    assert rechecked.json()["summary"]["waived"] == 0
    audit = client.get("/api/audit-events", headers=admin)
    assert audit.status_code == 200
    assert any(item["action"] == "requirement.waiver.revoked" and item["resource_id"] == project_id for item in audit.json())
    assert student_id


def test_published_version_is_used_by_upgrade_preview_and_application():
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    policy_id = f"upgrade-policy-{suffix}"
    policy = _policy(admin, policy_id=policy_id, version="1", items=[{
        "id": "upgrade-min", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
        "params": {"min": 10}, "severity": "warning", "enforce": "soft", "description": "摘要不少于 10 字",
    }])
    template = client.get("/api/templates/default-gb7713", headers=admin).json()
    template.update({"id": f"upgrade-template-{suffix}", "name": "升级测试模板", "requirement_set_ids": [policy_id]})
    assert client.post("/api/templates", headers=admin, json=template).status_code == 201
    project = client.post("/api/projects", headers=admin, json={"template_id": template["id"], "title": "升级项目"})
    project_id = project.json()["id"]
    version = client.post(f"/api/policies/{policy_id}/versions", headers=admin, json={"version": "2"})
    assert version.status_code == 201
    version_data = version.json()
    assert client.put(f"/api/policies/{version_data['id']}", headers=admin, json={**version_data, "items": [{
        "id": "upgrade-min", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
        "params": {"min": 200}, "severity": "error", "enforce": "hard", "description": "摘要不少于 200 字",
    }]}).status_code == 200
    assert client.post(f"/api/policies/{version_data['id']}/publish", headers=admin).status_code == 200
    preview = client.get(f"/api/projects/{project_id}/requirements/upgrade-preview", headers=admin)
    assert preview.status_code == 200, preview.text
    assert preview.json()["diff"]["changed"] is True
    applied = client.post(f"/api/projects/{project_id}/requirements/upgrade", headers=admin, json={
        "confirm": True, "expected_snapshot_hash": preview.json()["from_snapshot_hash"], "reason": "采用年度新规范",
    })
    assert applied.status_code == 200, applied.text
    assert applied.json()["project"]["requirement_snapshot"]["policy_sets"][0]["version"] == "2"


def test_batch_upgrade_uses_policy_family_for_projects_on_older_version():
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    policy_id = f"batch-policy-{suffix}"
    policy = _policy(admin, policy_id=policy_id, version="1", items=[{
        "id": "batch-min", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
        "params": {"min": 10}, "severity": "warning", "enforce": "soft", "description": "摘要不少于 10 字",
    }])
    template = client.get("/api/templates/default-gb7713", headers=admin).json()
    template.update({"id": f"batch-template-{suffix}", "name": "批量升级模板", "requirement_set_ids": [policy_id]})
    assert client.post("/api/templates", headers=admin, json=template).status_code == 201
    old_project = client.post("/api/projects", headers=admin, json={"template_id": template["id"], "title": "旧快照项目"})
    assert old_project.status_code == 201, old_project.text
    version = client.post(f"/api/policies/{policy_id}/versions", headers=admin, json={"version": "2"})
    assert version.status_code == 201, version.text
    version_data = version.json()
    assert client.put(f"/api/policies/{version_data['id']}", headers=admin, json={**version_data, "items": [{
        "id": "batch-min", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
        "params": {"min": 200}, "severity": "error", "enforce": "hard", "description": "摘要不少于 200 字",
    }]}).status_code == 200
    assert client.post(f"/api/policies/{version_data['id']}/publish", headers=admin).status_code == 200
    upgraded = client.post(f"/api/policies/{version_data['id']}/upgrade-projects", headers=admin, json={"reason": "批量切换新版本"})
    assert upgraded.status_code == 200, upgraded.text
    assert old_project.json()["id"] in upgraded.json()["upgraded"]
    current = client.get(f"/api/projects/{old_project.json()['id']}", headers=admin)
    assert current.json()["requirement_snapshot"]["policy_sets"][0]["version"] == "2"


def test_closed_college_admin_cannot_manage_any_policy(closed_role_session):
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    registered = client.post("/api/auth/register", json={"username": f"closed-college-{suffix}", "password": _random_password("teacher"), "role": "student"})
    assert registered.status_code == 201
    college_admin = closed_role_session(registered.json()["id"])
    sibling_college_id = f"sibling-college-{suffix}"
    for scope_type, scope_id in [("college", sibling_college_id), ("school", f"school-{suffix}"), ("global", "")]:
        outside = client.post("/api/policies", headers=college_admin, json={
            "name": "已关闭角色的规范", "level": "college", "version": "1", "status": "draft",
            "bindings": [{"id": f"binding-{suffix}", "scope_type": scope_type, "scope_id": scope_id}],
        })
        assert outside.status_code == 403, outside.text
        assert outside.json()["detail"]["code"] == "ROLE_NOT_OPEN"
    assert client.get("/api/policies", headers=college_admin).status_code == 403



def test_requirement_dispute_is_reviewable_without_bypassing_automatic_gate():
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    student_id, student = _new_student(suffix)
    policy_id = f"dispute-policy-{suffix}"
    policy = _policy(admin, policy_id=policy_id, items=[{
        "id": "disputed-abstract", "type": "auto", "target": "abstract", "checker": "abstract.word_count",
        "params": {"min": 100}, "severity": "error", "enforce": "hard", "description": "摘要不少于 100 字",
    }])
    template = client.get("/api/templates/default-gb7713", headers=admin).json()
    template.update({"id": f"dispute-template-{suffix}", "name": "申诉测试模板", "requirement_set_ids": [policy_id]})
    assert client.post("/api/templates", headers=admin, json=template).status_code == 201
    project = client.post(f"/api/projects", headers=student, json={"template_id": template["id"], "title": "申诉项目"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    dispute = client.post(
        f"/api/projects/{project_id}/requirements/disputed-abstract/dispute",
        headers=student, json={"reason": "该项目摘要需按特殊格式提交，请人工复核"},
    )
    assert dispute.status_code == 201, dispute.text
    duplicate = client.post(
        f"/api/projects/{project_id}/requirements/disputed-abstract/dispute",
        headers=student, json={"reason": "重复申诉"},
    )
    assert duplicate.status_code == 409
    decision = client.post(
        f"/api/projects/{project_id}/requirements/disputes/{dispute.json()['id']}/decision",
        headers=admin, json={"status": "accepted", "review_note": "已记录人工处理结论"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "accepted"
    listed = client.get(f"/api/projects/{project_id}/requirements/disputes", headers=student)
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "accepted"
    report = client.get(f"/api/projects/{project_id}/check", headers=student)
    assert report.status_code == 200
    assert report.json()["summary"]["hard_failures"] >= 1
    assert student_id


def test_rule_pack_parameters_are_frozen_and_applied_to_project_checks():
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    policy = _policy(admin, policy_id=f"pack-policy-{suffix}", items=[{
        "id": "body-spacing-override", "type": "auto", "target": "document",
        "checker": "references.format", "params": {}, "severity": "warning", "enforce": "soft",
        "description": "参考文献格式规则包参数化",
        "rule_pack_id": "GB-T-7713-1-2006",
        "rule_pack_params": {
            "PG-BODY-LINE-SPACING": {
                "when": {"!=": [{"var": "line_spacing"}, 2.0]},
            },
        },
    }])
    template = client.get("/api/templates/default-gb7713", headers=admin).json()
    template.update({"id": f"pack-template-{suffix}", "name": "规则包参数化模板", "requirement_set_ids": [policy["id"]]})
    assert client.post("/api/templates", headers=admin, json=template).status_code == 201
    project = client.post("/api/projects", headers=admin, json={"template_id": template["id"], "title": "规则包参数化项目"})
    assert project.status_code == 201, project.text
    snapshot = project.json()["requirement_snapshot"]
    assert snapshot["rule_pack_overrides"]["GB-T-7713-1-2006"]["PG-BODY-LINE-SPACING"]
    project_id = project.json()["id"]
    content = client.put(f"/api/projects/{project_id}/section/ch1", headers=admin, json={
        "paragraphs": [{"id": "ch1-p1", "text": "正文内容"}],
    })
    assert content.status_code == 200, content.text
    report = client.get(f"/api/projects/{project_id}/check", headers=admin)
    assert report.status_code == 200, report.text
    assert any(item["rule_id"] == "PG-BODY-LINE-SPACING" for item in report.json()["findings"])


def test_unbound_school_policy_is_global_but_closed_college_admin_cannot_create_global_scope(closed_role_session):
    admin = _admin_headers()
    suffix = uuid.uuid4().hex[:8]
    policy = _policy(admin, policy_id=f"global-school-{suffix}", scope_id="", items=[{
        "id": "global-manual", "type": "manual", "target": "document",
        "checker": "", "params": {}, "severity": "info", "enforce": "soft",
        "description": "全校人工确认项",
    }])
    student_id, student = _new_student(suffix)
    project = client.post("/api/projects", headers=student, json={"template_id": "default-gb7713", "title": "全校规范项目"})
    assert project.status_code == 201, project.text
    assert any(item["id"] == policy["id"] for item in project.json()["requirement_snapshot"]["policy_sets"])

    school_id, college_id = f"global-school-org-{suffix}", f"global-college-org-{suffix}"
    assert client.post("/api/organizations", headers=admin, json={"id": school_id, "name": "全局大学", "type": "school"}).status_code == 201
    assert client.post("/api/organizations", headers=admin, json={"id": college_id, "name": "全局学院", "type": "college", "parent_id": school_id}).status_code == 201
    username = f"global-college-admin-{suffix}"
    teacher_password = _random_password("teacher")
    registered = client.post("/api/auth/register", json={"username": username, "password": teacher_password, "display_name": "学院管理员", "role": "teacher"})
    assert registered.status_code == 201
    college_admin_id = registered.json()["id"]
    college_admin = closed_role_session(college_admin_id)
    denied = client.post("/api/policies", headers=college_admin, json={
        "name": "学院越权全局规范", "level": "college", "version": "1", "status": "draft",
        "bindings": [{"id": f"global-binding-{suffix}", "scope_type": "global", "scope_id": ""}],
    })
    assert denied.status_code == 403
    assert student_id
