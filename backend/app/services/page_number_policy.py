"""Prepare a reviewed, section-bound footer for body page numbers."""
from __future__ import annotations

import hashlib
import re
import zipfile

from lxml import etree

from app.core.extract.xml_utils import NS, load_part, qn
from app.services.source_template_profile import ProfileError

PART = "word/footerPGBody.xml"
REL_ID = "rIdPGBodyFooter"
REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"


def sections(root):
    body = root.find(qn("w:body"))
    if body is None:
        raise ProfileError("母版缺少正文，无法定位页码区域")
    result = []
    for node in body:
        section = node if node.tag == qn("w:sectPr") else node.find(f"{qn('w:pPr')}/{qn('w:sectPr')}")
        if section is not None:
            result.append(section)
    return result


def _footer_is_page_only(root):
    """Do not replace a footer with content or objects beyond a single PAGE field."""
    count, state, instruction = 0, None, ""
    for paragraph in root:
        if paragraph.tag != qn("w:p"):
            return False
        for child in paragraph:
            if child.tag == qn("w:pPr"):
                continue
            if child.tag == qn("w:fldSimple"):
                if state or not re.match(r"^\s*PAGE(?:\s|$)", child.get(qn("w:instr"), ""), re.I):
                    return False
                if any(n.tag not in {qn("w:fldSimple"), qn("w:r"), qn("w:rPr"), qn("w:t")} and
                       not any(a.tag == qn("w:rPr") for a in n.iterancestors()) for n in child.iter()):
                    return False
                count += 1
                continue
            if child.tag != qn("w:r"):
                return False
            for item in child:
                if item.tag == qn("w:rPr"):
                    continue
                if item.tag == qn("w:fldChar"):
                    kind = item.get(qn("w:fldCharType"))
                    if kind == "begin" and state is None:
                        state, instruction = "instruction", ""
                    elif kind == "separate" and state == "instruction":
                        if not re.match(r"^\s*PAGE(?:\s|$)", instruction, re.I):
                            return False
                        state = "result"
                    elif kind == "end" and state == "result":
                        count += 1
                        state = None
                    else:
                        return False
                elif item.tag == qn("w:instrText") and state == "instruction":
                    instruction += item.text or ""
                elif item.tag == qn("w:t") and (state == "result" or not (item.text or "").strip()):
                    continue
                else:
                    return False
    return state is None and count <= 1


def footer_bytes(settings, *, dirty=False):
    footer = etree.Element(qn("w:ftr"), nsmap={"w": NS["w"]})
    paragraph = etree.SubElement(footer, qn("w:p"))
    ppr = etree.SubElement(paragraph, qn("w:pPr"))
    etree.SubElement(ppr, qn("w:jc")).set(qn("w:val"), "center")
    field = etree.SubElement(paragraph, qn("w:fldSimple"))
    field.set(qn("w:instr"), " PAGE ")
    field.set(qn("w:dirty"), "true" if dirty else "false")
    run = etree.SubElement(field, qn("w:r"))
    rpr = etree.SubElement(run, qn("w:rPr"))
    fonts = etree.SubElement(rpr, qn("w:rFonts"))
    for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn("w:" + slot), settings["font"])
    for tag in ("sz", "szCs"):
        etree.SubElement(rpr, qn("w:" + tag)).set(qn("w:val"), str(round(settings["size_pt"] * 2)))
    etree.SubElement(run, qn("w:t")).text = "1"
    return etree.tostring(footer, xml_declaration=True, encoding="UTF-8", standalone=True)


def compile_page_number_policy(archive: zipfile.ZipFile, root, headings, settings):
    if not settings:
        return None
    body = root.find(qn("w:body"))
    first = next((h for h in headings if h.get("kind") == "heading1"), None)
    if body is None or first is None:
        raise ProfileError("正文页码规则需要可识别的一级标题")
    children = list(body)
    index = first["index"]
    if not 0 <= index < len(children):
        raise ProfileError("正文页码起始章节已失效")
    boundaries, start, body_index = sections(root), 0, None
    for pos, node in enumerate(children):
        if pos == index:
            body_index = start
        section = node if node.tag == qn("w:sectPr") else node.find(f"{qn('w:pPr')}/{qn('w:sectPr')}")
        if section is not None:
            start += 1
    if body_index is None or body_index >= len(boundaries):
        raise ProfileError("无法将正文标题绑定到分节")
    previous_boundary = max((i for i, node in enumerate(children[:index])
        if node.tag == qn("w:sectPr") or node.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None), default=-1)
    if any(node.xpath(".//w:t[normalize-space(.)] | .//w:drawing | .//w:pict | self::w:tbl", namespaces=NS)
           for node in children[previous_boundary + 1:index]):
        raise ProfileError("正文起始分节包含前置内容，无法单独设置正文页码")
    settings_xml = load_part(archive, "word/settings.xml")
    even = settings_xml.find(qn("w:evenAndOddHeaders")) if settings_xml is not None else None
    if even is not None and even.get(qn("w:val"), "1") not in {"0", "false", "off"}:
        raise ProfileError("奇偶页页脚尚不能自动套用正文页码规则")
    rels = load_part(archive, "word/_rels/document.xml.rels")
    by_id = {node.get("Id"): node for node in rels}
    if PART in archive.namelist() or REL_ID in by_id:
        raise ProfileError("正文页码部件与母版已有关系冲突")
    current = None
    for i, section in enumerate(boundaries):
        refs = section.findall(qn("w:footerReference"))
        defaults = [ref for ref in refs if ref.get(qn("w:type")) == "default"]
        if len(defaults) > 1:
            raise ProfileError("母版页脚关系不唯一")
        if defaults:
            current = defaults[0].get(qn("r:id"))
        if i < body_index:
            continue
        if section.find(qn("w:titlePg")) is not None or any(ref.get(qn("w:type")) != "default" for ref in refs):
            raise ProfileError("正文特殊首页或奇偶页页脚尚不能自动套用页码规则")
        if current:
            rel = by_id.get(current)
            target = rel.get("Target", "") if rel is not None else ""
            if rel is None or rel.get("Type") != REL_TYPE or rel.get("TargetMode") is not None or not re.fullmatch(r"footer[^/]*\.xml", target):
                raise ProfileError("正文页脚关系无法安全解析")
            part = "word/" + target
            if part not in archive.namelist() or not _footer_is_page_only(load_part(archive, part)):
                raise ProfileError("正文页脚含其他内容，不能自动替换；请先完成模板适配")
    payload = footer_bytes(settings)
    return {"settings": settings, "section_count": len(boundaries), "body_section_start": body_index,
            "part": PART, "relationship_id": REL_ID, "footer_sha256": hashlib.sha256(payload).hexdigest()}


def apply_page_number_sections(root, policy):
    if not policy:
        return
    found = sections(root)
    start = policy["body_section_start"]
    if len(found) != policy["section_count"] or not 0 <= start < len(found):
        raise ProfileError("正文页码分节映射已变化")
    for section in found[start:]:
        refs = [ref for ref in section.findall(qn("w:footerReference")) if ref.get(qn("w:type")) == "default"]
        if len(refs) > 1:
            raise ProfileError("正文页脚关系不唯一")
        if refs:
            ref = refs[0]
        else:
            ref = etree.Element(qn("w:footerReference"))
            position = sum(1 for node in section if node.tag in {qn("w:headerReference"), qn("w:footerReference")})
            section.insert(position, ref)
        ref.set(qn("w:type"), "default")
        ref.set(qn("r:id"), policy["relationship_id"])
