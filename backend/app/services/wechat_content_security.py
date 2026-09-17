"""微信 msgSecCheck：批注、回复、修改说明写入前的 UGC 内容安全前置。

- 仅从环境变量读取 PAPERGUARD_WECHAT_APP_ID / PAPERGUARD_WECHAT_APP_SECRET。
- 未配置时跳过，避免本地/无小程序凭据环境阻断论文闭环。
- 已配置时在持久化前调用微信官方 msgSecCheck；命中违规返回 422，文本不落库。
- 目标主机固定为微信官方 API 域名，不接受运行时拼接地址。
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import HTTPException

from app.services.wechat_push import fetch_access_token

LOGGER = logging.getLogger("paperguard.content-security")

_CHECK_URL = "https://api.weixin.qq.com/wxa/msg_sec_check"
_REJECTED_ERRCODES = frozenset({"87014"})
CONTENT_SECURITY_CODE = "CONTENT_SECURITY_REJECTED"
CONTENT_SECURITY_MESSAGE = "文本未通过内容安全检查，请修改后再提交"


def wechat_security_configured() -> bool:
    app_id = os.environ.get("PAPERGUARD_WECHAT_APP_ID", "").strip()
    secret = os.environ.get("PAPERGUARD_WECHAT_APP_SECRET", "").strip()
    return bool(app_id and secret)


def inspect_ugc_text(content: str) -> dict:
    """纯检查结果, 供测试断言: skipped / pass / rejected / unavailable。"""
    text = str(content or "").strip()
    if not text:
        return {"status": "skipped", "reason": "empty"}
    if not wechat_security_configured():
        return {"status": "skipped", "reason": "not_configured"}
    app_id = os.environ.get("PAPERGUARD_WECHAT_APP_ID", "").strip()
    secret = os.environ.get("PAPERGUARD_WECHAT_APP_SECRET", "").strip()
    try:
        token = fetch_access_token(app_id, secret)
        response = httpx.post(
            _CHECK_URL,
            params={"access_token": token},
            json={"content": text[:2500]},
            timeout=10,
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        LOGGER.warning("msgSecCheck unavailable: %s", type(exc).__name__)
        return {"status": "unavailable", "reason": type(exc).__name__}
    errcode = str(payload.get("errcode", 0))
    if errcode in _REJECTED_ERRCODES:
        return {"status": "rejected", "reason": errcode}
    if errcode not in {"0", "0.0"}:
        LOGGER.warning("msgSecCheck unexpected errcode=%s", errcode)
        return {"status": "unavailable", "reason": errcode}
    return {"status": "pass", "reason": "0"}


def assert_ugc_safe(content: str, *, field: str) -> None:
    """写入前调用。rejected → 422；接口不可用 → 503，避免未审核文本入库。"""
    result = inspect_ugc_text(content)
    status = result.get("status")
    if status in {"skipped", "pass"}:
        return
    if status == "rejected":
        raise HTTPException(
            status_code=422,
            detail={"code": CONTENT_SECURITY_CODE, "message": CONTENT_SECURITY_MESSAGE, "field": field},
        )
    raise HTTPException(
        status_code=503,
        detail={"code": "CONTENT_SECURITY_UNAVAILABLE", "message": "内容安全检查暂时不可用，请稍后重试"},
    )
