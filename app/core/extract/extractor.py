"""extract_template — 从 docx 抽取 Requirement[]. 7 category 子抽取器.

借鉴 references/format-thesis-docx/scripts/extract_requirements.py 的总体
结构（解 docx → 7 类遍历 → Requirement 列表），**实现重写**：

**混合策略**（Context7 调研确认）：
- 5 类高层用 lxml 直拆（styles / document / numbering / settings / core）
- 中文字体 eastAsia 必走 lxml（python-docx 不暴露，**关键陷阱**）
- 1 类（sections / paragraphs / tables）从 document.xml 拿
- 1 类（explicit-instruction）FORMAT_WORDS 关键词匹配文本

设计契约（plan §3.1 + §6）：
- extract_template(docx_path: str | Path) -> list[Requirement]
- 内部 7 个 _extract_* 子函数（style / section / paragraph-example / table /
  numbering / explicit-instruction / document）
- ID 递增 R0001, R0002, ...
- evidence 自动截 300 字符
- explicit-instruction 的 fix=None（无法自动 fix）
- 缺 part 静默跳过（不抛）
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from app.core.extract.format_words import match_format_words
from app.core.extract.para_props import para_props
from app.core.extract.requirement import Requirement
from app.core.extract.run_props import run_props
from app.core.extract.section_props import section_props
from app.core.extract.xml_utils import load_part, qn


# Module-level counter for Requirement id (R0001, R0002, ...).
_REQ_COUNTER = {"next": 1}


def _reset_counter() -> None:
    _REQ_COUNTER["next"] = 1


def _next_id() -> str:
    n = _REQ_COUNTER["next"]
    _REQ_COUNTER["next"] += 1
    return f"R{n:04d}"


def _evidence(text: str, max_chars: int = 300) -> str:
    return text[:max_chars]


def extract_template(docx_path: str | Path) -> list[Requirement]:
    """Extract Requirement[] from a .docx file (7 category)."""
    _reset_counter()
    docx_path = Path(docx_path)
    if not docx_path.exists():
        return []

    with zipfile.ZipFile(docx_path, "r") as zf:
        return _extract_all(zf)


def _extract_all(zf: zipfile.ZipFile) -> list[Requirement]:
    reqs: list[Requirement] = []
    document = load_part(zf, "word/document.xml")
    styles = load_part(zf, "word/styles.xml")
    numbering = load_part(zf, "word/numbering.xml")
    settings = load_part(zf, "word/settings.xml")
    core = load_part(zf, "docProps/core.xml")
    comments = load_part(zf, "word/comments.xml")

    if styles is not None:
        reqs.extend(_extract_styles(styles))
    if document is not None:
        reqs.extend(_extract_sections(document))
        reqs.extend(_extract_paragraphs(document))
        reqs.extend(_extract_tables(document))
        reqs.extend(_extract_explicit_instructions(document))
    if numbering is not None:
        reqs.extend(_extract_numbering(numbering))
    if settings is not None or core is not None or comments is not None:
        reqs.extend(_extract_document_settings(settings, core, comments))
    return reqs


# ---------------------------------------------------------------------------
# 7 个 _extract_* 子函数
# ---------------------------------------------------------------------------


def _extract_styles(styles: etree._Element) -> list[Requirement]:
    """从 word/styles.xml 抽所有 <w:style> → style 类别."""
    reqs: list[Requirement] = []
    for style in styles.findall(qn("w:style")):
        style_id = style.get(qn("w:styleId"))
        if not style_id:
            continue
        rpr = style.find(qn("w:rPr"))
        ppr = style.find(qn("w:pPr"))
        for props, prefix in ((run_props(rpr) if rpr is not None else {}, "rPr"),
                              (para_props(ppr) if ppr is not None else {}, "pPr")):
            for k, v in props.items():
                reqs.append(Requirement(
                    id=_next_id(),
                    category="style",
                    target=f"style:{style_id}",
                    property=k,
                    expected=v,
                    source_part="word/styles.xml",
                    evidence=f"<w:style w:styleId='{style_id}' {prefix}> {k}={v}",
                    auto_applicable=True,
                ))
    return reqs


def _extract_sections(document: etree._Element) -> list[Requirement]:
    """从 document.xml 抽所有 <w:sectPr> → section 类别."""
    reqs: list[Requirement] = []
    for idx, sect in enumerate(document.findall(f".//{qn('w:sectPr')}")):
        for k, v in section_props(sect).items():
            reqs.append(Requirement(
                id=_next_id(),
                category="section",
                target=f"section:{idx}",
                property=k,
                expected=v,
                source_part="word/document.xml",
                evidence=f"<w:sectPr index={idx}> {k}={v}",
                auto_applicable=True,
            ))
    return reqs


def _extract_paragraphs(document: etree._Element) -> list[Requirement]:
    """从 document.xml 抽所有 <w:p> → paragraph-example 类别.

    每段抽 pPr + 第一个 rPr 的 run 属性。
    """
    reqs: list[Requirement] = []
    body = document.find(qn("w:body"))
    if body is None:
        return reqs
    for idx, p in enumerate(body.findall(qn("w:p"))):
        ppr = p.find(qn("w:pPr"))
        first_run = p.find(qn("w:r"))
        rpr = first_run.find(qn("w:rPr")) if first_run is not None else None
        text_runs = p.findall(f".//{qn('w:t')}")
        text = "".join(t.text or "" for t in text_runs)[:50]
        for props, src in ((para_props(ppr) if ppr is not None else {}, "pPr"),
                           (run_props(rpr) if rpr is not None else {}, "firstRun")):
            for k, v in props.items():
                reqs.append(Requirement(
                    id=_next_id(),
                    category="paragraph-example",
                    target=f"paragraph-example:word/document.xml:{idx}",
                    property=k,
                    expected=v,
                    source_part="word/document.xml",
                    evidence=f"<w:p index={idx} text='{text[:30]}'> {src} {k}={v}",
                    auto_applicable=True,
                ))
    return reqs


def _extract_tables(document: etree._Element) -> list[Requirement]:
    """从 document.xml 抽所有 <w:tbl> → table 类别 (MVP: 仅宽度)."""
    reqs: list[Requirement] = []
    for idx, tbl in enumerate(document.findall(f".//{qn('w:tbl')}")):
        tblpr = tbl.find(qn("w:tblPr"))
        if tblpr is None:
            continue
        tblw = tblpr.find(qn("w:tblW"))
        if tblw is not None:
            w_val = tblw.get(qn("w:w"))
            if w_val:
                reqs.append(Requirement(
                    id=_next_id(),
                    category="table",
                    target=f"table:word/document.xml:{idx}",
                    property="table.width",
                    expected=w_val,
                    source_part="word/document.xml",
                    evidence=f"<w:tbl index={idx}> tblW={w_val}",
                    auto_applicable=True,
                ))
    return reqs


def _extract_numbering(numbering: etree._Element) -> list[Requirement]:
    """从 word/numbering.xml 抽所有 <w:num> → numbering 类别."""
    reqs: list[Requirement] = []
    for num in numbering.findall(qn("w:num")):
        num_id = num.get(qn("w:numId"))
        ab = num.find(qn("w:abstractNumId"))
        ab_id = ab.get(qn("w:val")) if ab is not None else "0"
        if num_id:
            reqs.append(Requirement(
                id=_next_id(),
                category="numbering",
                target=f"numbering:numId:{num_id}",
                property="numbering.abstract_num_id",
                expected=ab_id,
                source_part="word/numbering.xml",
                evidence=f"<w:num w:numId='{num_id}'> abstractNumId={ab_id}",
                auto_applicable=True,
            ))
    return reqs


def _extract_explicit_instructions(document: etree._Element) -> list[Requirement]:
    """从 document.xml 找含 FORMAT_WORDS 关键词的段落 → explicit-instruction."""
    reqs: list[Requirement] = []
    body = document.find(qn("w:body"))
    if body is None:
        return reqs
    for idx, p in enumerate(body.findall(qn("w:p"))):
        text_runs = p.findall(f".//{qn('w:t')}")
        text = "".join(t.text or "" for t in text_runs)
        if match_format_words(text):
            reqs.append(Requirement(
                id=_next_id(),
                category="explicit-instruction",
                target=f"paragraph-example:word/document.xml:{idx}",
                property="paragraph.instruction_text",
                expected=_evidence(text),
                source_part="word/document.xml",
                evidence=_evidence(text),
                auto_applicable=False,  # 显式说明不能自动 fix
            ))
    return reqs


def _extract_document_settings(
    settings: etree._Element | None,
    core: etree._Element | None,
    comments: etree._Element | None,
) -> list[Requirement]:
    """从 docProps/core.xml + word/settings.xml + word/comments.xml 抽 document 类别."""
    reqs: list[Requirement] = []
    if core is not None:
        # 抽 title (CP_NS namespace)
        cp_ns = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
        for child_name in ("title", "creator", "subject"):
            el = core.find(f"{{{cp_ns}}}{child_name}")
            if el is not None and el.text:
                reqs.append(Requirement(
                    id=_next_id(),
                    category="document",
                    target="document:core_properties",
                    property=f"document.{child_name}",
                    expected=_evidence(el.text),
                    source_part="docProps/core.xml",
                    evidence=f"<{child_name}>{el.text}</{child_name}>",
                    auto_applicable=False,
                ))
    if settings is not None:
        # 抽 defaultTabStop (settings w:defaultTabStop w:val=...)
        for child_name, prop_name in (("defaultTabStop", "document.default_tab_stop"),
                                      ("characterSpacingControl", "document.char_spacing_control"),
                                      ("compat", "document.compat")):
            el = settings.find(qn(f"w:{child_name}"))
            if el is not None:
                v = el.get(qn("w:val"))
                if v:
                    reqs.append(Requirement(
                        id=_next_id(),
                        category="document",
                        target="document:settings",
                        property=prop_name,
                        expected=v,
                        source_part="word/settings.xml",
                        evidence=f"<w:{child_name} w:val='{v}'/>",
                        auto_applicable=True,
                    ))
    if comments is not None:
        for idx, c in enumerate(comments.findall(qn("w:comment"))):
            cid = c.get(qn("w:id"))
            author = c.get(qn("w:author"))
            text = "".join(t.text or "" for t in c.findall(f".//{qn('w:t')}"))
            if text:
                reqs.append(Requirement(
                    id=_next_id(),
                    category="document",
                    target=f"document:comment:{cid}",
                    property="document.comment",
                    expected=_evidence(text),
                    source_part="word/comments.xml",
                    evidence=f"<w:comment id={cid} author={author}> {text[:50]}",
                    auto_applicable=False,
                ))
    return reqs


__all__ = ["extract_template"]
