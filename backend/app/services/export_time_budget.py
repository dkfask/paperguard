"""A shared, bounded time budget for one Word export attempt.

Queued exports persist an absolute deadline in their frozen record.  A context
variable carries the remaining budget through the synchronous renderer without
changing the document engine's public interface.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator


DEFAULT_EXPORT_TOTAL_SECONDS = 900
# A claimed job must cover the same bounded export as a synchronous request.
# The lease is still checked before publication and capped by Store.claim_jobs.
EXPORT_JOB_LEASE_SECONDS = 600
_MAX_EXPORT_TOTAL_SECONDS = 3600
_LEASE_SAFETY_SECONDS = 10


class ExportBudgetExceeded(RuntimeError):
    """The export's total time or the current worker lease has run out."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("导出任务已超过总时限" if reason == "total" else "本次导出执行超时")


class InvalidExportBudget(ValueError):
    """The configured or recorded export deadline cannot be trusted."""


def configured_export_total_seconds() -> int:
    value = os.environ.get("PAPERGUARD_EXPORT_TOTAL_SECONDS", "").strip()
    if not value:
        return DEFAULT_EXPORT_TOTAL_SECONDS
    if not value.isascii() or not value.isdecimal():
        raise InvalidExportBudget("导出总时限配置无效")
    seconds = int(value)
    if not 1 <= seconds <= _MAX_EXPORT_TOTAL_SECONDS:
        raise InvalidExportBudget("导出总时限必须在 1 到 3600 秒之间")
    return seconds


def configured_export_request_limit() -> int:
    value = os.environ.get("PAPERGUARD_EXPORT_REQUEST_LIMIT", "").strip()
    if not value:
        return 5
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 20:
        raise InvalidExportBudget("同一论文版本的导出请求上限必须在 1 到 20 之间")
    return int(value)


def parse_export_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidExportBudget("导出任务的时间记录无效")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidExportBudget("导出任务的时间记录无效") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise InvalidExportBudget("导出任务的时间记录缺少时区")
    return result.astimezone(timezone.utc)


def new_export_deadline(created_at: str, seconds: int) -> str:
    return (parse_export_time(created_at) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def recorded_export_deadline(record: dict) -> tuple[str, bool]:
    """Return a fixed deadline; bound pre-existing pending jobs by creation time.

    Legacy pending jobs without a deadline use the historical default from
    their creation time. Their first recovery persists the derived value, so
    later retries or configuration changes cannot extend it. A broken current
    configuration must not be mistaken for a corrupt historical record.
    """
    if "deadline_at" in record:
        value = record["deadline_at"]
        parse_export_time(value)
        return value, False
    return new_export_deadline(record.get("created_at"), DEFAULT_EXPORT_TOTAL_SECONDS), True


@dataclass(frozen=True)
class _Budget:
    total_wall: datetime
    total_monotonic: float
    attempt_wall: datetime | None
    attempt_monotonic: float | None


_current_budget: ContextVar[_Budget | None] = ContextVar("paperguard_export_budget", default=None)


@contextmanager
def export_budget(deadline_at: str, *, lease_until: str | None = None) -> Iterator[None]:
    """Limit nested operations by the frozen deadline and an optional lease."""
    total = parse_export_time(deadline_at)
    attempt = parse_export_time(lease_until) - timedelta(seconds=_LEASE_SAFETY_SECONDS) if lease_until else None
    wall_now, monotonic_now = datetime.now(timezone.utc), time.monotonic()
    budget = _Budget(
        total_wall=total,
        total_monotonic=monotonic_now + (total - wall_now).total_seconds(),
        attempt_wall=attempt,
        attempt_monotonic=(monotonic_now + (attempt - wall_now).total_seconds()) if attempt else None,
    )
    old = _current_budget.get()
    if old is not None:
        # A nested operation may shorten, but never extend, the outer budget.
        if old.total_monotonic < budget.total_monotonic:
            budget = _Budget(old.total_wall, old.total_monotonic, budget.attempt_wall, budget.attempt_monotonic)
        if old.attempt_monotonic is not None and (
                budget.attempt_monotonic is None or old.attempt_monotonic < budget.attempt_monotonic):
            budget = _Budget(budget.total_wall, budget.total_monotonic, old.attempt_wall, old.attempt_monotonic)
    token = _current_budget.set(budget)
    try:
        check_export_budget()
        yield
    finally:
        _current_budget.reset(token)


@contextmanager
def ensure_export_budget() -> Iterator[None]:
    """Give direct, unqueued callers the same bounded layout operation."""
    if _current_budget.get() is not None:
        yield
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with export_budget(new_export_deadline(now, configured_export_total_seconds())):
        yield


def remaining_export_seconds(maximum: float | None = None) -> float:
    budget = _current_budget.get()
    if budget is None:
        return maximum if maximum is not None else float(DEFAULT_EXPORT_TOTAL_SECONDS)
    now, monotonic_now = datetime.now(timezone.utc), time.monotonic()
    total = min((budget.total_wall - now).total_seconds(), budget.total_monotonic - monotonic_now)
    if total <= 0:
        raise ExportBudgetExceeded("total")
    remaining = total
    if budget.attempt_wall is not None and budget.attempt_monotonic is not None:
        attempt = min((budget.attempt_wall - now).total_seconds(), budget.attempt_monotonic - monotonic_now)
        if attempt <= 0:
            raise ExportBudgetExceeded("attempt")
        remaining = min(remaining, attempt)
    return min(remaining, maximum) if maximum is not None else remaining


def check_export_budget() -> None:
    remaining_export_seconds()
