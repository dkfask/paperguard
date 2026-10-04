"""AI 写作助手 API。

支持三级模型配置, 用户配置优先:
1. 用户自带: 写作页「AI 设置」保存到 users.ai_config (SQLite), 仅本人生效
2. 管理员默认: 管理员通过 Web UI 设置的系统默认模型 (system_settings 表), 全体用户共享
3. 环境变量兜底: 环境变量 PAPERGUARD_AI_BASE_URL / PAPERGUARD_AI_API_KEY / PAPERGUARD_AI_MODEL

所有补全请求走 OpenAI 兼容 /chat/completions 协议。
"""

from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user, require_admin
from app.db import Store, new_id
from app.services import ai_breaker, ai_governance, secret_store, ai_usage_facts
from app.services.outbound_guard import assert_safe_outbound_url

router = APIRouter(prefix="/api/ai")


def get_store() -> Store:
    """Store 依赖（与项目其余路由一致的注入方式）。"""
    return Store()


_ACTIONS = ("polish", "continue", "expand", "abstract", "english_abstract", "keywords")
_MAX_INPUT_CHARS = 12000
_MASKED_KEY_PREVIEW = 4
# 全部协议函数与 _metered_chat 的默认 max_tokens。集中定义以便
# `_metered_chat` 判断「调用方是否用了默认值」，从而在默认路径上
# 保持 3 参调用形状（兼容既有 3 参测试替身与外部包装）。
_DEFAULT_MAX_TOKENS = 4000


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


def _system_cfg(
    store: Store | None = None, organization_id: str = ""
) -> tuple[dict, str] | None:
    """获取系统级默认模型配置: 优先该校的数据库设置, 其次平台全局默认, 最后环境变量。"""
    if store is not None:
        try:
            db_cfg = store.get_system_ai_config(organization_id)
        except secret_store.SecretDecryptError as exc:
            raise HTTPException(status_code=503, detail={
                "code": "AI_SECRET_UNAVAILABLE", "message": "AI 凭据不可解密，已拒绝回退到其他供应商"
            }) from exc
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


def _tenant_or_school_of(user: dict | None) -> tuple[str, bool]:
    context = (user or {}).get("organization_context") or {}
    tenant_id = str((user or {}).get("tenant_id") or context.get("tenant_id") or "").strip()
    if tenant_id:
        return tenant_id, True
    return str(context.get("school_id") or ""), False


def _school_of(user: dict | None) -> str:
    """取用户所属学校 ID，作为默认模型配置的隔离维度。

    取不到学校（未分配组织）时返回空串，即回退到平台全局默认——保持
    与修复前一致的行为，不让「组织信息不全」的用户直接失去 AI 功能。
    """
    context = (user or {}).get("organization_context") or {}
    return str(context.get("school_id") or "")


def _resolve_cfg(store: Store, user: dict) -> tuple[dict, str] | None:
    """返回 (cfg, source); 用户配置优先, 其次系统配置 (数据库 > 环境变量), 都无则 None。

    系统配置按**用户所属学校**解析（S2）：一套部署服务多个学校时，各校
    应当能用自己的模型供应商，论文正文才不会全部流向同一家外部处理者。
    本校没配则回退到平台全局默认。
    """
    scope_id, explicit_tenant = _tenant_or_school_of(user)
    if explicit_tenant:
        tenant = store.get_tenant(scope_id)
        member = store.get_membership_for_user(user["id"], scope_id)
        if not tenant or tenant.get("status") != "active" or member is None or user.get("status") != "active":
            # Validate authorization before reading any credentials, including BYOK.
            raise HTTPException(status_code=403, detail={
                "code": "TENANT_CONTEXT_INVALID", "message": "租户上下文无效，禁止读取或回退 AI 配置"
            })
        # Only a validated legacy membership may reuse the old school config
        # key. The prefix is a config alias, never authorization evidence.
        school_id = _school_of(user)
        if school_id and scope_id == f"tenant-legacy-{school_id}":
            scope_id = school_id
    elif store.list_memberships_for_user(user["id"]):
        # Revoked, suspended or ambiguous memberships cannot fall back into
        # the pre-tenancy compatibility path merely by omitting a tenant hint.
        raise HTTPException(status_code=403, detail={
            "code": "TENANT_CONTEXT_INVALID", "message": "请先选择有效的租户成员身份"
        })
    try:
        user_cfg = store.get_user_ai_config(user["id"])
    except secret_store.SecretDecryptError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "AI_SECRET_UNAVAILABLE", "message": "AI 凭据不可解密，请联系管理员完成密钥迁移"
        }) from exc
    if user_cfg and all(user_cfg.get(k) for k in ("base_url", "api_key", "model")):
        return {
            "base_url": str(user_cfg["base_url"]).strip().rstrip("/"),
            "api_key": str(user_cfg["api_key"]).strip(),
            "model": str(user_cfg["model"]).strip(),
        }, "user"
    sys = _system_cfg(store, scope_id)
    if sys:
        cfg, sub_source = sys
        # 统一对外归属为 "system" (或在状态中返回更具体的来源)
        return cfg, "system"
    return None


def _mask_key(api_key: str) -> str:
    if len(api_key) <= _MASKED_KEY_PREVIEW:
        return "***"
    return f"***{api_key[-_MASKED_KEY_PREVIEW:]}"


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit_ai_config(
    store: Store,
    actor: dict,
    *,
    action: str,
    scope: str,
    result: str = "success",
    reason: str = "",
) -> None:
    """记录 AI 配置变更。

    为什么必须记：AI 配置决定了**用户数据流向哪个外部供应商**。改一个
    base_url 就等于把整份论文正文改道到另一个处理者，而这类变更在任何
    SaaS 合规视角下都属于必须可追溯的事件——修复前 ai.py 里对 `_audit`
    零调用，同一代码库的 `document_projects.py` 却对每一次项目变更都在记。

    审计里**只放元数据**：base_url 的 host 与 model 名，不放 API Key，
    也不放任何论文内容。审计流本身是管理端可见的，凭据不该进这里。
    """
    context = actor.get("organization_context") or {}
    try:
        store.save_audit_event({
            "id": new_id("audit"),
            "actor_id": str(actor.get("id") or ""),
            "action": action,
            "resource_type": f"ai_config:{scope}",
            "resource_id": str(actor.get("id") or scope),
            "organization_id": str(context.get("school_id") or ""),
            "result": result,
            "reason": reason,
            "created_at": _now(),
        })
    except Exception:  # 审计失败不得让配置本身写不进去
        import logging

        logging.getLogger("paperguard.ai").exception("AI 配置审计写入失败: %s", action)


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


def _anthropic_payload(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> dict:
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
        "max_tokens": max_tokens,
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


def _anthropic_completion(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> str:
    """调用 Anthropic Messages API, 兼容 MiniMax /anthropic 地址。"""
    url = _anthropic_messages_url(cfg["base_url"])
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
    }
    payload = _anthropic_payload(cfg, messages, temperature, max_tokens)
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
        data = resp.json()
        return ai_usage_facts.completion(_anthropic_response_text(data), protocol="anthropic", data=data)
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="AI 服务返回了无法解析的内容") from exc


def _request_cfg(req: AiConfigRequest, fallback_cfg: dict | None = None) -> dict:
    """构造请求配置, 空密钥仅允许沿用服务端已有配置。"""
    # 保存时只做语法级检查（resolve_dns=False），让用户在保存阶段就收到
    # 明确反馈，并拦住「主机就是内网 IP」这种一眼可判的情况。
    # 主机名的 DNS 解析不在这里做：否则保存成败取决于 DNS 可用性。
    # **安全边界在请求前**（见 _chat_completion / _anthropic_completion）。
    base_url = _normalize_base_url(
        assert_safe_outbound_url(req.base_url, label="Base URL", resolve_dns=False)
    )
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


def _breaker_key(cfg: dict) -> str:
    """隔离熔断状态，避免一个 BYOK 凭据拖垮其他租户。

    只把凭据的不可逆摘要放进进程内键，绝不把 API Key 写入日志或状态。
    ``provider/base_url/model/credential`` 共同构成上游身份；同一供应商的
    不同租户密钥故障不会互相打开熔断。
    """
    base_url = _normalize_base_url(cfg.get("base_url", "")).lower()
    provider = str(cfg.get("provider") or urlsplit(base_url).hostname or "").lower()
    model = str(cfg.get("model") or "")
    credential_digest = hashlib.sha256(str(cfg.get("api_key") or "").encode("utf-8")).hexdigest()[:16]
    return f"{provider}|{base_url}|{model}|cred:{credential_digest}"


def _chat_completion(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> str:
    """调用配置对应的 AI 协议, 返回回复文本。测试中会被替换。

    Anthropic Base URL (路径以 /anthropic 结尾) 走 /v1/messages;
    其他地址走 OpenAI 兼容 /chat/completions。

    OpenAI 兼容地址支持自动容错:
    若直接用 base_url 访问时返回非 JSON 网页(如返回 200 HTML 或 404),
    且 base_url 不以 /v1 结尾, 自动尝试 fallback 到 base_url + '/v1'。

    熔断放在这里（而不是两个协议函数各自实现）是因为它要覆盖**全部**
    上游调用路径，且只有在这一层才能同时看到成功与失败。
    """
    key = _breaker_key(cfg)
    allowed, reason = ai_breaker.allow(key)
    if not allowed:
        # 熔断打开时立刻返回，不再等 60 秒超时——这正是修复的目的。
        raise HTTPException(status_code=503, detail={"code": "AI_UPSTREAM_CIRCUIT_OPEN", "message": reason})
    try:
        text = _dispatch_completion(cfg, messages, temperature, max_tokens)
    except HTTPException:
        ai_breaker.record_failure(key)
        raise
    except Exception:
        ai_breaker.record_failure(key)
        raise
    ai_breaker.record_success(key)
    return text


def _ai_identity(store: Store, user: dict) -> dict:
    """Resolve the persisted idempotency domain for this dispatch.

    A client tenant hint selects a persisted membership; it never creates one.
    Platform scope is available only from an explicit platform binding. An
    ordinary global/admin user remains in the isolated legacy domain.
    """
    from app.services.saas_identity import platform_binding
    from app.services.tenant_access import effective_capabilities, resolve_membership

    persisted = store.get_user(str(user.get("id") or "")) if user.get("id") else None
    current = dict(persisted or user)
    # `tenant_id` is a request-context selector and is intentionally not a
    # persisted user grant. Preserve it while refreshing account state above.
    for key in ("tenant_id",):
        if key in user:
            current[key] = user[key]
    membership = resolve_membership(store, current)
    tenant_hint = current.get("tenant_id") or (current.get("organization_context") or {}).get("tenant_id")
    if membership is None and (tenant_hint or store.list_memberships_for_user(str(current.get("id") or ""))):
        raise HTTPException(status_code=403, detail={
            "code": "TENANT_CONTEXT_INVALID", "message": "租户上下文无效",
        })
    if membership is not None:
        capabilities = effective_capabilities(current, membership)
        if "tenant:read" not in capabilities:
            raise HTTPException(status_code=403, detail={
                "code": "CAPABILITY_REQUIRED", "capability": "tenant:read", "message": "成员能力不足",
            })
        tenant_id = str(membership["tenant_id"])
        membership_id = str(membership["id"])
        return {
            "user": current, "domain": "tenant", "scope": f"{tenant_id}:{membership_id}",
            "tenant_id": tenant_id, "membership_id": membership_id,
            "auth_version": int(membership.get("auth_version") or 0),
        }

    binding = platform_binding(store, str(current.get("id") or "")) if current.get("id") else None
    if binding and current.get("status") == "active" and not current.get("must_change_password"):
        return {
            "user": current, "domain": "platform", "scope": str(current["id"]),
            "tenant_id": None, "membership_id": None,
            "auth_version": int(binding.get("auth_version") or 0),
        }
    return {
        "user": current, "domain": "legacy", "scope": str(current.get("id") or user.get("id") or ""),
        "tenant_id": None, "membership_id": None, "auth_version": 0,
    }


def _metered_chat(
    store: Store,
    user: dict,
    cfg: dict,
    messages: list[dict],
    temperature: float,
    *,
    action: str,
    source: str,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    idempotency_key: str | None = None,
) -> str:
    """真实上游调用的唯一计量出口，并绑定显式幂等授权域。

    The registration transaction completes before provider dispatch. This is
    intentional: a concurrent duplicate observes the durable request and can
    never become a second provider dispatcher. Unknown outcomes remain terminal
    for automatic retry purposes and require reconciliation.
    """
    assert_safe_outbound_url(cfg["base_url"], label="Base URL")
    input_chars = sum(len(str(m.get("content", ""))) for m in messages)
    model = str(cfg.get("model", ""))
    base_url = str(cfg.get("base_url", ""))
    identity = _ai_identity(store, user)
    current_user = identity["user"]
    tenant_id = identity["tenant_id"]
    membership_id = identity["membership_id"]
    auth_version = identity["auth_version"]
    key = idempotency_key.strip() if idempotency_key and idempotency_key.strip() else None
    fingerprint = {
        "action": action, "tenant_id": tenant_id, "membership_id": membership_id,
        "idempotency_domain": identity["domain"], "idempotency_scope": identity["scope"],
        "auth_version": auth_version, "model": model, "base_url": base_url,
        "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
        "source": source,
        "credential_digest": hashlib.sha256(str(cfg.get("api_key") or "").encode()).hexdigest(),
    }
    request_hash = hashlib.sha256(json.dumps(
        fingerprint, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()

    def replay_existing(row: dict) -> str:
        # Re-check current authorization before returning a stored result. The
        # caller's original tenant/member/version is never trusted on replay.
        latest_user = store.get_user(str(user.get("id") or "")) if user.get("id") else None
        if latest_user and (latest_user.get("status") != "active" or latest_user.get("must_change_password")):
            raise HTTPException(status_code=403, detail={
                "code": "AUTHORIZATION_STALE", "message": "账号授权状态已变化，请重新解析授权",
            })
        latest = _ai_identity(store, user)
        if (
            row.get("idempotency_domain") != latest["domain"]
            or row.get("idempotency_scope") != latest["scope"]
            or row.get("action") != action
            or row.get("tenant_id") != latest["tenant_id"]
            or row.get("membership_id") != latest["membership_id"]
            or int(row.get("auth_version") or 0) != int(latest["auth_version"])
            or row.get("request_hash") != request_hash
        ):
            if int(row.get("auth_version") or 0) != int(latest["auth_version"]):
                raise HTTPException(status_code=403, detail={
                    "code": "AUTHORIZATION_STALE", "message": "成员授权或版本已变化，请重新解析授权",
                })
            raise HTTPException(status_code=409, detail={
                "code": "IDEMPOTENCY_KEY_REUSED", "message": "幂等键已用于另一份请求或授权范围",
            })
        if row.get("status") == "completed":
            return str(row.get("response_text") or "")
        if row.get("status") in {"unknown", "reconciliation_required"}:
            raise HTTPException(status_code=409, detail={
                "code": "AI_RECONCILIATION_REQUIRED", "message": "上游结果未知，需要人工复核，不可重发",
            })
        raise HTTPException(status_code=409, detail={
            "code": "REQUEST_IN_PROGRESS", "message": "相同幂等请求不能再次派发",
        })

    request_payload = {
        "id": new_id("aireq"), "idempotency_key": key, "request_hash": request_hash,
        "user_id": str(current_user.get("id") or user.get("id") or ""),
        "tenant_id": tenant_id, "membership_id": membership_id, "action": action,
        "idempotency_domain": identity["domain"], "idempotency_scope": identity["scope"],
        "auth_version": auth_version,
        "provider": urlsplit(base_url).hostname or "", "base_url_host": urlsplit(base_url).hostname or "",
        "model": model, "status": "reserved", "reservation_status": "reserved",
    }
    request_row, created = store.register_ai_request(request_payload)
    if not created:
        return replay_existing(request_row)
    request_id = request_row["id"]
    try:
        ai_governance.begin_call(store, current_user)
    except HTTPException as exc:
        store.update_ai_request(
            request_id, status="failed_final", reservation_status="released",
            error_code=str((exc.detail or {}).get("code") if isinstance(exc.detail, dict) else "QUOTA_REJECTED"),
        )
        raise
    store.update_ai_request(request_id, status="dispatching", reservation_status="consumed")
    input_tokens = max(1, input_chars // 4) if input_chars else 0
    try:
        if max_tokens == _DEFAULT_MAX_TOKENS:
            result = _chat_completion(cfg, messages, temperature)
        else:
            result = _chat_completion(cfg, messages, temperature, max_tokens=max_tokens)
    except HTTPException as exc:
        unknown = exc.status_code in {502, 504}
        store.update_ai_request(
            request_id, status="unknown" if unknown else "failed_final",
            reservation_status="unknown" if unknown else "consumed",
            error_code=str((exc.detail or {}).get("code") if isinstance(exc.detail, dict) else exc.status_code),
            input_tokens=input_tokens, output_tokens=0,
        )
        ai_governance.record_usage(
            store, current_user, action=action, source=source, model=model,
            base_url=base_url, input_chars=input_chars, output_chars=0, result="upstream_error",
        )
        raise
    except Exception:
        store.update_ai_request(
            request_id, status="unknown", reservation_status="unknown",
            error_code="AI_RESULT_UNKNOWN", input_tokens=input_tokens,
        )
        raise HTTPException(status_code=502, detail={
            "code": "AI_RESULT_UNKNOWN", "message": "上游结果无法确认，请勿重发，需人工复核",
        }) from None
    output_tokens = 0
    try:
        output_tokens = max(1, len(result) // 4) if result else 0
        ai_governance.record_usage(
            store, current_user, action=action, source=source, model=model,
            base_url=base_url, input_chars=input_chars, output_chars=len(result), result="success",
        )
        ai_usage_facts.complete_request(
            store, request_id, result, expected_membership=identity["membership_id"] and {
                "id": identity["membership_id"], "auth_version": identity["auth_version"],
            } or None,
            input_estimate=input_tokens, output_estimate=output_tokens,
        )
    except Exception:
        store.update_ai_request(
            request_id, status="unknown", reservation_status="unknown", error_code="AI_RESULT_UNKNOWN",
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
        raise HTTPException(status_code=502, detail={
            "code": "AI_RESULT_UNKNOWN", "message": "结果记录未完成，请勿重发，需人工复核",
        }) from None
    return result


def _dispatch_completion(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> str:
    if _is_anthropic_base_url(cfg["base_url"]):
        return _anthropic_completion(cfg, messages, temperature, max_tokens)

    timeout = float(os.environ.get("PAPERGUARD_AI_TIMEOUT", "60"))
    base_url = _normalize_base_url(cfg["base_url"])
    candidate_urls = [f"{base_url}/chat/completions"]
    if not base_url.endswith("/v1"):
        candidate_urls.append(f"{base_url}/v1/chat/completions")

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
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
                text = data["choices"][0]["message"]["content"].strip()
                return ai_usage_facts.completion(text, protocol="openai", data=data)
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
def ai_status(user: dict = Depends(current_user), store: Store = Depends(get_store)) -> dict:
    resolved = _resolve_cfg(store, user)
    if not resolved:
        return {"enabled": False, "source": None, "model": None}
    cfg, source = resolved
    return {"enabled": True, "source": source, "model": cfg["model"]}


@router.get("/disclosure")
def ai_disclosure(user: dict = Depends(current_user), store: Store = Depends(get_store)) -> dict:
    """当前用户使用 AI 功能时的数据出境与委托处理披露（S6）。

    披露内容由服务端给出而不是写死在界面文案里，理由有二：
    1. **接收方随配置而变**——用户可能自带 key 指向别家供应商，
       前端写死「发送给默认服务商」就成了假披露。真正生效的接收方只有
       服务端能算出来（见 `_resolve_cfg`）。
    2. 学校要导出这份披露给师生/合规审计时，读接口比抓界面可靠。

    只返回主机名，不含路径、密钥与任何论文内容。
    """
    resolved = _resolve_cfg(store, user)
    recipient = ""
    source = None
    model = None
    if resolved:
        cfg, source = resolved
        model = cfg.get("model")
        try:
            recipient = urlsplit(cfg["base_url"]).hostname or ""
        except ValueError:
            recipient = ""
    return {
        "enabled": bool(resolved),
        "source": source,
        "model": model,
        "data_recipient_host": recipient,
        "content_sent": "所选论文段落文本" if resolved else "",
        "notice": (
            "使用 AI 功能时，所选论文段落文本会发送给你所填的服务商处理，"
            "内容将离开本系统并可能存储在其境内。请确认该服务商已获授权，"
            "并满足学校对数据出境与委托处理的要求。"
        ),
    }


@router.get("/config")
def get_ai_config(user: dict = Depends(current_user), store: Store = Depends(get_store)) -> dict:
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
                   store: Store = Depends(get_store)) -> dict:
    cfg = _request_cfg(req, store.get_user_ai_config(user["id"]))
    store.set_user_ai_config(user["id"], cfg)
    _audit_ai_config(
        store, user,
        action="ai.config.saved",
        scope="user",
        reason=f"host:{urlsplit(cfg['base_url']).hostname} model:{cfg['model']}",
    )
    return {"configured": True}


@router.delete("/config", status_code=204)
def clear_ai_config(user: dict = Depends(current_user), store: Store = Depends(get_store)) -> None:
    store.set_user_ai_config(user["id"], None)
    _audit_ai_config(store, user, action="ai.config.cleared", scope="user")


@router.post("/config/test")
def test_ai_config(req: AiConfigRequest, user: dict = Depends(current_user),
                   store: Store = Depends(get_store)) -> dict:
    cfg = _request_cfg(req, store.get_user_ai_config(user["id"]))
    try:
        # 测试连通性会真的发请求，出口必须完整校验，且必须计量——
        # 否则这里就是一个不占配额、不留记录的免费上游入口。
        _metered_chat(
            store, user, cfg,
            [{"role": "user", "content": "回复「ok」两个字"}], 0.0,
            action="config_test", source="user",
        )
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}
    return {"ok": True, "model": cfg["model"]}


# ------------------- 管理员设置系统默认模型 -------------------

@router.get("/admin/config")
def get_admin_ai_config(admin: dict = Depends(require_admin),
                        store: Store = Depends(get_store),
                        organization_id: str = "") -> dict:
    """获取默认配置, 标明来自数据库还是环境变量。

    `organization_id` 指定学校：不传则读平台全局默认（S2）。
    """
    db_cfg = store.get_system_ai_config(organization_id)
    env_cfg = _system_env_cfg()
    # 三个分支都要带这两个字段：否则「已有哪些学校单独配过」会随配置来源
    # 忽隐忽现，管理员在环境变量模式下就看不到已有的租户覆盖。
    common = {
        "organization_id": organization_id,
        "tenant_overrides": store.list_system_settings_organizations("ai_default_config"),
    }
    if db_cfg and all(db_cfg.get(k) for k in ("base_url", "api_key", "model")):
        return {
            "configured": True,
            "source": "database",
            "base_url": db_cfg.get("base_url"),
            "model": db_cfg.get("model"),
            "api_key_masked": _mask_key(str(db_cfg.get("api_key", ""))),
            "has_env_fallback": env_cfg is not None,
            **common,
        }
    if env_cfg:
        return {
            "configured": True,
            "source": "environment",
            "base_url": env_cfg.get("base_url"),
            "model": env_cfg.get("model"),
            "api_key_masked": _mask_key(str(env_cfg.get("api_key", ""))),
            "has_env_fallback": True,
            **common,
        }
    return {
        "configured": False,
        "source": None,
        "base_url": None,
        "model": None,
        "api_key_masked": None,
        "has_env_fallback": False,
        **common,
    }


@router.put("/admin/config")
def save_admin_ai_config(req: AiConfigRequest, admin: dict = Depends(require_admin),
                         store: Store = Depends(get_store),
                         organization_id: str = "") -> dict:
    """管理员保存系统默认模型到数据库。

    `organization_id` 指定学校时写入该校自己的配置（S2 租户隔离），
    不传则写入平台全局默认。
    """
    # 已存在数据库配置或环境变量配置时, 管理员可以只修改 URL/模型, 留空表示保留密钥。
    fallback = _system_cfg(store, organization_id)
    cfg = _request_cfg(req, fallback[0] if fallback else None)
    store.set_system_ai_config(cfg, organization_id)
    _audit_ai_config(
        store, admin,
        action="ai.config.system_saved",
        scope=f"system:{organization_id}" if organization_id else "system",
        reason=(
            f"org:{organization_id} " if organization_id else ""
        ) + f"host:{urlsplit(cfg['base_url']).hostname} model:{cfg['model']}",
    )
    return {"configured": True, "source": "database", "organization_id": organization_id}


@router.delete("/admin/config", status_code=204)
def clear_admin_ai_config(admin: dict = Depends(require_admin),
                          store: Store = Depends(get_store),
                          organization_id: str = "") -> None:
    """管理员清除默认配置, 自动回退至上一级或未配置。"""
    store.set_system_ai_config(None, organization_id)
    _audit_ai_config(
        store, admin,
        action="ai.config.system_cleared",
        scope=f"system:{organization_id}" if organization_id else "system",
    )


@router.post("/admin/config/test")
def test_admin_ai_config(req: AiConfigRequest, admin: dict = Depends(require_admin),
                         store: Store = Depends(get_store),
                         organization_id: str = "") -> dict:
    """管理员测试准备配置的系统默认模型。"""
    fallback = _system_cfg(store, organization_id)
    cfg = _request_cfg(req, fallback[0] if fallback else None)
    source = fallback[1] if fallback else "system"
    try:
        # 同 test_ai_config：真实消耗上游，必须走计量出口。
        # source 记为系统级，平台据此分辨「这笔是管理员测平台配置产生的」。
        _metered_chat(
            store, admin, cfg,
            [{"role": "user", "content": "回复「ok」两个字"}], 0.0,
            action="admin_config_test", source=source,
        )
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}
    return {"ok": True, "model": cfg["model"]}


@router.post("/assist")
def ai_assist(req: AssistRequest, user: dict = Depends(current_user),
              store: Store = Depends(get_store),
              idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict:
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
    cfg, source = resolved

    # 计量与出口校验统一收敛在 _metered_chat，不再在本处理器内联——
    # 修复前计量逻辑只写在这里，导致其余 5 个真实上游调用点全部绕过配额。
    # 顺序与语义保持不变：出站守卫 → 扣配额 → 派发 → 记账（失败记 upstream_error 且不退还）。
    messages, temperature = _build_messages(req.action, req.text, req)
    result = _metered_chat(
        store, user, cfg, messages, temperature,
        action=req.action, source=source, idempotency_key=idempotency_key,
    )
    return {"text": result}
