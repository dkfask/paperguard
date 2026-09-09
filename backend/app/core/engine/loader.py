"""YAML pack loader — Sprint 2 commit #5.

把 ``app/core/rules/packs/{pack_id}.yaml`` 加载为 ``RulePack`` frozen dataclass。

设计要点（参考 docs/sprints/sprint-2-plan.md §2 ADR-1/2/5）：
- pydantic v2 ``BaseModel`` 做 schema 强校验（错误消息带字段路径）
- fail-fast（ADR-5）：YAML 语法错 / schema 错 / duplicate rule_id 立刻 raise
- ``Fix.target`` 强制点分形式（ADR-4）兼容 Sprint 1.5 LiveMutator
- 不实现 ``extends`` 继承（ADR-8 MVP 决策）
- 默认 packs_dir = ``app/core/rules/packs/``；测试可注入

Public API:
    load_pack(pack_id, packs_dir=None, params=None) -> RulePack
    list_packs(packs_dir=None) -> list[str]

Schema classes (pydantic):
    FixSchema      action / target / value
    RuleSchema     id / severity / scope / when / message / fix?
    RulePackSchema id / name / version / rules

Frozen dataclass (visitor 消费):
    Rule           id / severity: Severity / scope / when / message / fix: Fix | None
    RulePack       id / name / version / rules: tuple[Rule, ...]
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from app.core.ir import Fix, Severity


# ---------------------------------------------------------------------------
# 默认 packs 目录 — app/core/rules/packs/
# ---------------------------------------------------------------------------
_DEFAULT_PACKS_DIR = Path(__file__).parent.parent / "rules" / "packs"


# ---------------------------------------------------------------------------
# pydantic schema — fail-fast 校验
# ---------------------------------------------------------------------------

class FixSchema(BaseModel):
    """YAML fix block 的 schema。"""
    action: Literal["set", "insert", "delete", "wrap"]
    target: str = Field(min_length=1)
    value: Any = None


class RuleSchema(BaseModel):
    """单条规则的 schema。"""
    id: str = Field(min_length=1)
    severity: Literal["error", "warning", "info"]
    scope: str = Field(min_length=1)
    when: dict[str, Any]
    message: str = Field(min_length=1)
    fix: FixSchema | None = None


class RulePackSchema(BaseModel):
    """整个 pack 的 schema。"""
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: list[RuleSchema]


# ---------------------------------------------------------------------------
# Frozen dataclass — visitor 消费的内部表示
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """单条规则（visitor 消费）。"""
    id: str
    severity: Severity
    scope: str
    when: dict
    message: str
    fix: Fix | None


@dataclass(frozen=True)
class RulePack:
    """规则包（visitor 消费）。"""
    id: str
    name: str
    version: str
    rules: tuple[Rule, ...]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_pack(pack_id: str, packs_dir: Path | None = None,
              params: dict[str, Any] | None = None) -> RulePack:
    """加载并 fail-fast 校验 YAML pack，返回 ``RulePack`` frozen dataclass。

    Args:
        pack_id: pack 标识，对应 ``{packs_dir}/{pack_id}.yaml`` 文件
        packs_dir: pack 目录，默认 ``app/core/rules/packs/``（注入便于测试）
        params: 可选的 ``rule_id -> {when/scope/severity/message/fix}`` 覆盖，
                用于教师/规范项的小范围参数化调整

    Returns:
        ``RulePack`` 不可变实例

    Raises:
        FileNotFoundError: pack 文件不存在
        yaml.YAMLError:    YAML 语法错误
        ValueError:        空文件 / duplicate rule_id / 非法规则覆盖
        pydantic.ValidationError: schema 校验失败（缺字段 / 类型错 / severity 非法）
    """
    if packs_dir is None:
        packs_dir = _DEFAULT_PACKS_DIR

    pack_path = packs_dir / f"{pack_id}.yaml"
    if not pack_path.exists():
        raise FileNotFoundError(f"Pack not found: {pack_path}")

    with pack_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Pack file is empty: {pack_path}")

    # pydantic 校验 — schema 错误立刻 raise
    schema = RulePackSchema.model_validate(raw)

    # 检查 duplicate rule_id（pydantic 不验这个，手动）
    seen_ids: set[str] = set()
    for r in schema.rules:
        if r.id in seen_ids:
            raise ValueError(
                f"Duplicate rule_id '{r.id}' in pack '{schema.id}'"
            )
        seen_ids.add(r.id)

    # schema → frozen dataclass; reject misspelled rule IDs so an override is
    # never silently ignored.
    if params is not None and not isinstance(params, dict):
        raise ValueError("Rule pack params must be an object")
    rule_ids = {rule.id for rule in schema.rules}
    unknown_overrides = set(params or {}) - rule_ids
    if unknown_overrides:
        raise ValueError(f"Unknown rule override IDs: {sorted(unknown_overrides)}")
    rules = tuple(_rule_from_schema(r, (params or {}).get(r.id)) for r in schema.rules)
    return RulePack(
        id=schema.id,
        name=schema.name,
        version=schema.version,
        rules=rules,
    )


def list_packs(packs_dir: Path | None = None) -> list[str]:
    """枚举 ``packs_dir`` 下所有 ``*.yaml`` 文件的 pack id（即文件名去后缀）。

    Args:
        packs_dir: pack 目录，默认 ``app/core/rules/packs/``

    Returns:
        排序后的 pack id 列表；目录不存在返回空列表
    """
    if packs_dir is None:
        packs_dir = _DEFAULT_PACKS_DIR
    if not packs_dir.exists():
        return []
    return sorted(p.stem for p in packs_dir.glob("*.yaml"))


# ---------------------------------------------------------------------------
# 内部：schema → frozen dataclass
# ---------------------------------------------------------------------------

def _rule_from_schema(s: RuleSchema, override: dict[str, Any] | None = None) -> Rule:
    if override is not None and not isinstance(override, dict):
        raise ValueError(f"Rule override for '{s.id}' must be an object")
    override = override or {}
    unknown = set(override) - {"when", "scope", "severity", "message", "fix"}
    if unknown:
        raise ValueError(f"Unsupported rule override fields for '{s.id}': {sorted(unknown)}")
    scope = override.get("scope", s.scope)
    when = override.get("when", s.when)
    severity = override.get("severity", s.severity)
    message = override.get("message", s.message)
    raw_fix = override.get("fix", s.fix.model_dump(mode="json") if s.fix else None)
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError(f"Rule override scope for '{s.id}' must be a non-empty string")
    if not isinstance(when, dict):
        raise ValueError(f"Rule override when for '{s.id}' must be an object")
    if not isinstance(message, str) or not message.strip():
        raise ValueError(f"Rule override message for '{s.id}' must be a non-empty string")
    if raw_fix is not None and not isinstance(raw_fix, dict):
        raise ValueError(f"Rule override fix for '{s.id}' must be an object or null")
    return Rule(
        id=s.id,
        severity=Severity(severity),
        scope=scope,
        when=deepcopy(when),
        message=message,
        fix=_fix_from_schema(FixSchema.model_validate(raw_fix)) if raw_fix else None,
    )


def _fix_from_schema(s: FixSchema) -> Fix:
    return Fix(action=s.action, target=s.target, value=s.value)


__all__ = ["Rule", "RulePack", "load_pack", "list_packs"]
