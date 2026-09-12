"""Single authorization gateway for protected file and report resources.

The concrete resource relationships remain owned by their domain modules. The
gateway imports those predicates lazily so API modules can share one boundary
without introducing import cycles during application startup.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.db import Store


def assert_resource_read(store: Store, user: dict[str, Any], resource_type: str, resource: Any) -> Any:
    """Return a permitted resource or fail closed with a not-found response."""
    if resource_type == "project":
        from app.api.projects import _project_accessible

        project = resource if not isinstance(resource, str) else store.get_project(resource)
        # Soft-deleted projects belong to the trash workflow and must never be
        # reachable through read-only project/report/export endpoints.
        if project is None or getattr(project, "deleted_at", None) or not _project_accessible(project, user, store):
            raise HTTPException(status_code=404, detail="资源不存在或无权访问")
        return project

    if resource_type == "archive":
        from app.api.workflow import _participant_visible

        archive = resource if isinstance(resource, dict) else store.get_workflow_record("archive", str(resource))
        participant = store.get_workflow_record("participant", (archive or {}).get("participant_id", ""))
        if archive is None or participant is None or not _participant_visible(store, user, participant):
            raise HTTPException(status_code=404, detail="资源不存在或无权访问")
        return archive

    if resource_type == "process_record":
        from app.api.process_records import _ensure_record_access

        record = resource if isinstance(resource, dict) else store.get_workflow_record("process_record", str(resource))
        if record is None:
            raise HTTPException(status_code=404, detail="资源不存在或无权访问")
        _ensure_record_access(store, user, record)
        return record

    if resource_type == "policy":
        from app.api.policies import _is_visible

        requirement_set = resource if not isinstance(resource, str) else store.get_requirement_set(resource)
        if requirement_set is None or not _is_visible(requirement_set, user, store):
            raise HTTPException(status_code=404, detail="资源不存在或无权访问")
        return requirement_set

    raise HTTPException(status_code=404, detail="资源不存在或无权访问")
