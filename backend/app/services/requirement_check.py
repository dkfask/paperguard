"""文档级规范检查器：字数、章节、关键词、参考文献等可判定要求。"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.models.template import PaperTemplate
from app.models.project import ProjectSection, WritingProject
from app.services.references import reference_block_text, reference_number_for, validate_reference_facts


_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_DATE_RE = re.compile(r"^((?:19|20)\d{2})(?:年(\d{1,2})月(?:(\d{1,2})日)?|[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?$")


def _defense_year(value: str) -> int | None:
    match = _DATE_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    year = int(match.group(1))
    month = match.group(2) or match.group(4)
    day = match.group(3) or match.group(5)
    try:
        if month:
            date(year, int(month), int(day or 1))
    except ValueError:
        return None
    return year


def _recent_reference_summary(project: WritingProject, section: ProjectSection | None,
                              field_key: str) -> dict:
    """Summarize explicit reference facts; never infer years or languages from prose."""
    blocks = [block for block in (section.paragraphs if section else [])
              if block.type.value == "paragraph" and reference_block_text(block).strip()]
    year = _defense_year(project.meta.get(field_key, "")) if field_key else None
    result = {"total": len(blocks), "defense_year": year, "window": None,
              "recent_confirmed": None, "english_confirmed": None,
              "unknown_year": None, "unknown_language_in_window": None,
              "status": "window_unknown", "reasons": []}
    if year is None:
        result["reasons"].append("答辩日期未填写或年份不明确")
        return result
    result["window"] = [year - 2, year]
    recent = english = unknown_year = unknown_language = 0
    for block in blocks:
        reference = block.reference
        raw_year = str(reference.year or "").strip() if reference else ""
        year_fact = reference.year_fact if reference else None
        year_confirmed = bool(
            year_fact
            and year_fact.status == "confirmed"
            and year_fact.confirmed_by
            and year_fact.confirmed_at
            and str(year_fact.value or "").strip() == raw_year
        )
        if not re.fullmatch(r"(?:19|20)\d{2}", raw_year) or not year_confirmed:
            unknown_year += 1
            continue
        if not year - 2 <= int(raw_year) <= year:
            continue
        recent += 1
        fact = reference.language_fact
        languages = set(fact.languages) if fact else set()
        confirmed = bool(fact and fact.status == "confirmed" and fact.confirmed_by and fact.confirmed_at)
        if confirmed and languages == {"en"}:
            english += 1
        elif not confirmed or len(languages) != 1 or "und" in languages:
            unknown_language += 1
    result.update(recent_confirmed=recent, english_confirmed=english,
                  unknown_year=unknown_year, unknown_language_in_window=unknown_language)
    if unknown_year:
        result["reasons"].append(f"{unknown_year} 篇出版年份未知、未确认或不明确")
    if unknown_language:
        result["reasons"].append(f"窗口内 {unknown_language} 篇语种未知、混合或待确认")
    if recent >= 15 and english >= 5:
        result["status"] = "met"
    elif recent + unknown_year < 15 or english + unknown_year + unknown_language < 5:
        result["status"] = "not_met"
    else:
        result["status"] = "incomplete"
    return result


def _section_text(section: ProjectSection | None) -> str:
    if section is None:
        return ""
    parts: list[str] = []
    reference_number = 0
    for block in section.paragraphs:
        if block.type.value == "paragraph":
            is_references = str(getattr(getattr(section, "type", ""), "value", getattr(section, "type", ""))) == "references"
            has_reference_content = bool(reference_block_text(block).strip())
            if is_references and has_reference_content:
                reference_number += 1
            parts.append(reference_block_text(block, reference_number_for(block, reference_number) if is_references and has_reference_content else None))
        elif block.type.value == "table" and block.table:
            parts.extend(str(cell or "") for row in block.table.cells for cell in row)
    return "\n".join(parts).strip()


def _count_words(text: str, mode: str = "characters") -> int:
    if mode == "words":
        return len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?", text))
    return len(re.sub(r"\s+", "", text))


def _keywords(text: str) -> list[str]:
    text = re.sub(r"^\s*(?:关键词|key\s*words?)\s*[:：]?\s*", "", text, flags=re.I)
    return [item.strip() for item in re.split(r"[;,；，、\n]+", text) if item.strip()]


def _section_for(project: WritingProject, target: str, params: dict) -> ProjectSection | None:
    key = str(params.get("section_key") or target if target not in ("document", "body", "abstract", "keywords", "references") else "")
    if key:
        return next((section for section in project.sections if section.key == key), None)
    if target == "abstract":
        return next((section for section in project.sections if section.type == "abstract" or section.key == "abstract"), None)
    if target == "keywords":
        return next((section for section in project.sections if section.type in ("keywords", "english_keywords")), None)
    if target == "references":
        return next((section for section in project.sections if section.type == "references" or section.key == "references"), None)
    return None


def _target_text(project: WritingProject, target: str, params: dict) -> str:
    if target in ("document", "body"):
        excluded = {"cover", "toc", "abstract", "english_abstract", "keywords", "english_keywords", "references"}
        sections = [section for section in project.sections if section.type not in excluded]
        return "\n".join(_section_text(section) for section in sections).strip()
    return _section_text(_section_for(project, target, params))


def _finding(item: dict, message: str, actual, expected, section_key: str | None = None) -> dict:
    return {
        "rule_id": f"REQ-{item['id']}",
        "requirement_item_id": item["id"],
        "severity": item["severity"],
        "enforce": item["enforce"],
        "checker": item.get("checker", ""),
        "message": message,
        "location": {"paragraph_index": None, "text_snippet": None, "section_key": section_key},
        "section_key": section_key,
        "actual": actual,
        "expected": expected,
        "fix": None,
        "source_type": "requirement",
        "source": item.get("source", "规范要求"),
        "source_version": item.get("source_version", ""),
        "sources": item.get("sources", []),
    }


def _template_nodes(nodes: list[dict] | None) -> list[dict]:
    result: list[dict] = []
    for node in nodes or []:
        result.append(node)
        result.extend(_template_nodes(node.get("children")))
    return result


def _template_page_break(template: PaperTemplate, key: str) -> bool | None:
    nodes = _template_nodes([
        node.model_dump(mode="json") for node in template.sections
    ])
    node = next((item for item in nodes if item.get("key") == key), None)
    if node is None:
        return None
    style_model = template.styles.get(node.get("type", ""))
    style = node.get("style") or (style_model.model_dump(mode="json") if style_model else {})
    return bool(style.get("page_break_before"))


def _reference_format_issues(text: str, params: dict[str, Any]) -> list[str]:
    """Return malformed reference lines using a conservative GB/T-shaped check.

    This is intentionally a syntax sanity check. Detailed font/spacing rules remain
    in the bindable GB/T 7714 rule pack.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    prefix = str(params.get("prefix_pattern") or r"(?:\[\d+\]|\d+[.)、])")
    pattern = re.compile(rf"^\s*{prefix}")
    return [
        line for line in lines
        if not pattern.search(line) or not _YEAR_RE.search(line)
    ]


def check_requirements(project: WritingProject, snapshot: dict | None,
                       template: PaperTemplate | None = None) -> tuple[list[dict], list[dict]]:
    """返回 (未通过 findings, 未自动检查项)。"""
    if not snapshot:
        return [], []
    findings: list[dict] = []
    not_checked: list[dict] = []
    sections = {section.key: section for section in project.sections}
    format_item_ids = set(snapshot.get("format_item_ids") or [])

    for item in snapshot.get("resolved_items", []):
        checker = item.get("checker", "")
        item_type = item.get("type", "auto")
        descriptor = {
            "id": item.get("id", ""),
            "description": item.get("description") or checker or "未命名要求",
            "type": item_type,
            "checker": checker,
            "severity": item.get("severity", "warning"),
            "enforce": item.get("enforce", "soft"),
            "source": item.get("source", "规范要求"),
            "source_version": item.get("source_version", ""),
            "sources": item.get("sources", []),
            "fixable": False,
        }
        if item.get("id") in format_item_ids:
            continue
        if item_type != "auto":
            not_checked.append({
                **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                "reason": "manual" if item_type == "manual" else "reference_document",
                "fixable": False,
            })
            continue

        params = item.get("params") or {}
        section = _section_for(project, str(item.get("target") or "document"), params)
        target = str(item.get("target") or "document")
        if checker == "section.exists":
            keys = params.get("section_keys") or params.get("section_key") or target
            keys = keys if isinstance(keys, list) else [keys]
            for key in keys:
                current = sections.get(str(key))
                if current is None or not _section_text(current):
                    findings.append(_finding(descriptor, descriptor["description"] or f"章节 {key} 缺失或为空", 0, "存在且非空", str(key)))
            continue
        if checker == "section.table_required":
            section = section or _section_for(project, target, params)
            actual = sum(
                1 for block in (section.paragraphs if section else [])
                if block.type.value == "table" and block.table is not None
            )
            minimum = int(params.get("min", 1))
            if actual < minimum:
                findings.append(_finding(
                    descriptor,
                    descriptor["description"] or "本章节至少需要一个表格。",
                    actual,
                    {"min": minimum},
                    section.key if section else target,
                ))
            continue
        if checker in ("section.word_count", "abstract.word_count"):
            section = section or _section_for(project, "abstract" if checker.startswith("abstract") else target, params)
            text = _target_text(project, target, params) if target in ("document", "body") and not checker.startswith("abstract") else _section_text(section)
            actual = _count_words(text, str(params.get("count_mode") or "characters"))
            expected = {key: params[key] for key in ("min", "max") if key in params}
            failed = ("min" in params and actual < params["min"]) or ("max" in params and actual > params["max"])
            if failed:
                expectation = _expectation(expected)
                findings.append(_finding(descriptor, descriptor["description"] or f"字数未达到要求: {expectation}", actual, expectation, section.key if section else None))
            continue
        if checker == "keywords.count":
            section = section or _section_for(project, "keywords", params)
            values = _keywords(_section_text(section))
            actual = len(values)
            expected = {key: params[key] for key in ("min", "max") if key in params}
            failed = ("min" in params and actual < params["min"]) or ("max" in params and actual > params["max"])
            if failed:
                findings.append(_finding(descriptor, descriptor["description"] or f"关键词数量未达到要求: {_expectation(expected)}", actual, _expectation(expected), section.key if section else None))
            continue
        if checker in ("cover.field_present", "meta.field_present"):
            field_key = str(params.get("field_key") or item.get("target") or "")
            actual = bool(str(project.meta.get(field_key, "")).strip())
            if not actual:
                findings.append(_finding(descriptor, descriptor["description"] or f"封面字段 {field_key} 未填写", False, True, "cover"))
            continue
        if checker == "cover.fields_present":
            keys = params.get("field_keys")
            declared = {field.key: field.label for field in template.meta_fields} if template else {}
            if (not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not key for key in keys)
                    or len(keys) != len(set(keys)) or any(key not in declared for key in keys)):
                not_checked.append({
                    **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                    "reason": "cover_field_mapping_missing", "checker": checker, "fixable": False,
                })
                continue
            absent = [declared[key] for key in keys if not str(project.meta.get(key, "")).strip()]
            if absent:
                findings.append(_finding(descriptor, "封面缺少：" + "、".join(absent), absent, "所列封面字段均已填写", "cover"))
            continue
        if checker == "references.list_facts":
            section = section or _section_for(project, "references", params)
            if section is None:
                if params.get("required"):
                    findings.append(_finding(descriptor, "缺少参考文献章节", False, True, "references"))
                continue
            position = 0
            for block in section.paragraphs:
                if block.type.value != "paragraph":
                    findings.append(_finding(descriptor, "参考文献列表包含非文献内容", block.id, "参考文献条目", section.key))
                    continue
                reference = block.reference
                if not reference_block_text(block).strip() and not (reference and reference.original_pasted_text is not None):
                    continue
                position += 1
                issues = validate_reference_facts(reference) if reference else ["尚未录入结构化文献事实"]
                number = reference_number_for(block, position)
                if number != position:
                    issues.append(f"当前编号为 [{number}]，列表位置应为 [{position}]")
                for issue in issues:
                    findings.append(_finding(descriptor, f"参考文献第 {position} 条：{issue}",
                                             {"block_id": block.id}, "事实完整且按列表连续编号", section.key))
            if not position and params.get("required"):
                findings.append(_finding(descriptor, "参考文献列表为空", 0, ">= 1", section.key))
            continue
        if checker == "references.count":
            section = section or _section_for(project, "references", params)
            values = [line for line in (_section_text(section).splitlines()) if line.strip()]
            actual = len(values)
            expected = {key: params[key] for key in ("min", "max") if key in params}
            failed = ("min" in params and actual < params["min"]) or ("max" in params and actual > params["max"])
            if failed:
                findings.append(_finding(descriptor, descriptor["description"] or f"参考文献数量未达到要求: {_expectation(expected)}", actual, _expectation(expected), section.key if section else None))
            continue
        if checker == "references.recent_language_summary":
            section = section or _section_for(project, "references", params)
            field_key = str(params.get("defense_date_field_key") or "")
            bound = bool(field_key and template and sum(field.key == field_key for field in template.meta_fields) == 1)
            actual = _recent_reference_summary(project, section, field_key if bound else "")
            if not bound:
                actual["reasons"] = ["模板未明确绑定答辩日期字段"]
            if actual["window"] is None:
                message = "文献内容提示：答辩日期待确定，近三年统计窗口尚未确定。"
            else:
                start, end = actual["window"]
                status = {"met": "已满足数量下限", "not_met": "未满足数量下限", "incomplete": "统计不完整，暂不能判断"}[actual["status"]]
                message = (f"文献内容提示：{start}–{end} 年，文献共 {actual['total']} 篇，"
                           f"窗口内 {actual['recent_confirmed']} 篇，其中已确认英文 {actual['english_confirmed']} 篇；"
                           f"年份未知或待确认 {actual['unknown_year']} 篇，窗口内语种待核对 {actual['unknown_language_in_window']} 篇。{status}。")
                if actual["reasons"]:
                    message += " " + "；".join(actual["reasons"]) + "。"
            advisory = _finding({**descriptor, "severity": "info", "enforce": "soft"}, message, actual,
                                {"recent_min": 15, "english_min": 5}, section.key if section else "references")
            findings.append(advisory)
            continue
        if checker in ("references.recent_count", "references.recent_ratio"):
            section = section or _section_for(project, "references", params)
            values = [line for line in (_section_text(section).splitlines()) if line.strip()]
            current_year = int(params.get("current_year") or datetime.now().year)
            years = int(params.get("years") or 3)
            recent = sum(1 for value in values if any(current_year - years + 1 <= int(year) <= current_year for year in _YEAR_RE.findall(value)))
            if checker == "references.recent_ratio":
                actual = round(recent / len(values), 4) if values else 0
                expected = params.get("min_ratio", params.get("min", 0))
                failed = actual < expected
            else:
                actual = recent
                expected = params.get("min_count", params.get("min", 0))
                failed = actual < expected
            if failed:
                findings.append(_finding(descriptor, descriptor["description"] or f"近 {years} 年参考文献不足", actual, expected, section.key if section else None))
            continue
        if checker == "references.format":
            section = section or _section_for(project, "references", params)
            issues = _reference_format_issues(_section_text(section), params)
            if issues:
                findings.append(_finding(
                    descriptor,
                    descriptor["description"] or "参考文献著录格式存在疑问（应含序号和年份）",
                    {"invalid_count": len(issues), "sample": issues[:3]},
                    "每条参考文献均含序号和年份",
                    section.key if section else "references",
                ))
            continue
        if checker == "chapter.page_break":
            if template is None:
                not_checked.append({
                    **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                    "reason": "template_required",
                    "checker": checker,
                    "fixable": False,
                })
                continue
            keys = params.get("section_keys") or params.get("section_key") or item.get("target")
            keys = keys if isinstance(keys, list) else [keys]
            for key in keys:
                actual = _template_page_break(template, str(key))
                if actual is None or not actual:
                    findings.append(_finding(
                        descriptor,
                        descriptor["description"] or f"章节 {key} 应章前分页",
                        bool(actual), True, str(key),
                    ))
            continue
        if checker == "ai.usage_declared":
            declared = bool(str(project.meta.get("ai_usage_declared", "")).strip())
            if not declared:
                findings.append(_finding(descriptor, descriptor["description"] or "尚未声明 AI 使用情况", False, True, "cover"))
            continue

        not_checked.append({
            **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
            "reason": "unsupported_checker",
            "checker": checker,
            "fixable": False,
        })
    return findings, not_checked


def _expectation(expected: dict) -> str:
    if "min" in expected and "max" in expected:
        return f"{expected['min']} - {expected['max']}"
    if "min" in expected:
        return f"不少于 {expected['min']}"
    if "max" in expected:
        return f"不超过 {expected['max']}"
    return "符合规范"
