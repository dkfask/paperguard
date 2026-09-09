"""学校、组织与教师规范 API。"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth import can_manage, current_user, require_manager
from app.db import Store, new_id
from app.models.policy import (
    PolicyAuditEvent,
    RequirementBinding,
    RequirementLevel,
    RequirementSet,
    RequirementStatus,
)
from app.models.organization import OrganizationType
from app.services.policy_resolver import content_hash, hydrate_requirement_set
from app.core.engine.loader import load_pack

router = APIRouter(prefix="/api/policies", tags=["policies"])
_DOCUMENT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "policy-documents"
_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
_ALLOWED_DOCUMENT_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf", ".doc", ".docx"}
_VALID_BINDING_TYPES = {"global", "school", "college", "major", "course", "batch", "teacher", "student", "project"}


def get_store() -> Store:
    return Store()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user_context(user: dict) -> dict[str, str]:
    context = user.get("organization_context") or {}
    return {str(key): str(value) for key, value in context.items() if value}


def _user_scope_ids(store: Store | None, user: dict) -> set[str]:
    visible = set(_user_context(user).values())
    if store is None or user["role"] != "college_admin":
        return visible
    changed = True
    while changed:
        changed = False
        for unit in store.list_organization_units():
            if unit.parent_id in visible and unit.id not in visible:
                visible.add(unit.id)
                changed = True
    return visible


def _binding_visible(binding: dict, user: dict, store: Store | None = None) -> bool:
    if user["role"] == "admin":
        return True
    scope_type = binding.get("scope_type", "global")
    scope_id = binding.get("scope_id", "")
    if scope_type in ("global", "all"):
        return user["role"] != "college_admin"
    aliases = {
        "school": "school_id", "college": "college_id", "major": "major_id",
        "course": "course_id", "batch": "batch_id", "teacher": "teacher_id",
        "student": "student_id",
    }
    if not scope_id:
        return False
    context_key = aliases.get(scope_type, scope_type)
    return _user_context(user).get(context_key) == scope_id or scope_id in _user_scope_ids(store, user)


def _is_visible(requirement_set: RequirementSet, user: dict, store: Store | None = None) -> bool:
    if user["role"] == "admin":
        return True
    if requirement_set.level == RequirementLevel.TEACHER:
        if user["role"] == "teacher":
            return requirement_set.owner_id == user["id"]
        return requirement_set.status == RequirementStatus.PUBLISHED and requirement_set.owner_id == user.get("teacher_id")
    if requirement_set.level == RequirementLevel.PROJECT:
        return False
    if requirement_set.status != RequirementStatus.PUBLISHED and user["role"] != "college_admin":
        return False
    if requirement_set.level == RequirementLevel.SCHOOL:
        return requirement_set.status == RequirementStatus.PUBLISHED
    return any(_binding_visible(binding.model_dump(), user, store) for binding in requirement_set.bindings)


def _can_edit(requirement_set: RequirementSet, user: dict, store: Store | None = None) -> bool:
    if user["role"] == "admin":
        return True
    if user["role"] == "teacher":
        return can_manage(user) and requirement_set.level == RequirementLevel.TEACHER and requirement_set.owner_id == user["id"]
    if user["role"] == "college_admin":
        return can_manage(user) and requirement_set.level in (
            RequirementLevel.COLLEGE, RequirementLevel.MAJOR, RequirementLevel.COURSE, RequirementLevel.BATCH
        ) and any(_binding_visible(binding.model_dump(), user, store) for binding in requirement_set.bindings)
    return False


def _validate_policy(requirement_set: RequirementSet) -> None:
    ids: set[str] = set()
    targets: set[tuple[str, str]] = set()
    binding_ids: set[str] = set()
    for binding in requirement_set.bindings:
        if binding.id in binding_ids:
            raise HTTPException(status_code=422, detail=f"规范绑定 ID 重复: {binding.id}")
        binding_ids.add(binding.id)
        if binding.scope_type not in _VALID_BINDING_TYPES:
            raise HTTPException(status_code=422, detail=f"不支持的作用域类型: {binding.scope_type}")
        if binding.scope_type not in ("global", "all") and not binding.scope_id:
            raise HTTPException(status_code=422, detail="非全局绑定必须填写 scope_id")
    for item in requirement_set.items:
        if item.id in ids:
            raise HTTPException(status_code=422, detail=f"要求项 ID 重复: {item.id}")
        ids.add(item.id)
        if item.type.value == "auto" and not item.checker.strip():
            raise HTTPException(status_code=422, detail=f"自动检查项缺少 checker: {item.id}")
        key = (item.target, item.checker)
        if item.checker and key in targets:
            raise HTTPException(status_code=422, detail=f"同一目标和检查器重复: {item.target} / {item.checker}")
        targets.add(key)
        if item.rule_pack_id:
            try:
                load_pack(item.rule_pack_id, params=item.rule_pack_params)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=422, detail=f"规则包不存在: {item.rule_pack_id}") from exc
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"规则包参数无效: {item.id}: {exc}") from exc
    if requirement_set.level == RequirementLevel.TEACHER and not requirement_set.owner_id:
        raise HTTPException(status_code=422, detail="教师要求必须绑定教师")
    if requirement_set.level in (RequirementLevel.COLLEGE, RequirementLevel.MAJOR, RequirementLevel.COURSE, RequirementLevel.BATCH):
        if not requirement_set.bindings:
            raise HTTPException(status_code=422, detail="学院/专业/批次规范必须至少配置一个作用域绑定")


def _summary(requirement_set: RequirementSet) -> dict:
    return {
        "id": requirement_set.id,
        "name": requirement_set.name,
        "level": requirement_set.level.value,
        "scope": requirement_set.scope,
        "scope_id": requirement_set.scope_id,
        "owner_id": requirement_set.owner_id,
        "status": requirement_set.status.value,
        "version": requirement_set.version,
        "description": requirement_set.description,
        "effective_from": requirement_set.effective_from,
        "effective_to": requirement_set.effective_to,
        "stage": requirement_set.stage,
        "deadline_at": requirement_set.deadline_at,
        "item_count": len(requirement_set.items),
        "document_count": len(requirement_set.documents),
        "binding_count": len(requirement_set.bindings),
        "updated_at": requirement_set.updated_at,
    }


def _audit(store: Store, policy_id: str, action: str, user: dict, detail: dict | None = None) -> None:
    store.save_policy_audit(PolicyAuditEvent(
        id=new_id("policy-event"),
        policy_id=policy_id,
        action=action,
        actor_id=user["id"],
        detail=detail or {},
        created_at=_now(),
    ))


@router.get("")
def list_policies(store: Store = Depends(get_store), user: dict = Depends(current_user)) -> list[dict]:
    return [
        _summary(requirement_set)
        for requirement_set in store.list_requirement_sets()
        if _is_visible(requirement_set, user, store)
    ]


@router.get("/{policy_id}")
def get_policy(policy_id: str, store: Store = Depends(get_store), user: dict = Depends(current_user)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None or not _is_visible(requirement_set, user, store):
        raise HTTPException(status_code=404, detail="规范不存在或无权访问")
    return requirement_set


@router.post("", status_code=201)
def create_policy(payload: dict, store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> RequirementSet:
    data = dict(payload)
    data["id"] = str(data.get("id") or new_id("policy"))
    try:
        requirement_set = RequirementSet.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"规范数据无效: {exc}") from exc
    if requirement_set.level == RequirementLevel.SCHOOL and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只有学校管理员可以维护学校规范")
    if user["role"] == "teacher":
        if requirement_set.level != RequirementLevel.TEACHER:
            raise HTTPException(status_code=403, detail="教师只能创建教师要求")
        requirement_set.owner_id = user["id"]
    elif user["role"] == "college_admin" and requirement_set.level not in (
        RequirementLevel.COLLEGE, RequirementLevel.MAJOR, RequirementLevel.COURSE, RequirementLevel.BATCH
    ):
        raise HTTPException(status_code=403, detail="学院管理员只能维护学院、专业或批次规范")
    if user["role"] == "college_admin" and any(
        not _binding_visible(binding.model_dump(), user, store)
        for binding in requirement_set.bindings
    ):
        raise HTTPException(status_code=403, detail="规范绑定超出本人组织范围")
    if requirement_set.level == RequirementLevel.TEACHER and not requirement_set.owner_id:
        raise HTTPException(status_code=422, detail="管理员创建教师要求时必须指定 owner_id")
    if store.get_requirement_set(requirement_set.id):
        raise HTTPException(status_code=409, detail="规范 ID 已存在")
    if not requirement_set.family_id:
        requirement_set.family_id = requirement_set.id
    requirement_set = hydrate_requirement_set(requirement_set)
    _validate_policy(requirement_set)
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "created", user, {"version": saved.version})
    return saved


@router.put("/{policy_id}")
def update_policy(policy_id: str, payload: RequirementSet,
                  store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> RequirementSet:
    existing = store.get_requirement_set(policy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(existing, user, store):
        raise HTTPException(status_code=403, detail="无权编辑此规范")
    if existing.status == RequirementStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="已发布规范不可原地修改，请创建新版本")
    if payload.id != policy_id:
        raise HTTPException(status_code=422, detail="规范 ID 不可修改")
    if payload.level != existing.level or payload.owner_id != existing.owner_id:
        raise HTTPException(status_code=422, detail="规范归属不可修改")
    payload = hydrate_requirement_set(payload)
    _validate_policy(payload)
    saved = store.save_requirement_set(payload)
    _audit(store, saved.id, "updated", user, {"version": saved.version})
    return saved


class NewVersionRequest(BaseModel):
    id: str = ""
    version: str
    name: str = ""
    reason: str = ""


@router.post("/{policy_id}/versions", status_code=201)
def create_policy_version(policy_id: str, req: NewVersionRequest,
                          store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> RequirementSet:
    existing = store.get_requirement_set(policy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(existing, user, store):
        raise HTTPException(status_code=403, detail="无权创建此规范的新版本")
    new_id_value = req.id.strip() or new_id("policy")
    if store.get_requirement_set(new_id_value):
        raise HTTPException(status_code=409, detail="新版本 ID 已存在")
    data = existing.model_dump(mode="json")
    data.update({
        "id": new_id_value,
        "family_id": existing.family_id or existing.id,
        "version": req.version.strip(),
        "name": req.name.strip() or existing.name,
        "status": RequirementStatus.DRAFT.value,
        "created_at": "",
        "updated_at": "",
    })
    clone = RequirementSet.model_validate(data)
    for document in clone.documents:
        if not document.storage_key:
            continue
        source = _policy_document_path(policy_id, document.storage_key)
        suffix = Path(document.filename).suffix.lower()
        copied_key = f"{document.id}{suffix}"
        target = _policy_document_path(new_id_value, copied_key)
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            document.storage_key = copied_key
    saved = store.save_requirement_set(clone)
    _audit(store, saved.id, "version_created", user, {"from_policy_id": policy_id, "reason": req.reason})
    return saved


@router.get("/{policy_id}/diff")
def diff_policy(policy_id: str, against: str = "", store: Store = Depends(get_store),
                user: dict = Depends(current_user)) -> dict:
    current = store.get_requirement_set(policy_id)
    other = store.get_requirement_set(against) if against else None
    if current is None or not _is_visible(current, user, store) or (against and (other is None or not _is_visible(other, user, store))):
        raise HTTPException(status_code=404, detail="规范不存在或无权访问")
    if other is None:
        return {"policy_id": policy_id, "current": current, "content_hash": content_hash(current.model_dump(mode="json"))}
    current_items = {item.id: item.model_dump(mode="json") for item in current.items}
    other_items = {item.id: item.model_dump(mode="json") for item in other.items}
    return {
        "policy_id": policy_id,
        "against": against,
        "added": [current_items[key] for key in current_items.keys() - other_items.keys()],
        "removed": [other_items[key] for key in other_items.keys() - current_items.keys()],
        "changed": [{"before": other_items[key], "after": current_items[key]} for key in current_items.keys() & other_items.keys() if current_items[key] != other_items[key]],
        "metadata_changed": current.model_dump(mode="json") != other.model_dump(mode="json"),
    }


@router.post("/{policy_id}/submit-review")
def submit_policy_review(policy_id: str, store: Store = Depends(get_store),
                         user: dict = Depends(require_manager)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=403, detail="无权提交此规范审核")
    if requirement_set.status != RequirementStatus.DRAFT:
        raise HTTPException(status_code=409, detail="只有草稿可以提交审核")
    requirement_set = hydrate_requirement_set(requirement_set)
    _validate_policy(requirement_set)
    requirement_set.status = RequirementStatus.REVIEW
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "submitted_review", user)
    return saved


@router.post("/{policy_id}/publish")
def publish_policy(policy_id: str, store: Store = Depends(get_store),
                   user: dict = Depends(require_manager)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=403, detail="无权发布此规范")
    requirement_set = hydrate_requirement_set(requirement_set)
    _validate_policy(requirement_set)
    requirement_set.status = RequirementStatus.PUBLISHED
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "published", user, {"effective_from": saved.effective_from, "effective_to": saved.effective_to})
    return saved


@router.post("/{policy_id}/archive")
def archive_policy(policy_id: str, store: Store = Depends(get_store),
                   user: dict = Depends(require_manager)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=403, detail="无权停用此规范")
    requirement_set.status = RequirementStatus.ARCHIVED
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "archived", user)
    return saved


@router.get("/{policy_id}/audit")
def policy_audit(policy_id: str, store: Store = Depends(get_store),
                 user: dict = Depends(current_user)) -> list[dict]:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None or not _is_visible(requirement_set, user, store):
        raise HTTPException(status_code=404, detail="规范不存在或无权访问")
    return [item.model_dump(mode="json") for item in store.list_policy_audits(policy_id)]


@router.post("/{policy_id}/bindings")
def add_policy_binding(policy_id: str, binding: RequirementBinding,
                       store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=403, detail="无权修改此规范绑定")
    if requirement_set.status == RequirementStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="已发布规范不可原地修改，请创建新版本")
    if any(item.id == binding.id for item in requirement_set.bindings):
        raise HTTPException(status_code=409, detail="规范绑定 ID 已存在")
    requirement_set.bindings.append(binding)
    _validate_policy(requirement_set)
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "binding_added", user, binding.model_dump(mode="json"))
    return saved


@router.delete("/{policy_id}/bindings/{binding_id}")
def delete_policy_binding(policy_id: str, binding_id: str,
                          store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=403, detail="无权修改此规范绑定")
    if requirement_set.status == RequirementStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="已发布规范不可原地修改，请创建新版本")
    before = len(requirement_set.bindings)
    requirement_set.bindings = [item for item in requirement_set.bindings if item.id != binding_id]
    if len(requirement_set.bindings) == before:
        raise HTTPException(status_code=404, detail="规范绑定不存在")
    _validate_policy(requirement_set)
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "binding_removed", user, {"binding_id": binding_id})
    return saved


def _policy_document_path(policy_id: str, storage_key: str) -> Path:
    root = (_DOCUMENT_DIR / policy_id).resolve()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise HTTPException(status_code=400, detail="文档路径无效")
    return path


@router.post("/{policy_id}/documents", status_code=201)
async def upload_policy_document(policy_id: str, file: UploadFile = File(...),
                                 store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=403, detail="无权上传此规范文档")
    if requirement_set.status == RequirementStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="已发布规范不可原地修改，请创建新版本")
    filename = Path(file.filename or "document").name
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="只支持 Markdown、文本、PDF 或 Word 文档")
    content = await file.read(_MAX_DOCUMENT_BYTES + 1)
    if len(content) > _MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="规范文档不能超过 20 MB")
    document_id = new_id("doc")
    storage_key = f"{document_id}{suffix}"
    path = _policy_document_path(policy_id, storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    from app.models.policy import RequirementDocument
    requirement_set.documents.append(RequirementDocument(
        id=document_id,
        filename=filename,
        version=requirement_set.version,
        summary="",
        sort_order=len(requirement_set.documents),
        storage_key=storage_key,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        checksum="sha256:" + hashlib.sha256(content).hexdigest(),
    ))
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "document_added", user, {"document_id": document_id, "filename": filename})
    return saved


@router.get("/{policy_id}/documents/{document_id}")
def download_policy_document(policy_id: str, document_id: str,
                             store: Store = Depends(get_store), user: dict = Depends(current_user)):
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None or not _is_visible(requirement_set, user, store):
        raise HTTPException(status_code=404, detail="规范不存在或无权访问")
    document = next((item for item in requirement_set.documents if item.id == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="规范文档不存在")
    path = _policy_document_path(policy_id, document.storage_key)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="规范文档文件已丢失")
    return FileResponse(path, media_type=document.content_type, filename=document.filename)


@router.delete("/{policy_id}/documents/{document_id}")
def delete_policy_document(policy_id: str, document_id: str,
                           store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> RequirementSet:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None:
        raise HTTPException(status_code=404, detail="规范不存在")
    if not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=403, detail="无权删除此规范文档")
    if requirement_set.status == RequirementStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="已发布规范不可原地修改，请创建新版本")
    document = next((item for item in requirement_set.documents if item.id == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="规范文档不存在")
    path = _policy_document_path(policy_id, document.storage_key)
    path.unlink(missing_ok=True)
    requirement_set.documents = [item for item in requirement_set.documents if item.id != document_id]
    saved = store.save_requirement_set(requirement_set)
    _audit(store, saved.id, "document_removed", user, {"document_id": document_id})
    return saved


@router.post("/{policy_id}/preview")
def preview_policy(policy_id: str, payload: dict | None = None,
                   store: Store = Depends(get_store), user: dict = Depends(current_user)) -> dict:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None or not _is_visible(requirement_set, user, store):
        raise HTTPException(status_code=404, detail="规范不存在或无权访问")
    project_id = (payload or {}).get("project_id")
    if not project_id:
        return {"policy": requirement_set, "content_hash": content_hash(requirement_set.model_dump(mode="json")), "findings": [], "not_checked": []}
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="样例项目不存在")
    if user["role"] not in ("admin", "college_admin") and project.owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="无权使用该项目预览规范")
    from app.services.project_check import check_project
    template = store.get_template(project.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="样例项目模板不存在")
    snapshot = project.requirement_snapshot or {}
    isolated = project.model_copy(deep=True)
    isolated.requirement_snapshot = {
        **snapshot,
        "policy_sets": [requirement_set.model_dump(mode="json")],
        "resolved_items": [item.model_dump(mode="json") for item in hydrate_requirement_set(requirement_set).items],
        "content_hash": content_hash(requirement_set.model_dump(mode="json")),
    }
    report = check_project(isolated, template, pack=None)
    return {"policy": requirement_set, "report": report}


@router.post("/{policy_id}/upgrade-projects")
def batch_upgrade_policy_projects(policy_id: str, payload: dict | None = None,
                                  store: Store = Depends(get_store), user: dict = Depends(require_manager)) -> dict:
    requirement_set = store.get_requirement_set(policy_id)
    if requirement_set is None or not _can_edit(requirement_set, user, store):
        raise HTTPException(status_code=404, detail="规范不存在或无权操作")
    family_id = requirement_set.family_id or requirement_set.id
    project_ids = set((payload or {}).get("project_ids") or [])
    reason = str((payload or {}).get("reason") or "规范批量升级")
    upgraded: list[str] = []
    skipped: list[str] = []
    from app.api.projects import _current_requirement_snapshot, _ensure_requirement_snapshot, _project_template
    for project in store.list_projects():
        if project_ids and project.id not in project_ids:
            continue
        if not any(
            item.get("id") == policy_id or item.get("family_id") == family_id
            for item in (project.requirement_snapshot or {}).get("policy_sets", [])
        ):
            skipped.append(project.id)
            continue
        template = _project_template(project, store)
        _ensure_requirement_snapshot(project, template, store)
        new_snapshot = _current_requirement_snapshot(project, template, store)
        if new_snapshot.get("content_hash") == project.requirement_snapshot.get("content_hash"):
            skipped.append(project.id)
            continue
        from app.models.policy import RequirementUpgrade
        project.requirement_upgrades.append(RequirementUpgrade(
            id=new_id("upgrade"),
            from_snapshot_hash=project.requirement_snapshot.get("content_hash", ""),
            to_snapshot_hash=new_snapshot.get("content_hash", ""),
            changed_policy_ids=[policy_id],
            reason=reason,
            operated_by=user["id"],
            operated_at=_now(),
        ).model_dump(mode="json"))
        project.requirement_snapshot = new_snapshot
        store.save_project(project)
        upgraded.append(project.id)
    _audit(store, policy_id, "projects_upgraded", user, {"project_ids": upgraded, "reason": reason})
    return {"upgraded": upgraded, "skipped": skipped}
