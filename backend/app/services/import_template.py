"""Word 模板导入: 上传 docx → 识别样式 → 生成模板配置。

复用核心抽取层 (app/core/extract), 把 7 类 Requirement 中与排版相关的
样式约束映射为模板的 TextStyle。
"""

from __future__ import annotations

from pathlib import Path

from app.core.extract import extract_template
from app.models.template import Align, TextStyle

# Word 内置样式名 → 模板样式键
_STYLE_KEY_MAP: dict[str, str] = {
    "Heading 1": "heading1", "Heading1": "heading1", "heading 1": "heading1",
    "Heading 2": "heading2", "Heading2": "heading2", "heading 2": "heading2",
    "Heading 3": "heading3", "Heading3": "heading3", "heading 3": "heading3",
    "Normal": "body", "正文": "body",
    "Title": "cover_title", "BookTitle": "cover_title",
    "Abstract": "abstract", "摘要": "abstract",
    "TOCHeading": "toc",
    "References": "references", "Bibliography": "references", "参考文献": "references",
    "Acknowledgement": "acknowledgement", "致谢": "acknowledgement",
}

# 只识别这些模板键, 其余 Word 样式(页眉页脚/代码等)不进入模板
_KEEP_KEYS = {
    "heading1", "heading2", "heading3",
    "body", "cover_title", "abstract", "toc",
    "references", "acknowledgement",
}

_ALIGN_MAP = {
    "left": Align.LEFT, "start": Align.LEFT,
    "center": Align.CENTER, "centre": Align.CENTER,
    "right": Align.RIGHT, "end": Align.RIGHT,
    "both": Align.JUSTIFY, "justify": Align.JUSTIFY, "distribute": Align.JUSTIFY,
}


def import_template_styles(docx_path: str | Path) -> dict:
    """从 docx 抽取并映射为模板样式配置。

    Returns:
        {
            "styles": {模板键: TextStyle},
            "style_names": [识别的 Word 样式名, ...],
            "unmapped": [未映射的 Word 样式名, ...],
        }
    """
    reqs = extract_template(docx_path)

    # 按 Word 样式名分组: name -> {property: expected}
    by_style: dict[str, dict[str, str]] = {}
    for r in reqs:
        if r.category != "style" or not r.target.startswith("style:"):
            continue
        name = r.target.split(":", 1)[1].strip()
        if not name:
            continue
        by_style.setdefault(name, {})[r.property] = str(r.expected)

    styles: dict[str, TextStyle] = {}
    style_names: list[str] = []
    unmapped: list[str] = []

    for name, attrs in by_style.items():
        key = _STYLE_KEY_MAP.get(name)
        if key is None:
            unmapped.append(name)
            continue
        ts = _attrs_to_text_style(attrs)
        if ts is None:
            unmapped.append(name)
            continue
        # 后面出现的同键样式覆盖前面(如 Heading1Char 不映射, 无冲突)
        styles[key] = ts
        style_names.append(name)

    return {
        "styles": styles,
        "style_names": style_names,
        "unmapped": unmapped,
    }


def _attrs_to_text_style(attrs: dict[str, str]) -> TextStyle | None:
    """Word 样式属性字典 → TextStyle。没有任何可识别属性时返回 None。"""
    ts = TextStyle()
    changed = False

    if "font.size_half_points" in attrs:
        try:
            ts.size_pt = float(attrs["font.size_half_points"]) / 2.0
            changed = True
        except ValueError:
            pass
    if "font.ascii" in attrs:
        ts.font_en = attrs["font.ascii"]
        changed = True
    elif "font.hAnsi" in attrs:
        ts.font_en = attrs["font.hAnsi"]
        changed = True
    if "font.eastAsia" in attrs:
        ts.font_zh = attrs["font.eastAsia"]
        changed = True

    if "font.bold" in attrs:
        ts.bold = attrs["font.bold"] == "true"
        changed = True
    if "font.italic" in attrs:
        ts.italic = attrs["font.italic"] == "true"
        changed = True

    for prop, field in (("paragraph.spacing.before", "space_before_pt"),
                        ("paragraph.spacing.after", "space_after_pt")):
        if prop in attrs:
            try:
                setattr(ts, field, round(float(attrs[prop]) / 20.0, 1))
                changed = True
            except ValueError:
                pass

    if "paragraph.spacing.line" in attrs:
        try:
            # w:line 单位: 240 = 1.0 倍行距
            ts.line_spacing = round(float(attrs["paragraph.spacing.line"]) / 240.0, 2)
            changed = True
        except ValueError:
            pass

    if "paragraph.jc" in attrs:
        jc = attrs["paragraph.jc"].lower()
        if jc in _ALIGN_MAP:
            ts.align = _ALIGN_MAP[jc]
            changed = True

    if "paragraph.indent.first_line_chars" in attrs:
        try:
            ts.first_line_indent_chars = float(attrs["paragraph.indent.first_line_chars"])
            changed = True
        except ValueError:
            pass

    return ts if changed else None
