"""Instantiate approved Word role styles with a manuscript's complete chapter tree.

The uploaded master remains immutable. A separate, project-owned source package
retains its cover, front matter, section definitions and non-document parts.
"""
from __future__ import annotations

import copy
import io
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

from lxml import etree

from app.core.extract.xml_utils import qn
from app.models.template import TemplateSection
from app.services.import_template import extract_section_tree
from app.services.master_docx_renderer import apply_properties, text_paragraph, xml_bytes
from app.services.source_template_profile import (
    ConfirmWritingProfile, ProfileError, commentless_source, compile_profile,
    as_text_style, digest, execution_basis, flatten, freeze_protected_structure, sign_profile, verify_profile,
)
from app.core.extract.specification import extract_writing_specification


def _chapter_number(title):
    match = re.match(r"^第\s*([0-9一二三四五六七八九十百]+)\s*章", title)
    if not match: return ""
    token = match.group(1)
    if token.isdigit(): return str(int(token))
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    value, number = 0, 0
    for char in token:
        if char in digits: number = digits[char]
        elif char in {"十", "百"}: value += (number or 1) * {"十": 10, "百": 100}[char]; number = 0
    return str(value + number)


def _title(text):
    text = re.sub(r"[（(].*?[）)]\s*$", "", text)
    text = re.sub(r"^(?:第\s*[0-9一二三四五六七八九十百]+\s*章|\d+(?:\.\d+)*)\s*", "", text)
    return re.sub(r"\s+", "", text).casefold()


def derive_manuscript_template(template, source_path, imported, *, actor):
    """Return a signed instance and source bytes; the caller owns persistence."""
    parent = verify_profile(template, Path(source_path).read_bytes())
    if not parent.get("preserve_manuscript_structure"):
        raise ProfileError("此模板未确认采用论文原有章节结构")
    chapters = [s for s in imported["sections"] if s.get("type") in {"heading1", "heading2", "heading3"}]
    if not chapters or chapters[0]["type"] != "heading1":
        raise ProfileError("论文缺少一级章标题，不能建立章节树")
    level = 0
    for section in chapters:
        current = int(section["type"][-1])
        if current > level + 1:
            raise ProfileError("论文标题层级跳级，请确认：" + section["title"])
        level = current
    source = commentless_source(Path(source_path).read_bytes())
    original_specification = extract_writing_specification(Path(source_path))
    body_example = next((n.text for n in original_specification.nodes
                         if n.role == "body" and len(n.text.strip()) >= 80), None)
    if body_example is None:
        body_example = next((n.text for n in original_specification.nodes if n.role == "body" and n.text.strip()), None)
    if body_example is None:
        raise ProfileError("原模板缺少可复用的正文样例")
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(parts["word/document.xml"])
    body = root.find(qn("w:body")); original = list(body)
    mapped = [h for h in parent["headings"] if h["kind"] in {"heading1", "heading2", "heading3"}]
    if not mapped or mapped[0]["kind"] != "heading1":
        raise ProfileError("母版没有可复用的章标题区域")
    first, last = mapped[0]["index"], mapped[-1]["index"]
    tail = next((h["index"] for h in parent["headings"] if h["index"] > last), len(original) - 1)
    # Section boundaries stay associated with their original chapter; footer
    # and margin definitions are never inferred from the source manuscript.
    old_chapters = [h for h in mapped if h["kind"] == "heading1"]
    boundaries = {}
    for index in range(first, tail):
        node = original[index]
        section = node.find(qn("w:pPr") + "/" + qn("w:sectPr"))
        if section is not None:
            owner = next(h for h in reversed(old_chapters) if h["index"] <= index)
            key = _chapter_number(owner["title"]) or owner["title"]
            paragraph = etree.Element(qn("w:p")); properties = etree.SubElement(paragraph, qn("w:pPr"))
            properties.append(copy.deepcopy(section))
            boundaries.setdefault(key, []).append(paragraph)
    new_chapters = [s for s in chapters if s["type"] == "heading1"]
    new_numbers = {_chapter_number(s["title"]) or s["title"] for s in new_chapters}
    if set(boundaries) - new_numbers:
        raise ProfileError("部分分节边界没有对应的论文章节，请确认：" + "、".join(sorted(set(boundaries)-new_numbers)))
    toc_levels = {r["target_key"]: r["level"] for r in parent.get("toc_entries", [])}
    palette = {}
    for heading in mapped:
        actual_level = int(toc_levels.get(heading["key"], heading["kind"][-1]))
        palette.setdefault(actual_level, heading)
    inserted = []
    active_chapter = None
    for section in chapters:
        current = int(section["type"][-1])
        if current not in palette:
            raise ProfileError(f"母版缺少 {current} 级标题样式，无法套用")
        if current == 1:
            if active_chapter is not None:
                inserted.extend(boundaries.get(active_chapter, []))
            active_chapter = _chapter_number(section["title"]) or section["title"]
        sample = palette[current]
        paragraph = text_paragraph(section["title"], sample["title_properties"])
        source_properties = original[sample["index"]].find(qn("w:pPr"))
        if source_properties is not None:
            properties = copy.deepcopy(source_properties)
            for node in list(properties):
                if node.tag in {qn("w:sectPr"), qn("w:numPr")}:
                    properties.remove(node)
            paragraph.remove(paragraph.find(qn("w:pPr"))); paragraph.insert(0, properties)
            apply_properties(paragraph, sample["title_properties"])
        inserted.append(paragraph)
        # Only a format specimen, replaced by imported content when rendered.
        inserted.append(text_paragraph(body_example, sample["properties"]))
    inserted.extend(boundaries.get(active_chapter, []))
    for node in original[first:tail]:
        body.remove(node)
    for offset, node in enumerate(inserted):
        body.insert(first + offset, node)
    parts["word/document.xml"] = xml_bytes(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items(): archive.writestr(name, data)
    payload = buffer.getvalue()
    with tempfile.TemporaryDirectory(prefix="paperguard-manuscript-master-") as directory:
        path = Path(directory) / f"tplsrc-manuscript-{uuid.uuid4().hex}.docx"
        path.write_bytes(payload)
        candidate = template.model_copy(deep=True)
        candidate.source_docx = path.name
        candidate.sections = [TemplateSection.model_validate(n) for n in extract_section_tree(path)]
        specification = extract_writing_specification(path)
        candidate.writing_specification = specification
        request = ConfirmWritingProfile(expected_specification_sha256=specification.specification_sha256,
            accept_recommended=True, accept_new_object_defaults=True, remove_template_instructions=True,
            remove_unmatched_toc_entries=True, preserve_manuscript_structure=True,
            body_page_number=parent.get("body_page_number"))
        candidate = compile_profile(candidate, path, request, actor=actor)
    profile = candidate.source_execution
    inherited_labels = {}
    inherited_label_sources = set()
    for heading in profile["headings"]:
        if heading["kind"] in {"heading1", "heading2", "heading3"}:
            style_source = palette[int(heading["kind"][-1])]
        else:
            style_source = next((h for h in parent["headings"] if h["kind"] == heading["kind"]), None)
        if style_source:
            for key in ("properties", "title_properties", "title_overridden"):
                heading[key] = copy.deepcopy(style_source[key])
            if heading.get("inline"):
                for key in ("display_label", "separator", "separator_properties"):
                    if key in style_source:
                        heading[key] = copy.deepcopy(style_source[key])
                prior_label = (parent.get("inline_label_overrides") or {}).get(style_source["key"])
                if prior_label:
                    inherited_labels[heading["key"]] = copy.deepcopy(prior_label)
                    inherited_label_sources.add(style_source["key"])
    if set(parent.get("inline_label_overrides") or {}) != inherited_label_sources:
        raise ProfileError("已确认的关键词标签无法映射到导入论文模板，请重新确认")
    profile["inline_label_overrides"] = inherited_labels
    for field in profile["fields"]:
        prior = next((f for f in parent["fields"] if f["key"] == field["key"]), None)
        if prior: field["properties"] = copy.deepcopy(prior["properties"])
    for key in ("object_properties", "new_object_policy", "body_properties", "table_borders", "image_alignment"):
        profile[key] = copy.deepcopy(parent[key])
    if profile.get("toc_active"):
        # All imported headings are mandatory directory targets. Matching only
        # old sample labels would let a refreshed directory omit new chapters.
        prior_headings = {h["key"]: h for h in parent["headings"]}
        extra_levels = {prior_headings[r["target_key"]]["kind"]: r["level"]
                        for r in parent.get("toc_entries", [])
                        if r["target_key"] in prior_headings and not prior_headings[r["target_key"]]["kind"].startswith("heading")}
        entries = []
        for heading in profile["headings"]:
            kind = heading["kind"]
            level = int(kind[-1]) if kind in {"heading1", "heading2", "heading3"} else extra_levels.get(kind)
            if level is None: continue
            style_id = profile["toc_style_by_level"].get(level) or profile["toc_style_by_level"].get(str(level))
            if not style_id:
                raise ProfileError(f"母版缺少 {level} 级目录样式，无法完整生成论文目录")
            entries.append({"key": f"manuscript-toc-{len(entries)}", "target_key": heading["key"],
                "target_title": heading["title"], "source_label": heading["title"], "source_page": "",
                "level": level, "style_id": style_id, "origin": "manuscript_structure"})
        profile["toc_entries"] = entries
    sections_by_key = {n.key: n for n in flatten(candidate.sections)}
    for heading in profile["headings"]:
        sections_by_key[heading["key"]].style = as_text_style(heading["properties"], candidate.styles.get("body"))
    profile["template_basis_sha256"] = digest(execution_basis(candidate))
    for key in ("digit_font_review", "digit_font_rule", "confirmed_digit_font_rule_ids"):
        profile[key] = copy.deepcopy(parent.get(key))
    profile["protected_document_sha256"] = freeze_protected_structure(root, profile)
    # Keep the teacher's original content requirements and their original
    # provenance. Changed chapter numbers must not redirect a hard rule.
    new_headings = profile["headings"]
    mapping = {}
    for old in parent["headings"]:
        matches = [h for h in new_headings if _title(h["title"]) == _title(old["title"])]
        if len(matches) == 1: mapping[old["key"]] = matches[0]["key"]
    requirements = copy.deepcopy(parent.get("content_requirements", []))
    for item in requirements:
        target = item.get("target")
        if target not in {h["key"] for h in parent["headings"]}: continue
        if target not in mapping and item.get("enforce") == "hard":
            raise ProfileError("已确认的内容要求缺少对应章节，需要教师确认：" + target)
        replacement = mapping.get(target, "document")
        item["target"] = replacement
        if "section_key" in item.get("params", {}): item["params"]["section_key"] = replacement
    for key in ("confirmed_content_rule_ids", "content_rule_review", "content_rule_compiler_version"):
        profile[key] = copy.deepcopy(parent.get(key))
    profile["content_requirements"] = requirements
    profile["content_requirement_source_sha256"] = parent.get("content_requirement_source_sha256", parent["source_sha256"])
    profile["content_requirement_specification_sha256"] = parent.get("content_requirement_specification_sha256", parent["specification_sha256"])
    profile["manuscript_parent"] = {"profile_sha256": parent["profile_sha256"], "source_sha256": parent["source_sha256"]}
    profile["manuscript_headings_sha256"] = digest([(s["type"], s["title"]) for s in chapters])
    profile["manuscript_sha256"] = imported.get("source_sha256", "")
    candidate.source_execution = sign_profile(profile)
    verify_profile(candidate, payload)
    return candidate, payload
