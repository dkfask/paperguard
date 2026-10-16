"""毕业设计过程材料、附件和逐级审批 API。

过程材料是任务/提交/归档之间的证据层。每条记录只允许在所属学生或学院
范围内访问，审批历史追加保存，附件以摘要和受控下载地址引用。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.templates import get_store
from app.api.workflow import (
    _MANAGER_ROLES,
    _audit,
    _ensure_cohort_access,
    _ensure_manager,
    _must,
    _notify,
    _now,
    _participant_access,
    _record,
)
from app.auth import current_user
from app.db import Store, new_id
from app.domain.workflow_states import transition_state
from app.models.process_record import (
    ARTIFACT_BY_TYPE,
    MANAGEMENT_ARTIFACT_CATALOG,
    MANAGEMENT_FORM_SCHEMAS,
    PROCESS_ARTIFACT_CATALOG,
    ProcessDecision,
    ProcessRecordCreate,
    ProcessRecordUpdate,
    PROCESS_FORM_SCHEMAS,
    REFERENCE_DOCUMENT_CATALOG,
    canonical_artifact_type,
    artifact_form_schema,
    catalog_item,
)
from app.services.process_artifacts import (
    prefill_artifact_data,
    render_artifact_docx,
    render_artifact_xlsx,
    validate_artifact_data,
)
from app.services.upload_security import IMPORT_MAX_BYTES, read_limited, validate_docx_bytes
from app.services.resource_access import assert_resource_read

router = APIRouter(prefix="/api", tags=["process-records"])

_STUDENT_WRITABLE = {
    "task_book", "opening_report", "guidance_log", "final_report", "result_package",
    "similarity_report", "defense_presentation", "defense_application", "internship_identification",
    "excellence_application", "topic_change", "extension_application", "process_cover",
}
_READY_STATES = {"submitted", "approved"}
_ALLOWED_SUFFIXES = {".doc", ".docx", ".pdf", ".zip", ".pptx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".txt"}


def _process_file_dir() -> Path:
    value = os.environ.get("PAPERGUARD_PROCESS_FILE_DIR", "")
    path = Path(value) if value else Path(os.environ.get(
        "PAPERGUARD_DATA_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "data"),
    )) / "process-files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _canonical_or_422(value: str) -> tuple[str, dict[str, Any]]:
    artifact_type = canonical_artifact_type(value)
    item = catalog_item(artifact_type)
    if item is None:
        raise HTTPException(status_code=422, detail=f"未知过程材料类型: {value}")
    return artifact_type, item


def _record_view(record: dict) -> dict:
    result = dict(record)
    item = catalog_item(record.get("artifact_type", ""))
    result["artifact_name"] = item["name"] if item else record.get("artifact_type", "")
    result["required_approvals"] = item["required_approvals"] if item else []
    attachments = []
    for attachment in record.get("attachments", []):
        public = {key: value for key, value in attachment.items() if key != "storage_path"}
        public["download_url"] = f"/api/process-files/{attachment['id']}/download"
        attachments.append(public)
    result["attachments"] = attachments
    return result


def _records_for_participant(store: Store, participant_id: str) -> list[dict]:
    return [
        item for item in store.list_workflow_records("process_record")
        if item.get("scope_type") == "participant" and item.get("scope_id") == participant_id
    ]


def _records_for_cohort(store: Store, cohort_id: str) -> list[dict]:
    return [
        item for item in store.list_workflow_records("process_record")
        if item.get("scope_type") == "cohort" and item.get("scope_id") == cohort_id
    ]


def _participant_context(store: Store, user: dict, participant_id: str) -> tuple[dict, dict, dict, dict]:
    return _participant_access(store, user, participant_id)


def _can_edit_participant(user: dict, participant: dict) -> bool:
    return user["role"] in _MANAGER_ROLES or user["role"] == "student" and participant["student_id"] == user["id"] or user["role"] == "teacher" and participant.get("teacher_id") == user["id"]


def _ensure_record_access(store: Store, user: dict, record: dict) -> tuple[dict | None, dict | None]:
    if record.get("scope_type") == "participant":
        participant, stage, cohort, _ = _participant_context(store, user, record["scope_id"])
        return participant, cohort
    if record.get("scope_type") == "cohort":
        cohort = _ensure_cohort_access(store, user, record["scope_id"])
        if user["role"] not in _MANAGER_ROLES:
            raise HTTPException(status_code=403, detail="学院管理材料仅管理员可访问")
        return None, cohort
    raise HTTPException(status_code=404, detail="材料记录不存在")


def _ensure_record_editor(store: Store, user: dict, record: dict) -> tuple[dict | None, dict]:
    participant, cohort = _ensure_record_access(store, user, record)
    if cohort.get("status") == "closed" and user["role"] == "student":
        raise HTTPException(status_code=409, detail="批次已关闭，不能新增或提交材料")
    if participant is not None and not _can_edit_participant(user, participant):
        raise HTTPException(status_code=403, detail="无权编辑该学生的过程材料")
    if user["role"] == "student" and record.get("artifact_type") not in _STUDENT_WRITABLE:
        raise HTTPException(status_code=403, detail="学生不能直接修改该类材料")
    _ensure_student_material_write(store, user, participant)
    if record.get("state") not in {"draft", "returned"}:
        raise HTTPException(status_code=409, detail="当前材料已提交或审批完成，不能直接修改")
    return participant, cohort


def _ensure_student_material_write(store: Store, user: dict, participant: dict | None, config: dict | None = None) -> None:
    if user.get("role") != "student" or participant is None:
        return
    state = participant.get("state")
    if state in {"in_progress", "needs_revision"}:
        return
    if state in {"submitted", "approved", "pending_archive", "archived", "withdrawn", "exited"}:
        raise HTTPException(status_code=409, detail="当前阶段已提交或冻结，不能继续编辑过程材料")
    stage = store.get_workflow_record("stage", participant.get("stage_id", ""))
    if config is None:
        config = store.get_workflow_record(
            "stage_config",
            (participant or {}).get("config_version_id") or (stage or {}).get("config_version_id", ""),
        ) or {}
    opened = _parse_workflow_time(config.get("open_at", ""))
    if config.get("allow_early_write") or (opened and datetime.now(timezone.utc) >= opened and _workflow_prerequisites_satisfied(store, participant, config)):
        return
    raise HTTPException(status_code=409, detail="当前阶段尚未开放或前置阶段未完成，暂不能编辑过程材料")


def _transition_record(record: dict, target: str) -> None:
    try:
        record["state"] = transition_state("process_record", str(record.get("state") or ""), target)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="当前材料状态不允许此操作") from exc


def _parse_workflow_time(value: str):
    from app.api.workflow import _parse_time
    return _parse_time(value, "开放时间")


def _workflow_prerequisites_satisfied(store: Store, participant: dict, config: dict) -> bool:
    from app.api.workflow import _prerequisites_satisfied
    return _prerequisites_satisfied(store, participant, config)


def _validate_submission(record: dict) -> None:
    if not record.get("data") and not record.get("attachments"):
        raise HTTPException(status_code=422, detail="材料至少需要填写结构化内容或上传附件")
    artifact_type = record.get("artifact_type")
    data = record.get("data") or {}
    if artifact_type == "topic_change":
        new_topic = data.get("new_topic")
        if not isinstance(new_topic, str) or not new_topic.strip():
            raise HTTPException(status_code=422, detail="课题变更必须填写变更后课题名称")
    if artifact_type == "similarity_report":
        raw = data.get("copy_ratio", data.get("R"))
        try:
            ratio = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="相似性报告必须填写 0-100 的文字复制比") from exc
        if ratio < 0 or ratio > 100:
            raise HTTPException(status_code=422, detail="文字复制比必须在 0-100 之间")
        data["copy_ratio"] = ratio
        data["classification"] = "A" if ratio < 40 else "B" if ratio <= 60 else "C"
        data["requires_second_round"] = 40 <= ratio <= 60
        record["qualification_block"] = ratio > 60
    if artifact_type == "excellence_application":
        raw = data.get("copy_ratio", data.get("R"))
        try:
            ratio = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="优秀成果申请必须填写文字复制比") from exc
        if ratio > 15:
            raise HTTPException(status_code=422, detail="优秀成果申请要求文字复制比不超过 15%")
    if artifact_type == "defense_application":
        absence = data.get("absence_count")
        total = data.get("total_sessions")
        if absence is not None and total:
            try:
                if float(absence) / float(total) >= 1 / 3:
                    raise HTTPException(status_code=409, detail="缺席答辩相关活动达到三分之一，不能申请答辩")
            except ZeroDivisionError:
                raise HTTPException(status_code=422, detail="答辩活动总次数必须大于 0")
    if artifact_type == "defense_record":
        members = data.get("committee_members")
        if not isinstance(members, list) or len(members) < 3:
            raise HTTPException(status_code=422, detail="答辩小组至少需要 3 名成员")
    for score_type in {"advisor_score", "reviewer_score", "defense_score", "comprehensive_score"}:
        if artifact_type != score_type:
            continue
        raw = data.get("score", data.get("total_score"))
        try:
            score = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"{catalog_item(artifact_type)['name']}必须填写成绩") from exc
        if score < 0 or score > 100:
            raise HTTPException(status_code=422, detail="成绩必须在 0-100 之间")
        data["score"] = score


def _next_approval(record: dict, item: dict) -> str | None:
    approved = {entry.get("role") for entry in record.get("approval_history", []) if entry.get("decision") == "approved"}
    return next((role for role in item["required_approvals"] if role not in approved), None)


def _role_can_approve(user: dict, required_role: str, participant: dict | None) -> bool:
    role = user["role"]
    if required_role == "admin":
        return role == "admin"
    if required_role == "college_admin":
        return role in {"admin", "college_admin"}
    if required_role == "teacher":
        return role in {"admin", "college_admin"} or (role == "teacher" and participant and participant.get("teacher_id") == user["id"])
    if required_role == "defense_group":
        # 制度要求指导教师回避本人学生的答辩；管理员可代为登记答辩组结论。
        return role in {"admin", "college_admin"} or (
            role == "teacher" and participant and participant.get("teacher_id") != user["id"]
        )
    return False


def _notify_record_owner(store: Store, conn, record: dict, title: str) -> None:
    if record.get("scope_type") == "participant":
        participant = store.get_workflow_record("participant", record["scope_id"])
        if participant:
            _notify(store, conn, participant["student_id"], "process_record.updated", "process_record", record["id"], title)


@router.get("/process-records/catalog")
def list_catalog(_: dict = Depends(current_user)) -> dict:
    return {
        "participant": [dict(item) | {"form_schema": artifact_form_schema(item["artifact_type"])} for item in PROCESS_ARTIFACT_CATALOG],
        "cohort": [dict(item) | {"form_schema": artifact_form_schema(item["artifact_type"])} for item in MANAGEMENT_ARTIFACT_CATALOG],
        "reference": [dict(item) for item in REFERENCE_DOCUMENT_CATALOG],
    }


@router.get("/process-records/form-schemas")
def list_form_schemas(_: dict = Depends(current_user)) -> dict[str, Any]:
    return {key: artifact_form_schema(key) for key in {**PROCESS_FORM_SCHEMAS, **MANAGEMENT_FORM_SCHEMAS}}


@router.get("/stage-participants/{participant_id}/process-records/form/{artifact_type}")
def process_record_form(artifact_type: str, participant_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    participant, _, cohort, _ = _participant_context(store, user, participant_id)
    canonical, item = _canonical_or_422(artifact_type)
    if item["scope"] != "participant":
        raise HTTPException(status_code=422, detail="该材料必须挂在学院批次上")
    project = store.get_project(participant.get("project_id")) if participant.get("project_id") else None
    return {
        "artifact_type": canonical,
        "schema": artifact_form_schema(canonical),
        "prefill": prefill_artifact_data(canonical, user=store.get_user(participant["student_id"]) or user,
                                          participant=participant, cohort=cohort, project=project),
    }


@router.get("/cohorts/{cohort_id}/process-records/form/{artifact_type}")
def cohort_process_record_form(artifact_type: str, cohort_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    _ensure_manager(user)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    canonical, item = _canonical_or_422(artifact_type)
    if item["scope"] != "cohort":
        raise HTTPException(status_code=422, detail="该材料必须挂在学生阶段任务上")
    return {"artifact_type": canonical, "schema": artifact_form_schema(canonical),
            "prefill": {"generated_by": user.get("display_name") or user.get("username", ""), "rows": [], "cohort_id": cohort_id}}


@router.get("/stage-participants/{participant_id}/process-records")
def list_participant_records(participant_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    _participant_context(store, user, participant_id)
    return [_record_view(item) for item in _records_for_participant(store, participant_id)]


@router.post("/stage-participants/{participant_id}/process-records", status_code=201)
def create_participant_record(participant_id: str, req: ProcessRecordCreate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    participant, _, cohort, config = _participant_context(store, user, participant_id)
    if cohort.get("status") == "closed" and user["role"] == "student":
        raise HTTPException(status_code=409, detail="批次已关闭，不能新增材料")
    artifact_type, item = _canonical_or_422(req.artifact_type)
    if item["scope"] != "participant":
        raise HTTPException(status_code=422, detail="该材料必须挂在学院批次上")
    if user["role"] == "student" and artifact_type not in _STUDENT_WRITABLE:
        raise HTTPException(status_code=403, detail="学生不能直接创建该类材料")
    _ensure_student_material_write(store, user, participant, config)
    data = req.data
    if req.structured_form:
        try:
            data = validate_artifact_data(artifact_type, req.data, allow_partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    record = {
        "id": new_id("record"), "scope_type": "participant", "scope_id": participant_id,
        "participant_id": participant_id, "cohort_id": cohort["id"], "student_id": participant["student_id"],
        "artifact_type": artifact_type, "data": data, "form_schema": artifact_form_schema(artifact_type),
        "structured_form": req.structured_form, "note": req.note.strip(),
        "state": "draft", "version": 1, "approval_history": [], "attachments": [],
        "created_by": user["id"], "created_at": _now(), "revision": 0,
    }
    with store.workflow_transaction() as conn:
        _record(store, "process_record", record, conn=conn)
        _audit(store, conn, actor_id=user["id"], action="process_record.create", resource_type="process_record", resource_id=record["id"], organization_id=cohort["organization_id"])
    return _record_view(record)


@router.get("/cohorts/{cohort_id}/process-records")
def list_cohort_records(cohort_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    _ensure_manager(user)
    _ensure_cohort_access(store, user, cohort_id)
    return [_record_view(item) for item in _records_for_cohort(store, cohort_id)]


@router.post("/cohorts/{cohort_id}/process-records", status_code=201)
def create_cohort_record(cohort_id: str, req: ProcessRecordCreate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _ensure_manager(user)
    cohort = _ensure_cohort_access(store, user, cohort_id)
    if cohort.get("status") == "closed":
        raise HTTPException(status_code=409, detail="批次已关闭，不能新增学院管理材料")
    artifact_type, item = _canonical_or_422(req.artifact_type)
    if item["scope"] != "cohort":
        raise HTTPException(status_code=422, detail="该材料必须挂在学生阶段任务上")
    data = req.data
    if req.structured_form:
        try:
            data = validate_artifact_data(artifact_type, req.data, allow_partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    record = {
        "id": new_id("record"), "scope_type": "cohort", "scope_id": cohort_id,
        "cohort_id": cohort_id, "artifact_type": artifact_type, "data": data,
        "form_schema": artifact_form_schema(artifact_type), "structured_form": req.structured_form,
        "note": req.note.strip(), "state": "draft", "version": 1,
        "approval_history": [], "attachments": [], "created_by": user["id"],
        "created_at": _now(), "revision": 0,
    }
    with store.workflow_transaction() as conn:
        _record(store, "process_record", record, conn=conn)
        _audit(store, conn, actor_id=user["id"], action="process_record.create", resource_type="process_record", resource_id=record["id"], organization_id=cohort["organization_id"])
    return _record_view(record)


@router.get("/process-records/inbox")
def process_record_inbox(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    if user["role"] not in {"teacher", "admin", "college_admin"}:
        raise HTTPException(status_code=403, detail="只有教师或管理员可以查看材料审批收件箱")
    result = []
    for record in store.list_workflow_records("process_record"):
        if record.get("state") != "submitted":
            continue
        try:
            participant, _ = _ensure_record_access(store, user, record)
        except HTTPException:
            continue
        item = catalog_item(record.get("artifact_type", ""))
        required = _next_approval(record, item or {"required_approvals": []})
        if required and _role_can_approve(user, required, participant):
            result.append(_record_view(record))
    return result


@router.get("/process-records/{record_id}")
def get_record(record_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    record = _must(store, "process_record", record_id, "过程材料")
    _ensure_record_access(store, user, record)
    return _record_view(record)


@router.put("/process-records/{record_id}")
def update_record(record_id: str, req: ProcessRecordUpdate, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    record = _must(store, "process_record", record_id, "过程材料")
    participant, cohort = _ensure_record_editor(store, user, record)
    if req.base_revision is not None and req.base_revision != record.get("revision"):
        raise HTTPException(status_code=409, detail={"message": "材料已被更新，请刷新后重试", "revision": record.get("revision")})
    record.setdefault("history", []).append({
        "version": record.get("version", 1), "data": record.get("data", {}),
        "note": record.get("note", ""), "saved_at": _now(), "saved_by": user["id"],
    })
    record["version"] = int(record.get("version", 1)) + 1
    record["structured_form"] = bool(record.get("structured_form") or req.structured_form)
    if record["structured_form"]:
        try:
            record["data"] = validate_artifact_data(record["artifact_type"], req.data, allow_partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        record["data"] = req.data
    record["note"] = req.note.strip()
    expected = record.get("revision", 0)
    with store.workflow_transaction() as conn:
        _record(store, "process_record", record, expected_revision=expected, conn=conn)
        _audit(store, conn, actor_id=user["id"], action="process_record.update", resource_type="process_record", resource_id=record_id, organization_id=cohort["organization_id"])
    return _record_view(record)


@router.post("/process-records/{record_id}/submit")
def submit_record(record_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    record = _must(store, "process_record", record_id, "过程材料")
    participant, cohort = _ensure_record_editor(store, user, record)
    _validate_submission(record)
    if record.get("structured_form"):
        try:
            record["data"] = validate_artifact_data(record["artifact_type"], record.get("data", {}))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record["artifact_type"] == "defense_application" and participant:
        qualification = participant_qualification(store, participant["id"])
        if not qualification["eligible_for_application"]:
            raise HTTPException(status_code=409, detail={
                "message": "答辩申请尚未满足资格门禁", "gates": qualification["gates"],
            })
    if record["artifact_type"] == "defense_record" and participant:
        members = (record.get("data") or {}).get("committee_members") or []
        member_ids = {str(member.get("teacher_id") or member.get("id")) if isinstance(member, dict) else str(member) for member in members}
        advisor = str(participant.get("teacher_id", ""))
        if advisor and advisor in member_ids:
            raise HTTPException(status_code=409, detail="指导教师不得进入本人学生的答辩小组")
    item = catalog_item(record["artifact_type"])
    _transition_record(record, "submitted")
    record["submitted_by"] = user["id"]
    record["submitted_at"] = _now()
    record["pending_approvals"] = list(item["required_approvals"] if item else [])
    with store.workflow_transaction() as conn:
        _record(store, "process_record", record, expected_revision=record.get("revision", 0), conn=conn)
        _notify_record_owner(store, conn, record, f"{item['name'] if item else '过程材料'}已提交")
        _audit(store, conn, actor_id=user["id"], action="process_record.submit", resource_type="process_record", resource_id=record_id, organization_id=cohort["organization_id"])
    return _record_view(record)


@router.post("/process-records/{record_id}/decisions")
def decide_record(record_id: str, req: ProcessDecision, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    record = _must(store, "process_record", record_id, "过程材料")
    participant, cohort = _ensure_record_access(store, user, record)
    if record.get("state") != "submitted":
        raise HTTPException(status_code=409, detail="只有待审批材料可以处理")
    item = catalog_item(record["artifact_type"])
    if user["role"] == "student":
        raise HTTPException(status_code=403, detail="学生不能处理材料审批")
    reason = req.reason.strip()
    if req.decision in {"returned", "rejected"} and not reason:
        raise HTTPException(status_code=422, detail="退回或驳回必须填写原因")
    if req.decision == "approved":
        required = _next_approval(record, item or {"required_approvals": []})
        if required is None:
            raise HTTPException(status_code=409, detail="材料已完成全部审批")
        if not _role_can_approve(user, required, participant):
            raise HTTPException(status_code=403, detail=f"当前材料等待 {required} 审批")
    entry = {
        "id": new_id("approval"), "role": required if req.decision == "approved" else user["role"],
        "actor_role": user["role"], "actor_id": user["id"], "decision": req.decision,
        "reason": reason, "created_at": _now(),
    }
    record.setdefault("approval_history", []).append(entry)
    if req.decision == "approved":
        next_role = _next_approval(record, item or {"required_approvals": []})
        record["pending_approvals"] = [next_role] if next_role else []
        if next_role is None:
            _transition_record(record, "approved")
            record["approved_at"] = _now()
    else:
        _transition_record(record, "returned" if req.decision == "returned" else "rejected")
        record["pending_approvals"] = list(item["required_approvals"] if item else [])
    with store.workflow_transaction() as conn:
        if record["state"] == "approved" and record["artifact_type"] == "topic_change":
            if participant is None:
                raise HTTPException(status_code=409, detail="课题变更必须关联学生阶段任务")
            _apply_topic_change(store, conn, record, participant, cohort, actor_id=user["id"])
        _record(store, "process_record", record, expected_revision=record.get("revision", 0), conn=conn)
        if req.decision == "approved" and record["artifact_type"] == "extension_application" and participant:
            _apply_extension_application(store, conn, record, participant, cohort, actor_id=user["id"])
        if req.decision == "approved" and participant:
            _maybe_create_comprehensive_score(store, conn, participant, cohort, actor_id=user["id"], current_record=record)
        title = f"{item['name'] if item else '过程材料'}审批已通过" if record["state"] == "approved" else f"{item['name'] if item else '过程材料'}需要处理"
        _notify_record_owner(store, conn, record, title)
        _audit(store, conn, actor_id=user["id"], action=f"process_record.{req.decision}", resource_type="process_record", resource_id=record_id, organization_id=cohort["organization_id"], reason=reason)
    return _record_view(record)


def _apply_topic_change(store: Store, conn, record: dict, participant: dict, cohort: dict, *, actor_id: str) -> None:
    """Final approval changes the current draft; immutable submission snapshots stay intact."""
    current = store.get_workflow_record("participant", participant["id"])
    if current is None or current.get("revision", 0) != participant.get("revision", 0):
        raise HTTPException(status_code=409, detail="阶段任务已变化，请刷新后重新审批")
    project = store.get_project(current.get("project_id", ""))
    if (project is None or project.deleted_at or project.owner_id != current.get("student_id")
            or (project.organization_context or {}).get("batch_id") != cohort["id"]):
        raise HTTPException(status_code=409, detail="课题变更未关联有效的本学生、本批次论文")
    new_topic = (record.get("data") or {}).get("new_topic")
    if not isinstance(new_topic, str) or not new_topic.strip():
        raise HTTPException(status_code=422, detail="课题变更必须填写变更后课题名称")
    expected_revision = project.revision
    project.title = new_topic.strip()
    project.meta = {**project.meta, "title": project.title}
    if store.update_project_if_revision_conn(conn, project, expected_revision) is None:
        raise HTTPException(status_code=409, detail="论文已被其他操作更新，请刷新后重新审批")
    _audit(store, conn, actor_id=actor_id, action="project.topic_changed", resource_type="project",
           resource_id=project.id, organization_id=cohort["organization_id"], reason=record["id"])


def _approved_participant_records(store: Store, participant_id: str, current_record: dict | None = None) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in _records_for_participant(store, participant_id):
        if item.get("state") == "approved":
            result[item.get("artifact_type", "")] = item
    if current_record and current_record.get("state") == "approved":
        result[current_record.get("artifact_type", "")] = current_record
    return result


def score_summary(store: Store, participant_id: str, current_record: dict | None = None) -> dict[str, Any]:
    records = _approved_participant_records(store, participant_id, current_record)
    values: dict[str, float | None] = {}
    for key in ("advisor_score", "reviewer_score", "defense_score"):
        raw = (records.get(key, {}).get("data") or {}).get("score") if records.get(key) else None
        try:
            values[key] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            values[key] = None
    complete = all(value is not None for value in values.values())
    total = None
    rule = "weighted_30_20_50"
    if complete:
        if values["defense_score"] < 60:
            total = values["defense_score"]
            rule = "defense_below_60_uses_defense_score"
        else:
            total = values["advisor_score"] * 0.3 + values["reviewer_score"] * 0.2 + values["defense_score"] * 0.5
    return {"participant_id": participant_id, "complete": complete, "scores": values,
            "weights": {"advisor_score": 0.3, "reviewer_score": 0.2, "defense_score": 0.5},
            "total_score": round(total, 2) if total is not None else None, "rule": rule,
            "grade": "优秀" if total is not None and total >= 90 else "良好" if total is not None and total >= 80 else "中等" if total is not None and total >= 70 else "及格" if total is not None and total >= 60 else "不及格" if total is not None else "待计算"}


def _maybe_create_comprehensive_score(store: Store, conn, participant: dict, cohort: dict, *, actor_id: str, current_record: dict | None = None) -> None:
    if current_record and current_record.get("artifact_type") == "comprehensive_score":
        return
    summary = score_summary(store, participant["id"], current_record)
    if not summary["complete"]:
        return
    existing = [item for item in _records_for_participant(store, participant["id"])
                if item.get("artifact_type") == "comprehensive_score" and item.get("state") in {"draft", "submitted", "approved"}]
    if existing:
        return
    data = {**summary["scores"], "total_score": summary["total_score"], "grade": summary["grade"], "rule": summary["rule"]}
    record = {
        "id": new_id("record"), "scope_type": "participant", "scope_id": participant["id"],
        "participant_id": participant["id"], "cohort_id": cohort["id"], "student_id": participant["student_id"],
        "artifact_type": "comprehensive_score", "data": data,
        "form_schema": artifact_form_schema("comprehensive_score"), "note": "系统按 30%/20%/50% 自动计算",
        "state": "submitted", "version": 1, "approval_history": [], "attachments": [],
        "created_by": actor_id, "submitted_by": actor_id, "submitted_at": _now(),
        "pending_approvals": ["college_admin"], "created_at": _now(), "revision": 0,
    }
    _record(store, "process_record", record, conn=conn)
    _notify(store, conn, participant["student_id"], "score.computed", "process_record", record["id"], "综合成绩已计算，等待学院确认")


def _apply_extension_application(store: Store, conn, record: dict, participant: dict, cohort: dict, *, actor_id: str) -> None:
    raw = (record.get("data") or {}).get("effective_deadline_at")
    if not raw:
        return
    try:
        effective = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        effective = effective.replace(tzinfo=effective.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return
    current = participant.get("effective_deadline_at", "")
    if current:
        try:
            current_dt = datetime.fromisoformat(current.replace("Z", "+00:00"))
            current_dt = current_dt.replace(tzinfo=current_dt.tzinfo or timezone.utc).astimezone(timezone.utc)
            if effective <= current_dt:
                return
        except ValueError:
            pass
    participant["effective_deadline_at"] = effective.isoformat(timespec="seconds")
    participant["extension"] = {"reason": (record.get("data") or {}).get("reason", ""), "approved_by": actor_id, "approved_at": _now(), "record_id": record["id"]}
    _record(store, "participant", participant, expected_revision=participant.get("revision", 0), conn=conn)
    _notify(store, conn, participant["student_id"], "participant.extended", "participant", participant["id"], "延期申请已批准，阶段截止时间已更新")


@router.post("/process-records/{record_id}/attachments", status_code=201)
async def upload_record_attachment(record_id: str, file: UploadFile = File(...), store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    record = _must(store, "process_record", record_id, "过程材料")
    _, cohort = _ensure_record_editor(store, user, record)
    filename = Path(file.filename or "attachment").name
    if filename != file.filename or any(char in filename for char in "\\r\\n"):
        raise HTTPException(status_code=400, detail="附件名不合法")
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="附件格式仅支持 DOC、DOCX、PDF、ZIP、PPTX、Excel、图片或文本")
    data = read_limited(file, IMPORT_MAX_BYTES)
    if suffix == ".docx":
        validate_docx_bytes(data)
    file_id = new_id("file")
    directory = _process_file_dir() / record_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{file_id}{suffix}"
    target.write_bytes(data)
    attachment = {
        "id": file_id, "name": filename, "media_type": file.content_type or "application/octet-stream",
        "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "storage_path": str(target), "uploaded_by": user["id"], "created_at": _now(),
    }
    record.setdefault("attachments", []).append(attachment)
    try:
        with store.workflow_transaction() as conn:
            _record(store, "process_record", record, expected_revision=record.get("revision", 0), conn=conn)
            _audit(store, conn, actor_id=user["id"], action="process_record.attach", resource_type="process_record", resource_id=record_id, organization_id=cohort["organization_id"])
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return _record_view(record)["attachments"][-1]


@router.post("/process-records/{record_id}/export")
def export_process_record(record_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> FileResponse:
    """Export the structured record as a filled, hashable office snapshot."""
    record = _must(store, "process_record", record_id, "过程材料")
    participant, cohort = _ensure_record_access(store, user, record)
    schema_hash = hashlib.sha256(
        __import__("json").dumps({"type": record["artifact_type"], "version": record.get("version", 1), "data": record.get("data", {})}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    schema = artifact_form_schema(record["artifact_type"])
    is_xlsx = schema.get("format") == "xlsx"
    suffix = ".xlsx" if is_xlsx else ".docx"
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if is_xlsx
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    existing = next((attachment for attachment in record.get("attachments", []) if attachment.get("generated_from") == schema_hash), None)
    if existing and Path(existing.get("storage_path", "")).is_file():
        path = Path(existing["storage_path"])
        return FileResponse(path, filename=existing["name"], media_type=existing.get("media_type", media_type))
    directory = _process_file_dir() / record_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record_id}-v{record.get('version', 1)}{suffix}"
    if is_xlsx:
        render_artifact_xlsx(record["artifact_type"], record.get("data", {}), path, participant=participant, cohort=cohort)
    else:
        render_artifact_docx(record["artifact_type"], record.get("data", {}), path, participant=participant, cohort=cohort)
    generated = {
        "id": new_id("file"), "name": f"{record.get('artifact_type', 'process-record')}-filled{suffix}",
        "media_type": media_type,
        "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "storage_path": str(path), "uploaded_by": user["id"], "created_at": _now(),
        "generated_from": schema_hash,
    }
    record.setdefault("attachments", []).append(generated)
    try:
        with store.workflow_transaction() as conn:
            _record(store, "process_record", record, expected_revision=record.get("revision", 0), conn=conn)
            _audit(store, conn, actor_id=user["id"], action="process_record.export", resource_type="process_record", resource_id=record_id, organization_id=cohort["organization_id"])
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(path, filename=generated["name"], media_type=generated["media_type"])


@router.get("/process-files/{file_id}/download")
def download_record_attachment(file_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> FileResponse:
    for record in store.list_workflow_records("process_record"):
        attachment = next((item for item in record.get("attachments", []) if item.get("id") == file_id), None)
        if not attachment:
            continue
        assert_resource_read(store, user, "process_record", record)
        path = Path(attachment.get("storage_path", "")).resolve()
        base = _process_file_dir().resolve()
        if not path.is_file() or not path.is_relative_to(base):
            raise HTTPException(status_code=404, detail="附件不存在")
        return FileResponse(path, media_type=attachment.get("media_type"), filename=attachment.get("name", "attachment"), headers={"X-Content-Type-Options": "nosniff"})
    raise HTTPException(status_code=404, detail="附件不存在")


def material_requirement_state(store: Store, participant_id: str, requirement: str, supplied_refs: list[str] | None = None) -> dict[str, Any]:
    """为提交门禁返回一个材料要求的可解释状态。"""
    artifact_type, item = _canonical_or_422(requirement)
    records = [record for record in _records_for_participant(store, participant_id) if record.get("artifact_type") == artifact_type]
    supplied = set(supplied_refs or [])
    records = [record for record in records if not supplied or record["id"] in supplied or record.get("state") in _READY_STATES]
    ready = [record for record in records if record.get("state") in _READY_STATES]
    if artifact_type == "similarity_report":
        qualified = []
        for record in ready:
            data = record.get("data") or {}
            try:
                ratio = float(data.get("copy_ratio", data.get("R")))
            except (TypeError, ValueError):
                continue
            if ratio < 40:
                qualified.append(record)
        if any(float((record.get("data") or {}).get("copy_ratio", (record.get("data") or {}).get("R"))) > 60 for record in ready if (record.get("data") or {}).get("copy_ratio", (record.get("data") or {}).get("R")) is not None):
            ready = []
        else:
            ready = qualified
    return {
        "requirement": requirement, "artifact_type": artifact_type, "label": item["name"],
        "passed": bool(ready), "record_ids": [record["id"] for record in ready],
        "detail": "已提交/审批" if ready else f"缺少已提交的{item['name']}",
    }


def participant_qualification(store: Store, participant_id: str) -> dict[str, Any]:
    """按学校制度计算答辩资格的可解释摘要，不直接改写阶段状态。"""
    records = _records_for_participant(store, participant_id)
    by_type: dict[str, list[dict]] = {}
    for record in records:
        if record.get("state") in _READY_STATES:
            by_type.setdefault(record.get("artifact_type", ""), []).append(record)
    gates: list[dict[str, Any]] = []
    for artifact_type in ("task_book", "opening_report", "guidance_log", "midterm_check", "final_report", "similarity_report", "advisor_score", "reviewer_score"):
        item = ARTIFACT_BY_TYPE[artifact_type]
        values = by_type.get(artifact_type, [])
        passed = bool(values)
        detail = "已具备" if passed else f"缺少{item['name']}"
        if artifact_type == "similarity_report":
            ratios=[]
            for record in values:
                try: ratios.append(float((record.get("data") or {}).get("copy_ratio", (record.get("data") or {}).get("R"))))
                except (TypeError, ValueError): pass
            passed = bool(ratios) and max(ratios) <= 60 and any(ratio < 40 for ratio in ratios)
            detail = "存在 C 类检测记录，取消答辩资格" if ratios and max(ratios) > 60 else f"最低复制比 {min(ratios):g}%" if ratios and passed else "相似性检测未达到 A 类"
        if artifact_type in {"advisor_score", "reviewer_score"}:
            scores=[]
            for record in values:
                try: scores.append(float((record.get("data") or {}).get("score", (record.get("data") or {}).get("total_score"))))
                except (TypeError, ValueError): pass
            passed = any(score >= 60 for score in scores)
            detail = f"成绩 {max(scores):g}" if scores and passed else "指导/评阅成绩不得低于 60 分"
        gates.append({"key": artifact_type, "label": item["name"], "passed": passed, "detail": detail})
    integrity_cases = [record for record in records if record.get("artifact_type") == "academic_integrity_case" and record.get("state") in _READY_STATES]
    adverse = [record for record in integrity_cases if (record.get("data") or {}).get("status") in {"investigating", "confirmed", "cancel_defense"}]
    gates.append({"key": "academic_integrity", "label": "学术诚信", "passed": not adverse, "detail": "存在未结或已确认学术不端处理记录" if adverse else "无阻断记录"})
    application = next((record for record in records if record.get("artifact_type") == "defense_application" and record.get("state") in _READY_STATES), None)
    if application:
        data = application.get("data") or {}
        try:
            absent = float(data.get("absence_count", 0)); total = float(data.get("total_sessions", 0))
            attendance_passed = not total or absent / total < 1 / 3
        except (TypeError, ValueError, ZeroDivisionError):
            attendance_passed = False
        gates.append({"key": "attendance", "label": "出勤要求", "passed": attendance_passed, "detail": "缺席不得达到三分之一" if not attendance_passed else "已满足"})
    eligible = all(gate["passed"] for gate in gates)
    return {"participant_id": participant_id, "eligible": eligible, "eligible_for_application": eligible,
            "application": {"submitted": bool(application), "record_id": application.get("id") if application else ""},
            "gates": gates, "policy": {"similarity_a_max_exclusive": 40, "advisor_min": 60, "reviewer_min": 60}}


@router.get("/stage-participants/{participant_id}/score-summary")
def get_score_summary(participant_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict[str, Any]:
    _participant_context(store, user, participant_id)
    return score_summary(store, participant_id)


@router.get("/stage-participants/{participant_id}/qualification")
def get_qualification(participant_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    _participant_context(store, user, participant_id)
    return participant_qualification(store, participant_id)
