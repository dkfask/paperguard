"""表达式求值器 — Sprint 2 commit #7.

把 YAML rule 的 ``scope`` (JMESPath 路径过滤器) 和 ``when`` (JSONLogic 条件) 在
IR Document 上求值。被 ``visitor.visit_document`` 调用。

设计要点（参考 docs/sprints/sprint-2-plan.md §3.1 evaluator）：
- ``evaluate_scope(scope, document)``：
  - 内部调 ``document.to_dict()``（IR Document → 嵌套 dict）
  - 给每个 paragraph 注入 ``__index__`` 字段（反查 ``Finding.location.paragraph_index``）
  - 跑 ``jmespath.compile(scope).search(doc_dict)`` 过滤
  - 返回 ``list[tuple[paragraph_index | None, dict]]``
  - ``jmespath.compile`` 用 ``functools.lru_cache(128)`` 复用 AST
- ``evaluate_when(when, node)``：
  - 直接 forward 给 ``json_logic.jsonLogic(when, node)``
  - var 支持点分路径（``font.eastAsia``）和索引（``runs.0.font_eastasia``）
  - 缺失字段 var 返回 None（jsonLogic 行为）
- 异常 fail-fast：JMESPath ParseError 不 swallow

约束：``app/core/`` 严禁 import PyQt（Sprint 1 硬约束）。

Public API:
    evaluate_scope(scope: str, document: Document)
    evaluate_when(when: dict, node: dict) -> bool
"""

from __future__ import annotations

import functools
from typing import Any

import jmespath
from json_logic import jsonLogic

from app.core.ir import Document


# ---------------------------------------------------------------------------
# JMESPath compile cache — 同一 scope 复用 AST，避免重复 parse
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=128)
def _compile(expr: str) -> jmespath.parser.ParsedResult:
    """Compile JMESPath expression，缓存编译结果。"""
    return jmespath.compile(expr)


# ---------------------------------------------------------------------------
# Public: evaluate_scope
# ---------------------------------------------------------------------------

def evaluate_scope(
    scope: str,
    document: Document,
) -> list[tuple[int | None, dict]]:
    """用 JMESPath ``scope`` 过滤 IR Document，返回匹配节点 + paragraph_index。

    实现细节：
    1. ``document.to_dict()`` 转 dict 供 jmespath 消费
    2. 给每个 paragraph 注入 ``__index__`` 字段（visitor 反查 Finding.location 用）
    3. ``jmespath.compile(scope).search(dict)`` 过滤
    4. 返回 list[tuple[paragraph_index | None, node_dict]]

    Args:
        scope: JMESPath 表达式，如 ``"paragraphs[?style_id == 'Body Text']"``
        document: IR Document（不可变）

    Returns:
        匹配节点列表；每元素 ``(paragraph_index, node_dict)``：
        - paragraph 节点：index = 段落下标
        - 其他节点（section / metadata / run）：index = None
        无匹配返回空列表 []

    Raises:
        jmespath.exceptions.ParseError: scope 表达式语法错误
    """
    doc_dict = document.to_dict()

    # 注入 __index__ 到每个 paragraph
    for i, p in enumerate(doc_dict.get("paragraphs", [])):
        p["__index__"] = i

    raw = _compile(scope).search(doc_dict)

    if raw is None:
        return []
    if not isinstance(raw, list):
        # scope 返回单元素时包装成 list（如 "metadata"、"sections[0]"）
        raw = [raw]

    result: list[tuple[int | None, dict]] = []
    for node in raw:
        if not isinstance(node, dict):
            continue
        idx = node.get("__index__")
        # 非 paragraph 节点没有 __index__
        result.append((idx if isinstance(idx, int) else None, node))
    return result


# ---------------------------------------------------------------------------
# Public: evaluate_when
# ---------------------------------------------------------------------------

def evaluate_when(when: dict, node: dict) -> bool:
    """对单个节点求 JSONLogic ``when`` 条件，返回布尔结果。

    Args:
        when: JSONLogic 表达式 dict，如 ``{"!=": [{"var": "line_spacing"}, 1.5]}``
        node: 已展开的节点 dict（来自 evaluate_scope 返回）

    Returns:
        ``True`` 表示规则命中（违规存在）；``False`` 表示合规

    Notes:
        - var 支持点分路径（``font.eastAsia``）和索引（``runs.0.font_eastasia``）
        - 缺失字段时 var 返回 None；与具体值比较时通常为 False
        - 异常不 swallow（jsonLogic 内部不抛常见类型错，传播留给上层）
    """
    result = jsonLogic(when, node)
    return bool(result)


__all__ = ["evaluate_scope", "evaluate_when", "_compile"]
