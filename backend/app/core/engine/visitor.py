"""IR 遍历 + 规则命中 — Sprint 2 commit #9.

把 RulePack 中每条 ``Rule`` 在 IR Document 上跑：
1. ``evaluate_scope(rule.scope, document)`` 过滤候选节点
2. ``evaluate_when(rule.when, node)`` 求布尔条件
3. 命中节点 → 构造 ``Finding`` 加入结果

结果按 ``(paragraph_index, rule_id)`` 排序，与 Sprint 1.5 stub_engine 行为一致。

约束：``app/core/`` 严禁 import PyQt（Sprint 1 硬约束）。

Public API:
    visit_document(document: Document, pack: RulePack) -> list[Finding]
"""

from __future__ import annotations

from app.core.engine.evaluator import evaluate_scope, evaluate_when
from app.core.engine.loader import Rule, RulePack
from app.core.ir import Document, Finding, Location


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def visit_document(document: Document, pack: RulePack) -> list[Finding]:
    """遍历 IR Document，跑 pack 中每条 rule，返回所有命中的 ``Finding``。

    Args:
        document: IR Document（不可变）
        pack: RulePack（loader.load_pack 加载）

    Returns:
        ``list[Finding]``：按 ``(paragraph_index, rule_id)`` 字典序排序

    Notes:
        - 每条 rule 独立跑 scope + when，rule 间互不影响
        - Finding.actual / expected = None（MVP 简化）
        - Finding.location.text_snippet 来自 paragraph.text（PyQt sidebar 用）
        - 任一 rule 抛异常 → propagate（fail-fast，配合 loader 的 fail-fast）
    """
    findings: list[Finding] = []
    for rule in pack.rules:
        findings.extend(_visit_rule(document, rule))

    findings.sort(
        key=lambda f: (
            f.location.paragraph_index
            if f.location.paragraph_index is not None
            else -1,
            f.rule_id,
        )
    )
    return findings


# ---------------------------------------------------------------------------
# 内部：单条 rule 遍历
# ---------------------------------------------------------------------------

def _visit_rule(document: Document, rule: Rule) -> list[Finding]:
    """对单条 rule 跑 scope + when，返回该 rule 的所有命中 ``Finding``。"""
    findings: list[Finding] = []
    candidates = evaluate_scope(rule.scope, document)
    for paragraph_index, node in candidates:
        if not evaluate_when(rule.when, node):
            continue
        findings.append(_build_finding(rule, paragraph_index, node))
    return findings


def _build_finding(
    rule: Rule,
    paragraph_index: int | None,
    node: dict,
) -> Finding:
    """构造 ``Finding``：rule_id / severity / message / location / fix 全部就地填充。"""
    text_snippet = None
    if isinstance(node, dict):
        raw_text = node.get("text")
        if isinstance(raw_text, str):
            text_snippet = raw_text
    raw_section = node.get("__section__") if isinstance(node, dict) else None
    section = raw_section if raw_section in ("header_paragraphs", "footer_paragraphs") else None
    return Finding(
        rule_id=rule.id,
        severity=rule.severity,
        message=rule.message,
        location=Location(
            section=section,
            paragraph_index=paragraph_index,
            text_snippet=text_snippet,
        ),
        actual=None,    # MVP 简化（YAML rule 不显式提供 actual）
        expected=None,  # MVP 简化
        fix=rule.fix,
    )


__all__ = ["visit_document"]
