"""文档级规范检查器：字数、章节、关键词、参考文献等可判定要求。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from app.models.template import PaperTemplate
from app.models.project import ProjectSection, WritingProject
from app.services.policy_format import extract_image_font_size_limit, is_format_requirement
from app.services.references import reference_block_text, reference_number_for, validate_reference_facts


_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_DATE_RE = re.compile(r"^((?:19|20)\d{2})(?:年(\d{1,2})月(?:(\d{1,2})日)?|[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?$")
_LEGACY_1101_COVER_FIELDS = [
    "title", "college", "department", "major", "class_name", "author", "student_id", "advisor", "date",
]
_LEGACY_1101_COVER_DESCRIPTION = (
    "封面须包含题目、学生所在学院、系别、专业、班级、姓名、学号、指导教师、提交日期"
)
_LEGACY_1101_REFERENCE_DESCRIPTION = (
    "参考文献按引用或参考先后顺序编号，中文文献在前英文文献在后，序号用[1][2]...格式"
)
# Exact pilot-template writing advice: no professional-content judgment or
# page-count measurement is implied. Modified or additional wording is not
# covered by this classification.
_TEMPLATE_CONTENT_ADVICE = frozenset(
    re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)) for text in (
        "说明：在绪论中简要说明设计工作的目的、意义、范围、研究设想、方法、选题依据等。应当言简意赅，不要与摘要雷同。一般教科书中有的知识，在绪论中不必出现。有关历史回顾和前人工作的，可以适当综合评述。",
        "（列出项目中用到的主要关键技术，并进行描述，篇幅不能过长，2页左右即可）",
        "（以下略）按照实际设计的模块进行分节书写。例如下面4.2.1",
        "（以下略）这里要包括硬件选型和电路设计",
        "以下略）按照实际设计的模块程序进行分节书写。例如下面4.3.2",
        "按照功能分节进行书写，比如5.2.1 环境数据显示功能。",
        "说明：在致谢中，主要表达对导师和其他相关人员的感谢之意以及结合整体的大学学习，对整个毕业设计进行总体性、概括性总结，表达出设计的思路、学习收获、对未来进一步学习的设想。对此，仍要实事求是，过分的颂扬反而会带来消极影响。",
    )
)


def is_image_internal_typography(item: dict[str, Any]) -> bool:
    """Identify known image-content font rules that paragraph styles cannot satisfy."""
    if (item.get("params") or {}).get("image_typography_constraint") == "caption_font_size_upper_bound":
        return True
    description = str(item.get("description") or "")
    return bool(
        re.search(r"(?:插图|图片|图像|图形|图)(?:中|内|里)", description)
        and re.search(r"字号|字体大小", description)
    )


def _verified_simple_body_font(item: dict[str, Any], snapshot: dict, template: PaperTemplate | None) -> bool:
    """Bind this exact single-field rule to the effective body style.

    A format keyword or an override elsewhere cannot prove a compound rule.
    Source templates also need signed, source-bound properties for every body region.
    """
    if template is None:
        return False
    if item.get("type") != "manual" or item.get("checker") or item.get("target") not in {"body", "document"}:
        return False
    if item.get("params") not in ({}, {"format_override": True}, None):
        return False
    match = re.fullmatch(r"正文中文字体(?:为|使用|采用|必须使用|必须采用)(宋体|黑体|仿宋|楷体)[。.]?", str(item.get("description") or "").strip())
    if not match:
        return False
    font = match.group(1)
    body = template.styles.get("body")
    overrides = snapshot.get("format_overrides") or {}
    if not (body and body.font_zh == font and isinstance(overrides, dict)
            and isinstance(overrides.get("body"), dict) and overrides["body"].get("font_zh") == font):
        return False
    nodes = list(template.sections)
    while nodes:
        node = nodes.pop()
        nodes.extend(node.children)
        if node.type.value == "body" and node.style and node.style.font_zh != font:
            return False
    if not template.source_docx and not template.source_execution:
        return True
    if not template.source_docx or not template.source_execution:
        return False

    from zipfile import BadZipFile
    from lxml import etree
    from app.services.digit_font_rules import BODY_KINDS
    from app.services.docx_output_check import source_template_bytes
    from app.services.source_template_profile import digest, verify_profile
    from app.services.template_source import resolve_template_source

    try:
        profile = verify_profile(template, source_template_bytes(resolve_template_source(template.source_docx)))
    except (OSError, ValueError, BadZipFile, etree.XMLSyntaxError):
        return False
    headings = [h for h in profile.get("headings", []) if h.get("kind") in BODY_KINDS]
    return bool(profile.get("policy_snapshot_sha256") == digest(snapshot)
                and profile.get("body_properties", {}).get("font.eastAsia") == font
                and headings and all(h.get("properties", {}).get("font.eastAsia") == font for h in headings))


def _frozen_attachment_text(policy_id: str, document: dict[str, Any]) -> str | None:
    """Read only the exact policy attachment frozen in the project snapshot."""
    # Resolve the same attachment root as the API, including its test override.
    from app.api import policies
    from app.services.policy_import import extract_policy_text

    key = str(document.get("storage_key") or "")
    checksum = str(document.get("checksum") or "")
    for part in (policy_id, key):
        if not part or part in {".", ".."} or "/" in part or "\\" in part or "\x00" in part:
            return None
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum):
        return None
    suffix = Path(key).suffix.lower()
    if suffix not in {".md", ".markdown", ".txt", ".pdf", ".docx"}:
        return None
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(policies._DOCUMENT_DIR.resolve(), directory_flags)
        try:
            policy_fd = os.open(policy_id, directory_flags, dir_fd=root_fd)
            try:
                fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=policy_fd)
            finally:
                os.close(policy_fd)
        finally:
            os.close(root_fd)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            data = stream.read(policies._MAX_DOCUMENT_BYTES + 1)
        if len(data) > policies._MAX_DOCUMENT_BYTES or "sha256:" + hashlib.sha256(data).hexdigest() != checksum:
            return None
        text, _ = extract_policy_text(data, key, full_text=True)
        return text
    except (OSError, ValueError, HTTPException):
        return None


def _may_contain_images(project: WritingProject, template: PaperTemplate | None) -> bool:
    if template is None:
        return True
    if any(
        block.type.value == "image"
        or (block.table is not None and block.table.source_xml)
        or (block.equation is not None and block.equation.omml)
        for section in project.sections for block in section.paragraphs
    ):
        return True
    if not template.source_docx and not template.source_execution:
        return False
    if not template.source_docx or not template.source_execution:
        return True

    # Use the same signed source and editable-boundary transformation as the
    # renderer. Sample pictures removed from manuscript regions do not become
    # student figures; a retained body object still needs typography evidence.
    # Headers and footers are fixed master decorations, not manuscript figures.
    from io import BytesIO
    from zipfile import BadZipFile, ZipFile
    from lxml import etree
    from app.core.extract.xml_utils import load_part
    from app.services.docx_output_check import source_template_bytes
    from app.services.master_docx_renderer import prepare_source
    from app.services.source_template_profile import commentless_source, digest, package_manifest, verify_profile
    from app.services.source_template_safety import audit_source_package
    from app.services.template_source import resolve_template_source

    try:
        source = source_template_bytes(resolve_template_source(template.source_docx))
        profile = verify_profile(template, source)
        audit_source_package(source)
        source = commentless_source(source)
        if digest(package_manifest(source)) != profile.get("prepared_source_manifest_sha256"):
            return True
        with ZipFile(BytesIO(source)) as archive:
            root = load_part(archive, "word/document.xml")
            if root is None:
                return True
            roots = [prepare_source(root, profile, project.meta)]
            for name in ("word/footnotes.xml", "word/endnotes.xml"):
                if name in archive.namelist():
                    roots.append(load_part(archive, name))
        potentially_visual = {
            "drawing", "pict", "object", "imagedata", "blip", "altChunk", "subDoc",
            "contentPart", "OLEObject", "control", "AlternateContent",
        }
        return any(etree.QName(node).localname in potentially_visual
                   for root in roots if root is not None for node in root.iter()
                   if isinstance(node.tag, str))
    except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError,
            BadZipFile, etree.XMLSyntaxError, etree.XPathError):
        return True


def template_source_notes(snapshot: dict, template: PaperTemplate | None) -> list[dict]:
    """Identify source-bound placeholder and known advisory text removed from output."""
    if not template or not template.source_docx or not template.source_execution:
        return []
    items = snapshot.get("resolved_items", [])
    candidates = [item for item in items if isinstance(item, dict)
                  and item.get("type") == "manual" and item.get("enforce") == "soft"
                  and item.get("severity") == "info" and not item.get("checker")
                  and not item.get("params")
                  and (re.fullmatch(r"(?:\(以下略\)|以下略\)|以下略)",
                                    re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(item.get("description") or ""))))
                       or re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(item.get("description") or ""))) in _TEMPLATE_CONTENT_ADVICE)]
    if not candidates:
        return []
    from io import BytesIO
    from zipfile import BadZipFile, ZipFile
    from lxml import etree
    from app.core.extract.specification import _text
    from app.core.extract.xml_utils import load_part
    from app.services.docx_output_check import source_template_bytes
    from app.services.source_template_profile import verify_profile
    from app.services.template_source import resolve_template_source

    try:
        source = source_template_bytes(resolve_template_source(template.source_docx))
        profile = verify_profile(template, source)
        with ZipFile(BytesIO(source)) as archive:
            root = load_part(archive, "word/document.xml")
        namespaces = {key: value for element in root.iter() for key, value in element.nsmap.items() if key}
        signed = profile.get("content_requirements") or []
        notes = []
        for item in candidates:
            provenance = item.get("provenance") or {}
            path = provenance.get("source_path")
            if (not isinstance(path, str) or path not in profile.get("removals", [])
                    or provenance.get("kind") != "explicit_instruction"
                    or provenance.get("source_text") != item.get("description")
                    or sum(entry == item for entry in signed) != 1
                    or sum(entry.get("id") == item.get("id") for entry in items if isinstance(entry, dict)) != 1):
                continue
            nodes = root.xpath(path, namespaces=namespaces)
            if len(nodes) != 1 or _text(nodes[0]).strip() != item["description"].strip():
                continue
            normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", item["description"]))
            reason = "template_content_advisory" if normalized in _TEMPLATE_CONTENT_ADVICE else "template_example_placeholder_removed"
            notes.append({"id": item["id"], "description": item["description"],
                          "source": template.name, "original_source": item.get("source", ""),
                          "source_version": item.get("source_version", ""),
                          "provenance": provenance, "reason": reason})
        return notes
    except (OSError, ValueError, TypeError, RuntimeError, BadZipFile, etree.XMLSyntaxError, etree.XPathError):
        return []


def _normalize_legacy_1101_item(item: dict[str, Any]) -> dict[str, Any]:
    """Restore two exact 1101 rules saved before their automatic checkers existed."""
    if item.get("type") != "manual" or item.get("checker"):
        return item
    if (item.get("id") == "cover_required_fields"
            and item.get("description") == _LEGACY_1101_COVER_DESCRIPTION
            and item.get("params") in (
                {"min": 0, "max": 35},
                {"fields": ["题目", "学院", "系别", "专业", "班级", "姓名", "学号", "指导教师", "提交日期"]},
            )):
        return {
            **item,
            "type": "auto",
            "target": "cover",
            "checker": "cover.fields_present",
            "params": {"field_keys": list(_LEGACY_1101_COVER_FIELDS)},
        }
    if (item.get("id") == "references_format"
            and item.get("description") == _LEGACY_1101_REFERENCE_DESCRIPTION
            and item.get("params") == {
                "order": "sequential",
                "bracket": "square",
                "style": "顺序编码制",
                "language_order": "中文在前，英文在后",
            }):
        return {
            **item,
            "type": "auto",
            "target": "references",
            "checker": "references.list_facts",
            "params": {
                "required": True,
                "numbering": "sequential",
                "bracket_style": "square",
                "language_order": "zh_before_en",
            },
        }
    return item


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


def _reference_format_issues(text: str) -> list[str]:
    """Return malformed reference lines using a conservative GB/T-shaped check.

    This is intentionally a syntax sanity check. Detailed font/spacing rules remain
    in the bindable GB/T 7714 rule pack.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    pattern = re.compile(r"^\s*(?:\[\d+\]|\d+[.)、])")
    return [
        line for line in lines
        if not pattern.search(line) or not _YEAR_RE.search(line)
    ]


def check_requirements(project: WritingProject, snapshot: dict | None,
                       template: PaperTemplate | None = None, *,
                       source_notes: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """返回 (未通过 findings, 未自动检查项)。"""
    if not snapshot:
        return [], []
    findings: list[dict] = []
    not_checked: list[dict] = []
    sections = {section.key: section for section in project.sections}
    format_item_ids = set(snapshot.get("format_item_ids") or [])
    excluded_note_ids = {note["id"] for note in (source_notes if source_notes is not None
                                                   else template_source_notes(snapshot, template))}

    resolved_items = list(snapshot.get("resolved_items", []))
    resolved_descriptions = {(item.get("id"), item.get("description")) for item in resolved_items if item.get("id")}
    may_contain_images = _may_contain_images(project, template)
    # Old snapshots could merge two rules and retain only one description.
    # Restore visibility of a lost format requirement from the
    # frozen sources; a checker for the other rule cannot establish it passed.
    source_groups = [(str(policy.get("name") or "规范要求"), str(policy.get("version") or ""),
                      policy.get("items", []), True) for policy in snapshot.get("policy_sets", [])]
    source_groups.append(("模板规范", "", snapshot.get("template_instruction_items", []), False))
    recovered_formats = set()
    for source_name, source_version, items, policy_source in source_groups:
        for original in items:
            description = str(original.get("description") or "")
            if original.get("type", "auto") == "auto" and not original.get("checker"):
                continue
            format_text = (re.sub(r"章前分页|分页|另起一页|换页", "", description)
                           if original.get("checker") == "chapter.page_break" else description)
            if (not ((original.get("params") or {}).get("format_override") or is_format_requirement(format_text))
                    or (is_image_internal_typography(original) and not may_contain_images)):
                continue
            target = str(original.get("target") or "document")
            mapping = snapshot.get("manuscript_target_mapping") or {}
            if policy_source and target in (mapping.get("section_key_map") or {}):
                target = str(mapping["section_key_map"][target])
            elif policy_source and target in (mapping.get("source_section_keys") or []) and original.get("enforce") != "hard":
                target = "document"
            represented = original.get("id") and any(
                item.get("id") == original.get("id") and item.get("description") == original.get("description")
                and item.get("type", "auto") == original.get("type", "auto")
                and (item.get("checker") or "") == (original.get("checker") or "")
                and str(item.get("target") or "document") == target
                and (original.get("enforce") != "hard" or item.get("enforce") == "hard")
                and (not (original.get("params") or {}).get("format_override")
                     or (item.get("params") or {}).get("format_override"))
                for item in resolved_items
            )
            if represented:
                continue
            identity = f"{source_name}\0{source_version}\0{original.get('id', '')}\0{target}\0{description}\0{original.get('enforce', 'soft')}"
            if identity in recovered_formats:
                continue
            recovered_formats.add(identity)
            not_checked.append({
                "id": "merged-format-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                "description": description, "type": original.get("type", "auto"), "checker": original.get("checker"),
                "severity": original.get("severity", "warning"), "enforce": original.get("enforce", "soft"),
                "source": original.get("source") or source_name,
                "source_version": original.get("source_version") or source_version,
                "sources": original.get("sources", []), "reason": "format_rule_unverified", "fixable": False,
            })
    has_structured_limit = any(
        (item.get("params") or {}).get("image_typography_constraint") == "caption_font_size_upper_bound"
        and item.get("type") == "manual" and not item.get("checker") and item.get("enforce") == "hard"
        for item in resolved_items
    )
    for policy in snapshot.get("policy_sets", []):
        texts = [str(policy.get("markdown_content") or "")]
        texts.extend(str(item.get("description") or "").removeprefix("原文格式要求：")
                     for item in policy.get("items", [])
                     if (str(item.get("description") or "").startswith("原文格式要求：")
                         or not item.get("id")
                         or (item["id"], item.get("description")) not in resolved_descriptions))
        for document in policy.get("documents", []):
            text = _frozen_attachment_text(str(policy.get("id") or ""), document)
            if text is None:
                identity = f"{policy.get('id', '')}\0{policy.get('version', '')}\0{document.get('id', '')}\0{document.get('checksum', '')}"
                not_checked.append({
                    "id": "unverified-policy-attachment-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                    "description": "规范附件无法核验，请联系系统团队恢复并核对原附件",
                    "type": "doc", "severity": "error", "enforce": "hard",
                    "source": policy.get("name") or "冻结规范附件",
                    "source_version": policy.get("version") or "", "sources": [],
                    "reason": "policy_attachment_unverified", "fixable": False,
                })
            else:
                texts.append(text)
        for text in texts:
            description = extract_image_font_size_limit(text)
            if description and not has_structured_limit:
                identity = f"{policy.get('id', '')}\0{policy.get('version', '')}\0{description}"
                resolved_items.append({
                    "id": "recovered-image-font-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                    "type": "manual", "checker": "", "target": "document",
                    "params": {"image_typography_constraint": "caption_font_size_upper_bound"},
                    "severity": "error", "enforce": "hard", "description": description,
                    "source": policy.get("name") or "冻结规范原文",
                    "source_version": policy.get("version") or "",
                })
                has_structured_limit = True
            # This narrow recovery identifies a known mandatory wording only;
            # it does not claim that the rest of the attachment was parsed.
            for clause in re.split(r"[。！？；;\n]", text):
                clause = clause.strip()
                if not re.match(r"^正文(?:中文字体)?必须(?:使用|采用)(?:宋体|黑体|仿宋|楷体)", clause):
                    continue
                identity = f"{policy.get('id', '')}\0{policy.get('version', '')}\0{clause}"
                identifier = "recovered-body-font-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
                if not any(item.get("id") == identifier for item in resolved_items):
                    resolved_items.append({
                        "id": identifier, "type": "manual", "checker": "", "target": "body",
                        "params": {}, "severity": "error", "enforce": "hard", "description": clause,
                        "source": policy.get("name") or "冻结规范原文",
                        "source_version": policy.get("version") or "",
                    })
                    format_item_ids.add(identifier)
    for raw_item in resolved_items:
        if raw_item.get("id") in excluded_note_ids:
            continue
        item = _normalize_legacy_1101_item(raw_item)
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
        image_typography = is_image_internal_typography(item)
        if (item_type == "manual" and not checker
                and (item.get("params") or {}).get("image_typography_constraint") == "caption_font_size_upper_bound"
                and not may_contain_images):
            # A generated template and structured text/tables contain no
            # imported Word objects; there is no image for this rule to check.
            # Source masters and preserved XML remain unknown until inspected.
            continue
        classified_format = item_type != "auto" and item.get("id") in format_item_ids and not image_typography
        if classified_format and _verified_simple_body_font(item, snapshot, template):
            continue
        if item_type != "auto":
            not_checked.append({
                **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                "reason": ("image_typography_unverified" if image_typography else
                           "format_rule_unverified" if classified_format else
                           "manual" if item_type == "manual" else "reference_document"),
                "fixable": False,
            })
            continue

        params = item.get("params") or {}
        section = _section_for(project, str(item.get("target") or "document"), params)
        target = str(item.get("target") or "document")
        # These checkers verify facts or counts, not any formatting that may
        # have been bundled into the same natural-language requirement.
        non_format_checkers = {
            "section.exists", "section.table_required", "section.word_count", "abstract.word_count",
            "keywords.count", "cover.field_present", "meta.field_present", "cover.fields_present",
            "references.list_facts", "references.count", "references.recent_language_summary",
            "references.recent_count", "references.recent_ratio", "references.format", "ai.usage_declared",
        }
        if checker == "chapter.page_break":
            # Pagination itself is the checker's subject; only other detected
            # format requirements should be treated as unverified here.
            description = re.sub(r"章前分页|分页|另起一页|换页", "", str(item.get("description") or ""))
            additional_format = is_format_requirement(description)
        else:
            format_classified = item.get("id") in format_item_ids
            description = str(item.get("description") or "")
            if format_classified and not params.get("format_override") and not is_format_requirement(description):
                sources = [source for policy in snapshot.get("policy_sets", []) for source in policy.get("items", [])]
                sources.extend(snapshot.get("template_instruction_items", []))
                same_id = [source for source in sources if source.get("id") == item.get("id")]
                matching = [source for source in same_id
                            if source.get("description") == item.get("description")
                            and source.get("type", "auto") == item_type
                            and (source.get("checker") or "") == checker]
                other_format = any(
                    source not in matching and ((source.get("params") or {}).get("format_override")
                                                or is_format_requirement(source.get("description") or ""))
                    for source in same_id
                )
                if (matching and other_format and all(not (source.get("params") or {}).get("format_override")
                                                     and not is_format_requirement(source.get("description") or "")
                                                     for source in matching)):
                    format_classified = False
            # Older classification matched pt/cm inside English words. Only
            # disregard that classification when the frozen source confirms
            # the same text and has no explicit format marker.
            if (format_classified and not is_format_requirement(description)
                    and re.search(r"[a-z](?:pt|cm)|(?:pt|cm)[a-z]", description, re.IGNORECASE)):
                originals = [source for policy in snapshot.get("policy_sets", [])
                             for source in policy.get("items", []) if source.get("id") == item.get("id")]
                if (originals and not params.get("format_override")
                        and all(source.get("description") == item.get("description")
                                and not (source.get("params") or {}).get("format_override")
                                for source in originals)):
                    format_classified = False
            additional_format = checker in non_format_checkers and (
                format_classified or is_format_requirement(description)
            )
        if additional_format:
            not_checked.append({
                **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                "reason": "format_rule_unverified", "checker": checker, "fixable": False,
            })
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
            if (set(params) != {"field_keys"} or not isinstance(keys, list) or not keys
                    or any(not isinstance(key, str) or not key for key in keys)
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
            ordered_checks = {"numbering", "bracket_style", "language_order"} & set(params)
            params_supported = (
                set(params).issubset({"required", "numbering", "bracket_style", "language_order"})
                and ("required" not in params or type(params["required"]) is bool)
                and ("numbering" not in params or params["numbering"] == "sequential")
                and ("bracket_style" not in params or params["bracket_style"] == "square")
                and ("language_order" not in params or params["language_order"] == "zh_before_en")
                and ("required" in params or bool(ordered_checks))
            )
            if not params_supported:
                not_checked.append({
                    **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                    "reason": "unsupported_checker_parameters", "checker": checker, "fixable": False,
                })
                continue
            section = section or _section_for(project, "references", params)
            list_required = params.get("required", bool(ordered_checks))
            if section is None:
                if list_required:
                    findings.append(_finding(descriptor, "缺少参考文献章节", False, True, "references"))
                continue
            position = 0
            prior_language_rank = -1
            for block in section.paragraphs:
                if block.type.value != "paragraph":
                    findings.append(_finding(descriptor, "参考文献列表包含非文献内容", block.id, "参考文献条目", section.key))
                    continue
                reference = block.reference
                if not reference_block_text(block).strip() and not (reference and reference.original_pasted_text is not None):
                    continue
                position += 1
                issues = validate_reference_facts(reference) if reference else ["尚未录入结构化文献事实"]
                for issue in issues:
                    findings.append(_finding(descriptor, f"参考文献第 {position} 条：{issue}",
                                             {"block_id": block.id}, "事实完整并保留原文", section.key))
                if params.get("numbering") == "sequential":
                    actual_number = reference_number_for(block, position)
                    if actual_number != position:
                        findings.append(_finding(
                            descriptor, f"参考文献第 {position} 条编号不连续", actual_number, position, section.key,
                        ))
                if params.get("bracket_style") == "square":
                    number = reference_number_for(block, position)
                    rendered = reference_block_text(block, number)
                    if not re.match(rf"^\[\s*{number}\s*\]\s+\S", rendered):
                        findings.append(_finding(
                            descriptor, f"参考文献第 {position} 条未使用方括号编号",
                            rendered[:80], f"[{number}] 文献内容", section.key,
                        ))
                if params.get("language_order") == "zh_before_en":
                    fact = reference.language_fact if reference else None
                    languages = set(fact.languages) if fact else set()
                    confirmed = bool(fact and fact.status == "confirmed" and fact.confirmed_by and fact.confirmed_at)
                    if not confirmed or len(languages) != 1 or not languages.issubset({"zh", "en"}):
                        findings.append(_finding(
                            descriptor, f"参考文献第 {position} 条语种未确认，无法检查中英文排序",
                            sorted(languages), "已确认中文或英文", section.key,
                        ))
                    else:
                        language_rank = 0 if languages == {"zh"} else 1
                        if language_rank < prior_language_rank:
                            findings.append(_finding(
                                descriptor, f"参考文献第 {position} 条违反中文在前、英文在后的顺序",
                                sorted(languages), "先中文、后英文", section.key,
                            ))
                        prior_language_rank = max(prior_language_rank, language_rank)
            if not position and list_required:
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
            # Empty parameters are the legacy numbering/year check. New
            # imports allow only the explicit equivalent. A frozen snapshot
            # must not silently turn other standards or extra requirements
            # into that much narrower check.
            if params not in ({}, {"format_check": "number_and_year"}):
                not_checked.append({
                    **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                    "reason": "unsupported_checker_parameters", "checker": checker, "fixable": False,
                })
                continue
            # Number/year checks do not establish full standard conformance.
            # Preserve explicit broad claims as unverified while still
            # reporting the independently checkable number/year failures.
            description = str(item.get("description") or "")
            if re.search(r"\b(?:APA|IEEE)\b|GB\s*/\s*T|著录(?:格式|规则|标准)", description, re.I):
                not_checked.append({
                    **{key: descriptor[key] for key in ("id", "description", "type", "severity", "enforce", "source", "source_version", "sources")},
                    "reason": "reference_standard_unverified", "checker": checker, "fixable": False,
                })
            section = section or _section_for(project, "references", params)
            issues = _reference_format_issues(_section_text(section))
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
