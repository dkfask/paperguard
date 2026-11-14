"""组织创建只允许系统管理员。

界面上「新建组织」只对 admin / college_admin 显示，但创建接口原先走
require_manager，已认证教师可以直接 POST 一所学校或任意下级节点。
组织树决定批次、作用域和角色认证申请，因此这里锁的是创建，不放宽更新和删除。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import auth
from app.db import Store
from app.main import app


client = TestClient(app)
PASSWORD = f"Test-{uuid.uuid4().hex}-A1"


def _account(store: Store, role: str, **fields) -> dict:
    user_id = "org-create-" + uuid.uuid4().hex
    password_hash, salt = auth.hash_password(PASSWORD)
    return store.save_user({
        "id": user_id,
        "username": user_id,
        "role": role,
        "status": "active",
        "password_hash": password_hash,
        "salt": salt,
        "display_name": "组织创建回归",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    })


def _headers(user: dict) -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": user["username"], "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["token"]}


def test_active_teacher_cannot_create_school_or_child_organization():
    store = Store()
    admin = _headers(_account(store, "admin"))
    suffix = uuid.uuid4().hex[:8]
    parent_id = f"org-create-school-{suffix}"
    parent = client.post("/api/organizations", headers=admin, json={
        "id": parent_id, "name": "授权测试学校", "type": "school",
    })
    assert parent.status_code == 201, parent.text

    # 父级已在该教师的作用域根上。创建仍须失败，不能退化为「范围内可建下级」。
    teacher = _headers(_account(store, "teacher", organization_context={"school_id": parent_id}))
    school_id = f"org-create-teacher-school-{suffix}"
    denied_school = client.post("/api/organizations", headers=teacher, json={
        "id": school_id, "name": "教师越权学校", "type": "school",
    })
    college_id = f"org-create-teacher-college-{suffix}"
    denied_child = client.post("/api/organizations", headers=teacher, json={
        "id": college_id,
        "name": "教师越权学院",
        "type": "college",
        "parent_id": parent_id,
    })

    assert denied_school.status_code == 403, denied_school.text
    assert denied_child.status_code == 403, denied_child.text
    assert denied_school.json()["detail"] == "仅系统管理员可执行此操作"
    assert denied_child.json()["detail"] == "仅系统管理员可执行此操作"
    assert store.get_organization_unit(school_id) is None
    assert store.get_organization_unit(college_id) is None
    assert store.get_organization_unit(parent_id) is not None
