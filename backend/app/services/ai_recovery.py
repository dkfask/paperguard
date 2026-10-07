"""Metadata-only tenant recovery slice: never retries, refunds or settles usage."""
from fastapi import HTTPException

from app.core.context import RequestContext
from app.core.saas_contract import AiRequestView, RecoveryPlan
from app.db import Store
from app.services.ai_usage_facts import view_fields


def _scope(ctx: RequestContext) -> dict:
    if ctx.actor_kind != "member" or not ctx.tenant_id or not ctx.has_capability("tenant:read"):
        raise HTTPException(status_code=403, detail={"code": "TENANT_SCOPE_FORBIDDEN", "message": "需要有效租户成员上下文"})
    if ctx.has_capability("ai:read") or ctx.has_capability("ai:manage"):
        return {"tenant_id": ctx.tenant_id, "user_id": None, "membership_id": None}
    return {"tenant_id": ctx.tenant_id, "user_id": ctx.actor_id, "membership_id": ctx.membership_id}


def _view(row: dict, store: Store) -> AiRequestView:
    return AiRequestView(**{name: row[name] for name in ("id", "tenant_id", "membership_id", "action", "status", "reservation_status", "updated_at")},
                         input_tokens_estimate=row["input_tokens"], output_tokens_estimate=row["output_tokens"], **view_fields(store, row))


def list_requests(ctx: RequestContext, store: Store, *, limit: int):
    return [_view(row, store) for row in store.list_scoped_ai_requests(**_scope(ctx), limit=limit)]


def recovery_plan(ctx: RequestContext, store: Store, request_id: str) -> RecoveryPlan:
    row = store.get_scoped_ai_request(request_id, **_scope(ctx))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "AI_REQUEST_NOT_FOUND", "message": "请求不存在或不可见"})
    return RecoveryPlan(request=_view(row, store), next_action="manual_reconciliation" if row["status"] in {"unknown", "reconciliation_required", "dispatching"} else "none")


def request_reconciliation(ctx: RequestContext, store: Store, request_id: str):
    scope = _scope(ctx)
    if not ctx.has_capability("ai:manage"):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_REQUIRED", "message": "需要租户 AI 管理能力"})
    try:
        result = store.mark_ai_reconciliation(request_id, tenant_id=scope["tenant_id"], actor_id=ctx.actor_id,
                                              membership_id=ctx.membership_id, auth_version=ctx.auth_version, request_correlation_id=ctx.request_id)
    except PermissionError:
        raise HTTPException(status_code=403, detail={"code": "TENANT_CONTEXT_INVALID", "message": "授权已变化，请重新解析成员上下文"}) from None
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "AI_REQUEST_NOT_FOUND", "message": "请求不存在或不可见"})
    row, replay = result
    if row["status"] != "reconciliation_required":
        raise HTTPException(status_code=409, detail={"code": "AI_RECONCILIATION_STATE_INVALID", "message": "仅未知请求可标记人工复核"})
    return _view(row, store), replay
