"""规范绑定、层级继承、冲突检测与项目快照解析。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.db import Store
from app.models.policy import RequirementLevel, RequirementSet, RequirementStatus
from app.models.template import PaperTemplate
from app.services.policy_parser import items_from_markdown


_LEVEL_RANK = {
    RequirementLevel.SCHOOL.value: 10,
    RequirementLevel.COLLEGE.value: 20,
    RequirementLevel.MAJOR.value: 30,
    RequirementLevel.COURSE.value: 35,
    RequirementLevel.BATCH.value: 40,
    RequirementLevel.TEACHER.value: 50,
    RequirementLevel.PROJECT.value: 60,
}


def content_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hydrate_requirement_set(requirement_set: RequirementSet) -> RequirementSet:
    """允许 Markdown frontmatter 作为规则录入入口，但不覆盖显式结构化规则。"""
    if requirement_set.items or not requirement_set.markdown_content:
        return requirement_set
    requirement_set.items = items_from_markdown(requirement_set.markdown_content)
    return requirement_set


def _as_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _is_effective(requirement_set: RequirementSet, now: datetime) -> bool:
    if requirement_set.status != RequirementStatus.PUBLISHED:
        return False
    effective_from = _as_utc(requirement_set.effective_from) if requirement_set.effective_from else None
    effective_to = _as_utc(requirement_set.effective_to) if requirement_set.effective_to else None
    if effective_from and now < effective_from:
        return False
    if effective_to and now >= effective_to:
        return False
    return True


def _context_value(context: dict[str, str], scope_type: str) -> str:
    aliases = {
        "school": "school_id",
        "college": "college_id",
        "major": "major_id",
        "course": "course_id",
        "batch": "batch_id",
        "student": "student_id",
        "teacher": "teacher_id",
        "project": "project_id",
    }
    return str(context.get(scope_type) or context.get(aliases.get(scope_type, scope_type)) or "")


def _binding_matches(binding: dict, context: dict[str, str]) -> bool:
    if not binding.get("enabled", True):
        return False
    scope_type = str(binding.get("scope_type") or "global")
    scope_id = str(binding.get("scope_id") or "")
    return scope_type in ("global", "all") or (bool(scope_id) and _context_value(context, scope_type) == scope_id)


def _set_applies(requirement_set: RequirementSet, context: dict[str, str]) -> tuple[bool, list[str]]:
    """返回是否适用及命中的绑定 ID。没有绑定的组织规范不会静默全局生效。"""
    if requirement_set.course_id and requirement_set.course_id != context.get("course_id"):
        return False, []
    if requirement_set.stage and requirement_set.stage != context.get("stage"):
        return False, []
    matched = [binding.id for binding in requirement_set.bindings if _binding_matches(binding.model_dump(), context)]
    if matched:
        return True, matched

    # 兼容旧数据：教师要求按指导教师继承，项目要求按 project_id 绑定。
    level = requirement_set.level.value
    if level == RequirementLevel.SCHOOL.value and not requirement_set.scope_id:
        return True, []
    if level == RequirementLevel.TEACHER.value:
        return bool(requirement_set.owner_id and requirement_set.owner_id == context.get("teacher_id")) and (
            not requirement_set.applies_to.get("student_ids")
            or context.get("student_id") in requirement_set.applies_to.get("student_ids", [])
        ), []
    if level == RequirementLevel.PROJECT.value:
        return bool(requirement_set.scope_id and requirement_set.scope_id == context.get("project_id")), []
    if requirement_set.scope_id:
        return _context_value(context, level) == requirement_set.scope_id, []
    return False, []


def _family_id(requirement_set: RequirementSet) -> str:
    return requirement_set.family_id or requirement_set.id


def _latest_effective_set(store: Store, requirement_set: RequirementSet, now: datetime) -> RequirementSet | None:
    family_id = _family_id(requirement_set)
    candidates = [
        value for value in store.list_requirement_sets()
        if _family_id(value) == family_id and _is_effective(value, now)
    ]
    return max(candidates, key=lambda value: (value.updated_at or value.created_at or "", value.version)) if candidates else None


def _item_key(item: dict) -> str:
    return f"{item.get('target') or 'document'}|{item.get('checker') or ''}"


def _severity_rank(value: str) -> int:
    return {"info": 1, "warning": 2, "error": 3}.get(value, 0)


def _merge_items(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """同一目标/检查器去重，取更严格阈值并保留冲突来源。"""
    merged: dict[str, dict] = {}
    conflicts: list[dict] = []
    for item in items:
        key = _item_key(item)
        current = merged.get(key)
        incoming_detail = dict(item.get("source_detail") or {})
        if current is None:
            value = dict(item)
            value["sources"] = [incoming_detail]
            merged[key] = value
            continue

        current["sources"].append(incoming_detail)
        current["enforce"] = "hard" if "hard" in (current["enforce"], item["enforce"]) else "soft"
        if _severity_rank(item["severity"]) > _severity_rank(current["severity"]):
            current["severity"] = item["severity"]
            current["description"] = item["description"] or current["description"]
            current["id"] = item["id"]

        params = current.setdefault("params", {})
        incoming = item.get("params") or {}
        for key_name in ("min", "min_count"):
            values = [v for v in (params.get(key_name), incoming.get(key_name)) if isinstance(v, (int, float))]
            if len(values) > 1 and values[0] != values[1]:
                conflicts.append({
                    "key": key,
                    "parameter": key_name,
                    "kind": "threshold_merge",
                    "values": values,
                    "sources": [current["sources"][-2], incoming_detail],
                    "resolution": "取更严格的较大下限",
                })
            if values:
                params[key_name] = max(values)
        for key_name in ("max",):
            values = [v for v in (params.get(key_name), incoming.get(key_name)) if isinstance(v, (int, float))]
            if len(values) > 1 and values[0] != values[1]:
                conflicts.append({
                    "key": key,
                    "parameter": key_name,
                    "kind": "threshold_merge",
                    "values": values,
                    "sources": [current["sources"][-2], incoming_detail],
                    "resolution": "取更严格的较小上限",
                })
            if values:
                params[key_name] = min(values)
        if isinstance(incoming.get("years"), int):
            params["years"] = max(int(params.get("years", 0)), incoming["years"])
        if "min" in params and "max" in params and params["min"] > params["max"]:
            conflicts.append({
                "key": key,
                "kind": "incompatible_threshold",
                "values": {"min": params["min"], "max": params["max"]},
                "sources": current["sources"],
                "resolution": "保留全部来源并提示人工确认",
            })

    clean: list[dict] = []
    for value in merged.values():
        value.pop("source_detail", None)
        clean.append(value)
    return clean, conflicts


def build_requirement_snapshot(
    store: Store,
    template: PaperTemplate,
    *,
    teacher_id: str | None = None,
    student_id: str | None = None,
    organization_context: dict[str, str] | None = None,
    project_id: str | None = None,
    stage: str = "",
) -> dict:
    """解析模板绑定、组织绑定与教师要求，返回可复现的不可变快照。"""
    now = datetime.now(timezone.utc)
    context = {**(template.organization_context or {}), **(organization_context or {})}
    context.update({key: value for key, value in {
        "teacher_id": teacher_id or "",
        "student_id": student_id or "",
        "project_id": project_id or "",
        "stage": stage or "",
    }.items() if value})
    sets: list[RequirementSet] = []
    matched_bindings: dict[str, list[str]] = {}
    seen: set[str] = set()
    seen_families: set[str] = set()

    for set_id in template.requirement_set_ids:
        requirement_set = store.get_requirement_set(set_id)
        requirement_set = _latest_effective_set(store, requirement_set, now) if requirement_set else None
        if requirement_set and _family_id(requirement_set) not in seen_families:
            sets.append(hydrate_requirement_set(requirement_set))
            matched_bindings[requirement_set.id] = ["template"]
            seen.add(requirement_set.id)
            seen_families.add(_family_id(requirement_set))

    for requirement_set in store.list_requirement_sets():
        if _family_id(requirement_set) in seen_families or not _is_effective(requirement_set, now):
            continue
        applies, binding_ids = _set_applies(requirement_set, context)
        if applies:
            latest = _latest_effective_set(store, requirement_set, now)
            if latest is None or _family_id(latest) in seen_families:
                continue
            requirement_set = latest
            sets.append(hydrate_requirement_set(requirement_set))
            matched_bindings[requirement_set.id] = binding_ids
            seen.add(requirement_set.id)
            seen_families.add(_family_id(requirement_set))

    policy_snapshots: list[dict] = []
    all_items: list[dict] = []
    rule_pack_refs: list[str] = []
    rule_pack_overrides: dict[str, dict[str, Any]] = {}
    for requirement_set in sorted(sets, key=lambda value: _LEVEL_RANK.get(value.level.value, 0)):
        set_payload = requirement_set.model_dump(mode="json")
        set_payload["content_hash"] = content_hash(set_payload)
        policy_snapshots.append({
            "id": requirement_set.id,
            "family_id": _family_id(requirement_set),
            "name": requirement_set.name,
            "level": requirement_set.level.value,
            "scope": requirement_set.scope,
            "scope_id": requirement_set.scope_id,
            "version": requirement_set.version,
            "content_hash": set_payload["content_hash"],
            "description": requirement_set.description,
            "effective_from": requirement_set.effective_from,
            "effective_to": requirement_set.effective_to,
            "stage": requirement_set.stage,
            "deadline_at": requirement_set.deadline_at,
            "binding_ids": matched_bindings.get(requirement_set.id, []),
            "items": [item.model_dump(mode="json") for item in requirement_set.items],
            "documents": [doc.model_dump(mode="json") for doc in requirement_set.documents],
        })
        source_detail = {
            "id": requirement_set.id,
            "name": requirement_set.name,
            "level": requirement_set.level.value,
            "version": requirement_set.version,
            "rank": _LEVEL_RANK.get(requirement_set.level.value, 0),
        }
        for item in requirement_set.items:
            item_payload = item.model_dump(mode="json")
            item_payload["source_detail"] = source_detail
            item_payload["source"] = requirement_set.name
            item_payload["source_version"] = requirement_set.version
            all_items.append(item_payload)
            if item.rule_pack_id:
                rule_pack_refs.append(item.rule_pack_id)
                if item.rule_pack_params:
                    pack_overrides = rule_pack_overrides.setdefault(item.rule_pack_id, {})
                    for rule_id, override in item.rule_pack_params.items():
                        pack_overrides[str(rule_id)] = override

    resolved_items, conflicts = _merge_items(all_items)
    snapshot = {
        "snapshot_created_at": now.isoformat(timespec="seconds"),
        "template": {
            "id": template.id,
            "version": template.version,
            "content_hash": content_hash(template.model_dump(mode="json")),
        },
        "context": context,
        "rule_packs": list(dict.fromkeys(template.rule_packs or ["GB-T-7713-1-2006"])),
        "rule_pack_refs": list(dict.fromkeys(rule_pack_refs)),
        "rule_pack_overrides": rule_pack_overrides,
        "policy_sets": policy_snapshots,
        "resolved_items": resolved_items,
        "conflicts": conflicts,
        "student_id": student_id or "",
        "teacher_id": teacher_id or "",
    }
    snapshot["content_hash"] = content_hash(snapshot)
    return snapshot


__all__ = ["build_requirement_snapshot", "content_hash", "hydrate_requirement_set"]
