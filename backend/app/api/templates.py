"""模板管理 API: 章节结构 + 样式规范在线配置 + Word 导入。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.auth import require_manager

from app.db import Store, new_id
from app.models.template import PaperTemplate
from app.services.docx_package_fixer import strip_comments_from_docx
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.projects import default_template, seed_default_template
from app.services.upload_security import IMPORT_MAX_BYTES, read_limited, validate_docx_bytes

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


def _validate_template_structure(payload: PaperTemplate) -> None:
    keys: set[str] = set()
    def walk(nodes) -> None:
        for node in nodes:
            if node.key in keys:
                raise HTTPException(status_code=422, detail=f"章节 key 重复: {node.key}")
            if not node.title.strip():
                raise HTTPException(status_code=422, detail=f"章节标题不能为空: {node.key}")
            keys.add(node.key)
            walk(node.children)
    walk(payload.sections)
    fields: set[str] = set()
    for field in payload.meta_fields:
        if not field.key.strip() or not field.label.strip():
            raise HTTPException(status_code=422, detail="封面字段 key 和显示名不能为空")
        if field.key in fields:
            raise HTTPException(status_code=422, detail=f"封面字段 key 重复: {field.key}")
        fields.add(field.key)



@router.post("", status_code=201)
def create_template(payload: dict, store: Store = Depends(get_store),
                  _: dict = Depends(require_manager)) -> PaperTemplate:
    """创建模板。id 未指定时由服务端生成。"""
    data = dict(payload)
    if not str(data.get("id") or "").strip():
        data["id"] = new_id("tpl")
    try:
        template = PaperTemplate.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板数据无效: {exc}") from exc
    _validate_template_structure(template)
    if store.get_template(template.id):
        raise HTTPException(status_code=409, detail="模板 id 已存在")
    return store.save_template(template)


@router.post("/default")
def create_default_template(store: Store = Depends(get_store),
                        _: dict = Depends(require_manager)) -> PaperTemplate:
    """内置模板存库(幂等)。"""
    tpl = default_template()
    store.save_template(tpl)
    return tpl


@router.post("/strip-comments")
async def strip_template_comments(file: UploadFile = File(...),
                                  _: dict = Depends(require_manager)):
    """生成删除批注后的模板副本，不修改上传原文件。"""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="只支持 .docx 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-strip-comments-"))
    source = tmp_dir / "template.docx"
    output = tmp_dir / "template-no-comments.docx"
    try:
        data = read_limited(file, IMPORT_MAX_BYTES)
        validate_docx_bytes(data)
        source.write_bytes(data)
        counters = strip_comments_from_docx(source, output)
        stem = Path(file.filename).stem
        filename = f"{stem}-no-comments.docx"
        return StreamingResponse(
            output.open("rb"),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-PaperGuard-Removed-Comment-Parts": str(counters["parts"]),
                "X-PaperGuard-Removed-Comment-Anchors": str(counters["anchors"]),
            },
            background=BackgroundTask(shutil.rmtree, tmp_dir, True),
        )
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"批注清理失败: {exc}") from exc


@router.post("/import")
async def import_docx_template(file: UploadFile = File(...),
                              _: dict = Depends(require_manager)) -> dict:
    """上传 Word .docx, 识别其中的字体/字号/行距/缩进等样式, 返回可生成模板的配置。"""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="只支持 .docx 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-import-"))
    path = tmp_dir / "template.docx"
    try:
        data = read_limited(file, IMPORT_MAX_BYTES)
        validate_docx_bytes(data)
        path.write_bytes(data)
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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板识别失败: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.put("/{tpl_id}")
def update_template(tpl_id: str, payload: PaperTemplate,
                    store: Store = Depends(get_store),
                    _: dict = Depends(require_manager)) -> PaperTemplate:
    _validate_template_structure(payload)
    if store.get_template(tpl_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    payload.id = tpl_id
    return store.save_template(payload)


@router.delete("/{tpl_id}", status_code=204)
def delete_template(tpl_id: str, store: Store = Depends(get_store),
                  _: dict = Depends(require_manager)) -> None:
    if store.get_template(tpl_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if any(project.template_id == tpl_id for project in store.list_projects()):
        raise HTTPException(status_code=409, detail="模板仍被论文项目使用, 无法删除")
    if not store.delete_template(tpl_id):
        raise HTTPException(status_code=404, detail="模板不存在")


def _count_sections(nodes) -> int:
    return sum(1 + _count_sections(n.children) for n in nodes)
