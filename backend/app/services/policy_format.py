"""规范中的排版约束解析与标准优先样式合并。"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


STYLE_FIELDS = {
    "font_zh",
    "font_en",
    "size_pt",
    "bold",
    "italic",
    "underline",
    "align",
    "line_spacing",
    "line_spacing_rule",
    "space_before_pt",
    "space_after_pt",
    "first_line_indent_chars",
    "hanging_indent_cm",
    "color",
    "highlight",
    "vert_align",
    "page_break_before",
    "number_title_gap",
}

LAYOUT_FIELDS = {
    "page_width_cm",
    "page_height_cm",
    "top_margin_cm",
    "bottom_margin_cm",
    "left_margin_cm",
    "right_margin_cm",
    "header_distance_cm",
    "footer_distance_cm",
}

STYLE_ALIASES = {
    "正文": "body",
    "正文内容": "body",
    "正文段落": "body",
    "中文摘要": "abstract_body",
    "中文摘要正文": "abstract_body",
    "摘要正文": "abstract_body",
    "英文摘要": "english_abstract_body",
    "英文摘要正文": "english_abstract_body",
    "英文摘要标题": "english_abstract",
    "摘要标题": "abstract",
    "目录": "toc",
    "章": "heading1",
    "一级标题": "heading1",
    "第一级": "heading1",
    "节": "heading2",
    "二级标题": "heading2",
    "第二级": "heading2",
    "条": "heading3",
    "三级标题": "heading3",
    "第三级": "heading3",
    "项": "body",
    "四级标题": "body",
    "第四级": "body",
    "关键词": "keywords",
    "英文关键词": "english_keywords",
    "参考文献": "references",
    "致谢": "acknowledgement",
    "附录": "appendix",
    "图题": "figure_caption",
    "插图题注": "figure_caption",
    "表题": "table_caption",
    "表格题注": "table_caption",
    "表格": "table_cell",
    "表内文字": "table_cell",
    "代码": "code",
}

_SIZE_POINTS = {
    "初号": 42.0,
    "小初": 36.0,
    "一号": 26.0,
    "小一": 24.0,
    "二号": 22.0,
    "小二": 18.0,
    "三号": 16.0,
    "小三": 15.0,
    "四号": 14.0,
    "小四": 12.0,
    "五号": 10.5,
    "小五": 9.0,
    "六号": 7.5,
}
_SIZE_RE = re.compile("|".join(re.escape(value) + r"(?:号)?" for value in _SIZE_POINTS), re.I)
_FONT_NAMES = ("Times New Roman", "宋体", "黑体", "仿宋", "楷体", "隶书")
_FORMAT_TERM_RE = re.compile(
    r"字体|字号|行距|首行缩进|缩进|对齐|分页|另起一页|章前|换页|页边距|页眉|页脚|页码|段前|段后|"
    r"宋体|黑体|仿宋|楷体|隶书|Times\s*New\s*Roman|小二|小三|小四|小五|四号|五号|六号|"
    r"单倍|两端对齐|居中|居右|左对齐|右对齐|左顶格|顶格|加粗|斜体|下划线|磅|(?<![A-Za-z])(?:pt|cm)(?![A-Za-z])|倍行距",
    re.IGNORECASE,
)


def is_format_requirement(value: Any) -> bool:
    """Return whether a requirement description contains explicit layout language."""
    return bool(_FORMAT_TERM_RE.search(str(value or "")))


def _style_key(value: Any) -> str:
    raw = str(value or "").strip()
    return STYLE_ALIASES.get(raw, raw)


def normalize_format_overrides(value: Any) -> dict[str, dict[str, Any]]:
    """Normalize AI/user supplied style constraints to TextStyle field names."""
    if not isinstance(value, dict):
        return {}
    source = value.get("styles") if isinstance(value.get("styles"), dict) else value
    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_fields in source.items():
        key = _style_key(raw_key)
        if not key or key in {"page", "page_layout", "document"} or not isinstance(raw_fields, dict):
            continue
        fields = {field: deepcopy(field_value) for field, field_value in raw_fields.items() if field in STYLE_FIELDS}
        if (key not in {"figure_caption", "table_caption"}
                or fields.get("number_title_gap") not in (None, "one_character")):
            fields.pop("number_title_gap", None)
        if not fields:
            continue
        if "align" in fields:
            fields["align"] = {
                "左对齐": "left", "居左": "left", "left": "left",
                "居中": "center", "center": "center",
                "右对齐": "right", "居右": "right", "right": "right",
                "两端对齐": "justify", "justify": "justify",
            }.get(str(fields["align"]), str(fields["align"]))
        if key in {"english_abstract", "english_abstract_body", "english_keywords"}:
            if "font_en" in fields and "font_zh" not in fields:
                fields["font_zh"] = fields["font_en"]
            elif "font_zh" in fields and "font_en" not in fields:
                fields["font_en"] = fields["font_zh"]
        for field in ("size_pt", "line_spacing", "space_before_pt", "space_after_pt",
                      "first_line_indent_chars", "hanging_indent_cm"):
            if field in fields:
                try:
                    fields[field] = float(fields[field])
                except (TypeError, ValueError):
                    fields.pop(field, None)
        normalized.setdefault(key, {}).update(fields)
    return normalized


def normalize_layout_overrides(value: Any) -> dict[str, float]:
    """Normalize document-level page settings without treating them as text styles."""
    if not isinstance(value, dict):
        return {}
    source = value.get("layout") if isinstance(value.get("layout"), dict) else value
    aliases = {
        "页宽": "page_width_cm", "页高": "page_height_cm",
        "上边距": "top_margin_cm", "下边距": "bottom_margin_cm",
        "左边距": "left_margin_cm", "右边距": "right_margin_cm",
        "页眉距边距": "header_distance_cm", "页脚距边距": "footer_distance_cm",
    }
    result: dict[str, float] = {}
    for raw_key, raw_value in source.items():
        key = aliases.get(str(raw_key), str(raw_key))
        if key not in LAYOUT_FIELDS:
            continue
        try:
            result[key] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return result


def merge_format_overrides(*values: Any) -> dict[str, dict[str, Any]]:
    """Merge constraints in precedence order; later values are authoritative."""
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        for style_key, fields in normalize_format_overrides(value).items():
            result.setdefault(style_key, {}).update(deepcopy(fields))
    return result


def format_override_count(value: Any) -> int:
    return sum(len(fields) for fields in normalize_format_overrides(value).values())


def _size_points(clause: str) -> float | None:
    match = _SIZE_RE.search(clause)
    if not match:
        return None
    label = match.group(0)
    label = label if label in _SIZE_POINTS else label[:-1]
    return _SIZE_POINTS.get(label)


def _number(pattern: str, clause: str) -> float | None:
    match = re.search(pattern, clause, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _style_from_clause(clause: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    size = _size_points(clause)
    if size is not None:
        values["size_pt"] = size

    if re.search(r"Times\s*New\s*Roman", clause, re.IGNORECASE):
        values["font_en"] = "Times New Roman"
    for font in _FONT_NAMES[1:]:
        if re.search(re.escape(font), clause, re.IGNORECASE):
            values["font_zh"] = font
    if re.search(r"不加粗|不要加粗", clause):
        values["bold"] = False
    elif re.search(r"加粗", clause):
        values["bold"] = True
    if re.search(r"不使用斜体|不斜体", clause):
        values["italic"] = False
    elif re.search(r"斜体", clause):
        values["italic"] = True
    if re.search(r"不使用下划线|无下划线", clause):
        values["underline"] = False
    elif re.search(r"下划线", clause):
        values["underline"] = True

    if re.search(r"两端对齐", clause):
        values["align"] = "justify"
    elif re.search(r"居中", clause):
        values["align"] = "center"
    elif re.search(r"居右|右对齐", clause):
        values["align"] = "right"
    elif re.search(r"居左|左对齐", clause):
        values["align"] = "left"

    line_spacing = _number(r"多倍行距\s*(\d+(?:\.\d+)?)", clause)
    if line_spacing is None and re.search(r"单倍行距", clause):
        line_spacing = 1.0
    if line_spacing is None:
        line_spacing = _number(r"(\d+(?:\.\d+)?)\s*倍行距", clause)
    if line_spacing is not None:
        values["line_spacing"] = line_spacing
        values["line_spacing_rule"] = "auto"

    indent = _number(r"(?:首行|左)\s*缩进\s*(\d+(?:\.\d+)?)\s*(?:个)?(?:中文)?字符", clause)
    if indent is not None:
        values["first_line_indent_chars"] = indent
    elif re.search(r"无首行缩进|左顶格|顶格起行|序号顶格|顶格", clause):
        values["first_line_indent_chars"] = 0.0

    before = _number(r"段前\s*(\d+(?:\.\d+)?)\s*行", clause)
    after = _number(r"段后\s*(\d+(?:\.\d+)?)\s*行", clause)
    base_size = float(values.get("size_pt", 12.0))
    if before is not None:
        values["space_before_pt"] = before * base_size
    if after is not None:
        values["space_after_pt"] = after * base_size

    if re.search(r"章前分页|另起一页|分页前开始", clause):
        values["page_break_before"] = True
    return values


def _window(text: str, start: str, end: str | None = None) -> str:
    start_match = re.search(start, text, re.IGNORECASE)
    if not start_match:
        return ""
    tail = text[start_match.end():]
    if not end:
        return tail
    end_match = re.search(end, tail, re.IGNORECASE)
    return tail[:end_match.start()] if end_match else tail


def _add(overrides: dict[str, dict[str, Any]], key: str, clause: str) -> None:
    values = _style_from_clause(clause)
    if values:
        overrides.setdefault(key, {}).update(values)


def extract_image_font_size_limit(text: str) -> str:
    """Recognize the confirmed mandatory figure-text limit in source prose."""
    compact = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text or "")
    match = re.search(
        r"(?:^|[。！？；;])\s*(图中标注符号(?:和|与)?文字(?:的)?字号不大于图题(?:的)?字号)",
        compact,
    )
    return match.group(1) if match else ""


def extract_explicit_body_chinese_font(text: str) -> str:
    """Extract a single unambiguous, standalone body Chinese-font sentence."""
    fonts = set()
    for clause in re.split(r"[。！？；;\n]", str(text or "")):
        match = re.fullmatch(r"正文中文字体必须(?:使用|采用)(宋体|黑体|仿宋|楷体)", clause.strip())
        if match:
            fonts.add(match.group(1))
    return next(iter(fonts)) if len(fonts) == 1 else ""


def extract_format_overrides(text: str) -> dict[str, dict[str, Any]]:
    """Extract explicit style clauses from common Chinese thesis guidelines.

    AI supplied structured overrides can supplement this result. The source text is
    the authority here, so callers should merge this result after model output.
    """
    source = str(text or "")
    # PDF 提取常把一个中文词拆成“字\n符”，或把 Times New Roman 拆成多行。
    # 只修复这类断行，不改动上传原文本身，原文仍由导入服务完整保存。
    source = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", source)
    source = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z])", " ", source)
    normalized = re.sub(r"[ \t\r\n]+", " ", source).strip()
    if not normalized:
        return {}
    overrides: dict[str, dict[str, Any]] = {}

    front = _window(normalized, r"前置部分格式要求", r"主体部分格式要求")
    abstract = _window(front, r"1\s*[\.、]\s*中文摘要页", r"2\s*[\.、]\s*英文摘要页")
    _add(overrides, "abstract", _window(abstract, r"中文摘要标题", r"中文摘要内容"))
    abstract_body = _window(abstract, r"中文摘要内容和中文关键词", r"中文摘要内容后")
    _add(overrides, "abstract_body", abstract_body)
    _add(overrides, "keywords", abstract_body)

    english_abstract = _window(front, r"2\s*[\.、]\s*英文摘要页", r"3\s*[\.、]\s*目录")
    _add(overrides, "english_abstract", _window(english_abstract, r"英文摘要标题", r"英文摘要内容"))
    english_body = _window(english_abstract, r"英文摘要内容和英文关键词", r"英文摘要内容后")
    _add(overrides, "english_abstract_body", english_body)
    _add(overrides, "english_keywords", english_body)

    headings = _window(normalized, r"标题要求", r"2\.正文要求")
    heading_markers = [
        ("heading1", r"第一级\s*[（(]\s*章\s*[）)]", r"第二级\s*[（(]\s*节"),
        ("heading2", r"第二级\s*[（(]\s*节", r"第三级\s*[（(]\s*条"),
        ("heading3", r"第三级\s*[（(]\s*条", r"第四级\s*[（(]\s*项"),
    ]
    for key, start, end in heading_markers:
        _add(overrides, key, _window(headings, start, end))
    if re.search(r"每章\s*应另起一页", headings):
        overrides.setdefault("heading1", {})["page_break_before"] = True

    body = _window(normalized, r"2\.正文要求", r"3\.图表及公式格式要求")
    _add(overrides, "body", _window(body, r"字体字号", r"正文页眉"))

    ending = _window(normalized, r"结尾部分格式要求")
    references = _window(ending, r"1\.参考文献", r"2\.(?:结束语(?:（致谢）)?|致谢)")
    _add(overrides, "references", _window(references, r"中文字体采用", r"序号左顶格"))
    if re.search(r"序号(?:左)?顶格", references):
        overrides.setdefault("references", {})["first_line_indent_chars"] = 0.0
    acknowledgement = _window(ending, r"2\.(?:结束语(?:（致谢）)?|致谢)", r"3\.附录")
    _add(overrides, "acknowledgement", _window(acknowledgement, r"中文字体采用"))
    if references:
        overrides.setdefault("references", {})["page_break_before"] = True
    if acknowledgement:
        overrides.setdefault("acknowledgement", {})["page_break_before"] = True
    if re.search(r"3\.附录：.*?标题采用一级标题格式", ending):
        overrides.setdefault("appendix", {})["page_break_before"] = True

    figures = _window(normalized, r"3\.图表及公式格式要求", r"（四）毕业设计|\(四\)毕业设计")
    _add(overrides, "figure_caption", _window(figures, r"图题用", r"须全文统一|[。；;]"))
    _add(overrides, "table_caption", _window(figures, r"表题(?:用|[，,]\s*用)", r"须全文统一|[。；;]"))
    _add(overrides, "table_cell", _window(figures, r"表中文字用", r"表中|[。；;]"))
    sentences = re.split(r"[。；;]", figures)
    if any("图号与图题文字间置一字空格" in sentence and not re.search(r"建议|推荐", sentence)
           for sentence in sentences):
        overrides.setdefault("figure_caption", {})["number_title_gap"] = "one_character"
    if any(re.search(r"表应有表题.{0,80}与表号之间置一字空格", sentence)
           and not re.search(r"建议|推荐", sentence) for sentence in sentences):
        overrides.setdefault("table_caption", {})["number_title_gap"] = "one_character"

    code = re.search(r"代码：(.{0,300})", normalized)
    if code:
        _add(overrides, "code", code.group(1))
    return overrides


def extract_layout_overrides(text: str) -> dict[str, float]:
    """Extract explicit A4 and page-distance requirements from a guideline."""
    source = str(text or "")
    source = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", source)
    source = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z])", " ", source)
    normalized = re.sub(r"[ \t\r\n]+", " ", source).strip()
    page = _window(normalized, r"用纸、页面设置要求")
    if not page:
        page = normalized
    result: dict[str, float] = {}
    if re.search(r"标准\s*A4|A4复印纸", page, re.IGNORECASE):
        result.update(page_width_cm=21.0, page_height_cm=29.7)
    margin = re.search(r"页边距[：:]?上、下、左、右各\s*(\d+(?:\.\d+)?)\s*mm", page, re.IGNORECASE)
    if margin:
        value = float(margin.group(1)) / 10
        result.update(
            top_margin_cm=value,
            bottom_margin_cm=value,
            left_margin_cm=value,
            right_margin_cm=value,
        )
    header = _number(r"页眉距边距[：:]?\s*(\d+(?:\.\d+)?)\s*cm", page)
    footer = _number(r"页脚距边距[：:]?\s*(\d+(?:\.\d+)?)\s*cm", page)
    if header is not None:
        result["header_distance_cm"] = header
    if footer is not None:
        result["footer_distance_cm"] = footer
    return result


__all__ = [
    "STYLE_FIELDS",
    "extract_format_overrides",
    "extract_image_font_size_limit",
    "extract_layout_overrides",
    "format_override_count",
    "is_format_requirement",
    "merge_format_overrides",
    "normalize_format_overrides",
    "normalize_layout_overrides",
]
