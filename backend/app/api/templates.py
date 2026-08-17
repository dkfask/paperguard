"""模板管理 API: 章节结构 + 样式规范在线配置 + Word 导入。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import require_manager

from app.db import Store, new_id
from app.models.template import PaperTemplate
from app.services.import_template import extract_section_tree, import_template_styles
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
def create_template(payload: PaperTemplate, store: Store = Depends(get_store),
                  _: dict = Depends(require_manager)) -> PaperTemplate:
    """创建模板。id 未指定时自动生成。"""
    if not payload.id:
        payload.id = new_id("tpl")
    if store.get_template(payload.id):
        raise HTTPException(status_code=409, detail="模板 id 已存在")
    return store.save_template(payload)


@router.post("/default")
def create_default_template(store: Store = Depends(get_store),
                        _: dict = Depends(require_manager)) -> PaperTemplate:
    """内置模板存库(幂等)。"""
    tpl = default_template()
    store.save_template(tpl)
    return tpl


@router.post("/import")
async def import_docx_template(file: UploadFile = File(...),
                              _: dict = Depends(require_manager)) -> dict:
    """上传 Word .docx, 识别其中的字体/字号/行距/缩进等样式, 返回可生成模板的配置。"""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="只支持 .docx 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-import-"))
    path = tmp_dir / "template.docx"
    try:
        with path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        result = import_template_styles(path)
        # 识别结果合并默认样式: 未识别到的键保留默认值, 保证返回完整样式集
        merged = {k: v.model_dump() for k, v in default_template().styles.items()}
        merged.update({k: v.model_dump() for k, v in result["styles"].items()})
        # 识别章节结构 (Heading 段落 → 章节树)
        sections = extract_section_tree(path)
        return {
            "filename": file.filename,
            **result,
            "styles": merged,
            "sections": sections,
        }
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板识别失败: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.put("/{tpl_id}")
def update_template(tpl_id: str, payload: PaperTemplate,
                    store: Store = Depends(get_store),
                    _: dict = Depends(require_manager)) -> PaperTemplate:
    if store.get_template(tpl_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    payload.id = tpl_id
    return store.save_template(payload)


@router.delete("/{tpl_id}", status_code=204)
def delete_template(tpl_id: str, store: Store = Depends(get_store),
                  _: dict = Depends(require_manager)) -> None:
    if not store.delete_template(tpl_id):
        raise HTTPException(status_code=404, detail="模板不存在")


def _count_sections(nodes) -> int:
    return sum(1 + _count_sections(n.children) for n in nodes)
