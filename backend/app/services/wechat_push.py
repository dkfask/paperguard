"""微信订阅消息渠道 — 消费已投递事件, 按模板配额推送服务通知。

- 一次性订阅: 用户每次在小程序内授权, 记 1 次推送机会(mini_program_push_quotas)
- 事件按 mini_program_push_log 幂等去重, 失败不阻塞站内消息(outbox 主链路不变)
- 模板映射经 PAPERGUARD_WECHAT_SUBSCRIBE_TEMPLATES 配置, 例:
  {"submission.returned": "TMPL_A", "submission.approved": "TMPL_A", "submission.created": "TMPL_B"}
  模板需包含 thing1 字段(消息正文); page 按事件落到对应小程序页。
- 截止/逾期催办在发送前复核参与者状态: 已通过/退出/撤回/归档, 或延期后不再逾期的旧催办, 只保留站内信。
目标主机固定为微信官方 API 域名, 不接受任何运行时拼接的地址。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.auth import _openid_from_cipher
from app.core.context import RequestContext
from app.core.structured_logging import log_event
from app.db import Store, new_id
from app.domain.workflow_states import ACTIVE_PARTICIPANT_STATES

LOGGER = logging.getLogger("paperguard.wechat-push")

_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"
_PAGE = "/pages/tasks/index"
_TOKEN_CACHE: dict[str, Any] = {}
_SEND_WINDOW_HOURS = 24
_BATCH_LIMIT = 50
_REMINDER_EVENTS = frozenset({"participant.overdue", "participant.deadline_soon"})
_DEADLINE_SOON_SECONDS = 72 * 3600


def _configured() -> tuple[bool, dict[str, str]]:
    app_id = os.environ.get("PAPERGUARD_WECHAT_APP_ID", "").strip()
    secret = os.environ.get("PAPERGUARD_WECHAT_APP_SECRET", "").strip()
    raw = os.environ.get("PAPERGUARD_WECHAT_SUBSCRIBE_TEMPLATES", "").strip()
    templates: dict[str, str] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            templates = {str(key): str(value) for key, value in parsed.items()} if isinstance(parsed, dict) else {}
        except ValueError:
            LOGGER.warning("PAPERGUARD_WECHAT_SUBSCRIBE_TEMPLATES 不是合法 JSON, 忽略订阅推送配置")
    return bool(app_id and secret and templates), templates


def subscribe_page(event: dict) -> str:
    """卡片落地页: 审核结果→提交详情, 待审→审阅, 批注→意见, 截止催办→任务。"""
    event_type = str(event.get("event_type") or "")
    resource_type = str(event.get("resource_type") or "")
    resource_id = str(event.get("resource_id") or "").strip()
    if event_type in {"submission.returned", "submission.approved"} and resource_id:
        return f"/pages/submissions/detail?id={resource_id}"
    if event_type == "submission.created" and resource_id:
        return f"/pages/review/index?id={resource_id}"
    if event_type == "comment.created" and resource_id:
        return f"/pages/submissions/comments?id={resource_id}"
    if event_type == "comment.responded":
        return _PAGE
    if resource_type == "participant" or event_type.startswith("participant."):
        return _PAGE
    return _PAGE


def _parse_deadline(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def should_skip_stale_push(event: dict, participant: dict | None, *, now: datetime) -> bool:
    """发送前复核: 非活跃任务或延期后不再逾期/临近的催办不再发微信。"""
    event_type = str(event.get("event_type") or "")
    if event_type not in _REMINDER_EVENTS:
        return False
    if not participant:
        return True
    if participant.get("state") not in ACTIVE_PARTICIPANT_STATES:
        return True
    deadline = _parse_deadline(
        str(participant.get("revision_deadline_at") or participant.get("effective_deadline_at") or "")
    )
    if deadline is None:
        return True
    remaining = (deadline - now).total_seconds()
    if event_type == "participant.overdue":
        return remaining >= 0
    return remaining > _DEADLINE_SOON_SECONDS or remaining < 0


def _reminder_participant(store: Store, event: dict) -> dict | None:
    if str(event.get("resource_type") or "") != "participant":
        return None
    resource_id = str(event.get("resource_id") or "").strip()
    if not resource_id:
        return None
    return store.get_workflow_record("participant", resource_id)


def fetch_access_token(app_id: str, secret: str) -> str:
    cached = _TOKEN_CACHE.get("token")
    expires = float(_TOKEN_CACHE.get("expires_at", 0))
    if cached and _now_epoch() < expires:
        return str(cached)
    response = httpx.get(
        _TOKEN_URL,
        params={"grant_type": "client_credential", "appid": app_id, "secret": secret},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise RuntimeError(f"获取微信 access_token 失败: {payload.get('errcode')} {payload.get('errmsg')}")
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = _now_epoch() + int(payload.get("expires_in", 7200)) - 120
    return token


def send_subscribe(access_token: str, openid: str, template_id: str, title: str, page: str = _PAGE) -> tuple[bool, str]:
    response = httpx.post(
        _SEND_URL,
        params={"access_token": access_token},
        json={
            "touser": openid,
            "template_id": template_id,
            "page": page or _PAGE,
            "data": {"thing1": {"value": title[:20]}},
        },
        timeout=10,
    )
    payload = response.json()
    errcode = str(payload.get("errcode", 0))
    return errcode == "0", errcode


def deliver_wechat_push(store: Store, *, consumer_id: str, now: datetime | None = None) -> dict:
    """扫描近期已投递事件, 对有绑定且有余量配额的接收人发送订阅消息。"""
    now = now or datetime.now(timezone.utc)
    context = RequestContext(actor_id="system", role="system", request_id=f"wechat-push-{consumer_id}")
    configured, templates = _configured()
    if not configured:
        return {"skipped": "not_configured", "sent": 0, "failed": 0, "skipped_events": 0}
    since = (now - timedelta(hours=_SEND_WINDOW_HOURS)).isoformat(timespec="seconds")
    app_id = os.environ.get("PAPERGUARD_WECHAT_APP_ID", "").strip()
    secret = os.environ.get("PAPERGUARD_WECHAT_APP_SECRET", "").strip()
    sent = failed = skipped = 0
    try:
        token = fetch_access_token(app_id, secret)
    except Exception as exc:
        log_event(LOGGER, "wechat_push.token_failed", context=context, error=type(exc).__name__)
        return {"skipped": "token_failed", "sent": 0, "failed": 0, "skipped_events": 0}
    for event in store.list_delivered_push_events(since=since, limit=_BATCH_LIMIT):
        template_id = templates.get(event["event_type"])
        if not template_id or store.has_mini_program_push(event["id"]):
            continue
        if should_skip_stale_push(event, _reminder_participant(store, event), now=now):
            store.record_mini_program_push({
                "id": new_id("mp-push"),
                "event_id": event["id"],
                "recipient_id": event["recipient_id"],
                "template_id": template_id,
                "status": "skipped_stale",
                "error_code": "",
                "created_at": now.isoformat(timespec="seconds"),
            })
            skipped += 1
            continue
        binding = store.get_mini_program_binding_by_user(event["recipient_id"])
        status, error_code = "skipped_no_binding", ""
        if binding and binding.get("openid_cipher"):
            try:
                openid = _openid_from_cipher(str(binding["openid_cipher"]))
            except Exception:
                openid = ""
            if openid:
                if not store.consume_push_quota(event["recipient_id"], template_id, now.isoformat(timespec="seconds")):
                    status, error_code = "skipped_not_subscribed", "43101"
                else:
                    title = _event_title(event)
                    try:
                        ok, error_code = send_subscribe(
                            token, openid, template_id, title, page=subscribe_page(event),
                        )
                    except Exception as exc:
                        ok, error_code = False, type(exc).__name__
                    if ok:
                        status = "sent"
                    elif error_code == "43101":
                        status = "not_subscribed"
                        store.reset_push_quota(event["recipient_id"], template_id, now.isoformat(timespec="seconds"))
                    else:
                        status = "failed"
                        store.grant_push_quota(event["recipient_id"], template_id, 1, now.isoformat(timespec="seconds"))
        store.record_mini_program_push({
            "id": new_id("mp-push"),
            "event_id": event["id"],
            "recipient_id": event["recipient_id"],
            "template_id": template_id,
            "status": status,
            "error_code": error_code,
            "created_at": now.isoformat(timespec="seconds"),
        })
        if status == "sent":
            sent += 1
        elif status.startswith("skipped"):
            skipped += 1
        else:
            failed += 1
    return {"skipped": "", "sent": sent, "failed": failed, "skipped_events": skipped}


def _event_title(event: dict) -> str:
    try:
        data = json.loads(event.get("data") or "{}")
    except ValueError:
        data = {}
    return str(data.get("title") or event.get("event_type") or "论文卫士Pro 通知")


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()
