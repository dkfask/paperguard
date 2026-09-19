"""隔离执行 PaperGuard 学院管理员视角完整业务闭环。

运行:
    cd /Users/a1234/Documents/perpetual/paperguard
    .venv/bin/python college_admin_lifecycle_20260916.py

学院管理员主导组织范围内的批次、成员导入、阶段发布、过程材料审批、归档和报表。
模板仍由认证教师提供（学院管理员创建模板会被拒绝，这是产品权限边界）。
脚本只使用临时数据库和临时文件目录，原始测试文件以只读字节上传。
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent
BACKEND = REPO / "backend"
DATA_ROOT = Path("/Users/a1234/Documents/perpetual/22016022205-王俞尹")
TEMPLATE_ROOT = Path("/Users/a1234/Documents/perpetual/附件3-2026届毕业设计（论文）相关文档模板")

PAPER = DATA_ROOT / "其他过程材料" / "22016022205+王俞尹+毕业论文-计软学院-4.7.docx"
TEMPLATE_DOCX = TEMPLATE_ROOT / "阅读7-成都东软学院本科毕业设计报告样例.docx"
POLICY_PDF = TEMPLATE_ROOT / "阅读4-成都东软学院本科生毕业设计报告（论文）撰写规范.pdf"
TASK_BOOK = DATA_ROOT / "22016022205+王俞尹+任务书-计软学院.docx"
OPENING_REPORT = DATA_ROOT / "22016022205-王俞尹-开题报告-计软学院.docx"
GUIDANCE_LOG = DATA_ROOT / "22016022205+王俞尹+指导过程记录表.docx"
ADVISOR_SCORE = DATA_ROOT / "22016022205+王俞尹+指导教师成绩评定书-计软学院.docx"
REVIEWER_SCORE = DATA_ROOT / "22016022205+王俞尹+评阅教师成绩评定书-计软学院.docx"
DEFENSE_APPLICATION = DATA_ROOT / "22016022205+王俞尹+答辩申请表.docx"
SIMILARITY_REPORT = DATA_ROOT / "22016022205-王俞尹-查重简洁报告.pdf"
COVER_DOC = DATA_ROOT / "0-过程性文档封面.doc"
MATERIAL_LIST = DATA_ROOT / "0-过程材料清单.xlsx"
MIDTERM_TEMPLATE = TEMPLATE_ROOT / "管理5：XXX学院毕业设计（论文）中期质量检查表（指导教师填报）.xlsx"


class Run:
    def __init__(self, client):
        self.client = client
        self.steps: list[dict[str, Any]] = []
        self.findings: list[dict[str, Any]] = []

    def check(self, name: str, response, expected: int = 200, note: str = "") -> dict[str, Any]:
        body = response.text[:500]
        passed = response.status_code == expected
        self.steps.append({
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "http_status": response.status_code,
            "expected": expected,
            "note": note,
        })
        if not passed:
            raise AssertionError(f"{name}: expected {expected}, got {response.status_code}: {body}")
        try:
            return response.json()
        except ValueError:
            return {"raw": response.content, "headers": dict(response.headers)}

    def expected_guard(self, name: str, response, expected: int, note: str) -> None:
        if response.status_code != expected:
            raise AssertionError(f"{name}: expected guard {expected}, got {response.status_code}: {response.text[:500]}")
        self.steps.append({
            "name": name,
            "status": "EXPECTED_GUARD",
            "http_status": response.status_code,
            "expected": expected,
            "note": note,
        })


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def upload(path: Path):
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {"file": (path.name, path.read_bytes(), content_type)}


def login(run: Run, username: str, password: str) -> tuple[dict[str, str], dict[str, Any]]:
    data = run.check(
        f"登录 {username}",
        run.client.post("/api/auth/login", json={"username": username, "password": password}),
    )
    return headers(data["token"]), data["user"]


def main() -> int:
    required = (
        PAPER, TEMPLATE_DOCX, POLICY_PDF, TASK_BOOK, OPENING_REPORT, GUIDANCE_LOG,
        ADVISOR_SCORE, REVIEWER_SCORE, DEFENSE_APPLICATION, SIMILARITY_REPORT,
        COVER_DOC, MATERIAL_LIST, MIDTERM_TEMPLATE,
    )
    if not all(path.is_file() for path in required):
        missing = [str(path) for path in required if not path.is_file()]
        print(json.dumps({"status": "DATA_PRECONDITION", "missing": missing}, ensure_ascii=False))
        return 2

    sys.path.insert(0, str(BACKEND))
    suffix = uuid.uuid4().hex[:8]
    admin_password = f"IsolatedAdmin-{suffix}-2026a"
    college_admin_password = f"CollegeAdmin-{suffix}-2026a"
    teacher_password = f"Teacher-{suffix}-2026a"
    student_password = f"Student-{suffix}-2026a"

    with tempfile.TemporaryDirectory(prefix="paperguard-college-lifecycle-") as temp_dir:
        temp = Path(temp_dir)
        os.environ["PAPERGUARD_ADMIN_PASSWORD"] = admin_password
        os.environ["PAPERGUARD_DB_PATH"] = str(temp / "paperguard.db")
        os.environ["PAPERGUARD_TEMPLATE_DIR"] = str(temp / "templates")
        os.environ["PAPERGUARD_PROCESS_FILE_DIR"] = str(temp / "process-files")
        os.environ["PAPERGUARD_ARTIFACT_DIR"] = str(temp / "artifacts")

        from fastapi.testclient import TestClient
        from app.main import app
        from app.worker import run_once

        with TestClient(app) as client:
            run = Run(client)
            admin_headers, admin_user = login(run, "admin", admin_password)

            school_id = f"school-{suffix}"
            college_id = f"college-{suffix}"
            other_college_id = f"other-college-{suffix}"
            run.check(
                "学校管理员创建学校",
                client.post("/api/organizations", headers=admin_headers, json={
                    "id": school_id, "name": "学院闭环测试学校", "type": "school",
                }),
                201,
            )
            run.check(
                "学校管理员创建计软学院",
                client.post("/api/organizations", headers=admin_headers, json={
                    "id": college_id, "name": "计算机与软件学院", "type": "college", "parent_id": school_id,
                }),
                201,
            )
            run.check(
                "学校管理员创建对照学院",
                client.post("/api/organizations", headers=admin_headers, json={
                    "id": other_college_id, "name": "对照学院", "type": "college", "parent_id": school_id,
                }),
                201,
            )

            college_admin_name = f"college-admin-{suffix}"
            college_admin_reg = run.check(
                "学院管理员账号注册",
                client.post("/api/auth/register", json={
                    "username": college_admin_name,
                    "password": college_admin_password,
                    "display_name": "计软学院管理员",
                    "role": "student",
                }),
                201,
            )
            college_admin_id = college_admin_reg["id"]
            run.check(
                "学校管理员提升为学院管理员并启用",
                client.put(
                    f"/api/auth/admin/users/{college_admin_id}/role-status",
                    headers=admin_headers,
                    json={"role": "college_admin", "status": "active"},
                ),
            )
            run.check(
                "学校管理员绑定学院管理员组织范围",
                client.put(
                    f"/api/organizations/users/{college_admin_id}/context",
                    headers=admin_headers,
                    json={"organization_context": {"school_id": school_id, "college_id": college_id}},
                ),
            )
            college_headers, college_user = login(run, college_admin_name, college_admin_password)
            assert college_user["role"] == "college_admin"

            teacher_name = f"teacher-{suffix}"
            teacher_reg = run.check(
                "指导教师注册",
                client.post("/api/auth/register", json={
                    "username": teacher_name,
                    "password": teacher_password,
                    "display_name": "王俞尹指导教师",
                    "role": "teacher",
                }),
                201,
            )
            teacher_id = teacher_reg["id"]
            run.check(
                "学校管理员认证指导教师",
                client.put(
                    f"/api/auth/admin/users/{teacher_id}/role-status",
                    headers=admin_headers,
                    json={"role": "teacher", "status": "active"},
                ),
            )
            run.check(
                "学校管理员绑定教师到计软学院",
                client.put(
                    f"/api/organizations/users/{teacher_id}/context",
                    headers=admin_headers,
                    json={"organization_context": {"school_id": school_id, "college_id": college_id}},
                ),
            )
            teacher_headers, teacher_user = login(run, teacher_name, teacher_password)

            student_name = "22016022205"
            student_reg = run.check(
                "学生注册",
                client.post("/api/auth/register", json={
                    "username": student_name,
                    "password": student_password,
                    "display_name": "王俞尹",
                    "role": "student",
                }),
                201,
            )
            student_id = student_reg["id"]
            run.check(
                "学校管理员绑定学生到计软学院",
                client.put(
                    f"/api/organizations/users/{student_id}/context",
                    headers=admin_headers,
                    json={"organization_context": {"school_id": school_id, "college_id": college_id}},
                ),
            )
            student_headers, student_user = login(run, student_name, student_password)

            default_template = run.check("学院管理员读取默认模板", client.get("/api/templates/default-gb7713", headers=college_headers))
            run.expected_guard(
                "学院管理员不能创建论文模板",
                client.post("/api/templates", headers=college_headers, json=default_template),
                403,
                "模板由教师/学校管理员维护，学院管理员只能引用已有模板",
            )

            imported = run.check(
                "教师上传并解析论文模板",
                client.post("/api/templates/import", headers=teacher_headers, files=upload(TEMPLATE_DOCX)),
            )
            template_id = f"teacher-template-{suffix}"
            template_payload = {
                key: imported[key]
                for key in ("description", "styles", "page_layout", "meta_fields", "cover_blocks", "sections", "source_docx")
                if key in imported
            }
            template_payload.update({
                "id": template_id,
                "name": f"计软学院2026届毕业设计报告模板-{suffix}",
                "version": "2026.1",
                "rule_packs": [],
                "requirement_set_ids": [],
                "organization_context": {"school_id": school_id, "college_id": college_id},
            })
            run.check("教师保存论文模板", client.post("/api/templates", headers=teacher_headers, json=template_payload), 201)

            college_policy_id = f"college-policy-{suffix}"
            run.check(
                "学院管理员创建学院写作规范",
                client.post("/api/policies", headers=college_headers, json={
                    "id": college_policy_id,
                    "name": "计软学院毕业设计补充规范",
                    "level": "college",
                    "status": "draft",
                    "version": "1",
                    "description": "学院管理员发布的学院级补充规范",
                    "markdown_content": "# 学院规范\n\n封面、任务书、开题、中期、查重和答辩材料按学院清单执行。",
                    "bindings": [{"id": f"binding-{suffix}", "scope_type": "college", "scope_id": college_id}],
                    "items": [{
                        "id": f"college-manual-{suffix}",
                        "type": "manual",
                        "target": "document",
                        "severity": "warning",
                        "enforce": "soft",
                        "description": "学院确认过程材料齐全",
                    }],
                }),
                201,
            )
            run.check(
                "学院管理员上传规范原始文档",
                client.post(f"/api/policies/{college_policy_id}/documents", headers=college_headers, files=upload(POLICY_PDF)),
                201,
            )
            published_policy = run.check("学院管理员发布学院规范", client.post(f"/api/policies/{college_policy_id}/publish", headers=college_headers))
            assert published_policy["status"] == "published"

            outside_policy = client.post("/api/policies", headers=college_headers, json={
                "name": "越界规范",
                "level": "college",
                "version": "1",
                "status": "draft",
                "bindings": [{"id": f"outside-{suffix}", "scope_type": "college", "scope_id": other_college_id}],
            })
            if outside_policy.status_code == 403:
                run.expected_guard(
                    "学院管理员不能给对照学院建规范",
                    outside_policy,
                    403,
                    "学院管理员只能在本学院范围内发布规范",
                )
            else:
                run.steps.append({
                    "name": "学院管理员不能给对照学院建规范",
                    "status": "FINDING",
                    "http_status": outside_policy.status_code,
                    "expected": 403,
                    "note": "policies._user_scope_ids 把 school_id 及其全部下级算进可见范围，兄弟学院规范可被本学院管理员创建",
                })
                run.findings.append({
                    "id": "BUG-COLLEGE-SCOPE-001",
                    "classification": "scope_bypass",
                    "name": "学院管理员可给兄弟学院创建规范",
                    "status_code": outside_policy.status_code,
                    "detail": "批次/审计走 access_service 最具体组织根；规范创建走 policies._user_scope_ids，会把 context 里的 school_id 展开到全部下级学院。",
                })
                outside_publish = client.post(f"/api/policies/{outside_policy.json()['id']}/publish", headers=college_headers)
                run.steps.append({
                    "name": "学院管理员发布兄弟学院规范",
                    "status": "FINDING" if outside_publish.status_code == 200 else "PASS",
                    "http_status": outside_publish.status_code,
                    "expected": 403,
                    "note": "若创建已越权，发布接口同样未按学院根隔离",
                })
                if outside_publish.status_code == 200:
                    run.findings.append({
                        "id": "BUG-COLLEGE-SCOPE-002",
                        "classification": "scope_bypass",
                        "name": "学院管理员可发布兄弟学院规范",
                        "status_code": outside_publish.status_code,
                        "detail": outside_publish.json().get("status") if outside_publish.headers.get("content-type", "").startswith("application/json") else outside_publish.text[:200],
                    })

            template = run.check("教师读取模板", client.get(f"/api/templates/{template_id}", headers=teacher_headers))
            template["requirement_set_ids"] = [college_policy_id]
            template["rule_packs"] = []
            run.check("教师把学院规范绑定到模板", client.put(f"/api/templates/{template_id}", headers=teacher_headers, json=template))

            other_cohort = run.check(
                "学校管理员在对照学院创建批次",
                client.post("/api/cohorts", headers=admin_headers, json={
                    "name": f"对照学院批次-{suffix}",
                    "organization_id": other_college_id,
                    "academic_year": "2026",
                }),
                201,
            )
            other_cohort_id = other_cohort["id"]

            cohort = run.check(
                "学院管理员创建本学院毕业设计批次",
                client.post("/api/cohorts", headers=college_headers, json={
                    "name": f"计软学院2026届毕业设计-{suffix}",
                    "organization_id": college_id,
                    "academic_year": "2026",
                }),
                201,
            )
            cohort_id = cohort["id"]

            scoped_cohorts = run.check("学院管理员列出可见批次", client.get("/api/cohorts", headers=college_headers))
            scoped_ids = {item["id"] for item in scoped_cohorts}
            assert cohort_id in scoped_ids
            assert other_cohort_id not in scoped_ids
            run.expected_guard(
                "学院管理员不能查看对照学院批次",
                client.get(f"/api/cohorts/{other_cohort_id}", headers=college_headers),
                404,
                "组织范围隔离：外院批次对学院管理员不可见",
            )

            preview = run.check(
                "学院管理员预检成员导入",
                client.post(f"/api/cohorts/{cohort_id}/members/import", headers=college_headers, json={
                    "dry_run": True,
                    "rows": [{
                        "student_ref": student_name,
                        "teacher_ref": teacher_name,
                        "reason": "学院管理员按学号/工号导入",
                    }],
                }),
            )
            assert preview["summary"]["ready"] == 1, preview
            imported_members = run.check(
                "学院管理员导入师生配对",
                client.post(f"/api/cohorts/{cohort_id}/members/import", headers=college_headers, json={
                    "rows": [{
                        "student_ref": student_name,
                        "teacher_ref": teacher_name,
                        "reason": "学院管理员按学号/工号导入",
                    }],
                }),
            )
            assert imported_members["summary"]["imported"] == 1, imported_members

            now = datetime.now(timezone.utc)
            required_materials = [
                "task_book", "opening_report", "guidance_log", "midterm_check",
                "final_report", "similarity_report", "advisor_score", "reviewer_score",
                "defense_application",
            ]
            stage = run.check(
                "学院管理员创建毕业设计全过程阶段",
                client.post(f"/api/cohorts/{cohort_id}/stages", headers=college_headers, json={
                    "key": "final",
                    "name": "毕业设计全过程",
                    "open_at": (now - timedelta(minutes=5)).isoformat(),
                    "deadline_at": (now + timedelta(days=7)).isoformat(),
                    "template_id": template_id,
                    "requirement_set_ids": [college_policy_id],
                    "rule_packs": [],
                    "required_materials": required_materials,
                    "required_manual_checks": [],
                    "allow_early_write": True,
                    "is_final": True,
                }),
                201,
            )
            stage_id = stage["id"]
            stage_preview = run.check("学院管理员预览阶段发布", client.post(f"/api/stages/{stage_id}/preview", headers=college_headers))
            assert stage_preview["ok"] is True, stage_preview
            published_stage = run.check("学院管理员发布阶段", client.post(f"/api/stages/{stage_id}/publish", headers=college_headers))
            assert published_stage["participant_count"] == 1

            student_tasks = run.check("学生读取学院发布的阶段任务", client.get("/api/my-tasks", headers=student_headers))
            assert len(student_tasks) == 1
            participant_id = student_tasks[0]["id"]
            teacher_tasks = run.check("教师读取指导任务", client.get("/api/my-tasks", headers=teacher_headers))
            assert any(item["id"] == participant_id for item in teacher_tasks)
            college_tasks = run.check("学院管理员查看本学院任务", client.get("/api/my-tasks", headers=college_headers))
            assert any(item["id"] == participant_id for item in college_tasks)

            binding = run.check(
                "学生绑定主论文",
                client.post(
                    f"/api/stage-participants/{participant_id}/project",
                    headers=student_headers,
                    json={"title": "基于STM32的卧室智能控制系统的设计与实现"},
                ),
            )
            project_id = binding["project"]["id"]
            project = binding["project"]
            run.check("学生确认阶段规范快照", client.post(f"/api/stage-participants/{participant_id}/acknowledge", headers=student_headers, json={}))

            original_hash = hashlib.sha256(PAPER.read_bytes()).hexdigest()
            imported_project = run.check(
                "学生导入真实论文 DOCX",
                client.post(
                    f"/api/projects/{project_id}/import-docx",
                    params={"apply": "true", "base_revision": project["revision"]},
                    headers=student_headers,
                    files=upload(PAPER),
                ),
            )
            project = imported_project["project"]
            metadata = {
                "school": "学院闭环测试学校",
                "title": "基于STM32的卧室智能控制系统的设计与实现",
                "author": "王俞尹",
                "student_id": "22016022205",
                "major": "计算机科学与技术",
                "advisor": "王俞尹指导教师",
                "date": "2026",
            }
            project = run.check(
                "学生保存论文封面信息",
                client.put(f"/api/projects/{project_id}/meta", headers=student_headers, json={
                    "meta": metadata, "base_revision": project["revision"],
                }),
            )
            for section in project.get("sections", []):
                if not section.get("required") or section.get("type") in {"cover", "toc"}:
                    continue
                paragraphs = section.get("paragraphs") or []
                if any(str(item.get("text", "")).strip() for item in paragraphs):
                    continue
                project = run.check(
                    f"学生补齐必填章节 {section['key']}",
                    client.put(
                        f"/api/projects/{project_id}/section/{section['key']}",
                        headers=student_headers,
                        json={
                            "base_revision": project["revision"],
                            "paragraphs": [{"id": f"{section['key']}-college-loop", "text": "学院管理员视角闭环回归补充内容。"}],
                        },
                    ),
                )
            assert hashlib.sha256(PAPER.read_bytes()).hexdigest() == original_hash

            check_report = run.check(
                "学生执行 GB/T 7713-1-2006 规则检查",
                client.get(f"/api/projects/{project_id}/check", params={"pack": "GB-T-7713-1-2006"}, headers=student_headers),
            )
            template_report = run.check(
                "学生执行项目模板对照检查",
                client.get(f"/api/projects/{project_id}/template-check", headers=student_headers),
            )
            assert check_report.get("summary") is not None
            assert template_report.get("summary") is not None
            exported = run.check(
                "学生按学院引用模板导出论文",
                client.post(f"/api/projects/{project_id}/export", params={"force": "true"}, headers=student_headers),
            )
            export_bytes = exported["raw"]
            assert export_bytes[:2] == b"PK"
            with zipfile.ZipFile(io.BytesIO(export_bytes)) as archive:
                assert "[Content_Types].xml" in archive.namelist()

            def process_record(
                artifact_type: str,
                actor_headers: dict[str, str],
                submit_headers: dict[str, str],
                file_path: Path,
                data: dict[str, Any],
                approvers: list[dict[str, str]],
            ) -> dict[str, Any]:
                record = run.check(
                    f"创建过程材料 {artifact_type}",
                    client.post(
                        f"/api/stage-participants/{participant_id}/process-records",
                        headers=actor_headers,
                        json={"artifact_type": artifact_type, "data": data, "structured_form": False},
                    ),
                    201,
                )
                record_id = record["id"]
                run.check(
                    f"上传过程材料附件 {artifact_type}",
                    client.post(f"/api/process-records/{record_id}/attachments", headers=actor_headers, files=upload(file_path)),
                    201,
                )
                record = run.check(f"提交过程材料 {artifact_type}", client.post(f"/api/process-records/{record_id}/submit", headers=submit_headers))
                for index, approver in enumerate(approvers, start=1):
                    record = run.check(
                        f"审批过程材料 {artifact_type} 第{index}级",
                        client.post(f"/api/process-records/{record_id}/decisions", headers=approver, json={"decision": "approved"}),
                    )
                assert record["state"] in {"submitted", "approved"}
                return record

            process_record("task_book", student_headers, student_headers, TASK_BOOK, {"student_id": student_name, "student_name": "王俞尹"}, [teacher_headers])
            process_record("opening_report", student_headers, student_headers, OPENING_REPORT, {"student_id": student_name, "student_name": "王俞尹"}, [teacher_headers])
            process_record("guidance_log", student_headers, student_headers, GUIDANCE_LOG, {"student_id": student_name, "student_name": "王俞尹"}, [teacher_headers])
            process_record("midterm_check", teacher_headers, teacher_headers, MIDTERM_TEMPLATE, {"student_id": student_name, "student_name": "王俞尹", "task_progress": "已完成"}, [teacher_headers])
            process_record("final_report", student_headers, student_headers, PAPER, {"student_id": student_name, "student_name": "王俞尹", "title": "基于STM32的卧室智能控制系统的设计与实现"}, [])
            process_record("similarity_report", student_headers, student_headers, SIMILARITY_REPORT, {"copy_ratio": 8.5, "classification": "A"}, [teacher_headers])
            process_record("advisor_score", teacher_headers, teacher_headers, ADVISOR_SCORE, {"score": 92}, [teacher_headers])
            process_record("reviewer_score", teacher_headers, teacher_headers, REVIEWER_SCORE, {"score": 90}, [teacher_headers])
            process_record(
                "defense_application",
                student_headers,
                student_headers,
                DEFENSE_APPLICATION,
                {"absence_count": 0, "total_sessions": 3},
                [teacher_headers, college_headers],
            )

            cover_record = run.check(
                "创建过程材料封面记录",
                client.post(
                    f"/api/stage-participants/{participant_id}/process-records",
                    headers=student_headers,
                    json={"artifact_type": "process_cover", "data": {"source_filename": COVER_DOC.name}},
                ),
                201,
            )
            run.check(
                "上传真实过程材料封面 DOC",
                client.post(f"/api/process-records/{cover_record['id']}/attachments", headers=student_headers, files=upload(COVER_DOC)),
                201,
            )

            cohort_record = run.check(
                "学院管理员创建过程材料清单记录",
                client.post(
                    f"/api/cohorts/{cohort_id}/process-records",
                    headers=college_headers,
                    json={"artifact_type": "process_material_list", "data": {"generated_by": "学院管理员", "rows": [{"student_id": student_name, "file_name": PAPER.name}]}, "structured_form": False},
                ),
                201,
            )
            cohort_record_id = cohort_record["id"]
            run.check("学院管理员上传过程材料清单 Excel", client.post(f"/api/process-records/{cohort_record_id}/attachments", headers=college_headers, files=upload(MATERIAL_LIST)), 201)
            run.check("学院管理员提交过程材料清单", client.post(f"/api/process-records/{cohort_record_id}/submit", headers=college_headers))
            run.check("学院管理员审批过程材料清单", client.post(f"/api/process-records/{cohort_record_id}/decisions", headers=college_headers, json={"decision": "approved"}))

            records = run.check("学生读取全部过程材料", client.get(f"/api/stage-participants/{participant_id}/process-records", headers=student_headers))
            ready_types = {item["artifact_type"] for item in records if item["state"] in {"submitted", "approved"}}
            assert set(required_materials).issubset(ready_types), (required_materials, ready_types)

            candidate = run.check(
                "学生创建首次提交候选版本",
                client.post("/api/submission-candidates", headers=student_headers, json={
                    "participant_id": participant_id,
                    "modification_note": "学院管理员视角首次提交",
                    "materials": [item["id"] for item in records if item["artifact_type"] in required_materials and item["state"] in {"submitted", "approved"}],
                }),
                201,
            )
            assert candidate["status"] == "ready", candidate.get("gates")
            submitted = run.check(
                "学生确认提交论文",
                client.post("/api/submissions", headers=student_headers, json={
                    "candidate_id": candidate["id"],
                    "idempotency_key": f"college-loop-{suffix}-submit-1",
                    "warning_acknowledged": True,
                }),
            )
            submission_id = submitted["submission"]["id"]
            assert submitted["participant"]["state"] == "submitted"

            comment = run.check(
                "教师添加退回批注",
                client.post(f"/api/submissions/{submission_id}/comments", headers=teacher_headers, json={
                    "body": "请补充实验分析后再提交",
                    "severity": "blocking",
                }),
                201,
            )
            returned = run.check(
                "教师退回论文",
                client.post(f"/api/submissions/{submission_id}/reviews", headers=teacher_headers, json={
                    "decision": "returned",
                    "reason": "需要补充实验分析",
                }),
            )
            assert returned["participant"]["state"] == "needs_revision"
            mid_report = run.check("学院管理员查看退回后批次报告", client.get(f"/api/reports/cohorts/{cohort_id}", headers=college_headers))
            assert mid_report["state_counts"].get("needs_revision", 0) == 1, mid_report

            run.check("学生回复批注", client.post(f"/api/comments/{comment['id']}/response", headers=student_headers, json={"body": "已补充实验分析"}), 201)
            run.check("教师关闭批注", client.post(f"/api/comments/{comment['id']}/status", headers=teacher_headers, json={"status": "resolved", "body": "已复核"}))

            candidate2 = run.check(
                "学生创建修订后提交候选版本",
                client.post("/api/submission-candidates", headers=student_headers, json={
                    "participant_id": participant_id,
                    "modification_note": "按教师意见修订后再次提交",
                    "materials": [item["id"] for item in records if item["artifact_type"] in required_materials and item["state"] in {"submitted", "approved"}],
                }),
                201,
            )
            submitted2 = run.check(
                "学生再次提交论文",
                client.post("/api/submissions", headers=student_headers, json={
                    "candidate_id": candidate2["id"],
                    "idempotency_key": f"college-loop-{suffix}-submit-2",
                    "warning_acknowledged": True,
                }),
            )
            submission2_id = submitted2["submission"]["id"]
            reviewed = run.check(
                "教师审核通过修订稿",
                client.post(f"/api/submissions/{submission2_id}/reviews", headers=teacher_headers, json={"decision": "approved"}),
            )
            assert reviewed["participant"]["state"] == "pending_archive"

            archive = run.check(
                "学院管理员创建论文归档任务",
                client.post("/api/archives", headers=college_headers, json={"submission_id": submission2_id}),
                202,
            )
            worker_result = run_once()
            assert archive["job_id"] in worker_result["archives"]["succeeded"], worker_result
            archive_status = run.check("学院管理员确认归档完成", client.get(f"/api/archives/{archive['id']}", headers=college_headers))
            assert archive_status["status"] == "succeeded"
            archive_download = run.check("学生下载最终归档包", client.get(f"/api/archives/{archive['id']}/download", headers=student_headers))
            assert archive_download["raw"][:2] == b"PK"

            report = run.check("学院管理员读取批次完成报告", client.get(f"/api/reports/cohorts/{cohort_id}", headers=college_headers))
            assert report["state_counts"]["archived"] == 1, report
            audit = run.check("学院管理员读取本学院审计事件", client.get("/api/audit", headers=college_headers, params={"resource_type": "cohort"}))
            audit_orgs = {item.get("organization_id") for item in audit}
            assert college_id in audit_orgs
            assert other_college_id not in audit_orgs

            for label, token_headers in (
                ("学生", student_headers),
                ("教师", teacher_headers),
                ("学院管理员", college_headers),
                ("学校管理员", admin_headers),
            ):
                run.check(f"{label}退出登录", client.post("/api/auth/logout", headers=token_headers))
                run.check(f"{label}退出后会话失效", client.get("/api/auth/me", headers=token_headers), 401)

            result = {
                "status": "PASS_WITH_FINDINGS" if run.findings else "PASS",
                "operator": "college_admin",
                "suffix": suffix,
                "accounts": {
                    "admin": admin_user["username"],
                    "college_admin": college_user["username"],
                    "teacher": teacher_user["username"],
                    "student": student_user["username"],
                },
                "entities": {
                    "school_id": school_id,
                    "college_id": college_id,
                    "other_college_id": other_college_id,
                    "cohort_id": cohort_id,
                    "template_id": template_id,
                    "policy_id": college_policy_id,
                    "participant_id": participant_id,
                    "project_id": project_id,
                    "first_submission_id": submission_id,
                    "final_submission_id": submission2_id,
                    "archive_id": archive["id"],
                },
                "steps": run.steps,
                "findings": run.findings,
                "checks": {
                    "rule_findings": check_report["summary"]["total"],
                    "template_findings": template_report["summary"]["total"],
                    "ready_process_types": sorted(ready_types),
                    "export_bytes": len(export_bytes),
                    "archive_bytes": len(archive_download["raw"]),
                    "original_paper_sha256": original_hash,
                    "original_paper_sha256_after": hashlib.sha256(PAPER.read_bytes()).hexdigest(),
                    "mid_report_needs_revision": mid_report["state_counts"].get("needs_revision"),
                    "final_report_archived": report["state_counts"].get("archived"),
                },
            }
            print(json.dumps(result, ensure_ascii=False, indent=2, default=lambda value: value.decode("latin1") if isinstance(value, bytes) else str(value)))
            return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise
