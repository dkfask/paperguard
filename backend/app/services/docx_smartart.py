"""Read and write the inline, self-contained SmartArt used in 1101 samples."""

import hashlib
import posixpath
import re

from lxml import etree

from app.core.extract.xml_utils import safe_xml_parser

DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
DSP = "http://schemas.microsoft.com/office/drawing/2008/diagram"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
THEME_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
SETTINGS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
GRAPHIC_DATA = f"{{{A}}}graphicData"
PARTS = {
    "dm": (f"{{{DGM}}}dataModel", f"{R}/diagramData", "openxmlformats-officedocument.drawingml.diagramData"),
    "lo": (f"{{{DGM}}}layoutDef", f"{R}/diagramLayout", "openxmlformats-officedocument.drawingml.diagramLayout"),
    "qs": (f"{{{DGM}}}styleDef", f"{R}/diagramQuickStyle", "openxmlformats-officedocument.drawingml.diagramStyle"),
    "cs": (f"{{{DGM}}}colorsDef", f"{R}/diagramColors", "openxmlformats-officedocument.drawingml.diagramColors"),
    "dr": (f"{{{DSP}}}drawing", "http://schemas.microsoft.com/office/2007/relationships/diagramDrawing",
           "ms-office.drawingml.diagramDrawing"),
}
MAX_PART_BYTES = 64 * 1024
_HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")
_SCHEME_SLOTS = {
    "dk1", "lt1", "dk2", "lt2",
    "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
    "hlink", "folHlink",
}
_COLOR_MAP_KEYS = {
    "bg1": "bg1",
    "t1": "tx1",
    "bg2": "bg2",
    "t2": "tx2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}
_COLOR_MAP_TARGETS = {
    "dark1": "dk1",
    "light1": "lt1",
    "dark2": "dk2",
    "light2": "lt2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}
_DEFAULT_COLOR_MAP = {
    "bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2",
    **{slot: slot for slot in _SCHEME_SLOTS},
}


def read_theme_colors(theme_blob: bytes | None, settings_blob: bytes | None = None) -> dict[str, str]:
    """Resolve theme color names to RGB hex using the source package's theme and color map."""
    if not theme_blob:
        return {}
    try:
        theme_root = etree.fromstring(theme_blob, safe_xml_parser())
    except etree.XMLSyntaxError:
        return {}
    if theme_root.getroottree().docinfo.doctype or theme_root.tag != f"{{{A}}}theme":
        return {}
    theme_elements = theme_root.find(f"{{{A}}}themeElements")
    color_scheme = theme_elements.find(f"{{{A}}}clrScheme") if theme_elements is not None else None
    if color_scheme is None:
        return {}
    slots: dict[str, str] = {}
    for slot_node in color_scheme:
        if not isinstance(slot_node.tag, str):
            continue
        qname = etree.QName(slot_node)
        if qname.namespace != A or qname.localname not in _SCHEME_SLOTS or len(slot_node) != 1:
            continue
        color_node = slot_node[0]
        if len(color_node):
            continue
        rgb = ""
        if color_node.tag == f"{{{A}}}srgbClr":
            rgb = color_node.get("val", "")
        elif color_node.tag == f"{{{A}}}sysClr":
            rgb = color_node.get("lastClr", "")
        if _HEX_COLOR.fullmatch(rgb):
            slots[qname.localname] = rgb.upper()

    color_map = dict(_DEFAULT_COLOR_MAP)
    if settings_blob is not None:
        try:
            settings_root = etree.fromstring(settings_blob, safe_xml_parser())
        except etree.XMLSyntaxError:
            return {}
        if settings_root.getroottree().docinfo.doctype or settings_root.tag != f"{{{W}}}settings":
            return {}
        map_nodes = settings_root.findall(f"{{{W}}}clrSchemeMapping")
        if len(map_nodes) > 1:
            return {}
        if len(map_nodes) == 1:
            for attr_name, target in map_nodes[0].attrib.items():
                key = _COLOR_MAP_KEYS.get(etree.QName(attr_name).localname)
                if not key:
                    continue
                slot = _COLOR_MAP_TARGETS.get(target)
                if slot in slots:
                    color_map[key] = slot
                else:
                    color_map.pop(key, None)

    return {key: slots[target] for key, target in color_map.items() if target in slots}


def resolve_package_theme_colors(relations, read_blob) -> dict[str, str]:
    """Read theme and settings parts once when a package declares unique internal targets."""
    def unique_blob(relation_type: str) -> tuple[bool, bytes | None]:
        matched = [item for item in relations if item[0] == relation_type]
        if not matched:
            return True, None
        if len(matched) != 1 or matched[0][1]:
            return False, None
        blob = read_blob(matched[0][2])
        return (blob is not None), blob

    ok_theme, theme_blob = unique_blob(THEME_REL)
    ok_settings, settings_blob = unique_blob(SETTINGS_REL)
    if not ok_theme or not ok_settings:
        return {}
    return read_theme_colors(theme_blob, settings_blob)


def read_part_theme_colors(part) -> dict[str, str]:
    """Read theme colors once from an open python-docx document part."""
    return resolve_package_theme_colors(
        [(rel.reltype, rel.is_external, rel) for rel in part.rels.values()],
        lambda rel: rel.target_part.blob,
    )


def diagram_token(block_id):
    return hashlib.sha256(block_id.encode()).hexdigest()[:32]


def read_package_part(archive, target, content_type):
    if not re.fullmatch(r"/?word/diagrams/[A-Za-z0-9_-]+\.xml", target):
        raise ValueError("SmartArt 部件路径错误")
    name = target.lstrip("/")
    relationship_path = f"word/diagrams/_rels/{posixpath.basename(name)}.rels"
    if relationship_path in archive.namelist():
        raise ValueError("SmartArt 部件包含额外关系")
    types = etree.fromstring(archive.read("[Content_Types].xml"), safe_xml_parser())
    matches = [node for node in types if node.get("PartName") == f"/{name}"]
    if len(matches) != 1 or matches[0].get("ContentType") != content_type:
        raise ValueError("SmartArt 部件类型错误")
    if archive.getinfo(name).file_size > MAX_PART_BYTES:
        raise ValueError("SmartArt 部件过大")
    return archive.read(name)


def _part(key, data, *, theme_colors: dict[str, str] | None = None):
    if not data or len(data) > MAX_PART_BYTES:
        raise ValueError("SmartArt 部件为空或过大")
    root = etree.fromstring(data, safe_xml_parser())
    if root.tag != PARTS[key][0] or root.getroottree().docinfo.doctype:
        raise ValueError("SmartArt 部件结构无法核验")
    for item in root.iter():
        if not isinstance(item.tag, str):
            raise ValueError("SmartArt 包含无法核验的节点")
        name = etree.QName(item)
        if name.namespace not in {A, DGM, DSP} or name.localname in {"hlinkClick", "hlinkMouseOver", "blip"}:
            raise ValueError("SmartArt 含不支持的图形内容")
        if any(etree.QName(attribute).namespace == R and value for attribute, value in item.attrib.items()):
            raise ValueError("SmartArt 部件包含额外资源引用")
        if "relId" in item.attrib and (key != "dm" or item.tag != f"{{{DSP}}}dataModelExt"):
            raise ValueError("SmartArt 部件包含额外关系")
        if item.tag == f"{{{A}}}schemeClr":
            scheme_name = item.get("val", "")
            if scheme_name == "phClr" and key == "qs":
                continue
            if set(item.attrib) != {"val"} or not theme_colors or scheme_name not in theme_colors:
                raise ValueError("SmartArt 主题色无法独立保留")
            item.tag = f"{{{A}}}srgbClr"
            item.attrib.clear()
            item.set("val", theme_colors[scheme_name])
    return root


def read_inline_smartart(drawing, resolve, *, theme_colors: dict[str, str] | None = None):
    """Read a diagram and normalize the document-local drawing relationship."""
    inline = drawing.find(f"{{{WP}}}inline")
    if inline is None or len(drawing) != 1:
        raise ValueError("SmartArt 必须内嵌在正文中")
    frames = inline.findall(f"{{{A}}}graphic")
    allowed = {f"{{{WP}}}{name}" for name in ("extent", "effectExtent", "docPr", "cNvGraphicFramePr")}
    if len(frames) != 1 or len(frames[0]) != 1 or any(
        child.tag not in allowed and child is not frames[0] for child in inline
    ):
        raise ValueError("SmartArt 包含无法保留的混合图形")
    graphic = frames[0].find(GRAPHIC_DATA)
    rel_ids = graphic.find(f"{{{DGM}}}relIds") if graphic is not None else None
    if (graphic is None or graphic.get("uri") != DGM or rel_ids is None or len(graphic) != 1
            or set(rel_ids.attrib) != {f"{{{R}}}{key}" for key in ("dm", "lo", "qs", "cs")}):
        raise ValueError("SmartArt 关系不完整")
    parts = {}
    ids = {}
    for key, (_, relation, content_type) in PARTS.items():
        if key == "dr":
            extensions = parts["dm"].findall(f".//{{{DSP}}}dataModelExt")
            if len(extensions) != 1:
                raise ValueError("SmartArt 缓存绘图关系不唯一")
            identifier = extensions[0].get("relId")
        else:
            identifier = rel_ids.get(f"{{{R}}}{key}")
        if not identifier or identifier in ids.values():
            raise ValueError("SmartArt 关系缺失或重复")
        ids[key] = identifier
        parts[key] = _part(
            key,
            resolve(identifier, relation, f"application/vnd.{content_type}+xml"),
            theme_colors=theme_colors,
        )
    extensions[0].set("relId", "drawing")
    serialized = {}
    for key, root in parts.items():
        text = etree.tostring(root, encoding="unicode")
        if len(text.encode("utf-8")) > MAX_PART_BYTES:
            raise ValueError("SmartArt 部件过大")
        serialized[key] = text
    return serialized, ids


def label_size(parts):
    """Return the largest explicit label size, in hundredths of a point."""
    if set(parts) != set(PARTS):
        raise ValueError("SmartArt 部件不完整")
    sizes = []
    for key, raw in parts.items():
        root = _part(key, raw.encode())
        if key not in {"dm", "dr"}:
            continue
        labels = [text for text in root.iter(f"{{{A}}}t") if (text.text or "").strip()]
        if not labels:
            raise ValueError("SmartArt 缺少可核验标注")
        for text in labels:
            properties = text.getparent().find(f"{{{A}}}rPr")
            value = properties.get("sz", "") if properties is not None else ""
            if not value.isascii() or not value.isdecimal() or not 100 <= int(value) <= 400000:
                raise ValueError("SmartArt 标注缺少明确字号")
            sizes.append(int(value))
    return max(sizes)


def append_inline_smartart(parts, package, rels, types, token, width_cm, height_cm):
    """Emit internally related diagram parts in the frozen master's package."""
    label_size(parts)
    ids = {}
    for key, (_, relation, content_type) in PARTS.items():
        name = f"diagrams/pg-{token}-{key}.xml"
        identifier = f"rIdPGDiagram{token}{key}"
        if f"word/{name}" in package or any(rel.get("Id") == identifier for rel in rels):
            raise ValueError("SmartArt 与母版部件冲突")
        ids[key] = identifier
        etree.SubElement(rels, f"{{{REL}}}Relationship", Id=identifier, Type=relation, Target=name)
        etree.SubElement(types, f"{{{CT}}}Override", PartName=f"/word/{name}", ContentType=f"application/vnd.{content_type}+xml")
    for key, raw in parts.items():
        root = _part(key, raw.encode())
        if key == "dm":
            extension = root.find(f".//{{{DSP}}}dataModelExt")
            if extension is None or len(root.findall(f".//{{{DSP}}}dataModelExt")) != 1:
                raise ValueError("SmartArt 缓存绘图关系不唯一")
            extension.set("relId", ids["dr"])
        package[f"word/diagrams/pg-{token}-{key}.xml"] = etree.tostring(root)
    drawing = etree.Element(f"{{{W}}}drawing")
    inline = etree.SubElement(drawing, f"{{{WP}}}inline", distT="0", distB="0", distL="0", distR="0")
    etree.SubElement(inline, f"{{{WP}}}extent", cx=str(round(width_cm * 360000)), cy=str(round(height_cm * 360000)))
    etree.SubElement(inline, f"{{{WP}}}docPr", id="1", name="SmartArt")
    graphic = etree.SubElement(etree.SubElement(inline, f"{{{A}}}graphic"), GRAPHIC_DATA, uri=DGM)
    etree.SubElement(graphic, f"{{{DGM}}}relIds", {f"{{{R}}}{key}": ids[key] for key in ("dm", "lo", "qs", "cs")})
    return drawing
