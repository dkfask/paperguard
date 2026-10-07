"""上游 AI 服务的熔断保护。

为什么需要：修复前 `/api/ai/assist` 对上游故障没有任何保护。超时是
`PAPERGUARD_AI_TIMEOUT`，默认 **60 秒**——上游一旦挂掉，每个用户每个请求
都要挂 60 秒才拿到一个 504。后果有三层：

1. **用户侧**：写作页长时间无响应，看起来像系统死了。
2. **资源侧**：S3 已经给每个用户加了配额与突发限流，但突发限流挡不住
   「少量用户 × 长时间挂起」——10 个并发请求就能占住 10 个工作线程 60 秒，
   而 uvicorn 默认就是这类同步执行。工作线程池会被慢慢吃干。
3. **成本侧**：挂起期间连接一直占着，上游若已收到请求可能照样计费。

设计上刻意做得很小：一个进程内的滑动窗口计数器，没有引入
`pycircuitbreaker` 之类的依赖。理由是本项目是单进程部署；多副本时
每个副本各自熔断，语义仍然正确（只是阈值按副本数摊薄）。

阈值与半开：
- 连续失败 `PAPERGUARD_AI_BREAKER_THRESHOLD`（默认 5）次后打开
- 打开后 `PAPERGUARD_AI_BREAKER_RESET_SEC`（默认 30）秒内直接拒绝，
  **不再打上游**
- 冷却结束进入半开：放行 1 个探针请求；成功则完全关闭，失败则重新打开

半开只放一个探针是刻意的：冷却结束那一刻如果所有等待中的请求一起冲
过去，熔断等于没有。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

_DEFAULT_THRESHOLD = 5
_DEFAULT_RESET_SEC = 30.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass
class _Breaker:
    """单个上游端点的熔断状态。"""

    key: str
    failures: int = 0
    opened_at: float = 0.0
    probing: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def is_open(self) -> bool:
        return self.opened_at > 0.0

    def remaining(self, now: float, reset_sec: float) -> float:
        if not self.is_open:
            return 0.0
        return max(0.0, reset_sec - (now - self.opened_at))


_breakers: dict[str, _Breaker] = {}
_registry_lock = threading.Lock()


def _get(key: str) -> _Breaker:
    with _registry_lock:
        breaker = _breakers.get(key)
        if breaker is None:
            breaker = _Breaker(key=key)
            _breakers[key] = breaker
        return breaker


def allow(key: str) -> tuple[bool, str]:
    """请求是否可以发往上游。

    返回 `(是否放行, 拒绝原因)`。拒绝时**不应**再发请求——那正是熔断要
    避免的事情。
    """
    breaker = _get(key)
    threshold = _env_int("PAPERGUARD_AI_BREAKER_THRESHOLD", _DEFAULT_THRESHOLD)
    reset_sec = _env_float("PAPERGUARD_AI_BREAKER_RESET_SEC", _DEFAULT_RESET_SEC)
    now = time.monotonic()

    with breaker._lock:
        if not breaker.is_open:
            return True, ""
        remaining = breaker.remaining(now, reset_sec)
        if remaining > 0:
            return False, f"AI 服务暂时不可用, 请 {int(remaining) + 1} 秒后重试"
        # 冷却结束：半开，放行且仅放行一个探针
        if breaker.probing:
            return False, "AI 服务正在恢复中, 请稍后重试"
        breaker.probing = True
        return True, ""


def record_success(key: str) -> None:
    breaker = _get(key)
    with breaker._lock:
        breaker.failures = 0
        breaker.opened_at = 0.0
        breaker.probing = False


def record_failure(key: str) -> None:
    breaker = _get(key)
    threshold = _env_int("PAPERGUARD_AI_BREAKER_THRESHOLD", _DEFAULT_THRESHOLD)
    now = time.monotonic()
    with breaker._lock:
        breaker.failures += 1
        if breaker.probing or breaker.failures >= threshold:
            # 探针失败：立刻重新打开，不等阈值
            breaker.opened_at = now
            breaker.probing = False


def reset_all() -> None:
    """测试与运维用：清空所有熔断状态。"""
    with _registry_lock:
        _breakers.clear()


def state(key: str) -> dict:
    breaker = _get(key)
    reset_sec = _env_float("PAPERGUARD_AI_BREAKER_RESET_SEC", _DEFAULT_RESET_SEC)
    now = time.monotonic()
    return {
        "open": breaker.is_open,
        "failures": breaker.failures,
        "remaining": round(breaker.remaining(now, reset_sec), 1),
        "probing": breaker.probing,
    }
