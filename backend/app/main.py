"""PaperGuard Web 后端入口。

启动: uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.auth import current_user
from app.core.context import RequestContext, reset_request_context, set_request_context
from app.core.structured_logging import log_event
from app.db import Store
from app.services.image_cleanup import project_image_urls
from app.services.document_access import can_access_document_project
from app.services.resource_access import assert_resource_read
from app.api.ai import router as ai_router
from app.api.auth import router as auth_router
from app.api.projects import router as projects_router
from app.api.routes import router
from app.api.teacher import router as teacher_router
from app.api.templates import router as templates_router
from app.api.policies import router as policies_router
from app.api.specs import router as specs_router
from app.api.document_packages import router as document_packages_router
from app.api.document_projects import router as document_projects_router
from app.api.organizations import router as organizations_router
from app.api.operations import router as operations_router
from app.api.saas import router as saas_router
from app.api.process_records import router as process_records_router
from app.api.audit import router as audit_router
from app.api.workflow import recover_pending_archives, router as workflow_router


def _load_dotenv() -> None:
    """加载仓库根目录 / backend 目录下的 .env(本地 secrets, 已 gitignore)。

    只填充未设置的环境变量, 已显式设置的不覆盖(测试与 CI 优先)。
    """
    for base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
        env_file = base / ".env"
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                os.environ.setdefault(key, value)
        break


_load_dotenv()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动时准备默认数据并清理过期孤儿图片。"""
    try:
        from app.auth import seed_admin
        from app.db import Store
        from app.services.image_cleanup import cleanup_orphan_images
        from app.services.projects import seed_default_template

        store = Store()
        seed_admin(store)
        seed_default_template(store)
        recover_pending_archives(store)
        from app.services.export_jobs import recover_pending_exports
        recover_pending_exports(store)
        from app.services.document_render_jobs import recover_pending_document_render_jobs
        recover_pending_document_render_jobs(store)
        cleanup_orphan_images(store, grace_seconds=24 * 60 * 60)
    except Exception as exc:
        import logging
        logging.getLogger("paperguard.startup").exception("启动初始化失败: %s", exc)
    yield


app = FastAPI(
    title="PaperGuard API",
    description="论文格式规范检查与自动修复",
    version=__version__,
    lifespan=_lifespan,
)

LOGGER = logging.getLogger("paperguard.http")
LOGGER.setLevel(os.environ.get("PAPERGUARD_LOG_LEVEL", "INFO"))


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    """Attach one bounded correlation id to every response for support/debugging."""
    request_id = f"req-{uuid.uuid4().hex[:16]}"
    request.state.request_id = request_id
    context_token = set_request_context(RequestContext(request_id=request_id))
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_event(
            LOGGER, "http.request.failed", status_code=500,
            method=request.method, path=request.url.path,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    finally:
        reset_request_context(context_token)
    response.headers["X-Request-ID"] = request_id
    context = getattr(request.state, "context", None) or RequestContext(request_id=request_id)
    log_event(
        LOGGER, "http.request.completed", context=context,
        status_code=response.status_code, method=request.method,
        path=request.url.path,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return response

_allowed_origins = [
    origin.strip() for origin in os.environ.get(
        "PAPERGUARD_CORS_ORIGINS", "http://localhost:5173"
    ).split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(router)
app.include_router(auth_router)
app.include_router(templates_router)
app.include_router(projects_router)
app.include_router(ai_router)
app.include_router(teacher_router)
app.include_router(policies_router)
app.include_router(specs_router)
app.include_router(document_packages_router)
app.include_router(document_projects_router)
app.include_router(organizations_router)
app.include_router(workflow_router)
app.include_router(process_records_router)
app.include_router(operations_router)
app.include_router(saas_router)
app.include_router(audit_router)

# 通过 API 鉴权读取项目图片, 不再公开挂载整个 uploads 目录。
def _configured_upload_dir() -> Path:
    return Path(os.environ.get(
        "PAPERGUARD_DATA_DIR",
        str(Path(__file__).resolve().parent.parent / "data"),
    )) / "uploads"


# Kept for the existing runtime-path contract; request handlers resolve the
# directory dynamically so tests and workers can use PAPERGUARD_DATA_DIR.
_uploads_dir = _configured_upload_dir()
_configured_upload_dir().mkdir(parents=True, exist_ok=True)


@app.get("/media/uploads/{filename}")
def download_image(filename: str, user: dict = Depends(current_user)):
    if Path(filename).name != filename or ".." in filename:
        raise HTTPException(status_code=404, detail="图片不存在")
    url = f"/media/uploads/{filename}"
    store = Store()
    permitted = False
    for project in store.list_projects():
        if url not in project_image_urls(project) and not filename.startswith(project.id + "-"):
            continue
        try:
            assert_resource_read(store, user, "project", project)
            permitted = True
        except HTTPException:
            continue
        break
    if not permitted:
        for project in store.list_document_projects():
            if not filename.startswith(project.id + "-"):
                continue
            if can_access_document_project(store, user, project):
                permitted = True
                break
    path = _configured_upload_dir() / filename
    if not permitted or not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})
