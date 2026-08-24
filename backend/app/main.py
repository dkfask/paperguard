"""PaperGuard Web 后端入口。

启动: uvicorn app.main:app --reload
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.auth import can_manage, current_user
from app.db import Store
from app.services.image_cleanup import project_image_urls
from app.api.auth import router as auth_router
from app.api.projects import router as projects_router
from app.api.routes import router
from app.api.templates import router as templates_router

app = FastAPI(
    title="PaperGuard API",
    description="论文格式规范检查与自动修复",
    version=__version__,
)

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

# 通过 API 鉴权读取项目图片, 不再公开挂载整个 uploads 目录。
_uploads_dir = Path(__file__).resolve().parent.parent / "data" / "uploads"
_uploads_dir.mkdir(parents=True, exist_ok=True)


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
        if can_manage(user) or project.owner_id == user["id"]:
            permitted = True
            break
    path = _uploads_dir / filename
    if not permitted or not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})



@app.on_event("startup")
def _startup_init() -> None:
    """启动初始化: 内置管理员 + 孤儿图片清理。"""
    try:
        from app.auth import seed_admin
        from app.db import Store
        from app.services.image_cleanup import cleanup_orphan_images
        from app.services.projects import seed_default_template

        store = Store()
        seed_admin(store)
        seed_default_template(store)
        cleanup_orphan_images(store, grace_seconds=24 * 60 * 60)
    except Exception as exc:
        import logging
        logging.getLogger("paperguard.startup").exception("启动初始化失败: %s", exc)
