"""Dynamic generic document project API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user
from app.db import Store, new_id
from app.domain.documents.schemas import ContentBlock, DocumentProject
from app.services.document_project_engine import (
    DocumentProjectError,
    editor_schema,
    instantiate_document_project,
    validate_field_values,
    validate_section_content,
)

router = APIRouter(prefix="/api/document-projects", tags=["document-projects"])


class CreateDocumentProjectRequest(BaseModel):
    package_id: str = Field(min_length=1)
    title: str = Field(default="", max_length=300)
    field_values: dict[str, Any] = Field(default_factory=dict)


class UpdateDocumentProjectRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    field_values: dict[str, Any] | None = None
    sections: dict[str, list[ContentBlock]] | None = None
    base_revision: int | None = Field(default=None, ge=0)


def get_store() -> Store:
    return Store()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _package_visible(bundle: dict[str, Any], user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    context = bundle["package"].metadata.get("organization_context") or {}
    if not context:
        return True
    user_context = user.get("organization_context") or {}
    return bool(set(map(str, context.values())) & set(map(str, user_context.values())))


def _ensure_project_access(project: DocumentProject, user: dict) -> None:
    if user.get("role") != "admin" and project.owner_id != user.get("id"):
        raise HTTPException(status_code=404, detail="项目不存在或无权访问")


def _audit(store: Store, user: dict, *, action: str, project_id: str, result: str = "success", reason: str = "") -> None:
    context = user.get("organization_context") or {}
    store.save_audit_event({
        "id": new_id("audit"),
        "actor_id": str(user.get("id") or ""),
        "action": action,
        "resource_type": "document_project",
        "resource_id": project_id,
        "organization_id": str(context.get("school_id") or ""),
        "result": result,
        "reason": reason,
        "created_at": _now(),
    })


def _summary(project: DocumentProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "package_id": project.package_id,
        "document_type_id": project.document_type_id,
        "title": project.title,
        "status": project.status,
        "revision": project.revision,
        "updated_at": project.updated_at,
        "section_count": len(project.sections),
    }


@router.get("")
def list_document_projects(
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    projects = store.list_document_projects(owner_id="" if user.get("role") == "admin" else user["id"])
    return [_summary(project) for project in projects]


@router.post("", status_code=201)
def create_document_project(
    request: CreateDocumentProjectRequest,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> DocumentProject:
    bundle = store.get_document_package_bundle(request.package_id)
    if bundle is None or bundle["package"].status.value != "published" or not _package_visible(bundle, user):
        raise HTTPException(status_code=404, detail="文档包不存在或无权使用")
    definition = bundle["template_version"].definition
    try:
        project = instantiate_document_project(
            bundle["package"],
            definition,
            project_id=new_id("docproj"),
            owner_id=user["id"],
            title=request.title.strip() or bundle["template"].name,
            field_values=request.field_values,
            organization_context=dict(user.get("organization_context") or {}),
        )
        saved = store.save_document_project(project)
    except DocumentProjectError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(store, user, action="document.project.created", project_id=saved.id)
    return saved


@router.get("/{project_id}")
def get_document_project(
    project_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> DocumentProject:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(project, user)
    return project


@router.get("/{project_id}/editor")
def get_document_project_editor(
    project_id: str,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(project, user)
    bundle = store.get_document_package_bundle(project.package_id)
    if bundle is None:
        raise HTTPException(status_code=409, detail="项目引用的文档包已失效")
    return {
        "project": project,
        "package": bundle["package"],
        "template": bundle["template"],
        "template_version": bundle["template_version"],
        "spec_version": bundle["spec_version"],
        "editor_schema": editor_schema(bundle["template_version"].definition),
    }


@router.put("/{project_id}")
def update_document_project(
    project_id: str,
    request: UpdateDocumentProjectRequest,
    store: Store = Depends(get_store),
    user: dict = Depends(current_user),
) -> DocumentProject:
    project = store.get_document_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _ensure_project_access(project, user)
    expected_revision = project.revision if request.base_revision is None else request.base_revision
    if expected_revision != project.revision:
        raise HTTPException(status_code=409, detail={"message": "项目内容已被更新, 请刷新后重试", "revision": project.revision})
    bundle = store.get_document_package_bundle(project.package_id)
    if bundle is None:
        raise HTTPException(status_code=409, detail="项目引用的文档包已失效")
    definition = bundle["template_version"].definition
    try:
        if request.field_values is not None:
            validate_field_values(definition, request.field_values)
            project.field_values = dict(request.field_values)
        if request.sections is not None:
            validate_section_content(definition, request.sections)
            project.sections = {key: list(blocks) for key, blocks in request.sections.items()}
        if request.title is not None:
            project.title = request.title.strip()
        saved = store.update_document_project_if_revision(project, expected_revision)
    except DocumentProjectError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=409, detail={"message": "项目内容已被更新, 请刷新后重试"})
    _audit(store, user, action="document.project.updated", project_id=saved.id)
    return saved


__all__ = ["router"]