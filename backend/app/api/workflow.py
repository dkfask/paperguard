"""PaperGuard 论文指导业务闭环 API。

工作流记录采用版本化 JSON 存储，正文/模板/规范在候选版本和提交时冻结。
所有跨对象状态变化通过 SQLite IMMEDIATE transaction 与站内事件一起提交。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from app.api.templates import get_store
from app.auth import can_manage, current_user
from app.contracts.common import JobResponse
from app.core.context import get_request_context
from app.core.export.docx_exporter import export_project_to_docx
from app.db import Store, data_dir, new_id
from app.models.project import WritingProject
from app.models.process_record import catalog_item
from app.models.template import PaperTemplate
from app.models.workflow import (
    ArchiveCreate,
    BindProjectRequest,
    CandidateCreate,
    CohortCreate,
    CommentCreate,
    CommentResponse,
    CohortLifecycleRequest,
    ExtensionRequest,
    MemberUpsert,
    MemberImportRequest,
    OutboxAckRequest,
    OutboxClaimRequest,
    OutboxFailureRequest,
    ParticipantLifecycleRequest,
    ReviewDecision,
    RetryRequest,
    StageCreate,
    StageVersionCreate,
    StageVersionPublishRequest,
    SubmissionConfirm,
)
from app.domain.workflow_states import ACTIVE_PARTICIPANT_STATES, transition_state
from app.repositories.workflow_repository import WorkflowRepository
from app.services.access_service import visible_organization_ids
from app.services.audit_service import list_audit_events
from app.services.projects import create_project_from_template
from app.services.project_check import check_project
from app.services.project_validation import validate_project_completeness
from app.services.project_versions import project_attachment_refs, project_content_hash, project_snapshot, resolve_project_attachment
from app.services.resource_access import assert_resource_read
from app.services.policy_resolver import hydrate_requirement_set
from app.services.template_source import resolve_template_source
from app.services.wechat_content_security import assert_ugc_safe

router = APIRouter(prefix="/api", tags=["workflow"])

_MANAGER_ROLES = {"admin", "college_admin"}
_WORKFLOW_MANAGER_ROLES = _MANAGER_ROLES | {"teacher"}
_ACTIVE_STUDENT_STATES = set(ACTIVE_PARTICIPANT_STATES)


def _archive_dir() -> Path:
    """归档落盘目录：跟随数据目录（与数据库同源），不再硬编码到代码目录。

    历史实现为 `_ARCHIVE_DIR = Path(__file__).parent.parent.parent / "data" / "archives"`，
    与 `PAPERGUARD_DB_PATH` 脱钩：发布切换代码目录会带走旧归档，按数据目录做的备份也覆盖不到。
    改为函数式，避免在 import 期把环境变量固化，测试与运维切换后即时生效。
    """
    return data_dir() / "archives"

_OUTBOX_MAX_ATTEMPTS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_time(value: str, field: str, *, required: bool = False) -> datetime | None:
    if not value:
        if required:
            raise HTTPException(status_code=422, detail=f"{field}不能为空")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field}必须是 ISO 时间") from exc
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _record(store: Store, kind: str, record: dict, *, expected_revision: int | None = None, conn=None) -> dict:
    repository = WorkflowRepository(store)
    saved = (
        repository.save_in_transaction(conn, kind, record, expected_revision=expected_revision)
        if conn is not None
        else repository.save(kind, record, expected_revision=expected_revision)
    )
    if saved is None:
        raise HTTPException(status_code=409, detail="业务记录已被其他操作更新，请刷新后重试")
    # 事务内的乐观锁保存会生成新 revision；同步回调用方对象，避免后续
    # 同一事务链把旧 revision 当作并发冲突。
    record.clear()
    record.update(saved)
    return record


def _must(store: Store, kind: str, record_id: str, label: str) -> dict:
    record = WorkflowRepository(store).get(kind, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return record


def _visible_org_ids(store: Store, user: dict) -> set[str]:
    return visible_organization_ids(store, user)


def _ensure_manager(user: dict, *, workflow: bool = False) -> None:
    allowed_roles = _WORKFLOW_MANAGER_ROLES if workflow else _MANAGER_ROLES
    if user["role"] not in allowed_roles or user.get("status") != "active":
        detail = "仅认证教师或管理员可配置工作流" if workflow else "仅学院管理员或学校管理员可执行此操作"
        raise HTTPException(status_code=403, detail=detail)


def _ensure_active_person(store: Store, user_id: str, role: str, label: str) -> dict:
    person = store.get_user(user_id)
    if person is None or person.get("role") != role or person.get("status") != "active":
        raise HTTPException(status_code=422, detail=f"{label}账号不存在、角色不匹配或未启用")
    return person


def _cohort_access(store: Store, user: dict, cohort: dict) -> bool:
    if user["role"] in _MANAGER_ROLES:
        return cohort.get("organization_id") in _visible_org_ids(store, user)
    if user["role"] == "student":
        return any(
            item.get("cohort_id") == cohort["id"] and item.get("student_id") == user["id"]
            for item in store.list_workflow_records("supervision")
        )
    if user["role"] == "teacher" and cohort.get("organization_id") in _visible_org_ids(store, user):
        return True
    for item in store.list_workflow_records("supervision"):
        if item.get("cohort_id") != cohort["id"] or item.get("student_id") is None:
            continue
        current = _current_supervision(store, cohort["id"], item["student_id"])
        if current and current.get("teacher_id") == user["id"]:
            return True
    return False


def _visible_supervisions(store: Store, user: dict, cohort_id: str) -> list[dict]:
    records = _supervisions(store, cohort_id)
    if user["role"] in _MANAGER_ROLES:
        return records
    if user["role"] == "student":
        return [item for item in records if item.get("student_id") == user["id"]]
    return [
        item for item in records
        if item.get("teacher_id") == user["id"]
        and _current_supervision(store, cohort_id, item.get("student_id", ""))
        and _current_supervision(store, cohort_id, item.get("student_id", "")).get("teacher_id") == user["id"]
    ]


def _cohort_view(store: Store, user: dict, cohort: dict) -> dict:
    result = dict(cohort)
    members = _visible_supervisions(store, user, cohort["id"])
    result["member_ids"] = sorted({item["student_id"] for item in members})
    result["members"] = members
    result["stages"] = [
        {**item, "config": _stage_config(store, item)}
        for item in store.list_workflow_records("stage") if item.get("cohort_id") == cohort["id"]
    ]
    return result


def _participant_visible(store: Store, user: dict, participant: dict) -> bool:
    cohort = store.get_workflow_record("cohort", participant.get("cohort_id", ""))
    if not cohort or not _cohort_access(store, user, cohort):
        return False
    if user["role"] in _MANAGER_ROLES:
        return True
    if user["role"] == "student":
        return participant.get("student_id") == user["id"]
    relation = _current_supervision(store, participant["cohort_id"], participant.get("student_id", ""))
    return bool(relation and relation.get("teacher_id") == user["id"])


def _event_cohort_id(store: Store, event: dict) -> str | None:
    """Resolve an outbox resource to its cohort before exposing it to managers."""
    resource_type = event.get("resource_type")
    resource_id = event.get("resource_id", "")
    if resource_type == "cohort":
        return resource_id
    if resource_type == "participant":
        participant = store.get_workflow_record("participant", resource_id)
        return participant.get("cohort_id") if participant else None
    if resource_type == "submission":
        submission = store.get_workflow_record("submission", resource_id)
        participant = store.get_workflow_record("participant", submission.get("participant_id", "")) if submission else None
        return participant.get("cohort_id") if participant else None
    if resource_type == "comment":
        comment = store.get_workflow_record("comment", resource_id)
        submission = store.get_workflow_record("submission", comment.get("submission_id", "")) if comment else None
        participant = store.get_workflow_record("participant", submission.get("participant_id", "")) if submission else None
        return participant.get("cohort_id") if participant else None
    if resource_type == "archive":
        archive = store.get_workflow_record("archive", resource_id)
        participant = store.get_workflow_record("participant", archive.get("participant_id", "")) if archive else None
        return participant.get("cohort_id") if participant else None
    if resource_type == "process_record":
        record = store.get_workflow_record("process_record", resource_id)
        if not record:
            return None
        if record.get("scope_type") == "cohort":
            return record.get("scope_id")
        participant = store.get_workflow_record("participant", record.get("scope_id", ""))
        return participant.get("cohort_id") if participant else None
    return None


def _event_visible_to_manager(store: Store, user: dict, event: dict) -> bool:
    if user["role"] == "admin":
        return True
    cohort_id = _event_cohort_id(store, event)
    cohort = store.get_workflow_record("cohort", cohort_id or "")
    return bool(cohort and _cohort_access(store, user, cohort))


def _ensure_cohort_access(store: Store, user: dict, cohort_id: str) -> dict:
    cohort = _must(store, "cohort", cohort_id, "批次")
    if not _cohort_access(store, user, cohort):
        raise HTTPException(status_code=404, detail="批次不存在或无权访问")
    return cohort


def _supervisions(store: Store, cohort_id: str, student_id: str | None = None) -> list[dict]:
    records = [
        item for item in store.list_workflow_records("supervision")
        if item.get("cohort_id") == cohort_id
        and (student_id is None or item.get("student_id") == student_id)
    ]
    return sorted(records, key=lambda item: item.get("effective_from") or item.get("created_at", ""), reverse=True)


def _current_supervision(store: Store, cohort_id: str, student_id: str, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    for item in _supervisions(store, cohort_id, student_id):
        start = _parse_time(item.get("effective_from", ""), "生效时间") or datetime.min.replace(tzinfo=timezone.utc)
        end = _parse_time(item.get("effective_to", ""), "结束时间")
        if item.get("active", True) and start <= now and (end is None or now < end):
            return item
    return None


def supervised_student_ids(store: Store, teacher_id: str) -> dict[str, str]:
    """当前生效的「批次指导」关系：student_id -> cohort_id。

    批次级指导关系存在 supervision 记录里，与账号级的 users.teacher_id 是两套独立机制。
    教师工作台按前者取学生，「指导学生」页按后者取学生，两者长期不一致：
    批次里指派给某教师的学生不会出现在其指导学生列表里，页面静默显示「暂无关联学生」。
    这里对外暴露只读查询，供指导学生列表补全批次来源的学生。
    """
    now = datetime.now(timezone.utc)
    result: dict[str, str] = {}
    for item in store.list_workflow_records("supervision"):
        if item.get("teacher_id") != teacher_id:
            continue
        cohort_id = item.get("cohort_id", "")
        student_id = item.get("student_id", "")
        if not cohort_id or not student_id or student_id in result:
            continue
        if _current_supervision(store, cohort_id, student_id, now) is not None:
            result[student_id] = cohort_id
    return result


def _stage_config(store: Store, stage: dict) -> dict:
    return _must(store, "stage_config", stage["config_version_id"], "阶段配置")


def _stage_requirement_snapshot(store: Store, config: dict) -> dict:
    """Freeze explicitly selected policy sets into the stage config basis."""
    policy_sets = []
    resolved_items = []
    for set_id in config.get("requirement_set_ids", []) or []:
        requirement_set = store.get_requirement_set(set_id)
        if requirement_set is None:
            continue
        requirement_set = hydrate_requirement_set(requirement_set)
        payload = requirement_set.model_dump(mode="json")
        payload["content_hash"] = _hash(payload)
        policy_sets.append(payload)
        for item in requirement_set.items:
            item_payload = item.model_dump(mode="json")
            item_payload["source"] = requirement_set.name
            item_payload["source_version"] = requirement_set.version
            item_payload["source_detail"] = {
                "id": requirement_set.id, "name": requirement_set.name, "version": requirement_set.version,
            }
            resolved_items.append(item_payload)
    basis = {
        "requirement_set_ids": list(config.get("requirement_set_ids", []) or []),
        "policy_sets": policy_sets, "resolved_items": resolved_items,
    }
    basis["content_hash"] = _hash(basis)
    return basis


def _requirements_basis_hash(config: dict, project: WritingProject) -> str:
    """Bind the project policy snapshot and stage policy snapshot into one ack key."""
    project_hash = (project.requirement_snapshot or {}).get("content_hash", "")
    stage_hash = config.get("requirement_basis_hash", "")
    if not project_hash and not stage_hash:
        return ""
    return _hash({"project": project_hash, "stage": stage_hash})


def _project_for_stage_check(project: WritingProject, config: dict) -> WritingProject:
    """Run checks against the stage policy basis without mutating the live draft."""
    stage_basis = config.get("requirement_snapshot") or {}
    stage_ids = list(stage_basis.get("requirement_set_ids", []) or [])
    if not stage_ids:
        return project
    checked = project.model_copy(deep=True)
    snapshot = dict(checked.requirement_snapshot or {})
    previous_stage_ids = set(snapshot.get("stage_requirement_set_ids", []) or [])
    snapshot["policy_sets"] = [
        item for item in snapshot.get("policy_sets", [])
        if item.get("id") not in previous_stage_ids
    ] + list(stage_basis.get("policy_sets", []))
    snapshot["resolved_items"] = [
        item for item in snapshot.get("resolved_items", [])
        if (item.get("source_detail") or {}).get("id") not in previous_stage_ids
    ] + list(stage_basis.get("resolved_items", []))
    snapshot["stage_requirement_set_ids"] = stage_ids
    snapshot["stage_config_id"] = config.get("id", "")
    snapshot.pop("content_hash", None)
    snapshot["content_hash"] = _hash(snapshot)
    checked.requirement_snapshot = snapshot
    return checked


def _participant_stage_config(store: Store, stage: dict, participant: dict) -> dict:
    config_id = participant.get("config_version_id") or stage.get("config_version_id", "")
    return _must(store, "stage_config", config_id, "阶段配置")


def _prerequisites_satisfied(store: Store, participant: dict, config: dict) -> bool:
    required_stage_ids = config.get("prerequisite_stage_ids", [])
    if not required_stage_ids:
        return True
    participants = store.list_workflow_records("participant")
    return all(
        any(
            item.get("stage_id") == stage_id
            and item.get("student_id") == participant.get("student_id")
            and item.get("state") in {"approved", "pending_archive", "archived"}
            for item in participants
        )
        for stage_id in required_stage_ids
    )


def _stage_access(store: Store, user: dict, stage: dict) -> tuple[dict, dict]:
    cohort = _ensure_cohort_access(store, user, stage["cohort_id"])
    return cohort, _stage_config(store, stage)


def _participant_access(store: Store, user: dict, participant_id: str) -> tuple[dict, dict, dict, dict]:
    participant = _must(store, "participant", participant_id, "阶段任务")
    stage = _must(store, "stage", participant["stage_id"], "阶段")
    cohort = _ensure_cohort_access(store, user, participant["cohort_id"])
    if user["role"] == "student" and participant["student_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问其他学生的阶段任务")
    if user["role"] == "teacher":
        supervision = _current_supervision(store, participant["cohort_id"], participant["student_id"])
        if not supervision or supervision.get("teacher_id") != user["id"]:
            raise HTTPException(status_code=403, detail="当前指导关系无效")
    return participant, stage, cohort, _participant_stage_config(store, stage, participant)


def _ensure_project_workflow_access(store: Store, user: dict, project_id: str) -> WritingProject:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    if user["role"] == "admin":
        return project
    if user["role"] == "student" and project.owner_id == user["id"]:
        return project
    batch_id = (project.organization_context or {}).get("batch_id")
    cohort = store.get_workflow_record("cohort", batch_id) if batch_id else None
    if user["role"] == "teacher":
        if getattr(project, "teacher_id", "") == user["id"] or project.owner_id == user["id"]:
            return project
        participant = next((item for item in store.list_workflow_records("participant")
                            if item.get("project_id") == project_id and item.get("student_id")), None)
        relation = _current_supervision(store, participant["cohort_id"], participant["student_id"]) if participant else None
        if relation and relation.get("teacher_id") == user["id"]:
            return project
        raise HTTPException(status_code=403, detail="无权访问该论文历史版本")
    if cohort and _cohort_access(store, user, cohort):
        return project
    raise HTTPException(status_code=403, detail="无权访问该论文历史版本")


def _audit(store: Store, conn, *, actor_id: str, action: str, resource_type: str, resource_id: str,
           organization_id: str, result: str = "success", reason: str = "") -> None:
    store.save_audit_event_conn(conn, {
        "id": new_id("audit"),
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "organization_id": organization_id,
        "result": result,
        "reason": reason,
        "request_id": get_request_context().request_id,
        "created_at": _now(),
    })


def _transition(record: dict, entity: str, target: str) -> None:
    try:
        record["state"] = transition_state(entity, str(record.get("state") or ""), target)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="当前状态不允许此操作") from exc


def _notify(store: Store, conn, recipient_id: str, event_type: str, resource_type: str,
            resource_id: str, title: str, *, dedupe_key: str | None = None) -> bool:
    if not recipient_id:
        return False
    return store.save_workflow_event_conn(
        conn,
        event_type=event_type,
        recipient_id=recipient_id,
        resource_type=resource_type,
        resource_id=resource_id,
        dedupe_key=dedupe_key or f"{event_type}:{recipient_id}:{resource_type}:{resource_id}",
        data={"title": title, "event_type": event_type, "resource_type": resource_type, "resource_id": resource_id},
    )


def _late(participant: dict) -> bool:
    state = participant.get("state")
    if state in {"approved", "pending_archive", "archived", "withdrawn"}:
        return False
    now = datetime.now(timezone.utc)
    first_late = bool(participant.get("first_submission_late"))
    if not participant.get("first_submission_at"):
        first_deadline = _parse_time(
            participant.get("initial_deadline_at") or participant.get("effective_deadline_at", ""),
            "首次截止时间",
        )
        first_late = bool(first_deadline and now > first_deadline)
    if state == "needs_revision":
        revision_deadline = _parse_time(participant.get("revision_deadline_at", ""), "修订截止时间")
        return first_late or bool(revision_deadline and now > revision_deadline)
    return first_late


def _task_view(store: Store, participant: dict) -> dict:
    stage = _must(store, "stage", participant["stage_id"], "阶段")
    cohort = _must(store, "cohort", participant["cohort_id"], "批次")
    project = store.get_project(participant.get("project_id")) if participant.get("project_id") else None
    student = store.get_user(participant.get("student_id", "")) if participant.get("student_id") else None
    submission_id = (
        participant.get("current_submission_id", "")
        or participant.get("last_submission_id", "")
        or participant.get("previous_submission_id", "")
    )
    submission = store.get_workflow_record("submission", submission_id) if submission_id else None
    student_name = ""
    if student:
        student_name = str(student.get("display_name") or student.get("username") or "")
    return {
        **participant,
        "stage_name": stage["name"],
        "stage_key": stage["key"],
        "cohort_name": cohort["name"],
        "project_title": project.title if project else "",
        "student_name": student_name or participant.get("student_id", ""),
        "is_late": _late(participant),
        "first_submission_late": bool(participant.get("first_submission_late")),
        "current_submission": {
            "id": submission["id"],
            "number": submission["submission_number"],
            "state": submission["state"],
            "submitted_at": submission.get("submitted_at", ""),
            "review_cycle": submission.get("review_cycle", 1),
        } if submission else None,
    }


def _stage_preview(store: Store, stage: dict, cohort: dict, config: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not config.get("template_id") or store.get_template(config["template_id"]) is None:
        errors.append("阶段必须绑定已存在的模板")
    for set_id in config.get("requirement_set_ids", []) or []:
        requirement_set = store.get_requirement_set(set_id)
        if requirement_set is None:
            errors.append(f"阶段规范不存在: {set_id}")
        elif getattr(requirement_set.status, "value", requirement_set.status) != "published":
            errors.append(f"阶段规范未发布: {set_id}")
    opened = _parse_time(config.get("open_at", ""), "开放时间", required=True)
    deadline = _parse_time(config.get("deadline_at", ""), "首次提交截止时间", required=True)
    if opened and deadline and opened >= deadline:
        errors.append("开放时间必须早于首次提交截止时间")
    if not cohort.get("member_ids"):
        errors.append("批次还没有成员")
    members = []
    for member_id in cohort.get("member_ids", []):
        supervision = _current_supervision(store, cohort["id"], member_id)
        student = store.get_user(member_id)
        if not supervision:
            errors.append(f"学生 {member_id} 没有当前指导教师")
            continue
        members.append({
            "student_id": member_id,
            "teacher_id": supervision["teacher_id"],
            "student_name": (student or {}).get("display_name", member_id),
        })
    if config.get("required_materials"):
        warnings.append("必交材料已配置，提交时必须逐项绑定材料记录")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "member_snapshot": members}


def _gate(candidate: dict) -> list[dict]:
    return [item for item in candidate.get("gates", []) if item.get("blocking")]


def _stale_candidate_waiver_ids(store: Store, candidate: dict) -> list[str]:
    """审核通过前重新确认候选版本使用的豁免仍在有效期内。"""
    waiver_ids = {
        (finding.get("waiver") or {}).get("id")
        for finding in (candidate.get("check_report") or {}).get("waived", [])
    }
    waiver_ids.discard(None)
    if not waiver_ids:
        return []
    project = store.get_project(candidate.get("project_id", ""))
    if project is None:
        return sorted(waiver_ids)
    from app.services.project_check import _waiver_is_active
    now = datetime.now(timezone.utc)
    current = {item.get("id"): item for item in project.waivers}
    return sorted(
        waiver_id for waiver_id in waiver_ids
        if not current.get(waiver_id) or not _waiver_is_active(current[waiver_id], now)
    )


def _comment_records(store: Store, submission_id: str) -> list[dict]:
    return [item for item in store.list_workflow_records("comment") if item.get("submission_id") == submission_id]


def _conflict(code: str, message: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    for key, value in extra.items():
        if value not in (None, ""):
            detail[key] = value
    return HTTPException(status_code=409, detail=detail)


def _public_review(review: dict | None) -> dict | None:
    if not review:
        return None
    return {
        "id": review.get("id", ""),
        "decision": review.get("decision", ""),
        "reason": review.get("reason", ""),
        "review_cycle": review.get("review_cycle", 0),
        "created_at": review.get("created_at", ""),
    }


def _latest_review(store: Store, submission: dict) -> dict | None:
    review_id = str(submission.get("review_id") or "")
    if review_id:
        record = store.get_workflow_record("review", review_id)
        if record:
            return _public_review(record)
    reviews = [item for item in store.list_workflow_records("review") if item.get("submission_id") == submission.get("id")]
    if not reviews:
        return None
    reviews.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return _public_review(reviews[0])


def _archive_view(archive: dict) -> dict:
    """Return the public archive contract without exposing server filesystem paths."""
    result = {key: value for key, value in archive.items() if key != "file_path"}
    result.setdefault("job_id", archive.get("id", ""))
    if archive.get("status") == "succeeded":
        result["download_url"] = f"/api/archives/{archive['id']}/download"
    return result


def _submission_access(store: Store, user: dict, submission: dict) -> tuple[dict, dict, dict, dict]:
    participant, stage, cohort, config = _participant_access(store, user, submission["participant_id"])
    if user["role"] in _MANAGER_ROLES and not _cohort_access(store, user, cohort):
        raise HTTPException(status_code=403, detail="无权访问此提交")
    return submission, participant, stage, config


@router.post("/cohorts", status_code=201)
def create_cohort(req: CohortCreate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user, workflow=True)
    if store.get_organization_unit(req.organization_id) is None:
        raise HTTPException(status_code=422, detail="组织不存在")
    if req.organization_id not in _visible_org_ids(store, user):
        raise HTTPException(status_code=403, detail="批次不在当前组织范围内")
    starts_at = _parse_time(req.starts_at, "活动开始时间")
    ends_at = _parse_time(req.ends_at, "活动结束时间")
    if starts_at and ends_at and starts_at >= ends_at:
        raise HTTPException(status_code=422, detail="活动开始时间必须早于结束时间")
    cohort = {
        "id": new_id("cohort"), "name": req.name.strip(), "organization_id": req.organization_id,
        "academic_year": req.academic_year.strip(), "owner_id": req.owner_id or user["id"],
        "starts_at": req.starts_at, "ends_at": req.ends_at, "status": "draft", "revision": 0,
        "member_ids": [], "created_at": _now(),
    }
    with store.workflow_transaction() as conn:
        _record(store, "cohort", cohort, conn=conn)
        _audit(store, conn, actor_id=user["id"], action="cohort.create", resource_type="cohort",
               resource_id=cohort["id"], organization_id=req.organization_id)
    return cohort


@router.get("/cohorts")
def list_cohorts(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    return [
        _cohort_view(store, user, item) for item in store.list_workflow_records("cohort")
        if _cohort_access(store, user, item)
    ]


@router.get("/cohorts/{cohort_id}")
def get_cohort(cohort_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    cohort = _ensure_cohort_access(store, user, cohort_id)
    return _cohort_view(store, user, cohort)


@router.post("/cohorts/{cohort_id}/members", status_code=201)
def upsert_member(cohort_id: str, req: MemberUpsert, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user, workflow=True)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能新增或变更指导关系")
    normalized = _normalize_member_payload(store, req.model_dump(mode="json"))
    student_id = normalized["student_id"]
    teacher_id = normalized["teacher_id"]
    student = _ensure_active_person(store, student_id, "student", "学生")
    teacher = _ensure_active_person(store, teacher_id, "teacher", "教师")
    effective_from = req.effective_from or _now()
    _parse_time(effective_from, "生效时间", required=True)
    if student_id not in cohort.get("member_ids", []):
        cohort["member_ids"] = [*cohort.get("member_ids", []), student_id]
    current = _current_supervision(store, cohort_id, student_id)
    with store.workflow_transaction() as conn:
        if current and current.get("teacher_id") == teacher_id:
            return current
        now = _now()
        if current:
            current["active"] = False
            current["effective_to"] = effective_from
            _record(store, "supervision", current, expected_revision=current.get("revision", 0), conn=conn)
        relation = {
            "id": new_id("supervision"), "cohort_id": cohort_id, "student_id": student_id,
            "teacher_id": teacher_id, "effective_from": effective_from, "effective_to": "",
            "active": True, "reason": req.reason.strip(), "revision": 0, "created_at": now,
        }
        _record(store, "supervision", relation, conn=conn)
        _record(store, "cohort", cohort, expected_revision=cohort.get("revision", 0), conn=conn)
        # 未完成审核的任务跟随当前有效指导关系，历史审核者不变。
        for participant in store.list_workflow_records("participant"):
            if participant.get("cohort_id") == cohort_id and participant.get("student_id") == student_id \
                    and participant.get("state") not in {"approved", "archived"}:
                previous_teacher_id = participant.get("teacher_id", "")
                participant["teacher_id"] = teacher_id
                _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
                if previous_teacher_id and previous_teacher_id != teacher_id:
                    _notify(store, conn, previous_teacher_id, "supervision.transferred", "participant", participant["id"], "指导关系已转交")
                _notify(store, conn, teacher_id, "supervision.transferred", "participant", participant["id"], "您已接手阶段任务")
        _notify(store, conn, student_id, "supervision.transferred", "cohort", cohort_id, "指导关系已更新")
        _audit(store, conn, actor_id=user["id"], action="supervision.upsert", resource_type="cohort",
               resource_id=cohort_id, organization_id=cohort["organization_id"], reason=req.reason)
    return relation


@router.get("/cohorts/{cohort_id}/members")
def list_members(cohort_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    _ensure_cohort_access(store, user, cohort_id)
    return _visible_supervisions(store, user, cohort_id)


def _resolve_person_reference(store: Store, reference: str, role: str, label: str) -> str:
    value = str(reference or "").strip()
    if not value:
        return ""
    folded = value.casefold()
    matches = [
        person for person in store.list_users()
        if person.get("role") == role and person.get("status") == "active"
        and (person.get("id") == value or str(person.get("username", "")).casefold() == folded
             or str(person.get("display_name", "")).strip().casefold() == folded)
    ]
    if len(matches) > 1:
        raise HTTPException(status_code=422, detail=f"{label}“{value}”匹配多个账号，请改用唯一学号或工号")
    if not matches:
        raise HTTPException(status_code=422, detail=f"未找到启用的{label}账号：{value}")
    return matches[0]["id"]


def _normalize_member_payload(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if not str(normalized.get("student_id", "")).strip():
        normalized["student_id"] = _resolve_person_reference(
            store,
            normalized.get("student_ref") or normalized.get("student_no") or normalized.get("student_number") or normalized.get("student_name") or normalized.get("student"),
            "student",
            "学生",
        )
    if not str(normalized.get("teacher_id", "")).strip():
        normalized["teacher_id"] = _resolve_person_reference(
            store,
            normalized.get("teacher_ref") or normalized.get("teacher_no") or normalized.get("teacher_number") or normalized.get("teacher_name") or normalized.get("teacher"),
            "teacher",
            "教师",
        )
    if not normalized.get("student_id") or not normalized.get("teacher_id"):
        raise HTTPException(status_code=422, detail="学生和教师必须填写学号/工号、用户名或姓名")
    return normalized


def _member_import_row(store: Store, cohort: dict, raw: dict[str, Any], row_number: int,
                       seen_students: set[str]) -> dict[str, Any]:
    base = {
        "row_number": row_number,
        "student_id": str(raw.get("student_id", "")).strip() if isinstance(raw, dict) else "",
        "teacher_id": str(raw.get("teacher_id", "")).strip() if isinstance(raw, dict) else "",
        "status": "failed",
        "errors": [],
        "warnings": [],
        "payload": raw if isinstance(raw, dict) else {},
    }
    if not isinstance(raw, dict):
        base["errors"].append("行数据必须是对象")
        return base
    try:
        member = MemberUpsert.model_validate(_normalize_member_payload(store, raw))
    except ValidationError as exc:
        base["errors"].extend(error.get("msg", "字段格式错误") for error in exc.errors())
        return base
    base["payload"] = member.model_dump(mode="json")
    base["student_id"] = member.student_id
    base["teacher_id"] = member.teacher_id
    if member.student_id in seen_students:
        base["errors"].append("同一批量中学生重复出现")
    try:
        _ensure_active_person(store, member.student_id, "student", "学生")
    except HTTPException as exc:
        base["errors"].append(str(exc.detail))
    try:
        _ensure_active_person(store, member.teacher_id, "teacher", "教师")
    except HTTPException as exc:
        base["errors"].append(str(exc.detail))
    current = _current_supervision(store, cohort["id"], member.student_id)
    if current and current.get("teacher_id") == member.teacher_id:
        base["warnings"].append("该指导关系已存在，重试不会重复创建")
    if not base["errors"]:
        base["status"] = "ready"
        seen_students.add(member.student_id)
    return base


@router.post("/cohorts/{cohort_id}/members/import")
def import_members(cohort_id: str, req: MemberImportRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    _ensure_manager(user, workflow=True)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    seen_students: set[str] = set()
    results = [_member_import_row(store, cohort, raw, index + 1, seen_students) for index, raw in enumerate(req.rows)]
    if not req.dry_run:
        for result in results:
            if result["status"] != "ready":
                continue
            try:
                relation = upsert_member(
                    cohort_id,
                    MemberUpsert.model_validate(result["payload"]),
                    store,
                    user,
                )
                result["status"] = "imported"
                result["relation_id"] = relation.get("id", "")
            except HTTPException as exc:
                result["status"] = "failed"
                result["errors"].append(str(exc.detail))
    imported = sum(1 for item in results if item["status"] == "imported")
    ready = sum(1 for item in results if item["status"] == "ready")
    failed_rows = [item for item in results if item["status"] == "failed"]
    return {
        "dry_run": req.dry_run,
        "ok": not failed_rows,
        "results": results,
        "failed_rows": failed_rows,
        "summary": {"total": len(results), "imported": imported, "ready": ready, "failed": len(failed_rows)},
    }


@router.post("/cohorts/{cohort_id}/stages", status_code=201)
def create_stage(cohort_id: str, req: StageCreate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user, workflow=True)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能创建新阶段")
    if any(item.get("key") == req.key for item in store.list_workflow_records("stage") if item.get("cohort_id") == cohort_id):
        raise HTTPException(status_code=409, detail="同一批次中阶段 key 不能重复")
    template_id = req.template_id or "default-gb7713"
    if store.get_template(template_id) is None:
        raise HTTPException(status_code=422, detail="阶段模板不存在")
    unknown_materials = [item for item in req.required_materials if catalog_item(item) is None]
    if unknown_materials:
        raise HTTPException(status_code=422, detail=f"阶段必交材料未登记: {', '.join(unknown_materials)}")
    config = {
        "id": new_id("stage-config"), "stage_id": "", "version": 1, "status": "draft",
        **req.model_dump(mode="json"), "template_id": template_id, "created_at": _now(), "revision": 0,
    }
    stage = {
        "id": new_id("stage"), "cohort_id": cohort_id, "key": req.key.strip(), "name": req.name.strip(),
        "status": "draft", "config_version": 1, "config_version_id": config["id"], "revision": 0,
        "created_at": _now(),
    }
    config["stage_id"] = stage["id"]
    with store.workflow_transaction() as conn:
        _record(store, "stage_config", config, conn=conn)
        _record(store, "stage", stage, conn=conn)
        _audit(store, conn, actor_id=user["id"], action="stage.create", resource_type="stage", resource_id=stage["id"], organization_id=cohort["organization_id"])
    return {**stage, "config": config}


@router.get("/cohorts/{cohort_id}/stages")
def list_stages(cohort_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    _ensure_cohort_access(store, user, cohort_id)
    return [
        {**stage, "config": _stage_config(store, stage)}
        for stage in store.list_workflow_records("stage") if stage.get("cohort_id") == cohort_id
    ]


@router.post("/stages/{stage_id}/preview")
def preview_stage(stage_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user, workflow=True)
    stage = _must(store, "stage", stage_id, "阶段")
    cohort = _ensure_cohort_access(store, user, stage["cohort_id"])
    config = _stage_config(store, stage)
    return {"stage": stage, "config": config, **_stage_preview(store, stage, cohort, config)}


_STAGE_CONFIG_FIELDS = (
    "name", "open_at", "deadline_at", "revision_days", "prerequisite_stage_ids",
    "template_id", "requirement_set_ids", "rule_packs", "required_materials",
    "required_manual_checks", "allow_early_write", "is_final",
)


def _stage_config_diff(old: dict, new: dict) -> list[dict[str, Any]]:
    return [
        {"field": field, "before": old.get(field), "after": new.get(field)}
        for field in _STAGE_CONFIG_FIELDS
        if old.get(field) != new.get(field)
    ]


@router.post("/stages/{stage_id}/versions", status_code=201)
def create_stage_version(stage_id: str, req: StageVersionCreate,
                         store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    """Create an immutable draft config version without changing existing tasks."""
    _ensure_manager(user, workflow=True)
    stage = _must(store, "stage", stage_id, "阶段")
    cohort = _ensure_cohort_access(store, user, stage["cohort_id"])
    if stage.get("status") != "published":
        raise HTTPException(status_code=409, detail="只有已发布阶段可以创建升级配置版本")
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能创建新的阶段配置版本")
    current = _stage_config(store, stage)
    if req.base_revision is not None and req.base_revision != current.get("revision", 0):
        raise HTTPException(status_code=409, detail={"message": "当前配置已变化，请刷新后重试", "revision": current.get("revision", 0)})
    new_config = dict(current)
    new_config.update({
        "id": new_id("stage-config"), "version": int(current.get("version", 1)) + 1,
        "status": "draft", "created_at": _now(), "revision": 0,
        "upgrade_reason": req.reason.strip(), "supersedes_config_id": current["id"],
        "member_snapshot": [],
    })
    for field in _STAGE_CONFIG_FIELDS:
        value = getattr(req, field)
        if value is not None:
            new_config[field] = value
    template_id = new_config.get("template_id") or "default-gb7713"
    if store.get_template(template_id) is None:
        raise HTTPException(status_code=422, detail="阶段模板不存在")
    unknown_materials = [item for item in new_config.get("required_materials", []) if catalog_item(item) is None]
    if unknown_materials:
        raise HTTPException(status_code=422, detail=f"阶段必交材料未登记: {', '.join(unknown_materials)}")
    new_config["template_id"] = template_id
    new_config["requirement_snapshot"] = _stage_requirement_snapshot(store, new_config)
    new_config["requirement_basis_hash"] = new_config["requirement_snapshot"]["content_hash"]
    preflight = _stage_preview(store, stage, cohort, new_config)
    affected = [
        {
            "participant_id": item["id"], "student_id": item.get("student_id", ""),
            "state": item.get("state", ""), "current_config_id": item.get("config_version_id") or current["id"],
        }
        for item in store.list_workflow_records("participant")
        if item.get("stage_id") == stage_id and item.get("state") not in {"archived", "exited"}
    ]
    with store.workflow_transaction() as conn:
        _record(store, "stage_config", new_config, conn=conn)
        _audit(store, conn, actor_id=user["id"], action="stage.version.create", resource_type="stage_config",
               resource_id=new_config["id"], organization_id=cohort["organization_id"], reason=req.reason)
    return {
        "stage": stage, "current_config": current, "new_config": new_config,
        "diff": _stage_config_diff(current, new_config), "preflight": preflight,
        "affected_participants": affected,
    }


@router.post("/stages/{stage_id}/versions/{config_id}/publish")
def publish_stage_version(stage_id: str, config_id: str, req: StageVersionPublishRequest,
                          store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    """Publish one draft config and explicitly migrate only selected tasks."""
    _ensure_manager(user, workflow=True)
    stage = _must(store, "stage", stage_id, "阶段")
    cohort = _ensure_cohort_access(store, user, stage["cohort_id"])
    if stage.get("status") != "published":
        raise HTTPException(status_code=409, detail="只有已发布阶段可以发布升级配置")
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能发布新的阶段配置版本")
    new_config = _must(store, "stage_config", config_id, "阶段配置")
    if new_config.get("stage_id") != stage_id or new_config.get("status") != "draft":
        raise HTTPException(status_code=409, detail="只能发布当前阶段的草稿配置版本")
    current = _stage_config(store, stage)
    new_config["requirement_snapshot"] = _stage_requirement_snapshot(store, new_config)
    new_config["requirement_basis_hash"] = new_config["requirement_snapshot"]["content_hash"]
    preview = _stage_preview(store, stage, cohort, new_config)
    if not preview["ok"]:
        raise HTTPException(status_code=409, detail={"message": "阶段配置存在发布阻断项", "errors": preview["errors"], "warnings": preview["warnings"]})
    requested_ids = list(dict.fromkeys(req.participant_ids))
    participants = [item for item in store.list_workflow_records("participant") if item.get("stage_id") == stage_id]
    by_id = {item["id"]: item for item in participants}
    missing_ids = [item for item in requested_ids if item not in by_id]
    if missing_ids:
        raise HTTPException(status_code=404, detail={"message": "待迁移阶段任务不存在", "participant_ids": missing_ids})
    selected = [by_id[item] for item in requested_ids]
    blocked = [item["id"] for item in selected if item.get("state") in {"archived", "exited"}]
    if blocked:
        raise HTTPException(status_code=409, detail={"message": "已归档或已退出任务不能迁移配置版本", "participant_ids": blocked})
    incompatible = []
    for item in selected:
        project = store.get_project(item.get("project_id", "")) if item.get("project_id") else None
        if project and project.template_id != new_config.get("template_id"):
            incompatible.append({"participant_id": item["id"], "project_template_id": project.template_id, "required_template_id": new_config.get("template_id")})
    if incompatible:
        raise HTTPException(status_code=409, detail={"message": "存量论文模板与新配置不一致，不能直接迁移", "incompatible": incompatible})
    old_config_id = current["id"]
    new_config["status"] = "published"
    new_config["member_snapshot"] = preview["member_snapshot"]
    new_config["published_at"] = _now()
    new_config["published_by"] = user["id"]
    new_config["publish_reason"] = req.reason.strip()
    new_deadline = _parse_time(new_config.get("deadline_at", ""), "首次提交截止时间", required=True)
    with store.workflow_transaction() as conn:
        if old_config_id != config_id:
            current["status"] = "superseded"
            current["superseded_by_config_id"] = config_id
            _record(store, "stage_config", current, expected_revision=current.get("revision", 0), conn=conn)
        _record(store, "stage_config", new_config, expected_revision=new_config.get("revision", 0), conn=conn)
        stage["config_version"] = new_config["version"]
        stage["config_version_id"] = config_id
        stage["name"] = new_config.get("name", stage["name"])
        _record(store, "stage", stage, expected_revision=stage.get("revision", 0), conn=conn)
        migrated = []
        for participant in selected:
            previous_participant_config_id = participant.get("config_version_id") or old_config_id
            participant["config_version_id"] = config_id
            participant["acknowledged_snapshot_hash"] = ""
            participant["config_migration"] = {
                "from_config_id": previous_participant_config_id,
                "to_config_id": config_id, "reason": req.reason.strip(), "actor_id": user["id"], "at": _now(),
            }
            existing_deadline = _parse_time(participant.get("effective_deadline_at", ""), "现有截止时间")
            participant["effective_deadline_at"] = max(existing_deadline or new_deadline, new_deadline).isoformat(timespec="seconds")
            _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
            migrated.append(participant["id"])
            _notify(store, conn, participant["student_id"], "stage.config_migrated", "participant", participant["id"], "阶段要求已升级，请重新确认")
        _audit(store, conn, actor_id=user["id"], action="stage.version.publish", resource_type="stage_config",
               resource_id=config_id, organization_id=cohort["organization_id"], reason=req.reason)
    return {
        "stage": stage, "config": new_config, "previous_config_id": old_config_id,
        "migrated_participant_ids": requested_ids, "migration_required_for_existing": True,
    }


@router.post("/stages/{stage_id}/publish")
def publish_stage(stage_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user, workflow=True)
    stage = _must(store, "stage", stage_id, "阶段")
    cohort = _ensure_cohort_access(store, user, stage["cohort_id"])
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能发布新阶段")
    config = _stage_config(store, stage)
    if stage.get("status") != "draft":
        raise HTTPException(status_code=409, detail="阶段只能从草稿发布")
    preview = _stage_preview(store, stage, cohort, config)
    if not preview["ok"]:
        raise HTTPException(status_code=409, detail={"message": "阶段存在发布阻断项", "errors": preview["errors"], "warnings": preview["warnings"]})
    deadline = _parse_time(config["deadline_at"], "截止时间", required=True)
    config["requirement_snapshot"] = _stage_requirement_snapshot(store, config)
    config["requirement_basis_hash"] = config["requirement_snapshot"]["content_hash"]
    with store.workflow_transaction() as conn:
        stage["status"] = "published"
        _record(store, "stage", stage, expected_revision=stage.get("revision", 0), conn=conn)
        config["status"] = "published"
        config["member_snapshot"] = preview["member_snapshot"]
        _record(store, "stage_config", config, expected_revision=config.get("revision", 0), conn=conn)
        for member in preview["member_snapshot"]:
            participant_id = new_id("participant")
            participant = {
                "id": participant_id, "stage_id": stage_id, "cohort_id": cohort["id"],
                "student_id": member["student_id"], "teacher_id": member["teacher_id"],
                "config_version_id": config["id"],
                "project_id": "", "state": "not_started", "review_cycle": 0,
                "initial_deadline_at": deadline.isoformat(timespec="seconds"),
                "effective_deadline_at": deadline.isoformat(timespec="seconds"),
                "revision_deadline_at": "", "acknowledged_snapshot_hash": "",
                "current_submission_id": "", "revision": 0, "created_at": _now(),
            }
            _record(store, "participant", participant, conn=conn)
            _notify(store, conn, member["student_id"], "stage.published", "participant", participant_id, f"新阶段任务已发布：{stage['name']}")
            _notify(store, conn, member["teacher_id"], "stage.published", "participant", participant_id, f"学生阶段任务已发布：{stage['name']}")
        cohort["status"] = "published"
        _record(store, "cohort", cohort, expected_revision=cohort.get("revision", 0), conn=conn)
        _audit(store, conn, actor_id=user["id"], action="stage.publish", resource_type="stage", resource_id=stage_id, organization_id=cohort["organization_id"])
    stage["status"] = "published"
    return {"stage": stage, "participant_count": len(preview["member_snapshot"]), "member_snapshot": preview["member_snapshot"]}


@router.get("/my-tasks")
def my_tasks(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    result = []
    for participant in store.list_workflow_records("participant"):
        cohort_id = participant.get("cohort_id")
        student_id = participant.get("student_id")
        if not cohort_id or not student_id or not participant.get("stage_id"):
            continue
        allowed = student_id == user["id"]
        if user["role"] == "teacher":
            supervision = _current_supervision(store, cohort_id, student_id)
            allowed = bool(supervision and supervision.get("teacher_id") == user["id"])
        if user["role"] in _MANAGER_ROLES:
            cohort = store.get_workflow_record("cohort", cohort_id)
            allowed = bool(cohort and _cohort_access(store, user, cohort))
        if allowed:
            result.append(_task_view(store, participant))
    return result


@router.get("/stage-participants/{participant_id}")
def get_participant(participant_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    participant, stage, cohort, config = _participant_access(store, user, participant_id)
    return {"participant": _task_view(store, participant), "stage": stage, "cohort": cohort, "config": config}


@router.get("/projects/{project_id}/versions")
def list_project_versions(project_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    _ensure_project_workflow_access(store, user, project_id)
    return [item for item in store.list_workflow_records("project_version") if item.get("project_id") == project_id]


def _version_summary(version: dict) -> dict:
    return {
        "id": version.get("id", ""),
        "project_id": version.get("project_id", ""),
        "source_revision": version.get("source_revision", 0),
        "content_hash": version.get("content_hash", ""),
        "created_by": version.get("created_by", ""),
        "created_reason": version.get("created_reason", ""),
        "created_at": version.get("created_at", ""),
    }


def _version_content_diff(left: dict, right: dict) -> dict:
    left_snapshot = left.get("content_snapshot") or {}
    right_snapshot = right.get("content_snapshot") or {}
    left_sections = {item.get("key"): item for item in left_snapshot.get("sections", []) if item.get("key")}
    right_sections = {item.get("key"): item for item in right_snapshot.get("sections", []) if item.get("key")}
    sections = []
    for section_key in dict.fromkeys([*left_sections.keys(), *right_sections.keys()]):
        before = left_sections.get(section_key)
        after = right_sections.get(section_key)
        if before is None:
            sections.append({"key": section_key, "status": "added", "before": None, "after": after})
            continue
        if after is None:
            sections.append({"key": section_key, "status": "removed", "before": before, "after": None})
            continue
        before_blocks = {item.get("id"): item for item in before.get("paragraphs", []) if item.get("id")}
        after_blocks = {item.get("id"): item for item in after.get("paragraphs", []) if item.get("id")}
        blocks = []
        for block_id in dict.fromkeys([*before_blocks.keys(), *after_blocks.keys()]):
            old_block = before_blocks.get(block_id)
            new_block = after_blocks.get(block_id)
            if old_block is None:
                blocks.append({"id": block_id, "status": "added", "before": None, "after": new_block})
            elif new_block is None:
                blocks.append({"id": block_id, "status": "removed", "before": old_block, "after": None})
            elif old_block != new_block:
                blocks.append({"id": block_id, "status": "changed", "before": old_block, "after": new_block})
        section_changed = before.get("title") != after.get("title") or before.get("type") != after.get("type") or bool(blocks)
        if section_changed:
            sections.append({
                "key": section_key,
                "status": "changed",
                "before": {"title": before.get("title", ""), "type": before.get("type", "")},
                "after": {"title": after.get("title", ""), "type": after.get("type", "")},
                "blocks": blocks,
            })
    left_attachments = {item.get("filename"): item for item in left.get("attachment_refs", []) if item.get("filename")}
    right_attachments = {item.get("filename"): item for item in right.get("attachment_refs", []) if item.get("filename")}
    attachment_changes = {"added": [], "removed": [], "changed": []}
    for filename in dict.fromkeys([*left_attachments.keys(), *right_attachments.keys()]):
        old_ref = left_attachments.get(filename)
        new_ref = right_attachments.get(filename)
        if old_ref is None:
            attachment_changes["added"].append(new_ref)
        elif new_ref is None:
            attachment_changes["removed"].append(old_ref)
        elif old_ref.get("sha256") != new_ref.get("sha256") or old_ref.get("size_bytes") != new_ref.get("size_bytes"):
            attachment_changes["changed"].append({"before": old_ref, "after": new_ref})
    meta_changed = left_snapshot.get("title") != right_snapshot.get("title") or left_snapshot.get("meta") != right_snapshot.get("meta")
    return {
        "changed": bool(sections or any(attachment_changes.values()) or meta_changed),
        "meta_changed": meta_changed,
        "sections": sections,
        "attachments": attachment_changes,
    }


@router.get("/projects/{project_id}/versions/compare")
def compare_project_versions(project_id: str, left: str, right: str,
                             store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_project_workflow_access(store, user, project_id)
    left_version = _must(store, "project_version", left, "左侧论文版本")
    right_version = _must(store, "project_version", right, "右侧论文版本")
    if left_version.get("project_id") != project_id or right_version.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="论文版本不存在")
    return {
        "project_id": project_id,
        "left": _version_summary(left_version),
        "right": _version_summary(right_version),
        "diff": _version_content_diff(left_version, right_version),
    }


@router.get("/project-versions/{version_id}")
def get_project_version(version_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    version = _must(store, "project_version", version_id, "论文版本")
    _ensure_project_workflow_access(store, user, version["project_id"])
    return version


@router.post("/projects/{project_id}/restore-version")
def restore_project_version(project_id: str, req: RestoreVersionRequest,
                            store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    current = _ensure_project_workflow_access(store, user, project_id)
    if user.get("role") == "student":
        from app.api.projects import _ensure_student_can_write
        _ensure_student_can_write(current, user, store)
    version = _must(store, "project_version", req.version_id, "论文版本")
    if version.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="论文版本不存在")
    expected = current.revision if req.base_revision is None else req.base_revision
    if expected != current.revision:
        raise HTTPException(status_code=409, detail={"message": "当前论文已被更新，请刷新后重试", "revision": current.revision})
    snapshot = WritingProject.model_validate(version["content_snapshot"])
    current.title = snapshot.title
    current.meta = snapshot.meta
    current.sections = snapshot.sections
    saved = store.update_project_if_revision(current, expected)
    if saved is None:
        raise HTTPException(status_code=409, detail="当前论文已被更新，请刷新后重试")
    restored_version = {
        "id": new_id("version"), "project_id": project_id, "source_revision": saved.revision,
        "content_snapshot": project_snapshot(saved), "attachment_refs": project_attachment_refs(saved),
        "content_hash": project_content_hash(saved), "created_by": user["id"],
        "created_reason": "restore_version", "restored_from_version_id": version["id"],
        "immutable": True, "revision": 0, "created_at": _now(),
    }
    with store.workflow_transaction() as conn:
        _record(store, "project_version", restored_version, conn=conn)
        batch_id = (saved.organization_context or {}).get("batch_id", "")
        cohort = store.get_workflow_record("cohort", batch_id)
        _audit(store, conn, actor_id=user["id"], action="project.restore_version", resource_type="project", resource_id=project_id, organization_id=cohort.get("organization_id", "") if cohort else "")
    return {"project": saved, "version": restored_version}


@router.post("/stage-participants/{participant_id}/project")
def bind_project(participant_id: str, req: BindProjectRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    participant, stage, cohort, config = _participant_access(store, user, participant_id)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="只有学生可以绑定主论文")
    if stage.get("status") != "published":
        raise HTTPException(status_code=409, detail="阶段尚未发布")
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能绑定主论文")
    template_id = config["template_id"]
    if req.project_id:
        project = store.get_project(req.project_id)
        if project is None or project.owner_id != user["id"]:
            raise HTTPException(status_code=404, detail="论文不存在或无权访问")
        if project.template_id != template_id:
            raise HTTPException(status_code=409, detail={"message": "论文模板与阶段要求不一致", "required_template_id": template_id, "project_template_id": project.template_id})
        bound_batch_id = (project.organization_context or {}).get("batch_id")
        if bound_batch_id and bound_batch_id != cohort["id"]:
            raise HTTPException(status_code=409, detail="论文已绑定其他批次，不能跨批次复用")
    else:
        template = store.get_template(template_id)
        context = dict(user.get("organization_context") or {})
        context["batch_id"] = cohort["id"]
        project = create_project_from_template(
            store, template, owner_id=user["id"], teacher_id=participant.get("teacher_id"),
            organization_context=context, stage=stage["key"],
        )
        project.title = req.title.strip() or f"{cohort['name']} · {stage['name']}"
        store.save_project(project)
    if participant.get("project_id") and participant["project_id"] != project.id:
        raise HTTPException(status_code=409, detail="一个阶段只能绑定一篇主论文")
    with store.workflow_transaction() as conn:
        participant["project_id"] = project.id
        if participant.get("state") == "not_started":
            opened = _parse_time(config.get("open_at", ""), "开放时间", required=True)
            prereqs_done = _prerequisites_satisfied(store, participant, config)
            if config.get("allow_early_write") or (opened and datetime.now(timezone.utc) >= opened and prereqs_done):
                _transition(participant, "participant", "in_progress")
        _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
        _audit(store, conn, actor_id=user["id"], action="participant.bind_project", resource_type="participant", resource_id=participant_id, organization_id=cohort["organization_id"])
    return {"participant": _task_view(store, participant), "project": project}


@router.post("/stage-participants/{participant_id}/acknowledge")
def acknowledge_task(participant_id: str, payload: dict, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    participant, stage, cohort, config = _participant_access(store, user, participant_id)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="只有学生可以确认阶段要求")
    if not participant.get("project_id"):
        raise HTTPException(status_code=409, detail="请先绑定主论文")
    project = store.get_project(participant["project_id"])
    snapshot_hash = _requirements_basis_hash(config, project)
    requested = str(payload.get("snapshot_hash") or snapshot_hash)
    if snapshot_hash and requested != snapshot_hash:
        raise HTTPException(status_code=409, detail="规范版本已变化，请重新读取要求")
    with store.workflow_transaction() as conn:
        participant["acknowledged_snapshot_hash"] = requested
        if participant.get("state") == "not_started":
            opened = _parse_time(config.get("open_at", ""), "开放时间", required=True)
            if config.get("allow_early_write") or (
                opened and datetime.now(timezone.utc) >= opened and _prerequisites_satisfied(store, participant, config)
            ):
                _transition(participant, "participant", "in_progress")
        _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
        _audit(store, conn, actor_id=user["id"], action="participant.acknowledge", resource_type="participant", resource_id=participant_id, organization_id=cohort["organization_id"])
    return participant


@router.put("/stage-participants/{participant_id}/extension")
def extend_deadline(participant_id: str, req: ExtensionRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    # 教师可为自己指导的学生延期；_participant_access 继续把教师限制在有效指导关系内。
    _ensure_manager(user, workflow=True)
    participant, stage, cohort, _ = _participant_access(store, user, participant_id)
    effective = _parse_time(req.effective_deadline_at, "延期截止时间", required=True)
    current = _parse_time(participant.get("effective_deadline_at", ""), "现有截止时间")
    if current and effective <= current:
        raise HTTPException(status_code=422, detail="延期截止时间必须晚于现有截止时间")
    with store.workflow_transaction() as conn:
        participant["effective_deadline_at"] = effective.isoformat(timespec="seconds")
        participant["extension"] = {"reason": req.reason.strip(), "approved_by": user["id"], "approved_at": _now()}
        _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
        _notify(store, conn, participant["student_id"], "participant.extended", "participant", participant_id, "阶段任务截止时间已延期")
        _audit(store, conn, actor_id=user["id"], action="participant.extend", resource_type="participant", resource_id=participant_id, organization_id=cohort["organization_id"], reason=req.reason)
    return _task_view(store, participant)


@router.post("/stage-participants/{participant_id}/exit")
def exit_participant(participant_id: str, req: ParticipantLifecycleRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    participant, _, cohort, _ = _participant_access(store, user, participant_id)
    if participant.get("state") in {"archived", "withdrawn", "exited"}:
        raise HTTPException(status_code=409, detail="当前阶段任务不能再次退出")
    with store.workflow_transaction() as conn:
        _transition(participant, "participant", "exited")
        participant["exit"] = {"reason": req.reason.strip(), "actor_id": user["id"], "at": _now()}
        _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
        _notify(store, conn, participant["student_id"], "participant.exited", "participant", participant_id, "阶段任务已记录为退出")
        _audit(store, conn, actor_id=user["id"], action="participant.exit", resource_type="participant", resource_id=participant_id, organization_id=cohort["organization_id"], reason=req.reason)
    return _task_view(store, participant)


@router.post("/stage-participants/{participant_id}/reopen")
def reopen_participant(participant_id: str, req: ParticipantLifecycleRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    participant, _, cohort, _ = _participant_access(store, user, participant_id)
    if participant.get("state") not in {"approved", "pending_archive", "archived"}:
        raise HTTPException(status_code=409, detail="只有已通过、待归档或已归档任务可以重开")
    previous_submission_id = participant.get("current_submission_id", "")
    previous_archive_id = participant.get("archive_id", "")
    with store.workflow_transaction() as conn:
        _transition(participant, "participant", "in_progress")
        participant["current_submission_id"] = ""
        participant["last_submission_id"] = previous_submission_id or participant.get("last_submission_id", "")
        participant["previous_archive_id"] = previous_archive_id or participant.get("previous_archive_id", "")
        participant["reopen_count"] = int(participant.get("reopen_count", 0)) + 1
        participant["reopen"] = {"reason": req.reason.strip(), "actor_id": user["id"], "at": _now(), "from_submission_id": previous_submission_id, "from_archive_id": previous_archive_id}
        participant["archive_id"] = ""
        participant["revision_deadline_at"] = ""
        _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
        if previous_archive_id:
            archive = store.get_workflow_record("archive", previous_archive_id)
            if archive:
                archive["superseded_at"] = _now()
                archive["superseded_by_reopen"] = True
                _record(store, "archive", archive, expected_revision=archive.get("revision", 0), conn=conn)
        _notify(store, conn, participant["student_id"], "participant.reopened", "participant", participant_id, "阶段任务已重开，请完成修订后重新提交")
        _audit(store, conn, actor_id=user["id"], action="participant.reopen", resource_type="participant", resource_id=participant_id, organization_id=cohort["organization_id"], reason=req.reason)
    return _task_view(store, participant)


@router.post("/cohorts/{cohort_id}/close")
def close_cohort(cohort_id: str, req: CohortLifecycleRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    unfinished = [item["id"] for item in store.list_workflow_records("participant")
                  if item.get("cohort_id") == cohort_id and item.get("state") not in {"archived", "exited", "withdrawn"}]
    if unfinished and not req.force:
        raise HTTPException(status_code=409, detail={"message": "批次仍有未完成阶段任务", "participant_ids": unfinished})
    with store.workflow_transaction() as conn:
        cohort["status"] = "closed"
        cohort["closed_at"] = _now()
        cohort["closed_by"] = user["id"]
        cohort["close_reason"] = req.reason.strip()
        _record(store, "cohort", cohort, expected_revision=cohort.get("revision", 0), conn=conn)
        _audit(store, conn, actor_id=user["id"], action="cohort.close", resource_type="cohort", resource_id=cohort_id, organization_id=cohort["organization_id"], reason=req.reason)
    return _cohort_view(store, user, cohort)


@router.post("/cohorts/{cohort_id}/reopen")
def reopen_cohort(cohort_id: str, req: CohortLifecycleRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    if cohort.get("status") != "closed":
        raise HTTPException(status_code=409, detail="只有已关闭批次可以恢复")
    with store.workflow_transaction() as conn:
        cohort["status"] = "published"
        cohort["reopened_at"] = _now()
        cohort["reopened_by"] = user["id"]
        cohort["reopen_reason"] = req.reason.strip()
        _record(store, "cohort", cohort, expected_revision=cohort.get("revision", 0), conn=conn)
        _audit(store, conn, actor_id=user["id"], action="cohort.reopen", resource_type="cohort", resource_id=cohort_id, organization_id=cohort["organization_id"], reason=req.reason)
    return _cohort_view(store, user, cohort)


@router.post("/submission-candidates", status_code=201)
def create_candidate(req: CandidateCreate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    participant, stage, cohort, config = _participant_access(store, user, req.participant_id)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="只有学生可以准备提交")
    if participant.get("state") not in {"in_progress", "needs_revision"}:
        raise HTTPException(status_code=409, detail="当前阶段状态不允许准备提交")
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能创建新的提交")
    if not participant.get("project_id"):
        raise HTTPException(status_code=409, detail="请先绑定主论文")
    if req.modification_note.strip():
        assert_ugc_safe(req.modification_note, field="modification_note")
    if participant.get("current_submission_id"):
        current = store.get_workflow_record("submission", participant["current_submission_id"])
        if current and current.get("state") == "submitted":
            raise _conflict("SUBMISSION_ALREADY_PENDING", "当前已有待审核提交", submission_id=current.get("id"))
    deadline = _parse_time(participant.get("revision_deadline_at") or participant.get("effective_deadline_at", ""), "截止时间")
    if deadline and datetime.now(timezone.utc) > deadline:
        raise _conflict("DEADLINE_PASSED", "已超过服务器确认的有效截止时间")
    project = store.get_project(participant["project_id"])
    if project is None:
        raise HTTPException(status_code=409, detail="主论文不存在")
    content_snapshot = project_snapshot(project)
    content_hash = project_content_hash(project)
    attachment_refs = project_attachment_refs(project)
    version = {
        "id": new_id("version"), "project_id": project.id, "source_revision": project.revision,
        "content_snapshot": content_snapshot, "attachment_refs": attachment_refs, "content_hash": content_hash,
        "created_by": user["id"], "created_reason": "submission_candidate", "immutable": True,
        "revision": 0, "created_at": _now(),
    }
    try:
        template_payload = project.template_snapshot or project.template_source_snapshot
        template = PaperTemplate.model_validate(template_payload)
        checked_project = _project_for_stage_check(project, config)
        pack = (config.get("rule_packs") or template.rule_packs or [None])[0]
        report = check_project(checked_project, template, pack=pack, store=store, performed_by=user["id"])
        check_status = "succeeded"
    except Exception as exc:
        report = {"summary": {"total": 0, "error": 0, "warning": 0, "hard_failures": 1}, "findings": [], "not_checked": [], "error_code": "CHECK_FAILED"}
        check_status = "failed"
        check_error = type(exc).__name__
    missing = validate_project_completeness(project, template) if check_status == "succeeded" else ["检查运行失败"]
    gates = []
    hard_failures = int(report.get("summary", {}).get("hard_failures", 0))
    gates.append({"key": "automatic_checks", "label": "自动检查", "passed": check_status == "succeeded" and hard_failures == 0, "blocking": check_status != "succeeded" or hard_failures > 0, "detail": "存在自动硬性问题" if hard_failures else ""})
    snapshot_hash = _requirements_basis_hash(config, project)
    acknowledged = not snapshot_hash or participant.get("acknowledged_snapshot_hash") == snapshot_hash
    gates.append({"key": "requirements_acknowledged", "label": "已确认当前规范", "passed": acknowledged, "blocking": not acknowledged, "detail": "请先确认本阶段写作要求" if not acknowledged else ""})
    gates.append({"key": "completeness", "label": "必填内容", "passed": not missing, "blocking": bool(missing), "detail": missing})
    material_states = []
    if config.get("required_materials"):
        # 只有过程材料记录进入提交门禁，前端传入的 ID 仅用于选择具体版本，
        # 不能用任意字符串伪造“已提交材料”。
        from app.api.process_records import material_requirement_state
        for requirement in config["required_materials"]:
            material_states.append(material_requirement_state(store, participant["id"], requirement, req.materials))
    material_missing = [item["label"] for item in material_states if not item["passed"]]
    gates.append({"key": "materials", "label": "必交材料", "passed": not material_missing, "blocking": bool(material_missing), "detail": material_missing, "items": material_states})
    missing_attachments = [ref["filename"] for ref in attachment_refs if not ref.get("present")]
    gates.append({"key": "attachments", "label": "正文附件", "passed": not missing_attachments,
                  "blocking": bool(missing_attachments), "detail": missing_attachments})
    prior_id = participant.get("current_submission_id", "") or participant.get("last_submission_id", "") or participant.get("previous_submission_id", "")
    if prior_id:
        open_comments = [item for item in _comment_records(store, prior_id) if item.get("severity") == "blocking" and item.get("status") == "open"]
        gates.append({"key": "comment_responses", "label": "阻断批注已回应", "passed": not open_comments, "blocking": bool(open_comments), "detail": [item["id"] for item in open_comments]})
    candidate = {
        "id": new_id("candidate"), "participant_id": participant["id"], "project_id": project.id,
        "project_version_id": version["id"], "source_revision": project.revision, "content_hash": content_hash,
        "stage_config_id": config["id"], "stage_config_snapshot": config, "check_run_id": report.get("run_id", ""),
        "check_status": check_status, "check_report": report, "materials": req.materials,
        "modification_note": req.modification_note.strip(), "prior_submission_id": prior_id,
        "gates": gates, "warning_count": int(report.get("summary", {}).get("warning", 0)),
        "status": "ready" if not _gate({"gates": gates}) else "blocked", "revision": 0, "created_at": _now(),
    }
    if check_status == "failed":
        candidate["check_error_code"] = "CHECK_FAILED"
    with store.workflow_transaction() as conn:
        _record(store, "project_version", version, conn=conn)
        _record(store, "candidate", candidate, conn=conn)
        _audit(store, conn, actor_id=user["id"], action="submission_candidate.create", resource_type="candidate", resource_id=candidate["id"], organization_id=cohort["organization_id"])
    return candidate


@router.get("/submission-candidates/{candidate_id}")
def get_candidate(candidate_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    candidate = _must(store, "candidate", candidate_id, "提交候选版本")
    participant, _, _, _ = _participant_access(store, user, candidate["participant_id"])
    if user["role"] == "student" and participant["student_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问此提交候选版本")
    return candidate


@router.post("/submissions")
def confirm_submission(req: SubmissionConfirm, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="只有学生可以确认提交")
    request_hash = _hash(req.model_dump(mode="json"))
    existing = store.get_workflow_idempotency(user["id"], "submission.confirm", req.idempotency_key)
    if existing:
        if existing["request_hash"] != request_hash:
            raise HTTPException(status_code=409, detail="幂等键已被其他提交请求使用")
        return existing["response"]
    candidate = _must(store, "candidate", req.candidate_id, "提交候选版本")
    participant, stage, cohort, config = _participant_access(store, user, candidate["participant_id"])
    if candidate.get("status") != "ready":
        raise HTTPException(status_code=409, detail={"message": "提交门禁未满足", "gates": candidate.get("gates", [])})
    if candidate.get("warning_count", 0) and not req.warning_acknowledged:
        raise HTTPException(status_code=409, detail="存在格式警告，请确认后再提交")
    deadline = _parse_time(participant.get("revision_deadline_at") or participant.get("effective_deadline_at", ""), "截止时间")
    if deadline and datetime.now(timezone.utc) > deadline:
        raise _conflict("DEADLINE_PASSED", "已超过服务器确认的有效截止时间")
    with store.workflow_transaction() as conn:
        current_idempotency = store.get_workflow_idempotency(user["id"], "submission.confirm", req.idempotency_key)
        if current_idempotency:
            if current_idempotency["request_hash"] != request_hash:
                raise HTTPException(status_code=409, detail="幂等键已被其他提交请求使用")
            return current_idempotency["response"]
        current_candidate = store.get_workflow_record("candidate", candidate["id"])
        current_participant = store.get_workflow_record("participant", participant["id"])
        if current_candidate.get("status") != "ready" or current_participant.get("revision") != participant.get("revision"):
            raise _conflict("REVISION_CONFLICT", "候选版本或阶段任务已变化，请重新预检")
        current_submission_id = current_participant.get("current_submission_id", "")
        if current_submission_id:
            current_submission = store.get_workflow_record("submission", current_submission_id)
            if current_submission and current_submission.get("state") == "submitted":
                raise _conflict("SUBMISSION_ALREADY_PENDING", "当前已有待审核提交", submission_id=current_submission.get("id"))
        if current_participant.get("state") not in {"in_progress", "needs_revision"}:
            raise HTTPException(status_code=409, detail="当前阶段状态不允许正式提交")
        current_project = store.get_project(current_participant.get("project_id", ""))
        if current_project is None or current_project.revision != candidate.get("source_revision") or project_content_hash(current_project) != candidate.get("content_hash"):
            raise _conflict("CHECK_STALE", "论文正文或写作依据已变化，请重新预检")
        submission = {
            "id": new_id("submission"), "submission_number": f"SUB-{datetime.now(timezone.utc):%Y%m%d}-{new_id('n')[-6:].upper()}",
            "participant_id": participant["id"], "project_version_id": candidate["project_version_id"],
            "candidate_id": candidate["id"], "stage_config_id": candidate["stage_config_id"],
            "stage_config_snapshot": candidate["stage_config_snapshot"], "prior_submission_id": candidate.get("prior_submission_id", ""),
            "state": "submitted", "review_cycle": max(1, participant.get("review_cycle", 0) + 1),
            "submitted_by": user["id"], "submitted_at": _now(), "warning_acknowledged": req.warning_acknowledged,
            "revision": 0, "created_at": _now(),
        }
        _record(store, "submission", submission, conn=conn)
        current_candidate["status"] = "confirmed"
        current_candidate["submission_id"] = submission["id"]
        _record(store, "candidate", current_candidate, expected_revision=current_candidate.get("revision", 0), conn=conn)
        _transition(current_participant, "participant", "submitted")
        current_participant["current_submission_id"] = submission["id"]
        current_participant["last_submission_id"] = submission["id"]
        current_participant["first_submission_at"] = current_participant.get("first_submission_at") or submission["submitted_at"]
        if not current_participant.get("first_submission_late"):
            first_deadline = _parse_time(
                current_participant.get("initial_deadline_at") or current_participant.get("effective_deadline_at", ""),
                "首次截止时间",
            )
            submitted_at = _parse_time(submission["submitted_at"], "提交时间")
            current_participant["first_submission_late"] = bool(first_deadline and submitted_at and submitted_at > first_deadline)
        current_participant["review_cycle"] = submission["review_cycle"]
        _record(store, "participant", current_participant, expected_revision=current_participant.get("revision", 0), conn=conn)
        teacher_id = current_participant.get("teacher_id", "")
        _notify(store, conn, teacher_id, "submission.created", "submission", submission["id"], "有新的论文提交待审核")
        _audit(store, conn, actor_id=user["id"], action="submission.confirm", resource_type="submission", resource_id=submission["id"], organization_id=cohort["organization_id"])
        response = {"submission": submission, "participant": current_participant, "candidate": current_candidate}
        store.save_workflow_idempotency_conn(conn, user_id=user["id"], action="submission.confirm", idem_key=req.idempotency_key, request_hash=request_hash, response=response)
    return response


def _visible_submissions(store: Store, user: dict) -> list[dict]:
    result = []
    for submission in store.list_workflow_records("submission"):
        participant = store.get_workflow_record("participant", submission["participant_id"])
        if not participant:
            continue
        allowed = participant.get("student_id") == user["id"]
        if user["role"] == "teacher":
            relation = _current_supervision(store, participant["cohort_id"], participant["student_id"])
            allowed = bool(relation and relation.get("teacher_id") == user["id"])
        if user["role"] in _MANAGER_ROLES:
            cohort = store.get_workflow_record("cohort", participant["cohort_id"])
            allowed = bool(cohort and _cohort_access(store, user, cohort))
        if allowed:
            result.append(_submission_list_view(store, submission, participant))
    return result


def _submission_list_view(store: Store, submission: dict, participant: dict) -> dict:
    candidate = store.get_workflow_record("candidate", submission.get("candidate_id", "")) if submission.get("candidate_id") else None
    open_blocking = [
        item for item in _comment_records(store, submission["id"])
        if item.get("severity") == "blocking" and item.get("status") != "resolved"
    ]
    return {
        **submission,
        "project_id": participant.get("project_id", ""),
        "modification_note": (candidate or {}).get("modification_note", ""),
        "open_blocking_count": len(open_blocking),
    }


@router.get("/submissions")
def list_submissions(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    return _visible_submissions(store, user)


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    submission = _must(store, "submission", submission_id, "提交")
    submission, participant, stage, config = _submission_access(store, user, submission)
    version = _must(store, "project_version", submission["project_version_id"], "论文版本")
    candidate = store.get_workflow_record("candidate", submission.get("candidate_id", ""))
    return {
        "submission": submission,
        "participant": _task_view(store, participant),
        "stage": stage,
        "config": config,
        "version": version,
        "candidate": candidate,
        "comments": _comment_records(store, submission_id),
        "review": _latest_review(store, submission),
    }


@router.post("/submissions/{submission_id}/withdraw")
def withdraw_submission(submission_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    submission = _must(store, "submission", submission_id, "提交")
    submission, participant, _, _ = _submission_access(store, user, submission)
    if user["role"] != "student" or participant["student_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="只有提交学生可以撤回")
    if submission.get("state") != "submitted":
        raise _conflict("WITHDRAW_NOT_PENDING", "只有待审核提交可以撤回")
    deadline = _parse_time(
        participant.get("revision_deadline_at") or participant.get("effective_deadline_at", ""),
        "截止时间",
    )
    if deadline and datetime.now(timezone.utc) > deadline:
        raise _conflict("DEADLINE_PASSED", "已超过有效提交窗口，不能撤回")
    with store.workflow_transaction() as conn:
        _transition(submission, "submission", "withdrawn")
        submission["withdrawn_at"] = _now()
        _record(store, "submission", submission, expected_revision=submission.get("revision", 0), conn=conn)
        _transition(participant, "participant", "in_progress")
        participant["last_submission_id"] = submission_id or participant.get("last_submission_id", "")
        participant["current_submission_id"] = ""
        _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
        cohort = _must(store, "cohort", participant["cohort_id"], "批次")
        _audit(store, conn, actor_id=user["id"], action="submission.withdraw", resource_type="submission", resource_id=submission_id, organization_id=cohort["organization_id"])
    return submission


@router.post("/submissions/{submission_id}/reviews")
def decide_submission(submission_id: str, req: ReviewDecision, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    submission = _must(store, "submission", submission_id, "提交")
    submission, participant, stage, cohort_config = _submission_access(store, user, submission)
    if user["role"] not in {"teacher", "admin", "college_admin"}:
        raise HTTPException(status_code=403, detail="当前角色不能审核提交")
    if user["role"] == "teacher":
        relation = _current_supervision(store, participant["cohort_id"], participant["student_id"])
        if not relation or relation.get("teacher_id") != user["id"]:
            raise HTTPException(status_code=403, detail="当前教师不是该学生的有效指导教师")
    if submission.get("state") != "submitted":
        raise HTTPException(status_code=409, detail="当前提交已经处理，不能重复审核")
    if req.decision == "returned" and not req.reason.strip():
        raise HTTPException(status_code=422, detail="退回必须填写修改原因")
    if req.decision == "approved":
        required = cohort_config.get("required_manual_checks", [])
        incomplete = [item for item in required if req.manual_confirmations.get(item) is not True]
        open_blocking = [item["id"] for item in _comment_records(store, submission_id) if item.get("severity") == "blocking" and item.get("status") != "resolved"]
        candidate = store.get_workflow_record("candidate", submission.get("candidate_id", ""))
        stale_waivers = _stale_candidate_waiver_ids(store, candidate) if candidate else []
        if incomplete or open_blocking or not candidate or _gate(candidate) or stale_waivers:
            raise HTTPException(status_code=409, detail={"message": "通过门禁未满足", "manual_checks": incomplete, "open_blocking_comments": open_blocking, "gates": candidate.get("gates", []) if candidate else [], "recheck_required": bool(stale_waivers), "stale_waiver_ids": stale_waivers})
    revision_deadline = ""
    if req.decision == "returned":
        existing_deadline = _parse_time(participant.get("effective_deadline_at", ""), "截止时间") or datetime.now(timezone.utc)
        requested_deadline = _parse_time(req.revision_deadline_at, "修订截止时间")
        configured = datetime.now(timezone.utc) + timedelta(days=int(cohort_config.get("revision_days", 7)))
        revision_deadline = max(existing_deadline, requested_deadline or configured).isoformat(timespec="seconds")
    with store.workflow_transaction() as conn:
        review = {
            "id": new_id("review"), "submission_id": submission_id, "review_cycle": submission["review_cycle"],
            "reviewer_id": user["id"], "decision": req.decision, "reason": req.reason.strip(),
            "manual_confirmations": req.manual_confirmations, "created_at": _now(), "revision": 0,
        }
        _record(store, "review", review, conn=conn)
        _transition(submission, "submission", req.decision)
        submission["review_id"] = review["id"]
        _record(store, "submission", submission, expected_revision=submission.get("revision", 0), conn=conn)
        if req.decision == "returned":
            _transition(participant, "participant", "needs_revision")
            participant["revision_deadline_at"] = revision_deadline
        else:
            _transition(participant, "participant", "pending_archive" if cohort_config.get("is_final") else "approved")
            participant["revision_deadline_at"] = ""
        _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
        title = "论文需要修改" if req.decision == "returned" else "论文审核已通过"
        _notify(store, conn, participant["student_id"], f"submission.{req.decision}", "submission", submission_id, title)
        cohort = _must(store, "cohort", participant["cohort_id"], "批次")
        _audit(store, conn, actor_id=user["id"], action=f"submission.{req.decision}", resource_type="submission", resource_id=submission_id, organization_id=cohort["organization_id"], reason=req.reason)
    return {"review": review, "submission": submission, "participant": participant}


@router.post("/submissions/{submission_id}/comments", status_code=201)
def create_comment(submission_id: str, req: CommentCreate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    submission = _must(store, "submission", submission_id, "提交")
    submission, participant, _, _ = _submission_access(store, user, submission)
    if user["role"] not in {"teacher", "admin", "college_admin"}:
        raise HTTPException(status_code=403, detail="只有审核人员可以新增批注")
    assert_ugc_safe(req.body, field="comment")
    comment = {
        "id": new_id("comment"), "submission_id": submission_id, "author_id": user["id"],
        "body": req.body.strip(), "severity": req.severity, "block_id": req.block_id.strip(),
        "quoted_text": req.quoted_text, "status": "open", "responses": [], "revision": 0, "created_at": _now(),
    }
    with store.workflow_transaction() as conn:
        _record(store, "comment", comment, conn=conn)
        _notify(store, conn, participant["student_id"], "comment.created", "submission", submission_id, "教师新增了论文批注")
        cohort = _must(store, "cohort", participant["cohort_id"], "批次")
        _audit(store, conn, actor_id=user["id"], action="comment.create", resource_type="submission", resource_id=submission_id, organization_id=cohort["organization_id"])
    return comment


@router.post("/comments/{comment_id}/response", status_code=201)
def respond_comment(comment_id: str, req: CommentResponse, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    comment = _must(store, "comment", comment_id, "批注")
    submission = _must(store, "submission", comment["submission_id"], "提交")
    submission, participant, _, _ = _submission_access(store, user, submission)
    if user["role"] != "student" or participant["student_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="只有提交学生可以回复批注")
    if comment.get("status") == "resolved":
        raise HTTPException(status_code=409, detail="已解决的批注不能追加本轮回复")
    assert_ugc_safe(req.body, field="comment_response")
    with store.workflow_transaction() as conn:
        comment.setdefault("responses", []).append({"id": new_id("response"), "author_id": user["id"], "body": req.body.strip(), "created_at": _now()})
        comment["status"] = transition_state("comment", str(comment.get("status") or ""), "addressed")
        _record(store, "comment", comment, expected_revision=comment.get("revision", 0), conn=conn)
        _notify(store, conn, submission.get("submitted_by", ""), "comment.responded", "comment", comment_id, "学生已回复论文批注")
        cohort = _must(store, "cohort", participant["cohort_id"], "批次")
        _audit(store, conn, actor_id=user["id"], action="comment.respond", resource_type="comment", resource_id=comment_id, organization_id=cohort["organization_id"])
    return comment


class CommentStatusRequest(BaseModel):
    status: str


class RestoreVersionRequest(BaseModel):
    version_id: str
    base_revision: int | None = None


@router.post("/comments/{comment_id}/status")
def update_comment_status(comment_id: str, req: CommentStatusRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    comment = _must(store, "comment", comment_id, "批注")
    submission = _must(store, "submission", comment["submission_id"], "提交")
    submission, participant, _, _ = _submission_access(store, user, submission)
    if user["role"] not in {"teacher", "admin", "college_admin"}:
        raise HTTPException(status_code=403, detail="只有教师或管理员可以处理批注")
    if req.status not in {"open", "resolved"}:
        raise HTTPException(status_code=422, detail="批注状态只能是 open 或 resolved")
    try:
        comment["status"] = transition_state("comment", str(comment.get("status") or ""), req.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="当前批注状态不允许此操作") from exc
    with store.workflow_transaction() as conn:
        _record(store, "comment", comment, expected_revision=comment.get("revision", 0), conn=conn)
        cohort = _must(store, "cohort", participant["cohort_id"], "批次")
        _audit(store, conn, actor_id=user["id"], action=f"comment.{req.status}", resource_type="comment", resource_id=comment_id, organization_id=cohort["organization_id"])
    return comment


@router.post("/archives", status_code=202)
def create_archive(req: ArchiveCreate,
                   store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    submission = _must(store, "submission", req.submission_id, "提交")
    submission, participant, stage, config = _submission_access(store, user, submission)
    cohort = _must(store, "cohort", participant["cohort_id"], "批次")
    if submission.get("state") != "approved" or not config.get("is_final"):
        raise HTTPException(status_code=409, detail="只有已通过的终稿可以归档")
    archive = {
        "id": new_id("archive"), "submission_id": submission["id"], "participant_id": participant["id"],
        "status": "queued", "manifest_hash": "", "file_path": "", "attempt": 0, "error_code": "",
        "previous_archive_id": participant.get("previous_archive_id", ""),
        "revision": 0, "created_at": _now(),
    }
    archive["job_id"] = new_id("job")
    with store.workflow_transaction() as conn:
        # 重新在 IMMEDIATE 事务内检查，避免两个并发请求都先看到“无归档”
        # 后各自创建一份有效归档。
        existing = [
            item for item in store.list_workflow_records("archive")
            if item.get("submission_id") == submission["id"]
            and item.get("status") in {"queued", "running", "succeeded"}
        ]
        if existing:
            return _archive_view(existing[0])
        _record(store, "archive", archive, conn=conn)
        WorkflowRepository(store).create_job_in_transaction(
            conn, job_id=archive["job_id"], job_type="archive.generate", resource_type="archive",
            resource_id=archive["id"], max_attempts=5,
        )
        _audit(store, conn, actor_id=user["id"], action="archive.create", resource_type="archive", resource_id=archive["id"], organization_id=cohort["organization_id"])
    return _archive_view(archive)


def recover_pending_archives(store: Store) -> None:
    """Requeue durable archive jobs left in-flight by a process restart.

    The standalone worker performs the export; startup only repairs durable
    state so web processes never own a long-running archive task.
    """
    repository = WorkflowRepository(store)
    for archive in repository.list("archive"):
        if archive.get("status") == "running":
            archive["status"] = transition_state("archive", str(archive.get("status") or ""), "queued")
            archive["recovery_reason"] = "process_restart"
            saved = repository.save("archive", archive, expected_revision=archive.get("revision", 0))
            if saved is None:
                continue
            archive = saved
        if archive.get("status") not in {"queued", "failed"}:
            continue
        with store.workflow_transaction() as conn:
            job_id = archive.get("job_id") or new_id("job")
            if not archive.get("job_id"):
                archive["job_id"] = job_id
                _record(store, "archive", archive, expected_revision=archive.get("revision", 0), conn=conn)
            repository = WorkflowRepository(store)
            if repository.get_job_in_transaction(conn, job_id) is None:
                repository.create_job_in_transaction(
                    conn, job_id=job_id, job_type="archive.generate", resource_type="archive",
                    resource_id=archive["id"], max_attempts=5,
                )


def _run_archive(store: Store, archive_id: str, actor_id: str = "") -> dict:
    archive = _must(store, "archive", archive_id, "归档")
    if archive.get("status") not in {"queued", "failed"}:
        return archive
    with store.workflow_transaction() as conn:
        current_status = str(archive.get("status") or "")
        if current_status == "failed":
            current_status = transition_state("archive", current_status, "queued")
        archive["status"] = transition_state("archive", current_status, "running")
        archive["attempt"] = int(archive.get("attempt", 0)) + 1
        _record(store, "archive", archive, expected_revision=archive.get("revision", 0), conn=conn)
    temp_dir = None
    try:
        submission = _must(store, "submission", archive["submission_id"], "提交")
        version = _must(store, "project_version", submission["project_version_id"], "论文版本")
        participant = _must(store, "participant", archive["participant_id"], "阶段任务")
        project = WritingProject.model_validate(version["content_snapshot"])
        attachment_refs = version.get("attachment_refs") or project_attachment_refs(project)
        template = PaperTemplate.model_validate(project.template_snapshot or project.template_source_snapshot)
        archive_dir = _archive_dir()
        archive_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{archive_id}-", dir=str(archive_dir)))
        docx_path = temp_dir / "final.docx"
        source_path = resolve_template_source(template.source_docx) if template.source_docx else None
        export_project_to_docx(project, template, str(docx_path), template_path=source_path)
        candidate = store.get_workflow_record("candidate", submission["candidate_id"])
        reviews = [item for item in store.list_workflow_records("review") if item.get("submission_id") == submission["id"]]
        comments = _comment_records(store, submission["id"])
        process_records = [
            item for item in store.list_workflow_records("process_record")
            if item.get("scope_type") == "participant" and item.get("scope_id") == participant["id"]
        ]
        material_dir = temp_dir / "materials"
        material_dir.mkdir(parents=True, exist_ok=True)
        process_record_payload = []
        material_entries = []
        for record in process_records:
            snapshot = {key: value for key, value in record.items() if key != "attachments"}
            attachments = []
            for attachment in record.get("attachments", []):
                source = Path(attachment.get("storage_path", ""))
                if not source.is_file():
                    raise RuntimeError(f"过程材料附件缺失: {attachment.get('id', '')}")
                expected_hash = str(attachment.get("sha256", ""))
                actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                if expected_hash and actual_hash != expected_hash:
                    raise RuntimeError(f"过程材料附件摘要不匹配: {attachment.get('id', '')}")
                archive_name = f"materials/{attachment['id']}{source.suffix.lower()}"
                target = temp_dir / archive_name
                shutil.copyfile(source, target)
                attachments.append({
                    key: value for key, value in attachment.items()
                    if key not in {"storage_path", "media_type"}
                } | {"archive_name": archive_name})
                material_entries.append((target, archive_name))
            snapshot["attachments"] = attachments
            process_record_payload.append(snapshot)
        asset_dir = temp_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_entries = []
        for ref in attachment_refs:
            source = resolve_project_attachment(ref)
            if source is None or not source.is_file():
                raise RuntimeError(f"正文附件缺失: {ref.get('filename', '')}")
            actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            if actual_hash != ref.get("sha256") or source.stat().st_size != ref.get("size_bytes"):
                raise RuntimeError(f"正文附件摘要不匹配: {ref.get('filename', '')}")
            archive_name = f"assets/{ref['filename']}"
            target = temp_dir / archive_name
            shutil.copyfile(source, target)
            asset_entries.append((target, archive_name))
        template_snapshot = template.model_dump(mode="json")
        # 归档只保留模板结构快照，不把服务器模板源文件路径带出归档包。
        template_snapshot["source_docx"] = ""
        payloads = {
            "content.json": version["content_snapshot"],
            "check-report.json": (candidate or {}).get("check_report", {}),
            "submission.json": submission,
            "template.json": template_snapshot,
            "requirements.json": project.requirement_snapshot or {},
            "stage-requirements.json": submission.get("stage_config_snapshot", {}).get("requirement_snapshot", {}),
            "reviews.json": reviews,
            "comments.json": comments,
            "materials.json": (candidate or {}).get("materials", []),
            "process-records.json": process_record_payload,
        }
        for name, payload in payloads.items():
            (temp_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        bundle_entries = [
            (docx_path, "final.docx"),
            *asset_entries,
            *material_entries,
            *((temp_dir / name, name) for name in payloads),
        ]
        file_entries = [
            {"name": archive_name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}
            for path, archive_name in bundle_entries
        ]
        manifest = {
            "archive_id": archive_id, "submission_id": submission["id"], "submission_number": submission["submission_number"],
            "project_version_id": version["id"], "content_hash": version["content_hash"],
            "stage_config_id": submission["stage_config_id"], "generated_at": _now(),
            "template_hash": _hash(template_snapshot),
            "requirements_hash": _hash(project.requirement_snapshot or {}),
            "review_ids": [item["id"] for item in reviews],
            "material_refs": (candidate or {}).get("materials", []),
            "process_record_ids": [item["id"] for item in process_records],
            "attachment_refs": attachment_refs,
            "files": file_entries,
        }
        manifest_path = temp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        output = archive_dir / f"{archive_id}.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path, archive_name in (*bundle_entries, (manifest_path, "manifest.json")):
                bundle.write(path, archive_name)
        archive["status"] = transition_state("archive", str(archive.get("status") or ""), "succeeded")
        archive["manifest_hash"] = _hash(manifest)
        archive["file_path"] = str(output)
        archive["manifest"] = manifest
        archive["completed_at"] = _now()
        with store.workflow_transaction() as conn:
            _record(store, "archive", archive, expected_revision=archive.get("revision", 0), conn=conn)
            participant = store.get_workflow_record("participant", archive["participant_id"])
            cohort = store.get_workflow_record("cohort", participant["cohort_id"])
            _transition(participant, "participant", "archived")
            participant["archive_id"] = archive["id"]
            _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
            _notify(store, conn, participant["student_id"], "archive.succeeded", "archive", archive_id, "终稿归档已完成")
            _audit(store, conn, actor_id=actor_id, action="archive.succeeded", resource_type="archive", resource_id=archive_id, organization_id=cohort["organization_id"])
        return archive
    except Exception:
        archive["status"] = transition_state("archive", str(archive.get("status") or ""), "failed")
        archive["error_code"] = "ARCHIVE_GENERATION_FAILED"
        archive["failed_at"] = _now()
        store.save_workflow_record("archive", archive, expected_revision=archive.get("revision", 0))
        return archive
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


@router.get("/archives")
def list_archives(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    result = []
    for archive in store.list_workflow_records("archive"):
        participant = store.get_workflow_record("participant", archive.get("participant_id", ""))
        if not participant:
            continue
        if _participant_visible(store, user, participant):
            result.append(_archive_view(archive))
    return result


@router.get("/archives/{archive_id}")
def get_archive(archive_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    archive = _must(store, "archive", archive_id, "归档")
    participant = _must(store, "participant", archive["participant_id"], "阶段任务")
    if not _participant_visible(store, user, participant):
        raise HTTPException(status_code=404, detail="归档不存在或无权访问")
    return _archive_view(archive)


@router.post("/archives/{archive_id}/retry")
def retry_archive(archive_id: str, req: RetryRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    archive = _must(store, "archive", archive_id, "归档")
    participant = _must(store, "participant", archive["participant_id"], "阶段任务")
    cohort = _ensure_cohort_access(store, user, participant["cohort_id"])
    if archive.get("status") != "failed":
        raise HTTPException(status_code=409, detail="只有失败归档可以重试")
    archive["retry_reason"] = req.reason.strip()
    with store.workflow_transaction() as conn:
        job_id = archive.get("job_id") or new_id("job")
        archive["job_id"] = job_id
        archive["status"] = transition_state("archive", str(archive.get("status") or ""), "queued")
        archive["error_code"] = ""
        _record(store, "archive", archive, expected_revision=archive.get("revision", 0), conn=conn)
        repository = WorkflowRepository(store)
        job = repository.get_job_in_transaction(conn, job_id)
        if job is None:
            repository.create_job_in_transaction(
                conn, job_id=job_id, job_type="archive.generate", resource_type="archive",
                resource_id=archive_id, max_attempts=5,
            )
        else:
            repository.retry_job_in_transaction(conn, job_id, req.reason.strip())
    return _archive_view(archive)


@router.get("/archives/{archive_id}/download")
def download_archive(archive_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)):
    archive = _must(store, "archive", archive_id, "归档")
    participant = _must(store, "participant", archive["participant_id"], "阶段任务")
    assert_resource_read(store, user, "archive", archive)
    if archive.get("status") != "succeeded" or not archive.get("file_path"):
        raise HTTPException(status_code=409, detail="归档尚未生成完成")
    path = Path(archive["file_path"])
    if path.parent != _archive_dir() or not path.is_file():
        raise HTTPException(status_code=404, detail="归档制品不存在")
    return FileResponse(path, filename=f"{archive_id}.zip", media_type="application/zip", headers={"X-PaperGuard-Manifest-Hash": archive.get("manifest_hash", "")})


def _job_visible(store: Store, user: dict, job: dict) -> bool:
    if job.get("resource_type") == "project_export":
        record = store.get_workflow_record("project_export", job.get("resource_id", ""))
        project = store.get_project((record or {}).get("project_id", ""))
        from app.api.projects import _project_accessible
        return bool(project and not project.deleted_at and _project_accessible(project, user, store))
    if job.get("resource_type") != "archive":
        return user.get("role") in _MANAGER_ROLES
    archive = store.get_workflow_record("archive", job.get("resource_id", ""))
    participant = store.get_workflow_record("participant", archive.get("participant_id", "")) if archive else None
    return bool(participant and _participant_visible(store, user, participant))


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    """Query a durable background job without exposing storage internals."""
    job = store.get_job(job_id)
    if not job or not _job_visible(store, user, job):
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    result = dict(job)
    if job.get("resource_type") == "archive":
        archive = store.get_workflow_record("archive", job.get("resource_id", ""))
        if archive:
            result["archive"] = _archive_view(archive)
    elif job.get("resource_type") == "project_export":
        record = store.get_workflow_record("project_export", job.get("resource_id", ""))
        if record:
            from app.services.export_jobs import export_job_view
            result["export"] = export_job_view(record)
    return result


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    """List durable jobs visible to the current manager or archive owner."""
    jobs = store.list_jobs(limit=500)
    return [get_job(job["id"], store=store, user=user) for job in jobs if _job_visible(store, user, job)]


@router.get("/notifications")
def notifications(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    items = store.list_workflow_events(user["id"])
    return {"items": items, "unread": sum(1 for item in items if item.get("read_at") is None)}


@router.post("/notifications/{event_id}/read")
def mark_notification_read(event_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    if not store.mark_workflow_event_read(user["id"], event_id):
        raise HTTPException(status_code=404, detail="消息不存在或无权访问")
    return {"ok": True}


@router.get("/reports/cohorts/{cohort_id}")
def cohort_report(cohort_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    participants = [item for item in store.list_workflow_records("participant") if item.get("cohort_id") == cohort_id]
    submissions = [item for item in store.list_workflow_records("submission") if any(p.get("id") == item.get("participant_id") for p in participants)]
    counts = {state: sum(1 for item in participants if item.get("state") == state) for state in ("not_started", "in_progress", "submitted", "needs_revision", "approved", "pending_archive", "archived", "exited")}
    first_submissions = [item for item in submissions if int(item.get("review_cycle", 0)) == 1]
    on_time = 0
    for submission in first_submissions:
        participant = next((item for item in participants if item.get("id") == submission.get("participant_id")), None)
        if participant:
            deadline = _parse_time(participant.get("initial_deadline_at", ""), "首次截止时间")
            submitted_at = _parse_time(submission.get("submitted_at", ""), "提交时间")
            if deadline and submitted_at and submitted_at <= deadline:
                on_time += 1
    reviews = [item for item in store.list_workflow_records("review") if any(item.get("submission_id") == submission.get("id") for submission in submissions)]
    review_waits = []
    for submission in first_submissions:
        decision = next((item for item in reviews if item.get("submission_id") == submission.get("id")), None)
        if decision:
            started = _parse_time(submission.get("submitted_at", ""), "提交时间")
            finished = _parse_time(decision.get("created_at", ""), "审核时间")
            if started and finished:
                review_waits.append(max(0, int((finished - started).total_seconds())))
    archive_records = [item for item in store.list_workflow_records("archive") if item.get("status") == "succeeded" and any(p.get("current_submission_id") == item.get("submission_id") for p in participants)]
    member_snapshot = set(cohort.get("member_ids", []))
    student_states: dict[str, set[str]] = {}
    for participant in participants:
        student_states.setdefault(participant.get("student_id", ""), set()).add(participant.get("state", ""))
    excluded_student_ids = {
        student_id for student_id, states in student_states.items()
        if student_id and states and states <= {"exited", "withdrawn"}
    }
    denominator = len(member_snapshot - excluded_student_ids)
    return {
        "cohort_id": cohort_id, "cohort_name": cohort["name"], "generated_at": _now(),
        "member_count": denominator, "participant_count": len(participants),
        "state_counts": counts, "late_count": sum(1 for item in participants if _late(item)),
        "pending_review_count": sum(1 for item in submissions if item.get("state") == "submitted"),
        "archive_count": len(archive_records),
        "metrics": {
            "first_submission_count": len(first_submissions),
            "on_time_first_submission_rate": round(on_time / denominator, 4) if denominator else None,
            "average_review_wait_seconds": round(sum(review_waits) / len(review_waits), 1) if review_waits else None,
            "revision_submission_count": sum(1 for item in submissions if int(item.get("review_cycle", 0)) > 1),
            "archive_completion_rate": round(len(archive_records) / denominator, 4) if denominator else None,
            "denominator": {
                "member_snapshot_count": len(member_snapshot),
                "eligible_member_count": denominator,
                "excluded_student_count": len(excluded_student_ids),
                "excluded_states": ["exited", "withdrawn"],
            },
        },
    }


@router.post("/notifications/reminders/run")
def run_due_reminders(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    """Create durable, deduplicated in-app reminders for the current snapshot."""
    _ensure_manager(user)
    created: list[str] = []
    now = datetime.now(timezone.utc)
    for participant in store.list_workflow_records("participant"):
        cohort = store.get_workflow_record("cohort", participant.get("cohort_id", ""))
        if not cohort or not _cohort_access(store, user, cohort) or participant.get("state") not in _ACTIVE_STUDENT_STATES:
            continue
        deadline = _parse_time(participant.get("revision_deadline_at") or participant.get("effective_deadline_at", ""), "截止时间")
        if not deadline:
            continue
        remaining = (deadline - now).total_seconds()
        if remaining > 72 * 3600:
            continue
        kind = "overdue" if remaining < 0 else "deadline_soon"
        dedupe_key = f"reminder:{kind}:{participant['id']}:{participant.get('revision', 0)}:{deadline.isoformat()}"
        with store.workflow_transaction() as conn:
            inserted = _notify(store, conn, participant["student_id"], f"participant.{kind}", "participant", participant["id"], "阶段任务已逾期，请尽快处理" if kind == "overdue" else "阶段任务即将截止，请及时提交", dedupe_key=dedupe_key)
            if inserted:
                created.append(participant["id"])
            relation = _current_supervision(store, participant["cohort_id"], participant["student_id"])
            if relation:
                _notify(store, conn, relation["teacher_id"], f"participant.{kind}", "participant", participant["id"], "指导学生任务临近截止" if kind == "deadline_soon" else "指导学生任务已逾期", dedupe_key=f"{dedupe_key}:teacher")
    return {"created": created, "generated_at": _now()}


@router.get("/outbox")
def workflow_outbox(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    """Expose the durable notification queue to operators without message bodies."""
    _ensure_manager(user)
    return [
        {key: value for key, value in event.items() if key not in {"data"}}
        | {"title": (event.get("data") or {}).get("title", "")}
        for event in store.list_pending_workflow_events()
        if _event_visible_to_manager(store, user, event)
    ]


@router.post("/outbox/claim")
def claim_outbox(req: OutboxClaimRequest, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    """Claim visible events with a lease so an external consumer can deliver them safely."""
    _ensure_manager(user)
    consumer_id = req.consumer_id or f"manager:{user['id']}"
    visible = [
        event for event in store.list_pending_workflow_events(limit=1000)
        if _event_visible_to_manager(store, user, event)
    ]
    claimed = store.claim_workflow_events(
        [event["id"] for event in visible[:req.limit]],
        consumer_id=consumer_id,
        lease_seconds=req.lease_seconds,
        max_attempts=_OUTBOX_MAX_ATTEMPTS,
    )
    return [
        {key: value for key, value in event.items() if key != "data"}
        | {"title": (event.get("data") or {}).get("title", "")}
        for event in claimed
    ]


@router.post("/outbox/{event_id}/ack")
def acknowledge_outbox(event_id: str, req: OutboxAckRequest | None = None,
                       store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    """Mark an outbox item delivered after the notification consumer accepts it."""
    _ensure_manager(user)
    pending = next((item for item in store.list_pending_workflow_events() if item.get("id") == event_id), None)
    if pending is None:
        raise HTTPException(status_code=404, detail="出站事件不存在或已完成")
    if not _event_visible_to_manager(store, user, pending):
        raise HTTPException(status_code=404, detail="出站事件不存在或无权访问")
    lease_token = req.lease_token if req else ""
    if not store.mark_workflow_event_delivered_with_lease(event_id, lease_token):
        raise HTTPException(status_code=409, detail="出站事件租约无效或已被其他消费者确认")
    return {"ok": True, "event_id": event_id, "delivery_status": "delivered"}


@router.post("/outbox/{event_id}/fail")
def fail_outbox(event_id: str, req: OutboxFailureRequest,
                store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    """Release a failed delivery for retry, or park it after the retry ceiling."""
    _ensure_manager(user)
    pending = next((item for item in store.list_pending_workflow_events() if item.get("id") == event_id), None)
    if pending is None or not _event_visible_to_manager(store, user, pending):
        raise HTTPException(status_code=404, detail="出站事件不存在或无权访问")
    failed = store.fail_workflow_event(
        event_id, error=req.error, lease_token=req.lease_token, max_attempts=_OUTBOX_MAX_ATTEMPTS
    )
    if failed is None:
        raise HTTPException(status_code=409, detail="出站事件租约无效或已被其他消费者确认")
    return {
        "ok": True,
        "event_id": event_id,
        "delivery_status": failed["delivery_status"],
        "attempt_count": failed["attempt_count"],
        "last_error": failed["last_error"],
    }


@router.post("/outbox/{event_id}/retry")
def retry_outbox(event_id: str, req: RetryRequest | None = None,
                 store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    """Put a parked event back into the queue after explicit operator review."""
    _ensure_manager(user)
    pending = next((item for item in store.list_pending_workflow_events() if item.get("id") == event_id), None)
    if pending is None or not _event_visible_to_manager(store, user, pending):
        raise HTTPException(status_code=404, detail="出站事件不存在或无权访问")
    retried = store.retry_workflow_event(event_id, (req.reason if req else ""))
    if retried is None:
        raise HTTPException(status_code=409, detail="只有达到重试上限的出站事件可以人工重试")
    return {
        "ok": True,
        "event_id": event_id,
        "delivery_status": retried["delivery_status"],
        "attempt_count": retried["attempt_count"],
        "last_error": retried["last_error"],
    }


@router.get("/audit-events")
def audit_events(
    cohort_id: str = "",
    resource_type: str = "",
    action: str = "",
    actor_id: str = "",
    organization_id: str = "",
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict]:
    _ensure_manager(user)
    result = list_audit_events(
        store,
        user,
        limit=500,
        resource_type=resource_type.strip(),
        action=action.strip(),
        actor_id=actor_id.strip(),
        organization_id=organization_id.strip(),
    )
    if cohort_id:
        result = [event for event in result if event.get("resource_id") == cohort_id]
    return result
