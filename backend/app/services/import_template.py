"""Word 模板导入: 上传 docx → 识别样式 → 生成模板配置。

**识别策略**(重要):
中文 Word 论文模板的实际排版基本写在**段落直接格式**上(段前段后/行距/
首行缩进/中西文字体), styles.xml 里的命名样式往往只是 Word 的残留
(例如 styleId 为 a / ae / af0)。因此:

1. 以**段落样本**为准: 按段落文本与样式名归类, 取同类段落直接格式的众数
2. 命名样式作兜底: 只在没有段落样本时使用
3. 模板没写到的字段沿用内置规范值(DEFAULT_STYLES)

同时从封面区域(段落 + 封面信息表)识别 meta_fields。
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

from app.core.extract.para_props import para_props
from app.core.extract.para_samples import (
    ParaSample,
    build_style_table,
    default_paragraph_style_id,
    sample_paragraphs,
    style_id_to_name,
)
from app.core.extract.run_props import run_props
from app.core.extract.xml_utils import load_part, parse_xml, qn
from app.models.template import (
    DEFAULT_STYLES,
    Align,
    CoverBlock,
    CoverField,
    MetaField,
    TextStyle,
)

# Word 样式显示名/styleId (归一化: 小写 + 去空格) → 模板样式键
# 注意: 中文模板的 styleId 常是 a/ae/af0 这类内部 ID, 所以优先用显示名匹配,
# 显示名为空时才退回 styleId (内置样式如 Heading1 通常 styleId 可读)。
_STYLE_KEY_MAP: dict[str, str] = {
    "normal": "body", "正文": "body", "bodytext": "body", "正文文本": "body",
    "bodytextfirstindent": "body", "正文首行缩进": "body",
    "heading1": "heading1", "标题1": "heading1",
    "heading2": "heading2", "标题2": "heading2",
    "heading3": "heading3", "标题3": "heading3",
    "title": "cover_title", "标题": "cover_title",
    "abstract": "english_abstract", "英文摘要": "english_abstract",
    "摘要": "abstract", "中文摘要": "abstract",
    "keywords": "english_keywords", "keyword": "english_keywords", "英文关键词": "english_keywords",
    "关键词": "keywords", "关键字": "keywords",
    "tocheading": "toc", "目录": "toc",
    "references": "references", "bibliography": "references", "参考文献": "references",
    "acknowledgement": "acknowledgement", "acknowledgments": "acknowledgement", "致谢": "acknowledgement",
    "caption": "figure_caption",
}

# 只识别这些模板键, 其余 Word 样式(页眉页脚/代码等)不进入模板
_KEEP_KEYS = {
    "heading1", "heading2", "heading3",
    "body", "cover_title", "abstract", "english_abstract", "keywords", "english_keywords", "toc",
    "references", "acknowledgement",
    "figure_caption", "table_caption", "cover_meta",
}

_ALIGN_MAP = {
    "left": Align.LEFT, "start": Align.LEFT,
    "center": Align.CENTER, "centre": Align.CENTER,
    "right": Align.RIGHT, "end": Align.RIGHT,
    "both": Align.JUSTIFY, "justify": Align.JUSTIFY, "distribute": Align.JUSTIFY,
}

_HEADING_TEXT_RE = re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节]")
_NUMERIC_HEADING_RE = re.compile(r"^\d+(?:\.\d+){1,2}\s*\S")
_ORDINAL_HEADING_RE = re.compile(r"^[一二三四五六七八九十百千万]+[、.．]\s*\S+")
# 标题类元素: 未指定首行缩进时不应继承正文的缩进
_TITLE_KEYS = {"abstract", "english_abstract", "toc", "heading1", "heading2", "heading3", "cover_title"}
_SPECIAL_TITLES = {
    "摘要": "abstract", "中文摘要": "abstract",
    "abstract": "english_abstract", "英文摘要": "english_abstract", "englishabstract": "english_abstract",
    "目录": "toc", "contents": "toc", "tableofcontents": "toc",
    "参考文献": "references", "致谢": "acknowledgement", "谢辞": "acknowledgement", "附录": "appendix",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _is_ordinal_heading(text: str) -> bool:
    return bool(_ORDINAL_HEADING_RE.match(_compact(text)))


def _is_structural_heading_text(text: str) -> bool:
    compact = _compact(text)
    return bool(
        _norm(compact) in _SPECIAL_TITLES
        or _HEADING_TEXT_RE.match(compact)
        or _is_ordinal_heading(compact)
        or _NUMERIC_HEADING_RE.match(compact)
    )


def _visible_paragraph_text(paragraph) -> str:
    """读取 Word 段落的可见文本, 排除目录页码等域代码及域结果。

    Word 标题后经常带有 ``PAGEREF`` 域。直接遍历 ``w:t`` 会把域结果的
    页码拼进标题, 导致导入后的章节名变成“第1章 绪论1”。
    """
    parts: list[str] = []
    in_field = False
    for element in paragraph.iter():
        tag = element.tag
        if tag == qn("w:fldSimple") or any(
                ancestor.tag == qn("w:fldSimple")
                for ancestor in element.iterancestors()):
            continue
        if tag == qn("w:fldChar"):
            field_type = element.get(qn("w:fldCharType"))
            if field_type == "begin":
                in_field = True
            elif field_type == "end":
                in_field = False
            continue
        if in_field or tag == qn("w:instrText"):
            continue
        if tag == qn("w:t"):
            parts.append(element.text or "")
        elif tag == qn("w:tab"):
            parts.append("\t")
    return "".join(parts)


def import_template_styles(docx_path: str | Path) -> dict:
    """从 docx 抽取并映射为模板样式配置。

    Returns:
        {
            "styles": {模板键: TextStyle},
            "style_names": [识别的 Word 样式名, ...],
            "unmapped": [未映射的 Word 样式名, ...],
        }
    """
    docx_path = Path(docx_path)
    styles_el = document_el = None
    with zipfile.ZipFile(docx_path, "r") as zf:
        styles_el = load_part(zf, "word/styles.xml")
        document_el = load_part(zf, "word/document.xml")

    style_table = build_style_table(styles_el)
    id_to_name = style_id_to_name(styles_el)
    default_style_id = default_paragraph_style_id(styles_el)
    samples = sample_paragraphs(document_el)
    has_ordinal_heading = any(
        _ORDINAL_HEADING_RE.match(_compact(sample.text))
        for sample in samples
        if sample.text and not _is_instruction_run(sample.run)
    )

    # 段落样本 → 模板键 (直接格式优先)
    from_samples: dict[str, list[dict[str, str]]] = {}
    in_cover = True
    seen_ordinal_heading = False
    tail_section = ""      # 参考文献/致谢/附录 的正文上下文
    for sample in samples:
        # 红色文字是写给学生的填写说明, 不参与样式统计
        if _is_instruction_run(sample.run):
            continue
        style_key = _style_key(sample.style_id, id_to_name)
        # 跟踪章节上下文: 参考文献/致谢/附录的正文段落归到对应样式, 而不是 body
        special = _SPECIAL_TITLES.get(_norm(_compact(sample.text)))
        if special in ("references", "acknowledgement", "appendix"):
            tail_section = special
        elif special in ("abstract", "english_abstract", "toc"):
            tail_section = ""
        key = _classify(sample, style_key, in_cover)
        if _is_ordinal_heading(sample.text):
            seen_ordinal_heading = True
        if _is_content_start(
            sample,
            style_key,
            allow_style_heading=not has_ordinal_heading or seen_ordinal_heading,
        ):
            in_cover = False
        if key == "body" and tail_section:
            key = tail_section
        if key is None:
            continue
        # 无 pStyle 的段落继承默认段落样式(通常是 Normal)
        attrs = dict(style_table.get(sample.style_id or default_style_id, {}))
        attrs.update(sample.para)
        attrs.update(sample.run)
        from_samples.setdefault(key, []).append(attrs)

    # 命名样式 → 模板键 (兜底)
    from_styles: dict[str, list[dict[str, str]]] = {}
    recognized: list[str] = []
    unmapped: list[str] = []
    paragraph_style_ids = {
        style.get(qn("w:styleId"))
        for style in styles_el.findall(qn("w:style"))
        if (style.get(qn("w:type")) or "paragraph") == "paragraph"
    }
    used_style_ids = {
        sample.style_id or default_style_id
        for sample in samples
        if sample.text.strip()
    }
    for style_id, attrs in style_table.items():
        name = id_to_name.get(style_id, "") or style_id
        key = _style_key(style_id, id_to_name)
        if key is None or key not in _KEEP_KEYS:
            # styles.xml commonly contains unused Word defaults, table styles,
            # headers, and balloon styles. Report only paragraph styles that
            # actually occur in the document body.
            if name and style_id in used_style_ids and style_id in paragraph_style_ids:
                unmapped.append(name)
            continue
        recognized.append(name)
        from_styles.setdefault(key, []).append(attrs)

    styles: dict[str, TextStyle] = {}
    for key in _KEEP_KEYS:
        candidates = from_samples.get(key) or from_styles.get(key)
        if not candidates:
            continue
        styles[key] = _aggregate(candidates, key)

    return {
        "styles": styles,
        "style_names": recognized,
        "unmapped": unmapped,
    }


def _style_key(style_id: str, id_to_name: dict[str, str]) -> str | None:
    """styleId → 模板键: 先按显示名, 再退回 styleId。"""
    if not style_id:
        return None
    name = id_to_name.get(style_id, "")
    if name:
        key = _STYLE_KEY_MAP.get(_norm(name))
        if key:
            return key
    return _STYLE_KEY_MAP.get(_norm(style_id))


def _is_toc_style(style_id: str) -> bool:
    """目录条目(TOC1/TOC2...)不是章节标题样本。"""
    return _norm(style_id).startswith("toc")


def _is_content_start(
    sample: ParaSample,
    style_key: str | None,
    *,
    allow_style_heading: bool = True,
) -> bool:
    """判断该段落是否标志"封面结束/正文开始"。"""
    if _is_toc_style(sample.style_id):
        return False
    text = _compact(sample.text)
    if not text:
        return False
    if _norm(text) in _SPECIAL_TITLES:
        return True
    if _HEADING_TEXT_RE.match(text):
        return True
    if _is_ordinal_heading(text):
        return True
    return allow_style_heading and style_key in ("heading1", "heading2", "heading3")


def _classify(sample: ParaSample, style_key: str | None, in_cover: bool) -> str | None:
    """把段落样本归类到模板样式键; 无法判断返回 None。"""
    if _is_toc_style(sample.style_id):
        return None
    text = _compact(sample.text)
    if not text:
        return None

    # 封面信息表: 只把"值"单元格当作 cover_meta 样本 (标签是黑体, 值才是正文样式)
    if sample.in_table:
        return "cover_meta" if in_cover and sample.cell_index > 0 else None

    # 只把"标题型"特殊章节当作样式样本: 参考文献/致谢/附录的标题按一级标题
    # 处理(与章节标题同格式), 其正文条目样式分别由 references / body 承担。
    key = _SPECIAL_TITLES.get(_norm(text))
    if key in ("abstract", "english_abstract", "toc"):
        return key
    if text.startswith(("关键词", "关键字")):
        return "keywords"
    if re.match(r"^(key\s*words?|keywords)\s*[:：]", text, re.I):
        return "english_keywords"
    if re.match(r"^(图|fig\.?|figure)\s*\d", text, re.I):
        return "figure_caption"
    if re.match(r"^(表|tab\.?|table)\s*\d", text, re.I):
        return "table_caption"
    if re.match(r"^\[\d+\]", text):
        return "references"      # 参考文献条目

    if in_cover:
        if re.match(r"^题目\s*[：:]", text):
            return "cover_title"
        if re.match(r"^[\u4e00-\u9fa5A-Za-z]{1,8}\s*[：:]", text):
            return "cover_meta"
        return "cover_title" if style_key == "cover_title" else None

    if style_key in ("heading1", "heading2", "heading3"):
        return style_key
    if _HEADING_TEXT_RE.match(text):
        return "heading1"
    if not sample.style_id and _NUMERIC_HEADING_RE.match(text) and len(text) <= 40:
        depth = text.split()[0].count(".")
        return "heading2" if depth == 1 else "heading3"
    if style_key == "body" and len(text) >= 6:
        return "body"
    if not sample.style_id and len(text) >= 6:
        return "body"
    return None


def _mode(values: list[str]) -> str | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def _collect_all(candidates: list[dict[str, str]], dotted: str) -> list[str]:
    """含"未指定"的取值列表。

    用于判断"模板多数情况下没写这个属性"——此时应回落到内置规范值,
    而不是被少数几个特例(如红字说明段落)带偏。
    """
    return [str(c.get(dotted) or "") for c in candidates]


def _mode_all(candidates: list[dict[str, str]], dotted: str) -> str:
    """取众数(含未指定); 未指定占多数时返回空串。"""
    return _mode(_collect_all(candidates, dotted)) or ""


def _aggregate(candidates: list[dict[str, str]], key: str,
               seed: TextStyle | None = None) -> TextStyle:
    """把同类候选属性聚合为一个 TextStyle (逐字段取众数)。

    模板没写到的字段沿用该键的内置规范值(seed 为 None 时)。
    """
    ts = (seed if seed is not None else DEFAULT_STYLES.get(key, TextStyle())).model_copy()

    size = _mode_all(candidates, "font.size_half_points")
    if size:
        try:
            ts.size_pt = round(float(size) / 2.0, 2)
        except ValueError:
            pass

    east_asia = _mode_all(candidates, "font.eastAsia")
    if east_asia:
        ts.font_zh = east_asia
    latin = _mode_all(candidates, "font.ascii") or _mode_all(candidates, "font.hAnsi")
    if latin:
        ts.font_en = latin

    # 加粗/斜体/下划线: 继承链已展开, 未出现即表示不加粗
    ts.bold = _mode_all(candidates, "font.bold") == "true"
    ts.italic = _mode_all(candidates, "font.italic") == "true"
    underline = _mode_all(candidates, "font.underline")
    ts.underline = bool(underline) and underline not in ("none", "0", "false")

    color = _mode_all(candidates, "font.color")
    if color:
        ts.color = color

    align = _mode_all(candidates, "paragraph.align")
    if align and align.lower() in _ALIGN_MAP:
        ts.align = _ALIGN_MAP[align.lower()]

    line = _mode_all(candidates, "paragraph.spacing.line")
    if line:
        try:
            value = float(line)
            rule = (_mode_all(candidates, "paragraph.spacing.line_rule") or "auto").lower()
            if rule in ("exact", "atleast"):
                ts.line_spacing = round(value / 20.0, 2)     # 固定值/最小值: 磅
                ts.line_spacing_rule = "exact" if rule == "exact" else "atLeast"
            else:
                ts.line_spacing = round(value / 240.0, 2)    # 倍数
                ts.line_spacing_rule = "auto"
        except ValueError:
            pass
    elif key in _TITLE_KEYS:
        ts.line_spacing = 1.0            # 标题没写行距就是单倍, 不套用正文的 1.5

    for dotted, field in (("paragraph.spacing.before", "space_before_pt"),
                          ("paragraph.spacing.after", "space_after_pt")):
        # 段前段后完全以模板为准: Word 没写就是 0, 不用内置默认值(那会凭空多出间距)
        value = _mode_all(candidates, dotted)
        setattr(ts, field, round(float(value) / 20.0, 1) if value else 0.0)

    indent_chars = _mode_all(candidates, "paragraph.indent.first_line_chars")
    if indent_chars:
        try:
            ts.first_line_indent_chars = round(float(indent_chars) / 100.0, 2)
        except ValueError:
            pass
    else:
        indent_twips = _mode_all(candidates, "paragraph.indent.first_line")
        if indent_twips:
            try:
                ts.first_line_indent_chars = round(float(indent_twips) / 20.0 / ts.size_pt, 2)
            except (ValueError, ZeroDivisionError):
                pass
        elif key in _TITLE_KEYS:
            ts.first_line_indent_chars = 0.0   # 标题类元素默认不缩进

    if _mode_all(candidates, "paragraph.page_break_before") == "true":
        ts.page_break_before = True

    return ts


# ---------------------------------------------------------------------------
# 封面元信息字段: 从封面区域的"标签："段落与封面信息表识别
# ---------------------------------------------------------------------------

# (标签正则, 字段 key, 显示名)
_META_LABELS: list[tuple[str, str, str]] = [
    (r"学生学号", "student_id", "学号"),
    (r"学生姓名", "author", "姓名"),
    (r"学号", "student_id", "学号"),
    (r"姓名", "author", "姓名"),
    (r"指导教师|导师", "advisor", "指导教师"),
    (r"论文题目|题目", "title", "题目"),
    (r"学院", "college", "学院"),
    (r"专业", "major", "专业"),
    (r"班级", "class_name", "班级"),
    (r"学校名称|学校", "school", "学校名称"),
    (r"日期", "date", "日期"),
]
_META_LABEL_RE = re.compile(
    r"(学生学号|学生姓名|学号|姓名|指导教师|导师|论文题目|题目|学院|专业|班级|学校名称|学校|日期)\s*[：:]"
)
_META_LABEL_TEXT = {key: label for _, key, label in _META_LABELS}


def _meta_label_text(key: str) -> str:
    """字段 key → 封面上的标签文本(带全角冒号)。"""
    return f"{_META_LABEL_TEXT.get(key, key)}："


def _label_token(token: str) -> tuple[str, str] | None:
    """标签文本 → (字段 key, 显示名)。"""
    for pattern, key, label in _META_LABELS:
        if re.fullmatch(pattern, token):
            return key, label
    return None


def _paragraph_style_id(p: etree._Element) -> str:
    ppr = p.find(qn("w:pPr"))
    if ppr is None:
        return ""
    pstyle = ppr.find(qn("w:pStyle"))
    return (pstyle.get(qn("w:val")) or "") if pstyle is not None else ""


def _table_cell_text(cell: etree._Element) -> str:
    paragraphs = [
        _visible_paragraph_text(paragraph).strip()
        for paragraph in cell.findall(qn("w:p"))
    ]
    return "\n".join(value for value in paragraphs if value)


def _table_cell_span(cell: etree._Element) -> tuple[int, str]:
    properties = cell.find(qn("w:tcPr"))
    if properties is None:
        return 1, ""
    grid_span = properties.find(qn("w:gridSpan"))
    try:
        col_span = max(1, int(grid_span.get(qn("w:val")))) if grid_span is not None else 1
    except (TypeError, ValueError):
        col_span = 1
    vertical_merge = properties.find(qn("w:vMerge"))
    merge_value = ""
    if vertical_merge is not None:
        merge_value = vertical_merge.get(qn("w:val")) or "continue"
    return col_span, merge_value


def extract_document_structure(docx_path: str | Path) -> dict[str, object]:
    """Extract safe structural metadata for generic Word templates.

    The source DOCX remains the formatting authority. This projection is
    intentionally limited to table dimensions, merge markers, and a small
    text preview so the review UI can show what was recognized without
    serializing the original XML into the candidate.
    """
    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path, "r") as zf:
        document_el = load_part(zf, "word/document.xml")
        styles_el = load_part(zf, "word/styles.xml")
    if document_el is None:
        return {"paragraph_count": 0, "table_count": 0, "tables": []}

    body = document_el.find(qn("w:body"))
    if body is None:
        return {"paragraph_count": 0, "table_count": 0, "tables": []}

    id_to_name = style_id_to_name(styles_el) if styles_el is not None else {}
    paragraph_count = 0
    current_section = ""
    tables: list[dict[str, object]] = []
    for child in body:
        if child.tag == qn("w:p"):
            text = _visible_paragraph_text(child).strip()
            if text:
                paragraph_count += 1
            style_key = _style_key(_paragraph_style_id(child), id_to_name)
            if text and (_is_structural_heading_text(text) or style_key in {"heading1", "heading2", "heading3"}):
                current_section = text
            continue
        if child.tag != qn("w:tbl"):
            continue

        grid = child.find(qn("w:tblGrid"))
        grid_columns = len(grid.findall(qn("w:gridCol"))) if grid is not None else 0
        row_projections: list[list[dict[str, object]]] = []
        non_empty_cells = 0
        has_merged_cells = False
        max_columns = grid_columns
        for row in child.findall(qn("w:tr")):
            row_projection: list[dict[str, object]] = []
            logical_columns = 0
            for cell in row.findall(qn("w:tc")):
                text = _table_cell_text(cell)
                col_span, vertical_merge = _table_cell_span(cell)
                logical_columns += col_span
                has_merged_cells = has_merged_cells or col_span > 1 or bool(vertical_merge)
                if text:
                    non_empty_cells += 1
                row_projection.append({
                    "text": text,
                    "col_span": col_span,
                    "vertical_merge": vertical_merge,
                })
            max_columns = max(max_columns, logical_columns)
            row_projections.append(row_projection)

        tables.append({
            "index": len(tables) + 1,
            "rows": len(row_projections),
            "cols": max_columns,
            "grid_columns": grid_columns,
            "cell_count": sum(len(row) for row in row_projections),
            "non_empty_cells": non_empty_cells,
            "has_merged_cells": has_merged_cells,
            "section_title": current_section,
            "cells": [[str(cell["text"]) for cell in row] for row in row_projections],
            "preview": [
                [
                    {
                        "text": str(cell["text"])[:120],
                        "col_span": cell["col_span"],
                        "vertical_merge": cell["vertical_merge"],
                    }
                    for cell in row[:8]
                ]
                for row in row_projections[:6]
            ],
        })

    return {
        "paragraph_count": paragraph_count,
        "table_count": len(tables),
        "tables": tables,
    }


def _style_from_attrs(attrs: dict[str, str]) -> TextStyle:
    """单个属性字典 → TextStyle (不做众数聚合, 缺省用 TextStyle 默认值)。"""
    return _aggregate([attrs], "", seed=TextStyle())


def _text_runs(p: etree._Element) -> list[tuple[str, dict[str, str]]]:
    """段落里的 (文本, run 属性) 列表。"""
    out: list[tuple[str, dict[str, str]]] = []
    for run in p.iter(qn("w:r")):
        text = "".join(t.text or "" for t in run.findall(qn("w:t")))
        if not text:
            continue
        rpr = run.find(qn("w:rPr"))
        out.append((text, run_props(rpr) if rpr is not None else {}))
    return out


def _is_instruction_run(props: dict[str, str]) -> bool:
    """模板里红色文字是写给学生的填写说明, 不应进入导出的封面。"""
    return str(props.get("font.color", "")).upper() == "FF0000"


def _paragraph_base_attrs(p: etree._Element, style_table: dict[str, dict[str, str]],
                          default_style_id: str) -> dict[str, str]:
    """段落生效属性: 样式链 + 段落直接格式。"""
    attrs = dict(style_table.get(_paragraph_style_id(p) or default_style_id, {}))
    ppr = p.find(qn("w:pPr"))
    if ppr is not None:
        attrs.update(para_props(ppr))
    return attrs


def _cell_base_attrs(tc: etree._Element, style_table: dict[str, dict[str, str]],
                     default_style_id: str) -> dict[str, str]:
    """单元格生效属性 (取单元格里第一个段落)。"""
    first = tc.find(qn("w:p"))
    if first is None:
        return {}
    return _paragraph_base_attrs(first, style_table, default_style_id)


def _visible_spans(p: etree._Element) -> tuple[str, list[tuple[str, dict[str, str], int, int]]]:
    """去掉填写说明后的 (拼接文本, [(run 文本, 属性, 起始, 结束)])。"""
    parts: list[str] = []
    spans: list[tuple[str, dict[str, str], int, int]] = []
    offset = 0
    for text, props in _text_runs(p):
        if _is_instruction_run(props):
            continue
        spans.append((text, props, offset, offset + len(text)))
        parts.append(text)
        offset += len(text)
    return "".join(parts), spans


def _compact_map(text: str) -> tuple[str, list[int]]:
    """去空白后的文本 + 每个字符在原文中的下标(标签常被拆到多个 run)。"""
    chars: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        mapping.append(index)
    return "".join(chars), mapping


def _span_index_at(spans: list[tuple[str, dict[str, str], int, int]], offset: int) -> int:
    for index, (_, _, start, end) in enumerate(spans):
        if start <= offset < end:
            return index
    return -1


def _locate_labels(p: etree._Element, base: dict[str, str]) -> list[tuple[str, TextStyle, int]]:
    """在段落里定位所有"标签："。

    Returns:
        [(字段 key, 标签样式, 标签结束位置)] —— 位置用于取后面"值"的样式。
    """
    raw, spans = _visible_spans(p)
    if not raw.strip():
        return []
    compact, mapping = _compact_map(raw)
    out: list[tuple[str, TextStyle, int]] = []
    for match in _META_LABEL_RE.finditer(compact):
        info = _label_token(match.group(1))
        if info is None:
            continue
        index = _span_index_at(spans, mapping[match.start()])
        props = spans[index][1] if index >= 0 else (spans[0][1] if spans else {})
        out.append((info[0], _style_from_attrs({**base, **props}), mapping[match.end() - 1] + 1))
    return out


def _locate_label(p: etree._Element, base: dict[str, str]) -> tuple[str, TextStyle, int] | None:
    """取段落里最后一个标签(表格单元格里标签常跟在说明文字之后)。"""
    located = _locate_labels(p, base)
    return located[-1] if located else None


_DATE_TEXT_RE = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月(\s*\d{1,2}\s*日)?$")


def _cover_block_from_paragraph(p: etree._Element, style_table: dict[str, dict[str, str]],
                                default_style_id: str) -> list[CoverBlock]:
    """封面段落 → 版式元素(说明文字已剔除); 一段含多个字段时拆成多行。"""
    base = _paragraph_base_attrs(p, style_table, default_style_id)
    raw, spans = _visible_spans(p)
    text = raw.strip()
    if not text:
        return [CoverBlock(kind="spacer", style=_style_from_attrs(base))]

    located = _locate_labels(p, base)
    if not located:
        style = _style_from_attrs({**base, **(spans[0][1] if spans else {})})
        # 封面上的"2026年5月"这类无标签日期, 认成日期字段(模板上不带标签)
        if _DATE_TEXT_RE.match(_compact(text)):
            return [CoverBlock(kind="field", fields=[CoverField(
                label="", key="date",
                label_style=style, value_style=style,
            )])]
        return [CoverBlock(kind="text", text=text, style=style)]

    blocks: list[CoverBlock] = []
    for key, label_style, label_end in located:
        value_props: dict[str, str] = {}
        for _, props, start, _end in spans:
            if start >= label_end:
                value_props = props
                break
        blocks.append(CoverBlock(kind="field", fields=[CoverField(
            label=_meta_label_text(key),
            key=key,
            label_style=label_style,
            value_style=_style_from_attrs({**base, **value_props}) if value_props else label_style,
        )]))
    return blocks


def _cover_block_from_table(tbl: etree._Element, style_table: dict[str, dict[str, str]],
                            default_style_id: str) -> CoverBlock | None:
    """封面信息表 → 版式元素(标签列 + 取值列)。"""
    fields: list[CoverField] = []
    last_value_style: TextStyle | None = None
    for row in tbl.findall(qn("w:tr")):
        cells = row.findall(qn("w:tc"))
        if len(cells) < 2:
            continue
        located = _locate_label(cells[0], _cell_base_attrs(cells[0], style_table, default_style_id))
        if located is None:
            continue
        key, label_style, _ = located

        value_spans = [span for span in _visible_spans(cells[1])[1] if span[0].strip()]
        if value_spans:
            value_style = _style_from_attrs({
                **_cell_base_attrs(cells[1], style_table, default_style_id),
                **value_spans[0][1],
            })
            last_value_style = value_style
        else:
            # 取值为空(模板留白)时沿用上一行的值样式
            value_style = last_value_style or label_style

        fields.append(CoverField(
            label=_meta_label_text(key), key=key,
            label_style=label_style, value_style=value_style,
        ))

    if not fields:
        return None
    grid = tbl.find(qn("w:tblGrid"))
    widths: list[float] = []
    if grid is not None:
        for col in grid.findall(qn("w:gridCol")):
            value = col.get(qn("w:w"))
            if value:
                widths.append(round(float(value) / 567.0, 2))   # twips → cm
    return CoverBlock(kind="table", fields=fields, column_widths_cm=widths)


def extract_cover_blocks(docx_path: str | Path) -> list[CoverBlock]:
    """识别封面版式: 静态文字行 / 字段行 / 字段信息表 / 空行, 按文档顺序。

    遇到第一个正文起点(摘要/Abstract/目录/章节标题)即停止。
    """
    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path, "r") as zf:
        document_el = load_part(zf, "word/document.xml")
        styles_el = load_part(zf, "word/styles.xml")
    if document_el is None:
        return []
    body = document_el.find(qn("w:body"))
    if body is None:
        return []

    style_table = build_style_table(styles_el)
    default_style_id = default_paragraph_style_id(styles_el)
    id_to_name = style_id_to_name(styles_el)
    has_ordinal_heading = any(
        _is_ordinal_heading(_visible_paragraph_text(p))
        for p in body.findall(qn("w:p"))
    )

    blocks: list[CoverBlock] = []
    seen_ordinal_heading = False
    for child in body:
        if child.tag == qn("w:tbl"):
            block = _cover_block_from_table(child, style_table, default_style_id)
            if block is not None:
                blocks.append(block)
            continue
        if child.tag != qn("w:p"):
            continue
        text = _visible_paragraph_text(child).strip()
        style_id = _paragraph_style_id(child)
        probe = ParaSample(index=0, text=text, style_id=style_id)
        if _is_ordinal_heading(text):
            seen_ordinal_heading = True
        if _is_content_start(
            probe,
            _style_key(style_id, id_to_name),
            allow_style_heading=not has_ordinal_heading or seen_ordinal_heading,
        ):
            break
        blocks.extend(_cover_block_from_paragraph(child, style_table, default_style_id))
    return blocks


def extract_meta_fields(docx_path: str | Path) -> list[MetaField]:
    """从封面版式里提取元信息字段(供写作界面填写)。"""
    found: dict[str, MetaField] = {}
    for block in extract_cover_blocks(docx_path):
        for field in block.fields:
            if not field.key or field.key in found:
                continue
            found[field.key] = MetaField(
                key=field.key,
                label=field.label.rstrip("：:").strip() or _META_LABEL_TEXT.get(field.key, field.key),
                required=(field.key == "title"),
            )
    return list(found.values())


# ---------------------------------------------------------------------------
# 章节结构提取: 从 docx 的 Heading 段落识别章节树
# ---------------------------------------------------------------------------

_HEADING_STYLE_IDS = {
    "Heading1", "Heading 1", "标题1", "标题 1", "1",
    "Heading2", "Heading 2", "标题2", "标题 2", "2",
    "Heading3", "Heading 3", "标题3", "标题 3", "3",
}


def extract_section_tree(docx_path: str | Path) -> list[dict]:
    """从 docx 的 Heading 1/2/3 段落提取章节树。

    Returns:
        TemplateSection dict 列表: [{key, type, title, required, children}, ...]
        识别不到标题段落时返回空列表。
    """
    import re
    import zipfile

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    with zipfile.ZipFile(docx_path) as zf:
        if "word/document.xml" not in zf.namelist():
            return []
        root = parse_xml(zf.read("word/document.xml"))
        styles_el = load_part(zf, "word/styles.xml")

    id_to_name = style_id_to_name(styles_el) if styles_el is not None else {}

    body = root.find(f"{{{W}}}body")
    if body is None:
        return []

    # 收集 (style_id, text), 同时支持正文中的中文/数字章节标题。
    items: list[tuple[str, str]] = []
    has_ordinal_heading = any(
        _is_ordinal_heading(_visible_paragraph_text(p))
        for p in body.findall(f"{{{W}}}p")
    )
    seen_ordinal_heading = False
    toc_style_ids = {"TOC1", "TOC2", "TOC3", "TOC4", "TOC5", "TOC6", "TOC7", "TOC8", "TOC9", "toc 1", "toc 2", "toc 3"}
    in_toc = False
    for p in body.findall(f"{{{W}}}p"):
        style_id = None
        ppr = p.find(f"{{{W}}}pPr")
        if ppr is not None:
            pstyle = ppr.find(f"{{{W}}}pStyle")
            if pstyle is not None:
                style_id = pstyle.get(f"{{{W}}}val")
        style_name = id_to_name.get(style_id or "", "")
        if style_id in toc_style_ids or _is_toc_style(style_name):
            continue
        raw_text = _visible_paragraph_text(p)
        text = raw_text.strip()
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        lower = compact.lower()
        # 部分模板把目录条目也保存为普通 Heading 样式。目录页码前的
        # 制表符是它与正文同名标题之间可靠的结构边界。
        if in_toc and "\t" in raw_text:
            continue
        if compact in {"摘要", "中文摘要"}:
            items.append(("special-abstract", text))
        elif lower in {"abstract", "englishabstract", "英文摘要", "abstractenglish"}:
            items.append(("special-english-abstract", text))
        elif compact.startswith("关键词") or lower.startswith(("keywords", "keyword", "keyterms", "indexterms")):
            # 依据 GB/T 7713 规范，关键词是摘要下方的内嵌段落，不作为独立章节树节点进入目录
            continue
        elif compact in {"目录", "toc", "contents", "tableofcontents"} or lower in {"toc", "contents", "tableofcontents"} or style_id == "TOCHeading":
            items.append(("special-toc", text))
            in_toc = True
        elif compact == "参考文献":
            items.append(("special-references", text))
        elif compact in {"致谢", "谢辞"}:
            items.append(("special-acknowledgement", text))
        elif compact == "附录":
            items.append(("special-appendix", text))
        elif _is_ordinal_heading(text) or re.match(
            r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节]", text, re.I
        ) or re.match(r"^\d+(?:\.\d+){1,2}\s*\S", text):
            items.append(("text-heading", text))
            seen_ordinal_heading = seen_ordinal_heading or _is_ordinal_heading(text)
        elif style_id in _HEADING_STYLE_IDS or _norm(style_name) in {
                "heading1", "heading2", "heading3",
                "标题1", "标题2", "标题3"}:
            # 用显示名参与后续层级判断。中文模板经常使用 2/17 这类
            # 内部 styleId, 不能把它们直接当成标题级别。
            if not has_ordinal_heading or seen_ordinal_heading:
                items.append((style_name or style_id, text))

    if not items:
        return []

    # 构建树
    level_map = {"1": 1, "2": 2, "3": 3}
    roots: list[dict] = []
    stack: list[dict] = []   # (level, node) 栈
    ch_counter = {"1": 0, "2": 0, "3": 0}

    for style_id, text in items:
        if style_id.startswith("special-"):
            special_type = {
                "special-abstract": ("abstract", True),
                "special-english-abstract": ("english_abstract", True),
                "special-keywords": ("keywords", True),
                "special-english-keywords": ("english_keywords", True),
                "special-toc": ("toc", True),
                "special-references": ("references", True),
                "special-acknowledgement": ("acknowledgement", False),
                "special-appendix": ("appendix", False),
            }[style_id]
            roots.append({
                "key": special_type[0],
                "type": special_type[0],
                "title": text,
                "required": special_type[1],
                "children": [],
            })
            continue

        normalized = style_id.replace(" ", "").lower()
        if normalized in {"1", "heading1", "标题1"}:
            level = 1
        elif normalized in {"2", "heading2", "标题2"}:
            level = 2
        elif normalized in {"3", "heading3", "标题3"}:
            level = 3
        elif style_id == "text-heading":
            level = 1 if (
                _is_ordinal_heading(text)
                or re.match(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节]", text)
            ) else 2
        else:
            continue

        node = {
            "key": "",
            "type": f"heading{level}",
            "title": text,
            "required": level == 1,
            "children": [],
        }
        while stack and stack[-1][0] >= level:
            stack.pop()
        ch_counter[str(level)] += 1
        for n in range(level + 1, 4):
            ch_counter[str(n)] = 0

        parts = [str(ch_counter["1"])]
        for n in range(2, level + 1):
            parts.append(str(ch_counter[str(n)]))
        node["key"] = "ch" + "_".join(parts)

        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((level, node))

    return roots
