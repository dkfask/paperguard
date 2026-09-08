"""段落样本 + 样式表索引: 模板样式识别的数据源。

中文 Word 论文模板的实际排版几乎都写在**段落直接格式**上(段前段后/行距/
首行缩进/中西文字体), styles.xml 里的命名样式往往只是 Word 的残留。
因此样式识别以段落样本为准, 命名样式只作兜底。

本模块提供:
- sample_paragraphs(document)  → 段落样本(文本 + 样式 id + 段落/首个 run 属性)
- style_id_to_name(styles)     → styleId → Word 显示名 (中文模板的 styleId 常是 a/ae/af0)
- build_style_table(styles)    → styleId → 合并 docDefaults + basedOn 链后的属性
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from app.core.extract.para_props import para_props
from app.core.extract.run_props import run_props
from app.core.extract.xml_utils import qn


@dataclass
class ParaSample:
    """一个段落的排版样本。"""

    index: int
    text: str
    style_id: str = ""
    para: dict[str, str] = field(default_factory=dict)   # para_props 结果
    run: dict[str, str] = field(default_factory=dict)    # 首个有文字的 run 的 run_props
    in_table: bool = False                               # 位于表格单元格内
    cell_index: int = -1                                 # 所在单元格列号(-1=非表格)


def _sample_paragraph(p: etree._Element, index: int,
                      in_table: bool = False, cell_index: int = -1) -> ParaSample:
    ppr = p.find(qn("w:pPr"))
    style_id = ""
    if ppr is not None:
        pstyle = ppr.find(qn("w:pStyle"))
        if pstyle is not None:
            style_id = pstyle.get(qn("w:val")) or ""
    text = "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()

    run_attrs: dict[str, str] = {}
    for run in p.iter(qn("w:r")):
        run_text = "".join(t.text or "" for t in run.findall(qn("w:t")))
        if not run_text.strip():
            continue
        rpr = run.find(qn("w:rPr"))
        if rpr is not None:
            run_attrs = run_props(rpr)
        break

    return ParaSample(
        index=index,
        text=text,
        style_id=style_id,
        para=para_props(ppr) if ppr is not None else {},
        run=run_attrs,
        in_table=in_table,
        cell_index=cell_index,
    )


def sample_paragraphs(document: etree._Element | None) -> list[ParaSample]:
    """按文档顺序抽取段落样本 (含表格单元格, 带 in_table 标记)。"""
    if document is None:
        return []
    body = document.find(qn("w:body"))
    if body is None:
        return []

    samples: list[ParaSample] = []
    index = 0
    for child in body:
        if child.tag == qn("w:p"):
            samples.append(_sample_paragraph(child, index))
            index += 1
        elif child.tag == qn("w:tbl"):
            for row in child.findall(qn("w:tr")):
                for cell_index, cell in enumerate(row.findall(qn("w:tc"))):
                    for p in cell.findall(qn("w:p")):
                        samples.append(_sample_paragraph(
                            p, index, in_table=True, cell_index=cell_index))
                        index += 1
    return samples


def style_id_to_name(styles: etree._Element | None) -> dict[str, str]:
    """styleId → Word 显示名。中文模板的 styleId 常是 a/ae/af0, 必须靠名字识别。"""
    if styles is None:
        return {}
    mapping: dict[str, str] = {}
    for style in styles.findall(qn("w:style")):
        style_id = style.get(qn("w:styleId"))
        if not style_id:
            continue
        name = style.find(qn("w:name"))
        mapping[style_id] = (name.get(qn("w:val")) if name is not None else "") or ""
    return mapping


def default_paragraph_style_id(styles: etree._Element | None) -> str:
    """默认段落样式的 styleId (无 pStyle 的段落继承它, 通常是 Normal)。"""
    if styles is None:
        return ""
    for style in styles.findall(qn("w:style")):
        if (style.get(qn("w:type")) or "paragraph") != "paragraph":
            continue
        if (style.get(qn("w:default")) or "").lower() in ("1", "true", "on"):
            return style.get(qn("w:styleId")) or ""
    return ""


def doc_defaults(styles: etree._Element | None) -> dict[str, str]:
    """docDefaults 里的默认 run/段落属性。"""
    if styles is None:
        return {}
    dd = styles.find(qn("w:docDefaults"))
    if dd is None:
        return {}
    result: dict[str, str] = {}
    rpr_default = dd.find(qn("w:rPrDefault"))
    if rpr_default is not None:
        rpr = rpr_default.find(qn("w:rPr"))
        if rpr is not None:
            result.update(run_props(rpr))
    ppr_default = dd.find(qn("w:pPrDefault"))
    if ppr_default is not None:
        ppr = ppr_default.find(qn("w:pPr"))
        if ppr is not None:
            result.update(para_props(ppr))
    return result


def build_style_table(styles: etree._Element | None) -> dict[str, dict[str, str]]:
    """styleId → 合并 docDefaults + basedOn 继承链后的属性 (自身属性优先)。"""
    if styles is None:
        return {}

    raw: dict[str, tuple[str, dict[str, str]]] = {}
    for style in styles.findall(qn("w:style")):
        style_id = style.get(qn("w:styleId"))
        if not style_id:
            continue
        based_on = ""
        node = style.find(qn("w:basedOn"))
        if node is not None:
            based_on = node.get(qn("w:val")) or ""
        props: dict[str, str] = {}
        rpr = style.find(qn("w:rPr"))
        if rpr is not None:
            props.update(run_props(rpr))
        ppr = style.find(qn("w:pPr"))
        if ppr is not None:
            props.update(para_props(ppr))
        raw[style_id] = (based_on, props)

    defaults = doc_defaults(styles)

    def resolve(style_id: str, seen: tuple[str, ...] = ()) -> dict[str, str]:
        if style_id in seen:
            return {}
        entry = raw.get(style_id)
        if entry is None:
            return {}
        based_on, props = entry
        merged = dict(defaults)
        if based_on:
            merged.update(resolve(based_on, seen + (style_id,)))
        merged.update(props)
        return merged

    return {style_id: resolve(style_id) for style_id in raw}


__all__ = [
    "ParaSample",
    "build_style_table",
    "default_paragraph_style_id",
    "doc_defaults",
    "sample_paragraphs",
    "style_id_to_name",
]
