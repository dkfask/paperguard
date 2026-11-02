"""Fill mapped regions in a copy of the original OOXML package."""
from __future__ import annotations

import copy
import hashlib
import io
import math
import posixpath
import re
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Cm
from lxml import etree

from app.core.extract.xml_utils import NS, load_part, qn
from app.models.project import BlockType
from app.services.document_assets import resolve_document_image_path
from app.services.digit_font_rules import BODY_KINDS, HEADING_KINDS, apply_digit_font
from app.services.master_layout_anchors import block_bookmark_name
from app.services.references import reference_block_text, reference_number_for
from app.services.source_template_profile import ProfileError, verify_profile, commentless_source, package_manifest, digest

REL = "http://schemas.openxmlformats.org/package/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def xml_bytes(root):
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def child(parent, tag, **attrs):
    node = parent.find(qn(tag))
    if node is None:
        node = etree.SubElement(parent, qn(tag))
    for key, value in attrs.items():
        node.set(qn("w:" + key), str(value))
    return node


def apply_properties(paragraph, properties: dict):
    """Write normalized properties with their original units; no point/line mixing."""
    ppr = paragraph.find(qn("w:pPr"))
    if ppr is None:
        ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
    if "paragraph.align" in properties:
        align = properties["paragraph.align"]
        child(ppr, "w:jc", val="both" if align == "justify" else align)
    for prop, tag in (("keep_next", "keepNext"), ("page_break_before", "pageBreakBefore"), ("keep_lines", "keepLines"), ("widow_control", "widowControl")):
        key = "paragraph." + prop
        if key in properties:
            child(ppr, "w:" + tag, val=int(properties[key]))
    for name in ("before", "after", "line"):
        value = properties.get("paragraph.spacing." + name)
        if not isinstance(value, dict):
            continue
        spacing = child(ppr, "w:spacing")
        if name == "line":
            kind = value.get("kind", "auto")
            spacing.set(qn("w:lineRule"), kind)
            spacing.set(qn("w:line"), str(round(value["value"] * (240 if kind == "auto" else 20))))
        else:
            for suffix in (name, name + "Lines", name + "Autospacing"):
                spacing.attrib.pop(qn("w:" + suffix), None)
            attr = name + "Lines" if value.get("unit") == "line" else name
            spacing.set(qn("w:" + attr), str(round(value["value"] * (100 if attr.endswith("Lines") else 20))))
    for name, attr in (("first_line", "firstLine"), ("hanging", "hanging"), ("left", "left"), ("right", "right")):
        value = properties.get("paragraph.indent." + name)
        if not isinstance(value, dict):
            continue
        indent = child(ppr, "w:ind")
        indent.attrib.pop(qn("w:" + attr), None); indent.attrib.pop(qn("w:" + attr + "Chars"), None)
        chars = value.get("unit") == "character"
        indent.set(qn("w:" + attr + ("Chars" if chars else "")), str(round(value["value"] * (100 if chars else 20))))
    for run in paragraph.iter(qn("w:r")):
        rpr = run.find(qn("w:rPr"))
        if rpr is None:
            rpr = etree.Element(qn("w:rPr")); run.insert(0, rpr)
        for slot in ("eastAsia", "ascii", "hAnsi", "cs"):
            value = properties.get("font." + slot)
            if isinstance(value, str) and value:
                fonts = child(rpr, "w:rFonts")
                fonts.set(qn("w:" + slot), value)
                fonts.attrib.pop(qn("w:" + slot + "Theme"), None)
        size = properties.get("font.size", {})
        if size:
            child(rpr, "w:sz", val=round(size["value"] * 2)); child(rpr, "w:szCs", val=round(size["value"] * 2))
        for prop, tag in (("bold", "b"), ("italic", "i")):
            if "font." + prop in properties:
                child(rpr, "w:" + tag, val=int(properties["font." + prop]))
        if "font.underline" in properties:
            value = properties["font.underline"]
            if value not in {"none", "single", "double", "thick", "dotted", "dash", "dotDash", "wave", "words"}:
                raise ProfileError("母版使用了当前排版器不支持的下划线类型")
            child(rpr, "w:u", val=value)
        if "font.highlight" in properties:
            value = properties["font.highlight"]
            if value not in {"black", "blue", "cyan", "green", "magenta", "red", "yellow", "white", "darkBlue", "darkCyan", "darkGreen", "darkMagenta", "darkRed", "darkYellow", "darkGray", "lightGray", "none"}:
                raise ProfileError("母版使用了当前排版器不支持的文字底纹")
            child(rpr, "w:highlight", val=value)
        if "font.vertical_alignment" in properties:
            value = properties["font.vertical_alignment"]
            if value not in {"baseline", "superscript", "subscript"}:
                raise ProfileError("母版使用了当前排版器不支持的上下标格式")
            child(rpr, "w:vertAlign", val=value)
        if properties.get("font.color"):
            color = child(rpr, "w:color", val=properties["font.color"])
            for attr in ("themeColor", "themeTint", "themeShade"):
                color.attrib.pop(qn("w:" + attr), None)


def text_paragraph(text: str, properties: dict):
    paragraph = OxmlElement("w:p")
    run = etree.SubElement(paragraph, qn("w:r"))
    for index, line in enumerate(text.split("\n")):
        if index:
            etree.SubElement(run, qn("w:br"))
        for col, part in enumerate(line.split("\t")):
            if col:
                etree.SubElement(run, qn("w:tab"))
            t = etree.SubElement(run, qn("w:t")); t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve"); t.text = part
    apply_properties(paragraph, properties)
    return paragraph


def image_line_spacing(properties: dict) -> dict:
    """Let an inline picture expand beyond a template's fixed text line."""
    line = properties.get("paragraph.spacing.line")
    if line is None:
        return {"kind": "auto", "unit": "multiple", "value": 1}
    if not isinstance(line, dict):
        raise ProfileError("图片段落的行距规则无效")
    kind = line.get("kind", "auto")
    value = line.get("value")
    expected_unit = "multiple" if kind == "auto" else "pt"
    if (kind not in {"auto", "exact", "atLeast"}
            or isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0
            or line.get("unit", expected_unit) != expected_unit):
        raise ProfileError("图片段落的行距规则无效")
    if kind == "auto":
        # A sub-single multiple may make the graphic's line smaller than its
        # natural height; use a normal single line for the image only.
        return {"kind": "auto", "unit": "multiple", "value": max(1, value)}
    if round(value * 20) < 1:
        raise ProfileError("图片段落的行距规则无效")
    return {"kind": "atLeast", "unit": "pt", "value": value}


def make_generated_content_visible(root):
    """Override hidden formatting on student-owned content, not the source master."""
    controls = root.xpath(
        ".//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,'pg-field:') "
        "or starts-with(w:sdtPr/w:tag/@w:val,'pg-block:')]/w:sdtContent",
        namespaces=NS,
    )
    for content in controls:
        for run in content.iter(qn("w:r")):
            if not any(run.find(qn("w:" + tag)) is not None for tag in ("t", "drawing", "pict")):
                continue
            properties = run.find(qn("w:rPr"))
            if properties is None:
                properties = etree.Element(qn("w:rPr"))
                run.insert(0, properties)
            for name in ("vanish", "webHidden"):
                for old in properties.findall(qn("w:" + name)):
                    properties.remove(old)
                child(properties, "w:" + name, val="0")


def apply_content_decoration(run, block, inline=None):
    rpr = run.find(qn("w:rPr"))
    for flag, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
        if getattr(block, flag, False) or (inline is not None and getattr(inline, flag, False)):
            child(rpr, "w:" + tag, val="single" if tag == "u" else "1")
    highlight = inline.highlight if inline is not None and inline.highlight is not None else block.highlight
    if highlight is not None:
        names = {name.lower(): name for name in (
            "yellow", "green", "cyan", "magenta", "red", "blue", "darkBlue", "darkCyan", "darkGreen",
            "darkMagenta", "darkRed", "darkYellow", "lightGray", "darkGray", "black",
        )}
        value = names.get(highlight.lower())
        if value is None:
            raise ProfileError("段落包含不支持的文字高亮颜色")
        child(rpr, "w:highlight", val=value)
    vertical = inline.vert_align if inline is not None and inline.vert_align is not None else block.vert_align
    if vertical is not None:
        if vertical not in {"superscript", "subscript"}:
            raise ProfileError("段落包含不支持的上下标格式")
        child(rpr, "w:vertAlign", val=vertical)


def control(tag: str, nodes):
    sdt = etree.Element(qn("w:sdt"), nsmap={"w15":W15,"mc":MC})
    pr = etree.SubElement(sdt, qn("w:sdtPr"))
    # Keep the binding for artifact inspection without the editing rectangle.
    # Scope the extension to properties so older readers can ignore it safely.
    pr.set("{"+MC+"}Ignorable", "w15")
    child(pr, "w:tag", val=tag)
    etree.SubElement(pr,"{"+W15+"}appearance").set("{"+W15+"}val","hidden")
    content = etree.SubElement(sdt, qn("w:sdtContent"))
    content.extend(nodes)
    return sdt


def add_block_bookmarks(root, project, profile):
    """Place zero-width anchors on each block boundary and its caption."""
    blocks = [block for section in project.sections for block in section.paragraphs]
    if len({block.id for block in blocks}) != len(blocks):
        raise ProfileError("项目内容块 ID 重复，无法建立稳定锚点")
    existing = root.findall(".//" + qn("w:bookmarkStart"))
    existing_names = {item.get(qn("w:name"), "").casefold() for item in existing}
    ids = [int(item.get(qn("w:id"))) for item in root.xpath(
        ".//w:bookmarkStart | .//w:bookmarkEnd", namespaces=NS
    ) if (item.get(qn("w:id")) or "").isdigit()]
    next_id = max(ids + [-1]) + 1
    headings = {heading["key"]: heading for heading in profile["headings"]}
    controls = {}
    for content in root.xpath(".//w:sdt[w:sdtPr/w:tag]/w:sdtContent", namespaces=NS):
        tag = content.getparent().find(f"{qn('w:sdtPr')}/{qn('w:tag')}").get(qn("w:val"), "")
        if tag.startswith("pg-block:"):
            controls.setdefault(tag, []).append(content)

    def put(parent, role, block_id, *, at_end=False):
        nonlocal next_id
        name = block_bookmark_name(block_id, role)
        if name.casefold() in existing_names or next_id > 2147483647:
            raise ProfileError("内容锚点与现有书签冲突")
        existing_names.add(name.casefold())
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), str(next_id)); start.set(qn("w:name"), name)
        end = OxmlElement("w:bookmarkEnd"); end.set(qn("w:id"), str(next_id))
        next_id += 1
        position = len(parent) if at_end else (1 if parent.find(qn("w:pPr")) is not None else 0)
        parent.insert(position, start); parent.insert(position + 1, end)

    for section in project.sections:
        heading = headings.get(section.key)
        if heading is None or heading["kind"] == "toc":
            continue
        for block in section.paragraphs:
            matches = controls.get("pg-block:" + block.id, [])
            if len(matches) != 1:
                raise ProfileError("无法定位内容块以建立稳定锚点")
            content = matches[0]
            nodes = list(content)
            caption = None
            if heading.get("inline"):
                first = last = content
            elif block.type in {BlockType.PARAGRAPH, BlockType.IMAGE, BlockType.EQUATION}:
                first = last = nodes[0]
                if block.type == BlockType.IMAGE and block.image:
                    caption = nodes[-1]
            elif block.type == BlockType.TABLE and block.table:
                table = next((node for node in nodes if node.tag == qn("w:tbl")), None)
                paragraphs = table.findall(".//" + qn("w:p")) if table is not None else []
                if not paragraphs:
                    raise ProfileError("表格缺少可定位的单元格")
                first, last = paragraphs[0], paragraphs[-1]
                caption = nodes[0]
            else:
                raise ProfileError("当前内容类型不支持建立稳定锚点")
            put(first, "start", block.id)
            put(last, "end", block.id, at_end=True)
            if caption is not None:
                put(caption, "caption", block.id)


def prepare_source(root, profile, meta=None):
    """Apply the approved editable boundaries; all other source nodes stay in order."""
    body = root.find(qn("w:body"))
    original = list(body)
    ns = {k: v for element in root.iter() for k, v in element.nsmap.items() if k}
    targets = [(field, root.xpath(field["path"], namespaces=ns)) for field in profile["fields"]]
    removals = [node for path in profile["removals"] for node in root.xpath(path, namespaces=ns)]
    toc_range = [node for path in profile.get("toc_range_paths", []) for node in root.xpath(path, namespaces=ns)]
    toc_active = profile.get("toc_active", False)
    if toc_active and toc_range:
        parents = {node.getparent().getroottree().getpath(node.getparent()): node.getparent() for node in toc_range}
        if len(parents) != 1:
            raise ProfileError("母版目录区域分散，无法安全重建")
        toc_parent = next(iter(parents.values()))
        toc_first = min(toc_parent.index(node) for node in toc_range)
        # Word may start the index field in the directory title paragraph.
        # Keep that source title while replacing only the generated index.
        retained = []
        for node in toc_range:
            style = node.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
            if style is not None and style.get(qn("w:val")) in profile.get("toc_style_ids", []):
                continue
            title = copy.deepcopy(node)
            inside = False
            for item in list(title.iter()):
                if item.tag == qn("w:fldSimple"):
                    item.getparent().remove(item)
                elif item.tag == qn("w:fldChar"):
                    inside = item.get(qn("w:fldCharType")) != "end"
                    item.getparent().remove(item)
                elif item.tag == qn("w:instrText") or (inside and item.tag == qn("w:t")):
                    if item.getparent() is not None:item.getparent().remove(item)
            for section in title.findall(f"{qn('w:pPr')}/{qn('w:sectPr')}"):
                section.getparent().remove(section)
            if title.xpath('.//w:t[normalize-space(.)]', namespaces=ns):
                retained.append(title)
        boundaries = []
        for node in toc_range:
            section = node.find(f"{qn('w:pPr')}/{qn('w:sectPr')}")
            if section is not None:
                paragraph = OxmlElement("w:p")
                properties = OxmlElement("w:pPr")
                properties.append(copy.deepcopy(section)); paragraph.append(properties)
                boundaries.append(paragraph)
        for node in toc_range:
            toc_parent.remove(node)
        for offset, node in enumerate(retained):
            toc_parent.insert(toc_first + offset, node)
        toc_first += len(retained)
        toc_anchor = OxmlElement("w:p")
        ppr = OxmlElement("w:pPr")
        pstyle = OxmlElement("w:pStyle")
        pstyle.set(qn("w:val"), profile.get("toc_style_by_level", {}).get(1, profile.get("toc_style_by_level", {}).get("1", "")))
        ppr.append(pstyle); toc_anchor.append(ppr)
        field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), 'TOC \\o "1-3" \\h \\z \\u')
        field_run = OxmlElement("w:r"); field_text = OxmlElement("w:t"); field_text.text = "目录将在发布时更新"
        field_run.append(field_text); field.append(field_run); toc_anchor.append(field)
        toc_parent.insert(toc_first, control("pg-toc:placeholder", [toc_anchor]))
        for offset, node in enumerate(boundaries):
            toc_parent.insert(toc_first + 1 + offset, node)
    for node in removals:
        floating = [p for p in node.iterancestors() if etree.QName(p).localname in {"AlternateContent", "drawing", "pict"}]
        if floating:
            target = floating[-1]
            if target.getparent() is not None:
                target.getparent().remove(target)
        else:
            for item in list(node):
                if item.tag != qn("w:pPr"):
                    node.remove(item)
    for field, found in targets:
        if len(found) != 1:
            raise ProfileError("封面字段映射已失效")
        paragraph = found[0]
        for node in list(paragraph):
            if node.tag != qn("w:pPr"):
                paragraph.remove(node)
        if field["prefix"]:
            prefix = text_paragraph(field["prefix"], field["properties"])
            paragraph.extend(list(prefix)[1:])
        value = "" if meta is None else str(meta.get(field["key"], ""))
        content = text_paragraph(value, field["properties"])
        paragraph.append(control("pg-field:" + field["key"], list(content)[1:]))
    headings = profile["headings"]
    if profile.get("toc_active"):
        toc_levels={entry["target_key"]:entry["level"] for entry in profile.get("toc_entries",[])}
        for heading in headings:
            inferred=re.fullmatch(r"heading([1-9])",heading.get("kind",""))
            level=toc_levels.get(heading["key"],int(inferred.group(1)) if inferred else None)
            if level is None or not 1 <= int(level) <= 9:
                continue
            paragraph=original[heading["index"]]
            ppr=paragraph.find(qn("w:pPr"))
            if ppr is None:
                ppr=OxmlElement("w:pPr")
                paragraph.insert(0,ppr)
            outline=ppr.find(qn("w:outlineLvl"))
            if outline is None:
                outline=OxmlElement("w:outlineLvl")
                ppr.append(outline)
            outline.set(qn("w:val"),str(int(level)-1))
    for offset, heading in enumerate(headings):
        index = heading["index"]
        end = headings[offset+1]["index"] if offset+1 < len(headings) else len(original)
        node = original[index]
        if heading.get("inline"):
            for old in list(node):
                if old.tag != qn("w:pPr"):
                    node.remove(old)
            apply_properties(node,heading["properties"])
            prefix=text_paragraph(heading.get("display_label", heading["title"]),heading["title_properties"])
            delimiter=text_paragraph(heading["separator"],heading.get("separator_properties", heading["properties"]))
            node.extend(prefix.findall(qn("w:r")));node.extend(delimiter.findall(qn("w:r")))
            node.append(control("pg-region:"+heading["key"],[]))
            continue
        if heading["kind"] == "toc":
            continue
        # A source heading whose explicit number corrected its level uses a
        # source-bound sample of that level, including Word's outline level.
        if heading.get("title_style_id"):
            ppr = node.find(qn("w:pPr"))
            if ppr is None:
                ppr = OxmlElement("w:pPr"); node.insert(0, ppr)
            style = ppr.find(qn("w:pStyle"))
            if style is None:
                style = OxmlElement("w:pStyle"); ppr.insert(0, style)
            style.set(qn("w:val"), heading["title_style_id"])
            outline = ppr.find(qn("w:outlineLvl"))
            if outline is None:
                outline = OxmlElement("w:outlineLvl"); ppr.append(outline)
            outline.set(qn("w:val"), str(int(heading["kind"][-1]) - 1))
        # Do not rewrite original heading formatting unless explicitly overridden.
        if heading.get("title_overridden"):
            apply_properties(node, heading["title_properties"])
        if heading["kind"] in HEADING_KINDS and profile.get("digit_font_rule"):
            apply_digit_font(node, profile["digit_font_rule"]["font"])
        for old in original[index+1:end]:
            if old.tag == qn("w:sectPr"):
                continue
            if old.getparent() is not body:
                continue
            section = old.find(f"{qn('w:pPr')}/{qn('w:sectPr')}")
            explicit_breaks = old.xpath(".//w:br[@w:type='page' or @w:type='column']",namespaces=NS)
            if section is not None or explicit_breaks:
                for item in list(old):
                    if item.tag != qn("w:pPr"):
                        old.remove(item)
                if explicit_breaks:
                    run=etree.SubElement(old,qn("w:r"))
                    run.extend(copy.deepcopy(item) for item in explicit_breaks)
            else:
                body.remove(old)
        body.insert(list(body).index(node)+1, control("pg-region:"+heading["key"], []))
    bookmark_ids = [int(n.get(qn("w:id"))) for n in root.findall(".//"+qn("w:bookmarkStart")) if (n.get(qn("w:id")) or "").isdigit()]
    next_bookmark_id = max(bookmark_ids + [-1]) + 1
    for heading in headings:
        if heading.get("kind")=="toc":
            continue
        paragraph = original[heading["index"]]
        bookmark_name = heading.get("bookmark_name","")
        if not bookmark_name or paragraph.xpath(".//w:bookmarkStart[@w:name=$name]",namespaces=NS,name=bookmark_name):
            raise ProfileError("章节跳转书签名称冲突")
        start = OxmlElement("w:bookmarkStart"); start.set(qn("w:id"),str(next_bookmark_id)); start.set(qn("w:name"),bookmark_name)
        end = OxmlElement("w:bookmarkEnd"); end.set(qn("w:id"),str(next_bookmark_id)); next_bookmark_id += 1
        ppr = paragraph.find(qn("w:pPr"))
        paragraph.insert(paragraph.index(ppr)+1 if ppr is not None else 0,start)
        paragraph.append(end)
    from app.services.page_number_policy import apply_page_number_sections
    apply_page_number_sections(root, profile.get("body_page_number_policy"))
    return root


def page_box(root, source_index: int):
    body = root.find(qn("w:body"))
    width, height, left, right, top, bottom = 11906, 16838, 1440, 1440, 1440, 1440
    for block in list(body)[source_index:]:
        section = block if block.tag == qn("w:sectPr") else block.find(f"{qn('w:pPr')}/{qn('w:sectPr')}")
        if section is None:
            continue
        size, margins = section.find(qn("w:pgSz")), section.find(qn("w:pgMar"))
        if size is not None:
            width, height = int(size.get(qn("w:w"), width)), int(size.get(qn("w:h"), height))
        if margins is not None:
            left, right, top, bottom = [int(margins.get(qn("w:"+k), v)) for k,v in (("left",left),("right",right),("top",top),("bottom",bottom))]
        break
    return max(1, (width-left-right)/567), max(1, (height-top-bottom)/567 - 1.5)


def render_master_docx(project, template, out_path: str, *, source_path: str):
    source = Path(source_path).read_bytes()
    profile = verify_profile(template, source)
    source = commentless_source(source)
    if digest(package_manifest(source)) != profile["prepared_source_manifest_sha256"]:
        raise ProfileError("批注清理后的母版与已确认版本不一致，请重新确认")
    sections = {s.key: s for s in project.sections}
    expected = [h["key"] for h in profile["headings"] if h["kind"] != "toc"]
    if len(sections) != len(project.sections) or set(sections) != set(expected):
        raise ProfileError("项目章节与已确认母版不一致，请恢复冻结的章节结构")
    if any(sections[h["key"]].title!=h["title"] or sections[h["key"]].type!=h["kind"] for h in profile["headings"] if h["kind"]!="toc"):
        raise ProfileError("项目的章节标题或类型与冻结母版不一致")
    if profile.get("manuscript_headings_sha256") and digest([(s.type, s.title) for s in project.sections
            if s.type in {"heading1", "heading2", "heading3"}]) != profile["manuscript_headings_sha256"]:
        raise ProfileError("项目章节顺序与导入论文不一致，请重新预览导入")
    with zipfile.ZipFile(io.BytesIO(source)) as zf:
        source_root = load_part(zf, "word/document.xml")
        boxes = {h["key"]: h["page_box_cm"] for h in profile["headings"]}
        root = prepare_source(source_root, profile, project.meta)
        parts = {name: zf.read(name) for name in zf.namelist()}
    scratch = Document()
    blocks = []
    chapter = figure = table_number = 0
    for heading in profile["headings"]:
        if heading["kind"] == "toc":
            continue
        if heading["kind"] == "heading1":
            chapter += 1; figure = table_number = 0
        region = root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent", namespaces=NS, tag="pg-region:"+heading["key"])[0]
        props = heading["properties"]
        width, height = boxes[heading["key"]]
        reference_number = 0
        for block in sections[heading["key"]].paragraphs:
            if heading.get("inline") and (block.type != BlockType.PARAGRAPH or len(sections[heading["key"]].paragraphs)!=1 or '\n' in block.text or block.inline_runs):
                raise ProfileError("关键词区域只接受一段关键词文字")
            nodes = []
            if block.type == BlockType.PARAGRAPH:
                is_reference = heading["kind"] == "references" and bool(reference_block_text(block).strip())
                if is_reference:
                    reference_number += 1
                text = reference_block_text(block, reference_number_for(block, reference_number) if is_reference else None)
                if block.inline_runs and (is_reference or "".join(run.text for run in block.inline_runs) != block.text):
                    raise ProfileError("行内格式与段落文字不一致，无法保留原文")
                if block.inline_runs and not is_reference:
                    p = OxmlElement("w:p")
                    for inline in block.inline_runs:
                        fragment = text_paragraph(inline.text, props)
                        for run in fragment.findall(qn("w:r")):
                            p.append(run)
                            apply_content_decoration(run, block, inline)
                    # Paragraph properties apply before the explicit inline emphasis.
                    ppr = text_paragraph("", props).find(qn("w:pPr")); p.insert(0,ppr)
                else:
                    p = text_paragraph(text, props)
                    for run in p.findall(qn("w:r")):
                        apply_content_decoration(run, block)
                if heading["kind"] in BODY_KINDS and profile.get("digit_font_rule"):
                    apply_digit_font(p, profile["digit_font_rule"]["font"])
                nodes = [p]
            elif block.type == BlockType.IMAGE and block.image:
                figure += 1
                path = resolve_document_image_path(project.id, block.image.url)
                if path is None:
                    raise ProfileError("图片不属于当前项目或文件不存在")
                paragraph = scratch.add_paragraph()
                shape = paragraph.add_run().add_picture(str(path))
                ratio = float(shape.width) / float(shape.height)
                requested_w, requested_h = block.image.width_cm, block.image.height_cm
                # Imported Word images may intentionally have different display
                # extents from the embedded bitmap ratio. Preserve that source
                # display geometry, then scale the pair together to fit the
                # frozen template region.
                image_w = requested_w or (requested_h * ratio if requested_h else min(width, float(shape.width)/360000))
                image_h = requested_h or image_w/ratio
                scale = min(1, width/image_w, height/image_h)
                shape.width, shape.height = Cm(image_w*scale), Cm(image_h*scale)
                p = paragraph._p
                image_props = {k: v for k, v in props.items() if k != "paragraph.indent.hanging"}
                apply_properties(p, {**image_props, "paragraph.spacing.line": image_line_spacing(props),
                                     "paragraph.align": profile.get("image_alignment", "center"),
                                     "paragraph.indent.first_line": {"value":0,"unit":"character"},
                                     "paragraph.indent.left": {"value":0,"unit":"pt"},
                                     "paragraph.indent.right": {"value":0,"unit":"pt"}, "paragraph.keep_next": True})
                nodes.append(copy.deepcopy(p))
                caption = re.sub(r"^图\s*\d+(?:[.\-]\d+)*\s*", "", block.image.caption).strip()
                nodes.append(text_paragraph(f"图 {chapter}.{figure} {caption}".rstrip(), {**{k:v for k,v in profile["object_properties"]["figure_caption"].items() if k != "paragraph.indent.hanging"},"paragraph.align":"center","paragraph.indent.first_line":{"value":0,"unit":"character"},"paragraph.indent.left":{"value":0,"unit":"pt"},"paragraph.indent.right":{"value":0,"unit":"pt"},"paragraph.keep_lines":True}))
            elif block.type == BlockType.TABLE and block.table:
                table_number += 1
                data = block.table
                if data.rows < 1 or data.cols < 1 or len(data.cells) != data.rows or any(len(row) != data.cols for row in data.cells):
                    raise ProfileError("表格行列与单元格数据不一致")
                caption = re.sub(r"^表\s*\d+(?:[.\-]\d+)*\s*", "", data.caption).strip()
                nodes.append(text_paragraph(f"表 {chapter}.{table_number} {caption}".rstrip(), {**{k:v for k,v in profile["object_properties"]["table_caption"].items() if k != "paragraph.indent.hanging"},"paragraph.align":"center","paragraph.indent.first_line":{"value":0,"unit":"character"},"paragraph.indent.left":{"value":0,"unit":"pt"},"paragraph.indent.right":{"value":0,"unit":"pt"},"paragraph.keep_next":True,"paragraph.keep_lines":True}))
                table = scratch.add_table(rows=data.rows, cols=data.cols)
                table.autofit = False
                child(table._tbl.tblPr, "w:jc", val="center")
                from app.services.table_borders import EDGES, default_table_borders
                edges = profile.get("table_borders") or (data.borders.model_dump() if data.borders else None) or default_table_borders()
                borders = child(table._tbl.tblPr, "w:tblBorders")
                for edge in EDGES:
                    rule = edges[edge]
                    child(borders, "w:" + edge, val=rule["style"], color=rule["color"], sz=rule["size"], space=rule["space"])
                total = round(width*567)
                child(table._tbl.tblPr, "w:tblW", w=total, type="dxa")
                for c, column in enumerate(table.columns):
                    column.width = Cm(width/data.cols)
                for row_index, row in enumerate(table.rows):
                    if row_index == 0:
                        child(row._tr.get_or_add_trPr(), "w:tblHeader", val=1)
                    for col, cell in enumerate(row.cells):
                        cell.text = data.cells[row_index][col]
                        child(cell._tc.get_or_add_tcPr(), "w:tcW", w=round(total/data.cols), type="dxa")
                        for p in cell._tc.findall(qn("w:p")):
                            apply_properties(p,{**profile["object_properties"]["table_text"],"paragraph.indent.first_line":{"value":0,"unit":"character"}})
                nodes.append(copy.deepcopy(table._tbl))
            elif block.type == BlockType.EQUATION and block.equation:
                from app.services.docx_anchors import _safe_omml
                from app.domain.documents.schemas import ContentBlock
                p = text_paragraph("", props)
                math = _safe_omml(ContentBlock(id=block.id,kind="equation",data={"omml":block.equation.omml}))
                if math is None:
                    raise ProfileError("公式缺少有效 Office Math 数据")
                p.append(math)
                nodes.append(p)
            else:
                raise ProfileError("内容块类型或数据不受当前母版执行器支持")
            if heading.get("inline"):
                nodes = nodes[0].findall(qn("w:r"))
            tagged = control("pg-block:"+block.id, nodes)
            region.append(tagged)
            blocks.extend(nodes)
    add_block_bookmarks(root, project, profile)
    make_generated_content_visible(root)
    # The scratch document only supplies new images; existing source relationships remain untouched.
    buffer = io.BytesIO(); scratch.save(buffer)
    rels = etree.fromstring(parts["word/_rels/document.xml.rels"])
    existing_ids = {n.get("Id") for n in rels}
    image_index = 0
    with zipfile.ZipFile(buffer) as extra:
        extra_rels = load_part(extra,"word/_rels/document.xml.rels")
        for rel in extra_rels:
            if not rel.get("Type", "").endswith("/image"):
                continue
            image_index += 1
            new_id = "rIdPGImage"+str(image_index)
            while new_id in existing_ids:
                new_id += "X"
            existing_ids.add(new_id)
            old_id = rel.get("Id")
            old_part = posixpath.normpath(posixpath.join("word",rel.get("Target")))
            payload = extra.read(old_part)
            new_part = f"word/media/pg-{hashlib.sha256(payload).hexdigest()}{Path(old_part).suffix}"
            parts[new_part] = payload
            etree.SubElement(rels, "{"+REL+"}Relationship", Id=new_id, Type=rel.get("Type"), Target=new_part.removeprefix("word/"))
            for node in blocks:
                for blip in node.iter("{"+A+"}blip"):
                    if blip.get(qn("r:embed")) == old_id:
                        blip.set(qn("r:embed"),new_id)
        content_types = etree.fromstring(parts["[Content_Types].xml"])
        for ext in etree.fromstring(extra.read("[Content_Types].xml")):
            if ext.tag == "{"+CT+"}Default" and ext.get("ContentType", "").startswith("image/") and not any(e.get("Extension")==ext.get("Extension") for e in content_types):
                content_types.append(copy.deepcopy(ext))
    maximum = max([0]+[int(n.get("id",0)) for n in root.iter("{"+WP+"}docPr") if n.get("id", "0").isdigit()])
    for node in blocks:
        for docpr in node.iter("{"+WP+"}docPr"):
            maximum += 1; docpr.set("id",str(maximum))
    policy = profile.get("body_page_number_policy")
    if policy:
        from app.services.page_number_policy import CONTENT_TYPE, PART, REL_ID, REL_TYPE, footer_bytes
        footer = footer_bytes(policy["settings"])
        if (policy["part"] != PART or policy["relationship_id"] != REL_ID
                or hashlib.sha256(footer).hexdigest() != policy["footer_sha256"]):
            raise ProfileError("正文页码配置与已确认规则不一致")
        parts[PART] = footer
        etree.SubElement(rels, "{"+REL+"}Relationship", Id=REL_ID, Type=REL_TYPE, Target=PART.removeprefix("word/"))
        etree.SubElement(content_types, "{"+CT+"}Override", PartName="/"+PART, ContentType=CONTENT_TYPE)
    parts["word/document.xml"] = xml_bytes(root)
    parts["word/_rels/document.xml.rels"] = xml_bytes(rels)
    parts["[Content_Types].xml"] = xml_bytes(content_types)
    # The isolated Office worker writes measured field results before delivery.
    if "word/settings.xml" in parts:
        settings = etree.fromstring(parts["word/settings.xml"])
        child(settings,"w:updateFields",val=0)
        parts["word/settings.xml"] = xml_bytes(settings)
    with zipfile.ZipFile(out_path,"w",zipfile.ZIP_DEFLATED) as output:
        for name, payload in parts.items():
            output.writestr(name,payload)
    if profile.get("toc_active"):
        from app.services.office_layout import refresh_master_layout
        refresh_master_layout(Path(out_path), project, profile)
