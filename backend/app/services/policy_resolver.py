"""规范绑定、层级继承、冲突检测与项目快照解析。"""

from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.db import Store
from app.models.policy import RequirementLevel, RequirementSet, RequirementStatus
from app.models.template import PaperTemplate, TextStyle
from app.services.policy_format import (
    extract_format_overrides,
    extract_layout_overrides,
    format_override_count,
    is_format_requirement,
    merge_format_overrides,
    normalize_format_overrides,
    normalize_layout_overrides,
)
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
    if (item.get("params") or {}).get("format_override"):
        return f"format|{item.get('id') or 'unnamed'}"
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


def _requirement_format_overrides(requirement_set: RequirementSet) -> dict[str, dict[str, Any]]:
    """Return explicit format fields, supplemented by the preserved source text."""
    return merge_format_overrides(
        requirement_set.format_overrides,
        extract_format_overrides(requirement_set.markdown_content),
    )


def _requirement_layout_overrides(requirement_set: RequirementSet) -> dict[str, float]:
    """Return explicit page settings, supplemented by the preserved source text."""
    result = normalize_layout_overrides(requirement_set.layout_overrides)
    result.update(extract_layout_overrides(requirement_set.markdown_content))
    return result


def _style_override_properties(fields: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    simple = {
        "font_zh": "font.eastAsia",
        "font_en": "font.ascii",
        "bold": "font.bold",
        "italic": "font.italic",
        "color": "font.color",
        "align": "paragraph.align",
        "page_break_before": "paragraph.page_break_before",
    }
    for field, property_name in simple.items():
        if field in fields:
            value = fields[field]
            if field == "color" and not value:
                value = "auto"
            properties[property_name] = value
    if "font_en" in fields:
        properties["font.hAnsi"] = fields["font_en"]
        properties["font.cs"] = fields["font_en"]
    if "size_pt" in fields:
        properties["font.size"] = {"value": fields["size_pt"], "unit": "pt"}
    if "underline" in fields:
        properties["font.underline"] = "single" if fields["underline"] else "none"
    if "highlight" in fields:
        properties["font.highlight"] = fields["highlight"] or "none"
    if "vert_align" in fields:
        properties["font.vertical_alignment"] = fields["vert_align"] or "baseline"
    if "line_spacing" in fields:
        properties["paragraph.spacing.line"] = {
            "value": fields["line_spacing"],
            "kind": fields.get("line_spacing_rule", "auto"),
            "unit": "multiple" if fields.get("line_spacing_rule", "auto") == "auto" else "pt",
        }
    for field, property_name in (
        ("space_before_pt", "paragraph.spacing.before"),
        ("space_after_pt", "paragraph.spacing.after"),
    ):
        if field in fields:
            properties[property_name] = {"value": fields[field], "unit": "pt"}
    if "first_line_indent_chars" in fields:
        properties["paragraph.indent.first_line"] = {
            "value": fields["first_line_indent_chars"], "unit": "character",
        }
    if "hanging_indent_cm" in fields:
        properties["paragraph.indent.hanging"] = {
            "value": fields["hanging_indent_cm"] * 72 / 2.54, "unit": "pt",
        }
    return properties


def _merge_profile_style(properties: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    fields = dict(fields)
    if {"line_spacing", "line_spacing_rule"}.intersection(fields):
        previous = properties.get("paragraph.spacing.line") or {}
        fields.setdefault("line_spacing", previous.get("value", 1.5))
        fields.setdefault("line_spacing_rule", previous.get("kind", "auto"))
    return {**properties, **_style_override_properties(fields)}


def apply_requirement_format_overrides(
    template: PaperTemplate,
    snapshot: dict | None,
) -> PaperTemplate:
    """Apply frozen policy styles over a template without mutating the stored template."""
    overrides = normalize_format_overrides((snapshot or {}).get("format_overrides"))
    layout_overrides = normalize_layout_overrides((snapshot or {}).get("layout_overrides"))
    if not overrides and not layout_overrides:
        return template

    source_execution = template.source_execution
    profile_tools = None
    if source_execution:
        from app.services.source_template_profile import ProfileError, digest, execution_basis, sign_profile

        profile_tools = (digest, execution_basis, sign_profile)
        digest_profile = sign_profile(source_execution)
        if (not hmac.compare_digest(str(source_execution.get("signature", "")), digest_profile["signature"])
                or source_execution.get("profile_sha256") != digest_profile["profile_sha256"]):
            raise ProfileError("模板执行配置签名无效，不能应用规范格式覆盖")
        if source_execution.get("template_basis_sha256") != digest(execution_basis(template)):
            raise ProfileError("模板格式或章节已变化，不能应用规范格式覆盖")

    payload = template.model_dump(mode="json")
    styles = payload.setdefault("styles", {})
    for style_key, fields in overrides.items():
        base = dict(styles.get(style_key) or styles.get("body") or TextStyle().model_dump(mode="json"))
        base.update(fields)
        styles[style_key] = TextStyle.model_validate(base).model_dump(mode="json")
    if layout_overrides:
        page_layout = dict(payload.get("page_layout") or {})
        page_layout.update(layout_overrides)
        payload["page_layout"] = page_layout

    # Imported templates may put a one-off style on a section node. Apply the
    # same policy to that node so a node-level template exception cannot win.
    def apply_to_nodes(nodes: list[dict]) -> None:
        for node in nodes:
            style_key = str(node.get("type") or "")
            if style_key in overrides and node.get("style"):
                node_style = dict(node["style"])
                node_style.update(overrides[style_key])
                node["style"] = TextStyle.model_validate(node_style).model_dump(mode="json")
            apply_to_nodes(node.get("children") or [])

    apply_to_nodes(payload.get("sections") or [])
    effective = PaperTemplate.model_validate(payload)
    if source_execution and profile_tools:
        digest, execution_basis, sign_profile = profile_tools
        import io
        import zipfile
        from app.core.extract.xml_utils import load_part
        from app.services.source_template_profile import commentless_source, freeze_protected_structure, verify_profile
        from app.services.template_source import resolve_template_source

        source_path = resolve_template_source(template.source_docx)
        source_bytes = source_path.read_bytes() if source_path else None
        verify_profile(template, source_bytes)
        profile = deepcopy(source_execution)
        profile["body_properties"] = _merge_profile_style(
            dict(profile.get("body_properties") or {}), overrides.get("body", {}),
        )
        content_styles = {
            "heading1": "body", "heading2": "body", "heading3": "body",
            "body": "body", "references": "references",
            "acknowledgement": "acknowledgement", "appendix": "appendix",
            "abstract": "abstract_body", "english_abstract": "english_abstract_body",
            "keywords": "keywords", "english_keywords": "english_keywords",
        }
        for heading in profile.get("headings", []):
            kind = str(heading.get("kind") or "")
            content_style = content_styles.get(kind, "")
            if content_style in overrides:
                heading["properties"] = _merge_profile_style(
                    dict(heading.get("properties") or {}), overrides[content_style],
                )
            if kind in overrides and kind not in {"heading1", "heading2", "heading3"}:
                heading["title_properties"] = _merge_profile_style(
                    dict(heading.get("title_properties") or {}), overrides[kind],
                )
                if not heading.get("inline"):
                    heading["properties"] = _merge_profile_style(
                        dict(heading.get("properties") or {}), overrides[kind],
                    )
            if kind in {"heading1", "heading2", "heading3"} and kind in overrides:
                heading["title_properties"] = _merge_profile_style(
                    dict(heading.get("title_properties") or {}), overrides[kind],
                )
                heading["title_overridden"] = True
        object_styles = {
            "figure_caption": "figure_caption",
            "table_caption": "table_caption",
            "table_cell": "table_text",
        }
        for style_key, profile_key in object_styles.items():
            if style_key in overrides:
                profile["object_properties"][profile_key] = _merge_profile_style(
                    dict(profile["object_properties"].get(profile_key) or {}), overrides[style_key],
                )
        profile["policy_layout_overrides"] = {**profile.get("policy_layout_overrides", {}), **layout_overrides}
        with zipfile.ZipFile(io.BytesIO(commentless_source(source_bytes))) as package:
            profile["protected_document_sha256"] = freeze_protected_structure(load_part(package, "word/document.xml"), profile)
        profile["template_basis_sha256"] = digest(execution_basis(effective))
        profile["policy_snapshot_sha256"] = digest(snapshot or {})
        profile["policy_format_overrides_sha256"] = digest({
            "format_overrides": overrides,
            "layout_overrides": layout_overrides,
        })
        effective.source_execution = sign_profile(profile)
    return effective


def hydrate_snapshot_format_overrides(snapshot: dict | None) -> dict:
    """Backfill format metadata for projects created before policy styles existed."""
    result = deepcopy(snapshot or {})
    if not result:
        return result
    overrides = normalize_format_overrides(result.get("format_overrides"))
    layout_overrides = normalize_layout_overrides(result.get("layout_overrides"))
    format_item_ids = list(result.get("format_item_ids") or [])
    for policy in result.get("policy_sets", []):
        policy_overrides = merge_format_overrides(policy.get("format_overrides"))
        policy_text = str(policy.get("markdown_content") or "")
        layout_overrides.update(normalize_layout_overrides(policy.get("layout_overrides")))
        layout_overrides.update(extract_layout_overrides(policy_text))
        for item in policy.get("items", []):
            description = item.get("description") or ""
            if (item.get("params") or {}).get("format_override") or is_format_requirement(description):
                if item.get("id") and item["id"] not in format_item_ids:
                    format_item_ids.append(item["id"])
            policy_text += f"\n{description}"
        overrides = merge_format_overrides(
            overrides,
            policy_overrides,
            extract_format_overrides(policy_text),
        )
    result["format_overrides"] = overrides
    result["layout_overrides"] = layout_overrides
    result["layout_override_count"] = len(layout_overrides)
    result["format_override_count"] = format_override_count(overrides)
    result["format_item_ids"] = format_item_ids
    result.setdefault("format_override_sources", {})
    result.setdefault("format_resolution", {
        "policy_over_template": True,
        "description": "已明确的规范排版要求优先于模板样式",
    })
    if result.get("content_hash"):
        hash_payload = dict(result)
        hash_payload.pop("content_hash", None)
        result["content_hash"] = content_hash(hash_payload)
    return result


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
    all_format_overrides: dict[str, dict[str, Any]] = {}
    all_layout_overrides: dict[str, float] = {}
    format_override_sources: dict[str, list[dict[str, Any]]] = {}
    format_item_ids: list[str] = []
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
            "markdown_content": requirement_set.markdown_content,
            "format_overrides": _requirement_format_overrides(requirement_set),
            "layout_overrides": _requirement_layout_overrides(requirement_set),
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
        set_format_overrides = _requirement_format_overrides(requirement_set)
        all_format_overrides = merge_format_overrides(all_format_overrides, set_format_overrides)
        all_layout_overrides.update(_requirement_layout_overrides(requirement_set))
        for style_key in set_format_overrides:
            format_override_sources.setdefault(style_key, []).append(source_detail)
        for item in requirement_set.items:
            item_payload = item.model_dump(mode="json")
            item_payload["source_detail"] = source_detail
            item_payload["source"] = requirement_set.name
            item_payload["source_version"] = requirement_set.version
            all_items.append(item_payload)
            if item.params.get("format_override") or is_format_requirement(item.description):
                format_item_ids.append(item.id)
            if item.rule_pack_id:
                rule_pack_refs.append(item.rule_pack_id)
                if item.rule_pack_params:
                    pack_overrides = rule_pack_overrides.setdefault(item.rule_pack_id, {})
                    for rule_id, override in item.rule_pack_params.items():
                        pack_overrides[str(rule_id)] = override

    template_instruction_items: list[dict[str, Any]] = []
    template_instruction_identity: dict[str, Any] = {}
    execution_profile = template.source_execution or {}
    if execution_profile.get("content_requirements"):
        # These entries were compiled from the exact source/specification and
        # signed into the teacher-published template profile. Do not trust a
        # client-provided or stale profile when freezing a student project.
        from app.services.source_template_profile import ProfileError, verify_profile
        from app.services.template_source import resolve_template_source

        source_path = resolve_template_source(template.source_docx)
        try:
            verified_profile = verify_profile(
                template,
                source_path.read_bytes() if source_path is not None else None,
            )
        except ProfileError as exc:
            raise ValueError("Word 内容规范签名或母版摘要无效，不能创建写作快照") from exc
        template_instruction_items = list(verified_profile.get("content_requirements") or [])
        template_instruction_identity = {
            "source_sha256": verified_profile.get("source_sha256", ""),
            "specification_sha256": verified_profile.get("specification_sha256", ""),
            "profile_sha256": verified_profile.get("profile_sha256", ""),
            "compiler_version": verified_profile.get("content_rule_compiler_version", ""),
            "confirmed_rule_ids": list(verified_profile.get("confirmed_content_rule_ids") or []),
        }
        for item in template_instruction_items:
            if item.get("type") != "auto":
                continue
            item_payload = dict(item)
            item_payload["source_detail"] = {
                "id": item_payload["id"],
                "name": item_payload.get("source", template.name),
                "level": "template",
                "version": item_payload.get("source_version", template.version),
                "provenance": item_payload.get("provenance", {}),
            }
            all_items.append(item_payload)

    resolved_items, conflicts = _merge_items(all_items)
    # Manual review items use distinct IDs but have no checker; append them
    # after checker de-duplication so they cannot collapse into one row.
    resolved_items.extend(
        dict(item) for item in template_instruction_items if item.get("type") != "auto"
    )
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
        "template_instruction_identity": template_instruction_identity,
        "template_instruction_items": template_instruction_items,
        "resolved_items": resolved_items,
        "format_overrides": all_format_overrides,
        "layout_overrides": all_layout_overrides,
        "layout_override_count": len(all_layout_overrides),
        "format_override_sources": format_override_sources,
        "format_override_count": format_override_count(all_format_overrides),
        "format_item_ids": list(dict.fromkeys(format_item_ids)),
        "format_resolution": {
            "policy_over_template": True,
            "description": "已明确的规范排版要求优先于模板样式",
        },
        "conflicts": conflicts,
        "student_id": student_id or "",
        "teacher_id": teacher_id or "",
    }
    snapshot["content_hash"] = content_hash(snapshot)
    return snapshot


__all__ = [
    "apply_requirement_format_overrides",
    "build_requirement_snapshot",
    "content_hash",
    "hydrate_requirement_set",
    "hydrate_snapshot_format_overrides",
]
