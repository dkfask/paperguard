"""Structural table verification helpers extracted from master_docx_check."""

from __future__ import annotations

from collections import defaultdict

from docx.oxml.ns import qn

from app.services.master_docx_renderer import block_bookmark_name


def _table_rows_cant_split(table) -> bool:
    """Verify every generated table row sets w:cantSplit without disabling or duplicating it."""
    rows = table.findall(qn("w:tr"))
    if not rows:
        return False
    for row in rows:
        properties = row.findall(qn("w:trPr"))
        if len(properties) != 1:
            return False
        splits = properties[0].findall(qn("w:cantSplit"))
        if len(splits) != 1 or splits[0].get(qn("w:val")) not in {None, "1", "true", "on"}:
            return False
    return True


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


__all__ = [
    "_row_height_properties_allow_growth",
    "_table_direct_row_heights_allow_growth",
    "_table_header_matches_policy",
    "_table_row_page_relation",
    "_table_rows_cant_split",
    "_table_style_row_heights_allow_growth",
]
