"""写作项目 API: 基于模板创建、保存章节内容、导出 docx。"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.templates import get_store
from app.core.export.docx_exporter import export_project_to_docx
from app.db import Store, new_id
from app.models.project import ParagraphBlock, WritingProject
from app.services.image_cleanup import remove_project_images, remove_removed_images
from app.services.projects import create_project_from_template

router = APIRouter(prefix="/api/projects")

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"


class CreateProjectRequest(BaseModel):
    template_id: str
    title: str = ""


@router.get("")
def list_projects(store: Store = Depends(get_store)) -> list[dict]:
    return [
        {
            "id": p.id,
            "template_id": p.template_id,
            "template_name": p.template_name,
            "title": p.title,
            "updated_at": p.updated_at,
        }
        for p in store.list_projects()
    ]


@router.get("/{proj_id}")
def get_project(proj_id: str, store: Store = Depends(get_store)) -> WritingProject:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return proj


@router.post("", status_code=201)
def create_project(req: CreateProjectRequest, store: Store = Depends(get_store)) -> WritingProject:
    """基于模板创建写作项目(实例化章节树)。"""
    tpl = store.get_template(req.template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    proj = create_project_from_template(store, tpl)
    if req.title:
        proj.title = req.title
    return store.save_project(proj)


class UpdateSectionRequest(BaseModel):
    title: str | None = None
    paragraphs: list[dict] | None = None   # [{id, text, bold, italic, underline}]


@router.put("/{proj_id}/section/{section_key}")
def update_section(proj_id: str, section_key: str, req: UpdateSectionRequest,
                   store: Store = Depends(get_store)) -> WritingProject:
    """保存单个章节的内容。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    for sec in proj.sections:
        if sec.key == section_key:
            if req.title is not None:
                sec.title = req.title
            if req.paragraphs is not None:
                new_pars = [_parse_par(p) for p in req.paragraphs]
                # 删除从段落中移除的图片文件
                remove_removed_images(proj, new_pars)
                sec.paragraphs = new_pars
            return store.save_project(proj)
    raise HTTPException(status_code=404, detail="章节不存在")


def _parse_par(data: dict):
    """把前端传来的内容块 dict 解析为 ParagraphBlock (兼容旧格式纯文本)。"""
    block_type = data.get("type", "paragraph")
    if block_type == "image":
        return ParagraphBlock(
            id=str(data.get("id", "")),
            type="image",
            image={"url": str(data.get("image", {}).get("url", "")),
                   "caption": str(data.get("image", {}).get("caption", ""))},
        )
    if block_type == "table":
        t = data.get("table", {})
        return ParagraphBlock(
            id=str(data.get("id", "")),
            type="table",
            table={
                "rows": int(t.get("rows", 2)),
                "cols": int(t.get("cols", 2)),
                "cells": t.get("cells", []),
                "caption": str(t.get("caption", "")),
            },
        )
    return ParagraphBlock(
        id=str(data.get("id", "")),
        text=str(data.get("text", "")),
        bold=bool(data.get("bold", False)),
        italic=bool(data.get("italic", False)),
        underline=bool(data.get("underline", False)),
    )


@router.post("/{proj_id}/images", status_code=201)
async def upload_image(proj_id: str, file: UploadFile = File(...)) -> dict:
    """上传论文图片, 返回静态资源 URL。"""
    store = get_store()
    if store.get_project(proj_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片文件")

    ext = Path(file.filename or "image.png").suffix.lower() or ".png"
    fname = f"{proj_id}-{uuid.uuid4().hex[:8]}{ext}"
    dest = _UPLOAD_DIR / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"url": f"/media/uploads/{fname}"}


class UpdateMetaRequest(BaseModel):
    meta: dict[str, str]


@router.put("/{proj_id}/meta")
def update_meta(proj_id: str, req: UpdateMetaRequest,
                store: Store = Depends(get_store)) -> WritingProject:
    """保存封面元信息。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    proj.meta.update(req.meta)
    return store.save_project(proj)


@router.put("/{proj_id}")
def update_project(proj_id: str, proj: WritingProject,
                   store: Store = Depends(get_store)) -> WritingProject:
    if store.get_project(proj_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    proj.id = proj_id
    return store.save_project(proj)


@router.delete("/{proj_id}", status_code=204)
def delete_project(proj_id: str, store: Store = Depends(get_store)) -> None:
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    # 先删除项目图片文件, 再删数据库记录
    remove_project_images(proj)
    store.delete_project(proj_id)


@router.post("/{proj_id}/export")
def export_docx(proj_id: str, store: Store = Depends(get_store)):
    """按模板导出为 .docx, 下载新文件。"""
    proj = store.get_project(proj_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    tpl = store.get_template(proj.template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")

    out = Path(tempfile.mkdtemp(prefix="paperguard-export-")) / "paper.docx"
    export_project_to_docx(proj, tpl, str(out))
    safe_title = (proj.title or "paper").replace("/", "_").replace("\\", "_")
    # 中文文件名用 RFC 5987 filename* 编码, 避免 header 编码错误
    from urllib.parse import quote
    filename_ascii = "paper.docx"
    return StreamingResponse(
        out.open("rb"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename_ascii}"; filename*=UTF-8\'\'{quote(f"{safe_title}.docx")}'
        },
    )
