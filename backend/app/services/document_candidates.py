"""Server-side bindings for generic template/spec parse candidates.

The candidate JSON returned to a browser is a review projection, not proof of
what the parser produced. These helpers reuse the existing workflow-record
store to bind the candidate to its manager, organization scope, and revision.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from app.db import Store
from app.services.document_access import document_context_visible


T = TypeVar("T", bound=BaseModel)

TEMPLATE_CANDIDATE_KIND = "document_template_candidate"
SPEC_CANDIDATE_KIND = "document_spec_candidate"


class CandidateBindingError(ValueError):
    """Raised when a candidate is missing, out of scope, or not confirmed."""


def _candidate_visible(store: Store, record: dict[str, Any], manager: dict[str, Any]) -> bool:
    if record.get("usage") == "personal":
        return bool(record.get("owner_id")) and record["owner_id"] == manager.get("id")
    if manager.get("role") == "admin":
        return True
    if str(record.get("owner_id") or "") == str(manager.get("id") or ""):
        return True
    context = record.get("organization_context") or {}
    return bool(context) and document_context_visible(
        store,
        manager,
        context,
        allow_global=False,
    )


def save_parsed_candidate(
    store: Store,
    kind: str,
    candidate: BaseModel,
    manager: dict[str, Any],
) -> dict[str, Any]:
    """Persist a review projection with ownership and organization metadata."""
    record = {
        "id": str(getattr(candidate, "candidate_id")),
        "candidate": candidate.model_dump(mode="json"),
        "candidate_kind": kind,
        "owner_id": str(manager.get("id") or ""),
        "organization_context": dict(manager.get("organization_context") or {}),
        "usage": "personal" if manager.get("role") == "teacher" else "shared",
        "status": str(getattr(candidate, "status", "needs_confirmation")),
    }
    saved = store.save_workflow_record(kind, record)
    if saved is None:
        raise CandidateBindingError("候选保存失败")
    return saved


def _load_record(
    store: Store,
    kind: str,
    candidate_id: str,
    manager: dict[str, Any],
) -> dict[str, Any]:
    record = store.get_workflow_record(kind, candidate_id)
    if record is None or not _candidate_visible(store, record, manager):
        raise CandidateBindingError("候选不存在或无权访问")
    return record


def load_candidate(
    store: Store,
    kind: str,
    candidate_id: str,
    model_type: type[T],
    manager: dict[str, Any],
) -> T:
    """Load one review candidate without exposing out-of-scope records."""
    record = _load_record(store, kind, candidate_id, manager)
    try:
        return model_type.model_validate(record.get("candidate") or {})
    except Exception as exc:
        raise CandidateBindingError("候选记录格式无效") from exc


def list_candidates(
    store: Store,
    kind: str,
    model_type: type[T],
    manager: dict[str, Any],
    *,
    limit: int = 100,
) -> list[T]:
    """Return recent well-formed candidates visible to the current manager.

    Malformed historical records are skipped deliberately. A broken record must
    not turn a scoped review inbox into a server error or leak its contents.
    """
    candidates: list[T] = []
    for record in store.list_workflow_records(kind, limit=max(1, min(limit, 100))):
        if not _candidate_visible(store, record, manager):
            continue
        try:
            candidates.append(model_type.model_validate(record.get("candidate") or {}))
        except Exception:
            continue
    return candidates


def bind_candidate_for_confirmation(
    store: Store,
    kind: str,
    submitted: T,
    model_type: type[T],
    manager: dict[str, Any],
) -> tuple[T, dict[str, Any]]:
    """Bind editable review data to the server-owned candidate identity."""
    record = _load_record(store, kind, submitted.candidate_id, manager)
    persisted = model_type.model_validate(record.get("candidate") or {})
    if submitted.candidate_id != getattr(persisted, "candidate_id"):
        raise CandidateBindingError("候选身份不一致")

    immutable: dict[str, Any] = {"candidate_id": persisted.candidate_id}
    for attribute in ("template_id", "spec_set_id", "source_asset"):
        if hasattr(persisted, attribute):
            immutable[attribute] = getattr(persisted, attribute)
    persisted_definition = getattr(persisted, "definition", None)
    submitted_definition = getattr(submitted, "definition", None)
    if (persisted_definition is not None and submitted_definition is not None
            and hasattr(persisted_definition, "organization_context")):
        immutable["definition"] = submitted_definition.model_copy(update={
            "organization_context": dict(record.get("organization_context") or {}),
        })
    bound = submitted.model_copy(update=immutable)
    return bound, record


def save_confirmed_candidate(
    store: Store,
    kind: str,
    candidate: BaseModel,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Persist confirmation with an optimistic revision guard."""
    updated = dict(record)
    updated["candidate"] = candidate.model_dump(mode="json")
    updated["status"] = str(getattr(candidate, "status", "confirmed"))
    saved = store.save_workflow_record(
        kind,
        updated,
        expected_revision=int(record.get("revision", 0)),
    )
    if saved is None:
        raise CandidateBindingError("候选已被其他操作更新, 请重新加载")
    return saved


def load_confirmed_candidate(
    store: Store,
    kind: str,
    submitted: T,
    model_type: type[T],
    manager: dict[str, Any],
) -> T:
    """Return the server-owned confirmed candidate for publication."""
    candidate, _record = load_confirmed_candidate_with_record(
        store, kind, submitted, model_type, manager
    )
    return candidate


def load_confirmed_candidate_with_record(
    store: Store,
    kind: str,
    submitted: T,
    model_type: type[T],
    manager: dict[str, Any],
) -> tuple[T, dict[str, Any]]:
    """Return a confirmed candidate together with its server-owned record."""
    record = _load_record(store, kind, submitted.candidate_id, manager)
    if record.get("status") != "confirmed":
        raise CandidateBindingError("候选必须先在服务端确认")
    candidate = model_type.model_validate(record.get("candidate") or {})
    if candidate.status != "confirmed":
        raise CandidateBindingError("候选确认状态无效")
    return candidate, record


__all__ = [
    "CandidateBindingError",
    "SPEC_CANDIDATE_KIND",
    "TEMPLATE_CANDIDATE_KIND",
    "bind_candidate_for_confirmation",
    "list_candidates",
    "load_candidate",
    "load_confirmed_candidate",
    "load_confirmed_candidate_with_record",
    "save_confirmed_candidate",
    "save_parsed_candidate",
]
