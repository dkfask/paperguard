"""Bounded Word table-border observations and serialization."""
from __future__ import annotations

from collections import Counter

from app.core.extract.xml_utils import qn

EDGES = ('top', 'bottom', 'left', 'right', 'insideH', 'insideV')
STYLES = {'nil', 'none', 'single', 'double', 'dotted', 'dashed', 'dashSmallGap',
          'dotDash', 'dotDotDash', 'triple', 'thick', 'thinThickSmallGap',
          'thickThinSmallGap', 'thinThickThinSmallGap', 'thinThickMediumGap',
          'thickThinMediumGap', 'thinThickThinMediumGap', 'thinThickLargeGap',
          'thickThinLargeGap', 'thinThickThinLargeGap'}


def default_table_borders() -> dict:
    return {edge: {'style': 'single', 'color': '000000', 'size': 4, 'space': 0} for edge in EDGES}


def borderless_table_borders() -> dict:
    return {edge: {'style': 'nil', 'color': 'auto', 'size': 4, 'space': 0} for edge in EDGES}


def _read_border_set(node):
    result = {}
    if node is None:
        return result
    if any(node.find(qn("w:" + edge)) is not None for edge in ("start", "end")):
        raise ValueError("表格使用逻辑方向边框（start/end），当前无法可靠保留，请联系系统团队适配")
    for edge in EDGES:
        values = node.findall(qn('w:' + edge))
        if len(values) > 1:
            raise ValueError('表格边框包含重复定义')
        if not values:
            continue
        item = values[0]
        style = item.get(qn('w:val'), 'single')
        color = item.get(qn('w:color'), 'auto')
        if style not in STYLES or any(item.get(qn('w:' + attr)) is not None for attr in ('themeColor', 'themeTint', 'themeShade', 'shadow', 'frame')):
            raise ValueError('表格边框样式超出当前可保真范围')
        try:
            size = int(item.get(qn('w:sz'), '4'))
            space = int(item.get(qn('w:space'), '0'))
        except ValueError as exc:
            raise ValueError('表格边框尺寸无效') from exc
        if not (0 <= size <= 96 and 0 <= space <= 31) or not (color == 'auto' or len(color) == 6 and all(c in '0123456789abcdefABCDEF' for c in color)):
            raise ValueError('表格边框属性无效')
        result[edge] = {'style': style, 'color': color.upper() if color != 'auto' else color,
                        'size': size, 'space': space}
    return result


def observe_table_borders(table):
    """Observe direct and base-style edges; reject conditional/cell overrides."""
    if table._tbl.xpath('.//w:tcBorders'):
        raise ValueError('表格含单元格独立边框，当前无法完整保留')
    layers = []
    style = table.style
    visited = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        node = style.element
        if node.findall(qn('w:tblStylePr')):
            raise ValueError('表格含条件样式，当前无法完整保留边框')
        pr = node.find(qn('w:tblPr'))
        layers.insert(0, _read_border_set(pr.find(qn('w:tblBorders')) if pr is not None else None))
        style = style.base_style
    pr = table._tbl.tblPr
    layers.append(_read_border_set(pr.find(qn('w:tblBorders')) if pr is not None else None))
    result = {}
    for layer in layers:
        result.update(layer)
    if not result:
        return None
    return {edge: result.get(edge, {'style': 'nil', 'color': 'auto', 'size': 4, 'space': 0}) for edge in EDGES}


def template_table_borders(document):
    """Use only a table explicitly accompanied by a body table caption."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    import re

    body = document.element.body
    results = []
    previous = None
    for element in body:
        if element.tag == qn('w:tbl') and previous is not None:
            if re.match(r'^\s*表\s*\d+(?:[.．-]\d+)+', previous):
                borders = observe_table_borders(Table(element, document))
                results.append(borders if borders is not None else borderless_table_borders())
            previous = None
        elif element.tag == qn('w:p'):
            previous = Paragraph(element, document).text
        else:
            previous = None
    if not results:
        return None
    import json
    counts = Counter(json.dumps(item, sort_keys=True) for item in results)
    if len(counts) > 1:
        raise ValueError('母版正文表格的边框规则不一致')
    return results[0]
