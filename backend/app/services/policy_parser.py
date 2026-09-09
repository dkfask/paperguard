"""Markdown 规范的轻量解析器。

正文供人阅读，frontmatter 中的 ``rules`` 提供机器可执行部分。缺少显式规则时
不猜测合规结论，检查报告会把该规范作为未检查文档列出。
"""

from __future__ import annotations

from typing import Any

import yaml

from app.models.policy import RequirementItem


def parse_frontmatter(markdown_content: str) -> dict[str, Any]:
    text = str(markdown_content or "").lstrip()
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if len(lines) < 3:
        return {}
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}
    parsed = yaml.safe_load("\n".join(lines[1:end])) or {}
    return parsed if isinstance(parsed, dict) else {}


def items_from_markdown(markdown_content: str) -> list[RequirementItem]:
    """把 frontmatter.rules 转成统一 RequirementItem。"""
    frontmatter = parse_frontmatter(markdown_content)
    raw_rules = frontmatter.get("rules") or []
    if not isinstance(raw_rules, list):
        return []

    items: list[RequirementItem] = []
    for index, raw in enumerate(raw_rules, start=1):
        if not isinstance(raw, dict):
            continue
        params = dict(raw.get("params") or {})
        for key in (
            "section_key", "section_keys", "min", "max", "years", "min_count",
            "min_ratio", "current_year", "count_mode", "rule_pack_id",
        ):
            if key in raw and key not in params:
                params[key] = raw[key]
        raw_type = str(raw.get("type") or "").strip()
        checker = str(raw.get("checker") or "").strip()
        target = str(raw.get("target") or "document").strip()
        item_id = str(raw.get("id") or f"markdown-rule-{index}").strip()
        checker = {
            "word_count": "abstract.word_count" if target == "abstract" else "section.word_count",
            "section_exists": "section.exists",
            "reference_count": "references.count",
            "recent_reference_count": "references.recent_count",
            "recent_reference_ratio": "references.recent_ratio",
            "reference_format": "references.format",
            "chapter_page_break": "chapter.page_break",
        }.get(raw_type, checker or (raw_type if raw_type not in ("auto", "manual", "doc") else ""))
        item_type = str(raw.get("item_type") or (raw_type if raw_type in ("auto", "manual", "doc") else "auto"))
        items.append(RequirementItem(
            id=item_id,
            type=item_type,
            target=target,
            checker=checker,
            params=params,
            severity=str(raw.get("severity") or "warning"),
            enforce=str(raw.get("enforce") or "soft"),
            description=str(raw.get("description") or raw.get("message") or checker),
            source=str(raw.get("source") or ""),
            rule_pack_id=str(raw.get("rule_pack_id") or params.get("rule_pack_id") or ""),
            rule_pack_params=dict(raw.get("rule_pack_params") or {}),
        ))
    return items
