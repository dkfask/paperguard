"""Finding + Location + Fix + Severity — 规则引擎产出。

`Finding` 描述一条违规；`Location` 定位（章节、段落、文本片段）；`Fix` 描述可自动修复动作。

字段含义参考 docs/design-proposal.md §3.2。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Severity(Enum):
    """违规严重性。"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Location:
    """违规位置。"""
    section: str | None = None
    paragraph_index: int | None = None
    text_snippet: str | None = None
    section_key: str | None = None


@dataclass(frozen=True)
class Fix:
    """可自动修复的动作。"""
    action: str          # "set" / "insert" / "delete" / "wrap"
    target: str          # JMESPath 表达式或点分属性名，如 "paragraph.spacing.line"
    value: Any = None    # 目标值


@dataclass(frozen=True)
class Finding:
    """一条违规。"""
    rule_id: str
    severity: Severity
    message: str
    location: Location
    actual: Any = None
    expected: Any = None
    fix: Fix | None = None
