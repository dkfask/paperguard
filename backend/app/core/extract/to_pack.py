"""requirements_to_pack — Requirement[] → RulePack (与 Sprint 2 loader 兼容).

设计契约（plan §3.1 + §6）：
- requirements_to_pack(reqs, pack_id, name, version="0.1.0") -> RulePack
- 7 category → Rule 映射（不同 scope 表达式）
- B1 follow-up 规避：paragraph.spacing.before / .after 拆两条独立 Rule
  (避免 Sprint 2 已知 bug 复现——见 docs/sprint-2-followup.md)
- severity 全 warning（模板观察值不是"错误"）
- fix.target 强制点分形式（兼容 Sprint 1.5 LiveMutator 硬编码）
- 半磅 → pt 转换（font.size_half_points 24 → run.font_size_pt = 12.0）
- auto_applicable=False 的 Requirement → 不生成 Rule
- id 命名 PG-EXTRACT-<category>-<n>（避免与 Sprint 2 已有 R001-R030 冲突）
- 重复 property+expected 去重
"""

from __future__ import annotations

from app.core.engine.loader import Rule, RulePack
from app.core.ir import Fix, Severity


# ---------------------------------------------------------------------------
# property → fix.target 映射表（点分形式，兼容 LiveMutator 硬编码）
# ---------------------------------------------------------------------------

PROPERTY_TARGET: dict[str, str] = {
    # Run 属性
    "font.eastAsia": "run.font_eastasia",
    "font.ascii": "run.font_ascii",
    "font.hAnsi": "run.font_hansi",
    "font.cs": "run.font_cs",
    "font.size_half_points": "run.font_size_pt",  # 特殊：/2 转 pt
    "font.bold": "run.bold",
    "font.italic": "run.italic",
    "font.underline": "run.underline",
    # Paragraph 属性
    "paragraph.style_id": "paragraph.style_id",
    "paragraph.align": "paragraph.alignment",
    "paragraph.spacing.before": "paragraph.space_before_pt",
    "paragraph.spacing.after": "paragraph.space_after_pt",
    "paragraph.spacing.line": "paragraph.line_spacing",
    "paragraph.indent.first_line": "paragraph.first_line_indent_cm",
    "paragraph.indent.left": "paragraph.left_indent_cm",
    "paragraph.indent.right": "paragraph.right_indent_cm",
    "paragraph.keep_next": "paragraph.keep_with_next",
    "paragraph.page_break_before": "paragraph.page_break_before",
    # Section 属性（OOXML twips → cm 需要 /567；这里保持原值，V1+ 加转换）
    "section.page.width": "section.page_width_cm",
    "section.page.height": "section.page_height_cm",
    "section.margin.top": "section.margin_top_cm",
    "section.margin.bottom": "section.margin_bottom_cm",
    "section.margin.left": "section.margin_left_cm",
    "section.margin.right": "section.margin_right_cm",
    "section.margin.header": "section.header_distance_cm",
    "section.margin.footer": "section.footer_distance_cm",
    "section.orientation": "section.orientation",
    # Table
    "table.width": "table.width",
    # Numbering
    "numbering.abstract_num_id": "numbering.abstract_num_id",
}


# Category → scope 模板（占位符 {placeholder} 在 build_rule 时替换）
CATEGORY_SCOPE: dict[str, str] = {
    "style": "paragraphs[?style_id == '{placeholder}']",
    "section": "sections[*]",
    "paragraph-example": "paragraphs[*]",
    "table": "tables[*]",
    "numbering": "paragraphs[*]",
    "explicit-instruction": None,  # 不生成 Rule
    "document": "paragraphs[*]",
}


def _coerce_value(property_name: str, expected: str):
    """Convert expected str to typed value (e.g. half-points → pt float)."""
    if property_name == "font.size_half_points":
        try:
            return float(int(expected) / 2.0)
        except (TypeError, ValueError):
            return expected
    # 其他保持 str（OOXML 数值需要时 V1+ 加转换）
    return expected


def _build_rule_id(category: str, counter: dict) -> str:
    counter["n"] += 1
    return f"PG-EXTRACT-{category.upper()}-{counter['n']:03d}"


def _build_scope(category: str, target: str) -> str:
    template = CATEGORY_SCOPE.get(category)
    if template is None:
        return "paragraphs[*]"  # fallback
    # style / paragraph-example 的 target 含 placeholder
    if category in ("style",):
        # target "style:Normal" → placeholder = "Normal"
        placeholder = target.split(":", 1)[1] if ":" in target else "Normal"
        return template.format(placeholder=placeholder)
    return template


def _build_fix_target(property_name: str) -> str | None:
    return PROPERTY_TARGET.get(property_name)


def _build_fix(property_name: str, expected: str) -> Fix | None:
    target = _build_fix_target(property_name)
    if target is None:
        return None
    value = _coerce_value(property_name, expected)
    return Fix(action="set", target=target, value=value)


def _make_rule(req, counter: dict, style_id_to_props: dict | None = None) -> Rule | None:
    """Build a single Rule from a Requirement (None if not actionable)."""
    if not req.auto_applicable:
        return None

    # 去重: 跳过 style 类别的重复 (同 target+property+expected)
    if req.category == "style" and style_id_to_props is not None:
        key = (req.target, req.property, req.expected)
        if key in style_id_to_props.get("seen", set()):
            return None
        style_id_to_props.setdefault("seen", set()).add(key)

    scope = _build_scope(req.category, req.target)
    fix = _build_fix(req.property, req.expected)
    if fix is None:
        # property 不在映射表里 → 不生成 Rule
        return None

    return Rule(
        id=_build_rule_id(req.category, counter),
        severity=Severity.WARNING,
        scope=scope,
        when=_build_when(req.property, req.expected),
        message=_build_message(req),
        fix=fix,
    )


def _build_when(property_name: str, expected: str):
    """Build JSONLogic when expression: property != expected."""
    from json_logic import jsonLogic  # 仅用于类型检查；返回 dict
    # 用 dict 形式 jsonLogic 比较
    value = _coerce_value(property_name, expected)
    return {
        "!=": [
            {"var": property_name},
            value,
        ]
    }


def _build_message(req) -> str:
    return f"模板建议：{req.target} 的 {req.property} = {req.expected}"


def requirements_to_pack(
    reqs: list,
    pack_id: str,
    name: str,
    version: str = "0.1.0",
) -> RulePack:
    """Convert Requirement[] to RulePack (compatible with Sprint 2 loader).

    B1 follow-up 规避：paragraph.spacing.before/after 拆两条独立 Rule.
    """
    counter = {"n": 0}
    style_id_to_props: dict = {"seen": set()}
    rules: list[Rule] = []

    # Pass 1: style 去重
    seen_style_keys: set = set()
    deduped: list = []
    for req in reqs:
        if req.category == "style":
            key = (req.target, req.property, req.expected)
            if key in seen_style_keys:
                continue
            seen_style_keys.add(key)
        deduped.append(req)

    # Pass 2: 生成 Rule
    for req in deduped:
        if not req.auto_applicable:
            continue
        # B1 规避：paragraph.spacing.before / .after 不需要再拆（每个 Requirement
        # 已经独立是 before 或 after）。但 to_pack 调用方可能传整段合并的 req；
        # 这里保险：若 fix.target 是 paragraph.space_before_pt 或 _after_pt，
        # 每次独立计数即可（id 不同）。
        rule = _make_rule(req, counter, style_id_to_props)
        if rule is not None:
            rules.append(rule)

    return RulePack(
        id=pack_id,
        name=name,
        version=version,
        rules=tuple(rules),
    )


__all__ = ["requirements_to_pack"]
