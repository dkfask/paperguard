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


def _part(key, data):
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
    return root


def read_inline_smartart(drawing, resolve):
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
        parts[key] = _part(key, resolve(identifier, relation, f"application/vnd.{content_type}+xml"))
    extensions[0].set("relId", "drawing")
    return {key: etree.tostring(root, encoding="unicode") for key, root in parts.items()}, ids


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
