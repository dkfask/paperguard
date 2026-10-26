"""Conservatively compile confirmed DOCX instructions into writing checks.

Only explicit, deterministic instructions with a stable section target are
eligible. Unsupported or ambiguous text remains a human-review item.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.models.writing_specification import WritingSpecification

COMPILER_VERSION = "template-content-rules/1"
_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
_COUNT_UNIT = r"(字|字符|词|单词|个|项|条)"
_LOWER = re.compile(rf"(?:不少于|至少|不低于|不得少于|最少)\s*({_NUMBER})\s*{_COUNT_UNIT}")
_UPPER = re.compile(rf"(?:不超过|至多|最多|不得超过|最多为)\s*({_NUMBER})\s*{_COUNT_UNIT}")
_RANGE = re.compile(rf"({_NUMBER})\s*(?:-|－|—|至|到|~|～)\s*({_NUMBER})\s*{_COUNT_UNIT}")
_COUNT_MENTION = re.compile(rf"(?:{_NUMBER}(?:\s*(?:-|－|—|至|到|~|～)\s*{_NUMBER})?)\s*{_COUNT_UNIT}")


def _flatten_sections(sections: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in sections or []:
        if hasattr(node, "model_dump"):
            raw = node.model_dump(mode="json")
        else:
            raw = node if isinstance(node, dict) else {}
        result.append(raw)
        result.extend(_flatten_sections(raw.get("children")))
    return result


def _norm_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = re.sub(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节篇部分]\s*", "", value)
    value = re.sub(r"^\s*[0-9]+(?:\.[0-9]+)*[.、)）]?\s*", "", value)
    return re.sub(r"[\s\u3000\u00a0，。；：:,.、()（）]", "", value).lower()


def _node_title(node: dict[str, Any]) -> str:
    return str(node.get("title") or node.get("name") or "")


def _node_key(node: dict[str, Any]) -> str:
    return str(node.get("key") or "")


def _node_kind(node: dict[str, Any]) -> str:
    return str(node.get("type") or node.get("kind") or "")


def _map_section(rule: Any, specification: WritingSpecification, sections: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [node for node in sections if _node_key(node) and _node_kind(node) not in {"cover", "toc"}]
    scope_title = str(rule.scope.get("region_title") or "")
    folded_scope = _norm_title(scope_title)
    exact = [node for node in candidates if _norm_title(_node_title(node)) == folded_scope and folded_scope]
    if len(exact) == 1:
        return exact[0]

    evidence_ids = set(rule.evidence_node_ids)
    ordered_nodes = [node for node in specification.nodes if node.part == "word/document.xml"]
    instruction_index = next((i for i, node in enumerate(ordered_nodes) if node.node_id in evidence_ids), None)
    if instruction_index is None:
        return None

    source_heading = None
    # The nearest heading is the most specific target. If the source has a
    # deeper heading than the student template models, fall back to its parent.
    for node in reversed(ordered_nodes[:instruction_index]):
        if node.role in {"heading1", "heading2", "heading3"} and node.text.strip():
            source_heading = node
            break
    if source_heading is None:
        return None
    source_title = _norm_title(source_heading.text)
    matches = [node for node in candidates if _norm_title(_node_title(node)) == source_title and source_title]
    if len(matches) == 1:
        return matches[0]
    if source_heading.role in {"heading2", "heading3"}:
        parent_heading = next((node for node in reversed(ordered_nodes[:instruction_index])
                               if node.role == "heading1" and node.text.strip()), None)
        if parent_heading is not None:
            parent_title = _norm_title(parent_heading.text)
            matches = [node for node in candidates if _norm_title(_node_title(node)) == parent_title and parent_title]
            if len(matches) == 1:
                return matches[0]
    return None


def _count_constraint(text: str) -> tuple[str, dict[str, int], str] | None:
    # A second bare “300字” alongside a supported bound is still a second
    # numeric instruction; do not silently compile only the first/last one.
    if len(list(_COUNT_MENTION.finditer(text))) != 1:
        return None
    matches = [
        ("range", match) for match in _RANGE.finditer(text)
    ] + [
        ("minimum", match) for match in _LOWER.finditer(text)
    ] + [
        ("maximum", match) for match in _UPPER.finditer(text)
    ]
    # Never pick one number from a sentence containing several separate
    # constraints. The teacher can review those instructions manually.
    if len(matches) != 1:
        return None
    kind, match = matches[0]
    if kind == "range":
        minimum, maximum, unit = match.groups()
        minimum, maximum = int(minimum.replace(",", "")), int(maximum.replace(",", ""))
        if minimum <= maximum:
            return "range", {"min": minimum, "max": maximum}, unit
        return None
    return kind, {"min" if kind == "minimum" else "max": int(match.group(1).replace(",", ""))}, match.group(2)


def _compile_instruction(rule: Any, specification: WritingSpecification,
                         sections: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = str(rule.expected or "").strip()
    if rule.property != "instruction.text" or rule.evidence_kind != "explicit_instruction":
        return None
    # “表格形式” is enforceable when it names a section that exists in the
    # template tree. Other table guidance remains manual.
    if "表格" in text and re.search(r"(?:表格.{0,12}(?:形式|表示|展示|进行)|(?:用|以|采用|使用).{0,6}表格)", text):
        section = _map_section(rule, specification, sections)
        if section:
            return {
                "rule_id": rule.id,
                "kind": "auto",
                "checker": "section.table_required",
                "target": _node_key(section),
                "params": {"section_key": _node_key(section)},
                "description": f"{_node_title(section)}：模板明确要求使用表格表示。",
                "expected": "at least one table block",
                "source_text": text,
                "source_node_ids": list(rule.evidence_node_ids),
            }

    parsed = _count_constraint(text)
    if parsed is None:
        return None
    _kind, bounds, unit = parsed
    section = _map_section(rule, specification, sections)
    if not section:
        return None
    is_keyword_rule = "关键词" in text or "key words" in text.lower() or _node_kind(section) in {"keywords", "english_keywords"}
    if unit == "词":
        # Chinese “词” can mean lexical words, which the existing deterministic
        # counter cannot count reliably. Keep it as a manual review item.
        return None
    if is_keyword_rule and unit in {"个", "项", "条"}:
        checker = "keywords.count"
        target = "keywords"
        params = {**bounds, "count_mode": "entries"}
    elif is_keyword_rule:
        # A character limit on a keyword line is not a keyword item-count rule.
        return None
    elif unit in {"个", "项", "条"}:
        # No supported generic item counter exists yet.
        return None
    elif unit == "单词":
        count_mode = "words"
        if _node_kind(section) == "abstract":
            checker = "abstract.word_count"
            target = "abstract"
            params = {**bounds, "count_mode": count_mode}
        else:
            checker = "section.word_count"
            target = _node_key(section)
            params = {**bounds, "section_key": _node_key(section), "count_mode": count_mode}
    else:
        count_mode = "characters"
        if _node_kind(section) == "abstract":
            checker = "abstract.word_count"
            target = "abstract"
            params = {**bounds, "count_mode": count_mode}
        else:
            checker = "section.word_count"
            target = _node_key(section)
            params = {**bounds, "section_key": _node_key(section), "count_mode": count_mode}
    title = _node_title(section)
    return {
        "rule_id": rule.id,
        "kind": "auto",
        "checker": checker,
        "target": target,
        "params": params,
        "description": f"{title}：{text}",
        "expected": bounds,
        "source_text": text,
        "source_node_ids": list(rule.evidence_node_ids),
    }


def review_content_instructions(specification: WritingSpecification, sections: Any) -> dict[str, Any]:
    """Return machine-checkable candidates and retained manual instructions."""
    section_nodes = _flatten_sections(sections)
    candidates: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    for rule in specification.rules:
        if rule.property != "instruction.text":
            continue
        compiled = _compile_instruction(rule, specification, section_nodes)
        node = next((item for item in specification.nodes if item.node_id in rule.evidence_node_ids), None)
        item = {
            "rule_id": rule.id,
            "source_text": str(rule.expected or ""),
            "region_title": rule.scope.get("region_title", ""),
            "source_node_ids": list(rule.evidence_node_ids),
            "source_path": node.path if node else "",
        }
        if compiled is None:
            manual.append({**item, "reason": "未发现无歧义的自动检查条件，需教师人工确认。"})
        else:
            candidates.append({**compiled, **item})
    return {"compiler_version": COMPILER_VERSION, "candidates": candidates, "manual_items": manual}


def compile_confirmed_content_requirements(specification: WritingSpecification, sections: Any,
                                           confirmed_rule_ids: list[str], *, template_id: str,
                                           template_name: str, template_version: str) -> dict[str, Any]:
    """Compile only teacher-selected candidates; unknown IDs fail closed."""
    if len(confirmed_rule_ids) != len(set(confirmed_rule_ids)):
        raise ValueError("内容规则确认项重复")
    review = review_content_instructions(specification, sections)
    candidates = {item["rule_id"]: item for item in review["candidates"]}
    manual = {item["rule_id"] for item in review["manual_items"]}
    if set(confirmed_rule_ids) - set(candidates):
        if set(confirmed_rule_ids) & manual:
            raise ValueError("存在尚未支持自动检查的内容说明，不能启用为硬性规则")
        raise ValueError("内容规则引用未知或已过期的规范项")
    requirements = []
    review_requirements = []
    for rule_id, candidate in candidates.items():
        confirmed = rule_id in set(confirmed_rule_ids)
        item = {
            "id": f"template-instruction:{template_id}:{rule_id}",
            "type": "auto" if confirmed else "manual",
            "target": candidate["target"],
            "checker": candidate["checker"] if confirmed else "",
            "params": candidate["params"],
            "severity": "error" if confirmed else "info",
            "enforce": "hard" if confirmed else "soft",
            "description": candidate["description"],
            "source": f"{template_name} · Word 明确要求",
            "source_version": template_version,
            "provenance": {
                "kind": "explicit_instruction",
                "compiler_version": COMPILER_VERSION,
                "source_sha256": specification.source_sha256,
                "specification_sha256": specification.specification_sha256,
                "rule_id": rule_id,
                "source_node_ids": candidate["source_node_ids"],
                "source_path": candidate["source_path"],
                "source_text": candidate["source_text"],
            },
        }
        review_requirements.append(item)
        if confirmed:
            requirements.append(item)
    for item in review["manual_items"]:
        review_requirements.append({
            "id": f"template-instruction:{template_id}:{item['rule_id']}",
            "type": "manual",
            "target": "document",
            "checker": "",
            "params": {},
            "severity": "info",
            "enforce": "soft",
            "description": item["source_text"],
            "source": f"{template_name} · Word 待人工核查",
            "source_version": template_version,
            "provenance": {
                "kind": "explicit_instruction",
                "compiler_version": COMPILER_VERSION,
                "source_sha256": specification.source_sha256,
                "specification_sha256": specification.specification_sha256,
                "rule_id": item["rule_id"],
                "source_node_ids": item["source_node_ids"],
                "source_path": item["source_path"],
                "source_text": item["source_text"],
            },
        })
    return {
        "review": review,
        "confirmed_rule_ids": list(confirmed_rule_ids),
        "requirements": requirements,
        "review_requirements": review_requirements,
    }


__all__ = [
    "COMPILER_VERSION",
    "compile_confirmed_content_requirements",
    "review_content_instructions",
]
