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
from app.models.template import PaperTemplate, SectionType
from app.services.requirement_check import check_requirements

# 模板节类型 → 检查引擎 style_id
_STYLE_ID = {
    "heading1": "Heading 1",
    "heading2": "Heading 2",
    "heading3": "Heading 3",
    "abstract": "AbstractBody",
    "english_abstract": "EnglishAbstractBody",
    "keywords": "Keywords",
    "english_keywords": "Keywords",
    "body": "Body Text",
    "references": "Bibliography",
    "acknowledgement": "Body Text",
    "appendix": "Body Text",
}


def project_to_ir(project: WritingProject, template: PaperTemplate, *, include_block_ids: bool = False):
    """项目 → IR Document, 同时返回 IR 段落索引 → 章节 key 的映射表。

    run 的字体属性按模板样式填充, 避免"模板未设置"被误报为违规。
    """
    paragraphs: list[Paragraph] = []
    index_to_section: list[str] = []
    index_to_block: list[str | None] = []
    proj_map = {s.key: s for s in project.sections}
    table_no = 0

    def walk(node) -> None:
        nonlocal table_no
        sec = proj_map.get(node.key)
        if sec is None:
            return
        stype = sec.type

        # 标题节: 输出标题段落
        if stype in ("heading1", "heading2", "heading3"):
            if sec.title and sec.title.strip():
                paragraphs.append(_heading_par(sec.title, stype, template))
                index_to_section.append(sec.key)
                index_to_block.append(None)
            stype = "body"

        body_style = template.styles.get(stype) or template.styles.get("body")
        style_id = {
            "abstract": "AbstractBody",
            "references": "Bibliography",
            "body": "Body Text",
            "acknowledgement": "Body Text",
            "appendix": "Body Text",
        }.get(stype, "Body Text")

        # 内容块
        for par in sec.paragraphs:
            if par.type.value == "paragraph" and par.text.strip():
                paragraphs.append(_body_par(par.text, par, template, body_style, style_id))
                index_to_section.append(sec.key)
                index_to_block.append(par.id)
            elif par.type.value == "table" and par.table:
                for r, row in enumerate(par.table.cells):
                    for c, cell in enumerate(row):
                        if str(cell).strip():
                            paragraphs.append(_cell_par(str(cell), table_no, r, c, template))
                            index_to_section.append(sec.key)
                            index_to_block.append(None)
                table_no += 1
            # 图片块跳过(无文本检查项)

        for child in node.children:
            walk(child)

    for root in template.sections:
        walk(root)

    ir = Document(
        sections=(),
        paragraphs=tuple(paragraphs),
        metadata=DocumentMetadata(title=project.title),
    )
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
    ir, index_to_section, index_to_block = project_to_ir(project, template, include_block_ids=True)
    snapshot = project.requirement_snapshot or {}
    pack_ids = [pack] if pack else list(dict.fromkeys(
        (snapshot.get("rule_packs") or template.rule_packs or ["GB-T-7713-1-2006"])
        + list(snapshot.get("rule_pack_refs") or [])
    ))

    result = []
    sources = []
    rule_pack_overrides = snapshot.get("rule_pack_overrides") or {}
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
            if idx is not None and 0 <= idx < len(index_to_section):
                item["section_key"] = index_to_section[idx]
                item["location"]["section_key"] = index_to_section[idx]
                item["location"]["block_id"] = index_to_block[idx]
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
            "hard_failures": sum(1 for r in result if r.get("enforce") == "hard" and r["severity"] == "error"),
            "waived": len(waived),
        },
        "findings": result,
        "not_checked": not_checked,
        "waived": waived,
        "sources": sources,
        "context": {
            "mode": "project-compliance",
            "project_title": project.title,
            "template_name": template.name,
            "template_version": template.version,
            "requirement_snapshot_hash": snapshot.get("content_hash", ""),
            "organization_context": snapshot.get("context", {}),
            "conflict_count": len(snapshot.get("conflicts", [])),
        },
    }
    report["context"]["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if store is not None:
        run = CheckRun(
            id=new_id("check"),
            project_id=project.id,
            snapshot_hash=snapshot.get("content_hash", ""),
            engine_version="0.2.0",
            result=report,
            duration_ms=report["context"]["duration_ms"],
            performed_by=performed_by,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        store.save_check_run(run)
        project.check_run_ids = [run.id, *[item for item in project.check_run_ids if item != run.id]][:50]
        store.save_project(project)
        report["run_id"] = run.id
    return report


_PROJECT_FIX_TARGETS = {
    "runs.*.bold": "bold",
    "run.bold": "bold",
    "runs.*.italic": "italic",
    "run.italic": "italic",
    "runs.*.underline": "underline",
    "run.underline": "underline",
}


def apply_project_fixes(project: WritingProject, report: dict) -> tuple[int, list[str]]:
    """Apply only safe inline-format fixes that belong to writing content blocks."""
    sections = {section.key: section for section in project.sections}
    fixed_rule_ids: list[str] = []

    for finding in report.get("findings", []):
        fix = finding.get("fix") or {}
        if fix.get("action") != "set":
            continue
        field = _PROJECT_FIX_TARGETS.get(fix.get("target"))
        location = finding.get("location") or {}
        section = sections.get(location.get("section_key") or finding.get("section_key"))
        block_id = location.get("block_id")
        if not field or not block_id or section is None:
            continue

        block = next((item for item in section.paragraphs if item.id == block_id), None)
        block_type = getattr(getattr(block, "type", None), "value", getattr(block, "type", None))
        if block is None or block_type != "paragraph":
            continue

        value = bool(fix.get("value"))
        if getattr(block, field) == value:
            continue
        setattr(block, field, value)
        fixed_rule_ids.append(finding.get("rule_id", ""))

    return len(fixed_rule_ids), fixed_rule_ids


def _heading_par(title: str, stype: str, template: PaperTemplate) -> Paragraph:
    style_id = _STYLE_ID.get(stype, "Heading 1")
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
            color_hex="000000",
        ),),
        line_spacing=style.line_spacing,
        space_before_pt=style.space_before_pt,
        space_after_pt=style.space_after_pt,
        alignment=style.align.value,
        is_page_break_before=style.page_break_before,
        first_line_indent_cm=0.0,
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
            bold=par.bold,
            italic=par.italic,
            underline="single" if par.underline else None,
            color_hex="000000",
        ),),
        line_spacing=style.line_spacing,
        space_before_pt=style.space_before_pt,
        space_after_pt=style.space_after_pt,
        first_line_indent_cm=style.first_line_indent_chars * 0.37,
        alignment=style.align.value,
    )


def _cell_par(text: str, table_index: int, row: int, col: int,
             template: PaperTemplate) -> Paragraph:
    style = template.styles.get("table_cell") or template.styles["body"]
    return Paragraph(
        style_id="Body Text",
        text=text,
        level=None,
        runs=(Run(text=text, font_eastasia=style.font_zh,
                  font_ascii=style.font_en, font_size_pt=style.size_pt),),
        line_spacing=1.0,
        space_before_pt=0.0,
        space_after_pt=0.0,
        alignment="center",
        table_index=table_index,
        table_row_index=row,
        table_cell_index=col,
        first_line_indent_cm=0.0,
    )
