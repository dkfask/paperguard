"""PaperGuard Web 后端入口。

启动: uvicorn app.main:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.auth import router as auth_router
from app.api.projects import router as projects_router
from app.api.routes import router
from app.api.templates import router as templates_router

app = FastAPI(
    title="PaperGuard API",
    description="论文格式规范检查与自动修复",
    version=__version__,
)

# 开发阶段放开跨域, 允许 Vite dev server 直接调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(auth_router)
app.include_router(templates_router)
app.include_router(projects_router)

# 上传的图片等静态资源
_uploads_dir = Path(__file__).resolve().parent.parent / "data" / "uploads"
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/uploads", StaticFiles(directory=_uploads_dir), name="uploads")


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
        cleanup_orphan_images(store)
    except Exception:
        # 初始化失败不影响服务启动
        pass
