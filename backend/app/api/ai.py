"""AI 写作助手 API。

支持三级模型配置, 用户配置优先:
1. 用户自带: 写作页「AI 设置」保存到 users.ai_config (SQLite), 仅本人生效
2. 管理员默认: 管理员通过 Web UI 设置的系统默认模型 (system_settings 表), 全体用户共享
3. 环境变量兜底: 环境变量 PAPERGUARD_AI_BASE_URL / PAPERGUARD_AI_API_KEY / PAPERGUARD_AI_MODEL

所有补全请求走 OpenAI 兼容 /chat/completions 协议。
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user, require_admin
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
    # 密钥只写入服务端, 查询时不会回传明文。更新已存在的配置时允许留空,
    # 由保存/测试逻辑沿用服务端已有密钥。
    api_key: str | None = None
    model: str = Field(min_length=1)


def _system_env_cfg() -> dict | None:
    base_url = os.environ.get("PAPERGUARD_AI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("PAPERGUARD_AI_API_KEY", "").strip()
    model = os.environ.get("PAPERGUARD_AI_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None
    return {"base_url": base_url, "api_key": api_key, "model": model}


def _system_cfg(store: Store | None = None) -> tuple[dict, str] | None:
    """获取系统级默认模型配置: 优先数据库中的管理员设置, 其次环境变量。"""
    if store is not None:
        db_cfg = store.get_system_ai_config()
        if db_cfg and all(db_cfg.get(k) for k in ("base_url", "api_key", "model")):
            return {
                "base_url": str(db_cfg["base_url"]).strip().rstrip("/"),
                "api_key": str(db_cfg["api_key"]).strip(),
                "model": str(db_cfg["model"]).strip(),
            }, "system_db"
    env_cfg = _system_env_cfg()
    if env_cfg:
        return env_cfg, "system_env"
    return None


def _resolve_cfg(store: Store, user: dict) -> tuple[dict, str] | None:
    """返回 (cfg, source); 用户配置优先, 其次系统配置 (数据库 > 环境变量), 都无则 None。"""
    user_cfg = store.get_user_ai_config(user["id"])
    if user_cfg and all(user_cfg.get(k) for k in ("base_url", "api_key", "model")):
        return {
            "base_url": str(user_cfg["base_url"]).strip().rstrip("/"),
            "api_key": str(user_cfg["api_key"]).strip(),
            "model": str(user_cfg["model"]).strip(),
        }, "user"
    sys = _system_cfg(store)
    if sys:
        cfg, sub_source = sys
        # 统一对外归属为 "system" (或在状态中返回更具体的来源)
        return cfg, "system"
    return None


def _mask_key(api_key: str) -> str:
    if len(api_key) <= _MASKED_KEY_PREVIEW:
        return "***"
    return f"***{api_key[-_MASKED_KEY_PREVIEW:]}"


def _normalize_base_url(raw: str) -> str:
    """标准化 Base URL: 去除首尾空白与结尾斜杠。"""
    return raw.strip().rstrip("/")


def _is_anthropic_base_url(base_url: str) -> bool:
    """通过 Base URL 的路径识别 Anthropic 兼容服务。"""
    path = urlsplit(base_url).path.rstrip("/")
    return path.endswith("/anthropic") or path.endswith("/anthropic/v1")


def _anthropic_messages_url(base_url: str) -> str:
    """将 Anthropic Base URL 规范化为 Messages API 地址。"""
    parts = urlsplit(_normalize_base_url(base_url))
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = f"{path}/messages"
    else:
        path = f"{path}/v1/messages"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _anthropic_payload(cfg: dict, messages: list[dict], temperature: float) -> dict:
    """把内部的 system/user 消息转换为 Anthropic Messages 请求格式。"""
    system_parts = [
        str(message["content"])
        for message in messages
        if message.get("role") == "system" and message.get("content")
    ]
    anthropic_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if message.get("role") != "system"
    ]
    payload = {
        "model": cfg["model"],
        "messages": anthropic_messages,
        "temperature": temperature,
        "max_tokens": 4000,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    return payload


def _anthropic_response_text(data: dict) -> str:
    """从 Anthropic content 内容块中提取正文, 忽略 thinking 等非正文块。"""
    if not isinstance(data, dict):
        raise ValueError("响应不是 JSON 对象")
    content = data.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = [
            str(block["text"])
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        if text_parts:
            return "\n".join(text_parts).strip()
    raise ValueError("响应中没有可用的文本内容")


def _anthropic_completion(cfg: dict, messages: list[dict], temperature: float) -> str:
    """调用 Anthropic Messages API, 兼容 MiniMax /anthropic 地址。"""
    url = _anthropic_messages_url(cfg["base_url"])
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
    }
    payload = _anthropic_payload(cfg, messages, temperature)
    timeout = float(os.environ.get("PAPERGUARD_AI_TIMEOUT", "60"))
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="AI 服务响应超时, 请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="无法连接 AI 服务, 请检查 Base URL") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=_parse_error_detail(resp))
    try:
        return _anthropic_response_text(resp.json())
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="AI 服务返回了无法解析的内容") from exc


def _request_cfg(req: AiConfigRequest, fallback_cfg: dict | None = None) -> dict:
    """构造请求配置, 空密钥仅允许沿用服务端已有配置。"""
    base_url = _normalize_base_url(req.base_url)
    model = req.model.strip()
    api_key = (req.api_key or "").strip()
    if not api_key and fallback_cfg:
        api_key = str(fallback_cfg.get("api_key", "")).strip()
    missing = []
    if not base_url:
        missing.append("Base URL")
    if not api_key:
        missing.append("API Key")
    if not model:
        missing.append("模型")
    if missing:
        raise HTTPException(status_code=400, detail=f"请填写完整的 {' / '.join(missing)}")
    return {"base_url": base_url, "api_key": api_key, "model": model}


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
    """调用配置对应的 AI 协议, 返回回复文本。测试中会被替换。

    Anthropic Base URL (路径以 /anthropic 结尾) 走 /v1/messages;
    其他地址走 OpenAI 兼容 /chat/completions。

    OpenAI 兼容地址支持自动容错:
    若直接用 base_url 访问时返回非 JSON 网页(如返回 200 HTML 或 404),
    且 base_url 不以 /v1 结尾, 自动尝试 fallback 到 base_url + '/v1'。
    """
    if _is_anthropic_base_url(cfg["base_url"]):
        return _anthropic_completion(cfg, messages, temperature)

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
    if not cfg or not all(cfg.get(k) for k in ("base_url", "api_key", "model")):
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
    cfg = _request_cfg(req, store.get_user_ai_config(user["id"]))
    store.set_user_ai_config(user["id"], cfg)
    return {"configured": True}


@router.delete("/config", status_code=204)
def clear_ai_config(user: dict = Depends(current_user), store: Store = Depends(lambda: Store())) -> None:
    store.set_user_ai_config(user["id"], None)


@router.post("/config/test")
def test_ai_config(req: AiConfigRequest, user: dict = Depends(current_user),
                   store: Store = Depends(lambda: Store())) -> dict:
    cfg = _request_cfg(req, store.get_user_ai_config(user["id"]))
    try:
        _chat_completion(cfg, [{"role": "user", "content": "回复「ok」两个字"}], 0.0)
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}
    return {"ok": True, "model": cfg["model"]}


# ------------------- 管理员设置系统默认模型 -------------------

@router.get("/admin/config")
def get_admin_ai_config(admin: dict = Depends(require_admin),
                        store: Store = Depends(lambda: Store())) -> dict:
    """获取系统当前默认配置, 标明来自数据库还是环境变量。"""
    db_cfg = store.get_system_ai_config()
    env_cfg = _system_env_cfg()
    if db_cfg and all(db_cfg.get(k) for k in ("base_url", "api_key", "model")):
        return {
            "configured": True,
            "source": "database",
            "base_url": db_cfg.get("base_url"),
            "model": db_cfg.get("model"),
            "api_key_masked": _mask_key(str(db_cfg.get("api_key", ""))),
            "has_env_fallback": env_cfg is not None,
        }
    if env_cfg:
        return {
            "configured": True,
            "source": "environment",
            "base_url": env_cfg.get("base_url"),
            "model": env_cfg.get("model"),
            "api_key_masked": _mask_key(str(env_cfg.get("api_key", ""))),
            "has_env_fallback": True,
        }
    return {
        "configured": False,
        "source": None,
        "base_url": None,
        "model": None,
        "api_key_masked": None,
        "has_env_fallback": False,
    }


@router.put("/admin/config")
def save_admin_ai_config(req: AiConfigRequest, admin: dict = Depends(require_admin),
                         store: Store = Depends(lambda: Store())) -> dict:
    """管理员保存系统默认模型到数据库。"""
    # 已存在数据库配置或环境变量配置时, 管理员可以只修改 URL/模型, 留空表示保留密钥。
    fallback = _system_cfg(store)
    cfg = _request_cfg(req, fallback[0] if fallback else None)
    store.set_system_ai_config(cfg)
    return {"configured": True, "source": "database"}


@router.delete("/admin/config", status_code=204)
def clear_admin_ai_config(admin: dict = Depends(require_admin),
                          store: Store = Depends(lambda: Store())) -> None:
    """管理员清除数据库中的默认配置, 自动回退至环境变量或未配置。"""
    store.set_system_ai_config(None)


@router.post("/admin/config/test")
def test_admin_ai_config(req: AiConfigRequest, admin: dict = Depends(require_admin),
                         store: Store = Depends(lambda: Store())) -> dict:
    """管理员测试准备配置的系统默认模型。"""
    fallback = _system_cfg(store)
    cfg = _request_cfg(req, fallback[0] if fallback else None)
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
