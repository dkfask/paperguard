"""模板管理 API: 章节结构 + 样式规范在线配置。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.db import Store, new_id
from app.models.template import PaperTemplate
from app.services.projects import default_template, seed_default_template

router = APIRouter(prefix="/api/templates")


def get_store() -> Store:
    store = Store()
    seed_default_template(store)
    return store


@router.get("")
def list_templates(store: Store = Depends(get_store)) -> list[dict]:
    """模板列表(不含章节树细节)。"""
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "updated_at": t.updated_at,
            "section_count": _count_sections(t.sections),
        }
        for t in store.list_templates()
    ]


@router.get("/{tpl_id}")
def get_template(tpl_id: str, store: Store = Depends(get_store)) -> PaperTemplate:
    tpl = store.get_template(tpl_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl


@router.post("", status_code=201)
def create_template(payload: PaperTemplate, store: Store = Depends(get_store)) -> PaperTemplate:
    """创建模板。id 未指定时自动生成。"""
    if not payload.id:
        payload.id = new_id("tpl")
    if store.get_template(payload.id):
        raise HTTPException(status_code=409, detail="模板 id 已存在")
    return store.save_template(payload)


@router.post("/default")
def create_default_template(store: Store = Depends(get_store)) -> PaperTemplate:
    """内置模板存库(幂等)。"""
    tpl = default_template()
    store.save_template(tpl)
    return tpl


@router.put("/{tpl_id}")
def update_template(tpl_id: str, payload: PaperTemplate,
                    store: Store = Depends(get_store)) -> PaperTemplate:
    if store.get_template(tpl_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    payload.id = tpl_id
    return store.save_template(payload)


@router.delete("/{tpl_id}", status_code=204)
def delete_template(tpl_id: str, store: Store = Depends(get_store)) -> None:
    if not store.delete_template(tpl_id):
        raise HTTPException(status_code=404, detail="模板不存在")


def _count_sections(nodes) -> int:
    return sum(1 + _count_sections(n.children) for n in nodes)
