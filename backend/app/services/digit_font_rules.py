"""Source-bound handling of the pilot's confirmed digit-font instruction."""
from __future__ import annotations

import copy
import re
import unicodedata

from lxml import etree

from app.core.extract.effective_format import EffectiveFormatResolver
from app.core.extract.xml_utils import NS, qn


DIGIT_FONT = "Times New Roman"
BODY_KINDS = {"heading1", "heading2", "heading3", "body"}
HEADING_KINDS = {"heading1", "heading2", "heading3"}
_RULE_TEXT = "各级标题、正文中的数字全部使用timesnewroman,字号与文字部分相同"


def is_digit_font_instruction(text: str) -> bool:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()
    return compact.rstrip(".。") == _RULE_TEXT


def review_digit_font_rules(specification) -> list[dict]:
    candidates = []
    nodes = {node.node_id: node for node in specification.nodes}
    for rule in specification.rules:
        if (rule.property != "instruction.text" or rule.evidence_kind != "explicit_instruction"
                or not is_digit_font_instruction(rule.expected)):
            continue
        node = next((nodes[node_id] for node_id in rule.evidence_node_ids if node_id in nodes), None)
        candidates.append({"rule_id": rule.id, "source_text": rule.expected,
                           "source_node_ids": list(rule.evidence_node_ids),
                           "source_path": node.path if node else "", "source_part": node.part if node else "",
                           "font": DIGIT_FONT})
    return candidates


def apply_digit_font(paragraph, font: str) -> None:
    """Split text without changing its other properties or non-text children."""
    for run in list(paragraph.iter(qn("w:r"))):
        if next(run.iterancestors(qn("w:p")), None) is not paragraph:
            continue
        if not any(re.search(r"[0-9]", child.text or "") for child in run if child.tag == qn("w:t")):
            continue
        parent = run.getparent()
        if parent is None:
            continue
        old_properties = run.find(qn("w:rPr"))
        replacements = []
        for item in run:
            if item.tag == qn("w:rPr"):
                continue
            fragments = re.split(r"([0-9]+)", item.text or "") if item.tag == qn("w:t") else [None]
            for fragment in fragments:
                if fragment == "":
                    continue
                replacement = etree.Element(qn("w:r"), attrib=dict(run.attrib))
                properties = copy.deepcopy(old_properties) if old_properties is not None else etree.Element(qn("w:rPr"))
                replacement.append(properties)
                element = copy.deepcopy(item)
                if fragment is not None:
                    element.text = fragment
                    if fragment[:1].isspace() or fragment[-1:].isspace():
                        element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                if fragment is not None and re.fullmatch(r"[0-9]+", fragment):
                    fonts = properties.find(qn("w:rFonts"))
                    if fonts is None:
                        fonts = etree.Element(qn("w:rFonts")); properties.insert(0, fonts)
                    for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
                        fonts.set(qn("w:" + slot), font)
                    for slot in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme", "hint"):
                        fonts.attrib.pop(qn("w:" + slot), None)
                replacement.append(element)
                replacements.append(replacement)
        position = parent.index(run)
        parent.remove(run)
        for offset, replacement in enumerate(replacements):
            parent.insert(position + offset, replacement)


def _digit_numbering_level(paragraph, numbering, resolver):
    """Resolve whether a heading's automatic label contains Arabic digits."""
    ppr = paragraph.find(qn("w:pPr"))
    style_node = ppr.find(qn("w:pStyle")) if ppr is not None else None
    style_id = style_node.get(qn("w:val"), "") if style_node is not None else resolver.default_style
    layers = [style.find(qn("w:pPr")) for style in resolver.chain(style_id)] + [ppr]
    if resolver.issues:
        return None, False, "标题编号样式继承无法解析"
    number_id = level_id = None
    has_numbering_properties = False
    for layer in layers:
        numpr = layer.find(qn("w:numPr")) if layer is not None else None
        if numpr is None:
            continue
        has_numbering_properties = True
        number = numpr.find(qn("w:numId"))
        level = numpr.find(qn("w:ilvl"))
        if number is not None:
            number_id = number.get(qn("w:val"))
        if level is not None:
            level_id = level.get(qn("w:val"))
    if number_id == "0" or (number_id is None and not has_numbering_properties):
        return None, False, ""
    if numbering is None or not str(number_id).isdigit() or level_id is None or not str(level_id).isdigit():
        return None, False, "标题自动编号缺少可核验的编号定义或层级"
    nums = numbering.xpath("./w:num[@w:numId=$value]", namespaces=NS, value=number_id)
    if len(nums) != 1:
        return None, False, "标题自动编号定义不唯一"
    num = nums[0]
    abstract_id = num.find(qn("w:abstractNumId"))
    abstracts = numbering.xpath("./w:abstractNum[@w:abstractNumId=$value]", namespaces=NS,
                                value=abstract_id.get(qn("w:val")) if abstract_id is not None else "")
    if len(abstracts) != 1 or abstracts[0].find(qn("w:numStyleLink")) is not None:
        return None, False, "标题自动编号的样式链接无法核验"
    abstract = abstracts[0]

    def level(index):
        overrides = num.xpath("./w:lvlOverride[@w:ilvl=$value]", namespaces=NS, value=index)
        if len(overrides) > 1:
            return None
        if overrides:
            entries = overrides[0].findall(qn("w:lvl"))
            if len(entries) > 1:
                return None
            if entries:
                return entries[0]
        entries = abstract.xpath("./w:lvl[@w:ilvl=$value]", namespaces=NS, value=index)
        return entries[0] if len(entries) == 1 else None

    current = level(str(level_id))
    text = current.find(qn("w:lvlText")) if current is not None else None
    if text is None:
        return None, False, "标题自动编号缺少可核验的显示格式"
    label = text.get(qn("w:val"), "")
    has_digits = bool(re.search(r"[0-9]", re.sub(r"%[1-9]", "", label)))
    for token in re.findall(r"%([1-9])", label):
        target = level(str(int(token) - 1))
        numfmt = target.find(qn("w:numFmt")) if target is not None else None
        kind = numfmt.get(qn("w:val"), "") if numfmt is not None else ""
        if kind in {"decimal", "decimalZero"}:
            has_digits = True
        elif kind not in {"upperRoman", "lowerRoman", "upperLetter", "lowerLetter", "bullet", "none"}:
            return None, False, "标题自动编号的数字类型无法核验"
    return current, has_digits, ""


def numbering_has_digits(paragraph, numbering, styles) -> bool:
    _, has_digits, error = _digit_numbering_level(paragraph, numbering, EffectiveFormatResolver(styles))
    if error:
        raise ValueError(error)
    return has_digits


def numbering_font_error(paragraph, numbering, styles, font: str) -> str:
    """Check numbering font and inherited size/decoration against heading text."""
    resolver = EffectiveFormatResolver(styles)
    current, has_digits, error = _digit_numbering_level(paragraph, numbering, resolver)
    if error:
        return error
    if not has_digits:
        return ""
    ppr = paragraph.find(qn("w:pPr"))
    fonts = current.find(f"{qn('w:rPr')}/{qn('w:rFonts')}")
    if (fonts is None or fonts.get(qn("w:ascii"), "").casefold() != font.casefold()
            or fonts.get(qn("w:asciiTheme")) or fonts.get(qn("w:hint"), "default") != "default"):
        return "标题自动编号的数字字体无法证明符合已确认规则"

    # Numbering properties augment the paragraph-mark format. Compare them
    # with the visible heading runs rather than assuming the list has a size.
    numbering_rpr = current.find(qn("w:rPr"))
    mark = ppr.find(qn("w:rPr")) if ppr is not None else None
    if any(rpr is not None and any(rpr.find(qn(f"w:{name}")) is not None
                                   for name in ("rStyle", "rtl", "cs", "szCs", "bCs", "iCs"))
           for rpr in (numbering_rpr, mark)):
        return "标题自动编号的字号或字形继承无法核验"
    mark_paragraph = etree.Element(qn("w:p"))
    if ppr is not None:
        mark_paragraph.append(copy.deepcopy(ppr))
    number_properties, _, _, _ = resolver.paragraph(mark_paragraph)
    resolver._run(number_properties, {}, numbering_rpr)
    _, _, runs, _ = resolver.paragraph(paragraph, include_whitespace=True)
    if resolver.issues.intersection({"style_inheritance_cycle", "missing_style_reference"}):
        return "标题编号样式继承无法解析"
    text_runs = [run for run in runs if any(char.isalpha() for char in run["text"])]
    if not text_runs:
        return "标题自动编号缺少可比较的标题文字"
    number_size = number_properties.get("font.size")
    if not number_size or any(not run["properties"].get("font.size") for run in text_runs):
        return "标题自动编号的字号继承无法核验"
    if any(run["properties"]["font.size"] != number_size for run in text_runs):
        return "标题自动编号字号与标题文字不一致"
    for key, label in (("font.bold", "粗体"), ("font.italic", "斜体")):
        if any(bool(run["properties"].get(key, False)) != bool(number_properties.get(key, False))
               for run in text_runs):
            return f"标题自动编号{label}与标题文字不一致"
    return ""
