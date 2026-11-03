"""Source-only layout observations; never an execution profile or layout receipt.

No source section, header/footer or cached page number grants formatting authority.
Dispositions describe work required by normalization, not work already performed.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from io import BytesIO
import posixpath
import re
from zipfile import ZipFile

from lxml import etree

from app.core.extract.xml_utils import parse_xml, qn
from app.services.upload_security import DOCX_MAX_BYTES, validate_docx_bytes

LAYOUT_INVENTORY_VERSION = "docx-import-layout/1"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SUPPORTED_FIELDS = {"TOC", "PAGEREF", "SEQ", "PAGE"}
_HEADER_CONTENT = {qn("w:" + name) for name in (
    "hdr", "ftr", "p", "r", "t", "tab", "br", "cr", "fldSimple", "fldChar",
    "instrText", "hyperlink", "bookmarkStart", "bookmarkEnd", "proofErr",
    "lastRenderedPageBreak", "tbl", "tr", "tc", "tblGrid", "gridCol",
)}
_PROPERTY_CONTAINERS = {qn("w:" + name) for name in ("pPr", "rPr", "tblPr", "trPr", "tcPr", "tblPrEx")}
_SECTION_PROPERTIES = {
    "footnotePr", "endnotePr", "type", "pgSz", "pgMar", "paperSrc", "pgBorders",
    "lnNumType", "pgNumType", "cols", "formProt", "vAlign", "noEndnote",
    "titlePg", "textDirection", "bidi", "rtlGutter", "docGrid", "printerSettings",
}


def _location(part: str, node) -> str:
    return f"{part}:{node.getroottree().getpath(node)}"


def _properties(node) -> dict:
    # Clark names retain namespace identity, including unfamiliar attributes.
    return {"tag": node.tag, "attributes": dict(sorted(node.attrib.items())),
            "children": [_properties(child) for child in node if isinstance(child.tag, str)]}


def _part_target(part: str, target: str) -> str | None:
    if not target or any(value in target for value in ("\\", "#", "?", ":")):
        return None
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                             else posixpath.join(posixpath.dirname(part), target))
    return path if path.startswith("word/") else None


def _fields(root, part: str, unresolved: list[dict]) -> list[dict]:
    fields: list[dict] = []
    stack: list[dict] = []

    def issue(node, reason):
        unresolved.append({"kind": "field", "location": _location(part, node), "reason": reason})

    def new(node, representation, instruction=""):
        item = {"location": _location(part, node), "part": part,
                "representation": representation, "instruction": instruction,
                "cached_text": "", "has_cached_result": representation == "simple",
                "complete": representation == "simple", "depth": len(stack),
                "dirty": node.get(qn("w:dirty")), "locked": node.get(qn("w:fldLock")),
                "_phase": "result" if representation == "simple" else "instruction"}
        fields.append(item)
        stack.append(item)
        return item

    for event, node in etree.iterwalk(root, events=("start", "end")):
        if event == "end":
            if node.tag == qn("w:fldSimple"):
                location = _location(part, node)
                if stack and stack[-1]["location"] == location:
                    stack.pop()
                else:
                    issue(node, "简单字段中含未闭合或交叉的复杂字段")
                    while stack:
                        item = stack.pop()
                        item["complete"] = False
                        if item["location"] == location:
                            break
            continue
        if node.tag == qn("w:fldSimple"):
            new(node, "simple", node.get(qn("w:instr"), ""))
        elif node.tag == qn("w:fldChar"):
            kind = node.get(qn("w:fldCharType"), "")
            if kind == "begin":
                new(node, "complex")
            elif kind in {"separate", "end"} and stack and stack[-1]["representation"] == "complex":
                if kind == "separate":
                    if stack[-1]["_phase"] == "result":
                        stack[-1]["complete"] = False
                        stack[-1]["_malformed"] = True
                        issue(node, "复杂字段包含重复结果分隔符")
                    stack[-1]["_phase"] = "result"
                    stack[-1]["has_cached_result"] = True
                else:
                    stack.pop()["complete"] = True
            else:
                issue(node, "字段控制符未知或缺少匹配的开始标记")
        elif node.tag == qn("w:instrText"):
            if stack and stack[-1]["representation"] == "complex" and stack[-1]["_phase"] == "instruction":
                stack[-1]["instruction"] += node.text or ""
            else:
                issue(node, "字段指令不在有效的复杂字段指令区间内")
        elif node.tag in {qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")}:
            value = (node.text or "") if node.tag == qn("w:t") else ("\t" if node.tag == qn("w:tab") else "\n")
            for item in stack:
                if item["_phase"] == "result":
                    item["cached_text"] += value
    for item in stack:
        item["complete"] = False
    for item in fields:
        item.pop("_phase")
        if item.pop("_malformed", False):
            item["complete"] = False
        match = re.match(r"\s*([A-Za-z]+)(?:\s|$)", item["instruction"])
        item["field_type"] = match.group(1).upper() if match else "UNKNOWN"
        item["supported"] = item["field_type"] in _SUPPORTED_FIELDS and item["complete"]
        item["cache_authoritative"] = False
        if not item["supported"]:
            unresolved.append({"kind": "field", "location": item["location"],
                               "reason": "字段类型未知或字段结构不完整，需要人工确认"})
    return fields


def inventory_docx_layout(data: bytes) -> dict:
    """Extract source sections, header/footer parts and fields without importer data.

    ``preserve`` means preserve the source observation, ``convert`` requires the
    target template/system to replace formatting/fields, and ``exclude`` rejects
    using source pagination caches as evidence of final pagination. No content is
    deleted by this read-only inventory. Unknown structures stay unresolved.
    """
    if len(data) > DOCX_MAX_BYTES:
        raise ValueError("Word 文件超过导入大小限制")
    validate_docx_bytes(data)
    result = {"version": LAYOUT_INVENTORY_VERSION, "source_sha256": sha256(data).hexdigest(),
              "sections": [], "header_footer_parts": [], "fields": [],
              "dispositions": [], "unresolved": [], "authority": "source_observation_only",
              "scope": ["document", "header", "footer"]}
    unresolved = result["unresolved"]

    def issue(kind, location, reason):
        unresolved.append({"kind": kind, "location": location, "reason": reason})

    def disposition(action, kind, location, reason):
        result["dispositions"].append({"action": action, "kind": kind, "location": location,
                                       "reason": reason, "applied": False})

    with ZipFile(BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Word 文件包含重复的包内路径，无法安全盘点版式")
        root = parse_xml(archive.read("word/document.xml"))
        if root.tag != qn("w:document") or root.find(qn("w:body")) is None:
            raise ValueError("Word 正文结构无效")
        relationships = {}
        rel_part = "word/_rels/document.xml.rels"
        if rel_part in names:
            rel_root = parse_xml(archive.read(rel_part))
            if rel_root.tag != f"{{{REL}}}Relationships":
                raise ValueError("Word 关系文件无效")
            for relation in rel_root:
                identifier = relation.get("Id", "")
                if relation.tag != f"{{{REL}}}Relationship" or not identifier or identifier in relationships:
                    raise ValueError("Word 关系标识缺失、重复或结构无效")
                relationships[identifier] = dict(relation.attrib)
        result["fields"].extend(_fields(root, "word/document.xml", unresolved))
        referenced_parts = set()
        for index, section in enumerate(root.iter(qn("w:sectPr")), 1):
            location = _location("word/document.xml", section)
            parent = section.getparent()
            active = (parent is not None and (parent.tag == qn("w:body") or
                      (parent.tag == qn("w:pPr") and parent.getparent() is not None
                       and parent.getparent().tag == qn("w:p"))))
            if not active:
                issue("section", location, "节属性位于非活动或未知结构（含修订历史）中")
            item = {"index": index, "location": location, "active_location": active,
                    "attributes": dict(section.attrib), "properties": [], "references": []}
            for child in section:
                if not isinstance(child.tag, str):
                    continue
                if child.tag in {qn("w:headerReference"), qn("w:footerReference")}:
                    kind = "header" if child.tag == qn("w:headerReference") else "footer"
                    identifier = child.get(f"{{{R}}}id", "")
                    relation = relationships.get(identifier, {})
                    path = _part_target("word/document.xml", relation.get("Target", ""))
                    valid = (relation.get("TargetMode", "").lower() != "external" and
                             relation.get("Type") == f"{R}/{kind}" and path in names)
                    reference = {"kind": kind, "type": child.get(qn("w:type")),
                                 "relationship_id": identifier, "target": path,
                                 "resolved": bool(valid), "location": _location("word/document.xml", child)}
                    item["references"].append(reference)
                    if valid:
                        referenced_parts.add((path, kind))
                    else:
                        issue("header_footer_reference", reference["location"], "页眉/页脚关系缺失、外部、类型错误或目标部件不存在")
                    if reference["type"] not in {"default", "first", "even"}:
                        issue("header_footer_reference", reference["location"], "页眉/页脚引用类型未知")
                else:
                    item["properties"].append(_properties(child))
                    if child.tag not in {qn("w:" + name) for name in _SECTION_PROPERTIES}:
                        issue("section_property", _location("word/document.xml", child), "未知节属性或修订结构需确认")
            result["sections"].append(item)
            disposition("preserve", "section_observation", location, "保留源节属性与关系的事实记录")
            disposition("convert", "section_layout", location, "源节属性不覆盖目标模板，须由目标版式重新生成")
        # Include conventional orphan parts as observations; references may use arbitrary names.
        part_kinds = {path: kind for path, kind in referenced_parts}
        for name in names:
            match = re.fullmatch(r"word/(header|footer)[^/]*\.xml", name)
            if match:
                part_kinds.setdefault(name, match.group(1))
        for path, kind in sorted(part_kinds.items()):
            raw = archive.read(path)
            item = {"part": path, "kind": kind, "sha256": sha256(raw).hexdigest(),
                    "byte_size": len(raw), "referenced": (path, kind) in referenced_parts}
            result["header_footer_parts"].append(item)
            try:
                content = parse_xml(raw)
            except etree.XMLSyntaxError:
                issue("header_footer_part", path, "页眉/页脚 XML 无法解析")
                continue
            expected = qn("w:hdr" if kind == "header" else "w:ftr")
            if content.tag != expected:
                issue("header_footer_part", path, "页眉/页脚部件根节点错误")
                continue
            item.update({"text": "".join(node.text or "" for node in content.iter(qn("w:t"))),
                         "paragraph_count": len(list(content.iter(qn("w:p")))),
                         "table_count": len(list(content.iter(qn("w:tbl")))),
                         "drawing_count": len(list(content.iter(qn("w:drawing"))))})
            part_fields = _fields(content, path, unresolved)
            result["fields"].extend(part_fields)
            item["field_locations"] = [field["location"] for field in part_fields]
            # Complex header objects require their own import mapping, not flattening.
            for node in content.iter():
                if (not isinstance(node.tag, str) or node.tag in _PROPERTY_CONTAINERS
                        or any(ancestor.tag in _PROPERTY_CONTAINERS for ancestor in node.iterancestors())):
                    continue
                if node.tag not in _HEADER_CONTENT:
                    issue("header_footer_structure", _location(path, node), "页眉/页脚含对象或未知结构，需独立确认")
            disposition("preserve", "header_footer_observation", path, "保留部件摘要与可见内容，避免静默丢弃")
            disposition("convert", "header_footer_layout", path, "页眉/页脚内容须确认映射，版式由目标模板生成")
        for field in result["fields"]:
            disposition("preserve", "field_observation", field["location"], "保留指令、源缓存与位置供对象身份转换和对账")
            if field["supported"]:
                disposition("convert", "field", field["location"], "字段须绑定目标对象或由最终排版引擎重新生成和计算")
            if field["has_cached_result"]:
                disposition("exclude", "field_cache_authority", field["location"], "源字段缓存不作为最终目录、编号或页码正确性的证据")
    result["summary"] = {"section_count": len(result["sections"]),
                         "header_footer_part_count": len(result["header_footer_parts"]),
                         "field_types": dict(sorted(Counter(field["field_type"] for field in result["fields"]).items())),
                         "unresolved_count": len(unresolved)}
    result["status"] = "needs_review" if unresolved else "observed"
    return result
