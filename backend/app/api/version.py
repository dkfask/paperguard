"""Anonymous identity declaration for this backend artifact only."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from typing import Literal
import uuid

from app.build_identity import BuildIdentity
from app.core.saas_contract import Error, ErrorBody

router = APIRouter(prefix="/api", tags=["version"])


class BuildIdentityErrorBody(ErrorBody):
    """Keep null identity fields while preserving the existing error contract."""
    component: Literal["backend"] = "backend"
    product_version: None = None
    source_revision: None = None
    release_id: None = None
    build_id: None = None
    built_at: None = None
    identity_status: Literal["missing", "invalid"]


@router.get("/version", response_model=BuildIdentity,
            responses={503: {"model": BuildIdentityErrorBody, "description": "Build identity unavailable or invalid"}},
            openapi_extra={"x-anonymous": True})
def version(request: Request) -> JSONResponse:
    identity = getattr(request.app.state, "build_identity", BuildIdentity())
    headers = {"Cache-Control": "no-store"}
    body = identity.public_dict()
    if identity.status_code == 503:
        request_id = getattr(request.state, "request_id", "") or f"req-{uuid.uuid4().hex[:16]}"
        error = Error(code=f"BUILD_IDENTITY_{identity.identity_status.upper()}",
                      message="Backend build identity unavailable", request_id=request_id)
        body = BuildIdentityErrorBody(**body, error=error).model_dump()
        headers["X-Request-ID"] = request_id
    return JSONResponse(body, status_code=identity.status_code, headers=headers)
