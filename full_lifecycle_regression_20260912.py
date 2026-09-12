"""隔离执行 PaperGuard 教师-管理员-学生真实材料全流程回归。

运行:
    cd /Users/a1234/Documents/perpetual/paperguard
    ../.venv/bin/python full_lifecycle_regression_20260912.py

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

    def expected_failure(self, name: str, response, expected: int, classification: str, note: str) -> None:
        passed = response.status_code == expected
        self.steps.append({
            "name": name,
            "status": "EXPECTED_FAILURE" if passed else "UNEXPECTED_RESULT",
            "http_status": response.status_code,
            "expected": expected,
            "note": note,
        })
        if not passed:
            raise AssertionError(f"{name}: expected known failure {expected}, got {response.status_code}: {response.text[:500]}")
        self.findings.append({
            "classification": classification,
            "name": name,
            "status_code": response.status_code,
            "detail": response.json().get("detail", response.text),
        })

    def expected_guard(self, name: str, response, expected: int, note: str) -> None:
        """Record an intentional permission/state guard without creating a Bug."""
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
    if not all(path.is_file() for path in (PAPER, TEMPLATE_DOCX, POLICY_PDF, TASK_BOOK, OPENING_REPORT, GUIDANCE_LOG, ADVISOR_SCORE, REVIEWER_SCORE, DEFENSE_APPLICATION, SIMILARITY_REPORT, COVER_DOC, MATERIAL_LIST, MIDTERM_TEMPLATE)):
        missing = [str(path) for path in (PAPER, TEMPLATE_DOCX, POLICY_PDF, TASK_BOOK, OPENING_REPORT, GUIDANCE_LOG, ADVISOR_SCORE, REVIEWER_SCORE, DEFENSE_APPLICATION, SIMILARITY_REPORT, COVER_DOC, MATERIAL_LIST, MIDTERM_TEMPLATE) if not path.is_file()]
        print(json.dumps({"status": "DATA_PRECONDITION", "missing": missing}, ensure_ascii=False))
        return 2

    sys.path.insert(0, str(BACKEND))
    suffix = uuid.uuid4().hex[:8]
    admin_password = f"IsolatedAdmin-{suffix}-2026a"
    teacher_password = f"Teacher-{suffix}-2026a"
    student_password = f"Student-{suffix}-2026a"

    with tempfile.TemporaryDirectory(prefix="paperguard-full-lifecycle-") as temp_dir:
        temp = Path(temp_dir)
        os.environ["PAPERGUARD_ADMIN_PASSWORD"] = admin_password
        os.environ["PAPERGUARD_DB_PATH"] = str(temp / "paperguard.db")
        os.environ["PAPERGUARD_TEMPLATE_DIR"] = str(temp / "templates")
        os.environ["PAPERGUARD_PROCESS_FILE_DIR"] = str(temp / "process-files")

        from fastapi.testclient import TestClient
        from app.main import app
        from app.worker import run_once

        with TestClient(app) as client:
            run = Run(client)
            admin_headers, admin_user = login(run, "admin", admin_password)

            teacher_name = f"teacher-{suffix}"
            teacher_reg = run.check(
                "教师注册",
                client.post("/api/auth/register", json={
                    "username": teacher_name,
                    "password": teacher_password,
                    "display_name": "王俞尹指导教师",
                    "role": "teacher",
                }),
                201,
            )
            assert teacher_reg["status"] == "pending"
            teacher_id = teacher_reg["id"]
            teacher_headers, teacher_user = login(run, teacher_name, teacher_password)

            pending_import = client.post("/api/templates/import", headers=teacher_headers, files=upload(TEMPLATE_DOCX))
            run.expected_guard(
                "待认证教师上传模板被拒绝",
                pending_import,
                403,
                "教师注册后的 pending 状态不能管理模板",
            )

            approved = run.check(
                "管理员认证教师",
                client.put(
                    f"/api/auth/admin/users/{teacher_id}/role-status",
                    headers=admin_headers,
                    json={"role": "teacher", "status": "active"},
                ),
            )
            assert approved["status"] == "active"
            # 当前会话按请求读取用户状态，认证后原 teacher token 可继续使用。
            run.check("认证后教师会话恢复管理权限", client.get("/api/templates", headers=teacher_headers))

            school_id = f"school-{suffix}"
            run.check(
                "管理员创建学校组织",
                client.post("/api/organizations", headers=admin_headers, json={
                    "id": school_id, "name": "王俞尹测试学校", "type": "school",
                }),
                201,
            )
            run.check(
                "管理员绑定教师组织范围",
                client.put(
                    f"/api/organizations/users/{teacher_id}/context",
                    headers=admin_headers,
                    json={"organization_context": {"school_id": school_id}},
                ),
            )

            imported = run.check(
                "教师上传并解析论文模板",
                client.post("/api/templates/import", headers=teacher_headers, files=upload(TEMPLATE_DOCX)),
                200,
            )
            template_id = f"teacher-template-{suffix}"
            template_payload = {
                key: imported[key]
                for key in ("description", "styles", "page_layout", "meta_fields", "cover_blocks", "sections", "source_docx")
                if key in imported
            }
            template_payload.update({
                "id": template_id,
                "name": f"2026届毕业设计报告模板-{suffix}",
                "version": "2026.1",
                "rule_packs": [],
                "requirement_set_ids": [],
                "organization_context": {"school_id": school_id},
            })
            template = run.check(
                "教师保存论文模板",
                client.post("/api/templates", headers=teacher_headers, json=template_payload),
                201,
            )

            policy_id = f"teacher-policy-{suffix}"
            policy = run.check(
                "教师创建写作规范",
                client.post("/api/policies", headers=teacher_headers, json={
                    "id": policy_id,
                    "name": "2026届毕业设计写作规范",
                    "level": "teacher",
                    "scope": "teacher",
                    "scope_id": teacher_id,
                    "owner_id": teacher_id,
                    "status": "draft",
                    "version": "1",
                    "description": "王俞尹测试用教师级论文规范",
                    "markdown_content": "# 论文规范\n\n正文、摘要、参考文献按学校模板执行。",
                    "items": [{
                        "id": f"manual-review-{suffix}",
                        "type": "manual",
                        "target": "document",
                        "severity": "warning",
                        "enforce": "soft",
                        "description": "指导教师确认论文内容完整",
                    }],
                }),
                201,
            )
            policy_with_doc = run.check(
                "教师上传规范原始文档",
                client.post(
                    f"/api/policies/{policy_id}/documents",
                    headers=teacher_headers,
                    files=upload(POLICY_PDF),
                ),
                201,
            )
            assert policy_with_doc["documents"]
            run.check("教师提交规范审核", client.post(f"/api/policies/{policy_id}/submit-review", headers=teacher_headers))
            published_policy = run.check("教师发布写作规范", client.post(f"/api/policies/{policy_id}/publish", headers=teacher_headers))
            assert published_policy["status"] == "published"

            template = run.check("读取教师模板", client.get(f"/api/templates/{template_id}", headers=teacher_headers))
            template["requirement_set_ids"] = [policy_id]
            template["rule_packs"] = []
            run.check("教师把规范绑定到模板", client.put(f"/api/templates/{template_id}", headers=teacher_headers, json=template))

            teacher_cohort = run.check(
                "教师创建毕业设计批次",
                client.post("/api/cohorts", headers=teacher_headers, json={
                    "name": f"2026届计算机毕业设计-{suffix}",
                    "organization_id": school_id,
                    "academic_year": "2026",
                }),
                201,
            )
            cohort_id = teacher_cohort["id"]

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
            student_headers, student_user = login(run, student_name, student_password)
            run.check(
                "管理员绑定学生组织范围",
                client.put(
                    f"/api/organizations/users/{student_id}/context",
                    headers=admin_headers,
                    json={"organization_context": {"school_id": school_id}},
                ),
            )

            run.check(
                "教师绑定学生和教师",
                client.post(f"/api/cohorts/{cohort_id}/members", headers=teacher_headers, json={
                    "student_id": student_id,
                    "teacher_id": teacher_id,
                    "reason": "完整流程回归绑定",
                }),
                201,
            )

            now = datetime.now(timezone.utc)
            required_materials = [
                "task_book", "opening_report", "guidance_log", "midterm_check",
                "final_report", "similarity_report", "advisor_score", "reviewer_score",
                "defense_application",
            ]
            stage = run.check(
                "教师创建论文全过程阶段",
                client.post(f"/api/cohorts/{cohort_id}/stages", headers=teacher_headers, json={
                    "key": "final",
                    "name": "毕业设计全过程",
                    "open_at": (now - timedelta(minutes=5)).isoformat(),
                    "deadline_at": (now + timedelta(days=7)).isoformat(),
                    "template_id": template_id,
                    "requirement_set_ids": [policy_id],
                    "rule_packs": [],
                    "required_materials": required_materials,
                    "required_manual_checks": [],
                    "allow_early_write": True,
                    "is_final": True,
                }),
                201,
            )
            stage_id = stage["id"]
            preview = run.check("教师预览阶段发布", client.post(f"/api/stages/{stage_id}/preview", headers=teacher_headers))
            assert preview["ok"] is True, preview
            published_stage = run.check("教师发布论文全过程阶段", client.post(f"/api/stages/{stage_id}/publish", headers=teacher_headers))
            assert published_stage["participant_count"] == 1

            student_tasks = run.check("学生读取阶段任务", client.get("/api/my-tasks", headers=student_headers))
            assert len(student_tasks) == 1
            participant_id = student_tasks[0]["id"]
            teacher_tasks = run.check("教师读取指导任务", client.get("/api/my-tasks", headers=teacher_headers))
            assert any(item["id"] == participant_id for item in teacher_tasks)

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
                "school": "王俞尹测试学校",
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
                            "paragraphs": [{"id": f"{section['key']}-regression", "text": "王俞尹真实论文流程回归补充内容。"}],
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
                "学生按教师模板导出论文",
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
                # 目录中没有 required_approvals 的材料以 submitted 作为就绪态。
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
            process_record("defense_application", student_headers, student_headers, DEFENSE_APPLICATION, {"absence_count": 0, "total_sessions": 3}, [teacher_headers, admin_headers])

            cover_record = run.check(
                "创建过程材料封面记录",
                client.post(
                    f"/api/stage-participants/{participant_id}/process-records",
                    headers=student_headers,
                    json={"artifact_type": "process_cover", "data": {"source_filename": COVER_DOC.name}},
                ),
                201,
            )
            cover_upload = client.post(
                f"/api/process-records/{cover_record['id']}/attachments",
                headers=student_headers,
                files=upload(COVER_DOC),
            )
            run.check(
                "上传真实过程材料封面 DOC",
                cover_upload,
                201,
            )

            cohort_record = run.check(
                "管理员创建过程材料清单记录",
                client.post(
                    f"/api/cohorts/{cohort_id}/process-records",
                    headers=admin_headers,
                    json={"artifact_type": "process_material_list", "data": {"generated_by": "管理员", "rows": [{"student_id": student_name, "file_name": PAPER.name}]}, "structured_form": False},
                ),
                201,
            )
            cohort_record_id = cohort_record["id"]
            run.check("管理员上传过程材料清单 Excel", client.post(f"/api/process-records/{cohort_record_id}/attachments", headers=admin_headers, files=upload(MATERIAL_LIST)), 201)
            run.check("管理员提交过程材料清单", client.post(f"/api/process-records/{cohort_record_id}/submit", headers=admin_headers))
            run.check("管理员审批过程材料清单", client.post(f"/api/process-records/{cohort_record_id}/decisions", headers=admin_headers, json={"decision": "approved"}))

            records = run.check("学生读取全部过程材料", client.get(f"/api/stage-participants/{participant_id}/process-records", headers=student_headers))
            ready_types = {item["artifact_type"] for item in records if item["state"] in {"submitted", "approved"}}
            assert set(required_materials).issubset(ready_types), (required_materials, ready_types)

            candidate = run.check(
                "学生创建论文提交候选版本",
                client.post("/api/submission-candidates", headers=student_headers, json={
                    "participant_id": participant_id,
                    "modification_note": "王俞尹真实材料全过程首次提交",
                    "materials": [item["id"] for item in records if item["artifact_type"] in required_materials and item["state"] in {"submitted", "approved"}],
                }),
                201,
            )
            assert candidate["status"] == "ready", candidate.get("gates")
            submitted = run.check(
                "学生确认提交论文",
                client.post("/api/submissions", headers=student_headers, json={
                    "candidate_id": candidate["id"],
                    "idempotency_key": f"full-regression-{suffix}-submit",
                    "warning_acknowledged": True,
                }),
            )
            submission_id = submitted["submission"]["id"]
            assert submitted["participant"]["state"] == "submitted"
            reviewed = run.check(
                "教师审核论文提交",
                client.post(f"/api/submissions/{submission_id}/reviews", headers=teacher_headers, json={"decision": "approved"}),
            )
            assert reviewed["participant"]["state"] == "pending_archive"

            archive = run.check(
                "管理员创建论文归档任务",
                client.post("/api/archives", headers=admin_headers, json={"submission_id": submission_id}),
                202,
            )
            worker_result = run_once()
            assert archive["job_id"] in worker_result["archives"]["succeeded"], worker_result
            archive_status = run.check("管理员确认归档完成", client.get(f"/api/archives/{archive['id']}", headers=admin_headers))
            assert archive_status["status"] == "succeeded"
            archive_download = run.check("学生下载最终归档包", client.get(f"/api/archives/{archive['id']}/download", headers=student_headers))
            assert archive_download["raw"][:2] == b"PK"

            report = run.check("管理员读取批次完成报告", client.get(f"/api/reports/cohorts/{cohort_id}", headers=admin_headers))
            assert report["state_counts"]["archived"] == 1, report

            for label, token_headers in (("学生", student_headers), ("教师", teacher_headers), ("管理员", admin_headers)):
                run.check(f"{label}退出登录", client.post("/api/auth/logout", headers=token_headers))
                run.check(f"{label}退出后会话失效", client.get("/api/auth/me", headers=token_headers), 401)

            result = {
                "status": "PASS_WITH_FINDINGS" if run.findings else "PASS",
                "suffix": suffix,
                "accounts": {
                    "admin": admin_user["username"],
                    "teacher": teacher_user["username"],
                    "student": student_user["username"],
                },
                "entities": {"school_id": school_id, "cohort_id": cohort_id, "template_id": template_id, "policy_id": policy_id, "participant_id": participant_id, "project_id": project_id, "submission_id": submission_id, "archive_id": archive["id"]},
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
