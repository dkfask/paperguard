"""Compile reviewed DOCX observations into a signed, version-bound execution plan."""
from __future__ import annotations

import copy
import hashlib
import hmac
import io
import json
import math
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.extract.specification import extract_writing_specification, _text
from app.core.extract.xml_utils import NS, load_part, qn
from app.models.template import DEFAULT_STYLES, PaperTemplate, TextStyle, SectionType
from app.services.secret_store import _require_master_key

PROFILE_VERSION = "word-master/6"


class ProfileError(ValueError):
    pass


class InlineLabelOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=64)
    separator: str = Field(min_length=1, max_length=5)
    bold: bool | None = None


class BodyPageNumberSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    font: str = Field(min_length=1, max_length=128)
    size_pt: float = Field(ge=6, le=36, multiple_of=0.5)

    @field_validator("font")
    @classmethod
    def valid_font(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("页码字体名称无效")
        return value


class ConfirmWritingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_specification_sha256: str
    accept_recommended: bool = False
    remove_template_instructions: bool = False
    # Each override addresses one source-bound rule, never an arbitrary XML selector.
    overrides: dict[str, Any] = Field(default_factory=dict)
    # Only explicit instruction candidates shown by the server may become hard checks.
    confirmed_content_rule_ids: list[str] = Field(default_factory=list)
    confirmed_digit_font_rule_ids: list[str] = Field(default_factory=list)
    inline_label_overrides: dict[str, InlineLabelOverride] = Field(default_factory=dict)
    body_page_number: BodyPageNumberSettings | None = None
    defense_date_field_key: str = ""
    # New objects have no universal dimensions inferred from a single source example.
    accept_new_object_defaults: bool = False
    remove_unmatched_toc_entries: bool = False
    preserve_manuscript_structure: bool = False
    proposal_id: str = ""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def package_manifest(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name:hashlib.sha256(zf.read(name)).hexdigest() for name in sorted(zf.namelist())}


def commentless_source(source: bytes) -> bytes:
    """Keep the uploaded master intact; remove reviewer data only from the working copy."""
    import tempfile
    from app.services.docx_package_fixer import strip_comments_from_docx
    with tempfile.TemporaryDirectory(prefix="paperguard-master-clean-") as directory:
        before,after=Path(directory)/"source.docx",Path(directory)/"clean.docx"
        before.write_bytes(source)
        strip_comments_from_docx(before,after)
        return after.read_bytes()


def execution_basis(template: PaperTemplate) -> dict:
    return {key: template.model_dump(mode="json")[key] for key in (
        "id", "version", "source_docx", "sections", "styles", "meta_fields", "page_layout", "allow_structure_edit")}


def sign_profile(profile: dict) -> dict:
    result = {k: v for k, v in profile.items() if k not in {"signature", "profile_sha256"}}
    result["profile_sha256"] = digest(result)
    result["signature"] = hmac.new(_require_master_key(), (PROFILE_VERSION + result["profile_sha256"]).encode(), hashlib.sha256).hexdigest()
    return result


def verify_profile(template: PaperTemplate, source: bytes | None) -> dict:
    profile = template.source_execution
    if not profile:
        raise ProfileError("此模板尚未确认母版填充与撰写规范")
    if profile.get("compiler_version") != PROFILE_VERSION:
        raise ProfileError("模板执行配置版本已更新，请重新确认母版填充与撰写规范")
    calculated = sign_profile(profile)
    if not hmac.compare_digest(str(profile.get("signature", "")), calculated["signature"]) or profile.get("profile_sha256") != calculated["profile_sha256"]:
        raise ProfileError("模板执行配置签名无效，请重新确认发布")
    if source is None or hashlib.sha256(source).hexdigest() != profile.get("source_sha256"):
        raise ProfileError("母版文件摘要变化，请重新导入并确认")
    if digest(execution_basis(template)) != profile.get("template_basis_sha256"):
        raise ProfileError("模板格式或章节已变化，请重新确认此版本")
    # Older signed profiles may have omitted logical table borders. Re-read
    # the digest-bound source so they cannot certify an unverified layout.
    from docx import Document
    from app.services.table_borders import template_table_borders
    try:
        template_table_borders(Document(io.BytesIO(source)))
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc
    from app.services.digit_font_rules import is_digit_font_instruction
    digit_candidates = (profile.get("digit_font_review") or {}).get("candidates", [])
    legacy_candidates = [item for item in (profile.get("content_rule_review") or {}).get("manual_items", [])
                         if is_digit_font_instruction(item.get("source_text", ""))]
    digit_rule = profile.get("digit_font_rule")
    if (digit_candidates or legacy_candidates) and not digit_rule:
        raise ProfileError("模板中的数字字体规则尚未确认，请重新预览并确认后发布")
    if digit_rule and (len(digit_candidates) != 1
                       or profile.get("confirmed_digit_font_rule_ids") != [digit_candidates[0].get("rule_id")]
                       or digit_rule != digit_candidates[0]):
        raise ProfileError("模板数字字体规则与确认记录不一致")
    if profile.get("content_requirements"):
        if (profile.get("content_rule_compiler_version") not in {"template-content-rules/1", "template-content-rules/2"}
                or not profile.get("specification_sha256")
                or not isinstance(profile.get("confirmed_content_rule_ids"), list)):
            raise ProfileError("模板内容规则确认记录不完整")
        confirmed_ids = set(profile["confirmed_content_rule_ids"])
        seen_ids = set()
        for item in profile["content_requirements"]:
            provenance = item.get("provenance") or {}
            rule_id = str(provenance.get("rule_id") or "")
            if (not rule_id or rule_id in seen_ids
                    or provenance.get("source_sha256") != profile.get("content_requirement_source_sha256", profile.get("source_sha256"))
                    or provenance.get("specification_sha256") != profile.get("content_requirement_specification_sha256", profile.get("specification_sha256"))
                    or item.get("source_version") != template.version):
                raise ProfileError("模板内容规则与源文件、规范或版本不匹配")
            seen_ids.add(rule_id)
            if rule_id in confirmed_ids:
                quality_rule = (profile.get("content_rule_compiler_version") == "template-content-rules/2"
                                and item.get("checker") == "references.recent_language_summary")
                if quality_rule:
                    date_key = (item.get("params") or {}).get("defense_date_field_key")
                    if (item.get("type") != "auto" or item.get("enforce") != "soft" or item.get("severity") != "info"
                            or not date_key or date_key != profile.get("defense_date_field_key")
                            or sum(field.key == date_key for field in template.meta_fields) != 1):
                        raise ProfileError("文献内容提示缺少已确认的答辩日期字段")
                elif item.get("type") != "auto" or item.get("enforce") != "hard" or item.get("severity") != "error":
                    raise ProfileError("已确认的内容规则未绑定硬性检查")
            elif item.get("type") != "manual" or item.get("enforce") != "soft":
                raise ProfileError("未确认的内容规则不能启用为硬性检查")
        if not confirmed_ids.issubset(seen_ids):
            raise ProfileError("模板内容规则确认项与编译结果不匹配")
    if profile.get("toc_active"):
        # The source digest is already signed. Derive this rule for older
        # profiles too, so a formatting repair does not invalidate templates.
        from app.services.toc_appearance import source_toc_appearance
        observed = source_toc_appearance(source, profile)
        frozen = profile.get("toc_appearance_by_level")
        # Profiles predating separate tab-leader evidence are still bound to
        # the same source; derive that part from the source on every read.
        without_leader = {level: {k: v for k, v in rules.items() if k != "leader"}
                          for level, rules in observed.items()}
        if frozen is not None and frozen != observed and frozen != without_leader:
            raise ProfileError("目录外观与已确认母版不一致，请重新确认")
        profile = {**profile, "toc_appearance_by_level": observed}
    return profile


def flatten(nodes):
    for node in nodes:
        yield node
        yield from flatten(node.children)


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _majority(nodes) -> dict:
    values: dict[str, Counter] = {}
    for n in nodes:
        defaults = {"paragraph.spacing.before":{"value":0.0,"unit":"pt"}, "paragraph.spacing.after":{"value":0.0,"unit":"pt"},
                    "paragraph.indent.left":{"value":0.0,"unit":"pt"}, "paragraph.indent.right":{"value":0.0,"unit":"pt"},
                    "paragraph.indent.first_line":{"value":0.0,"unit":"character"}}
        for key, value in {**defaults,**n.properties}.items():
            if key.startswith(("font.", "paragraph.")) and value is not None:
                values.setdefault(key, Counter())[json.dumps(value,sort_keys=True)] += max(1,min(len(n.text.strip()),1000))
    return {key: json.loads(counts.most_common(1)[0][0]) for key, counts in values.items()}


def as_text_style(properties: dict, fallback: TextStyle | None = None) -> TextStyle:
    raw = (fallback or TextStyle()).model_dump(mode="json")
    mapping = {"font.eastAsia": "font_zh", "font.ascii": "font_en", "font.bold": "bold", "font.italic": "italic", "font.color": "color", "paragraph.align": "align", "paragraph.page_break_before": "page_break_before"}
    for prop, key in mapping.items():
        if prop in properties and not isinstance(properties[prop], dict):
            raw[key] = properties[prop]
    if raw.get("align") in {"both","distribute"}:
        raw["align"] = "justify"
    for prop, key in (("font.size", "size_pt"), ("paragraph.spacing.before", "space_before_pt"), ("paragraph.spacing.after", "space_after_pt")):
        value = properties.get(prop, {})
        if isinstance(value, dict) and value.get("unit") == "pt":
            raw[key] = value["value"]
    line = properties.get("paragraph.spacing.line", {})
    if line:
        raw.update(line_spacing=line["value"], line_spacing_rule=line.get("kind", "auto"))
    indent = properties.get("paragraph.indent.first_line", {})
    if indent.get("unit") == "character":
        raw["first_line_indent_chars"] = indent["value"]
    return TextStyle.model_validate(raw)


def _configured_object_properties(style: TextStyle, inherited: dict) -> dict:
    """Use the reviewed object style when the master has no body specimen."""
    kind = style.line_spacing_rule
    if kind not in {"auto", "exact", "atLeast"}:
        raise ProfileError("图表文字的行距规则暂不支持，请调整模板样式")
    properties = {
        **inherited,
        "font.eastAsia": style.font_zh,
        "font.ascii": style.font_en,
        "font.hAnsi": style.font_en,
        "font.size": {"value": style.size_pt, "unit": "pt"},
        "font.bold": style.bold,
        "font.italic": style.italic,
        "font.underline": "single" if style.underline else "none",
        "paragraph.align": "both" if style.align.value == "justify" else style.align.value,
        "paragraph.spacing.before": {"value": style.space_before_pt, "unit": "pt"},
        "paragraph.spacing.after": {"value": style.space_after_pt, "unit": "pt"},
        "paragraph.spacing.line": {"value": style.line_spacing,
            "unit": "multiple" if kind == "auto" else "pt", "kind": kind},
        "paragraph.indent.first_line": {"value": style.first_line_indent_chars, "unit": "character"},
        "paragraph.indent.hanging": {"value": style.hanging_indent_cm * 72 / 2.54, "unit": "pt"},
        "paragraph.page_break_before": style.page_break_before,
    }
    if style.color:
        properties["font.color"] = style.color
    if style.highlight:
        properties["font.highlight"] = style.highlight
    if style.vert_align:
        properties["font.vertical_alignment"] = style.vert_align
    return properties


def compile_profile(template: PaperTemplate, source_path: Path, request: ConfirmWritingProfile, *, actor: str) -> PaperTemplate:
    if not request.accept_recommended or not request.accept_new_object_defaults:
        raise ProfileError("请确认采用的格式、冲突建议与新增图表的版心约束")
    from app.services.source_template_safety import audit_source_package
    raw_source = source_path.read_bytes()
    audit_source_package(raw_source)
    spec = extract_writing_specification(source_path)
    if request.expected_specification_sha256 != spec.specification_sha256:
        raise ProfileError("规范候选已变化，请重新读取后确认")
    rules = {r.id: r for r in spec.rules}
    if set(request.overrides) - set(rules):
        raise ProfileError("覆盖项引用未知或过期的规则")
    from app.services.digit_font_rules import review_digit_font_rules
    digit_candidates = review_digit_font_rules(spec)
    if len(digit_candidates) > 1:
        raise ProfileError("模板含多个数字字体要求，请先澄清规则来源")
    candidate_ids = {item["rule_id"] for item in digit_candidates}
    if (len(request.confirmed_digit_font_rule_ids) != len(set(request.confirmed_digit_font_rule_ids))
            or set(request.confirmed_digit_font_rule_ids) - candidate_ids):
        raise ProfileError("数字字体确认项未知、重复或已过期")
    source = commentless_source(raw_source)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        root = load_part(archive, "word/document.xml")
        styles_root = load_part(archive, "word/styles.xml")
    body = root.find(qn("w:body"))
    if body is None:
        raise ProfileError("母版没有可填充正文")
    children = list(body)
    source_nodes = {n.path: n for n in spec.nodes if n.part == "word/document.xml"}
    cover_table_regions = {n.region for n in spec.nodes if n.role == "table_text" and n.details.get("in_cover")}
    headings = []
    cursor = 0
    candidate = template.model_copy(deep=True)
    # Keywords are inline semantic slots in ordinary Word documents, not new headings.
    from app.models.template import TemplateSection
    existing = {n.type.value for n in flatten(candidate.sections)}
    for evidence in spec.nodes:
        if evidence.part!="word/document.xml" or evidence.role not in {"keywords","english_keywords"} or evidence.role in existing:
            continue
        parents = [n for n in flatten(candidate.sections) if n.type.value == ("abstract" if evidence.role=="keywords" else "english_abstract")]
        prefix = re.split("[：:]",evidence.text,maxsplit=1)[0]
        slot=TemplateSection(key=evidence.role,type=evidence.role,title=prefix,required=True)
        if parents:
            parents[0].children.append(slot)
        else:
            candidate.sections.append(slot)
        existing.add(evidence.role)
    # Structure edits require a fresh mapping and therefore a new published version.
    candidate.allow_structure_edit = False
    prose = [n for n in spec.nodes if n.role == "body" and len(n.text.strip()) >= 80]
    body_properties = _majority(prose or [n for n in spec.nodes if n.role == "body" and n.text.strip()])
    if not body_properties:
        body_properties = _majority(n for n in spec.nodes if n.role in {"abstract", "table_text"}
                                    and not n.details.get("in_cover") and n.text.strip())
    if not body_properties:
        raise ProfileError("未找到可复用的正文格式，请先补充正文示例")
    applied_rule_ids: set[str] = set()
    for rule_id, value in request.overrides.items():
        rule = rules[rule_id]
        if not rule.property.startswith(("font.", "paragraph.")) or rule.property == "instruction.text":
            raise ProfileError("此属性需要在 Word 母版中调整后重新导入")
        # Validate types against the observation, including measurement units.
        if isinstance(rule.expected, dict):
            if not isinstance(value, dict) or value.get("unit") != rule.expected.get("unit") or type(value.get("value")) not in {int,float} or not math.isfinite(value["value"]) or not 0 <= value["value"] <= 1000:
                raise ProfileError("格式覆盖的单位或数值无效")
            if rule.property == "font.size" and (not .5 <= value["value"] <= 200 or value["value"]*2 != round(value["value"]*2)):
                raise ProfileError("字号须为 0.5 到 200 磅，且为半磅的倍数")
        elif type(value) is not type(rule.expected):
            raise ProfileError("格式覆盖类型无效")
        if isinstance(value,str) and (not value or len(value)>128 or any(ord(c)<32 for c in value)):
            raise ProfileError("字体或格式名称无效")
        if rule.property == "paragraph.align" and value not in {"left","right","center","both","justify","distribute"}:
            raise ProfileError("对齐方式无效")
        if rule.property == "font.color" and not re.fullmatch(r"(?:[0-9A-Fa-f]{6}|auto)",value):
            raise ProfileError("颜色须为六位 RGB 或 auto")
        if rule.scope.get("role") == "body":
            body_properties[rule.property] = value
            applied_rule_ids.add(rule_id)
    for section in flatten(candidate.sections):
        if section.type == SectionType.COVER:
            continue
        inline = section.type.value in {"keywords","english_keywords"}
        matches = [i for i in range(cursor,len(children)) if children[i].tag==qn("w:p")
                   and ((norm(_text(children[i])).startswith(norm(section.title)+"：") or norm(_text(children[i])).startswith(norm(section.title)+":")) if inline else norm(_text(children[i]))==norm(section.title))
                   and source_nodes.get(children[i].getroottree().getpath(children[i]))
                   and source_nodes[children[i].getroottree().getpath(children[i])].role != "toc_entry"]
        if not matches:
            raise ProfileError(f"章节未能映射到母版：{section.title}。请采用 Word 中实际存在的章节。")
        index = matches[0]
        cursor = index + 1
        evidence = source_nodes[children[index].getroottree().getpath(children[index])]
        if (section.type.value.startswith("heading") and evidence.role in {"heading1", "heading2", "heading3"}
                and section.type.value != evidence.role):
            raise ProfileError("章节层级与 Word 母版不一致，请重新读取章节后确认：" + section.title)
        region_nodes = [n for n in spec.nodes if n.region == evidence.region and n.role in {"body", "abstract", "english_abstract", "references", "acknowledgement", "appendix"} and n.text.strip()]
        if section.type.value.startswith("heading"):
            # Short lists and instructions often differ from regular prose.
            # A local override needs more than a lone example; explicit reviewed
            # overrides below still apply regardless of the sample count.
            prose = [n for n in region_nodes if len(n.text.strip()) >= 80
                     and not re.search(r"(?:list|列表)", n.details.get("style_name", ""), re.I)]
            region_nodes = prose if len(prose) >= 2 else []
        properties = {**body_properties, **_majority(region_nodes)}
        if inline:
            values = [r["properties"] for r in evidence.details.get("runs",[]) if r.get("text","").strip()]
            properties = {**evidence.properties, **(values[-1] if values else {})}
            properties = {k:v for k,v in properties.items() if k.startswith(("font.","paragraph."))}
        for rule_id, value in request.overrides.items():
            rule = rules[rule_id]
            if rule.scope.get("region") == evidence.region and rule.scope.get("role") in ({section.type.value} if inline else {"body", "abstract", "english_abstract", "references", "acknowledgement", "appendix"}):
                properties[rule.property] = value
                applied_rule_ids.add(rule_id)
        title_properties = copy.deepcopy(evidence.properties)
        title_overridden = False
        title_style_id = ""
        title_style_source_node_id = ""
        if evidence.details.get("source_heading_level") is not None:
            # Preserve the observed conflict in the specification, then bind
            # the corrected heading to an actual source sample of its level.
            level = evidence.details.get("semantic_heading_level")
            examples = [n for n in spec.nodes if n.part == "word/document.xml"
                        and n.role == section.type.value
                        and n.details.get("source_heading_level") is None
                        and re.fullmatch(rf"(?:heading|标题)\s*{level}",
                                         n.details.get("style_name", ""), re.I)
                        and n.details.get("style_id") and n.properties.get("font.size")]
            if not examples:
                raise ProfileError("标题编号与样式冲突，但母版缺少可核验的同级标题样例：" + section.title)
            body_order = {child.getroottree().getpath(child): i for i, child in enumerate(children)}
            examples = [n for n in examples if n.path in body_order]
            if not examples:
                raise ProfileError("同级标题样例不在可映射的正文中：" + section.title)
            exemplar = min(examples, key=lambda n: (body_order[n.path] > index, abs(body_order[n.path] - index)))
            title_properties = copy.deepcopy(exemplar.properties)
            title_style_id = exemplar.details["style_id"]
            title_style_source_node_id = exemplar.node_id
            title_overridden = True
        if inline:
            first = next((r for r in evidence.details.get("runs",[]) if r.get("text","").strip()),{})
            title_properties.update(first.get("properties",{}))
        for rule_id, value in request.overrides.items():
            rule = rules[rule_id]
            if evidence.node_id in rule.evidence_node_ids and rule.scope.get("role", "").startswith("heading"):
                title_properties[rule.property] = value
                applied_rule_ids.add(rule_id)
                title_overridden = True
        section.style = as_text_style(properties, candidate.styles.get("body"))
        if inline:
            candidate.styles[section.type.value]=section.style
        separator = re.search(r"[：:]\s*",evidence.text)
        label_override = request.inline_label_overrides.get(section.key)
        if label_override and (not inline or not label_override.text.strip()
                or re.search(r"[：:\x00-\x1f\x7f]", label_override.text)
                or not re.fullmatch(r"[：:][ \t]{0,4}", label_override.separator)):
            raise ProfileError("关键词标签或分隔符无效：" + section.title)
        separator_properties = copy.deepcopy(properties)
        if label_override and label_override.bold is not None:
            title_properties["font.bold"] = label_override.bold
            separator_properties["font.bold"] = label_override.bold
        headings.append({"key": section.key, "kind": section.type.value, "title": section.title,"inline":inline,
                         "display_label": label_override.text if label_override else section.title,
                         "separator_properties": separator_properties,
                         "title_overridden":title_overridden,
                         "title_style_id":title_style_id, "title_style_source_node_id":title_style_source_node_id,
                         "bookmark_name":"PGH"+hashlib.sha256(section.key.encode()).hexdigest()[:16],
                         "separator":label_override.separator if label_override else (separator.group() if inline and separator else "："),
                         "index": index, "properties": properties, "title_properties": title_properties})
    if not headings:
        raise ProfileError("模板需要至少一个明确的正文或表单区域")
    if set(request.inline_label_overrides) - {h["key"] for h in headings if h["inline"]}:
        raise ProfileError("关键词标签无法绑定到已识别区域")
    # Resolve cover values without asking users to add programming placeholders.
    fields = []
    mapped_targets: dict[str, str] = {}
    aliases = {"title": r"(?:论文)?题目", "author": r"(?:学生)?姓名", "student_id": r"(?:学生)?学号",
               "college": "学院(?:[（(]系[）)])?", "major": "专业", "class_name": "班级", "advisor": "指导教师|导师", "school": "学校名称|学校", "date": "日期"}
    before = children[:headings[0]["index"]]
    for field in candidate.meta_fields:
        targets: dict[str, tuple[Any, str]] = {}
        pattern = aliases.get(field.key, re.escape(field.label))
        for block in before:
            for row in block.findall(f".//{qn('w:tr')}"):
                cells = row.findall(qn("w:tc"))
                for column, cell in enumerate(cells[:-1]):
                    visible_label = "".join(t.text or "" for t in cell.xpath(".//w:t[not(ancestor::w:txbxContent)]",namespaces=NS))
                    if re.fullmatch(f"(?:{pattern})[：:]?", norm(visible_label)):
                        paragraphs = cells[column+1].findall(qn("w:p"))
                        target = next((p for p in paragraphs if _text(p).strip() and source_nodes.get(p.getroottree().getpath(p), None) and source_nodes[p.getroottree().getpath(p)].role != "instruction"), paragraphs[-1] if paragraphs else None)
                        if target is not None:
                            path = target.getroottree().getpath(target)
                            label_path = cell.getroottree().getpath(cell)
                            targets[label_path] = (target, "")
            if block.tag == qn("w:p"):
                text = _text(block)
                match = re.match(rf"\s*({pattern})\s*[：:]", text.replace(" ", ""))
                if match:
                    path = block.getroottree().getpath(block)
                    targets[path] = (block, match.group(1) + "：")
                if field.key == "date" and re.fullmatch(r"\s*\d{4}\s*年\s*\d{1,2}\s*月\s*", text):
                    path = block.getroottree().getpath(block)
                    targets[path] = (block, "")
        if len(targets) > 1:
            raise ProfileError(f"封面字段存在多个候选位置：{field.label}（{len(targets)}处），请先在 Word 模板中消除重复标签")
        target, prefix = next(iter(targets.values()), (None, ""))
        if target is None:
            if field.required:
                raise ProfileError(f"封面字段无法定位：{field.label}")
            continue
        path = target.getroottree().getpath(target)
        previous_key = mapped_targets.get(path)
        if previous_key and previous_key != field.key:
            raise ProfileError(f"多个封面字段映射到了同一位置：{previous_key}、{field.key}（{path}）")
        mapped_targets[path] = field.key
        evidence = source_nodes.get(path)
        properties = dict(evidence.properties) if evidence else dict(body_properties)
        if not _text(target).strip():
            # Prefer the same cover column's actual values over a blank run's defaults.
            peers = [n for n in spec.nodes if n.role == "table_text" and n.text.strip() and not re.search(r"[：:]\s*$", n.text)]
            if evidence and evidence.details.get("in_cover"):
                peers = [n for n in peers if n.details.get("in_cover")]
            properties = {**properties, **_majority(peers)}
        if evidence and evidence.details.get("in_cover"):
            for rule_id, value in request.overrides.items():
                rule = rules[rule_id]
                if rule.scope.get("role") == "table_text" and rule.scope.get("region") == evidence.region:
                    properties[rule.property] = value
                    applied_rule_ids.add(rule_id)
        fields.append({"key": field.key, "path": path, "prefix": prefix, "properties": properties})
    removals = []
    if request.remove_template_instructions:
        for n in spec.nodes:
            if n.part == "word/document.xml" and n.role == "instruction":
                removals.append(n.path)
    elif any(n.role == "instruction" for n in spec.nodes):
        raise ProfileError("模板包含填写说明，请确认是否从学生成品移除")
    candidate.styles["body"] = as_text_style(body_properties, candidate.styles.get("body"))
    candidate.writing_specification = spec
    from app.services.template_content_rules import compile_confirmed_content_requirements
    try:
        content_rules = compile_confirmed_content_requirements(
            spec,
            candidate.sections,
            request.confirmed_content_rule_ids,
            template_id=candidate.id,
            template_name=candidate.name,
            template_version=candidate.version,
            meta_fields=candidate.meta_fields,
            defense_date_field_key=request.defense_date_field_key,
        )
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc
    content_rules["review"]["manual_items"] = [item for item in content_rules["review"]["manual_items"]
                                                 if item["rule_id"] not in candidate_ids]
    content_rules["review_requirements"] = [item for item in content_rules["review_requirements"]
                                             if (item.get("provenance") or {}).get("rule_id") not in candidate_ids]
    profile = {"compiler_version": PROFILE_VERSION, "control_appearance":"hidden", "source_sha256": spec.source_sha256,
               "preserve_manuscript_structure": request.preserve_manuscript_structure,
               "specification_sha256": spec.specification_sha256, "confirmed_by": actor,
               "confirmed_at": datetime.now(timezone.utc).isoformat(), "headings": headings, "fields": fields,
               "removals": removals, "overrides": request.overrides, "accept_recommended": True,
               "content_rule_compiler_version": content_rules["review"]["compiler_version"],
               "confirmed_content_rule_ids": content_rules["confirmed_rule_ids"],
               "confirmed_digit_font_rule_ids": request.confirmed_digit_font_rule_ids,
               "inline_label_overrides": {key: value.model_dump(mode="json") for key, value in request.inline_label_overrides.items()},
               "body_page_number": request.body_page_number.model_dump(mode="json") if request.body_page_number else None,
               "digit_font_review": {"candidates": digit_candidates},
               "digit_font_rule": digit_candidates[0] if request.confirmed_digit_font_rule_ids else None,
               "defense_date_field_key": request.defense_date_field_key,
               "content_rule_review": content_rules["review"],
               "content_requirements": content_rules["review_requirements"],
               "new_object_policy": {"images": "fit_page_preserve_ratio", "tables": "fit_page_repeat_header",
                                     "origin": "explicitly_confirmed_layout_defaults"},
               "body_properties": body_properties, "template_basis_sha256": digest(execution_basis(candidate))}
    # Freeze mapped headings and the source TOC region. The Office worker
    # rebuilds its rows from the refreshed document instead of patching old runs.
    from app.services.office_layout import heading_matches
    ns = {k: v for element in root.iter() for k, v in element.nsmap.items() if k}
    toc_entries = []
    toc_style_by_level = {}
    toc_font_requirements = set()
    toc_removals = []
    unmapped_toc_labels = []
    source_order = {child.getroottree().getpath(child): index for index, child in enumerate(children)}
    style_names = {n.get(qn("w:styleId")): (n.find(qn("w:name")).get(qn("w:val"), "") if n.find(qn("w:name")) is not None else n.get(qn("w:styleId"), ""))
                   for n in (styles_root.findall(qn("w:style")) if styles_root is not None else [])}
    def toc_level(style_id):
        name = style_names.get(style_id, style_id)
        match = re.search(r"(?:toc|contents|目录)\s*([1-9])", name, re.I)
        return int(match.group(1)) if match else None
    toc_field_paragraphs = []
    for paragraph in root.findall(".//"+qn("w:p")):
        instructions = [node.text or "" for node in paragraph.findall(".//"+qn("w:instrText"))]
        instructions.extend(node.get(qn("w:instr"), "") for node in paragraph.findall(".//"+qn("w:fldSimple")))
        if any(re.match(r"^\s*TOC(?:\s|$)", instruction, re.I) for instruction in instructions):
            toc_field_paragraphs.append(paragraph)
    if any(n.role == "toc_entry" for n in spec.nodes) and not toc_field_paragraphs:
        raise ProfileError("检测到静态目录但没有 Word 目录域，无法可靠刷新页码；请先在 Word 中插入自动目录")
    toc_active = bool(toc_field_paragraphs)
    if len(toc_field_paragraphs) > 1:
        raise ProfileError("模板包含多个目录域，当前版本要求一个主目录域")
    toc_range_paths = []
    toc_container_path = ""
    if toc_active:
        anchor = toc_field_paragraphs[0]
        parent = anchor.getparent()
        if parent is None or parent.tag != qn("w:body"):
            raise ProfileError("目录域必须位于正文主流程，当前模板结构暂不支持")
        toc_container_path = parent.getroottree().getpath(parent)
        row_indexes = [source_order[n.path] for n in spec.nodes if n.part == "word/document.xml" and n.role == "toc_entry" and n.path in source_order]
        anchor_index = source_order.get(anchor.getroottree().getpath(anchor))
        if anchor_index is None:
            raise ProfileError("目录域段落无法定位到正文")
        first = min(row_indexes + [anchor_index]); last = max(row_indexes + [anchor_index])
        for child in children[first:last+1]:
            if child.tag != qn("w:p"):
                raise ProfileError("目录区域含有非段落对象，无法安全重建")
            style = child.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
            sid = style.get(qn("w:val"), "") if style is not None else ""
            if sid and toc_level(sid) is None and _text(child).strip():
                raise ProfileError("目录行之间含有普通正文，无法安全重建")
            if not sid and _text(child).strip() and child is not anchor:
                raise ProfileError("目录区域含有非目录文字，无法安全重建")
            toc_range_paths.append(child.getroottree().getpath(child))
    toc_style_ids = sorted({style_id for style_id in style_names if toc_level(style_id) is not None}
                           | {n.details.get("style_id", "") for n in spec.nodes if n.role == "toc_entry" and n.details.get("style_id")})
    toc_evidence = sorted((n for n in spec.nodes if n.role == "toc_entry"),
                          key=lambda n: source_order.get(n.path, 10**9))
    if any(n.path not in source_order for n in toc_evidence):
        raise ProfileError("目录行必须是正文中的独立段落，表格目录暂不支持")
    for index, evidence in enumerate(toc_evidence):
        text = evidence.text.strip()
        page_match = re.search(r"([0-9ivxlcdmIVXLCDM]+)\s*$", text)
        if not page_match:
            raise ProfileError("目录条目缺少可识别的页码：" + text[:80])
        label = text[:page_match.start()].rstrip()
        level = toc_level(evidence.details.get("style_id", ""))
        if level is None:
            raise ProfileError("目录层级样式无法识别：" + label[:80])
        style_id = evidence.details.get("style_id", "")
        if level in toc_style_by_level and toc_style_by_level[level] != style_id:
            raise ProfileError("同一目录层级使用了多个母版样式，当前版本无法安全重建")
        toc_style_by_level[level] = style_id
        for run in evidence.details.get("runs", []):
            props = run.get("properties", {})
            counts = run.get("script_character_counts", {})
            if counts.get("eastAsia") and isinstance(props.get("font.eastAsia"), str):
                toc_font_requirements.add(props["font.eastAsia"])
            if counts.get("latin") and isinstance(props.get("font.ascii"), str):
                toc_font_requirements.add(props["font.ascii"])
        targets = [h for h in headings if h["kind"] != "toc" and heading_matches(label, h["title"])]
        if len(targets) != 1:
            unmapped_toc_labels.append(label)
            toc_removals.append(evidence.path)
            continue
        target = targets[0]
        toc_entries.append({"key": f"toc-{index}", "paragraph_path": evidence.path,
                            "target_key": target["key"], "target_title": target["title"],
                            "source_label": label, "source_page": page_match.group(1),
                            "level": level, "style_id": style_id})
    # A source directory may repeat the incorrect source style. The confirmed
    # semantic level, not that cached entry, determines the rebuilt TOC level.
    for entry in toc_entries:
        target = next(h for h in headings if h["key"] == entry["target_key"])
        if not target.get("title_style_id"):
            continue
        expected_level = int(target["kind"][-1])
        if entry["level"] != expected_level:
            if expected_level not in toc_style_by_level:
                raise ProfileError("目录缺少已修正标题层级的母版样式：" + target["title"])
            entry["level"] = expected_level
            entry["style_id"] = toc_style_by_level[expected_level]
    profile["toc_entries"] = toc_entries
    profile["toc_active"] = toc_active
    profile["toc_style_ids"] = toc_style_ids
    profile["toc_style_by_level"] = toc_style_by_level
    profile["toc_font_requirements"] = sorted(toc_font_requirements)
    if toc_active:
        from app.services.toc_appearance import source_toc_appearance
        profile["toc_appearance_by_level"] = source_toc_appearance(source, profile)
    profile["toc_range_paths"] = toc_range_paths
    profile["toc_container_path"] = toc_container_path
    if unmapped_toc_labels and not request.remove_unmatched_toc_entries:
        raise ProfileError("目录中有无对应正文的条目，请确认是否清理：" + "、".join(label[:50] for label in unmapped_toc_labels[:8]))
    profile["toc_removals"] = toc_removals if request.remove_unmatched_toc_entries else []
    profile["removed_toc_entries"] = unmapped_toc_labels if request.remove_unmatched_toc_entries else []
    profile["confirmed_remove_unmatched_toc_entries"] = bool(request.remove_unmatched_toc_entries)
    profile["prepared_source_manifest_sha256"]=digest(package_manifest(source))
    # Prefer explicit alignment on body figure specimens; absent such evidence,
    # the confirmed new-object policy places figures in the middle of the page.
    namespaces = {k: v for element in root.iter() for k, v in element.nsmap.items() if k}
    image_alignments = []
    for image in (node for node in spec.nodes if node.part == "word/document.xml" and node.role == "image"):
        found = root.xpath(image.path, namespaces=namespaces)
        paragraphs = found[0].xpath("ancestor::w:p[1]", namespaces=namespaces) if found else []
        if not paragraphs:
            continue
        paragraph = paragraphs[0]
        evidence = source_nodes.get(paragraph.getroottree().getpath(paragraph))
        align = paragraph.find(f"{qn('w:pPr')}/{qn('w:jc')}")
        value = align.get(qn("w:val")) if align is not None else None
        if evidence and evidence.role == "body" and value in {"left", "center", "right"}:
            image_alignments.append(value)
    if len(set(image_alignments)) > 1:
        raise ProfileError("母版正文图片存在不同对齐方式，请先明确统一图版规则")
    profile["image_alignment"] = image_alignments[0] if image_alignments else "center"
    from docx import Document
    from app.services.table_borders import template_table_borders
    try:
        profile["table_borders"] = template_table_borders(Document(source_path))
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc
    object_style_keys = {"figure_caption": "figure_caption", "table_caption": "table_caption", "table_text": "table_cell"}
    profile["object_properties"] = {}
    for role, style_key in object_style_keys.items():
        specimens = [n for n in spec.nodes if n.role == role and n.part == "word/document.xml"
                     and not n.details.get("in_cover") and n.text.strip()]
        configured = candidate.styles.get(style_key) or DEFAULT_STYLES[style_key]
        profile["object_properties"][role] = (_majority(specimens) if specimens
                                                else _configured_object_properties(configured, body_properties))
    for rule_id, value in request.overrides.items():
        role = rules[rule_id].scope.get("role")
        if role in profile["object_properties"] and rules[rule_id].scope.get("region") not in cover_table_regions:
            profile["object_properties"][role][rules[rule_id].property] = value
            applied_rule_ids.add(rule_id)
    if set(request.overrides) - applied_rule_ids:
        ignored = [rules[item].property for item in sorted(set(request.overrides) - applied_rule_ids)]
        raise ProfileError("格式覆盖无法绑定到已识别区域：" + "、".join(ignored))
    from app.services.page_number_policy import compile_page_number_policy
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        profile["body_page_number_policy"] = compile_page_number_policy(
            archive, root, headings, profile["body_page_number"])
    profile["protected_document_sha256"] = freeze_protected_structure(root, profile)
    candidate.source_execution = sign_profile(profile)
    return candidate


def freeze_protected_structure(root, profile):
    """Freeze fixed structure independently of all later student content."""
    from app.services.master_docx_renderer import apply_page_layout, prepare_source, page_box
    root = copy.deepcopy(root)
    apply_page_layout(root, profile.get("policy_layout_overrides"))
    for heading in profile["headings"]:
        heading["page_box_cm"] = list(page_box(root,heading["index"]))
    prepared = prepare_source(copy.deepcopy(root),profile)
    for content in prepared.xpath(".//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,'pg-region:') or starts-with(w:sdtPr/w:tag/@w:val,'pg-field:')]/w:sdtContent",namespaces=NS):
        for node in list(content):
            content.remove(node)
    if profile.get("toc_active"):
        # Field controls are intentionally staticized by the Office round-trip.
        # Normalize them in the expected protected structure as well, while the
        # unique placeholder still anchors removal of a dangling TOC end marker.
        from app.services.office_layout import _remove_toc_field_controls
        _remove_toc_field_controls(prepared.find(qn("w:body")), profile.get("toc_style_ids", []))
        placeholders=prepared.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val='pg-toc:placeholder']",namespaces=NS)
        if len(placeholders)!=1:
            raise ProfileError("母版目录锚点无法绑定到受保护结构")
        parent=placeholders[0].getparent()
        if parent is None:
            raise ProfileError("母版目录锚点已脱离文档结构")
        parent.remove(placeholders[0])
    def structural(node):
        return [node.tag,sorted(node.attrib.items()),node.text or "",[structural(c) for c in node]]
    return digest(structural(prepared))


def trial_profile(template: PaperTemplate, source_path: Path) -> dict:
    """Prove normal and long-content execution before activating a published plan."""
    import tempfile
    from app.models.project import WritingProject, ProjectSection, ParagraphBlock
    from app.services.master_docx_renderer import render_master_docx
    from app.services.docx_output_check import inspect_docx_output
    reports = {}
    for label, text in (("normal","正文试填 123。Template validation 456."),("long","长段落 123 用于验证正文扩展与母版保护。"*250)):
        expandable=next((n.key for n in flatten(template.sections) if n.type.value=="heading1"),"")
        project = WritingProject(id="profile-trial",template_id=template.id,
            meta={field.key:"试填 "+field.label for field in template.meta_fields},
            sections=[ProjectSection(key=n.key,type=n.type,title=n.title,paragraphs=[ParagraphBlock(id=n.key+"-trial",text=text if label=="normal" or n.key==expandable else "正文试填。Template validation.")]) for n in flatten(template.sections) if n.type.value not in {"cover","toc"}])
        with tempfile.TemporaryDirectory(prefix="paperguard-profile-trial-") as directory:
            output = Path(directory)/"trial.docx"
            render_master_docx(project,template,str(output),source_path=str(source_path))
            report = inspect_docx_output(output.read_bytes(),project,template,source_template_bytes=source_path.read_bytes())
        if report["status"] != "pass":
            failed = [c["rule"]+":"+str(c["object"]) for c in report["checks"] if c["status"]!="pass"]
            raise ProfileError("母版试填未通过，请调整以下格式或映射："+"；".join(failed[:8]))
        reports[label]={key:report[key] for key in ("status","check_count","source_profile_sha256","output_sha256","project_content_hash")}
    return reports


def paper_from_definition(template_id: str, version: str, definition) -> PaperTemplate:
    """Use one execution model for thesis, generic documents and personal writing."""
    from app.models.template import TemplateSection, MetaField
    raw = definition.model_dump(mode="json") if hasattr(definition,"model_dump") else definition
    kinds = {s.value for s in SectionType}
    def section(node):
        kind = node.get("kind",node.get("type","body"))
        return TemplateSection(key=node["key"],title=node["title"],type=kind if kind in kinds else "body",
                               required=node.get("required",True),style=node.get("style"),children=[section(c) for c in node.get("children",[])])
    return PaperTemplate(id=template_id,name="Word 母版",version=version,source_docx=raw.get("source_docx",""),
                         sections=[section(n) for n in raw.get("sections",[])],styles=raw.get("styles",{}),
                         meta_fields=[MetaField(key=f["key"],label=f["label"],required=f.get("required",True)) for f in raw.get("fields",[]) if f.get("location","metadata")=="metadata"],
                         page_layout=raw.get("page_layout",{}),allow_structure_edit=raw.get("allow_structure_edit",True),
                         source_execution=raw.get("extensions",{}).get("source_execution",{}))
