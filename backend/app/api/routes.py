"""PaperGuard Web API 路由。

把核心引擎(IR + YAML 规则包 + 修复回写)暴露为 HTTP 接口:
- GET  /api/health            健康检查
- GET  /api/health/live       进程存活探针
- GET  /api/health/ready      业务存储就绪探针
- GET  /api/packs             规则包列表
- GET  /api/packs/{pack_id}   规则包详情
- POST /api/check             上传 docx → 检查报告 (JSON)
- POST /api/fix               上传 docx → 修复后的新 docx 文件

设计:
- 上传文件写入临时目录处理, 请求结束即清理, 不保留论文内容
- 修复永远输出新文件, 不修改上传的原文件 (核心硬约束)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.auth import current_user
from app.db import Store
from app.core.engine import validate_docx
from app.core.engine.loader import load_pack, list_packs
from app.core.loaders.docx_loader import load_docx
from app.core.reporters.json_reporter import JSONReporter
from app.services.template_compare import compare_document_to_template
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes
from app.services.docx_package_fixer import patch_docx_package
from app.services.upload_security import (
    DOCX_MAX_BYTES,
    read_limited,
    safe_docx_filename,
    validate_docx_bytes,
)

router = APIRouter(prefix="/api")

_JSON_REPORTER = JSONReporter()


def _load_pack_or_404(pack_id: str):
    try:
        return load_pack(pack_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"规则包不存在: {pack_id}") from exc


async def _save_upload(upload: UploadFile, suffix: str = ".docx") -> Path:
    """限制大小并验证 DOCX 后写入临时目录。"""
    if not upload.filename or not upload.filename.lower().endswith(suffix):
        raise HTTPException(status_code=400, detail="只支持 .docx 文件")
    data = read_limited(upload, DOCX_MAX_BYTES)
    validate_docx_bytes(data)
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-"))
    target = tmp_dir / f"upload{suffix}"
    try:
        target.write_bytes(data)
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"文件保存失败: {exc}") from exc
    return target


def _cleanup(tmp: Path) -> None:
    shutil.rmtree(tmp.parent, ignore_errors=True)


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "paperguard"}


@router.get("/health/live")
def health_live() -> dict:
    """进程存活探针：只确认 HTTP 应用仍能响应。"""
    return {"status": "ok", "service": "paperguard", "probe": "live"}


@router.get("/health/ready")
def health_ready() -> dict:
    """就绪探针：确认应用可以初始化并访问业务存储。"""
    try:
        store = Store()
        store.list_templates()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="业务存储未就绪") from exc
    return {"status": "ok", "service": "paperguard", "probe": "ready"}


@router.get("/packs")
def packs() -> list[str]:
    """可用规则包 ID 列表。"""
    return list_packs()


@router.get("/packs/{pack_id}")
def pack_detail(pack_id: str) -> dict:
    """规则包详情 (id / name / version / 规则条数)。"""
    pack = _load_pack_or_404(pack_id)
    return {
        "id": pack.id,
        "name": pack.name,
        "version": pack.version,
        "rule_count": len(pack.rules),
        "rules": [
            {
                "id": r.id,
                "severity": r.severity.value,
                "scope": r.scope,
                "message": r.message,
                "has_fix": r.fix is not None,
            }
            for r in pack.rules
        ],
    }


@router.post("/check-template")
async def check_template(
    file: UploadFile = File(...),
    template: UploadFile = File(...),
    _: dict = Depends(current_user),
) -> dict:
    """用本次上传的 Word 模板规范检查论文 DOCX。"""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="论文只支持 .docx 文件")
    if not template.filename or not template.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="模板只支持 .docx 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paperguard-template-check-"))
    paper_path = tmp_dir / "paper.docx"
    template_path = tmp_dir / "template.docx"
    try:
        paper_data = read_limited(file, DOCX_MAX_BYTES)
        template_data = read_limited(template, DOCX_MAX_BYTES)
        validate_docx_bytes(paper_data)
        validate_docx_bytes(template_data)
        paper_path.write_bytes(paper_data)
        template_path.write_bytes(template_data)
        from app.services.import_template import extract_section_tree, import_template_styles
        from app.models.template import PaperTemplate
        from app.services.projects import default_template
        template_config = default_template().model_dump(mode="json")
        imported_styles = import_template_styles(template_path)["styles"]
        template_config["id"] = "uploaded-template"
        template_config["name"] = template.filename
        template_config["styles"].update({key: value.model_dump(mode="json") for key, value in imported_styles.items()})
        template_config["sections"] = extract_section_tree(template_path) or template_config["sections"]
        template_model = PaperTemplate.model_validate(template_config)
        findings, context = compare_document_to_template(load_docx(str(paper_path)), template_model)
        return _JSON_REPORTER._build_payload(findings, {
            "mode": "template",
            "paper_filename": file.filename,
            "template_filename": template.filename,
            "template_section_count": context.section_count,
            "template_style_count": context.style_count,
        })
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"模板规范检查失败: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/check")
async def check(
    file: UploadFile = File(...),
    pack: str = Form("GB-T-7713-1-2006"),
    _: dict = Depends(current_user),
) -> dict:
    """上传 .docx, 用指定规则包检查, 返回结构化报告 (含可修复项)。"""
    _load_pack_or_404(pack)
    tmp = await _save_upload(file)
    try:
        ir = load_docx(str(tmp))
        findings = validate_docx(ir, pack=pack)
        return _JSON_REPORTER._build_payload(findings)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"检查失败: {exc}") from exc
    finally:
        _cleanup(tmp)


@router.post("/fix")
async def fix(
    file: UploadFile = File(...),
    pack: str = Form("GB-T-7713-1-2006"),
    filename: str = Form("fixed.docx"),
    _: dict = Depends(current_user),
):
    """上传 .docx, 自动修复可修复项, 返回修复后的新 .docx (原文件不受影响)。"""
    _load_pack_or_404(pack)
    filename = safe_docx_filename(filename)
    tmp = await _save_upload(file)
    out = tmp.parent / "fixed.docx"
    try:
        ir = load_docx(str(tmp))
        findings = validate_docx(ir, pack=pack)
        _remaining = patch_docx_package(tmp, out, findings)
        # Starlette encodes response headers as latin-1. Keep a stable ASCII
        # fallback and preserve the requested name with RFC 5987 filename*.
        filename_ascii = "fixed.docx"
        return StreamingResponse(
            out.open("rb"),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_ascii}"; filename*=UTF-8\'\'{quote(filename)}',
                "X-PaperGuard-Remaining-Fixes": str(len(_remaining)),
            },
            background=BackgroundTask(_cleanup, tmp),
        )
    except Exception as exc:
        _cleanup(tmp)
        raise HTTPException(status_code=422, detail=f"修复失败: {exc}") from exc
