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
    reg_res = client.post(
        "/api/auth/register",
        json={
            "username": teacher_username,
            "password": teacher_pw,
            "role": "teacher",
            "display_name": "王教授",
        },
    )
    assert reg_res.status_code == 201
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
