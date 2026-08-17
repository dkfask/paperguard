"""PaperGuard Web 后端入口。

启动: uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
