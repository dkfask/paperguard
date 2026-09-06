"""AI 写作助手 API。

两级模型配置, 用户配置优先:
- 系统自带: 环境变量 PAPERGUARD_AI_BASE_URL / PAPERGUARD_AI_API_KEY / PAPERGUARD_AI_MODEL
- 用户自带: 写作页「AI 设置」保存到 users.ai_config (SQLite), 仅本人生效

所有补全请求走 OpenAI 兼容 /chat/completions 协议。
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user
from app.db import Store

router = APIRouter(prefix="/api/ai")

_ACTIONS = ("polish", "continue", "expand", "abstract", "english_abstract", "keywords")
_MAX_INPUT_CHARS = 12000
_MASKED_KEY_PREVIEW = 4


class AssistRequest(BaseModel):
    action: str
    text: str
    section_title: str | None = None
    section_type: str | None = None


class AiConfigRequest(BaseModel):
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)


def _system_cfg() -> dict | None:
    base_url = os.environ.get("PAPERGUARD_AI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("PAPERGUARD_AI_API_KEY", "").strip()
    model = os.environ.get("PAPERGUARD_AI_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None
    return {"base_url": base_url, "api_key": api_key, "model": model}


def _resolve_cfg(store: Store, user: dict) -> tuple[dict, str] | None:
    """返回 (cfg, source); 用户配置优先, 其次系统配置, 都无则 None。"""
    user_cfg = store.get_user_ai_config(user["id"])
    if user_cfg and all(user_cfg.get(k) for k in ("base_url", "api_key", "model")):
        return {
            "base_url": str(user_cfg["base_url"]).strip().rstrip("/"),
            "api_key": str(user_cfg["api_key"]).strip(),
            "model": str(user_cfg["model"]).strip(),
        }, "user"
    sys_cfg = _system_cfg()
    if sys_cfg:
        return sys_cfg, "system"
    return None


def _mask_key(api_key: str) -> str:
    if len(api_key) <= _MASKED_KEY_PREVIEW:
        return "***"
    return f"***{api_key[-_MASKED_KEY_PREVIEW:]}"


def _normalize_base_url(raw: str) -> str:
    """标准化 Base URL: 去除首尾空白与结尾斜杠。"""
    return raw.strip().rstrip("/")


def _parse_error_detail(resp: httpx.Response) -> str:
    """尝试从上游错误响应中提取详细原因, 提取不到则返回默认 HTTP 状态码。"""
    try:
        data = resp.json()
        if isinstance(data, dict):
            # 常见 OpenAI 格式: {"error": {"message": "..."}} 或 {"error": "..."}
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"]).strip()
            if isinstance(err, str) and err.strip():
                return err.strip()
            # 其他兼容网关格式: {"message": "..."} 或 {"detail": "..."}
            msg = data.get("message") or data.get("detail")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    except Exception:
        pass
    return f"AI 服务调用失败 (HTTP {resp.status_code})"


def _chat_completion(cfg: dict, messages: list[dict], temperature: float) -> str:
    """调用 OpenAI 兼容 /chat/completions, 返回回复文本。测试中会被替换。

    支持 Base URL 自动容错:
    若直接用 base_url 访问时返回非 JSON 网页(如返回 200 HTML 或 404),
    且 base_url 不以 /v1 结尾, 自动尝试 fallback 到 base_url + '/v1'。
    """
    timeout = float(os.environ.get("PAPERGUARD_AI_TIMEOUT", "60"))
    base_url = _normalize_base_url(cfg["base_url"])
    candidate_urls = [f"{base_url}/chat/completions"]
    if not base_url.endswith("/v1"):
        candidate_urls.append(f"{base_url}/v1/chat/completions")

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4000,
    }
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}

    last_error: HTTPException | None = None
    with httpx.Client(timeout=timeout) as client:
        for idx, url in enumerate(candidate_urls):
            is_last = (idx == len(candidate_urls) - 1)
            try:
                resp = client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise HTTPException(status_code=504, detail="AI 服务响应超时, 请稍后重试") from exc
            except httpx.HTTPError as exc:
                if not is_last:
                    continue
                raise HTTPException(status_code=502, detail="无法连接 AI 服务, 请检查 Base URL") from exc

            # 遇到 404 或 502/503 时, 如果有备选 candidate (/v1), 先尝试备选
            if resp.status_code in (404, 502, 503) and not is_last:
                continue

            if resp.status_code >= 400:
                err_detail = _parse_error_detail(resp)
                last_error = HTTPException(status_code=502, detail=err_detail)
                # 401 / 403 认证错误通常说明 endpoint 已经找对(或上游返回明确鉴权失败), 不必盲目 fallback
                # 但如果是 404 或特定网关未路由则继续
                if resp.status_code not in (404,) or is_last:
                    raise last_error
                continue

            # HTTP 200/2xx 响应, 尝试解析 choices
            try:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as exc:
                # 常见场景: 域名根目录是静态网站前端(返回 200 HTML), 不是 JSON
                if not is_last:
                    continue
                hint = "AI 服务返回了无法解析的内容"
                content_type = resp.headers.get("content-type", "")
                if "text/html" in content_type.lower():
                    hint = "AI 服务返回了 HTML 页面而非 API 数据, 若 Base URL 未包含「/v1」请尝试加上"
                raise HTTPException(status_code=502, detail=hint) from exc

    if last_error:
        raise last_error
    raise HTTPException(status_code=502, detail="无法连接 AI 服务")


def _build_messages(action: str, text: str, req: AssistRequest) -> list[dict]:
    system = (
        "你是一名中文学术论文写作助手, 熟悉学位论文写作规范。"
        "只输出处理后的正文纯文本, 不要任何解释、前缀、引号或 Markdown 标记。"
    )
    where = f"(当前章节: {req.section_title})" if req.section_title else ""
    if action == "polish":
        user = (
            f"请润色以下论文段落{where}: 保持原意、术语与事实不变, 修正语病并让表达更严谨流畅, "
            "不要增删信息。\n\n" + text
        )
        temperature = 0.3
    elif action == "continue":
        user = f"请续写以下论文段落{where}: 紧接文意再写 1 段(约 100-200 字), 不要重复已有内容, 直接输出续写部分。\n\n" + text
        temperature = 0.7
    elif action == "expand":
        user = f"请扩写以下论文段落{where}: 在保持原意的前提下补充论证与细节, 篇幅约为原文的 1.5-2 倍。\n\n" + text
        temperature = 0.7
    elif action == "abstract":
        user = "请根据以下论文正文内容, 撰写一段约 300 字的中文摘要, 概括研究目的、方法、结果与结论:\n\n" + text
        temperature = 0.5
    elif action == "english_abstract":
        user = (
            "Based on the following thesis body text, write an English abstract of about 200 words "
            "covering purpose, methods, results and conclusions. Output plain text only:\n\n" + text
        )
        temperature = 0.5
    else:  # keywords
        if req.section_type == "english_keywords":
            user = (
                "Extract 3-5 academic keywords from the following thesis body text. "
                "Separate them with \"; \". Output the keywords only:\n\n" + text
            )
        else:
            user = "请根据以下论文正文内容提炼 3-5 个学术关键词, 用「; 」分隔, 直接输出关键词:\n\n" + text
        temperature = 0.3
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], temperature


@router.get("/status")
def ai_status(user: dict = Depends(current_user), store: Store = Depends(lambda: Store())) -> dict:
    resolved = _resolve_cfg(store, user)
    if not resolved:
        return {"enabled": False, "source": None, "model": None}
    cfg, source = resolved
    return {"enabled": True, "source": source, "model": cfg["model"]}


@router.get("/config")
def get_ai_config(user: dict = Depends(current_user), store: Store = Depends(lambda: Store())) -> dict:
    cfg = store.get_user_ai_config(user["id"])
    if not cfg:
        return {"configured": False, "base_url": None, "model": None, "api_key_masked": None}
    return {
        "configured": True,
        "base_url": cfg.get("base_url"),
        "model": cfg.get("model"),
        "api_key_masked": _mask_key(str(cfg.get("api_key", ""))),
    }


@router.put("/config")
def save_ai_config(req: AiConfigRequest, user: dict = Depends(current_user),
                   store: Store = Depends(lambda: Store())) -> dict:
    store.set_user_ai_config(user["id"], {
        "base_url": req.base_url.strip().rstrip("/"),
        "api_key": req.api_key.strip(),
        "model": req.model.strip(),
    })
    return {"configured": True}


@router.delete("/config", status_code=204)
def clear_ai_config(user: dict = Depends(current_user), store: Store = Depends(lambda: Store())) -> None:
    store.set_user_ai_config(user["id"], None)


@router.post("/config/test")
def test_ai_config(req: AiConfigRequest, user: dict = Depends(current_user)) -> dict:
    cfg = {
        "base_url": req.base_url.strip().rstrip("/"),
        "api_key": req.api_key.strip(),
        "model": req.model.strip(),
    }
    try:
        _chat_completion(cfg, [{"role": "user", "content": "回复「ok」两个字"}], 0.0)
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}
    return {"ok": True, "model": cfg["model"]}


@router.post("/assist")
def ai_assist(req: AssistRequest, user: dict = Depends(current_user),
              store: Store = Depends(lambda: Store())) -> dict:
    if req.action not in _ACTIONS:
        raise HTTPException(status_code=400, detail=f"不支持的 AI 操作: {req.action}")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="内容为空, 无法执行 AI 操作")
    if len(req.text) > _MAX_INPUT_CHARS:
        raise HTTPException(status_code=400, detail=f"内容过长(上限 {_MAX_INPUT_CHARS} 字符)")
    resolved = _resolve_cfg(store, user)
    if not resolved:
        raise HTTPException(
            status_code=503,
            detail="AI 功能未启用: 请在「AI 设置」中填写自己的模型配置, 或联系管理员配置系统默认模型",
        )
    cfg, _source = resolved
    messages, temperature = _build_messages(req.action, req.text, req)
    result = _chat_completion(cfg, messages, temperature)
    return {"text": result}
