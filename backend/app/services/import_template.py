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
    "Heading 1": "heading1", "Heading1": "heading1", "heading 1": "heading1", "1": "heading1", "标题1": "heading1", "标题 1": "heading1",
    "Heading 2": "heading2", "Heading2": "heading2", "heading 2": "heading2", "2": "heading2", "标题2": "heading2", "标题 2": "heading2",
    "Heading 3": "heading3", "Heading3": "heading3", "heading 3": "heading3", "3": "heading3", "标题3": "heading3", "标题 3": "heading3",
    "Normal": "body", "正文": "body", "a": "body",
    "Body Text First Indent": "body", "ae": "body",
    "Abstract": "english_abstract", "EnglishAbstract": "english_abstract", "EnglishAbstractBody": "english_abstract", "英文摘要": "english_abstract",
    "AbstractHeader": "abstract", "AbstractBody": "abstract", "AbstractContent": "abstract", "摘要": "abstract",
    "Keywords": "english_keywords", "KeyWords": "english_keywords", "Key Words": "english_keywords", "英文关键词": "english_keywords",
    "关键词": "keywords",
    "Title": "cover_title", "BookTitle": "cover_title",
    "Abstract": "abstract", "摘要": "abstract",
    "TOCHeading": "toc",
    "References": "references", "Bibliography": "references", "参考文献": "references",
    "Acknowledgement": "acknowledgement", "致谢": "acknowledgement",
}

# 只识别这些模板键, 其余 Word 样式(页眉页脚/代码等)不进入模板
_KEEP_KEYS = {
    "heading1", "heading2", "heading3",
    "body", "cover_title", "abstract", "english_abstract", "keywords", "english_keywords", "toc",
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

    import lxml.etree as etree

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    with zipfile.ZipFile(docx_path) as zf:
        if "word/document.xml" not in zf.namelist():
            return []
        root = etree.fromstring(zf.read("word/document.xml"))

    body = root.find(f"{{{W}}}body")
    if body is None:
        return []

    # 收集 (style_id, text), 同时支持正文中的中文/数字章节标题。
    items: list[tuple[str, str]] = []
    toc_style_ids = {"TOC1", "TOC2", "TOC3", "TOC4", "TOC5", "TOC6", "TOC7", "TOC8", "TOC9", "toc 1", "toc 2", "toc 3"}
    for p in body.iter(f"{{{W}}}p"):
        style_id = None
        ppr = p.find(f"{{{W}}}pPr")
        if ppr is not None:
            pstyle = ppr.find(f"{{{W}}}pStyle")
            if pstyle is not None:
                style_id = pstyle.get(f"{{{W}}}val")
        if style_id in toc_style_ids:
            continue
        text = "".join(t.text or "" for t in p.iter(f"{{{W}}}t")).strip()
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        lower = compact.lower()
        if compact in {"摘要", "中文摘要"}:
            items.append(("special-abstract", text))
        elif lower in {"abstract", "englishabstract", "英文摘要", "abstractenglish"}:
            items.append(("special-english-abstract", text))
        elif compact.startswith("关键词") or lower.startswith(("keywords", "keyword", "keyterms", "indexterms")):
            # 依据 GB/T 7713 规范，关键词是摘要下方的内嵌段落，不作为独立章节树节点进入目录
            continue
        elif compact in {"目录", "toc", "contents", "tableofcontents"} or lower in {"toc", "contents", "tableofcontents"} or style_id == "TOCHeading":
            items.append(("special-toc", text))
        elif compact == "参考文献":
            items.append(("special-references", text))
        elif compact in {"致谢", "谢辞"}:
            items.append(("special-acknowledgement", text))
        elif compact == "附录":
            items.append(("special-appendix", text))
        elif style_id in _HEADING_STYLE_IDS:
            items.append((style_id, text))
        elif re.match(r"^(第\s*[0-9一二三四五六七八九十百]+\s*[章节]|\d+(?:\.\d+){1,2}\s*\S+)", text, re.I):
            items.append(("text-heading", text))

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

        normalized = style_id.replace(" ", "")
        if normalized in {"1", "Heading1", "标题1"}:
            level = 1
        elif normalized in {"2", "Heading2", "标题2"}:
            level = 2
        elif normalized in {"3", "Heading3", "标题3"}:
            level = 3
        elif style_id == "text-heading":
            level = 1 if re.match(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节]", text) else 2
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
