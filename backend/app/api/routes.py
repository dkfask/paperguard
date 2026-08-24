"""PaperGuard Web API 路由。

把核心引擎(IR + YAML 规则包 + 修复回写)暴露为 HTTP 接口:
- GET  /api/health            健康检查
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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.auth import current_user
from app.core.engine import validate_docx
from app.core.engine.loader import load_pack, list_packs
from app.core.loaders.docx_loader import load_docx
from app.core.reporters.json_reporter import JSONReporter
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
        return StreamingResponse(
            out.open("rb"),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-PaperGuard-Remaining-Fixes": str(len(_remaining)),
            },
            background=BackgroundTask(_cleanup, tmp),
        )
    except Exception as exc:
        _cleanup(tmp)
        raise HTTPException(status_code=422, detail=f"修复失败: {exc}") from exc
