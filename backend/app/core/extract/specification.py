"""Extract a deterministic, source-bound writing specification from one DOCX.

Observation is separate from rule acceptance. No rule emitted here is an
approved school requirement; publication/compilation is a later operation.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import posixpath
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from lxml import etree

from app.core.extract.effective_format import EffectiveFormatResolver, location, measure, on
from app.core.extract.heading_semantics import plausible_unstyled_numbered_heading, semantic_heading_level
from app.core.extract.xml_utils import NS, load_part as _load_part, qn
from app.models.writing_specification import (
    SpecificationEvidence, SpecificationReviewItem, WritingRule, WritingSpecification,
)

_NS = {**NS, "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
       "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
       "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
       "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
       "c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MAX_OBJECTS = 10_000
_MAX_RUNS = 30_000
_SIZES = {"初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18,
          "三号": 16, "小三": 15, "四号": 14, "小四": 12, "五号": 10.5, "小五": 9,
          "六号": 7.5, "小六": 6.5, "七号": 5.5, "八号": 5}
ROLE_LABELS = {"body": "正文", "heading1": "一级标题", "heading2": "二级标题", "heading3": "三级标题",
               "heading4": "四级标题", "abstract": "中文摘要正文", "english_abstract": "英文摘要正文",
               "abstract_title": "中文摘要标题", "english_abstract_title": "英文摘要标题", "keywords": "关键词",
               "english_keywords": "英文关键词", "toc": "目录标题", "toc_entry": "目录条目", "cover": "封面",
               "table_text": "表格文字", "table": "表格", "image": "图片", "section": "页面与分节",
               "figure_caption": "图题", "table_caption": "表题", "references": "参考文献正文",
               "references_title": "参考文献标题", "acknowledgement": "致谢正文", "acknowledgement_title": "致谢标题",
               "appendix": "附录正文", "appendix_title": "附录标题", "header": "页眉", "footer": "页脚",
               "instruction": "模板说明", "unknown": "待确认内容"}
PROPERTY_LABELS = {"font.eastAsia": "中文字体", "font.ascii": "西文字体", "font.hAnsi": "西文扩展字体",
                   "font.cs": "复杂文字字体", "font.size": "字号", "font.bold": "加粗", "font.italic": "斜体",
                   "font.color": "文字颜色", "paragraph.align": "对齐", "paragraph.spacing.line": "行距",
                   "paragraph.spacing.before": "段前", "paragraph.spacing.after": "段后",
                   "paragraph.indent.first_line": "首行缩进", "paragraph.indent.hanging": "悬挂缩进",
                   "paragraph.indent.left": "左缩进", "paragraph.indent.right": "右缩进",
                   "paragraph.keep_next": "与下段同页", "paragraph.page_break_before": "段前分页",
                   "table.width": "表格宽度", "table.layout": "表格布局", "image.width": "图片显示宽度",
                   "image.height": "图片显示高度", "instruction.text": "原文要求"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_part(archive: zipfile.ZipFile, part: str) -> etree._Element | None:
    root = _load_part(archive, part)
    if root is not None and root.getroottree().docinfo.doctype:
        raise ValueError("模板 XML 不允许 DTD")
    return root


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _text(element: etree._Element) -> str:
    texts = element.findall(f".//{qn('w:t')}")
    if element.tag == qn("w:p"):
        texts = [t for t in texts if t.xpath("ancestor::w:p[1]", namespaces=_NS)[0] is element]
    return "".join(t.text or "" for t in texts)


def _active(element: etree._Element) -> bool:
    # Preserve all parts; count primary DrawingML once instead of also its VML fallback.
    return not bool(element.xpath("ancestor::mc:Fallback[../mc:Choice]", namespaces=_NS))


def _heading(paragraph: etree._Element, style_id: str, resolver: EffectiveFormatResolver, text: str) -> int:
    for ppr in [paragraph.find(qn("w:pPr")),
                *[s.find(qn("w:pPr")) for s in reversed(resolver.chain(style_id))]]:
        level = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
        if level is not None:
            try:
                return int(level.get(qn("w:val"), "9")) + 1 if int(level.get(qn("w:val"), "9")) < 9 else 0
            except ValueError:
                return 0
    match = re.search(r"(?:heading|标题)\s*([1-9])", resolver.style_name(style_id), re.I)
    if match:
        return int(match.group(1))
    if len(text) < 90:
        match = re.match(r"^\s*(\d+(?:\.\d+){0,3})[\s、．]+\S", text)
        if match:
            if "." in match.group(1) and not plausible_unstyled_numbered_heading(text):
                return 0
            return match.group(1).count(".") + 1
        if re.match(r"^第[一二三四五六七八九十\d]+章\s*\S", text):
            return 1
    return 0


def _instruction(text: str) -> bool:
    format_instruction = re.search(r"(?:正文|标题|图题|表题|图片|表格|页边距|字号|字体|行距|缩进|关键词).{0,24}"
                                   r"(?:采用|使用|设置|要求|应为|不得|不少于|不超过|宋体|黑体|小四|小五|倍行距)", text)
    writing_hint = re.search(r"^\s*(?:[（(]?(?:以下略)|[（(](?:列出|填写|此处|这里)|说明[：:]|此处|这里(?:要|应|需)|按照.{0,35}(?:填写|书写)|.{0,10}部分(?:要用|应当|需要))", text)
    reference_hint = re.search(r"参考文献.{0,30}至少\s*15\s*篇\s*近\s*3\s*年.{0,12}其中\s*5\s*篇\s*是英文", text)
    return bool(format_instruction or writing_hint or reference_hint)


def _role(text: str, style_name: str, heading: int, current: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", text).lower()
    if re.match(r"^(?:toc|目录)\s*\d", style_name, re.I):
        return "toc_entry", current
    titles = {"摘要": "abstract", "中文摘要": "abstract", "abstract": "english_abstract",
              "英文摘要": "english_abstract", "目录": "toc", "contents": "toc", "tableofcontents": "toc",
              "参考文献": "references", "致谢": "acknowledgement", "谢辞": "acknowledgement", "附录": "appendix"}
    if compact in titles:
        role = titles[compact]
        return (f"{role}_title" if role != "toc" else "toc"), role
    if re.match(r"^关键词[：:]", text.strip()):
        return "keywords", current
    if re.match(r"^key\s*words?\s*[:：]", text.strip(), re.I):
        return "english_keywords", current
    if _instruction(text):
        return "instruction", current
    if heading:
        return f"heading{heading}", "body"
    if re.match(r"^\s*图\s*\d+(?:[.．\-]\d+)*\s+\S", text):
        return "figure_caption", current
    if re.match(r"^\s*表\s*\d+(?:[.．\-]\d+)*\s+\S", text):
        return "table_caption", current
    if current in {"abstract", "english_abstract", "references", "acknowledgement", "appendix"}:
        return current, current
    return "body", current


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, dict[str, str]]:
    rel_path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    root = load_part(archive, rel_path)
    if root is None:
        return {}
    result = {}
    for rel in root.findall(f"{{{_REL_NS}}}Relationship"):
        external = rel.get("TargetMode") == "External"
        target = rel.get("Target", "")
        result[rel.get("Id", "")] = {
            "target": target if external else posixpath.normpath(posixpath.join(posixpath.dirname(part), target)),
            "type": rel.get("Type", "").rsplit("/", 1)[-1], "mode": "external" if external else "internal",
        }
    return result


def _paragraph_font_observations(runs: list[dict]) -> tuple[dict, dict, dict]:
    """Use all runs and keep alternatives rather than selecting the first run."""
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for run in runs:
        for prop, value in run["properties"].items():
            if not prop.startswith("font.") or value is None or isinstance(value, dict) and "unresolved_theme" in value:
                continue
            # A Latin-only run cannot supply evidence for the paragraph's Chinese font.
            if prop == "font.eastAsia" and not run['script_character_counts']['eastAsia']:
                continue
            if prop in {"font.ascii", "font.hAnsi"} and not run['script_character_counts']['latin']:
                continue
            key = _canonical(value)
            item = grouped[prop].setdefault(key, {"value": value, "weight": 0, "sources": []})
            item["weight"] += run["character_count"]
            item["sources"].extend(run["property_sources"].get(prop, [run["path"]]))
    props, sources, alternatives = {}, {}, {}
    for prop, variants in grouped.items():
        ranked = sorted(variants.values(), key=lambda v: (-v["weight"], _canonical(v["value"])))
        props[prop] = ranked[0]["value"]
        sources[prop] = list(dict.fromkeys(ranked[0]["sources"]))
        if len(ranked) > 1:
            alternatives[prop] = [{"value": v["value"], "character_count": v["weight"]} for v in ranked]
    return props, sources, alternatives


def _heading_content_runs(text: str, runs: list[dict]) -> list[dict]:
    """Exclude an explicitly instructional trailing parenthesis from title formatting.

    The complete source and individual runs remain in evidence; only the
    formatting sample for the actual heading is narrowed.
    """
    match = re.search(r"[（(](?=[^（）()]{0,100}(?:按.{0,30}(?:描述|填写|编写|撰写)|"
                      r"示例|说明|请填写|此处填写|根据.{0,30}(?:填写|描述)))"
                      r"[^（）()]*[）)]\s*$", text)
    if not match or not text[:match.start()].strip():
        return runs
    remaining = match.start()
    selected = []
    for run in runs:
        part = run['text'][:remaining]
        remaining -= len(part)
        if part.strip():
            sample = dict(run)
            sample['text'] = part
            sample['character_count'] = len(part)
            sample['script_character_counts'] = {
                'eastAsia': sum('\u3400' <= c <= '\u9fff' for c in part),
                'latin': sum(c.isascii() and c.isalnum() for c in part),
            }
            selected.append(sample)
        if remaining <= 0:
            break
    return selected or runs


def _width(element: etree._Element | None) -> dict | None:
    if element is None:
        return None
    kind, value = element.get(qn("w:type"), "dxa"), element.get(qn("w:w"))
    if kind in {"auto", "nil"}:
        return {"kind": kind}
    return measure(value, 50, "percent", "preferred") if kind == "pct" else measure(value, 20, "pt", "preferred")


def _properties(element: etree._Element) -> dict:
    """Keep XML attributes/children without duplicating all namespace declarations."""
    result: dict[str, Any] = {etree.QName(k).localname: v for k, v in element.attrib.items()}
    for child in element:
        if isinstance(child.tag, str):
            result.setdefault(etree.QName(child).localname, []).append(_properties(child))
    if element.text and element.text.strip():
        result["text"] = element.text
    return result


def _table_details(table: etree._Element) -> tuple[dict, dict]:
    props = {}
    tblpr = table.find(qn("w:tblPr"))
    if tblpr is not None:
        width = _width(tblpr.find(qn("w:tblW")))
        if width is not None:
            props["table.width"] = width
        layout = tblpr.find(qn("w:tblLayout"))
        if layout is not None:
            props["table.layout"] = layout.get(qn("w:type"), "autofit")
        for name in ("tblBorders", "tblCellMar", "tblLook", "tblStyle"):
            child = tblpr.find(qn(f"w:{name}"))
            if child is not None:
                props[f"table.{name}"] = _properties(child)
    rows = []
    for row in table.findall(qn("w:tr")):
        trpr = row.find(qn("w:trPr"))
        height = trpr.find(qn("w:trHeight")) if trpr is not None else None
        cells = []
        for cell in row.findall(qn("w:tc")):
            tcpr = cell.find(qn("w:tcPr"))
            cells.append({"path": location(cell), "width": _width(tcpr.find(qn("w:tcW"))) if tcpr is not None else None,
                          "properties": _properties(tcpr) if tcpr is not None else {}})
        rows.append({"height": measure(height.get(qn("w:val")), 20, "pt", height.get(qn("w:hRule"), "atLeast")) if height is not None else None,
                     "repeat_header": on(trpr.find(qn("w:tblHeader"))) if trpr is not None else False,
                     "cant_split": on(trpr.find(qn("w:cantSplit"))) if trpr is not None else False, "cells": cells})
    grid = [measure(col.get(qn("w:w")), 20, "pt") for col in table.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")]
    return props, {"grid_columns": grid, "rows": rows, "size_semantics": "preferred widths; rendered geometry requires layout verification"}


def extract_writing_specification(docx_path: str | Path) -> WritingSpecification:
    """Read a validated DOCX; all results are pending review and bound to its bytes."""
    data = Path(docx_path).read_bytes()
    spec = WritingSpecification(source_sha256=hashlib.sha256(data).hexdigest())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        # Also bound resources for direct/offline callers, not just the upload path.
        infos = archive.infolist()
        if len(data) > 50 * 1024 * 1024 or len(infos) > 10_000 or sum(i.file_size for i in infos) > 250 * 1024 * 1024:
            raise ValueError("模板超过规范抽取资源预算")
        if any(i.compress_size and i.file_size / i.compress_size > 100 for i in infos):
            raise ValueError("模板压缩比超过规范抽取资源预算")
        document = load_part(archive, "word/document.xml")
        if document is None:
            raise ValueError("模板缺少 word/document.xml")
        resolver = EffectiveFormatResolver(load_part(archive, "word/styles.xml"),
                                           load_part(archive, "word/theme/theme1.xml"),
                                           load_part(archive, "word/settings.xml"))
        if document.getroottree().docinfo.doctype:
            raise ValueError("模板 XML 不允许 DTD")

        def node(element: etree._Element, part: str, role: str, region: str, **kwargs: Any) -> SpecificationEvidence:
            if len(spec.nodes) >= _MAX_OBJECTS:
                raise ValueError("模板对象数量超过规范抽取预算")
            path = location(element)
            item = SpecificationEvidence(node_id="n-" + _hash([spec.source_sha256, part, path])[:24],
                                         part=part, path=path, role=role, region=region, **kwargs)
            spec.nodes.append(item)
            return item

        relationships = _relationships(archive, "word/document.xml")
        inherited_headers: dict[str, Any] = {}
        inherited_footers: dict[str, Any] = {}
        for index, section in enumerate(document.findall(f".//{qn('w:sectPr')}")):
            props: dict[str, Any] = {}
            for tag, fields in (("pgSz", ("w", "h")), ("pgMar", ("top", "bottom", "left", "right", "header", "footer", "gutter"))):
                child = section.find(qn(f"w:{tag}"))
                if child is not None:
                    for field in fields:
                        value = measure(child.get(qn(f"w:{field}")), 20, "pt")
                        if value is not None:
                            props[f"section.{tag}.{field}"] = value
                    if child.get(qn("w:orient")):
                        props["section.orientation"] = child.get(qn("w:orient"))
            for tag in ("type", "pgNumType", "cols", "docGrid"):
                child = section.find(qn(f"w:{tag}"))
                if child is not None:
                    props[f"section.{tag}"] = {etree.QName(k).localname: v for k, v in child.attrib.items()}
            for kind, refs in (("header", inherited_headers), ("footer", inherited_footers)):
                for ref in section.findall(qn(f"w:{kind}Reference")):
                    refs[ref.get(qn("w:type"), "default")] = relationships.get(ref.get(qn("r:id"), ""), {"missing_relationship": True})
                props[f"section.{kind}_references"] = dict(refs)
            props["section.different_first_page"] = on(section.find(qn("w:titlePg")))
            node(section, "word/document.xml", "section", f"section-{index+1}", properties=props,
                 details={"mapping_semantics": "headers and footers inherit by variant; missing first/even variants are not default aliases"})
        settings = load_part(archive, "word/settings.xml")
        even_headers = on(settings.find(qn("w:evenAndOddHeaders"))) if settings is not None else False
        parts = ["word/document.xml", *sorted(name for name in archive.namelist() if re.fullmatch(r"word/(?:header|footer)[^/]*\.xml", name))]
        cover_paths: set[str] = set()
        body = document.find(qn("w:body"))
        if body is not None:
            before_heading: list[etree._Element] = []
            for child in body:
                if child.tag == qn("w:p"):
                    compact = re.sub(r"\s+", "", _text(child)).lower()
                    pstyle = child.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
                    sid = pstyle.get(qn("w:val"), "") if pstyle is not None else resolver.default_style
                    if compact in {"摘要", "中文摘要", "abstract", "英文摘要", "目录", "contents"} or _heading(child, sid, resolver, _text(child)):
                        break
                before_heading.append(child)
            has_cover = any(c.tag == qn("w:tbl") for c in before_heading) and any(
                re.search(r"毕业|学位|题目[：:]", _text(c)) for c in before_heading)
            if has_cover:
                cover_paths = {location(p) for c in before_heading for p in c.iter(qn("w:p"))}
        total_runs = 0
        for part in parts:
            root = document if part == "word/document.xml" else load_part(archive, part)
            if root is None:
                continue
            if root.getroottree().docinfo.doctype:
                raise ValueError("模板 XML 不允许 DTD")
            rels = _relationships(archive, part)
            current, region, region_title = "body", "document-body", "未分章内容"
            for p in root.findall(f".//{qn('w:p')}"):
                if not _active(p):
                    continue
                text = _text(p)
                props, sources, runs, style_id = resolver.paragraph(p)
                total_runs += len(runs)
                if total_runs > _MAX_RUNS:
                    raise ValueError("模板文字片段数量超过规范抽取预算")
                table = p.xpath("ancestor::w:tbl[1]", namespaces=_NS)
                heading_details = {}
                if part != "word/document.xml":
                    role = "header" if "/header" in part else "footer"
                    node_region = part
                    node_region_title = "页眉" if role == "header" else "页脚"
                elif table:
                    role, node_region = "instruction" if _instruction(text) else "table_text", location(table[0])
                    node_region_title = "封面表格" if location(p) in cover_paths else "表格内文字"
                elif location(p) in cover_paths:
                    role, node_region = "instruction" if _instruction(text) else "cover", "cover"
                    node_region_title = "封面"
                else:
                    declared_heading = _heading(p, style_id, resolver, text)
                    heading = semantic_heading_level(text, declared_heading)
                    if heading != declared_heading:
                        heading_details = {"source_heading_level": declared_heading, "semantic_heading_level": heading}
                    role, current = _role(text, resolver.style_name(style_id), heading, current)
                    if heading or role.endswith("_title") or role == "toc":
                        region = "region-" + _hash([part, location(p)])[:16]
                        region_title = text[:90]
                    node_region = region
                    node_region_title = region_title
                font_props, font_sources, mixed = _paragraph_font_observations(runs)
                if role.startswith("heading"):
                    title_runs = _heading_content_runs(text, runs)
                    if title_runs is not runs:
                        font_props, font_sources, _ = _paragraph_font_observations(title_runs)
                props.update(font_props)
                sources.update(font_sources)
                paragraph = node(p, part, role, node_region, text=text[:300], properties=props, property_sources=sources,
                                 details={"style_id": style_id, "style_name": resolver.style_name(style_id), "runs": runs,
                                          "mixed_properties": mixed, "empty": not text.strip(), "region_title": node_region_title,
                                          **({"in_cover": True} if part == "word/document.xml" and location(p) in cover_paths else {}),
                                          **heading_details})
                if heading_details and role.startswith("heading"):
                    spec.review_items.append(SpecificationReviewItem(code="heading_number_style_conflict",
                        message="标题编号层级与源样式不一致，按编号识别并在编译时采用对应级别的标题版式。",
                        node_ids=[paragraph.node_id]))
                if not text.strip() and table:
                    spec.review_items.append(SpecificationReviewItem(code="empty_field_style", message="空白表格区域的格式仅为候选，需结合字段用途试填确认。", node_ids=[paragraph.node_id]))
            for table in root.findall(f".//{qn('w:tbl')}"):
                if _active(table):
                    props, details = _table_details(table)
                    node(table, part, "table", location(table), properties=props, details=details)
            for drawing in root.xpath(".//w:drawing | .//w:pict", namespaces=_NS):
                if not _active(drawing):
                    continue
                props, details = {}, {"dimensions_are_instance_observations": True, "assets": []}
                extent = drawing.find(".//wp:extent", namespaces=_NS)
                if extent is not None:
                    for axis, prop in (("cx", "image.width"), ("cy", "image.height")):
                        value = measure(extent.get(axis), 360000, "cm", "display")
                        if value is not None:
                            props[prop] = value
                for blip in drawing.findall(".//a:blip", namespaces=_NS):
                    relation = rels.get(blip.get(qn("r:embed")) or blip.get(qn("r:link")), {})
                    target = relation.get("target", "")
                    asset = dict(relation)
                    if relation.get("mode") == "internal" and target in archive.namelist():
                        asset["sha256"] = hashlib.sha256(archive.read(target)).hexdigest()
                    details["assets"].append(asset)
                for tag in ("anchor", "inline", "positionH", "positionV", "wrapSquare", "wrapTight", "srcRect", "xfrm"):
                    values = drawing.xpath(f".//*[local-name()='{tag}']")
                    if values:
                        details[tag] = [{etree.QName(k).localname: value for k, value in v.attrib.items()} if tag in {"anchor", "inline"} else _properties(v) for v in values]
                image = node(drawing, part, "image", location(drawing), properties=props, details=details)
                if details["assets"]:
                    spec.review_items.append(SpecificationReviewItem(code="image_content_review", message="图片尺寸为当前对象的显示尺寸。位图内文字和图例的字体需另行核对。", node_ids=[image.node_id]))
            for object_type, expression in (("equation", ".//m:oMathPara | .//m:oMath[not(parent::m:oMathPara)]"), ("chart", ".//c:chart")):
                for obj in root.xpath(expression, namespaces=_NS):
                    if _active(obj):
                        item = node(obj, part, object_type, location(obj), details={"requires_specialized_verification": True})
                        spec.review_items.append(SpecificationReviewItem(code=f"{object_type}_review", message="该可编辑对象需专用格式与版面核对，未推断为已验证。", node_ids=[item.node_id]))
        comments = load_part(archive,"word/comments.xml")
        if comments is not None:
            body_nodes = {n.path:n for n in spec.nodes if n.part=="word/document.xml"}
            for comment in comments.findall(qn("w:comment")):
                comment_id = comment.get(qn("w:id"),"")
                anchors = document.xpath(".//w:commentRangeStart[@w:id=$id]",namespaces=_NS,id=comment_id)
                anchored = [body_nodes.get(location(p)) for a in anchors for p in a.xpath("ancestor::w:p[1]",namespaces=_NS)]
                anchored = [n for n in anchored if n is not None]
                text = "\n".join(_text(p) for p in comment.findall(f".//{qn('w:p')}"))
                if text.strip():
                    node(comment,"word/comments.xml","instruction",anchored[0].region if anchored else "comments",
                         text=text[:2000],details={"comment_id":comment_id,"anchor_node_ids":[n.node_id for n in anchored],
                             "region_title":anchored[0].details.get("region_title",anchored[0].region) if anchored else "模板批注"})
        for issue in sorted(resolver.issues):
            spec.review_items.append(SpecificationReviewItem(code=issue, message={
                "style_inheritance_cycle": "样式继承存在循环，需修订样式关系。",
                "missing_style_reference": "样式引用缺失，部分有效格式无法确定。",
                "unresolved_theme_font": "主题字体依赖无法完整解析，需确认实际字体。",
                "conditional_table_style_needs_review": "表格包含条件样式，当前候选格式需核对表头、条带或边角区域。",
            }[issue]))
        spec.rules = _rules(spec)
        counts: dict[str, int] = defaultdict(int)
        for item in spec.nodes:
            counts[item.role] += 1
        spec.coverage = {"node_counts": dict(sorted(counts.items())), "rule_count": len(spec.rules),
                         "review_count": len(spec.review_items), "run_count": total_runs,
                         "even_and_odd_headers": even_headers,
                         "automatic_compliance_verified": False,
                         "missing_information": ["未在 Word 中表达的学校要求不会自动补充。"],
                         "limitations": ["条件表格样式、编号样式的段落影响需专门复核。",
                                         "图片内部文字、实际分页和目录域结果尚未通过排版核验。",
                                         "所有规则均为待确认候选，尚未编译为写作或交付门禁。"]}
    spec.specification_sha256 = _hash(spec.model_dump(mode="json", exclude={"specification_sha256"}))
    return spec


def _rules(spec: WritingSpecification) -> list[WritingRule]:
    groups: dict[str, list[tuple[SpecificationEvidence, Any]]] = defaultdict(list)
    for item in spec.nodes:
        if item.details.get("empty") or item.role == "instruction":
            continue
        for prop, value in item.properties.items():
            if prop.startswith("language."):
                continue
            key = _canonical([item.part, item.region, item.role, prop])
            groups[key].append((item, value))
    rules = []
    for key, observations in sorted(groups.items()):
        part, region, role, prop = json.loads(key)
        variants: dict[str, list[SpecificationEvidence]] = defaultdict(list)
        values = {}
        for item, value in observations:
            fingerprint = _canonical(value)
            values[fingerprint] = value
            variants[fingerprint].append(item)
        ranked = sorted(variants, key=lambda k: (-len(variants[k]), k))
        winner = ranked[0]
        mixed = any(prop in item.details.get("mixed_properties", {}) for item, _ in observations)
        conflict = len(variants) > 1 or mixed
        singleton = role in {"table", "image", "section"} or len(observations) == 1
        rule = WritingRule(id="r-" + _hash(key)[:24], scope={"part": part, "region": region, "role": role,
                                                         "region_title": observations[0][0].details.get("region_title", region)},
                          property=prop, expected=values[winner], evidence_kind="single_observation" if singleton else "observed_pattern",
                          confidence="conflict" if conflict else "single" if singleton else "consistent",
                          evidence_node_ids=[item.node_id for item, _ in observations], supporting_count=len(variants[winner]),
                          observed_count=len(observations), alternatives=[{"expected": values[k], "node_ids": [n.node_id for n in variants[k]]} for k in ranked[1:]])
        rules.append(rule)
        if conflict:
            spec.review_items.append(SpecificationReviewItem(code="format_variation", message=f"{ROLE_LABELS.get(role, role)}的{PROPERTY_LABELS.get(prop, prop)}存在多种格式，需确认规则或例外。",
                                                            node_ids=rule.evidence_node_ids, rule_ids=[rule.id]))
    for item in spec.nodes:
        if item.role == "instruction":
            rules.append(WritingRule(id="r-" + _hash([item.node_id, "instruction"])[:24],
                                     scope={"part": item.part, "region": item.region, "role": "instruction", "region_title": item.details.get("region_title", item.region)}, property="instruction.text",
                                     expected=item.text, evidence_kind="explicit_instruction", confidence="needs_review",
                                     evidence_node_ids=[item.node_id], supporting_count=1, observed_count=1))
            spec.review_items.append(SpecificationReviewItem(code="instruction_scope_review", message="模板中的文字说明已保留，请确认其适用范围及与实际格式的关系。", node_ids=[item.node_id], rule_ids=[rules[-1].id]))
    return rules


def render_specification_markdown(spec: WritingSpecification) -> str:
    """Produce a readable view from the same structured evidence; no model rewrite."""
    def text(value: Any) -> str:
        return html.escape(str(value), quote=False).replace("|", "\\|").replace("\n", " ").replace("`", "&#96;")

    def value_text(value: Any) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, dict) and "unit" in value:
            unit = {"pt": "磅", "cm": "cm", "line": "行", "multiple": "倍", "character": "字符", "percent": "%"}.get(value["unit"], value["unit"])
            size = value["value"]
            kind = {"auto": "倍数", "exact": "固定值", "atLeast": "最小值", "preferred": "首选宽度", "display": "显示尺寸"}.get(value.get("kind"), value.get("kind", ""))
            return f"{size:g} {unit}" + (f"，{kind}" if kind else "")
        return str(value) if not isinstance(value, (dict, list)) else _canonical(value)

    lines = ["# 从 Word 提取的撰写规范（待确认）", "", "以下内容来自当前 Word 的文字说明与格式观察，发布确认前作为候选。", "",
             f"源文件摘要：`{spec.source_sha256}`", f"规范摘要：`{spec.specification_sha256}`", ""]
    grouped: dict[str, list[WritingRule]] = defaultdict(list)
    for rule in spec.rules:
        grouped[rule.scope["role"]].append(rule)
    for role, rules in grouped.items():
        lines.extend([f"## {ROLE_LABELS.get(role, role)}", "", "| 要求候选 | 观察值 | 依据 | 范围与来源 |", "| --- | --- | --- | --- |"])
        for rule in rules:
            label = PROPERTY_LABELS.get(rule.property, rule.property)
            val = value_text(rule.expected)
            if rule.property == "font.size" and isinstance(rule.expected, dict):
                alias = next((k for k, v in _SIZES.items() if v == rule.expected.get("value")), "")
                val = f"{alias}（{rule.expected['value']:g} 磅）" if alias else val
            # XML is diagnostic evidence; avoid turning it into an unreadable normative sentence.
            if "<" in val or len(val) > 220:
                val = val[:180] + "…（完整数据见来源）"
            confidence = {"consistent": "重复样本一致", "single": "单个实例", "conflict": "格式有差异，待确认", "needs_review": "说明原文，待确认"}[rule.confidence]
            lines.append(f"| {text(label)} | {text(val)} | {confidence} | {text(rule.scope.get('region_title', rule.scope['region']))}；{rule.supporting_count}/{rule.observed_count}；{text(', '.join(rule.evidence_node_ids))} |")
        lines.append("")
    lines.extend(["## 待确认与能力范围", ""])
    lines.extend(f"- {text(item.message)}（{text(item.code)}）" for item in spec.review_items)
    lines.extend(f"- {text(limit)}" for limit in spec.coverage.get("limitations", []))
    lines.extend(["", "图片和表格的单个实例尺寸不会自动成为所有同类对象的统一尺寸。", "模板中未提供的字数、数量等学校要求应另行补充并标明来源。", ""])
    return "\n".join(lines)
