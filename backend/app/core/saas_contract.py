"""Typed wire contracts for the SaaS slice, not a legacy API-wide wrapper."""
from typing import Generic, Literal, TypeVar

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.core.context import RequestContext

T = TypeVar("T")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Error(ContractModel):
    code: str
    message: str
    request_id: str
    checks: dict[str, Literal["passed", "blocked", "unverified"]] | None = None


class ErrorBody(ContractModel):
    error: Error


class CommandMeta(ContractModel):
    action: str
    status: Literal["succeeded"] = "succeeded"
    command_id: str
    idempotent_replay: bool = False


class ApiMeta(ContractModel):
    request_id: str
    command: CommandMeta | None = None


class ApiEnvelope(ContractModel, Generic[T]):
    data: T
    meta: ApiMeta


def envelope(data: T, ctx: RequestContext, *, action: str | None = None, command_id: str = "", replay: bool = False) -> ApiEnvelope[T]:
    command = CommandMeta(action=action, command_id=command_id, idempotent_replay=replay) if action else None
    return ApiEnvelope(data=data, meta=ApiMeta(request_id=ctx.request_id, command=command))


ERROR_RESPONSES = {code: {"model": ErrorBody} for code in (400, 401, 403, 404, 409, 422, 500)}


class SaasRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            request_id = getattr(request.state, "request_id", "")
            checks = None
            try:
                return await handler(request)
            except RequestValidationError:
                # Never echo validation input, including extra credential/identity fields.
                status, code, message = 422, "API_INPUT_INVALID", "请求参数不符合接口契约"
            except HTTPException as exc:
                status = exc.status_code
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                code = str(detail.get("code") or "API_REQUEST_REJECTED")
                message = str(detail.get("message") or "请求无法执行")
                candidate_checks = detail.get("checks")
                readiness_keys = {"owner", "organization", "published_template", "member_admission", "entitlements_quotas", "ai_policy", "readiness"}
                if isinstance(candidate_checks, dict) and set(candidate_checks) <= readiness_keys and all(v in {"passed", "blocked", "unverified"} for v in candidate_checks.values()):
                    checks = candidate_checks
            except Exception:
                # Don't expose SQL, paths, provider output or arbitrary exception text.
                status, code, message = 500, "API_INTERNAL_ERROR", "请求无法完成，请凭请求标识联系管理员"
            body = ErrorBody(error=Error(code=code, message=message, request_id=request_id, checks=checks))
            return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True))

        return wrapped


class TenantView(ContractModel):
    id: str
    slug: str
    name: str
    status: str
    auth_version: int
    created_at: str
    updated_at: str


class MembershipView(ContractModel):
    id: str
    tenant_id: str = Field(json_schema_extra={"readOnly": True})
    user_id: str
    role: str
    status: str
    capabilities: list[str]
    auth_version: int
    created_at: str
    updated_at: str


class ContextView(ContractModel):
    actor_kind: Literal["member", "platform"]
    actor_id: str = Field(json_schema_extra={"readOnly": True})
    tenant_id: str | None = Field(json_schema_extra={"readOnly": True})
    membership_id: str | None = Field(json_schema_extra={"readOnly": True})
    role: str
    capabilities: tuple[str, ...]
    organization_ids: tuple[str, ...]
    auth_version: int
    trace_id: str


class ContextData(ContractModel):
    authenticated: Literal[True] = True
    context: ContextView
    tenant: TenantView | None


class AiRequestView(ContractModel):
    id: str
    tenant_id: str = Field(json_schema_extra={"readOnly": True})
    membership_id: str
    action: str
    status: str
    reservation_status: str
    # The legacy columns contain character-based ESTIMATES, not supplier facts.
    input_tokens_estimate: int
    output_tokens_estimate: int
    usage_quality: Literal["provider_reported", "unavailable", "legacy_chars"] = "legacy_chars"
    provider_input_tokens: int | None = Field(default=None, ge=0)
    provider_output_tokens: int | None = Field(default=None, ge=0)
    provider_request_ref: str | None = None
    metering_review_required: bool = True
    billable: Literal[False] = False
    updated_at: str


class RecoveryPlan(ContractModel):
    request: AiRequestView
    next_action: Literal["manual_reconciliation", "none"]
    automatic_retry_allowed: Literal[False] = False
    automatic_refund_allowed: Literal[False] = False
    scope: Literal["ai_request_metadata_only"] = "ai_request_metadata_only"
