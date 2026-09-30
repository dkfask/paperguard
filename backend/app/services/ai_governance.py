"""AI 调用的运行治理：限流、配额、用量记账。

为什么必须有这层：LLM 是**按量计费**资源。修复前 `/api/ai/assist` 没有任何
速率限制，也没有任何用量记录——意味着单个账号被盗或脚本失控即可烧光上游
额度，而运维事后无法回答「谁花了多少」，也就无法计费与按租户分摊。

对照之下，同一项目里连微信推送都做了 `mini_program_push_quotas` 表，
唯独成本更高的 LLM 调用没有。

三层约束：

1. **每日配额**（`PAPERGUARD_AI_DAILY_QUOTA`，默认 200 次/用户/UTC 日）
2. **每分钟突发**（`PAPERGUARD_AI_BURST_PER_MINUTE`，默认 10 次）
   防止把日额度在几分钟内一次性打光
3. **用量记账**：每次调用都落 `ai_usage`，成功失败都记——失败同样消耗上游成本

配额消费是**原子**的：一条带 `WHERE used < limit` 的 upsert 完成「判超限 + 计数」。
先查后写会有并发竞态，两个请求同时读到 used=limit-1 会双双通过检查。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.db import Store, new_id

# 默认值偏保守：正常写作场景一天几十次足够，异常时会很快撞线。
_DEFAULT_DAILY_QUOTA = 200
_DEFAULT_BURST_PER_MINUTE = 10


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def daily_quota() -> int:
    return _env_int("PAPERGUARD_AI_DAILY_QUOTA", _DEFAULT_DAILY_QUOTA)


def burst_per_minute() -> int:
    return _env_int("PAPERGUARD_AI_BURST_PER_MINUTE", _DEFAULT_BURST_PER_MINUTE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_periods(now: datetime) -> tuple[str, str]:
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M")


def _user_organization_id(user: dict) -> str | None:
    """调用时把学校归属落到用量记录上。

    不在汇总时再关联用户表：用户的学校归属之后可能变，一变历史用量的归属
    就跟着漂移，计费口径不成立。
    """
    context = user.get("organization_context")
    if isinstance(context, dict):
        school_id = context.get("school_id")
        if school_id:
            return str(school_id)
    return None


def begin_call(store: Store, user: dict) -> str:
    """进入 AI 调用前的准入检查；通过则返回本次的日期周期标识。

    顺序有讲究：先扣突发限制再扣日配额。突发是「瞬时行为」的闸，应当先拦；
    否则突发风暴会把日配额迅速吃光，用户连当天正常写作都做不了。
    """
    now = datetime.now(timezone.utc)
    day_period, minute_period = _utc_periods(now)
    stamp = _now()

    if not store.consume_ai_quota(user["id"], minute_period, stamp, burst_per_minute()):
        raise HTTPException(
            status_code=429,
            detail={
                "code": "AI_RATE_LIMITED",
                "message": f"操作过于频繁，请稍后再试（每分钟最多 {burst_per_minute()} 次）",
            },
        )

    if not store.consume_ai_quota(user["id"], day_period, stamp, daily_quota()):
        # 日配额没扣成，把刚才的突发计数还回去，否则用户会被两处同时卡住
        store.release_ai_quota(user["id"], minute_period, stamp)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "AI_DAILY_QUOTA_EXCEEDED",
                "message": (
                    f"今日 AI 调用次数已达上限（{daily_quota()} 次），"
                    "明日 0 时（UTC）重置"
                ),
            },
        )

    return day_period


def refund_call(store: Store, user: dict, day_period: str) -> None:
    """请求**尚未发往上游**就失败时归还额度。

    只有这种情况才退：出站校验拦截、参数校验等。真正发出去之后失败（超时、
    上游 5xx）**不退**——那种情况上游可能已经计费了。
    """
    stamp = _now()
    minute_period = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    store.release_ai_quota(user["id"], day_period, stamp)
    store.release_ai_quota(user["id"], minute_period, stamp)


def record_usage(
    store: Store,
    user: dict,
    *,
    action: str,
    source: str,
    model: str,
    base_url: str,
    input_chars: int,
    output_chars: int,
    result: str,
) -> None:
    """记录一次调用。成功与失败都记——失败同样产生成本。"""
    try:
        host = urlsplit(base_url).hostname or ""
    except ValueError:
        host = ""
    store.record_ai_usage(
        {
            "id": new_id("aiusage"),
            "user_id": user["id"],
            "organization_id": _user_organization_id(user),
            "action": action,
            "source": source,
            "model": model,
            "base_url_host": host,
            "input_chars": input_chars,
            "output_chars": output_chars,
            "result": result,
            "created_at": _now(),
        }
    )
