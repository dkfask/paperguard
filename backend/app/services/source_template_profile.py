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


class CoverPaginationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # An offset of 1 means the page immediately before the first content heading.
    allowed_blank_offsets: list[int] = Field(default_factory=list, max_length=64)
    require_boundary_new_page: bool = True

    @field_validator("allowed_blank_offsets", mode="before")
    @classmethod
    def valid_blank_offsets(cls, value):
        if (not isinstance(value, list) or any(type(item) is not int or not 1 <= item <= 63 for item in value)
                or len(set(value)) != len(value)):
            raise ValueError("允许留白页的相对位置无效或重复")
        return sorted(value)


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
    cover_pagination: CoverPaginationSettings | None = None
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


def _heading_instruction(paragraph):
    """Find an explicitly red, imperative suffix in a source heading."""
    text = _text(paragraph)
    match = re.search(
        r"[（(]\s*(?:(?:按|按照|根据)[^（）()]{0,36}(?:进行|予以)(?:描述|填写|编写|撰写)"
        r"|(?:请|此处请)(?:填写|描述|编写|撰写)[^（）()]{0,30})\s*[）)]\s*$", text)
    if not match or not text[:match.start()].strip():
        return None
    offset = 0
    red = False
    for node in paragraph.findall(".//" + qn("w:t")):
        parents = node.xpath("ancestor::w:p[1]", namespaces=NS)
        if not parents or parents[0] is not paragraph:
            continue
        value = node.text or ""
        if offset + len(value) > match.start() and value.strip():
            runs = node.xpath("ancestor::w:r[1]", namespaces=NS)
            color = runs[0].find(f"{qn('w:rPr')}/{qn('w:color')}") if runs else None
            red |= color is not None and color.get(qn("w:val"), "").upper() == "FF0000"
        offset += len(value)
    if not red:
        return None
    kept = text[:match.start()].rstrip()
    return {"source_text": text, "kept_text": kept, "removed_text": text[len(kept):]}


def _cover_table_overrides(spec, overrides):
    """Bind confirmed table-text overrides to the observed cover paragraphs."""
    rules = {rule.id: rule for rule in spec.rules}
    targets = {}
    for rule_id, value in overrides.items():
        rule = rules.get(rule_id)
        if not rule or rule.scope.get("role") != "table_text":
            continue
        for node in spec.nodes:
            if (node.part == "word/document.xml" and node.role == "table_text"
                    and node.details.get("in_cover") and node.region == rule.scope.get("region")):
                targets.setdefault(node.path, {})[rule.property] = value
    return [{"path": path, "properties": targets[path]} for path in sorted(targets)]


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
    # A previously signed profile may have frozen a red template instruction as
    # heading text. Check the bound source before allowing it to certify output.
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        root = load_part(archive, "word/document.xml")
        if profile.get("inline_content_format_version") != "source-content-runs/1":
            from app.core.extract.effective_format import EffectiveFormatResolver
            inline_resolver = EffectiveFormatResolver(load_part(archive, "word/styles.xml"),
                load_part(archive, "word/theme/theme1.xml"), load_part(archive, "word/settings.xml"))
        else:
            inline_resolver = None
    body = root.find(qn("w:body")) if root is not None else None
    children = list(body) if body is not None else []
    if inline_resolver is not None:
        for heading in profile.get("headings", []):
            if not heading.get("inline") or heading.get("properties", {}).get("font.bold") is not True:
                continue
            index = heading.get("index")
            if type(index) is not int or not 0 <= index < len(children) or children[index].tag != qn("w:p"):
                raise ProfileError("关键词母版位置已失效，请联系系统团队重新适配")
            _, _, runs, _ = inline_resolver.paragraph(children[index], include_whitespace=True)
            text = "".join(run["text"] for run in runs)
            separator = re.search(r"[：:]\s*", text)
            if separator:
                offset = 0
                content_runs = []
                for run in runs:
                    start = max(0, separator.end() - offset)
                    if run["text"][start:].strip():
                        content_runs.append(run)
                    offset += len(run["text"])
                if content_runs and not content_runs[-1]["properties"].get("font.bold", False):
                    spec = template.writing_specification
                    bound_spec = (spec is not None and spec.source_sha256 == profile.get("source_sha256")
                                  and spec.specification_sha256 == profile.get("specification_sha256")
                                  and digest(spec.model_dump(mode="json", exclude={"specification_sha256"})) == spec.specification_sha256)
                    source_path = children[index].getroottree().getpath(children[index])
                    nodes = ([node for node in spec.nodes if node.part == "word/document.xml"
                              and node.path == source_path and node.role == heading.get("kind")]
                             if bound_spec else [])
                    explicitly_bold = (len(nodes) == 1 and any(
                        rule.scope.get("role") == heading.get("kind")
                        and rule.scope.get("region") == nodes[0].region
                        and rule.property == "font.bold"
                        and profile.get("overrides", {}).get(rule.id) is True
                        for rule in spec.rules))
                    if not explicitly_bold:
                        raise ProfileError("模板关键词内容格式需要重新适配，请联系系统团队")
    for heading in profile.get("headings", []):
        if heading.get("kind") not in {"heading1", "heading2", "heading3"}:
            continue
        index = heading.get("index")
        if type(index) is not int or not 0 <= index < len(children):
            raise ProfileError("模板标题的母版位置已失效，请重新确认")
        observed = _heading_instruction(children[index])
        removal = heading.get("instruction_removal")
        if removal:
            if (not observed or any(removal.get(key) != value for key, value in observed.items())
                    or removal.get("source_path") != children[index].getroottree().getpath(children[index])
                    or not removal.get("source_node_id")
                    or removal.get("source_title", "").rstrip() != heading["title"] + observed["removed_text"].rstrip()):
                raise ProfileError("模板标题说明的移除记录与母版不一致，请重新确认")
        elif observed and not profile.get("manuscript_headings_sha256"):
            raise ProfileError("模板标题仍含填写说明，请重新确认并移除后交付")
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
    if "cover_table_overrides" not in profile and profile.get("overrides"):
        # Old signed profiles can be upgraded in memory only when the bound
        # specification proves the exact cover targets and the old frozen
        # structure still reproduces. The stored signature and old files stay
        # unchanged; new outputs are checked against the derived structure.
        spec = template.writing_specification
        if (spec is None or spec.source_sha256 != profile.get("source_sha256")
                or spec.specification_sha256 != profile.get("specification_sha256")
                or digest(spec.model_dump(mode="json", exclude={"specification_sha256"})) != spec.specification_sha256):
            raise ProfileError("旧模板格式覆盖缺少可验证的源规范，请重新确认模板")
        cover_overrides = _cover_table_overrides(spec, profile["overrides"])
        if cover_overrides:
            with zipfile.ZipFile(io.BytesIO(commentless_source(source))) as archive:
                original_root = load_part(archive, "word/document.xml")
            if (original_root is None or freeze_protected_structure(original_root, copy.deepcopy(profile))
                    != profile.get("protected_document_sha256")):
                raise ProfileError("旧模板受保护结构无法重现，请重新确认模板")
            profile = copy.deepcopy(profile)
            profile["cover_table_overrides"] = cover_overrides
            profile["protected_document_sha256"] = freeze_protected_structure(original_root, copy.deepcopy(profile))
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


def _majority(nodes, *, ignore_instructional_red: bool = False) -> dict:
    """Aggregate dominant font/paragraph properties across specimen nodes.

    Implicit Word defaults (non-bold, non-italic, automatic color, no
    underline) vote alongside explicit properties so minority overrides (such as
    bold table headers or red instructional examples) do not override the
    baseline. Automatic color and ``none`` underline remain omitted from the
    returned dict so uncolored outputs preserve implicit-color XML contracts.
    """
    defaults = {
        "paragraph.spacing.before": {"value": 0.0, "unit": "pt"},
        "paragraph.spacing.after": {"value": 0.0, "unit": "pt"},
        "paragraph.indent.left": {"value": 0.0, "unit": "pt"},
        "paragraph.indent.right": {"value": 0.0, "unit": "pt"},
        "paragraph.indent.first_line": {"value": 0.0, "unit": "character"},
        "font.bold": False,
        "font.italic": False,
        "font.underline": "none",
        "font.color": "auto",
    }
    values: dict[str, Counter] = {}
    for n in nodes:
        for key, value in {**defaults, **n.properties}.items():
            if not key.startswith(("font.", "paragraph.")) or value is None:
                continue
            if (ignore_instructional_red and key == "font.color"
                    and isinstance(value, str) and value.upper() == "FF0000"):
                value = "auto"
            values.setdefault(key, Counter())[json.dumps(value, sort_keys=True)] += max(1, min(len(n.text.strip()), 1000))
    result = {key: json.loads(counts.most_common(1)[0][0]) for key, counts in values.items()}
    if result.get("font.color") == "auto":
        result.pop("font.color", None)
    if result.get("font.underline") == "none":
        result.pop("font.underline", None)
    return result


def _table_header_cell_prefixes(spec) -> tuple[str, ...]:
    """Return cell XPath prefixes for header rows of multi-row sample tables."""
    prefixes = []
    for node in spec.nodes:
        if node.role != "table" or node.part != "word/document.xml":
            continue
        rows = node.details.get("rows") or []
        if len(rows) <= 1:
            continue
        for row_index, row in enumerate(rows):
            if row_index == 0 or row.get("repeat_header"):
                for cell in row.get("cells") or []:
                    path = cell.get("path")
                    if path:
                        prefixes.append(path + "/")
    return tuple(prefixes)


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



def _automatic_heading_evidence(source: bytes, source_nodes: dict) -> tuple[dict, dict, set[int]]:
    """Recover only decimal numbering whose complete Word counter is observable."""
    from docx import Document
    from docx.text.paragraph import Paragraph
    from app.services.docx_project_import import _HeadingNumbering

    document = Document(io.BytesIO(source))
    body = document.element.body
    records = []
    numbered_heading_indexes = set()
    for element in body.iter(qn("w:p")):
        paragraph = Paragraph(element, document)
        direct = element.find(f"{qn('w:pPr')}/{qn('w:numPr')}")
        style = paragraph.style
        inherited = []
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            properties = style.element.find(f"{qn('w:pPr')}/{qn('w:numPr')}")
            if properties is not None:
                inherited.append(properties)
            style = style.base_style
        number = direct if direct is not None else next(iter(inherited), None)
        if number is None:
            continue
        num_id = number.find(qn("w:numId"))
        if num_id is None:
            # A partial numPr can inherit its numbering ID from an ancestor style.
            num_id = next((item.find(qn("w:numId")) for item in inherited
                           if item.find(qn("w:numId")) is not None), None)
        value = num_id.get(qn("w:val"), "") if num_id is not None else ""
        evidence = source_nodes.get(element.getroottree().getpath(element))
        if (value != "0" and element.getparent() is body and evidence
                and evidence.role in {"heading1", "heading2", "heading3"}):
            numbered_heading_indexes.add(body.index(element))
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
            continue
        level = number.find(qn("w:ilvl"))
        complete = direct is not None and direct.find(qn("w:numId")) is not None and level is not None
        records.append((element, paragraph, value, complete, element.getparent() is body))

    heading_ids = set()
    for element, _, num_id, complete, top_level in records:
        evidence = source_nodes.get(element.getroottree().getpath(element))
        if complete and top_level and evidence and evidence.role in {"heading1", "heading2", "heading3"}:
            heading_ids.add(num_id)
    labels, uses = {}, {}
    numbering = _HeadingNumbering(document)
    if numbering.root is None:
        return labels, uses, numbered_heading_indexes
    for num_id in heading_ids:
        numbered = [(e, p, complete, top) for e, p, ident, complete, top in records if ident == num_id]
        if any(not complete or not top for _, _, complete, top in numbered):
            continue
        nums = numbering.root.xpath(f'./w:num[@w:numId="{num_id}"]')
        if len(nums) != 1 or nums[0].xpath('./w:lvlOverride/w:lvl'):
            continue
        abstract_ref = nums[0].find(qn("w:abstractNumId"))
        abstract_id = abstract_ref.get(qn("w:val"), "") if abstract_ref is not None else ""
        if not re.fullmatch(r"[0-9]{1,10}", abstract_id):
            continue
        abstracts = numbering.root.xpath(f'./w:abstractNum[@w:abstractNumId="{abstract_id}"]')
        if len(abstracts) != 1:
            continue
        abstract = abstracts[0]
        restart = abstract.get("{http://schemas.microsoft.com/office/word/2012/wordml}restartNumberingAfterBreak", "0")
        if (restart not in {"0", "false", "off"}
                or abstract.find(qn("w:numStyleLink")) is not None
                or abstract.findall(f"{qn('w:lvl')}/{qn('w:lvlRestart')}")):
            continue
        pending = {}
        try:
            for element, paragraph, _, _ in numbered:
                evidence = source_nodes.get(element.getroottree().getpath(element))
                is_heading = bool(evidence and evidence.role in {"heading1", "heading2", "heading3"})
                text = evidence.text if evidence is not None else paragraph.text
                visible = numbering.text(paragraph, text, is_heading)
                if is_heading and norm(visible) != norm(text):
                    pending[body.index(element)] = (visible, num_id)
        except (ValueError, TypeError, KeyError, IndexError):
            continue
        labels.update(pending)
        uses[num_id] = {body.index(element) for element, _, _, _ in numbered}
    return labels, uses, numbered_heading_indexes


def compile_profile(template: PaperTemplate, source_path: Path, request: ConfirmWritingProfile, *, actor: str,
                    preserve_manuscript_heading_text: bool = False) -> PaperTemplate:
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
    body_properties = _majority(
        prose or [n for n in spec.nodes if n.role == "body" and n.text.strip()],
        ignore_instructional_red=True,
    )
    if not body_properties:
        body_properties = _majority(
            (n for n in spec.nodes if n.role in {"abstract", "table_text"}
             and not n.details.get("in_cover") and n.text.strip()),
            ignore_instructional_red=True,
        )
    if not body_properties:
        raise ProfileError("未找到可复用的正文格式，请先补充正文示例")
    automatic_headings, numbering_uses, numbered_heading_indexes = _automatic_heading_evidence(source, source_nodes)
    matched_automatic_ids: set[str] = set()
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
    section_parents = {}
    def remember_parents(nodes, parent=None):
        for node in nodes:
            section_parents[node.key] = parent
            remember_parents(node.children, node)
    remember_parents(candidate.sections)
    ordered_sections = list(flatten(candidate.sections))
    semantic_corrections = []
    last_heading2 = None
    for section in ordered_sections:
        if section.type == SectionType.COVER:
            continue
        inline = section.type.value in {"keywords","english_keywords"}
        matches = [i for i in range(cursor,len(children)) if children[i].tag==qn("w:p")
                   and ((norm(_text(children[i])).startswith(norm(section.title)+"：") or norm(_text(children[i])).startswith(norm(section.title)+":")) if inline else norm(_text(children[i]))==norm(section.title))
                   and source_nodes.get(children[i].getroottree().getpath(children[i]))
                   and source_nodes[children[i].getroottree().getpath(children[i])].role != "toc_entry"]
        automatic_matches = [i for i, (text, _) in automatic_headings.items()
                             if i >= cursor and not inline and section.type.value.startswith("heading")
                             and norm(text) == norm(section.title)
                             and source_nodes[children[i].getroottree().getpath(children[i])].role == section.type.value]
        matches = sorted(set(matches + automatic_matches))
        if not matches:
            raise ProfileError(f"章节未能映射到母版：{section.title}。请采用 Word 中实际存在的章节。")
        if automatic_matches and len(matches) != 1:
            raise ProfileError("自动编号章节存在多个匹配位置，请先消除歧义：" + section.title)
        index = matches[0]
        if index in numbered_heading_indexes:
            if index not in automatic_headings:
                raise ProfileError("章节自动编号的完整计数无法验证：" + section.title)
            matched_automatic_ids.add(automatic_headings[index][1])
        cursor = index + 1
        evidence = source_nodes[children[index].getroottree().getpath(children[index])]
        if (section.type.value.startswith("heading") and evidence.role in {"heading1", "heading2", "heading3"}
                and section.type.value != evidence.role):
            source_level = evidence.details.get("source_heading_level")
            semantic_level = evidence.details.get("semantic_heading_level")
            child_number = re.match(r"^\s*(\d+\.\d+)\.\d+(?:\s|$)", section.title)
            parent_number = re.match(r"^\s*(\d+\.\d+)(?!\.)", last_heading2.title) if last_heading2 else None
            old_parent = section_parents[section.key]
            if not (section.type == SectionType.HEADING2 and evidence.role == "heading3"
                    and source_level == 2 and semantic_level == 3 and child_number and parent_number
                    and child_number.group(1) == parent_number.group(1) and not section.children
                    and old_parent is section_parents[last_heading2.key]):
                raise ProfileError("章节层级与 Word 母版不一致，请重新读取章节后确认：" + section.title)
            container = old_parent.children if old_parent is not None else candidate.sections
            container.remove(section)
            last_heading2.children.append(section)
            section_parents[section.key] = last_heading2
            section.type = SectionType.HEADING3
            semantic_corrections.append({"key": section.key, "title": section.title,
                "from": "heading2", "to": "heading3", "parent_key": last_heading2.key,
                "source_node_id": evidence.node_id})
        instruction_removal = None
        observed_instruction = (_heading_instruction(children[index])
                                if section.type.value in {"heading1", "heading2", "heading3"}
                                and not preserve_manuscript_heading_text else None)
        if observed_instruction:
            if not request.remove_template_instructions:
                raise ProfileError("模板标题包含填写说明，请确认是否从学生成品移除")
            suffix = observed_instruction["removed_text"].rstrip()
            source_title = section.title.rstrip()
            if not source_title.endswith(suffix):
                raise ProfileError("模板标题说明无法绑定到章节原文，请重新读取母版")
            section.title = source_title[:-len(suffix)].rstrip()
            instruction_removal = {**observed_instruction, "source_title": source_title,
                                   "source_path": evidence.path, "source_node_id": evidence.node_id}
        if section.type == SectionType.HEADING1:
            last_heading2 = None
        elif section.type == SectionType.HEADING2:
            last_heading2 = section
        region_nodes = [n for n in spec.nodes if n.region == evidence.region and n.role in {"body", "abstract", "english_abstract", "references", "acknowledgement", "appendix"} and n.text.strip()]
        if section.type.value.startswith("heading"):
            # Short lists and instructions often differ from regular prose.
            # A local override needs more than a lone example; explicit reviewed
            # overrides below still apply regardless of the sample count.
            prose = [n for n in region_nodes if len(n.text.strip()) >= 80
                     and not re.search(r"(?:list|列表)", n.details.get("style_name", ""), re.I)]
            region_nodes = prose if len(prose) >= 2 else []
        region_majority = _majority(region_nodes, ignore_instructional_red=True)
        properties = {**body_properties, **region_majority}
        if region_nodes and "font.color" not in region_majority:
            properties.pop("font.color", None)
        if inline:
            values = [r["properties"] for r in evidence.details.get("runs",[]) if r.get("text","").strip()]
            # Paragraph observations may contain the bold label's run properties.
            # Take character formatting from the content sample, not that aggregate.
            properties = {**{k: v for k, v in evidence.properties.items() if k.startswith("paragraph.")},
                          **(values[-1] if values else evidence.properties)}
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
                         "index": index, "properties": properties, "title_properties": title_properties,
                         **({"instruction_removal": instruction_removal} if instruction_removal else {})})
    retained_heading_indexes = {heading["index"] for heading in headings}
    if any(not numbering_uses[num_id] <= retained_heading_indexes for num_id in matched_automatic_ids):
        raise ProfileError("自动编号还用于未保留的段落，无法保证导出编号一致")
    if not headings:
        raise ProfileError("模板需要至少一个明确的正文或表单区域")
    if set(request.inline_label_overrides) - {h["key"] for h in headings if h["inline"]}:
        raise ProfileError("关键词标签无法绑定到已识别区域")
    # Resolve cover values without asking users to add programming placeholders.
    fields = []
    mapped_targets: dict[str, str] = {}
    aliases = {"title": r"(?:论文)?题目", "author": r"(?:学生)?姓名", "student_id": r"(?:学生)?学号",
               "college": "学院(?:[（(]系[）)])?", "department": "系别|系部", "major": "专业", "class_name": "班级", "advisor": "指导教师|导师", "school": "学校名称|学校", "date": "日期"}
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
            properties = {**properties, **_majority(peers, ignore_instructional_red=True)}
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
    profile = {"compiler_version": PROFILE_VERSION, "inline_content_format_version": "source-content-runs/1",
               "control_appearance":"hidden", "source_sha256": spec.source_sha256,
               "preserve_manuscript_structure": request.preserve_manuscript_structure,
               "specification_sha256": spec.specification_sha256, "confirmed_by": actor,
               "confirmed_at": datetime.now(timezone.utc).isoformat(), "headings": headings, "fields": fields,
               "cover_table_overrides": _cover_table_overrides(spec, request.overrides),
               "heading_semantic_corrections": semantic_corrections,
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
    entry_order = source_order
    if toc_active:
        from app.services.office_layout import _accepted_toc_anchor_parent
        anchor = toc_field_paragraphs[0]
        parent = anchor.getparent()
        # Direct body paragraphs stay on the original index. A Word automatic
        # directory is the only extra container: its rows live in w:sdtContent.
        if not _accepted_toc_anchor_parent(parent, body):
            raise ProfileError("目录域必须位于正文主流程，当前模板结构暂不支持")
        if parent is body:
            container_children = children
        else:
            container_children = list(parent)
            entry_order = {child.getroottree().getpath(child): index for index, child in enumerate(container_children)}
        toc_container_path = parent.getroottree().getpath(parent)
        row_indexes = [entry_order[n.path] for n in spec.nodes if n.part == "word/document.xml" and n.role == "toc_entry" and n.path in entry_order]
        anchor_index = entry_order.get(anchor.getroottree().getpath(anchor))
        if anchor_index is None:
            raise ProfileError("目录域段落无法定位到正文")
        first = min(row_indexes + [anchor_index]); last = max(row_indexes + [anchor_index])
        for child in container_children[first:last+1]:
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
                          key=lambda n: entry_order.get(n.path, 10**9))
    if any(n.path not in entry_order for n in toc_evidence):
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
        targets = [h for h in headings if h["kind"] != "toc" and
                   (heading_matches(label, h["title"]) or
                    (h.get("instruction_removal") and
                     heading_matches(label, h["instruction_removal"]["source_title"])))]
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
    header_cell_prefixes = _table_header_cell_prefixes(spec)
    for role, style_key in object_style_keys.items():
        specimens = [n for n in spec.nodes if n.role == role and n.part == "word/document.xml"
                     and not n.details.get("in_cover") and n.text.strip()]
        if role == "table_text" and header_cell_prefixes:
            body_cells = [n for n in specimens if not n.path.startswith(header_cell_prefixes)]
            if body_cells:
                specimens = body_cells
        configured = candidate.styles.get(style_key) or DEFAULT_STYLES[style_key]
        profile["object_properties"][role] = (
            _majority(specimens, ignore_instructional_red=True)
            if specimens else _configured_object_properties(configured, body_properties)
        )
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
    if request.cover_pagination is not None:
        first = min(headings, key=lambda item: item["index"])
        if not toc_active or first["kind"] == "toc" or first.get("inline"):
            raise ProfileError("封面分页检查需要可定位的首个正文标题和 Word 自动目录")
        profile["cover_pagination"] = {
            **request.cover_pagination.model_dump(mode="json"),
            "boundary_key": first["key"], "boundary_bookmark": first["bookmark_name"],
            "source": {"kind": "explicit_template_confirmation", "source_sha256": spec.source_sha256,
                       "specification_sha256": spec.specification_sha256},
        }
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
