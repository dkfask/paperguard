"""PaperGuard Web 后端入口。

启动: uvicorn app.main:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
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
app.include_router(templates_router)
app.include_router(projects_router)

# 上传的图片等静态资源
_uploads_dir = Path(__file__).resolve().parent.parent / "data" / "uploads"
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/uploads", StaticFiles(directory=_uploads_dir), name="uploads")


@app.on_event("startup")
def _startup_cleanup() -> None:
    """启动时清理未被任何项目引用的孤儿图片文件。"""
    try:
        from app.db import Store
        from app.services.image_cleanup import cleanup_orphan_images
        cleanup_orphan_images(Store())
    except Exception:
        # 清理失败不影响服务启动
        pass
