"""在原始 OOXML 包上做保留式 DOCX 修复。"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from app.core.ir import Finding

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W}
_WP = f"{{{_W}}}"
_SUPPORTED_PREFIXES = (
    "paragraph.",
    "run.",
    "runs.*.",
)


def _child(parent, local_name: str):
    node = parent.find(f"{{{_W}}}{local_name}")
    if node is None:
        node = etree.SubElement(parent, f"{{{_W}}}{local_name}")
    return node


def _remove(parent, local_name: str) -> None:
    node = parent.find(f"{{{_W}}}{local_name}")
    if node is not None:
        parent.remove(node)


def _set_attr(element, local_name: str, value) -> None:
    element.set(f"{{{_W}}}{local_name}", str(value))


def _paragraphs(root):
    return list(root.iter(f"{{{_W}}}p"))


def _runs(paragraph):
    return list(paragraph.iter(f"{{{_W}}}r"))


def _twips(value: object, factor: float = 20.0) -> str:
    return str(round(float(value) * factor))


def _set_paragraph(paragraph, attr: str, value) -> bool:
    ppr = _child(paragraph, "pPr")
    if attr == "line_spacing":
        spacing = _child(ppr, "spacing")
        _set_attr(spacing, "line", round(float(value) * 240))
        _set_attr(spacing, "lineRule", "auto")
    elif attr == "first_line_indent_cm":
        ind = _child(ppr, "ind")
        _set_attr(ind, "firstLine", _twips(value, 1440 / 2.54))
        ind.attrib.pop(f"{{{_W}}}hanging", None)
    elif attr == "hanging_indent_cm":
        ind = _child(ppr, "ind")
        _set_attr(ind, "hanging", _twips(value, 1440 / 2.54))
        ind.attrib.pop(f"{{{_W}}}firstLine", None)
    elif attr == "alignment":
        alignment = "both" if value == "justify" else value
        _set_attr(_child(ppr, "jc"), "val", alignment)
    elif attr in ("space_before_pt", "space_after_pt"):
        spacing = _child(ppr, "spacing")
        _set_attr(spacing, "before" if attr.endswith("before_pt") else "after", _twips(value))
    elif attr == "is_page_break_before":
        if bool(value):
            _child(ppr, "pageBreakBefore")
        else:
            _remove(ppr, "pageBreakBefore")
    else:
        return False
    return True


def _set_run(run, attr: str, value) -> bool:
    rpr = _child(run, "rPr")
    if attr in ("font_eastasia", "font_ascii", "font_hansi", "font_cs"):
        fonts = _child(rpr, "rFonts")
        font_attr = {"font_eastasia": "eastAsia", "font_ascii": "ascii", "font_hansi": "hAnsi", "font_cs": "cs"}[attr]
        _set_attr(fonts, font_attr, value)
    elif attr == "font_size_pt":
        size = _child(rpr, "sz")
        _set_attr(size, "val", round(float(value) * 2))
        size_cs = _child(rpr, "szCs")
        _set_attr(size_cs, "val", round(float(value) * 2))
    elif attr in ("bold", "italic"):
        if bool(value):
            _child(rpr, "b" if attr == "bold" else "i")
        else:
            _remove(rpr, "b" if attr == "bold" else "i")
    elif attr == "underline":
        if value is None or str(value).lower() in ("none", "false", "0"):
            _remove(rpr, "u")
        else:
            _set_attr(_child(rpr, "u"), "val", value)
    elif attr == "color_hex":
        _set_attr(_child(rpr, "color"), "val", str(value).lstrip("#").upper())
    elif attr == "highlight":
        if value is None:
            _remove(rpr, "highlight")
        else:
            _set_attr(_child(rpr, "highlight"), "val", value)
    elif attr == "vert_align":
        if value is None:
            _remove(rpr, "vertAlign")
        else:
            _set_attr(_child(rpr, "vertAlign"), "val", value)
    else:
        return False
    return True


def _apply_finding(root, finding: Finding) -> bool:
    if finding.fix is None or finding.fix.action != "set":
        return False
    if finding.location.section not in (None, "paragraphs"):
        return False
    index = finding.location.paragraph_index
    if index is None:
        return False
    paragraphs = _paragraphs(root)
    if index < 0 or index >= len(paragraphs):
        return False
    target = finding.fix.target
    if target.startswith("paragraph."):
        return _set_paragraph(paragraphs[index], target.removeprefix("paragraph."), finding.fix.value)
    runs = _runs(paragraphs[index])
    if target.startswith("runs.*."):
        attr = target.removeprefix("runs.*.")
        return bool(runs) and all(_set_run(run, attr, finding.fix.value) for run in runs)
    if target.startswith("run."):
        attr = target.removeprefix("run.")
        return bool(runs) and _set_run(runs[0], attr, finding.fix.value)
    return False


def patch_docx_package(source: str | Path, output: str | Path, findings: list[Finding]) -> list[Finding]:
    """复制原始 ZIP 包，仅修改 word/document.xml，返回无法安全应用的 finding。"""
    source = Path(source)
    output = Path(output)
    with zipfile.ZipFile(source, "r") as zin:
        document_xml = zin.read("word/document.xml")
        parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)
        root = etree.fromstring(document_xml, parser)
        remaining = [finding for finding in findings if not _apply_finding(root, finding)]
        changed_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        with zipfile.ZipFile(output, "w") as zout:
            for info in zin.infolist():
                data = changed_xml if info.filename == "word/document.xml" else zin.read(info.filename)
                zout.writestr(info, data)
    return remaining


__all__ = ["patch_docx_package"]
