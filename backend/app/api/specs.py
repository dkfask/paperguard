"""Generic specification parsing and human confirmation endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api import ai as ai_api
from app.auth import require_manager
from app.db import Store, new_id
from app.services.document_candidates import (
    CandidateBindingError,
    SPEC_CANDIDATE_KIND,
    bind_candidate_for_confirmation,
    save_confirmed_candidate,
    save_parsed_candidate,
)
from app.services.policy_import import AI_IMPORT_EXTENSIONS
from app.services.spec_engine import (
    SpecCandidateError,
    SpecParseCandidate,
    confirm_spec_candidate,
    parse_spec_document,
)
from app.services.upload_security import read_limited

router = APIRouter(prefix="/api/specs", tags=["specifications"])
_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def get_store() -> Store:
    return Store()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(store: Store, manager: dict, *, action: str, resource_id: str, result: str, reason: str = "") -> None:
    context = manager.get("organization_context") or {}
    store.save_audit_event({
        "id": f"audit-{resource_id}-{action.replace('.', '-')}",
        "actor_id": str(manager.get("id") or ""),
        "action": action,
        "resource_type": "spec_candidate",
        "resource_id": resource_id,
        "organization_id": str(context.get("school_id") or ""),
        "result": result,
        "reason": reason,
        "created_at": _now(),
    })


@router.post("/parse", response_model=SpecParseCandidate)
async def parse_spec_candidate(
    file: UploadFile = File(...),
    manager: dict = Depends(require_manager),
    store: Store = Depends(get_store),
) -> SpecParseCandidate:
    """Parse a specification document into a review-only candidate."""
    filename = Path(file.filename or "specification").name
    suffix = Path(filename).suffix.lower()
    if suffix not in AI_IMPORT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="规范导入只支持 .docx、.pdf、.md、.markdown 或 .txt 文件")
    data = read_limited(file, _MAX_DOCUMENT_BYTES)
    candidate_id = new_id("speccand")
    spec_set_id = new_id("spec")
    resolved = ai_api._resolve_cfg(store, manager)
    completion = None
    if resolved:
        cfg, _source = resolved

        def completion(messages: list[dict[str, str]]) -> str:
            return ai_api._chat_completion(cfg, messages, 0.1, max_tokens=8000)

    try:
        candidate = parse_spec_document(
            data,
            filename,
            candidate_id=candidate_id,
            spec_set_id=spec_set_id,
            ai_completion=completion,
        )
    except RuntimeError as exc:
        _audit(store, manager, action="spec.candidate.parse_failed", resource_id=candidate_id, result="failure", reason="ai_not_configured")
        raise HTTPException(status_code=503, detail="AI 功能未启用；请配置 AI，或上传带 frontmatter.rules 的 Markdown 规范") from exc
    except ValueError as exc:
        _audit(store, manager, action="spec.candidate.parse_failed", resource_id=candidate_id, result="failure", reason="invalid_candidate")
        raise HTTPException(status_code=502, detail=f"规范结果无法转换: {exc}") from exc
    except HTTPException:
        _audit(store, manager, action="spec.candidate.parse_failed", resource_id=candidate_id, result="failure", reason="source_extraction_failed")
        raise
    _audit(store, manager, action="spec.candidate.parsed", resource_id=candidate_id, result="success")
    save_parsed_candidate(store, SPEC_CANDIDATE_KIND, candidate, manager)
    return candidate


@router.post("/parse/confirm", response_model=SpecParseCandidate)
def confirm_parsed_spec_candidate(
    candidate: SpecParseCandidate,
    manager: dict = Depends(require_manager),
    store: Store = Depends(get_store),
) -> SpecParseCandidate:
    """Confirm a candidate after review; publishing is intentionally later."""
    try:
        bound, record = bind_candidate_for_confirmation(
            store,
            SPEC_CANDIDATE_KIND,
            candidate,
            SpecParseCandidate,
            manager,
        )
        confirmed = confirm_spec_candidate(
            bound,
            confirmed_by=str(manager.get("id") or manager.get("username") or ""),
            confirmed_at=_now(),
        )
        save_confirmed_candidate(store, SPEC_CANDIDATE_KIND, confirmed, record)
    except CandidateBindingError as exc:
        raise HTTPException(status_code=404, detail="规范候选不存在或无权访问") from exc
    except SpecCandidateError as exc:
        _audit(
            store,
            manager,
            action="spec.candidate.confirmation_failed",
            resource_id=candidate.candidate_id,
            result="failure",
            reason="blocking_candidate_issues",
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "规范候选不能确认",
                "issues": [issue.model_dump(mode="json") for issue in exc.issues],
            },
        ) from exc
    _audit(store, manager, action="spec.candidate.confirmed", resource_id=candidate.candidate_id, result="success")
    return confirmed


__all__ = ["router"]
