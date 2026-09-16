"""邮件发送 — 邮箱注册验证码与后续通知通道。

通道优先级:
1. Resend HTTP API:PAPERGUARD_RESEND_API_KEY + PAPERGUARD_RESEND_FROM(推荐)
   - 沙箱(未验证域名)仅能发给 Resend 账号所有者邮箱;生产需在 Resend 控制台验证发信域名
2. SMTP:PAPERGUARD_SMTP_HOST/PORT/USER/PASSWORD/FROM(+PAPERGUARD_SMTP_SSL=1 走 465 隐式 SSL)
两者都未配置时抛 EmailNotConfigured, 由调用方返回明确的服务不可用错误。
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

import httpx

LOGGER = logging.getLogger("paperguard.emailer")


class EmailNotConfigured(RuntimeError):
    """SMTP 未配置。"""


def smtp_configured() -> bool:
    host = os.environ.get("PAPERGUARD_SMTP_HOST", "").strip()
    return bool(host and os.environ.get("PAPERGUARD_SMTP_FROM", "").strip())


def resend_configured() -> bool:
    return bool(os.environ.get("PAPERGUARD_RESEND_API_KEY", "").strip())


def email_configured() -> bool:
    """是否具备任一发信通道(决定注册是否强制验证码)。"""
    return resend_configured() or smtp_configured()


def _from_address() -> str:
    return os.environ.get("PAPERGUARD_SMTP_FROM", "").strip()


def send_via_resend(api_key: str, to: str, subject: str, body: str) -> str:
    """发送并返回 Resend 邮件 id(可与控制台活动记录对账)。"""
    from_addr = os.environ.get("PAPERGUARD_RESEND_FROM", "").strip() or "onboarding@resend.dev"
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": f"论文卫士Pro <{from_addr}>", "to": [to], "subject": subject, "text": body},
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Resend 发送失败: HTTP {response.status_code} {response.text[:200]}")
    email_id = str(response.json().get("id") or "")
    LOGGER.info("email.sent via=resend to_domain=%s email_id=%s", to.split("@")[-1], email_id)
    return email_id


def send_email(to: str, subject: str, body: str) -> None:
    api_key = os.environ.get("PAPERGUARD_RESEND_API_KEY", "").strip()
    if api_key:
        send_via_resend(api_key, to, subject, body)
        return
    if not smtp_configured():
        raise EmailNotConfigured("邮件通道未配置")
        raise EmailNotConfigured("SMTP 未配置")
    host = os.environ.get("PAPERGUARD_SMTP_HOST", "").strip()
    port = int(os.environ.get("PAPERGUARD_SMTP_PORT", "587"))
    use_ssl = os.environ.get("PAPERGUARD_SMTP_SSL", "").strip().lower() in {"1", "true", "yes"}
    user = os.environ.get("PAPERGUARD_SMTP_USER", "").strip()
    password = os.environ.get("PAPERGUARD_SMTP_PASSWORD", "")
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = formataddr(("论文卫士Pro", _from_address()))
    message["To"] = to
    # 465 端口为隐式 SSL(QQ/企业邮常用), 587 为 STARTTLS; 腾讯云封出站 25 端口
    client_ctx = smtplib.SMTP_SSL(host, port, timeout=15) if use_ssl else smtplib.SMTP(host, port, timeout=15)
    with client_ctx as client:
        if not use_ssl:
            client.starttls()
        if user:
            client.login(user, password)
        client.sendmail(_from_address(), [to], message.as_string())
    LOGGER.info("email.sent to_domain=%s", to.split("@")[-1])


def send_verification_code(to: str, code: str, minutes: int = 10) -> None:
    send_email(
        to,
        "论文卫士Pro 邮箱验证码",
        f"你的邮箱验证码是:{code}\n\n{minutes} 分钟内有效。若非本人操作, 请忽略本邮件。\n\n—— 论文卫士Pro",
    )
