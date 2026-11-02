"""Preserve and verify the source template's directory text appearance."""
from __future__ import annotations

import copy
import io
import re
import zipfile

from lxml import etree

from app.core.extract.xml_utils import load_part, qn
from app.services.source_template_profile import ProfileError

_UNDERLINES = {
    "none", "words", "single", "double", "thick", "dotted", "dottedHeavy",
    "dash", "dashedHeavy", "dashLong", "dashLongHeavy", "dotDash",
    "dashDotHeavy", "dotDotDash", "dashDotDotHeavy", "wave", "wavyHeavy", "wavyDouble",
}
_RUN_PROPERTY_ORDER = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
    "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid",
    "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs",
    "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs",
    "em", "lang", "eastAsianLayout", "specVanish", "oMath", "rPrChange",
]
_ORDER = {qn("w:" + name): i for i, name in enumerate(_RUN_PROPERTY_ORDER)}
_TOGGLES = {
    "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
    "outline", "shadow", "emboss", "imprint", "vanish", "specVanish",
}
_TOGGLE_TAGS = {qn("w:" + name) for name in _TOGGLES}
_FONT_PAIRS = [(qn("w:" + slot), qn("w:" + theme)) for slot, theme in (
    ("ascii", "asciiTheme"), ("hAnsi", "hAnsiTheme"),
    ("eastAsia", "eastAsiaTheme"), ("cs", "cstheme"),
)]


def _styles(styles_root):
    result = {}
    if styles_root is not None:
        for item in styles_root.findall(qn("w:style")):
            style_id = item.get(qn("w:styleId"), "")
            if style_id in result:
                raise ProfileError("母版含重复的目录样式标识")
            result[style_id] = item
    return result


def _style_nodes(styles, style_id):
    chain, seen = [], set()
    while style_id:
        if style_id in seen or len(seen) >= 128 or style_id not in styles:
            raise ProfileError("目录文字样式的继承关系无法确定")
        seen.add(style_id)
        style = styles[style_id]
        chain.append(style)
        parent = style.find(qn("w:basedOn"))
        style_id = parent.get(qn("w:val"), "") if parent is not None else ""
    return list(reversed(chain))


def _chain(styles, style_id):
    return [style.find(qn("w:rPr")) for style in _style_nodes(styles, style_id)]


def _layer(appearance, rpr):
    if rpr is None:
        return
    colors = rpr.findall(qn("w:color"))
    if colors:
        color = colors[0]
        value = color.get(qn("w:val"), "")
        appearance["color"] = (value.lower() if value.lower() == "auto" else value.upper()) if (
            len(colors) == 1 and set(color.attrib) == {qn("w:val")} and len(color) == 0
            and not (color.text or "").strip() and re.fullmatch(r"auto|[0-9a-fA-F]{6}", value, re.I)
        ) else None
    underlines = rpr.findall(qn("w:u"))
    if underlines:
        underline = underlines[0]
        value = underline.get(qn("w:val"), "single")
        appearance["underline"] = value if (
            len(underlines) == 1 and set(underline.attrib).issubset({qn("w:val")})
            and len(underline) == 0 and not (underline.text or "").strip() and value in _UNDERLINES
        ) else None


def _visible_runs(paragraph, *, include_whitespace=False):
    for run in paragraph.iter(qn("w:r")):
        parent = run.getparent()
        while parent is not None and parent.tag != qn("w:p"):
            parent = parent.getparent()
        if parent is not paragraph:
            continue
        text = "".join((node.text or "") for node in run.iter(qn("w:t")))
        if text.strip() or (include_whitespace and text) or run.find(qn("w:tab")) is not None or "\t" in text:
            yield run, text


def paragraph_appearances(paragraph, styles_root, *, include_whitespace=False):
    """Resolve foreground/underline for visible runs, including character styles."""
    styles = _styles(styles_root)
    defaults = styles_root.find(qn("w:docDefaults")) if styles_root is not None else None
    base = {"color": "auto", "underline": "none"}
    _layer(base, defaults.find(qn("w:rPrDefault") + "/" + qn("w:rPr")) if defaults is not None else None)
    pstyle = paragraph.find(qn("w:pPr") + "/" + qn("w:pStyle"))
    style_id = pstyle.get(qn("w:val"), "") if pstyle is not None else next((
        key for key, style in styles.items() if style.get(qn("w:type")) == "paragraph"
        and style.get(qn("w:default"), "").lower() in {"1", "true", "on"}
    ), "")
    for rpr in _chain(styles, style_id):
        _layer(base, rpr)
    runs = []
    for run, text in _visible_runs(paragraph, include_whitespace=include_whitespace):
        rprs = run.findall(qn("w:rPr"))
        if len(rprs) > 1:
            raise ProfileError("目录文字含有重复的格式属性")
        rpr = rprs[0] if rprs else None
        values = dict(base)
        rstyle = rpr.find(qn("w:rStyle")) if rpr is not None else None
        if rstyle is not None:
            for layer in _chain(styles, rstyle.get(qn("w:val"), "")):
                _layer(values, layer)
        _layer(values, rpr)
        if None in values.values():
            raise ProfileError("目录文字颜色或下划线无法可靠解析")
        runs.append((run, text, values))
    return runs


def _split(runs):
    text_runs = [item for item in runs if item[1].strip()]
    if not text_runs:
        return {"label": [], "leader": [], "page": []}
    # A source sample may put both title and page in one run. Its appearance
    # then applies to both. A generated page is already a separate run.
    last = text_runs[-1]
    prefix = re.sub(r"[0-9ivxlcdmIVXLCDM]+\s*$", "", last[1].strip()).strip()
    last_index = runs.index(last)
    tab_indexes = [i for i, item in enumerate(runs[:last_index])
                   if item[0].find(qn("w:tab")) is not None or "\t" in item[1]]
    last_tab = tab_indexes[-1] if tab_indexes else -1
    label = [item for i, item in enumerate(runs)
             if item[1] and (i < last_index and (item[1].strip() or i <= last_tab or last_tab < 0)
                             or i == last_index and (prefix or len(text_runs) == 1))]
    page = [item for i, item in enumerate(runs)
            if item[1] and (i == last_index or i > last_index
                            or i > last_tab >= 0 and i < last_index and not item[1].strip())]
    return {"label": label,
            "leader": [item for item in runs if item[0].find(qn("w:tab")) is not None or "\t" in item[1]],
            "page": page}


def source_toc_appearance(source, profile):
    """Read per-level title and page appearance from the authenticated source."""
    if not profile.get("toc_active"):
        return {}
    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            root, styles = load_part(archive, "word/document.xml"), load_part(archive, "word/styles.xml")
    except (ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise ProfileError("无法读取母版目录外观") from exc
    body = root.find(qn("w:body")) if root is not None else None
    if body is None or styles is None:
        raise ProfileError("母版目录或样式缺失")
    levels = {str(style): str(level) for level, style in profile.get("toc_style_by_level", {}).items()}
    observed = {level: {"label": set(), "leader": set(), "page": set()} for level in levels.values()}
    for paragraph in body.findall(qn("w:p")):
        style = paragraph.find(qn("w:pPr") + "/" + qn("w:pStyle"))
        level = levels.get(style.get(qn("w:val"), "")) if style is not None else None
        if level is None:
            continue
        groups = _split(paragraph_appearances(paragraph, styles))
        if not groups["label"] or not groups["page"]:
            continue
        for role, runs in groups.items():
            observed[level][role].update((v["color"], v["underline"]) for _, _, v in runs)
    result = {}
    for level, groups in observed.items():
        result[level] = {}
        # If the master has no explicit tab, new leaders use the label format.
        if not groups["leader"]:
            groups["leader"] = set(groups["label"])
        for role, values in groups.items():
            if len(values) != 1:
                raise ProfileError("母版同级目录文字颜色或下划线缺少一致样例")
            color, underline = next(iter(values))
            result[level][role] = {"color": color, "underline": underline}
    if not result:
        raise ProfileError("母版缺少可验证的目录文字样式")
    return result


def _set_property(rpr, tag, value):
    for node in rpr.findall(qn("w:" + tag)):
        rpr.remove(node)
    new = etree.Element(qn("w:" + tag))
    new.set(qn("w:val"), value)
    rank = _ORDER[qn("w:" + tag)]
    index = next((i for i, child in enumerate(rpr) if _ORDER.get(child.tag, 10_000) > rank), len(rpr))
    rpr.insert(index, new)


def _put_property(rpr, element):
    for old in rpr.findall(element.tag):
        rpr.remove(old)
    rank = _ORDER.get(element.tag, 10_000)
    index = next((i for i, child in enumerate(rpr) if _ORDER.get(child.tag, 10_000) > rank), len(rpr))
    rpr.insert(index, element)


def _on_off(element):
    value = element.get(qn("w:val"), "1").lower()
    if value in {"1", "true", "on"}:
        return True
    if value in {"0", "false", "off"}:
        return False
    raise ProfileError("目录字符样式的开关属性无法确定")


def _merge_attributes(base, later):
    merged = copy.deepcopy(base) if base is not None else etree.Element(later.tag)
    if later.tag == qn("w:rFonts"):
        # A later literal font overrides an earlier theme font and vice versa.
        for literal, theme in _FONT_PAIRS:
            if literal in later.attrib or theme in later.attrib:
                merged.attrib.pop(literal, None)
                merged.attrib.pop(theme, None)
    merged.attrib.update(later.attrib)
    return merged


def _base_toggle(paragraph, styles_root, styles, tag):
    defaults = styles_root.find(qn("w:docDefaults")) if styles_root is not None else None
    rpr = defaults.find(qn("w:rPrDefault") + "/" + qn("w:rPr")) if defaults is not None else None
    prop = rpr.find(tag) if rpr is not None else None
    result = _on_off(prop) if prop is not None else False
    pstyle = paragraph.find(qn("w:pPr") + "/" + qn("w:pStyle"))
    style_id = pstyle.get(qn("w:val"), "") if pstyle is not None else next((
        key for key, style in styles.items() if style.get(qn("w:type")) == "paragraph"
        and style.get(qn("w:default"), "").lower() in {"1", "true", "on"}
    ), "")
    for layer in _chain(styles, style_id):
        prop = layer.find(tag) if layer is not None else None
        if prop is not None and _on_off(prop):
            result = not result
    return result


def _flatten_character_style(run, paragraph, styles_root, styles):
    """Keep character formatting without retaining Word's hyperlink style.

    Some Office renderers apply a link style to the whole link when *any* run
    retains a character style, even when its direct color is ``auto``. Copy
    its non-appearance properties to this TOC run instead of editing the
    document-wide Hyperlink style (which may also be used in the body).
    """
    rpr = run.find(qn("w:rPr"))
    reference = rpr.find(qn("w:rStyle")) if rpr is not None else None
    if reference is None:
        return
    style_id = reference.get(qn("w:val"), "")
    if not style_id:
        raise ProfileError("目录字符样式缺少标识")
    chain = _style_nodes(styles, style_id)
    if any(style.get(qn("w:type")) != "character" for style in chain):
        raise ProfileError("目录字符样式的继承类型不受支持")
    properties, toggles = {}, {}
    for style in chain:
        layer = style.find(qn("w:rPr"))
        if layer is None:
            continue
        seen = set()
        for prop in layer:
            if prop.tag in seen:
                raise ProfileError("目录字符样式含重复属性")
            seen.add(prop.tag)
            if prop.tag in {qn("w:color"), qn("w:u")}:
                continue
            if prop.tag in _TOGGLE_TAGS:
                if _on_off(prop):
                    toggles[prop.tag] = not toggles.get(prop.tag, False)
            elif prop.tag in {qn("w:rFonts"), qn("w:lang")}:
                properties[prop.tag] = _merge_attributes(properties.get(prop.tag), prop)
            else:
                properties[prop.tag] = copy.deepcopy(prop)
    rpr.remove(reference)
    for tag, prop in properties.items():
        existing = rpr.findall(tag)
        if len(existing) > 1:
            raise ProfileError("目录文字含重复的格式属性")
        if existing and tag in {qn("w:rFonts"), qn("w:lang")}:
            _put_property(rpr, _merge_attributes(prop, existing[0]))
        elif not existing:
            _put_property(rpr, prop)
    for tag, changed in toggles.items():
        if changed and rpr.find(tag) is None:
            prop = etree.Element(tag)
            prop.set(qn("w:val"), "0" if _base_toggle(paragraph, styles_root, styles, tag) else "1")
            _put_property(rpr, prop)


def apply_toc_appearance(paragraph, expected, styles_root):
    groups = _split([(run, text, None) for run, text in _visible_runs(paragraph, include_whitespace=True)])
    if not groups["label"] or not groups["page"] or not isinstance(expected, dict):
        raise ProfileError("目录文字缺少母版外观规则")
    styles = _styles(styles_root)
    assigned = {}
    for role, runs in groups.items():
        rule = expected.get(role, {})
        if (not isinstance(rule, dict) or not re.fullmatch(r"auto|[0-9A-F]{6}", str(rule.get("color", "")))
                or rule.get("underline") not in _UNDERLINES):
            raise ProfileError("母版目录外观规则无效")
        for run, _, _ in runs:
            if run in assigned and assigned[run] != rule:
                raise ProfileError("目录文字和点线的格式无法分别设置")
            assigned[run] = rule
            rprs = run.findall(qn("w:rPr"))
            if len(rprs) > 1:
                raise ProfileError("目录文字含有重复的格式属性")
            rpr = rprs[0] if rprs else None
            if rpr is None:
                rpr = etree.Element(qn("w:rPr"))
                run.insert(0, rpr)
            _flatten_character_style(run, paragraph, styles_root, styles)
            _set_property(rpr, "color", rule["color"])
            _set_property(rpr, "u", rule["underline"])


def toc_appearance_matches(paragraph, styles_root, expected):
    try:
        groups = _split(paragraph_appearances(paragraph, styles_root, include_whitespace=True))
        return bool(groups["label"] and groups["page"] and isinstance(expected, dict) and all(
            isinstance(expected.get(role), dict) and values == expected[role]
            and run.find(qn("w:rPr") + "/" + qn("w:rStyle")) is None
            for role, runs in groups.items() for run, _, values in runs
        ))
    except (ProfileError, TypeError, KeyError):
        return False
