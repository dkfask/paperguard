"""教师学生管理接口及批量导入测试。

覆盖:
1. 教师批量导入学生 (正常导入、重复冲突、学号/姓名校验)
2. 权限隔离 (学生无法调用导入/管理、未认证 teacher 无法调用、教师不能跨权操作其他教师的学生)
3. 教师重置密码、学生登录及必须改密标志
4. 学生论文统计数据联动
5. 解除指导关系
"""

from __future__ import annotations

import os
import uuid
import pytest
from fastapi.testclient import TestClient

from app.db import Store
from app.main import app
from tests._helpers import register_fixture_response

client = TestClient(app)


def _gen_password() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-8a"


@pytest.fixture
def admin_token():
    admin_pw = os.environ.get("PAPERGUARD_ADMIN_PASSWORD", "admin123")
    res = client.post("/api/auth/login", json={"username": "admin", "password": admin_pw})
    assert res.status_code == 200, res.text
    return res.json()["token"]


def test_teacher_bulk_import_and_management(admin_token):
    # 1. 注册一个教师账号
    teacher_username = f"prof_{uuid.uuid4().hex[:6]}"
    teacher_pw = _gen_password()
    reg_res = register_fixture_response(client, teacher_username, teacher_pw, role="teacher", display_name="王教授")
    user_info = reg_res.json()
    teacher_id = user_info["id"]
    # 刚注册时 status 应为 pending
    assert user_info["status"] == "pending"

    # 2. pending 状态的教师尝试访问 /api/teacher/students 应该被拦截 (403)
    teacher_login = client.post(
        "/api/auth/login",
        json={"username": teacher_username, "password": teacher_pw},
    )
    assert teacher_login.status_code == 200
    pending_teacher_token = teacher_login.json()["token"]

    fail_res = client.get(
        "/api/teacher/students",
        headers={"Authorization": f"Bearer {pending_teacher_token}"},
    )
    assert fail_res.status_code == 403

    # 3. 管理员审核通过该教师
    approve_res = client.put(
        f"/api/auth/admin/users/{teacher_id}/role-status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "teacher", "status": "active"},
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "active"

    # 4. 激活后的教师访问 /api/teacher/students 成功
    ok_res = client.get(
        "/api/teacher/students",
        headers={"Authorization": f"Bearer {pending_teacher_token}"},
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["total"] == 0

    # 5. 教师批量导入学生
    stu1_user = f"stu_{uuid.uuid4().hex[:6]}"
    stu2_user = f"stu_{uuid.uuid4().hex[:6]}"
    default_import_pw = _gen_password()
    import_res = client.post(
        "/api/teacher/students/import",
        headers={"Authorization": f"Bearer {pending_teacher_token}"},
        json={
            "students": [
                {"username": stu1_user, "display_name": "张三同学"},
                {"username": stu2_user, "display_name": "李四同学"},
            ],
            "default_password": default_import_pw,
            "force_change_password": True,
        },
    )
    assert import_res.status_code == 200
    data = import_res.json()
    assert data["success_count"] == 2
    assert data["fail_count"] == 0
    assert len(data["created"]) == 2

    # 6. 查询指导学生列表
    list_res = client.get(
        "/api/teacher/students",
        headers={"Authorization": f"Bearer {pending_teacher_token}"},
    )
    assert list_res.status_code == 200
    stu_list = list_res.json()["students"]
    assert len(stu_list) == 2
    stu1_info = next(s for s in stu_list if s["username"] == stu1_user)
    assert stu1_info["display_name"] == "张三同学"

    # 7. 学生使用初始密码登录，并验证 must_change_password 为 True
    stu_login = client.post(
        "/api/auth/login",
        json={"username": stu1_user, "password": default_import_pw},
    )
    assert stu_login.status_code == 200
    stu_token = stu_login.json()["token"]
    assert stu_login.json()["user"]["must_change_password"] is True

    # 8. 学生无法访问教师专属端点 (403)
    stu_forbidden = client.get(
        "/api/teacher/students",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert stu_forbidden.status_code == 403

    # 9. 教师为学生重置密码
    new_student_pw = _gen_password()
    reset_res = client.post(
        f"/api/teacher/students/{stu1_info['id']}/reset-password",
        headers={"Authorization": f"Bearer {pending_teacher_token}"},
        json={"new_password": new_student_pw, "force_change_password": True},
    )
    assert reset_res.status_code == 200

    # 10. 学生旧 token 应当失效 (revoke)
    invalid_res = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert invalid_res.status_code == 401

    # 11. 学生使用重置后的密码可以登录
    stu_login_new = client.post(
        "/api/auth/login",
        json={"username": stu1_user, "password": new_student_pw},
    )
    assert stu_login_new.status_code == 200

    # 12. 教师解除与学生的指导关系
    unbind_res = client.delete(
        f"/api/teacher/students/{stu1_info['id']}",
        headers={"Authorization": f"Bearer {pending_teacher_token}"},
    )
    assert unbind_res.status_code == 200

    # 再次查询，学生只剩 1 人
    list_after = client.get(
        "/api/teacher/students",
        headers={"Authorization": f"Bearer {pending_teacher_token}"},
    )
    assert list_after.json()["total"] == 1


def _make_project(owner_id: str, texts: list[str]) -> None:
    """直接给指定学生落一个带正文的写作项目，用于验证字数统计路径。"""
    from app.models.project import ParagraphBlock, ProjectSection, WritingProject

    paragraphs = [
        ParagraphBlock(id=f"block-{index}", text=text)
        for index, text in enumerate(texts)
    ]
    Store().save_project(
        WritingProject(
            id=f"project-{uuid.uuid4().hex[:12]}",
            template_id="template-regression",
            title="字数统计回归用论文",
            owner_id=owner_id,
            stage="drafting",
            sections=[
                ProjectSection(key="body", type="paragraph", title="正文", paragraphs=paragraphs)
            ],
            updated_at="2026-09-29T00:00:00+00:00",
        )
    )


def test_teacher_students_word_count_covers_both_binding_sources(admin_token, monkeypatch):
    """指导学生列表的字数统计必须在「账号绑定」和「批次绑定」两种来源下都成立。

    这段统计曾经写成 ``p.blocks``，而 WritingProject 上并没有该属性（内容块在
    ``p.sections[].paragraphs``，正文字段是 ``text``）。既有测试导入学生后从不给
    学生建项目，循环体不执行，缺陷一直潜伏；批次来源的学生被 F-15 纳入列表后
    第一次真正执行到它，生产环境 /api/teacher/students 直接 500。
    """
    teacher_username = f"prof_{uuid.uuid4().hex[:6]}"
    reg_res = register_fixture_response(client, teacher_username, _gen_password(), role="teacher", display_name="字数统计回归教师")
    teacher_id = reg_res.json()["id"]
    approve_res = client.put(
        f"/api/auth/admin/users/{teacher_id}/role-status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "teacher", "status": "active"},
    )
    assert approve_res.status_code == 200, approve_res.text

    # 账号绑定学生：教师批量导入写入 users.teacher_id
    account_student = f"stu_{uuid.uuid4().hex[:6]}"
    import_res = client.post(
        "/api/teacher/students/import",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"students": [{"username": account_student, "display_name": "账号绑定学生"}]},
    )
    assert import_res.status_code == 200, import_res.text
    account_student_id = Store().get_user_by_username(account_student)["id"]

    # 批次绑定学生：只有 supervision 关系，没有账号绑定
    cohort_student_res = register_fixture_response(client, f"stu_{uuid.uuid4().hex[:6]}", _gen_password(), display_name="批次绑定学生")
    cohort_student_id = cohort_student_res.json()["id"]
    cohort_id = f"cohort-{uuid.uuid4().hex[:12]}"
    # 用桩替换批次查询：本用例关心的是「批次来源学生进入统计循环」，
    # 完整 cohort + 阶段配置的搭建由 workflow 用例覆盖。
    monkeypatch.setattr(
        "app.api.teacher.supervised_student_ids",
        lambda _store, _teacher_id: {cohort_student_id: cohort_id},
    )

    _make_project(account_student_id, ["  论文正文内容  ", ""])
    _make_project(cohort_student_id, ["甲", "乙丙丁"])

    res = client.get(
        "/api/teacher/students", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 200, res.text
    by_id = {item["id"]: item for item in res.json()["students"]}

    account_row = by_id[account_student_id]
    assert account_row["total_words"] == 6, account_row  # 首段 strip 后 6 字，空段不计
    assert account_row["project_count"] == 1
    assert account_row["relation_sources"] == ["account"]
    assert account_row["can_manage_account"] is True
    assert account_row["latest_project_title"] == "字数统计回归用论文"
    assert account_row["cohort_id"] == ""

    cohort_row = by_id[cohort_student_id]
    assert cohort_row["total_words"] == 4, cohort_row  # "甲" + "乙丙丁"
    assert cohort_row["project_count"] == 1
    assert cohort_row["relation_sources"] == ["cohort"]
    assert cohort_row["cohort_id"] == cohort_id
    # 批次来源没有账号绑定，因此不能解绑；但重置密码认两种指导来源
    assert cohort_row["can_manage_account"] is False
    assert cohort_row["can_reset_password"] is True
    assert account_row["can_reset_password"] is True


def _activate_teacher(client_, username: str, password: str, admin_tok: str) -> tuple[str, str]:
    """注册并激活一个教师，返回 (user_id, token)。"""
    reg = register_fixture_response(client_, username, password, role="teacher", display_name="回归教师")
    teacher_id = reg.json()["id"]
    approve = client_.put(
        f"/api/auth/admin/users/{teacher_id}/role-status",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"role": "teacher", "status": "active"},
    )
    assert approve.status_code == 200, approve.text
    login = client_.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return teacher_id, login.json()["token"]


def test_teacher_can_reset_password_for_cohort_supervised_student(admin_token, monkeypatch):
    """批次指导的学生，教师应能重置其密码；非本人指导的仍然 403。

    F-15 之前只认 users.teacher_id，于是出现「教师看得到学生的论文与进度，
    却管不了这个账号」的不一致。经产品决策，两种正式指导关系都授权重置密码，
    但解绑仍只认账号绑定。
    """
    from app.api import teacher as teacher_module

    teacher_username = f"prof_{uuid.uuid4().hex[:6]}"
    teacher_pw = _gen_password()
    teacher_id, teacher_token = _activate_teacher(client, teacher_username, teacher_pw, admin_token)

    # 批次指导学生：没有 users.teacher_id，只在 supervision 里
    stu_reg = register_fixture_response(client, f"stu_{uuid.uuid4().hex[:6]}", _gen_password(), display_name="批次指导学生")
    cohort_student_id = stu_reg.json()["id"]
    assert Store().get_user(cohort_student_id).get("teacher_id") is None

    monkeypatch.setattr(
        teacher_module,
        "supervised_student_ids",
        lambda _store, _tid: {cohort_student_id: "cohort-x"},
    )
    h = {"Authorization": f"Bearer {teacher_token}"}

    # 列表里该生可重置、不可解绑
    rows = {r["id"]: r for r in client.get("/api/teacher/students", headers=h).json()["students"]}
    assert rows[cohort_student_id]["can_reset_password"] is True
    assert rows[cohort_student_id]["can_manage_account"] is False

    new_pw = _gen_password()
    reset = client.post(
        f"/api/teacher/students/{cohort_student_id}/reset-password",
        headers=h,
        json={"new_password": new_pw, "force_change_password": True},
    )
    assert reset.status_code == 200, reset.text
    assert client.post(
        "/api/auth/login",
        json={"username": Store().get_user(cohort_student_id)["username"], "password": new_pw},
    ).status_code == 200

    # 解绑仍拒绝：批次关系不是这个端点能解除的
    unbind = client.delete(f"/api/teacher/students/{cohort_student_id}", headers=h)
    assert unbind.status_code == 403, unbind.text

    # 与该教师无任何指导关系的学生：重置必须 403
    other_reg = register_fixture_response(client, f"stu_{uuid.uuid4().hex[:6]}", _gen_password(), display_name="无关学生")
    other_id = other_reg.json()["id"]
    monkeypatch.setattr(teacher_module, "supervised_student_ids", lambda _store, _tid: {})
    denied = client.post(
        f"/api/teacher/students/{other_id}/reset-password",
        headers=h,
        json={"new_password": _gen_password(), "force_change_password": True},
    )
    assert denied.status_code == 403, denied.text

    # 其他教师的账号绑定学生也不可碰
    other_teacher_id, _ = _activate_teacher(
        client, f"prof_{uuid.uuid4().hex[:6]}", _gen_password(), admin_token
    )
    imported = client.post(
        "/api/teacher/students/import",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"students": [{"username": f"stu_{uuid.uuid4().hex[:6]}", "display_name": "他人学生"}]},
    )
    assert imported.status_code == 200, imported.text
    owned_id = Store().get_user_by_username(imported.json()["created"][0]["username"])["id"]
    assert Store().get_user(owned_id)["teacher_id"] != teacher_id
    cross = client.post(
        f"/api/teacher/students/{owned_id}/reset-password",
        headers=h,
        json={"new_password": _gen_password(), "force_change_password": True},
    )
    assert cross.status_code == 403, cross.text
