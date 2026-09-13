"""SMTP 邮件发送 — 邮箱注册验证码与后续通知通道。

环境变量:
- PAPERGUARD_SMTP_HOST / PAPERGUARD_SMTP_PORT(默认 587, STARTTLS)
- PAPERGUARD_SMTP_USER / PAPERGUARD_SMTP_PASSWORD(可选认证)
- PAPERGUARD_SMTP_FROM(发件人, 与 HOST 同时配置才视为已配置)
未配置时 send 抛出 EmailNotConfigured, 调用方降级(注册免验证并在账号上标记未验证)。
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

LOGGER = logging.getLogger("paperguard.emailer")


class EmailNotConfigured(RuntimeError):
    """SMTP 未配置。"""


def smtp_configured() -> bool:
    host = os.environ.get("PAPERGUARD_SMTP_HOST", "").strip()
    return bool(host and os.environ.get("PAPERGUARD_SMTP_FROM", "").strip())


def _from_address() -> str:
    return os.environ.get("PAPERGUARD_SMTP_FROM", "").strip()


def send_email(to: str, subject: str, body: str) -> None:
    if not smtp_configured():
        raise EmailNotConfigured("SMTP 未配置")
    host = os.environ.get("PAPERGUARD_SMTP_HOST", "").strip()
    port = int(os.environ.get("PAPERGUARD_SMTP_PORT", "587"))
    user = os.environ.get("PAPERGUARD_SMTP_USER", "").strip()
    password = os.environ.get("PAPERGUARD_SMTP_PASSWORD", "")
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = formataddr(("论文卫士Pro", _from_address()))
    message["To"] = to
    with smtplib.SMTP(host, port, timeout=15) as client:
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
