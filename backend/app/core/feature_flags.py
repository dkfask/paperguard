"""Environment-backed rollout switches for additive product surfaces."""

from __future__ import annotations

import os

from fastapi import HTTPException


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def general_document_platform_enabled() -> bool:
    """Return whether the generic DOCX platform is available in this environment."""
    return _enabled("PAPERGUARD_FEATURE_GENERAL_DOCUMENT_PLATFORM", default=True)


def require_general_document_platform() -> None:
    if not general_document_platform_enabled():
        raise HTTPException(status_code=404, detail="通用文档平台尚未开放")


def manuscript_import_enabled() -> bool:
    """Whole-document import remains closed until its separate pilot approval."""
    return False


def require_manuscript_import() -> None:
    if not manuscript_import_enabled():
        raise HTTPException(status_code=403, detail={
            "code": "manuscript_import_unavailable",
            "message": "整篇 Word 导入尚未开放，可继续在系统内写作。",
        })


def m1_pilot_active() -> bool:
    """Restrict unfinished delivery surfaces in a configured pilot environment.

    Unknown configured stages remain restricted until explicitly implemented.
    Existing environments retain their legacy behavior until pilot mode is set.
    """
    return os.environ.get("PAPERGUARD_DELIVERY_STAGE", "legacy").strip().lower() != "legacy"


def require_generic_delivery() -> None:
    if m1_pilot_active():
        raise HTTPException(status_code=403, detail={
            "code": "m1_generic_delivery_unavailable",
            "message": "试点期间通用文档仅支持草稿，正式导出和下载尚未开放。",
        })


def teacher_student_workflow_enabled() -> bool:
    """Enable the checked teacher/student workflow in known delivery stages.

    Other pilot restrictions remain independent; an unknown stage stays closed.
    """
    stage = os.environ.get("PAPERGUARD_DELIVERY_STAGE", "legacy").strip().lower()
    return stage in {"legacy", "m1"}


def require_m2_workflow() -> None:
    # Kept as the route dependency name for compatibility with existing callers.
    if not teacher_student_workflow_enabled():
        raise HTTPException(status_code=403, detail={
            "code": "m1_workflow_unavailable",
            "message": "当前交付阶段暂不开放论文提交、审核、批注和归档，可继续写作并保存草稿。",
        })


def require_template_migration() -> None:
    if m1_pilot_active():
        raise HTTPException(status_code=403, detail={
            "code": "m1_template_migration_unavailable",
            "message": "试点阶段暂不开放已有论文的模板迁移，当前论文继续使用原模板。",
        })


def project_delivery_enabled(store, project_owner_id: str) -> bool:
    """M1 permits the student writing path; personal staff projects stay drafts."""
    if not m1_pilot_active():
        return True
    owner = store.get_user(project_owner_id) if project_owner_id else None
    return bool(owner and owner.get("role") == "student")


def require_project_delivery(store, project_owner_id: str) -> None:
    if not project_delivery_enabled(store, project_owner_id):
        raise HTTPException(status_code=403, detail={
            "code": "m1_personal_delivery_unavailable",
            "message": "试点期间教师个人写作仅支持草稿，正式导出和下载尚未开放。",
        })


__all__ = ["general_document_platform_enabled", "require_general_document_platform",
           "manuscript_import_enabled", "require_manuscript_import",
           "m1_pilot_active", "require_generic_delivery", "teacher_student_workflow_enabled", "require_m2_workflow",
           "require_template_migration", "project_delivery_enabled", "require_project_delivery"]
