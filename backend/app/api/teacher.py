"""教师指导学生及学生账号批量导入与管理接口。"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user, hash_password, require_manager
from app.db import Store


router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudentImportItem(BaseModel):
    username: str = Field(..., min_length=2, max_length=50, description="学号或用户名")
    display_name: str = Field(..., min_length=1, max_length=50, description="学生姓名")


class BulkImportStudentsRequest(BaseModel):
    students: list[StudentImportItem] = Field(..., min_length=1, max_length=200)
    default_password: str = Field(
        default="Student123456",
        min_length=8,
        max_length=50,
        description="默认初始密码",
    )
    force_change_password: bool = Field(
        default=True,
        description="首次登录是否强制修改密码",
    )


class ResetStudentPasswordRequest(BaseModel):
    new_password: str = Field(
        default="Student123456",
        min_length=8,
        max_length=50,
        description="重置后的密码",
    )
    force_change_password: bool = Field(
        default=True,
        description="首次登录是否强制修改密码",
    )


@router.get("/students")
def list_my_students(
    user: dict = Depends(require_manager),
    store: Store = Depends(lambda: Store()),
):
    """获取当前教师指导的学生列表，同时附带学生论文项目的简要进度统计。"""
    students = store.list_students_by_teacher(user["id"])
    all_projects = store.list_projects()

    result = []
    for s in students:
        s_projects = [p for p in all_projects if p.owner_id == s["id"]]
        # 统计字数和最新更新时间
        total_words = 0
        latest_title = ""
        last_updated = ""
        if s_projects:
            # 取最近更新的项目
            s_projects.sort(key=lambda p: p.updated_at, reverse=True)
            latest_p = s_projects[0]
            latest_title = latest_p.title
            last_updated = latest_p.updated_at
            for p in s_projects:
                for b in p.blocks:
                    total_words += len(b.content.strip()) if b.content else 0

        result.append({
            "id": s["id"],
            "username": s["username"],
            "display_name": s["display_name"],
            "status": s.get("status", "active"),
            "created_at": s["created_at"],
            "project_count": len(s_projects),
            "latest_project_title": latest_title,
            "total_words": total_words,
            "last_updated": last_updated,
        })

    return {"students": result, "total": len(result)}


@router.post("/students/import")
def bulk_import_students(
    req: BulkImportStudentsRequest,
    user: dict = Depends(require_manager),
    store: Store = Depends(lambda: Store()),
):
    """批量导入并生成学生账号。"""
    created_list = []
    failed_list = []

    for item in req.students:
        username = item.username.strip()
        display_name = item.display_name.strip()

        if not username or not display_name:
            failed_list.append({
                "username": username,
                "display_name": display_name,
                "reason": "学号或姓名不能为空",
            })
            continue

        # 检查是否已存在同名账号
        existing = store.get_user_by_username(username)
        if existing:
            # 如果账号已存在，且目前未绑定指导教师，可直接建立指导绑定
            if not existing.get("teacher_id"):
                existing["teacher_id"] = user["id"]
                if existing.get("display_name") != display_name and display_name:
                    existing["display_name"] = display_name
                store.save_user(existing)
                created_list.append({
                    "username": username,
                    "display_name": existing.get("display_name", display_name),
                    "initial_password": "（保留已有密码）",
                    "action": "linked_existing",
                })
            else:
                failed_list.append({
                    "username": username,
                    "display_name": display_name,
                    "reason": "该学号账号已存在且已绑定其他指导教师",
                })
            continue

        # 创建新学生账号
        pw_hash, salt = hash_password(req.default_password)
        new_student = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password_hash": pw_hash,
            "salt": salt,
            "role": "student",
            "display_name": display_name,
            "status": "active",
            "teacher_id": user["id"],
            "must_change_password": 1,
            "email": "",
            "email_verified": 0,
            "password_set": 1,
            "created_at": _now(),
        }
        store.save_user(new_student)
        created_list.append({
            "username": username,
            "display_name": display_name,
            "initial_password": req.default_password,
            "first_login_required": True,
            "action": "created",
        })

    return {
        "success_count": len(created_list),
        "fail_count": len(failed_list),
        "created": created_list,
        "failed": failed_list,
    }


@router.post("/students/{student_id}/reset-password")
def reset_student_password(
    student_id: str,
    req: ResetStudentPasswordRequest,
    user: dict = Depends(require_manager),
    store: Store = Depends(lambda: Store()),
):
    """指导教师为学生重置密码。"""
    student = store.get_user(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="学生账号不存在")

    # 权限校验：管理员可以重置任何学生；教师只能重置自己绑定的学生
    if user["role"] != "admin" and student.get("teacher_id") != user["id"]:
        raise HTTPException(status_code=403, detail="无权操作非本人指导的学生账号")

    pw_hash, salt = hash_password(req.new_password)
    student["password_hash"] = pw_hash
    student["salt"] = salt
    student["must_change_password"] = 1 if req.force_change_password else 0
    store.save_user(student)
    # 强制让学生旧会话下线
    store.revoke_user_sessions(student["id"], _now())

    return {
        "ok": True,
        "message": f"学生 {student['display_name']}（{student['username']}）密码重置成功",
        "new_password": req.new_password,
    }


@router.delete("/students/{student_id}")
def unbind_student(
    student_id: str,
    user: dict = Depends(require_manager),
    store: Store = Depends(lambda: Store()),
):
    """解除教师与学生的指导关系。"""
    student = store.get_user(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="学生账号不存在")

    if user["role"] != "admin" and student.get("teacher_id") != user["id"]:
        raise HTTPException(status_code=403, detail="无权操作非本人指导的学生")

    teacher_id = student.get("teacher_id")
    if teacher_id:
        store.unbind_teacher_student(teacher_id, student_id)

    return {"ok": True, "message": "解除指导关系成功"}
