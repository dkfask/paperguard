"""Inspect emitted master-filled DOCX independently of its content renderer."""
from __future__ import annotations

import copy
import hashlib
import io
import math
import posixpath
import re
import zipfile
from collections import Counter, defaultdict

from lxml import etree

from app.core.extract.effective_format import EffectiveFormatResolver
from app.core.extract.xml_utils import NS, load_part, qn
from app.models.project import BlockType
from app.services.document_assets import resolve_document_image_path
from app.services.digit_font_rules import BODY_KINDS, HEADING_KINDS, numbering_font_error
from app.services.master_layout_anchors import block_bookmark_name
from app.services.references import (ReferenceInlineMappingError, reference_block_text, reference_inline_runs,
                                     reference_number_for, uses_sequential_reference_numbering)
from app.services.source_template_profile import verify_profile, digest, commentless_source, package_manifest

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"

_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD_META_NS = {
    "http://schemas.microsoft.com/office/word/2010/wordml",
    "http://schemas.microsoft.com/office/word/2012/wordml",
    "http://schemas.microsoft.com/office/word/2015/wordml",
    "http://schemas.microsoft.com/office/word/2016/wordml",
    "http://schemas.microsoft.com/office/word/2018/wordml",
    "http://schemas.microsoft.com/office/word/2020/wordml",
    "http://schemas.microsoft.com/office/word/2023/wordml",
    "http://schemas.microsoft.com/office/word/2024/wordml",
}


_INVALID_VISIBILITY = object()


def _visibility_property(properties, name):
    """Read an OOXML on/off value; malformed or repeated values are unknown."""
    if properties is None:
        return None
    values = properties.findall(qn("w:" + name))
    if not values:
        return None
    if len(values) != 1 or len(values[0]) or (values[0].text or "").strip():
        return _INVALID_VISIBILITY
    value = values[0].get(qn("w:val"))
    if value is None or value in {"1", "true", "on"}:
        return True
    if value in {"0", "false", "off"}:
        return False
    return _INVALID_VISIBILITY


def _hidden_in_run(run, styles, name):
    """Resolve text visibility, returning unknown rather than guessing styles.

    A direct off value overrides the entire cascade. Otherwise, resolve document
    defaults and table, paragraph and character styles. Conditional table
    styles with visibility rules require a direct override because this
    checker does not resolve their applicability to individual cells.
    """
    properties = run.findall(qn("w:rPr"))
    if len(properties) > 1:
        return _INVALID_VISIBILITY
    properties = properties[0] if properties else None
    direct = _visibility_property(properties, name)
    if direct is not None:
        return direct
    if styles is None:
        return _INVALID_VISIBILITY
    paragraph = next((item for item in run.iterancestors() if item.tag == qn("w:p")), None)
    if paragraph is None:
        return _INVALID_VISIBILITY

    def one(parent, tag):
        if parent is None:
            return None
        matches = parent.findall(qn("w:" + tag))
        return matches[0] if len(matches) == 1 else _INVALID_VISIBILITY if matches else None

    def style_chain(style_id, kind):
        result, seen = [], set()
        while style_id:
            if style_id in seen or len(seen) >= 128:
                return None
            seen.add(style_id)
            matches = styles.xpath("./w:style[@w:styleId=$ident]", namespaces=NS, ident=style_id)
            if len(matches) != 1 or matches[0].get(qn("w:type")) != kind:
                return None
            style = matches[0]
            result.append(style)
            base = one(style, "basedOn")
            if base is _INVALID_VISIBILITY:
                return None
            style_id = base.get(qn("w:val"), "") if base is not None else ""
            if base is not None and not style_id:
                return None
        return list(reversed(result))

    def default_style(kind):
        candidates = [style for style in styles.findall(qn("w:style")) if style.get(qn("w:type")) == kind]
        if any(style.get(qn("w:default")) not in {None, "0", "false", "off", "1", "true", "on"}
               for style in candidates):
            return None
        found = [style.get(qn("w:styleId"), "") for style in candidates
                 if style.get(qn("w:default"), "") in {"1", "true", "on"}]
        return found[0] if len(found) == 1 and found[0] else "" if not found else None

    defaults = one(styles, "docDefaults")
    if defaults is _INVALID_VISIBILITY:
        return _INVALID_VISIBILITY
    run_defaults = one(defaults, "rPrDefault")
    if run_defaults is _INVALID_VISIBILITY:
        return _INVALID_VISIBILITY
    run_defaults = one(run_defaults, "rPr")
    if run_defaults is _INVALID_VISIBILITY:
        return _INVALID_VISIBILITY
    initial = _visibility_property(run_defaults, name)
    if initial is _INVALID_VISIBILITY:
        return initial
    hidden = initial is True

    layers = []
    table = next((item for item in paragraph.iterancestors() if item.tag == qn("w:tbl")), None)
    if table is not None:
        table_properties = one(table, "tblPr")
        if table_properties is _INVALID_VISIBILITY:
            return _INVALID_VISIBILITY
        table_style = one(table_properties, "tblStyle")
        if table_style is _INVALID_VISIBILITY:
            return _INVALID_VISIBILITY
        table_style_id = table_style.get(qn("w:val"), "") if table_style is not None else default_style("table")
        if table_style_id is None or (table_style is not None and not table_style_id):
            return _INVALID_VISIBILITY
        table_chain = style_chain(table_style_id, "table")
        if table_chain is None:
            return _INVALID_VISIBILITY
        if any(style.xpath("./w:tblStylePr//w:rPr/w:" + name, namespaces=NS) for style in table_chain):
            return _INVALID_VISIBILITY
        layers.extend(table_chain)

    ppr = one(paragraph, "pPr")
    if ppr is _INVALID_VISIBILITY:
        return _INVALID_VISIBILITY
    pstyle = one(ppr, "pStyle")
    if pstyle is _INVALID_VISIBILITY:
        return _INVALID_VISIBILITY
    paragraph_style_id = pstyle.get(qn("w:val"), "") if pstyle is not None else default_style("paragraph")
    if paragraph_style_id is None or (pstyle is not None and not paragraph_style_id):
        return _INVALID_VISIBILITY
    paragraph_chain = style_chain(paragraph_style_id, "paragraph")
    if paragraph_chain is None:
        return _INVALID_VISIBILITY
    layers.extend(paragraph_chain)

    character_style = one(properties, "rStyle")
    if character_style is _INVALID_VISIBILITY:
        return _INVALID_VISIBILITY
    if character_style is not None:
        character_id = character_style.get(qn("w:val"), "")
        character_chain = style_chain(character_id, "character") if character_id else None
        if character_chain is None:
            return _INVALID_VISIBILITY
        layers.extend(character_chain)

    for style in layers:
        style_properties = one(style, "rPr")
        if style_properties is _INVALID_VISIBILITY:
            return _INVALID_VISIBILITY
        value = _visibility_property(style_properties, name)
        if value is _INVALID_VISIBILITY:
            return value
        if value is not None:
            # vanish is a style toggle; webHidden is an ordinary on/off value.
            hidden = (not hidden if value else hidden) if name == "vanish" else value
    return hidden


def _run_content_visible(run, styles):
    return all(_hidden_in_run(run, styles, name) is False for name in ("vanish", "webHidden"))


def _generated_content_visible(content, styles):
    return all(_run_content_visible(run, styles) for run in content.iter(qn("w:r"))
               if any(run.find(qn("w:" + tag)) is not None for tag in ("t", "drawing", "pict")))


# Word's named highlighting palette (ST_HighlightColor).  This check only
# proves that a known foreground is not identical to the highlight; it does
# not claim a contrast ratio or account for every inherited background.
_HIGHLIGHT_RGB = {
    "black": "000000", "blue": "0000FF", "cyan": "00FFFF",
    "green": "00FF00", "magenta": "FF00FF", "red": "FF0000",
    "yellow": "FFFF00", "white": "FFFFFF", "darkblue": "000080",
    "darkcyan": "008080", "darkgreen": "008000", "darkmagenta": "800080",
    "darkred": "800000", "darkyellow": "808000", "darkgray": "808080",
    "lightgray": "C0C0C0",
}


def _known_white_document_background(root):
    background = root.find(qn("w:background"))
    if background is None:
        return True
    return (not len(background) and set(background.attrib) == {qn("w:color")}
            and background.get(qn("w:color"), "").upper() == "FFFFFF")


def _text_color_is_visible(node, effective, *, white_document):
    """Reject or mark unprovable text colors that can disappear on the page."""
    rprs = node.findall(qn("w:rPr"))
    if len(rprs) > 1:
        return None
    colors = rprs[0].findall(qn("w:color")) if rprs else []
    if len(colors) > 1:
        return None
    if colors:
        color = colors[0]
        if (set(color.attrib) != {qn("w:val")} or len(color)
                or (color.text or "").strip()):
            return None
        foreground = color.get(qn("w:val"), "").lower()
    else:
        foreground = effective.get("font.color")
    if foreground is None or foreground == "auto":
        return True if white_document else None
    if not isinstance(foreground, str) or not re.fullmatch(r"[0-9a-fA-F]{6}", foreground):
        return None
    if not white_document:
        return None
    if foreground.upper() != "FFFFFF":
        return True
    highlight = effective.get("font.highlight")
    if highlight is None or isinstance(highlight, str) and highlight.lower() == "none":
        return False
    if not isinstance(highlight, str):
        return None
    highlight_rgb = _HIGHLIGHT_RGB.get(highlight.lower())
    return None if highlight_rgb is None else highlight_rgb != "FFFFFF"


def _highlight_is_distinct_from_text(node, effective, *, white_document):
    """Check a single effective run; unknown colors fail instead of guessing."""
    rprs = node.findall(qn("w:rPr"))
    if len(rprs) > 1:
        return False
    rpr = rprs[0] if rprs else None
    highlights = rpr.findall(qn("w:highlight")) if rpr is not None else []
    colors = rpr.findall(qn("w:color")) if rpr is not None else []
    if len(highlights) > 1 or len(colors) > 1:
        return False
    highlight = effective.get("font.highlight")
    if highlight is None or isinstance(highlight, str) and highlight.lower() == "none":
        return True
    if not isinstance(highlight, str):
        return False
    background = _HIGHLIGHT_RGB.get(highlight.lower())
    if background is None:
        return False
    # A direct 'auto' resets inherited color. On a plain white document Word
    # renders automatic text black even when its highlight is black.
    if colors:
        color = colors[0]
        if set(color.attrib) != {qn("w:val")} or len(color) or (color.text or "").strip():
            return False
        foreground = color.get(qn("w:val"), "")
    else:
        foreground = effective.get("font.color")
    if foreground is None or isinstance(foreground, str) and foreground.lower() == "auto":
        if not white_document:
            return False
        foreground = "000000"
    if not isinstance(foreground, str) or not re.fullmatch(r"[0-9a-fA-F]{6}", foreground):
        return False
    return foreground.upper() != background


def _generated_content_has_no_direct_shading(content):
    """Generated blocks cannot add shading that the renderer does not model.

    Source-master shading outside the content control remains protected by the
    static structure check. Inherited shading and highlight contrast need
    separate evidence and are not established by this check.
    """
    return next(content.iter(qn("w:shd")), None) is None


def _previous_table_cell(cell):
    if cell is None or cell.getparent() is None or cell.getparent().tag != qn("w:tr"):
        return None
    cells = cell.getparent().findall(qn("w:tc"))
    index = cells.index(cell)
    return cells[index - 1] if index else None


def _cover_label_visible(source_root, field, output_content, styles):
    """Check a mapped table label without inspecting unrelated cover content.

    The profile compiler maps a table value to the cell immediately following
    the source label, but its field path points to the value paragraph. Use
    the frozen source to distinguish that case from inline or standalone fields.
    Text boxes are excluded just as they are when the source label is mapped.
    """
    namespaces = {key: value for element in source_root.iter()
                  for key, value in element.nsmap.items() if key}
    targets = source_root.xpath(field["path"], namespaces=namespaces)
    if len(targets) != 1:
        return False
    source_cell = next((node for node in targets[0].iterancestors() if node.tag == qn("w:tc")), None)
    output_cell = next((node for node in output_content.iterancestors() if node.tag == qn("w:tc")), None)
    if source_cell is None:
        return output_cell is None
    source_label = _previous_table_cell(source_cell)
    output_label = _previous_table_cell(output_cell)
    if source_label is None or output_label is None:
        return False
    source_text = "".join(source_label.xpath(
        ".//w:t[not(ancestor::w:txbxContent)]/text()", namespaces=NS))
    output_text = "".join(output_label.xpath(
        ".//w:t[not(ancestor::w:txbxContent)]/text()", namespaces=NS))
    runs = output_label.xpath(
        ".//w:r[not(ancestor::w:txbxContent) and w:t[normalize-space(.)]]", namespaces=NS)
    return bool(source_text.strip() and source_text == output_text and runs
                and all(_run_content_visible(run, styles) for run in runs))


def _one_picture_fill(content):
    """Locate exactly one generated picture fill in a dynamic image block."""
    fills = content.findall(f".//{{{PIC}}}blipFill")
    return fills[0] if len(fills) == 1 else None


def _identity_picture_rectangle(rectangle) -> bool:
    """Accept absent or explicit zero offsets; reject ambiguous XML values."""
    if len(rectangle) or (rectangle.text or "").strip():
        return False
    if any(name not in {"l", "t", "r", "b"} for name in rectangle.attrib):
        return False
    return all(re.fullmatch(r"[+-]?0+(?:\.0+)?%?", value) is not None
               for value in rectangle.attrib.values())


def _picture_source_is_uncropped(content) -> bool:
    """Verify source-rectangle settings, not rendered visibility or occlusion."""
    fill = _one_picture_fill(content)
    if fill is None:
        return False
    direct = fill.findall(f"{{{A}}}srcRect")
    all_rectangles = content.findall(f".//{{{A}}}srcRect")
    return (len(direct) <= 1 and len(all_rectangles) == len(direct)
            and all(_identity_picture_rectangle(rectangle) for rectangle in direct))


def _picture_fill_is_unmodified(content) -> bool:
    """Check stretch/fill-rectangle settings for renderer-generated pictures."""
    fill = _one_picture_fill(content)
    if fill is None or content.findall(f".//{{{A}}}tile"):
        return False
    stretches = fill.findall(f"{{{A}}}stretch")
    if len(stretches) != 1 or len(content.findall(f".//{{{A}}}stretch")) != 1:
        return False
    stretch = stretches[0]
    rectangles = stretch.findall(f"{{{A}}}fillRect")
    if (stretch.attrib or (stretch.text or "").strip() or len(rectangles) > 1
            or len(content.findall(f".//{{{A}}}fillRect")) != len(rectangles)
            or any(child.tag != f"{{{A}}}fillRect" for child in stretch)):
        return False
    return all(_identity_picture_rectangle(rectangle) for rectangle in rectangles)


def _picture_shape_matches_inline_extent(content) -> bool:
    """Require the generated image's inner geometry to match its inline frame.

    This structural check does not prove that Word rendered every pixel or that
    another object did not cover the picture.
    """
    inlines = content.findall(f".//{{{WP}}}inline")
    if len(inlines) != 1 or content.findall(f".//{{{WP}}}anchor"):
        return False
    inline = inlines[0]
    pictures = inline.findall(f".//{{{PIC}}}pic")
    outer_extents = inline.findall(f"{{{WP}}}extent")
    if len(pictures) != 1 or len(outer_extents) != 1:
        return False
    properties = pictures[0].findall(f"{{{PIC}}}spPr")
    if len(properties) != 1:
        return False
    transforms = properties[0].findall(f"{{{A}}}xfrm")
    geometries = properties[0].findall(f"{{{A}}}prstGeom")
    if (len(transforms) != 1 or len(geometries) != 1
            or properties[0].findall(f"{{{A}}}custGeom")):
        return False
    transform, geometry = transforms[0], geometries[0]
    if any(name not in {"rot", "flipH", "flipV"} for name in transform.attrib):
        return False
    if re.fullmatch(r"[+-]?0+", transform.get("rot", "0")) is None:
        return False
    if any(transform.get(name, "0") not in {"0", "false"} for name in ("flipH", "flipV")):
        return False
    offsets = transform.findall(f"{{{A}}}off")
    inner_extents = transform.findall(f"{{{A}}}ext")
    if (len(offsets) != 1 or len(inner_extents) != 1 or len(transform) != 2
            or len(offsets[0]) or (offsets[0].text or "").strip()
            or set(offsets[0].attrib) != {"x", "y"}
            or any(re.fullmatch(r"[+-]?0+", offsets[0].get(name, "")) is None for name in ("x", "y"))):
        return False
    outer, inner = outer_extents[0], inner_extents[0]
    if (set(outer.attrib) != {"cx", "cy"} or set(inner.attrib) != {"cx", "cy"}
            or len(outer) or len(inner) or (outer.text or "").strip() or (inner.text or "").strip()):
        return False
    for axis in ("cx", "cy"):
        values = [outer.get(axis, ""), inner.get(axis, "")]
        if any(not value.isascii() or not value.isdecimal() or len(value) > 18 for value in values):
            return False
        if int(values[0]) <= 0 or int(values[0]) != int(values[1]):
            return False
    if (geometry.get("prst") != "rect" or set(geometry.attrib) != {"prst"}
            or len(geometry) > 1 or (geometry.text or "").strip()):
        return False
    return all(child.tag == f"{{{A}}}avLst" and not len(child)
               and not child.attrib and not (child.text or "").strip() for child in geometry)


def _picture_has_no_unverified_effects(content) -> bool:
    """Reject unsupported drawing effects that can alter or hide a picture.

    Transparency already present in the source image is part of the verified
    asset; this check concerns additional OOXML drawing effects only.
    """
    fill = _one_picture_fill(content)
    if fill is None:
        return False
    blips = fill.findall(f"{{{A}}}blip")
    if len(blips) != 1 or len(content.findall(f".//{{{A}}}blip")) != 1:
        return False
    blip = blips[0]
    if (len(blip) or (blip.text or "").strip()
            or set(blip.attrib) - {qn("r:embed"), "cstate"}
            or not blip.get(qn("r:embed"))
            or blip.get("cstate", "none") not in {"none", "email", "screen", "print", "hqprint"}):
        return False
    shapes = content.findall(f".//{{{PIC}}}spPr")
    if len(shapes) != 1:
        return False
    shape = shapes[0]
    allowed = {f"{{{A}}}xfrm", f"{{{A}}}prstGeom", f"{{{A}}}effectLst"}
    if any(child.tag not in allowed for child in shape):
        return False
    effects = shape.findall(f"{{{A}}}effectLst")
    return (len(effects) <= 1 and all(not len(effect) and not effect.attrib
            and not (effect.text or "").strip() for effect in effects))


def _picture_line_allows_growth(paragraph, styles) -> bool:
    """Check line-height safety without claiming rendered picture visibility.

    A complete direct line rule takes precedence. Older outputs may instead
    inherit their line rule from the paragraph style; resolve that only when
    the style chain and the relevant spacing values are unambiguous.
    """
    if paragraph is None or paragraph.tag != qn("w:p"):
        return False
    pprs = paragraph.findall(qn("w:pPr"))
    if len(pprs) > 1:
        return False
    invalid = object()

    def spacing_value(properties):
        if properties is None:
            return None
        nodes = properties.findall(qn("w:spacing"))
        if len(nodes) > 1:
            return invalid
        if not nodes:
            return None
        rule = nodes[0].get(qn("w:lineRule"))
        line = nodes[0].get(qn("w:line"))
        if rule is None and line is None:
            return None
        if (rule not in {"auto", "atLeast", "exact"} or line is None
                or len(line) > 10 or re.fullmatch(r"[0-9]+", line) is None
                or int(line) > 2_147_483_647):
            return invalid
        return rule, int(line)

    def grows(value):
        return (value[0] == "atLeast" and value[1] > 0
                or value[0] == "auto" and value[1] >= 240)

    ppr = pprs[0] if pprs else None
    direct = spacing_value(ppr)
    if direct is invalid:
        return False
    if direct is not None:
        return grows(direct)
    if paragraph.xpath("ancestor::w:tbl", namespaces=NS):
        # Conditional table styles are not resolved by this check.
        return False
    if styles is None:
        return ppr is None or not ppr.findall(qn("w:pStyle"))
    if styles.tag != qn("w:styles"):
        return False
    all_styles = styles.findall(qn("w:style"))
    ids = [style.get(qn("w:styleId")) for style in all_styles]
    if any(not style_id for style_id in ids) or len(ids) != len(set(ids)):
        return False
    paragraph_styles = [style for style in all_styles
                        if style.get(qn("w:type")) == "paragraph"]
    defaults = [style for style in paragraph_styles
                if style.get(qn("w:default"), "").lower() in {"1", "true", "on"}]
    if len(defaults) > 1:
        return False
    style_nodes = ppr.findall(qn("w:pStyle")) if ppr is not None else []
    if len(style_nodes) > 1:
        return False
    style_id = style_nodes[0].get(qn("w:val"), "") if style_nodes else (
        defaults[0].get(qn("w:styleId"), "") if defaults else "")
    if style_nodes and not style_id:
        return False
    resolver = EffectiveFormatResolver(styles)
    chain = resolver.chain(style_id)
    if resolver.issues or any(style.get(qn("w:type")) != "paragraph" for style in chain):
        return False
    document_defaults = styles.findall(qn("w:docDefaults"))
    if len(document_defaults) > 1:
        return False
    default_ppr = []
    if document_defaults:
        defaults_nodes = document_defaults[0].findall(qn("w:pPrDefault"))
        if len(defaults_nodes) > 1:
            return False
        if defaults_nodes:
            default_ppr = defaults_nodes[0].findall(qn("w:pPr"))
    if len(default_ppr) > 1:
        return False
    layers = [default_ppr[0] if default_ppr else None]
    for style in chain:
        based_on = style.findall(qn("w:basedOn"))
        properties = style.findall(qn("w:pPr"))
        if (len(based_on) > 1 or len(properties) > 1
                or (based_on and not based_on[0].get(qn("w:val")))):
            return False
        layers.append(properties[0] if properties else None)
    inherited = None
    for layer in layers:
        value = spacing_value(layer)
        if value is invalid:
            return False
        if value is not None:
            inherited = value
    # With no applicable line rule, Word uses its automatic single-line default.
    return inherited is None or grows(inherited)


def _table_header_matches_policy(table) -> bool:
    """Verify the confirmed policy: exactly the first row repeats as header.

    WordprocessingML permits several consecutive header rows, but the current
    renderer and confirmed new-table policy produce a single header row.
    Explicitly disabled flags on later rows are valid and must not be mistaken
    for enabled headers. Unknown values and duplicate properties fail closed.
    """
    rows = table.findall(qn("w:tr"))
    if not rows:
        return False
    for index, row in enumerate(rows):
        properties = row.findall(qn("w:trPr"))
        if len(properties) > 1:
            return False
        headers = properties[0].findall(qn("w:tblHeader")) if properties else []
        if index == 0:
            if len(headers) != 1 or headers[0].get(qn("w:val")) not in {None, "1", "true", "on"}:
                return False
        elif len(headers) > 1 or (headers and headers[0].get(qn("w:val")) not in {"0", "false", "off"}):
            return False
    return True


def _row_height_properties_allow_growth(properties) -> bool:
    """Accept only an unambiguous, non-fixed row height, if one is present."""
    if len(properties) > 1:
        return False
    if not properties:
        return True
    if properties[0].find(qn("w:trPrChange")) is not None:
        return False
    heights = properties[0].findall(qn("w:trHeight"))
    if len(heights) > 1:
        return False
    if not heights:
        return True
    height = heights[0]
    # Word 2007 documented atLeast as the default and 31680 twips as
    # the upper limit. This check does not prove rendered visibility.
    rule = height.get(qn("w:hRule"), "")
    value = height.get(qn("w:val"))
    return (rule in {"", "auto", "atLeast"} and value is not None
            and value.isascii() and value.isdecimal()
            and len(value) <= 9 and int(value) <= 31680)


def _table_direct_row_heights_allow_growth(table) -> bool:
    """Reject direct fixed or malformed row heights on generated tables."""
    rows = table.findall(qn("w:tr"))
    return bool(rows) and all(_row_height_properties_allow_growth(row.findall(qn("w:trPr")))
                              for row in rows)


def _table_style_row_heights_allow_growth(table, styles) -> bool:
    """Reject unsafe row heights in explicit/default table style chains.

    Conditional row properties are considered even when their applicability
    cannot be established. This is a conservative structural check, not a
    claim that a particular Word version applies every such property.
    """
    if styles is None:
        return False
    table_properties = table.findall(qn("w:tblPr"))
    if len(table_properties) != 1:
        return False
    references = table_properties[0].findall(qn("w:tblStyle"))
    if len(references) > 1:
        return False

    entries = styles.findall(qn("w:style"))
    by_id = defaultdict(list)
    defaults = []
    for entry in entries:
        style_id = entry.get(qn("w:styleId"), "")
        if style_id:
            by_id[style_id].append(entry)
        if entry.get(qn("w:type")) != "table":
            continue
        default = entry.get(qn("w:default"), "0").lower()
        if default not in {"0", "false", "off", "1", "true", "on"}:
            return False
        if default in {"1", "true", "on"}:
            defaults.append(style_id)
    if len(defaults) > 1 or (defaults and not defaults[0]):
        return False

    selected = list(defaults)
    if references:
        style_id = references[0].get(qn("w:val"), "")
        if not style_id:
            return False
        selected.append(style_id)
    if not selected:
        # With no declared default, the effective built-in style is unknown.
        # It is safe for this check only if no authored table style supplies
        # a row height that Word could inherit.
        return not any(entry.findall(".//" + qn("w:trHeight")) for entry in entries
                       if entry.get(qn("w:type")) == "table")

    for style_id in selected:
        seen = set()
        while style_id:
            if style_id in seen or len(seen) >= 128:
                return False
            seen.add(style_id)
            matches = by_id.get(style_id, [])
            if len(matches) != 1 or matches[0].get(qn("w:type")) != "table":
                return False
            style = matches[0]
            direct = style.findall(qn("w:trPr"))
            conditionals = style.findall(qn("w:tblStylePr"))
            groups = [direct, *(item.findall(qn("w:trPr")) for item in conditionals)]
            if not all(_row_height_properties_allow_growth(group) for group in groups):
                return False
            known_heights = sum(len(group[0].findall(qn("w:trHeight"))) for group in groups if group)
            if known_heights != len(style.findall(".//" + qn("w:trHeight"))):
                return False
            bases = style.findall(qn("w:basedOn"))
            if len(bases) > 1:
                return False
            style_id = bases[0].get(qn("w:val"), "") if bases else ""
            if bases and not style_id:
                return False
    return True


def _object_page_relation(block, layout_receipt) -> tuple[bool, dict]:
    """Check object/caption pages using verified native Word measurements."""
    native = (layout_receipt or {}).get("server_layout") or (layout_receipt or {}).get("word_native") or {}
    rows = native.get("extra_bookmarks") or []
    positions = {row.get("bookmark"): row.get("physical_page")
                 for row in rows if isinstance(row, dict)}
    pages = {role: positions.get(block_bookmark_name(block.id, role))
             for role in ("start", "end", "caption")}
    valid = all(type(page) is int and page > 0 for page in pages.values())
    if not valid:
        return False, pages
    if block.type == BlockType.IMAGE:
        return pages["start"] == pages["end"] == pages["caption"], pages
    if block.type == BlockType.TABLE:
        return pages["caption"] == pages["start"] <= pages["end"], pages
    return False, pages


def _table_row_page_relation(block, layout_receipt) -> tuple[bool, dict]:
    """Independently check measured row locations, not rendered visibility."""
    native = (layout_receipt or {}).get("server_layout") or (layout_receipt or {}).get("word_native") or {}
    evidence = native.get("table_rows") or {}
    tables = evidence.get("tables") if isinstance(evidence, dict) else None
    name = block_bookmark_name(block.id, "start")
    matches = [table for table in tables or [] if isinstance(table, dict) and table.get("bookmark") == name]
    rows = matches[0].get("rows") if len(matches) == 1 else None
    pages = {row.get("bookmark"): row.get("physical_page")
             for row in native.get("extra_bookmarks", []) if isinstance(row, dict)}
    start, end = pages.get(name), pages.get(block_bookmark_name(block.id, "end"))
    summary = {"row_count": len(rows) if isinstance(rows, list) else None,
               "table_start": start, "table_end": end}
    if (not block.table or not isinstance(rows, list) or len(rows) != block.table.rows
            or type(start) is not int or type(end) is not int):
        return False, summary
    last_page = 0
    total = (layout_receipt or {}).get("page_count")
    if type(total) is not int or total < 1:
        return False, summary
    for index, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or type(row.get("row")) is not int or row["row"] != index
                or type(row.get("start_page")) is not int or type(row.get("end_page")) is not int
                or not 1 <= row["start_page"] <= row["end_page"] <= total
                or row["start_page"] < last_page):
            return False, summary
        last_page = row["end_page"]
    return (rows[0]["start_page"] == start
            and rows[-1]["start_page"] <= end <= rows[-1]["end_page"]), summary


def _heading_page_relation(heading, section, next_heading, layout_receipt):
    """Compare a heading's last page with its next meaningful content in Word."""
    native = (layout_receipt or {}).get("server_layout") or (layout_receipt or {}).get("word_native") or {}
    rows = native.get("extra_bookmarks") or []
    positions = {row.get("bookmark"): row.get("physical_page")
                 for row in rows if isinstance(row, dict)}
    following = None
    for block in section.paragraphs:
        if block.type == BlockType.PARAGRAPH and not reference_block_text(block).strip():
            continue
        role = "caption" if block.type == BlockType.TABLE and block.table else "start"
        following = block_bookmark_name(block.id, role)
        break
    if following is None and next_heading:
        following = next_heading.get("bookmark_name")
    if not following:
        return None, {"heading": positions.get(heading.get("bookmark_name")), "following": None}
    pages = {"heading": positions.get(heading.get("bookmark_name")),
             "following": positions.get(following)}
    valid = all(type(page) is int and page > 0 for page in pages.values())
    return bool(valid and pages["heading"] == pages["following"]), pages


def _normalize_word_roundtrip_document(root):
    """Remove metadata Word regenerates while preserving fixed document facts.

    Word adds revision/session identifiers, moves zero-width bookmarks next to
    content controls, and may remove direct font declarations that are already
    inherited from the style cascade.  Those mutations do not change the
    protected text, section geometry, or control topology.  Content and layout
    checks below continue to inspect the actual saved document independently.
    """
    root = copy.deepcopy(root)
    for node in root.iter():
        for key in list(node.attrib):
            q = etree.QName(key)
            if q.namespace == _W_NS and q.localname.startswith("rsid"):
                del node.attrib[key]
            elif q.namespace in _WORD_META_NS and q.localname in {"paraId", "textId", "displacedByCustomXml", "Ignorable"}:
                del node.attrib[key]
            elif q.namespace == _MC_NS and q.localname == "Ignorable":
                del node.attrib[key]
        # A saved content control receives a volatile w:id.  The tag remains
        # the stable binding and is checked separately.
        if node.tag == qn("w:sdtPr"):
            for child in list(node):
                if child.tag == qn("w:id"):
                    node.remove(child)
        if node.tag == qn("w:rPr"):
            for child in list(node):
                if child.tag in {qn("w:rFonts"), qn("w:sz"), qn("w:szCs"), qn("w:lang")}:
                    node.remove(child)
            if not len(node) and node.getparent() is not None:
                node.getparent().remove(node)

    body = root.find(qn("w:body"))
    if body is not None and len(body) >= 2 and body[-1].tag == qn("w:sectPr"):
        trailing = body[-2]
        if trailing.tag == qn("w:p") and not "".join(trailing.itertext()).strip() and not trailing.xpath(".//w:drawing", namespaces=NS):
            body.remove(trailing)
    return root


def _roundtrip_part_allowed(name):
    """Parts Word is known to rewrite during a native save."""
    return (
        name in {"_rels/.rels", "docProps/app.xml", "docProps/core.xml", "word/fontTable.xml",
                 "word/numbering.xml", "word/styles.xml", "word/theme/theme1.xml", "word/webSettings.xml",
                 "customXml/item1.xml", "customXml/itemProps1.xml", "customXml/_rels/item1.xml.rels",
                 }
        or name.startswith("word/media/")
    )


def _relationship_target_signature(relationship, archive):
    """Resolve a relationship to a stable type/content identity."""
    target = relationship.get("Target", "")
    if relationship.get("TargetMode") == "External":
        return (relationship.get("Type", ""), "external:" + target)
    path = posixpath.normpath(posixpath.join("word", target))
    if path in archive.namelist():
        payload = archive.read(path)
        return (relationship.get("Type", ""), path if not path.startswith("word/media/") else
                "media:" + hashlib.sha256(payload).hexdigest())
    return (relationship.get("Type", ""), path)


def _settings_subset(root, *, allow_word_roundtrip=False):
    """Return settings facts, allowing only renderer-owned field refresh by default.

    The exporter adds ``w:updateFields`` when it emits a document.  Other
    settings are part of the template contract unless a signed layout receipt
    authorizes a native Office round-trip, in which case Word-owned zoom and
    document identifiers may be regenerated.
    """
    if root is None:
        return []
    ignored = {"updateFields"}
    if allow_word_roundtrip:
        ignored.update({"docId", "zoom"})
    values = []
    for child in root:
        if etree.QName(child).localname in ignored:
            continue
        values.append(semantic(child))
    return sorted(values, key=repr)


def semantic(node):
    """Compare expanded names, attributes, text and order, independent of prefixes."""
    return (node.tag, tuple(sorted(node.attrib.items())), node.text or "", tuple(semantic(c) for c in node))


def paragraph_text(node):
    pieces = []
    for e in node.iter():
        if e.tag == qn("w:t"):
            pieces.append(e.text or "")
        elif e.tag == qn("w:tab"):
            pieces.append("\t")
        elif e.tag in {qn("w:br"), qn("w:cr")}:
            pieces.append("\n")
    return "".join(pieces)


def _layout_positions(root):
    """Index visible content and paragraph boundaries for bookmark checks.

    Word can lift a zero-width bookmark out of a content control when it
    remains at the same text boundary. Paragraph and page-break markers keep
    an apparently empty neighbouring paragraph from becoming equivalent.
    """
    paragraph = qn("w:p")
    text_tags = {qn("w:t"), qn("w:delText"), qn("w:instrText")}
    atom_tags = {qn("w:tab"), qn("w:br"), qn("w:cr"), qn("w:drawing"),
                 qn("w:object"), qn("w:sym"), qn("w:noBreakHyphen"),
                 qn("w:softHyphen"), qn("w:lastRenderedPageBreak"),
                 qn("w:fldChar"), qn("w:fldSimple"),
                 "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"}
    before, after = {}, {}
    position = 0
    for event, node in etree.iterwalk(root, events=("start", "end")):
        if event == "start":
            before[node] = position
            if node.tag == paragraph or node.tag in atom_tags:
                position += 1
            elif node.tag in text_tags:
                position += len(node.text or "")
        else:
            if node.tag == paragraph:
                position += 1
            after[node] = position
    return before, after


def _valid_toc_page_reference(paragraph, bookmark, page):
    fields=paragraph.findall(".//"+qn("w:fldSimple"))
    return bool(bookmark and len(fields)==1
        and fields[0].get(qn("w:instr"),"").split()==["PAGEREF",bookmark,"\\h"]
        and fields[0].get(qn("w:dirty")) in {"0","false","off"}
        and fields[0].get(qn("w:fldLock"),"false") in {"0","false","off"}
        and not fields[0].findall(".//"+qn("w:tab"))
        and paragraph_text(fields[0]).strip()==page)


def _paragraph_property_layers(resolver, paragraph):
    """Return the known paragraph cascade, or None when it cannot be resolved."""
    layers = []
    if resolver.defaults is not None:
        layers.append(resolver.defaults.find(f"{qn('w:pPrDefault')}/{qn('w:pPr')}"))

    def add_style_chain(style_id, *, table=False):
        chain, seen = [], set()
        while style_id:
            if style_id in seen or len(seen) >= 128 or style_id not in resolver.styles:
                return False
            seen.add(style_id)
            style = resolver.styles[style_id]
            if table and style.find(qn("w:tblStylePr")) is not None:
                return False
            chain.append(style.find(qn("w:pPr")))
            parent = style.find(qn("w:basedOn"))
            style_id = parent.get(qn("w:val"), "") if parent is not None else ""
        layers.extend(reversed(chain))
        return True

    tables = paragraph.xpath("ancestor::w:tbl[1]", namespaces=NS)
    if tables:
        table_style = tables[0].find(f"{qn('w:tblPr')}/{qn('w:tblStyle')}")
        if table_style is not None:
            table_style_id = table_style.get(qn("w:val"), "")
            if not table_style_id or not add_style_chain(table_style_id, table=True):
                return None
    ppr = paragraph.find(qn("w:pPr"))
    style = ppr.find(qn("w:pStyle")) if ppr is not None else None
    style_id = style.get(qn("w:val"), "") if style is not None else resolver.default_style
    if not style_id or not add_style_chain(style_id):
        return None
    layers.append(ppr)
    if any(layer is not None and layer.find(qn("w:pPrChange")) is not None for layer in layers):
        return None
    return layers


def _paragraph_property_matches(key, wanted, actual, properties, expected, layers):
    """Accept omitted default zero only when the complete cascade supports it."""
    if layers is None:
        return False
    if key in {"paragraph.spacing.before", "paragraph.spacing.after"}:
        name = key.rsplit(".", 1)[-1]
        automatic = None
        for layer in layers:
            spacing = layer.find(qn("w:spacing")) if layer is not None else None
            if spacing is None:
                continue
            value = spacing.get(qn("w:" + name + "Autospacing"))
            if value is not None:
                automatic = value.lower()
            for attr in (name, name + "Lines"):
                raw = spacing.get(qn("w:" + attr))
                if raw is not None:
                    try:
                        int(raw)
                    except ValueError:
                        return False
        if automatic not in {None, "0", "false", "off", "no"}:
            return False
    if key.startswith("paragraph.indent."):
        for layer in layers:
            indent = layer.find(qn("w:ind")) if layer is not None else None
            if indent is None:
                continue
            # Logical start/end indents require bidirectional layout resolution.
            if any(indent.get(qn("w:" + attr)) is not None
                   for attr in ("start", "end", "startChars", "endChars")):
                return False
            for attr in ("left", "right", "firstLine", "hanging", "leftChars", "rightChars", "firstLineChars", "hangingChars"):
                raw = indent.get(qn("w:" + attr))
                if raw is not None:
                    try:
                        int(raw)
                    except ValueError:
                        return False
        if key == "paragraph.indent.first_line":
            hanging = properties.get("paragraph.indent.hanging")
            if hanging is not None and hanging != expected.get("paragraph.indent.hanging"):
                if not isinstance(hanging, dict) or hanging.get("value") != 0:
                    return False
            # Resolve both physical and character values independently.  A
            # zero character value alone does not cancel inherited firstLine
            # in Word, even though the normalized resolver prefers characters.
            if isinstance(wanted, dict) and wanted.get("value") == 0:
                effective = {}
                for layer in layers:
                    indent = layer.find(qn("w:ind")) if layer is not None else None
                    if indent is not None:
                        for name in ("firstLine", "firstLineChars"):
                            value = indent.get(qn("w:" + name))
                            if value is not None:
                                effective[name] = int(value)
                if any(effective.get(name, 0) != 0 for name in ("firstLine", "firstLineChars")):
                    return False
    if actual == wanted:
        return True
    zero = lambda item: (isinstance(item, dict) and item.get("unit") in {"pt", "line", "character"}
                         and isinstance(item.get("value"), (int, float)) and item["value"] == 0)
    return zero(wanted) and (actual is None or zero(actual))


def _generated_indent_baselines(source_styles, output_styles, root, profile, *, word_roundtrip):
    """Verify the narrowly declared base styles and every generated use."""
    if source_styles is None or output_styles is None:
        return False
    defaults = [style for style in source_styles.findall(qn("w:style"))
                if style.get(qn("w:type")) == "paragraph"
                and style.get(qn("w:default"), "").lower() in {"1", "true", "on"}]
    source_ids = [style.get(qn("w:styleId")) for style in source_styles.findall(qn("w:style"))]
    expected = {}
    for heading in profile.get("headings", []):
        properties = heading.get("properties", {})
        indent = properties.get("paragraph.indent.first_line", {})
        if heading.get("inline") or indent.get("unit") != "character" or not indent.get("value"):
            continue
        size = properties.get("font.size", {})
        value = size.get("value") if size.get("unit") == "pt" else None
        if (len(defaults) != 1 or not defaults[0].get(qn("w:styleId"))
                or not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value <= 0 or value * 2 != round(value * 2)):
            return False
        style_id = "PGIndentBase" + str(round(value * 2))
        if style_id in source_ids:
            return False
        expected[heading["key"]] = style_id
    generated_ids = set(expected.values())
    present_ids = {style.get(qn("w:styleId")) for style in output_styles.findall(qn("w:style"))}
    if generated_ids and not (generated_ids & present_ids):
        # Older renderers used the source default paragraph style. Accept it
        # only when its effective base size proves the same character width.
        if not word_roundtrip and etree.tostring(source_styles, method="c14n") != etree.tostring(output_styles, method="c14n"):
            return False
        output_defaults = [style for style in output_styles.findall(qn("w:style"))
                           if style.get(qn("w:type")) == "paragraph"
                           and style.get(qn("w:default"), "").lower() in {"1", "true", "on"}]
        default_id = defaults[0].get(qn("w:styleId"))
        if len(output_defaults) != 1 or output_defaults[0].get(qn("w:styleId")) != default_id:
            return False
        resolver = EffectiveFormatResolver(output_styles)
        values, sources = {}, {}
        if resolver.defaults is not None:
            resolver._run(values, sources, resolver.defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}"))
        chain = resolver.chain(default_id)
        if resolver.issues & {"missing_style_reference", "style_inheritance_cycle"} or not chain:
            return False
        for style in chain:
            if sum(item.get(qn("w:styleId")) == style.get(qn("w:styleId"))
                   for item in output_styles.findall(qn("w:style"))) != 1:
                return False
            resolver._run(values, sources, style.find(qn("w:rPr")), style=True)
        for paragraph in root.xpath(".//w:sdt[starts-with(w:sdtPr/w:tag/@w:val, 'pg-block:')]//w:p", namespaces=NS):
            regions = paragraph.xpath("ancestor::w:sdt[starts-with(w:sdtPr/w:tag/@w:val, 'pg-region:')][1]/w:sdtPr/w:tag/@w:val", namespaces=NS)
            region_key = regions[0].removeprefix("pg-region:") if len(regions) == 1 else ""
            if region_key not in expected:
                continue
            style = paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
            actual = style.get(qn("w:val"), "") if style is not None else default_id
            size = int(expected[region_key].removeprefix("PGIndentBase")) / 2
            if actual != default_id or values.get("font.size") != {"value": size, "unit": "pt"}:
                return False
        return True
    styles_without_generated = copy.deepcopy(output_styles)
    for style_id in generated_ids:
        styles = output_styles.xpath("./w:style[@w:styleId=$ident]", namespaces=NS, ident=style_id)
        if len(styles) != 1:
            return False
        style = styles[0]
        if (style.get(qn("w:type")) != "paragraph" or style.get(qn("w:customStyle")) not in {"1", "true", "on"}
                or set(style.attrib) != {qn("w:type"), qn("w:customStyle"), qn("w:styleId")}):
            return False
        children = [child for child in style if child.tag not in {qn("w:rsid"), qn("w:uiPriority"),
                    qn("w:qFormat"), qn("w:semiHidden"), qn("w:unhideWhenUsed")}]
        if [child.tag for child in children] != [qn("w:name"), qn("w:basedOn"), qn("w:rPr")]:
            return False
        name, base, rpr = children
        if (name.attrib != {qn("w:val"): style_id}
                or base.attrib != {qn("w:val"): defaults[0].get(qn("w:styleId"))}
                or len(name) or len(base) or rpr.attrib or len(rpr) != 1
                or rpr[0].tag != qn("w:sz")
                or rpr[0].attrib != {qn("w:val"): style_id.removeprefix("PGIndentBase")}
                or len(rpr[0])):
            return False
        match = styles_without_generated.xpath("./w:style[@w:styleId=$ident]", namespaces=NS, ident=style_id)[0]
        styles_without_generated.remove(match)
    if not word_roundtrip and etree.tostring(source_styles, method="c14n") != etree.tostring(styles_without_generated, method="c14n"):
        return False
    for paragraph in root.xpath(".//w:sdt[starts-with(w:sdtPr/w:tag/@w:val, 'pg-block:')]//w:p", namespaces=NS):
        regions = paragraph.xpath("ancestor::w:sdt[starts-with(w:sdtPr/w:tag/@w:val, 'pg-region:')][1]/w:sdtPr/w:tag/@w:val", namespaces=NS)
        region_key = regions[0].removeprefix("pg-region:") if len(regions) == 1 else ""
        style = paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
        actual = style.get(qn("w:val"), "") if style is not None else ""
        if region_key in expected and actual != expected[region_key]:
            return False
        if actual in generated_ids and expected.get(region_key) != actual:
            return False
    for style in root.xpath(".//w:pStyle", namespaces=NS):
        if style.get(qn("w:val"), "") in generated_ids and not style.xpath(
                "ancestor::w:sdt[starts-with(w:sdtPr/w:tag/@w:val, 'pg-block:')]", namespaces=NS):
            return False
    return True


def _apply_master_layout_review_policy(report, verified_receipt):
    """Release the blanket review gate only for complete, verified Word evidence.

    ``verified_receipt`` must come from ``verify_layout_receipt`` for these
    exact bytes and project.  Older receipts remain readable but lack some of
    the current evidence.  Visual equivalence in other viewers is not inferred.
    """
    from app.services.office_layout import NATIVE_TOC_VERSION, SERVER_TOC_VERSION
    from app.services.output_acceptance import output_report_is_acceptable

    checks = report.get("checks")
    layout_checks = [item for item in checks if isinstance(item, dict)
                     and item.get("rule") == "master.layout_receipt"] if isinstance(checks, list) else []
    complete = (
        isinstance(verified_receipt, dict)
        and ((verified_receipt.get("version") == NATIVE_TOC_VERSION
              and verified_receipt.get("pagination_source") == "word_native"
              and verified_receipt.get("toc_preparation_source") == "word_native")
             or (verified_receipt.get("version") == SERVER_TOC_VERSION
                 and verified_receipt.get("pagination_source") == "libreoffice_server"))
        and verified_receipt.get("state_stable") is True
        and len(layout_checks) == 1 and layout_checks[0].get("status") == "pass"
        and isinstance(checks, list) and bool(checks)
        and report.get("check_count") == len(checks)
        and output_report_is_acceptable(report)
        and not report.get("unsupported")
        and not report.get("layout_evidence_limitations")
    )
    report["office_review_required"] = not complete
    report["manual_checks"] = [] if complete else [
        "在目标版本 Word/WPS 打开文件，核对实测分页、目录跳转、字体、图表及跨页表格；后台排版回执不替代目标 Office 版本验收。"
    ]
    if complete:
        report["visual_evidence_limitations"] = [
            ("已核验服务器分页、目录、字体及对象位置；学生本机 Word/WPS 的分页可能不同。"
             if verified_receipt.get("version") == SERVER_TOC_VERSION else
             "已核验 Word 分页、目录、字体及对象位置；图片内容、视觉裁切和其他 Word/WPS 环境的显示效果未逐项验证。")
        ]


def inspect_master_output(data, project, template, source, report):
    # Retain the outer inspector version for frozen reports. This marker
    # distinguishes reports that include the inherited-visibility checks.
    report["visibility_check_version"] = "word-run-visibility/1"
    report["cover_label_visibility_check_version"] = "word-cover-label-visibility/1"
    report["image_line_spacing_check_version"] = "word-image-line-spacing/1"
    report["direct_shading_check_version"] = "word-direct-shading/1"
    report["highlight_color_check_version"] = "word-highlight-color/1"
    report["cover_pagination_check_version"] = "word-cover-pagination/2"

    def check(rule, good, obj, expected=None, actual=None, *, unit=None, status=None):
        item = {"rule": "master."+rule, "status": status or ("pass" if good else "fail"), "object": obj,
                "expected": expected, "actual": actual,
                "evidence": "independent inspection of emitted OOXML and frozen source/profile"}
        if unit is not None:
            item["unit"] = unit
        report["checks"].append(item)
    layout_receipt = None
    try:
        profile = verify_profile(template, source)
        source = commentless_source(source)
        check("prepared_source",digest(package_manifest(source))==profile["prepared_source_manifest_sha256"],"comment-free source")
        with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(io.BytesIO(data)) as output:
            if len(output.infolist()) > 10000 or sum(n.file_size for n in output.infolist()) > 250_000_000 or len({n.filename for n in output.infolist()}) != len(output.infolist()):
                raise ValueError("invalid or excessive package entries")
            source_root, root = load_part(original,"word/document.xml"), load_part(output,"word/document.xml")
            if any(n.getroottree().docinfo.doctype for n in (source_root,root)):
                raise ValueError("DTD is forbidden")
            expected_block_names = {
                block_bookmark_name(block.id, role).casefold()
                for section in project.sections for block in section.paragraphs
                for role in ("start", "end", "caption")
            }
            starts = root.findall(".//" + qn("w:bookmarkStart"))
            ends = root.findall(".//" + qn("w:bookmarkEnd"))
            generated_ids = {
                item.get(qn("w:id")) for item in starts
                if item.get(qn("w:name"), "").casefold() in expected_block_names
                and (item.get(qn("w:id")) or "").isdigit()
            }

            def generated_marker(node):
                return (node.tag in {qn("w:bookmarkStart"), qn("w:bookmarkEnd")}
                        and node.get(qn("w:id")) in generated_ids)

            def content_nodes(content):
                return [node for node in content if not generated_marker(node)]

            styles_root = load_part(output,"word/styles.xml")
            resolver = EffectiveFormatResolver(styles_root,load_part(output,"word/theme/theme1.xml"),load_part(output,"word/settings.xml"))
            source_names, output_names = set(original.namelist()),set(output.namelist())
            from app.services.page_number_policy import CONTENT_TYPE, PART, REL_ID, REL_TYPE, footer_bytes, sections
            page_policy = profile.get("body_page_number_policy")
            from app.services.office_layout import NATIVE_TOC_VERSION, SERVER_TOC_VERSION, RECEIPT_PART, verify_layout_receipt, heading_name
            toc_active = profile.get("toc_active", False)
            layout_receipt = None
            if toc_active:
                try:
                    layout_receipt = verify_layout_receipt(data, project, profile)
                    check("layout_receipt", True, "directory pagination", expected="signed receipt bound to current output, project, template and worker")
                except Exception as exc:
                    check("layout_receipt", False, "directory pagination", expected="valid signed measured receipt", actual=type(exc).__name__)
            else:
                check("layout_receipt", RECEIPT_PART not in output_names, "directory pagination", expected="no receipt for templates without mapped TOC")
            if profile.get("cover_pagination") is not None:
                from app.services.office_layout import cover_pagination_result
                cover_result = cover_pagination_result(profile, layout_receipt)
                check("cover_pagination", cover_result["ok"], "cover before first content heading",
                      expected="no undeclared blank cover pages and the confirmed boundary policy", actual=cover_result)
            # The current native path copies only verified TOC rows from Word;
            # its signed receipt does not authorize changes to source parts or
            # styles. Legacy Office round-trips may have rewritten those parts.
            roundtrip_authorized = (layout_receipt is not None
                                    and layout_receipt.get("version") not in {NATIVE_TOC_VERSION, SERVER_TOC_VERSION})
            mutable = {"word/document.xml","word/_rels/document.xml.rels","[Content_Types].xml","word/settings.xml", "word/styles.xml"}
            check("indent_baseline_styles", _generated_indent_baselines(
                load_part(original, "word/styles.xml"), styles_root, root, profile,
                word_roundtrip=roundtrip_authorized), "generated content styles")
            source_media = {hashlib.sha256(original.read(n)).hexdigest() for n in source_names if n.startswith("word/media/")}
            output_media = {hashlib.sha256(output.read(n)).hexdigest() for n in output_names if n.startswith("word/media/")}
            # These omissions are only a native Office round-trip allowance.
            # The signed layout receipt is checked below before this flag is
            # used, so a hand-edited package cannot opt into the relaxed set.
            allowed_missing = {"docProps/thumbnail.jpeg", "word/stylesWithEffects.xml"} if roundtrip_authorized else set()
            protected_good = True
            for name in source_names - mutable:
                if name not in output_names:
                    if name.startswith("word/media/") and hashlib.sha256(original.read(name)).hexdigest() in output_media:
                        continue
                    if name in allowed_missing:
                        continue
                    protected_good = False
                    continue
                if original.read(name) == output.read(name):
                    continue
                if name.startswith("word/media/") and hashlib.sha256(original.read(name)).hexdigest() in output_media:
                    continue
                if roundtrip_authorized and _roundtrip_part_allowed(name):
                    continue
                protected_good = False
            check("protected_parts", protected_good, "source package")
            toc_entries = profile.get("toc_entries", [])
            added_parts = output_names-source_names
            def empty_note(name):
                if name not in output_names:
                    return False
                note = load_part(output, name)
                return note is not None and not "".join(note.itertext()).strip()
            def word_media_name(name):
                stem = posixpath.basename(name).rsplit(".", 1)[0]
                return name.startswith("word/media/") and (stem.startswith("pg-") or stem.startswith("image"))
            added_media_ok = all(word_media_name(n) for n in added_parts if n.startswith("word/media/"))
            added_parts_ok = all(
                (word_media_name(n))
                or (roundtrip_authorized and n in {"word/footnotes.xml", "word/endnotes.xml"} and empty_note(n))
                or n==RECEIPT_PART or (page_policy and n==PART)
                for n in added_parts
            )
            check("new_parts", added_parts_ok and added_media_ok
                  and (RECEIPT_PART in output_names if toc_active else RECEIPT_PART not in output_names)
                  and (PART in added_parts if page_policy else PART not in added_parts), "package parts")
            if page_policy:
                footer = load_part(output, PART)
                settings = profile.get("body_page_number") or {}
                fields = footer.findall(".//" + qn("w:fldSimple")) if footer is not None else []
                fonts = footer.findall(".//" + qn("w:rFonts")) if footer is not None else []
                sizes = footer.findall(".//" + qn("w:sz")) if footer is not None else []
                align = footer.findall(".//" + qn("w:jc")) if footer is not None else []
                sects = sections(root)
                start = page_policy.get("body_section_start", -1)
                refs_ok = 0 <= start < len(sects) == page_policy.get("section_count") and all(
                    len(refs := [ref for ref in sec.findall(qn("w:footerReference"))
                                 if ref.get(qn("w:type")) == "default"]) == 1
                    and refs[0].get(qn("r:id")) == REL_ID for sec in sects[start:])
                canonical_footer_hash = hashlib.sha256(footer_bytes(settings)).hexdigest()
                legacy_footer_hash = hashlib.sha256(footer_bytes(settings, dirty=True)).hexdigest()
                signed_footer_hash = page_policy.get("footer_sha256")
                observed_footer_hash = hashlib.sha256(output.read(PART)).hexdigest() if PART in output_names else ""
                # A legacy signed profile may produce a new canonical footer;
                # its original footer also remains valid for historical files.
                # A canonical profile never authorizes the dirty legacy footer.
                footer_matches = (signed_footer_hash in {canonical_footer_hash, legacy_footer_hash}
                                  and (observed_footer_hash == signed_footer_hash
                                       or (signed_footer_hash == legacy_footer_hash
                                           and observed_footer_hash == canonical_footer_hash)))
                good = (PART in output_names and page_policy.get("part") == PART
                        and page_policy.get("relationship_id") == REL_ID
                        and page_policy.get("settings") == settings
                        and footer_matches
                        and len(fields) == 1 and fields[0].get(qn("w:instr"), "").strip() == "PAGE"
                        and len(fonts) == 1 and all(fonts[0].get(qn("w:" + slot)) == settings.get("font") for slot in ("ascii", "hAnsi", "eastAsia", "cs"))
                        and len(sizes) == 1 and sizes[0].get(qn("w:val")) == str(round(settings.get("size_pt", 0) * 2))
                        and len(align) == 1 and align[0].get(qn("w:val")) == "center" and refs_ok)
                check("body_page_footer", bool(good), "body sections",
                      expected="confirmed PAGE field, font, size and section references",
                      actual={"profile_footer_sha256": signed_footer_hash,
                              "output_footer_sha256": observed_footer_hash,
                              "compatibility": ("invalid" if not footer_matches else
                                                "legacy-dirty-to-clean/1" if signed_footer_hash != observed_footer_hash else "exact")})
            source_rels = load_part(original,"word/_rels/document.xml.rels")
            out_rels = load_part(output,"word/_rels/document.xml.rels")
            relations = {n.get("Id"):n for n in out_rels}
            check("relationship_ids",len(relations)==len(out_rels),"relationships")
            source_relation_signatures = {_relationship_target_signature(n, original) for n in source_rels}
            output_relation_signatures = {_relationship_target_signature(n, output) for n in out_rels}
            check("source_relationships",source_relation_signatures.issubset(output_relation_signatures),"source relationships")
            # A set of targets cannot detect two section IDs swapping their
            # header/footer targets. Bind each surviving section reference to
            # the exact source relationship; the separately checked generated
            # body footer is the only allowed new section relationship.
            source_by_id = {node.get("Id"): node for node in source_rels}
            section_parts_good = len(source_by_id) == len(source_rels)
            for ref in root.xpath(".//w:sectPr/w:headerReference | .//w:sectPr/w:footerReference", namespaces=NS):
                relation_id = ref.get(qn("r:id"))
                output_relation = relations.get(relation_id)
                if page_policy and ref.tag == qn("w:footerReference") and relation_id == REL_ID:
                    continue  # validated by body_page_footer and body_page_relationship
                source_relation = source_by_id.get(relation_id)
                expected_type = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                                 + ("header" if ref.tag == qn("w:headerReference") else "footer"))
                if (source_relation is None or output_relation is None
                        or source_relation.get("Type") != expected_type
                        or output_relation.get("Type") != expected_type
                        or source_relation.get("TargetMode") is not None
                        or output_relation.get("TargetMode") is not None
                        or _relationship_target_signature(source_relation, original)
                           != _relationship_target_signature(output_relation, output)
                        or posixpath.normpath(posixpath.join("word", output_relation.get("Target", ""))) not in output_names):
                    section_parts_good = False
            check("section_part_relationships", section_parts_good, "section headers and footers",
                  expected="each section reference retains its confirmed source relationship")
            if page_policy:
                relation = relations.get(REL_ID)
                check("body_page_relationship", relation is not None and relation.get("Type") == REL_TYPE
                      and relation.get("Target") == PART.removeprefix("word/") and relation.get("TargetMode") is None,
                      "body page footer relationship")
            new_relation_signatures = output_relation_signatures - source_relation_signatures
            allowed_new_relation_types = {
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes",
            }
            new_relationships_good = True
            for relation in out_rels:
                signature = _relationship_target_signature(relation, output)
                if signature in source_relation_signatures:
                    continue
                if page_policy and relation.get("Type") == REL_TYPE and relation.get("Target") == PART.removeprefix("word/"):
                    continue
                if relation.get("Type") in allowed_new_relation_types and empty_note(posixpath.normpath(posixpath.join("word", relation.get("Target", "")))):
                    continue
                if relation.get("Type", "").endswith("/image") and posixpath.normpath(posixpath.join("word", relation.get("Target", ""))).startswith("word/media/"):
                    continue
                new_relationships_good = False
            check("new_relationships",new_relationships_good,"new relationships")
            for name in ("[Content_Types].xml","word/settings.xml"):
                before,after = load_part(original,name),load_part(output,name)
                if before is None:
                    check("protected_configuration",after is None,name); continue
                if name.endswith("settings.xml"):
                    check("protected_configuration",
                          _settings_subset(before, allow_word_roundtrip=roundtrip_authorized)
                          == _settings_subset(after, allow_word_roundtrip=roundtrip_authorized),
                          name)
                else:
                    source_entries = {(c.tag, c.get("Extension"), c.get("PartName"), c.get("ContentType")) for c in before}
                    output_entries = {(c.tag, c.get("Extension"), c.get("PartName"), c.get("ContentType")) for c in after}
                    actual_parts = {"/" + n for n in output_names}
                    actual_extensions = {posixpath.basename(n).rsplit(".", 1)[-1].lower() for n in output_names if "." in posixpath.basename(n)}
                    required = {
                        entry for entry in source_entries
                        if (entry[0].endswith("}Default") and (entry[1] or "").lower() in actual_extensions)
                        or (entry[0].endswith("}Override") and (not entry[2] or entry[2] in actual_parts))
                    }
                    valid_types = all((not entry[2] or entry[2] in actual_parts) for entry in output_entries)
                    check("protected_configuration",required.issubset(output_entries) and valid_types,name)
                    if page_policy:
                        check("body_page_content_type",sum(1 for c in after if c.tag.endswith("}Override")
                              and c.get("PartName")=="/"+PART and c.get("ContentType")==CONTENT_TYPE)==1,
                              "body page footer content type")
            # Structural oracle: source XML plus ONLY the reviewed mutation boundaries.
            # This does not generate expected student paragraphs or their formatting.
            actual_static = copy.deepcopy(root)
            for doc in (actual_static,):
                for marker in doc.xpath(".//w:bookmarkStart | .//w:bookmarkEnd", namespaces=NS):
                    if generated_marker(marker):
                        marker.getparent().remove(marker)
                for content in doc.xpath(".//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,'pg-region:') or starts-with(w:sdtPr/w:tag/@w:val,'pg-field:')]/w:sdtContent",namespaces=NS):
                    for item in list(content):
                        content.remove(item)
                if doc is actual_static:
                    toc_styles=set(profile.get("toc_style_ids", []))
                    body=doc.find(qn("w:body"))
                    for paragraph in list(doc.findall(".//"+qn("w:p"))):
                        style=paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
                        if style is not None and style.get(qn("w:val")) in toc_styles and paragraph.getparent() is body:
                            body.remove(paragraph)
            fixed_relationship_ids = {
                value for node in actual_static.iter() for name, value in node.attrib.items()
                if etree.QName(name).namespace == NS["r"]
            }
            fixed_relationships_good = True
            for relation_id in fixed_relationship_ids:
                if page_policy and relation_id == REL_ID:
                    continue  # validated by body_page_footer and body_page_relationship
                source_relation, output_relation = source_by_id.get(relation_id), relations.get(relation_id)
                if (source_relation is None or output_relation is None
                        or _relationship_target_signature(source_relation, original)
                           != _relationship_target_signature(output_relation, output)):
                    fixed_relationships_good = False
                    continue
                if (output_relation.get("TargetMode") != "External"
                        and posixpath.normpath(posixpath.join("word", output_relation.get("Target", ""))) not in output_names):
                    fixed_relationships_good = False
            check("fixed_part_relationships", fixed_relationships_good, "fixed template content",
                  expected="fixed objects and links retain their confirmed source relationships")
            def structural(node):
                return [node.tag,sorted(node.attrib.items()),node.text or "",[structural(c) for c in node]]
            check("fixed_body_and_sections",digest(structural(actual_static))==profile["protected_document_sha256"],"cover, fixed text, section breaks, heading order")
            if profile.get("manuscript_headings_sha256"):
                check("manuscript_structure", digest([(s.type, s.title) for s in project.sections
                    if s.type in {"heading1", "heading2", "heading3"}]) == profile["manuscript_headings_sha256"],
                    "source manuscript chapter hierarchy and order")
            controls = root.xpath(".//w:sdt[w:sdtPr/w:tag]",namespaces=NS)
            tags = [n.find(f"{qn('w:sdtPr')}/{qn('w:tag')}").get(qn("w:val")) for n in controls]
            check("unique_bindings",len(tags)==len(set(tags)),"content bindings")
            appearance="{http://schemas.microsoft.com/office/word/2012/wordml}"
            for control,tag in zip(controls,tags):
                if not tag.startswith("pg-"):continue
                settings=control.findall(qn("w:sdtPr")+'/'+appearance+'appearance')
                check("content_control_appearance",len(settings)==1 and settings[0].get(appearance+'val')=='hidden',tag)

            if toc_active:
                updates=load_part(output,"word/settings.xml").findall(qn("w:updateFields"))
                check("toc_preserve_measured_cache",len(updates)==1 and updates[0].get(qn("w:val")) in {"0","false","off"},
                      "directory page references",expected="preserve measured fields until document pagination")
                receipt_rows=(layout_receipt or {}).get("toc",[])
                receipt_by_key={row.get("key"):row for row in receipt_rows if isinstance(row,dict)}
                actual_rows=[]
                bookmarks={n.get(qn("w:name")):n.get(qn("w:id")) for n in root.findall(".//"+qn("w:bookmarkStart"))}
                style_levels={str(style):int(level) for level,style in profile.get("toc_style_by_level",{}).items()}
                style_levels.update({str(style):int(level) for level,style in profile.get("toc_style_by_level",{}).items() if str(level).isdigit()})
                for paragraph in root.findall(".//"+qn("w:p")):
                    if paragraph.getparent() is not root.find(qn("w:body")):
                        continue
                    style=paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
                    if style is None or style.get(qn("w:val")) not in set(profile.get("toc_style_ids", [])):
                        continue
                    runs=[(run, paragraph_text(run)) for run in paragraph.findall(".//"+qn("w:r"))]
                    significant=[i for i, (_, text) in enumerate(runs) if text.strip()]
                    good=len(significant)>=2
                    page_index=significant[-1] if significant else None
                    page=runs[page_index][1].strip() if page_index is not None else ""
                    # Word may put a label's separating space or tab in its own
                    # run. Keep those characters when comparing the actual
                    # label to the authenticated layout receipt.
                    label="".join(text for _, text in runs[:page_index]).rstrip(" .\t\u2026·") if page_index is not None else ""
                    matches=[heading for heading in profile["headings"] if heading.get("kind")!="toc" and heading_name(label)==heading_name(heading["title"])]
                    good &= len(matches)==1
                    row=receipt_by_key.get(matches[0]["key"]) if len(matches)==1 else None
                    anchors=paragraph.xpath("./w:hyperlink/@w:anchor",namespaces=NS)
                    good &= bool(row) and page==str(row.get("page","")) and label==row.get("label")
                    good &= style.get(qn("w:val"))==row.get("style_id") and style_levels.get(style.get(qn("w:val")))==row.get("level") if row else False
                    good &= anchors==[row.get("bookmark")] and row.get("bookmark")==matches[0].get("bookmark_name") if row and len(matches)==1 else False
                    good &= bool(row and row.get("bookmark") in bookmarks)
                    fields=paragraph.findall(".//"+qn("w:fldSimple"))
                    instructions=[field.get(qn("w:instr"),"").split() for field in fields]
                    from app.services.toc_appearance import toc_appearance_matches
                    level = style_levels.get(style.get(qn("w:val")))
                    appearance = profile.get("toc_appearance_by_level", {}).get(str(level))
                    check("toc_appearance", toc_appearance_matches(paragraph, styles_root, appearance),
                          label or "unmapped TOC row", expected=appearance)
                    reference_good=_valid_toc_page_reference(paragraph,(row or {}).get("bookmark"),page)
                    check("toc_page_reference",reference_good,label or "unmapped TOC row",
                          expected=(row or {}).get("bookmark"),actual=instructions)
                    good &= paragraph.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None
                    good &= all(run.xpath('./w:rPr/w:vanish/@w:val',namespaces=NS)==['0']
                                and run.xpath('./w:rPr/w:webHidden/@w:val',namespaces=NS)==['0']
                                for run, text in runs if text)
                    actual_rows.append(matches[0]["key"] if len(matches)==1 else "")
                    check("toc_row",bool(good),label or "unmapped TOC row",expected=(row or {}).get("page"),actual=page)
                expected_order=[row.get("key") for row in receipt_rows]
                check("toc_rows_complete",actual_rows==expected_order and len(actual_rows)==len(receipt_rows),"refreshed directory order",expected=expected_order,actual=actual_rows)

            def get(tag):
                matches = root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent",namespaces=NS,tag=tag)
                return matches[0] if len(matches)==1 else None

            positions_before, positions_after = _layout_positions(root)
            starts_by_name = defaultdict(list)
            for item in starts:
                starts_by_name[item.get(qn("w:name"), "").casefold()].append(item)
            start_ids = Counter(item.get(qn("w:id")) for item in starts)
            ends_by_id = defaultdict(list)
            for item in ends:
                ends_by_id[item.get(qn("w:id"))].append(item)
            sections_for_anchors = {section.key: section for section in project.sections}
            expected_anchors = set()

            def anchor(name, parent, obj, *, at_end=False):
                expected_anchors.add(name)
                found = starts_by_name[name.casefold()]
                good = len(found) == 1 and parent is not None
                if good:
                    start = found[0]
                    ident = start.get(qn("w:id"), "")
                    matching_ends = ends_by_id[ident]
                    good = (ident.isdigit() and start_ids[ident] == 1 and len(matching_ends) == 1
                            and start.getparent() is matching_ends[0].getparent()
                            and start.getnext() is matching_ends[0])
                    if good:
                        position = positions_before[start]
                        boundary = positions_after[parent] if at_end else positions_before[parent]
                        valid = {boundary}
                        if parent.tag == qn("w:p"):
                            valid.add(boundary - 1 if at_end else boundary + 1)
                        good = position in valid
                check("layout_anchor", bool(good), obj, expected=name)

            block_ids = [block.id for section in project.sections for block in section.paragraphs]
            check("block_ids_unique", len(block_ids) == len(set(block_ids)), "project content")
            for heading in profile["headings"]:
                if heading["kind"] == "toc":
                    continue
                section = sections_for_anchors.get(heading["key"])
                if section is None:
                    continue
                heading_bookmark = heading.get("bookmark_name", "")
                heading_marks = starts_by_name[heading_bookmark.casefold()]
                heading_ok = len(heading_marks) == 1 and bool(heading_bookmark)
                if heading_ok:
                    mark = heading_marks[0]
                    ident = mark.get(qn("w:id"), "")
                    heading_ok = (ident.isdigit() and start_ids[ident] == 1 and len(ends_by_id[ident]) == 1)
                check("heading_anchor_unique", bool(heading_ok), heading["key"], expected=heading_bookmark)
                if heading_ok:
                    heading_paragraphs = heading_marks[0].xpath("ancestor::w:p[1]", namespaces=NS)
                    check("heading_visibility", len(heading_paragraphs) == 1
                          and _generated_content_visible(heading_paragraphs[0], styles_root), heading["key"],
                          expected="heading visible in Word and web layout")
                for block in section.paragraphs:
                    content = get("pg-block:" + block.id)
                    nodes = content_nodes(content) if content is not None else []
                    first = last = caption = None
                    if heading.get("inline"):
                        first = last = content
                    elif nodes and block.type in {BlockType.PARAGRAPH, BlockType.IMAGE, BlockType.EQUATION}:
                        first = last = nodes[0] if nodes[0].tag == qn("w:p") else None
                        if block.type == BlockType.IMAGE and block.image:
                            caption = nodes[-1] if nodes[-1].tag == qn("w:p") else None
                    elif nodes and block.type == BlockType.TABLE and block.table:
                        tables = [node for node in nodes if node.tag == qn("w:tbl")]
                        paragraphs = tables[0].findall(".//" + qn("w:p")) if len(tables) == 1 else []
                        if paragraphs:
                            first, last = paragraphs[0], paragraphs[-1]
                        caption = nodes[0] if nodes[0].tag == qn("w:p") else None
                    anchor(block_bookmark_name(block.id, "start"), first, block.id + ":start")
                    anchor(block_bookmark_name(block.id, "end"), last, block.id + ":end", at_end=True)
                    if (block.type == BlockType.IMAGE and block.image
                            or block.type == BlockType.TABLE and block.table):
                        anchor(block_bookmark_name(block.id, "caption"), caption, block.id + ":caption")
            import re
            actual_anchors = [item.get(qn("w:name"), "") for item in starts
                              if re.fullmatch(r"PG[BEC][0-9a-f]{32}", item.get(qn("w:name"), ""), re.IGNORECASE)]
            check("layout_anchor_inventory", len(actual_anchors) == len(expected_anchors)
                  and {name.casefold() for name in actual_anchors} == {name.casefold() for name in expected_anchors},
                  "generated content anchors")

            decoration_keys = {"font.bold", "font.italic", "font.underline", "font.highlight", "font.vertical_alignment"}

            def style(p, expected, obj, content_decoration=False, digit_font=False, color_scope=None, unit=None):
                properties,_,runs,_ = resolver.paragraph(p, include_whitespace=True)
                layers = _paragraph_property_layers(resolver, p)
                unit = unit or p.getroottree().getpath(p)
                color_good = True
                # Color needs an explicit check even when the frozen template
                # contains no color rule: accepting a new white override could
                # make the student text disappear against a white page.
                # A theme color or duplicate direct color is not resolved by
                # the run-property reader and must not be treated as a match.
                scoped_nodes = list((color_scope if color_scope is not None else p).iter(qn("w:r")))
                nodes_by_path = {node.getroottree().getpath(node): node for node in scoped_nodes}
                distinct_highlights = True
                visible_colors = []
                for run in runs:
                    node = nodes_by_path.get(run["path"])
                    if node is None:
                        if color_scope is None:
                            color_good = False
                        continue  # e.g. a fixed cover label outside the field
                    if run["text"].strip():
                        visible_colors.append(_text_color_is_visible(
                            node, run["properties"], white_document=_known_white_document_background(root)))
                        distinct_highlights &= _highlight_is_distinct_from_text(
                            node, run["properties"], white_document=_known_white_document_background(root))
                    rpr = node.findall(qn("w:rPr"))
                    colors = rpr[0].findall(qn("w:color")) if len(rpr) == 1 else []
                    if len(rpr) > 1 or len(colors) > 1:
                        color_good = False
                        continue
                    direct = None
                    if colors:
                        color = colors[0]
                        direct = color.get(qn("w:val"), "").lower()
                        if (set(color.attrib) != {qn("w:val")} or len(color)
                                or (color.text or "").strip()
                                or not re.fullmatch(r"(?:auto|[0-9a-f]{6})", direct)):
                            color_good = False
                            continue
                    wanted = expected.get("font.color")
                    actual = run["properties"].get("font.color")
                    if wanted is None or isinstance(wanted, str) and wanted.lower() == "auto":
                        # A direct auto value resets inherited colors. Without
                        # it, an effective literal color is not automatic.
                        color_good &= direct == "auto" if direct is not None else actual in (None, "auto")
                    elif isinstance(wanted, str) and re.fullmatch(r"[0-9a-fA-F]{6}", wanted):
                        color_good &= (direct is None or direct == wanted.lower()) and (
                            isinstance(actual, str) and actual.lower() == wanted.lower())
                    else:
                        color_good = False
                check("highlight_color_distinct", bool(distinct_highlights), obj,
                      expected="effective foreground differs from the highlight; known colors only", unit=unit)
                if any(value is False for value in visible_colors):
                    color_status = "fail"
                elif any(value is None for value in visible_colors):
                    color_status = "unverifiable"
                else:
                    color_status = "pass"
                check("text_color_visibility", color_status == "pass", obj,
                      expected="text is visible on the verified page background",
                      actual=visible_colors, unit=unit, status=color_status)
                for key,value in expected.items():
                    if key.startswith("paragraph."):
                        property_good = _paragraph_property_matches(
                            key, value, properties.get(key), properties, expected, layers,
                        )
                    elif key.startswith("font.") and key != "font.color" and not (content_decoration and key in decoration_keys):
                        property_good = all(
                            run["properties"].get(key) == value
                            for run in runs
                            if not (digit_font and key in {"font.ascii", "font.hAnsi", "font.eastAsia", "font.cs"}
                                    and run["text"] and all("0" <= char <= "9" for char in run["text"]))
                        )
                    elif key == "font.color":
                        property_good = color_good
                    else:
                        continue
                    check("effective_format", bool(property_good), obj,
                          expected={key: value}, unit=f"{unit}#{key}")
                if "font.color" not in expected:
                    check("effective_format", bool(color_good), obj,
                          expected={"font.color": "preserve the source profile color"},
                          unit=f"{unit}#font.color")

            def digit_style(p, obj, font):
                _, _, runs, _ = resolver.paragraph(p, include_whitespace=True)
                slots = ("font.ascii", "font.hAnsi", "font.eastAsia", "font.cs")
                good = all(all(str(run["properties"].get(slot, "")).casefold() == font.casefold() for slot in slots)
                           for run in runs if any("0" <= char <= "9" for char in run["text"]))
                check("digit_font", good, obj, expected=font)

            digit_rule = profile.get("digit_font_rule")
            if digit_rule:
                numbering = load_part(output, "word/numbering.xml")
                styles = load_part(output, "word/styles.xml")
                for heading in profile["headings"]:
                    if heading.get("kind") not in HEADING_KINDS:
                        continue
                    marks = root.xpath(".//w:bookmarkStart[@w:name=$name]", namespaces=NS,
                                       name=heading.get("bookmark_name", ""))
                    paragraphs = marks[0].xpath("ancestor::w:p[1]", namespaces=NS) if len(marks) == 1 else []
                    check("digit_heading_anchor", len(paragraphs) == 1, heading["key"])
                    if len(paragraphs) == 1:
                        digit_style(paragraphs[0], heading["key"] + ":heading", digit_rule["font"])
                        error = numbering_font_error(paragraphs[0], numbering, styles, digit_rule["font"])
                        check("digit_numbering_font", not error, heading["key"], expected=digit_rule["font"], actual=error or None)

            def content_style(p, expected, block, text, inline=None):
                # Compare text spans, not run indexes: Word may split or merge runs.
                _, _, actual_runs, _ = resolver.paragraph(p, include_whitespace=True)
                segments = [(len(item.text), item) for item in inline or [] if item.text]
                if not inline and text:
                    segments = [(len(text), None)]

                def wanted(item):
                    def flag(name):
                        return bool(getattr(block, name, False) or (item and getattr(item, name, False)))

                    highlight = item.highlight if item and item.highlight is not None else block.highlight
                    vertical = item.vert_align if item and item.vert_align is not None else block.vert_align
                    return {
                        "font.bold": bool(expected.get("font.bold", False) or flag("bold")),
                        "font.italic": bool(expected.get("font.italic", False) or flag("italic")),
                        "font.underline": "single" if flag("underline") else expected.get("font.underline", "none"),
                        "font.highlight": highlight if highlight is not None else expected.get("font.highlight", "none"),
                        "font.vertical_alignment": vertical if vertical is not None else expected.get("font.vertical_alignment", "baseline"),
                    }

                mapping_good = sum(length for length, _ in segments) == len(text)
                mapping_good &= sum(run["character_count"] for run in actual_runs) == len(text)
                decoration_good = mapping_good
                vertical_good = mapping_good
                segment_index = 0
                segment_remaining = segments[0][0] if segments else 0
                for run in actual_runs:
                    remaining = run["character_count"]
                    actual = run["properties"]
                    while remaining and segment_index < len(segments):
                        required = wanted(segments[segment_index][1])
                        for key, value in required.items():
                            default = False if key in {"font.bold", "font.italic"} else "baseline" if key == "font.vertical_alignment" else "none"
                            observed = actual.get(key, default)
                            if key == "font.highlight" and isinstance(value, str) and isinstance(observed, str):
                                value, observed = value.lower(), observed.lower()
                            if key == "font.vertical_alignment":
                                vertical_good &= observed == value
                            else:
                                decoration_good &= observed == value
                        consumed = min(remaining, segment_remaining)
                        remaining -= consumed
                        segment_remaining -= consumed
                        if not segment_remaining:
                            segment_index += 1
                            if segment_index < len(segments):
                                segment_remaining = segments[segment_index][0]
                    decoration_good &= remaining == 0
                    vertical_good &= remaining == 0
                check("inline_decoration", bool(decoration_good), block.id)
                check("inline_vertical_alignment", bool(vertical_good), block.id,
                      expected="preserve baseline, superscript and subscript semantics")

            for field in profile["fields"]:
                content = get("pg-field:"+field["key"])
                check("field_value",content is not None and paragraph_text(content)==project.meta.get(field["key"],""),field["key"])
                if content is not None:
                    check("content_visibility", _generated_content_visible(content, styles_root), field["key"],
                          expected="field text visible in Word and web layout")
                    check("content_direct_shading", _generated_content_has_no_direct_shading(content), field["key"],
                          expected="no unmodeled direct shading in generated field content")
                    # Resolve the enclosing source paragraph, including field runs inside SDT.
                    p = next((n for n in content.iterancestors() if n.tag==qn("w:p")),None)
                    if p is not None:
                        check("field_label_visibility", _generated_content_visible(p, styles_root)
                              and _cover_label_visible(source_root, field, content, styles_root), field["key"],
                              expected="cover field and its label visible in Word and web layout")
                        paragraph_rules = next((item["properties"] for item in profile.get("cover_table_overrides", [])
                                                if item["path"] == field["path"]), {})
                        expected = {k: v for k, v in field["properties"].items() if k.startswith("font.")}
                        expected.update({k: v for k, v in paragraph_rules.items() if k.startswith("paragraph.")})
                        style(p, expected, field["key"], color_scope=content)
            expected_regions = [h["key"] for h in profile["headings"] if h["kind"]!="toc"]
            check("project_sections",[s.key for s in project.sections]==expected_regions,"project section order")
            sections = {s.key:s for s in project.sections}
            if uses_sequential_reference_numbering(project):
                reference_blocks = [block for section in project.sections
                    if str(getattr(section.type, "value", section.type)) == "references" or section.key == "references"
                    for block in section.paragraphs
                    if block.type == BlockType.PARAGRAPH and reference_block_text(block).strip()]
                actual_numbers = []
                complete = True
                for block in reference_blocks:
                    controls = root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent", namespaces=NS, tag="pg-block:" + block.id)
                    paragraphs = controls[0].findall(qn("w:p")) if len(controls) == 1 else []
                    match = re.match(r"^\[(\d+)\](?:\s|$)", paragraph_text(paragraphs[0])) if len(paragraphs) == 1 else None
                    if match:
                        actual_numbers.append(int(match.group(1)))
                    else:
                        complete = False
                expected_numbers = list(range(1, len(reference_blocks) + 1))
                check("reference_sequential_numbers", complete and actual_numbers == expected_numbers,
                      "references", expected=expected_numbers, actual=actual_numbers)
            chapter=figure=table_number=0
            measured_headings = [h for h in profile["headings"] if h["kind"] != "toc"]
            for heading_index, heading in enumerate(measured_headings):
                if heading["kind"]=="heading1":
                    chapter+=1; figure=table_number=0
                region=get("pg-region:"+heading["key"])
                blocks=sections.get(heading["key"])
                if region is None or blocks is None:
                    check("region",False,heading["key"]); continue
                if toc_active and heading["kind"] in HEADING_KINDS:
                    following_heading = (measured_headings[heading_index + 1]
                                         if heading_index + 1 < len(measured_headings) else None)
                    together, pages = _heading_page_relation(heading, blocks, following_heading, layout_receipt)
                    if together is not None:
                        check("heading_pagination", together, heading["key"],
                              expected="heading and following content start on the same Word page", actual=pages)
                block_tags = [n.find(f"{qn('w:sdtPr')}/{qn('w:tag')}").get(qn("w:val")) if n.tag==qn("w:sdt") and n.find(f"{qn('w:sdtPr')}/{qn('w:tag')}") is not None else "" for n in content_nodes(region)]
                check("block_order",block_tags==["pg-block:"+b.id for b in blocks.paragraphs],heading["key"])
                maximum_width, maximum_height=heading["page_box_cm"]
                reference_number=0
                for block in blocks.paragraphs:
                    if block.type == BlockType.IMAGE and block.image:
                        figure += 1
                    elif block.type == BlockType.TABLE and block.table:
                        table_number += 1
                    content=get("pg-block:"+block.id)
                    if content is None:
                        check("block",False,block.id); continue
                    check("content_visibility", _generated_content_visible(content, styles_root), block.id,
                          expected="student text and images visible in Word and web layout")
                    check("content_direct_shading", _generated_content_has_no_direct_shading(content), block.id,
                          expected="no unmodeled direct shading in generated block content")
                    nodes=content_nodes(content)
                    props=heading["properties"]
                    if block.type==BlockType.PARAGRAPH:
                        is_reference=heading["kind"]=="references" and bool(reference_block_text(block).strip())
                        if is_reference: reference_number+=1
                        text=reference_block_text(block,reference_number_for(block,reference_number) if is_reference else None)
                        inline_runs = block.inline_runs
                        if block.inline_runs:
                            try:
                                if is_reference:
                                    inline_runs = reference_inline_runs(block, reference_number_for(block, reference_number))
                                consistent = ("".join(run.text for run in block.inline_runs) == block.text
                                              and "".join(run.text for run in inline_runs) == text)
                            except ReferenceInlineMappingError:
                                consistent = False
                                inline_runs = []
                            check("inline_text_consistency", consistent, block.id)
                        if heading.get("inline"):
                            nodes = [node for node in nodes if node.tag not in {qn("w:bookmarkStart"), qn("w:bookmarkEnd")}]
                            actual_p=next((n for n in content.iterancestors() if n.tag==qn("w:p")),None)
                            check("inline_slot",len(blocks.paragraphs)==1 and not block.inline_runs and '\n' not in text and bool(nodes) and all(n.tag==qn("w:r") for n in nodes) and paragraph_text(content)==text,block.id)
                            if actual_p is not None:
                                label_override = (profile.get("inline_label_overrides") or {}).get(heading["key"])
                                if label_override:
                                    label = etree.Element(qn("w:p"))
                                    label_ppr = actual_p.find(qn("w:pPr"))
                                    if label_ppr is not None:
                                        label.append(copy.deepcopy(label_ppr))
                                    label.extend(copy.deepcopy(n) for n in actual_p.findall(qn("w:r")))
                                    expected_label = label_override["text"] + label_override["separator"]
                                    check("inline_label", paragraph_text(label) == expected_label, heading["key"],
                                          expected=expected_label, actual=paragraph_text(label))
                                    if label_override.get("bold") is not None:
                                        _, _, label_runs, _ = resolver.paragraph(label, include_whitespace=True)
                                        check("inline_label_bold", bool(label_runs) and all(
                                            run["properties"].get("font.bold") == label_override["bold"]
                                            for run in label_runs if run["text"]), heading["key"],
                                            expected=label_override["bold"])
                                virtual=etree.Element(qn("w:p"))
                                ppr=actual_p.find(qn("w:pPr"))
                                if ppr is not None:virtual.append(copy.deepcopy(ppr))
                                virtual.extend(copy.deepcopy(n) for n in nodes)
                                style(virtual,props,block.id,content_decoration=True)
                                content_style(virtual,props,block,text)
                            continue
                        check("text",len(nodes)==1 and nodes[0].tag==qn("w:p") and paragraph_text(nodes[0])==text,block.id)
                        if len(nodes)==1 and nodes[0].tag==qn("w:p"):
                            apply_digits = bool(digit_rule and heading["kind"] in BODY_KINDS)
                            style(nodes[0],props,block.id,content_decoration=True,digit_font=apply_digits)
                            if apply_digits:
                                digit_style(nodes[0],block.id,digit_rule["font"])
                            content_style(nodes[0],props,block,text,inline_runs)
                            check("text_only",not nodes[0].xpath(".//w:drawing | .//w:object | .//w:instrText | .//w:fldSimple | .//w:fldChar",namespaces=NS),block.id)
                    elif block.type==BlockType.IMAGE and block.image:
                        path=resolve_document_image_path(project.id,block.image.url)
                        embeds=content.findall(".//{"+A+"}blip")
                        actual=[]
                        for embed in embeds:
                            rel=relations.get(embed.get(qn("r:embed")))
                            if rel is not None:
                                target=posixpath.normpath(posixpath.join("word",rel.get("Target", "")))
                                actual.append(hashlib.sha256(output.read(target)).hexdigest() if target in output_names else "")
                        check("image_asset",path is not None and actual==[hashlib.sha256(path.read_bytes()).hexdigest()],block.id)
                        check("image_source_crop", _picture_source_is_uncropped(content), block.id,
                              expected="generated picture has no source crop")
                        check("image_fill_geometry", _picture_fill_is_unmodified(content), block.id,
                              expected="generated picture uses an unmodified stretch fill")
                        check("image_shape_geometry", _picture_shape_matches_inline_extent(content), block.id,
                              expected="generated inline picture has an identity transform and matching rectangular geometry")
                        check("image_effects", _picture_has_no_unverified_effects(content), block.id,
                              expected="generated picture has no unverified drawing effects or external image link")
                        extents=content.findall(".//{"+WP+"}extent")
                        if path is not None and len(extents)==1:
                            from docx.image.image import Image
                            image=Image.from_file(str(path)); ratio=image.width/image.height
                            w,h=[int(extents[0].get(k,"0"))/360000 for k in ("cx","cy")]
                            wanted_w=block.image.width_cm or (block.image.height_cm*ratio if block.image.height_cm else min(maximum_width,image.width/360000))
                            wanted_h=block.image.height_cm or wanted_w/ratio
                            scale=min(1,maximum_width/wanted_w,maximum_height/wanted_h)
                            check("image_geometry",w>0 and h>0 and abs(w-wanted_w*scale)<.01 and abs(h-wanted_h*scale)<.01,block.id)
                        else:
                            check("image_geometry",False,block.id)
                        check("image_line_spacing", len(nodes) == 2
                              and _picture_line_allows_growth(nodes[0], styles_root), block.id,
                              expected="image paragraph line can expand to its natural height")
                        if len(nodes)==2 and nodes[0].tag==qn("w:p"):
                            image_properties = resolver.paragraph(nodes[0])[0]
                            image_layers = _paragraph_property_layers(resolver, nodes[0])
                            image_indents = {
                                "paragraph.indent.left": {"value": 0, "unit": "pt"},
                                "paragraph.indent.right": {"value": 0, "unit": "pt"},
                                "paragraph.indent.first_line": {"value": 0, "unit": "character"},
                            }
                            check("figure_alignment", image_properties.get("paragraph.align") == profile.get("image_alignment", "center")
                                  and all(_paragraph_property_matches(key, value, image_properties.get(key),
                                                                      image_properties, image_indents, image_layers)
                                          for key, value in image_indents.items()), block.id)
                            check("figure_keep_with_caption", image_properties.get("paragraph.keep_next") is True, block.id)
                        if toc_active:
                            same_page, pages = _object_page_relation(block, layout_receipt)
                            check("figure_pagination", same_page, block.id,
                                  expected="image and caption on the same Word page", actual=pages)
                        caption=re.sub(r"^图\s*\d+(?:[.\-]\d+)*\s*","",block.image.caption).strip()
                        check("figure_caption",len(nodes)==2 and paragraph_text(nodes[-1])==f"图 {chapter}.{figure} {caption}".rstrip(),block.id)
                        if len(nodes)==2:
                            style(nodes[-1],{**{k:v for k,v in profile["object_properties"]["figure_caption"].items() if k != "paragraph.indent.hanging"},"paragraph.align":"center","paragraph.indent.first_line":{"value":0,"unit":"character"},"paragraph.indent.left":{"value":0,"unit":"pt"},"paragraph.indent.right":{"value":0,"unit":"pt"},"paragraph.keep_lines":True},block.id+":caption")
                    elif block.type==BlockType.TABLE and block.table:
                        tables=[n for n in nodes if n.tag==qn("w:tbl")]
                        actual=[[paragraph_text(c) for c in r.findall(qn("w:tc"))] for r in tables[0].findall(qn("w:tr"))] if len(tables)==1 else []
                        check("table_cells",not block.table.source_xml and actual==block.table.cells,block.id)
                        if len(tables)==1:
                            table=tables[0]; grid=table.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")
                            width=sum(int(c.get(qn("w:w"),"0")) for c in grid)/567
                            check("table_geometry",len(grid)==block.table.cols and abs(width-maximum_width)<.03 and all(abs(int(c.get(qn("w:w"),"0"))/567-maximum_width/block.table.cols)<.01 for c in grid),block.id)
                            from app.services.table_borders import EDGES, default_table_borders
                            expected_borders = profile.get("table_borders") or (block.table.borders.model_dump() if block.table.borders else None) or default_table_borders()
                            tblprs = table.findall(qn("w:tblPr"))
                            tblpr = tblprs[0] if len(tblprs) == 1 else None
                            border_sets = tblpr.findall(qn("w:tblBorders")) if tblpr is not None else []
                            borders = border_sets[0] if len(border_sets) == 1 else None
                            actual_borders = {}
                            safe_borders = (borders is not None and not borders.attrib and not (borders.text or "").strip()
                                            and len(borders) == len(EDGES))
                            if borders is not None:
                                for edge in EDGES:
                                    found = borders.findall(qn("w:" + edge))
                                    if len(found) == 1:
                                        node = found[0]
                                        safe_borders &= (set(node.attrib) == {qn("w:val"), qn("w:color"), qn("w:sz"), qn("w:space")}
                                                         and not len(node) and not (node.text or "").strip())
                                        actual_borders[edge] = {"style": node.get(qn("w:val")), "color": node.get(qn("w:color")),
                                            "size": node.get(qn("w:sz")), "space": node.get(qn("w:space"))}
                                    else:
                                        safe_borders = False
                            expected_xml_borders = {edge: {key: str(value) for key, value in rule.items()}
                                                    for edge, rule in expected_borders.items()}
                            check("table_borders", safe_borders and actual_borders == expected_xml_borders
                                  and not table.xpath(".//w:tcBorders", namespaces=NS),
                                  block.id, expected=expected_xml_borders, actual=actual_borders)
                            alignment = tblpr.find(qn("w:jc")) if tblpr is not None else None
                            check("table_alignment", alignment is not None and alignment.get(qn("w:val")) == "center", block.id)
                            for row_index, row in enumerate(table.findall(qn("w:tr"))):
                                for cell_index, cell in enumerate(row.findall(qn("w:tc"))):
                                    for paragraph_index, paragraph in enumerate(cell.findall(".//" + qn("w:p"))):
                                        style(paragraph, {**profile["object_properties"]["table_text"],
                                              "paragraph.indent.first_line":{"value":0,"unit":"character"}},
                                              block.id, unit=f"table-cell:{row_index}:{cell_index}:{paragraph_index}")
                            check("table_header",_table_header_matches_policy(table),block.id)
                            check("table_row_height", _table_direct_row_heights_allow_growth(table), block.id,
                                  expected="no fixed or invalid direct row height")
                            check("table_style_row_height", _table_style_row_heights_allow_growth(table, styles_root), block.id,
                                  expected="no fixed or invalid inherited table-style row height")
                        caption=re.sub(r"^表\s*\d+(?:[.\-]\d+)*\s*","",block.table.caption).strip()
                        check("table_caption",len(nodes)==2 and paragraph_text(nodes[0])==f"表 {chapter}.{table_number} {caption}".rstrip() and nodes[-1].tag==qn("w:tbl"),block.id)
                        if toc_active:
                            same_page, pages = _object_page_relation(block, layout_receipt)
                            check("table_caption_pagination", same_page, block.id,
                                  expected="caption and table start on the same Word page", actual=pages)
                            native = (layout_receipt or {}).get("server_layout") or (layout_receipt or {}).get("word_native") or {}
                            if "table_rows" in native:
                                located, details = _table_row_page_relation(block, layout_receipt)
                                check("table_row_pagination", located, block.id,
                                      expected="every table row has an ordered Word page location", actual=details)
                            else:
                                reason = ("旧版 Word 排版回执不含表格逐行定位证据"
                                          if layout_receipt is not None else "Word 排版回执缺失，无法验证表格逐行分页")
                                check("table_row_pagination", False, block.id,
                                      expected="every table row has an ordered Word page location",
                                      actual=reason, status="unverifiable")
                                report.setdefault("layout_evidence_limitations", []).append(reason + ":" + block.id)
                        if len(nodes)==2:
                            style(nodes[0],{**{k:v for k,v in profile["object_properties"]["table_caption"].items() if k != "paragraph.indent.hanging"},"paragraph.align":"center","paragraph.indent.first_line":{"value":0,"unit":"character"},"paragraph.indent.left":{"value":0,"unit":"pt"},"paragraph.indent.right":{"value":0,"unit":"pt"},"paragraph.keep_next":True,"paragraph.keep_lines":True},block.id+":caption")
                    elif block.type==BlockType.EQUATION and block.equation:
                        from app.domain.documents.schemas import ContentBlock
                        from app.services.docx_anchors import _safe_omml
                        expected_math = _safe_omml(ContentBlock(
                            id=block.id, kind="equation", data={"omml": block.equation.omml},
                        ))
                        actual_math = content.findall(".//"+qn("m:oMath"))
                        good = (expected_math is not None and len(actual_math)==1
                                and semantic(expected_math)==semantic(actual_math[0]))
                        check("equation_omml",good,block.id,
                              expected=semantic(expected_math) if expected_math is not None else None,
                              actual=[semantic(item) for item in actual_math])
                        check("equation_structure",len(nodes)==1 and nodes[0].tag==qn("w:p") and len(actual_math)==1,block.id)
                        if len(nodes)==1 and nodes[0].tag==qn("w:p"):
                            style(nodes[0],props,block.id)
                    else:
                        check("unsupported_dynamic_block",False,block.id)
            report["source_profile_sha256"]=profile["profile_sha256"]
    except Exception as exc:
        check("inspectable",False,"document",actual=type(exc).__name__)
    report["check_count"]=len(report["checks"])
    report["passed_count"]=sum(c["status"]=="pass" for c in report["checks"])
    report["status"]="pass" if report["check_count"] and report["check_count"]==report["passed_count"] else "fail"
    from app.services.output_acceptance import apply_1101_acceptance_contract
    apply_1101_acceptance_contract(report)
    _apply_master_layout_review_policy(report, layout_receipt)
    return report
