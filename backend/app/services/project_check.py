"""写作项目实时检查: 项目内容 → IR → 规则引擎。

把结构化写作项目转换为检查引擎的 IR Document, 复用 YAML 规则包
(与上传检查同一套规则), 让学生写作时就能看到格式问题。
"""

from __future__ import annotations

from datetime import datetime, timezone
import time

from app.core.engine import validate_docx
from app.core.engine.loader import load_pack
from app.core.ir import Document, DocumentMetadata, Paragraph, Run
from app.db import Store, new_id
from app.models.policy import CheckRun
from app.models.project import WritingProject
from app.models.template import PaperTemplate, SectionType, TextStyle
from app.services.policy_format import normalize_format_overrides
from app.services.requirement_check import check_requirements
from app.services.project_versions import project_content_hash

# 模板节类型 → 检查引擎 style_id
_STYLE_ID = {
    "heading1": "Heading 1",
    "heading2": "Heading 2",
    "heading3": "Heading 3",
    "abstract": "AbstractBody",
    "english_abstract": "EnglishAbstractBody",
    "keywords": "Keywords",
    "english_keywords": "EnglishKeywords",
    "body": "Body Text",
    "references": "Bibliography",
    "acknowledgement": "AcknowledgementBody",
    "appendix": "AppendixBody",
}


_HEADING_TYPES = {"heading1", "heading2", "heading3"}


def _style_key_for(stype: str, template: PaperTemplate) -> str:
    """Return the actual template style key used when building a paragraph."""
    if stype in template.styles:
        return stype
    if stype in _HEADING_TYPES and "heading1" in template.styles:
        return "heading1"
    return "body"


def _style_id_for(stype: str) -> str:
    """Return the rule-engine style id for a project section type."""
    return _STYLE_ID.get(stype, "Body Text")


def _style_color(style: TextStyle) -> str:
    """Return a six-digit color for the IR, defaulting to explicit black."""
    color = (style.color or "").strip().lstrip("#")
    return color if len(color) == 6 and all(c in "0123456789abcdefABCDEF" for c in color) else "000000"


def _is_keyword_paragraph(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith(("关键词", "关键字", "key words", "keywords"))


def project_to_ir(project: WritingProject, template: PaperTemplate, *,
                  include_block_ids: bool = False,
                  include_style_keys: bool = False):
    """项目 → IR Document, 同时返回 IR 段落索引 → 章节 key 的映射表。

    run 的字体属性按模板样式填充, 避免"模板未设置"被误报为违规。
    """
    paragraphs: list[Paragraph] = []
    index_to_section: list[str] = []
    index_to_block: list[str | None] = []
    index_to_style: list[str] = []
    table_no = 0

    def emit_section(sec) -> None:
        nonlocal table_no
        stype = sec.type

        # 标题节: 输出标题段落
        if stype in ("heading1", "heading2", "heading3"):
            if sec.title and sec.title.strip():
                paragraphs.append(_heading_par(sec.title, stype, template))
                index_to_section.append(sec.key)
                index_to_block.append(None)
                index_to_style.append(_style_key_for(stype, template))

        # 标题内容按正文检查; 摘要标题和摘要正文使用不同样式, 与导出保持一致。
        if stype in _HEADING_TYPES:
            content_type = "body"
        elif stype == "abstract":
            content_type = "abstract_body" if "abstract_body" in template.styles else "body"
        elif stype == "english_abstract":
            content_type = "english_abstract_body" if "english_abstract_body" in template.styles else "body"
        else:
            content_type = stype

        # 内容块
        for par in sec.paragraphs:
            if par.type.value == "paragraph" and par.text.strip():
                paragraph_type = content_type
                rule_type = "body" if stype in _HEADING_TYPES else stype
                if stype in {"abstract", "english_abstract"} and _is_keyword_paragraph(par.text):
                    paragraph_type = "keywords" if stype == "abstract" else "english_keywords"
                    rule_type = paragraph_type
                body_style = template.styles.get(paragraph_type) or template.styles.get("body")
                paragraphs.append(_body_par(
                    par.text, par, template, body_style, _style_id_for(rule_type),
                ))
                index_to_section.append(sec.key)
                index_to_block.append(par.id)
                index_to_style.append(_style_key_for(paragraph_type, template))
            elif par.type.value == "table" and par.table:
                for r, row in enumerate(par.table.cells):
                    for c, cell in enumerate(row):
                        if str(cell).strip():
                            paragraphs.append(_cell_par(str(cell), table_no, r, c, template))
                            index_to_section.append(sec.key)
                            index_to_block.append(None)
                            index_to_style.append("table_cell" if "table_cell" in template.styles else "body")
                table_no += 1
            # 图片块跳过(无文本检查项)

    # 项目 sections 是写作工作台和导出的唯一实际顺序。模板树只负责提供
    # 样式和初始结构, 因此手动新增或旧模板遗留的章节也必须进入检查。
    for section in project.sections:
        emit_section(section)

    ir = Document(
        sections=(),
        paragraphs=tuple(paragraphs),
        metadata=DocumentMetadata(title=project.title),
    )
    if include_style_keys:
        return ir, index_to_section, index_to_block, index_to_style
    if include_block_ids:
        return ir, index_to_section, index_to_block
    return ir, index_to_section


def _waiver_is_active(waiver: dict, now: datetime) -> bool:
    if waiver.get("status") != "approved":
        return False
    for key in ("effective_from",):
        raw = waiver.get(key)
        if raw:
            try:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                if now < value:
                    return False
            except ValueError:
                pass
    raw = waiver.get("effective_to")
    if raw:
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            if now >= value:
                return False
        except ValueError:
            pass
    return True


def check_project(project: WritingProject, template: PaperTemplate,
                  pack: str | None = None, *, store: Store | None = None,
                  performed_by: str = "") -> dict:
    """运行模板格式 + 项目规范检查，返回统一、带来源的报告。"""
    started = time.perf_counter()
    ir, index_to_section, index_to_block, index_to_style = project_to_ir(
        project, template, include_block_ids=True, include_style_keys=True,
    )
    snapshot = project.requirement_snapshot or {}
    pack_ids = [pack] if pack else list(dict.fromkeys(
        (snapshot.get("rule_packs") or template.rule_packs or ["GB-T-7713-1-2006"])
        + list(snapshot.get("rule_pack_refs") or [])
    ))

    result = []
    sources = []
    rule_pack_overrides = snapshot.get("rule_pack_overrides") or {}
    format_overrides = normalize_format_overrides(snapshot.get("format_overrides"))
    for pack_id in dict.fromkeys(pack_ids):
        pack_params = rule_pack_overrides.get(pack_id) or {}
        rule_pack = load_pack(pack_id, params=pack_params)
        sources.append({
            "source_type": "rule_pack",
            "id": rule_pack.id,
            "name": rule_pack.name,
            "version": rule_pack.version,
        })
        for f in validate_docx(ir, pack=pack_id, pack_params=pack_params):
            item = {
                "rule_id": f.rule_id,
                "severity": f.severity.value,
                "message": f.message,
                "location": {
                    "paragraph_index": f.location.paragraph_index,
                    "text_snippet": f.location.text_snippet,
                    "section_key": None,
                    "block_id": None,
                    "style_key": None,
                },
                "actual": f.actual,
                "expected": f.expected,
                "fix": {
                    "action": f.fix.action,
                    "target": f.fix.target,
                    "value": f.fix.value,
                } if f.fix else None,
                "source_type": "rule_pack",
                "source": rule_pack.name,
                "source_version": rule_pack.version,
                "enforce": "soft",
                "checker": f.rule_id,
            }
            idx = f.location.paragraph_index
            style_key = index_to_style[idx] if idx is not None and 0 <= idx < len(index_to_style) else ""
            format_field = _FORMAT_FIX_FIELDS.get(f.fix.target) if f.fix else None
            if style_key and format_field in format_overrides.get(style_key, {}):
                continue
            if idx is not None and 0 <= idx < len(index_to_section):
                item["section_key"] = index_to_section[idx]
                item["location"]["section_key"] = index_to_section[idx]
                item["location"]["block_id"] = index_to_block[idx]
                item["location"]["style_key"] = index_to_style[idx]
            result.append(item)

    policy_sources = snapshot.get("policy_sets", [])
    sources.extend({
        "source_type": "requirement",
        "id": item.get("id"),
        "name": item.get("name"),
        "level": item.get("level"),
        "version": item.get("version"),
    } for item in policy_sources)
    requirement_findings, not_checked = check_requirements(project, snapshot, template)
    applied = []
    format_item_ids = set(snapshot.get("format_item_ids") or [])
    for item in snapshot.get("resolved_items", []):
        if item.get("id") in format_item_ids:
            applied.append({
                "id": item.get("id", ""),
                "description": item.get("description") or "规范排版约束",
                "type": "format",
                "severity": item.get("severity", "info"),
                "enforce": item.get("enforce", "soft"),
                "source": item.get("source", "规范要求"),
                "source_version": item.get("source_version", ""),
                "reason": "format_override_applied",
                "applied": True,
            })
    for policy in policy_sources:
        for document in policy.get("documents", []):
            not_checked.append({
                "id": document.get("id") or document.get("filename"),
                "description": document.get("filename") or "规范参考文档",
                "type": "doc",
                "severity": "info",
                "enforce": "soft",
                "source": policy.get("name", "规范要求"),
                "source_version": policy.get("version", ""),
                "reason": "reference_document",
                "document": document,
                "fixable": False,
            })
    if snapshot.get("conflicts"):
        not_checked.extend({
            "id": f"conflict-{index + 1}",
            "description": conflict.get("resolution", "存在规范冲突，需要人工确认"),
            "type": "conflict",
            "severity": "warning",
            "enforce": "soft",
            "source": "规范解析",
            "source_version": "",
            "reason": "conflict",
            "detail": conflict,
            "fixable": False,
        } for index, conflict in enumerate(snapshot["conflicts"]))

    waived: list[dict] = []
    active_waivers = {
        waiver.get("requirement_item_id"): waiver
        for waiver in project.waivers
        if _waiver_is_active(waiver, datetime.now(timezone.utc))
    }
    for finding in requirement_findings:
        source_ids = {
            source.get("id")
            for source in finding.get("sources", [])
            if source.get("id")
        }
        source_ids.add(finding.get("requirement_item_id"))
        waiver = next((active_waivers[item_id] for item_id in source_ids if item_id in active_waivers), None)
        if waiver:
            waived.append({**finding, "waiver": waiver})
        else:
            result.append(finding)

    report = {
        "summary": {
            "total": len(result),
            "error": sum(1 for r in result if r["severity"] == "error"),
            "warning": sum(1 for r in result if r["severity"] == "warning"),
            "info": sum(1 for r in result if r["severity"] == "info"),
            "not_checked": len(not_checked),
            "applied": len(applied),
            "hard_failures": sum(1 for r in result if r.get("enforce") == "hard" and r["severity"] == "error"),
            "waived": len(waived),
        },
        "findings": result,
        "not_checked": not_checked,
        "applied": applied,
        "waived": waived,
        "sources": sources,
        "context": {
            "mode": "project-compliance",
            "project_title": project.title,
            "template_name": template.name,
            "template_version": template.version,
            "content_hash": project_content_hash(project),
            "requirement_snapshot_hash": snapshot.get("content_hash", ""),
            "organization_context": snapshot.get("context", {}),
            "conflict_count": len(snapshot.get("conflicts", [])),
            "format_override_count": snapshot.get("format_override_count", len(format_overrides)),
            "format_resolution": snapshot.get("format_resolution", {
                "policy_over_template": True,
                "description": "已明确的规范排版要求优先于模板样式",
            }),
        },
    }
    report["context"]["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if store is not None:
        run = CheckRun(
            id=new_id("check"),
            project_id=project.id,
            snapshot_hash=snapshot.get("content_hash", ""),
            content_hash=project_content_hash(project),
            engine_version="0.2.0",
            result=report,
            duration_ms=report["context"]["duration_ms"],
            performed_by=performed_by,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        store.save_check_run(run)
        current = store.get_project(project.id)
        if current is not None:
            next_check_run_ids = [run.id, *[item for item in current.check_run_ids if item != run.id]][:50]
            saved = store.update_project_check_runs(project.id, next_check_run_ids, current.revision)
            if saved is not None:
                project.check_run_ids = saved.check_run_ids
                project.updated_at = saved.updated_at
        report["run_id"] = run.id
    return report


_PROJECT_FIX_TARGETS = {
    "runs.*.bold": "bold",
    "run.bold": "bold",
    "runs.*.italic": "italic",
    "run.italic": "italic",
    "runs.*.underline": "underline",
    "run.underline": "underline",
    "runs.*.highlight": "highlight",
    "run.highlight": "highlight",
    "runs.*.vert_align": "vert_align",
    "run.vert_align": "vert_align",
}


_TEMPLATE_FIX_TARGETS = {
    "paragraph.line_spacing": "line_spacing",
    "paragraph.first_line_indent_cm": "first_line_indent_chars",
    "paragraph.alignment": "align",
    "paragraph.space_before_pt": "space_before_pt",
    "paragraph.space_after_pt": "space_after_pt",
    "paragraph.hanging_indent_cm": "hanging_indent_cm",
    "paragraph.is_page_break_before": "page_break_before",
    "paragraph.page_break_before": "page_break_before",
    "runs.*.font_eastasia": "font_zh",
    "run.font_eastasia": "font_zh",
    "runs.*.font_ascii": "font_en",
    "run.font_ascii": "font_en",
    "runs.*.font_hansi": "font_en",
    "run.font_hansi": "font_en",
    "runs.*.font_size_pt": "size_pt",
    "run.font_size_pt": "size_pt",
    "runs.*.color_hex": "color",
    "run.color_hex": "color",
    "runs.*.underline": "underline",
    "run.underline": "underline",
    "runs.*.bold": "bold",
    "run.bold": "bold",
    "runs.*.italic": "italic",
    "run.italic": "italic",
    "runs.*.highlight": "highlight",
    "run.highlight": "highlight",
    "runs.*.vert_align": "vert_align",
    "run.vert_align": "vert_align",
}


_FORMAT_FIX_FIELDS = {
    "paragraph.line_spacing": "line_spacing",
    "paragraph.first_line_indent_cm": "first_line_indent_chars",
    "paragraph.alignment": "align",
    "paragraph.space_before_pt": "space_before_pt",
    "paragraph.space_after_pt": "space_after_pt",
    "paragraph.hanging_indent_cm": "hanging_indent_cm",
    "paragraph.is_page_break_before": "page_break_before",
    "paragraph.page_break_before": "page_break_before",
    "runs.*.font_eastasia": "font_zh",
    "run.font_eastasia": "font_zh",
    "runs.*.font_ascii": "font_en",
    "run.font_ascii": "font_en",
    "runs.*.font_hansi": "font_en",
    "run.font_hansi": "font_en",
    "runs.*.font_size_pt": "size_pt",
    "run.font_size_pt": "size_pt",
}


def _template_fix_value(target: str, value):
    """Convert an IR fix value into the corresponding TextStyle value."""
    field = _TEMPLATE_FIX_TARGETS[target]
    if value is None:
        return None
    if field == "first_line_indent_chars":
        return round(float(value) / 0.37, 4)
    if field in {"line_spacing", "size_pt", "space_before_pt", "space_after_pt", "hanging_indent_cm"}:
        return float(value)
    if field == "page_break_before":
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return bool(value)
    return value


def _set_template_style_value(template: PaperTemplate, style_key: str,
                              target: str, value) -> bool:
    field = _TEMPLATE_FIX_TARGETS[target]
    style = template.styles.get(style_key)
    if style is None:
        return False
    normalized = _template_fix_value(target, value)
    if normalized is None:
        if field == "color":
            normalized = ""
        elif field == "underline":
            normalized = False
        elif field not in {"highlight", "vert_align"}:
            return False
    payload = style.model_dump(mode="json")
    if payload.get(field) == normalized:
        return False
    payload[field] = normalized
    updated = TextStyle.model_validate(payload)
    if updated == style:
        return False
    template.styles[style_key] = updated
    return True


def apply_project_fixes(project: WritingProject, report: dict,
                        template: PaperTemplate | None = None) -> tuple[int, list[str]]:
    """Apply safe fixes to content blocks and the project's bound template styles."""
    sections = {section.key: section for section in project.sections}
    fixed_rule_ids: list[str] = []
    fixed_count = 0
    touched_template_fields: set[tuple[str, str]] = set()

    def record(rule_id: str) -> None:
        nonlocal fixed_count
        fixed_count += 1
        if rule_id and rule_id not in fixed_rule_ids:
            fixed_rule_ids.append(rule_id)

    for finding in report.get("findings", []):
        fix = finding.get("fix") or {}
        if fix.get("action") != "set":
            continue
        field = _PROJECT_FIX_TARGETS.get(fix.get("target"))
        location = finding.get("location") or {}
        section = sections.get(location.get("section_key") or finding.get("section_key"))
        block_id = location.get("block_id")
        changed = False

        if field and block_id and section is not None:
            block = next((item for item in section.paragraphs if item.id == block_id), None)
            block_type = getattr(getattr(block, "type", None), "value", getattr(block, "type", None))
            if block is not None and block_type == "paragraph":
                value = _project_fix_value(field, fix.get("value"))
                if getattr(block, field) != value:
                    setattr(block, field, value)
                    changed = True

        target = fix.get("target")
        if template is not None and target in _TEMPLATE_FIX_TARGETS and section is not None:
            style_key = location.get("style_key")
            if not style_key:
                if section.type in _HEADING_TYPES and not block_id:
                    style_key = _style_key_for(section.type, template)
                elif section.type in _HEADING_TYPES:
                    style_key = _style_key_for("body", template)
                else:
                    style_key = _style_key_for(section.type, template)
            if style_key in template.styles:
                template_field = _TEMPLATE_FIX_TARGETS[target]
                touched_key = (style_key, template_field)
                style_changed = _set_template_style_value(template, style_key, target, fix.get("value"))
                if style_changed or touched_key in touched_template_fields:
                    touched_template_fields.add(touched_key)
                    changed = True

        if changed:
            record(finding.get("rule_id", ""))

    return fixed_count, fixed_rule_ids


def _project_fix_value(field: str, value):
    if field in {"bold", "italic", "underline"}:
        return bool(value)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _heading_par(title: str, stype: str, template: PaperTemplate) -> Paragraph:
    style_id = _style_id_for(stype)
    level = {"heading1": 1, "heading2": 2, "heading3": 3}.get(stype)
    style = template.styles.get(stype) or template.styles.get("heading1", None)
    if style is None:
        style = template.styles["body"]
    return Paragraph(
        style_id=style_id,
        text=title,
        level=level,
        runs=(Run(
            text=title,
            font_eastasia=style.font_zh,
            font_ascii=style.font_en,
            font_hansi=style.font_en,
            font_size_pt=style.size_pt,
            bold=style.bold,
            italic=style.italic,
            underline="single" if style.underline else None,
            color_hex=_style_color(style),
            highlight=style.highlight,
            vert_align=style.vert_align,
        ),),
        line_spacing=style.line_spacing,
        space_before_pt=style.space_before_pt,
        space_after_pt=style.space_after_pt,
        alignment=style.align.value,
        is_page_break_before=style.page_break_before,
        first_line_indent_cm=0.0,
        hanging_indent_cm=style.hanging_indent_cm,
    )


def _body_par(text: str, par, template: PaperTemplate, style=None, style_id: str = "Body Text") -> Paragraph:
    style = style or template.styles.get("body") or template.styles["body"]
    return Paragraph(
        style_id=style_id,
        text=text,
        level=None,
        runs=(Run(
            text=text,
            font_eastasia=style.font_zh,
            font_ascii=style.font_en,
            font_hansi=style.font_en,
            font_size_pt=style.size_pt,
            bold=bool(style.bold or par.bold),
            italic=bool(style.italic or par.italic),
            underline="single" if style.underline or par.underline else None,
            color_hex=_style_color(style),
            highlight=par.highlight or style.highlight,
            vert_align=par.vert_align or style.vert_align,
        ),),
        line_spacing=style.line_spacing,
        space_before_pt=style.space_before_pt,
        space_after_pt=style.space_after_pt,
        first_line_indent_cm=(
            0.0 if style.hanging_indent_cm else style.first_line_indent_chars * 0.37
        ),
        hanging_indent_cm=style.hanging_indent_cm,
        alignment=style.align.value,
    )


def _cell_par(text: str, table_index: int, row: int, col: int,
             template: PaperTemplate) -> Paragraph:
    style = template.styles.get("table_cell") or template.styles["body"]
    return Paragraph(
        style_id="Table Cell",
        text=text,
        level=None,
        runs=(Run(text=text, font_eastasia=style.font_zh,
                  font_ascii=style.font_en, font_hansi=style.font_en,
                  font_size_pt=style.size_pt, bold=style.bold,
                  italic=style.italic,
                  underline="single" if style.underline else None,
                  color_hex=_style_color(style),
                  highlight=style.highlight,
                  vert_align=style.vert_align),),
        line_spacing=style.line_spacing,
        space_before_pt=style.space_before_pt,
        space_after_pt=style.space_after_pt,
        alignment=style.align.value,
        table_index=table_index,
        table_row_index=row,
        table_cell_index=col,
        first_line_indent_cm=(
            0.0 if style.hanging_indent_cm else style.first_line_indent_chars * 0.37
        ),
        hanging_indent_cm=style.hanging_indent_cm,
    )
