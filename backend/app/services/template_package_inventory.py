"""Inventory a template's original OPC package without using its semantic parser.

An inventory match is only structural evidence. It does not approve a template,
prove formatting support, or replace the required independent approval and tests.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import json
import posixpath
import re
import zipfile
from typing import Any

from lxml import etree

from app.core.extract.xml_utils import safe_xml_parser
from app.services.upload_security import DOCX_MAX_BYTES, validate_docx_bytes

INVENTORY_VERSION = "template-package-inventory/1"
ALLOWLIST_FORMAT = "template-package-allowlist/1"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_FIELD_CHAR = f"{{{_W}}}fldChar"
_FIELD_TYPE = f"{{{_W}}}fldCharType"
_FIELD_TEXT = f"{{{_W}}}instrText"
_SIMPLE_FIELD = f"{{{_W}}}fldSimple"
_FIELD_INSTR = f"{{{_W}}}instr"
_FINDING_LIMIT = 250


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _xml(data: bytes, part: str):
    try:
        root = etree.fromstring(data, safe_xml_parser())
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"模板部件 XML 无法解析：{part}") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError(f"模板部件不允许 DTD：{part}")
    return root


def _content_types(data: bytes) -> tuple[dict[str, str], dict[str, str]]:
    root = _xml(data, "[Content_Types].xml")
    if root.tag != f"{{{_CT}}}Types":
        raise ValueError("模板内容类型表根节点无效")
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for item in root:
        if item.tag == f"{{{_CT}}}Default":
            name, value = item.get("Extension", "").lower(), item.get("ContentType", "")
            if not name or not value or name in defaults:
                raise ValueError("模板内容类型默认项缺失或重复")
            defaults[name] = value
        elif item.tag == f"{{{_CT}}}Override":
            name, value = item.get("PartName", ""), item.get("ContentType", "")
            if not name.startswith("/") or not value or name in overrides:
                raise ValueError("模板内容类型部件项缺失或重复")
            overrides[name] = value
        # Unknown nodes are inventoried and compared to the allowlist below.
    return defaults, overrides


def _part_content_type(name: str, defaults: dict[str, str], overrides: dict[str, str]) -> str:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return overrides.get("/" + name, defaults.get(extension, ""))


def _is_xml_part(name: str, content_type: str) -> bool:
    lowered = content_type.lower().split(";", 1)[0].strip()
    return (name == "[Content_Types].xml" or name.lower().endswith((".xml", ".rels"))
            or lowered.endswith("+xml") or lowered in {"application/xml", "text/xml"})


def _element_path(node) -> tuple[str, ...]:
    return tuple(reversed([node.tag, *(parent.tag for parent in node.iterancestors())]))


def _validate_allowlist(allowlist: dict[str, Any]) -> tuple[dict, dict, set, dict]:
    if not isinstance(allowlist, dict) or allowlist.get("format") != ALLOWLIST_FORMAT:
        raise ValueError("模板结构白名单格式无效")
    if not isinstance(allowlist.get("version"), str) or not allowlist["version"].strip():
        raise ValueError("模板结构白名单缺少版本")
    parts = allowlist.get("parts")
    if not isinstance(parts, dict) or any(
        not isinstance(name, str) or not name or kind not in {"xml", "binary"}
        for name, kind in parts.items()
    ):
        raise ValueError("模板结构白名单部件配置无效")
    namespaces = allowlist.get("namespaces")
    if not isinstance(namespaces, list) or any(not isinstance(item, str) or not item or "*" in item for item in namespaces):
        raise ValueError("模板结构白名单命名空间配置无效")

    def path_key(item):
        path = item.get("path")
        if (item.get("part") not in parts or parts[item["part"]] != "xml"
                or not isinstance(path, list) or not path
                or any(not isinstance(name, str) or not name or "*" in name for name in path)):
            raise ValueError("模板结构白名单元素上下文无效")
        return item["part"], tuple(path)

    elements = {}
    rules = allowlist.get("elements")
    if not isinstance(rules, list):
        raise ValueError("模板结构白名单缺少元素配置")
    for item in rules:
        if not isinstance(item, dict):
            raise ValueError("模板结构白名单元素配置无效")
        key = path_key(item)
        if key in elements or not isinstance(item.get("attributes"), dict):
            raise ValueError("模板结构白名单元素重复或属性配置无效")
        attributes = {}
        for name, constraint in item["attributes"].items():
            if not isinstance(name, str) or not name or "*" in name or not isinstance(constraint, dict):
                raise ValueError("模板结构白名单属性配置无效")
            if set(constraint) == {"values"} and isinstance(constraint["values"], list) and constraint["values"] and all(isinstance(v, str) for v in constraint["values"]):
                attributes[name] = ("values", frozenset(constraint["values"]))
            elif set(constraint) == {"pattern"} and isinstance(constraint["pattern"], str) and len(constraint["pattern"]) <= 256:
                try:
                    attributes[name] = ("pattern", re.compile(constraint["pattern"]))
                except re.error as exc:
                    raise ValueError("模板结构白名单属性规则无效") from exc
            else:
                raise ValueError("模板结构白名单属性必须限定允许值或表达式")
        elements[key] = attributes

    fields = {}
    rules = allowlist.get("fields")
    if not isinstance(rules, list):
        raise ValueError("模板结构白名单缺少域配置")
    for item in rules:
        if not isinstance(item, dict):
            raise ValueError("模板结构白名单域配置无效")
        key = path_key(item)
        operation, pattern = item.get("operation"), item.get("pattern")
        if not isinstance(operation, str) or not re.fullmatch(r"[A-Z]+", operation) or not isinstance(pattern, str) or len(pattern) > 256:
            raise ValueError("模板结构白名单域规则无效")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError("模板结构白名单域表达式无效") from exc
        fields.setdefault(key, []).append((operation, compiled))
    return parts, elements, set(namespaces), fields


def inventory_template_package(data: bytes, allowlist: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inventory original package parts; compare every observed structure if configured.

    Values and document text are not returned. Attribute and field values are
    only compared in memory, and the report records hashes for correlation.
    """
    if len(data) > DOCX_MAX_BYTES:
        raise ValueError("Word 模板超过上传大小限制")
    validate_docx_bytes(data)
    policy = _validate_allowlist(allowlist) if allowlist is not None else None
    parts_policy, elements_policy, namespaces_policy, fields_policy = policy or ({}, {}, set(), {})
    findings: list[dict[str, Any]] = []
    finding_count = 0
    parts: list[dict[str, Any]] = []
    elements: dict[tuple, dict[str, Any]] = {}
    attributes: dict[tuple, dict[str, Any]] = {}
    namespaces: Counter = Counter()
    relationships: list[dict[str, str]] = []
    fields: list[dict[str, str]] = []
    xml_nodes: list[dict[str, str]] = []

    def finding(code: str, part: str, location: str, **details: str) -> None:
        nonlocal finding_count
        finding_count += 1
        if len(findings) < _FINDING_LIMIT:
            findings.append({"code": code, "part": part, "location": location, **details})

    def record_field(part: str, node, instruction: str) -> None:
        location = node.getroottree().getpath(node)
        normalized = instruction.strip()
        operation_match = re.match(r"([A-Za-z]+)(?:\s|\\|$)", normalized)
        operation = operation_match.group(1).upper() if operation_match else ""
        fields.append({"part": part, "location": location, "operation": operation,
                       "instruction_sha256": _sha256(normalized.encode("utf-8"))})
        if policy is not None:
            rules = fields_policy.get((part, _element_path(node)), [])
            if not operation or len(normalized) > 4096 or not any(
                op == operation and pattern.fullmatch(normalized) for op, pattern in rules
            ):
                finding("unknown_field", part, location, operation=operation)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("模板含重复部件路径")
        if any(name.startswith(("/", "\\")) or "\\" in name or name != posixpath.normpath(name)
               or name == ".." or name.startswith("../") for name in names):
            raise ValueError("模板含无效部件路径")
        defaults, overrides = _content_types(archive.read("[Content_Types].xml"))
        for name in sorted(names):
            info = archive.getinfo(name)
            if info.is_dir():
                raise ValueError("模板含目录条目，无法确认包部件")
            content = archive.read(name)
            content_type = "application/xml" if name == "[Content_Types].xml" else _part_content_type(name, defaults, overrides)
            is_xml = _is_xml_part(name, content_type)
            kind = "xml" if is_xml else "binary"
            parts.append({"name": name, "kind": kind, "content_type": content_type,
                          "size": len(content), "sha256": _sha256(content)})
            if policy is not None and parts_policy.get(name) != kind:
                finding("unknown_part", name, name)
            if name != "[Content_Types].xml" and not content_type:
                finding("missing_content_type", name, name)
            if not is_xml:
                continue
            root = _xml(content, name)
            stack: list[dict[str, Any]] = []
            sibling = root.getprevious()
            while sibling is not None:
                node_kind = "comment" if isinstance(sibling, etree._Comment) else "processing_instruction"
                xml_nodes.append({"part": name, "location": "before-root", "kind": node_kind})
                if policy is not None:
                    finding("unknown_xml_node", name, "before-root", kind=node_kind)
                sibling = sibling.getprevious()
            sibling = root.getnext()
            while sibling is not None:
                node_kind = "comment" if isinstance(sibling, etree._Comment) else "processing_instruction"
                xml_nodes.append({"part": name, "location": "after-root", "kind": node_kind})
                if policy is not None:
                    finding("unknown_xml_node", name, "after-root", kind=node_kind)
                sibling = sibling.getnext()
            for node in root.iter():
                if not isinstance(node.tag, str):
                    node_kind = "comment" if isinstance(node, etree._Comment) else "processing_instruction"
                    location = node.getroottree().getpath(node)
                    xml_nodes.append({"part": name, "location": location, "kind": node_kind})
                    if policy is not None:
                        finding("unknown_xml_node", name, location, kind=node_kind)
                    continue
                path = _element_path(node)
                key = (name, path)
                location = node.getroottree().getpath(node)
                item = elements.setdefault(key, {"part": name, "path": list(path), "count": 0, "first_location": location})
                item["count"] += 1
                allowed_attributes = elements_policy.get(key)
                if policy is not None and allowed_attributes is None:
                    finding("unknown_element", name, location, qname=node.tag)
                parent = node.getparent()
                parent_namespaces = parent.nsmap if parent is not None else {}
                for prefix, uri in node.nsmap.items():
                    if parent_namespaces.get(prefix) != uri:
                        namespaces[(name, prefix or "", uri)] += 1
                        if policy is not None and uri not in namespaces_policy:
                            finding("unknown_namespace", name, location, namespace=uri)
                for attr_name, value in node.attrib.items():
                    attr_key = (name, path, attr_name)
                    item = attributes.setdefault(attr_key, {"part": name, "path": list(path), "qname": attr_name,
                                                            "count": 0, "first_location": location, "_hashes": set()})
                    item["count"] += 1
                    item["_hashes"].add(_sha256(value.encode("utf-8")))
                    if policy is None or allowed_attributes is None:
                        continue
                    constraint = allowed_attributes.get(attr_name)
                    if constraint is None:
                        finding("unknown_attribute", name, location, qname=attr_name)
                    elif len(value) > 4096 or (constraint[0] == "values" and value not in constraint[1]) or (
                        constraint[0] == "pattern" and not constraint[1].fullmatch(value)
                    ):
                        finding("unsupported_attribute_value", name, location, qname=attr_name)
                    if attr_name in {f"{{{_MC}}}Ignorable", f"{{{_MC}}}MustUnderstand"} or (
                        node.tag == f"{{{_MC}}}Choice" and attr_name == "Requires"
                    ):
                        for prefix in value.split():
                            uri = node.nsmap.get(prefix)
                            if not uri or uri not in namespaces_policy:
                                finding("unknown_compatibility_prefix", name, location, prefix=prefix)
                    elif attr_name in {f"{{{_MC}}}ProcessContent", f"{{{_MC}}}PreserveElements", f"{{{_MC}}}PreserveAttributes"}:
                        for qname in value.split():
                            prefix, separator, local = qname.partition(":")
                            uri = node.nsmap.get(prefix) if separator and local else None
                            if not uri or uri not in namespaces_policy:
                                finding("unknown_compatibility_prefix", name, location, prefix=prefix)
                if node.tag == f"{{{_REL}}}Relationship":
                    relationships.append({"part": name, "location": location, "type": node.get("Type", ""),
                                          "target_mode": node.get("TargetMode", "Internal"),
                                          "target_sha256": _sha256(node.get("Target", "").encode("utf-8"))})
                if node.tag == _SIMPLE_FIELD:
                    record_field(name, node, node.get(_FIELD_INSTR, ""))
                elif node.tag == _FIELD_CHAR:
                    field_type = node.get(_FIELD_TYPE, "").lower()
                    if field_type == "begin":
                        stack.append({"node": node, "text": [], "separated": False})
                    elif field_type == "separate" and stack and not stack[-1]["separated"]:
                        stack[-1]["separated"] = True
                    elif field_type == "end" and stack:
                        field = stack.pop()
                        record_field(name, field["node"], "".join(field["text"]))
                    else:
                        finding("invalid_field_structure", name, location)
                elif node.tag == _FIELD_TEXT:
                    if not stack or stack[-1]["separated"]:
                        finding("invalid_field_structure", name, location)
                    else:
                        stack[-1]["text"].append(node.text or "")
            for field in stack:
                finding("invalid_field_structure", name, field["node"].getroottree().getpath(field["node"]))

    for item in attributes.values():
        item["distinct_value_count"] = len(item.pop("_hashes"))
    return {
        "inventory_version": INVENTORY_VERSION,
        "source_sha256": _sha256(data),
        "allowlist_version": allowlist["version"] if allowlist is not None else None,
        "allowlist_sha256": _canonical_sha256(allowlist) if allowlist is not None else None,
        "status": "inventory_only" if policy is None else "unknown_structure" if finding_count else "structurally_matched",
        "formal_admission": False,
        "parts": parts,
        "elements": list(elements.values()),
        "attributes": list(attributes.values()),
        "namespaces": [{"part": part, "prefix": prefix, "uri": uri, "count": count}
                       for (part, prefix, uri), count in sorted(namespaces.items())],
        "relationships": relationships,
        "fields": fields,
        "xml_nodes": xml_nodes,
        "finding_count": finding_count,
        "findings_truncated": finding_count > len(findings),
        "findings": findings,
    }


__all__ = ["ALLOWLIST_FORMAT", "INVENTORY_VERSION", "inventory_template_package"]
