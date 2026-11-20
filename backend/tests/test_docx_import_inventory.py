"""Independent source inventory and manuscript import reconciliation."""

from copy import deepcopy
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml import parse_xml
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from PIL import Image

from app.services.docx_import_inventory import inventory_docx_source, reconcile_docx_import
from app.services.docx_project_import import import_docx_project


def _source(*, unsupported=False):
    document = Document()
    document.add_heading("第一章 研究", level=1)
    document.add_paragraph("正文内容")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "第一格"
    table.cell(0, 1).text = "第二格"
    picture = BytesIO()
    Image.new("RGB", (8, 8), color="red").save(picture, format="PNG")
    run = document.add_paragraph().add_run()
    run.add_picture(BytesIO(picture.getvalue()))
    run.add_picture(BytesIO(picture.getvalue()))
    if unsupported:
        paragraph = document.add_paragraph("带关系的文字")
        bookmark = OxmlElement("w:bookmarkStart")
        bookmark.set(qn("w:id"), "1")
        bookmark.set(qn("w:name"), "sample")
        paragraph._p.insert(0, bookmark)
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), "SEQ Figure")
        paragraph._p.append(field)
        relationship_id = paragraph.part.relate_to(
            "https://example.org/", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            is_external=True,
        )
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), relationship_id)
        paragraph._p.append(hyperlink)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _find(imported, kind):
    return next(block for section in imported["sections"] for block in section["paragraphs"]
                if block.get("type", "paragraph") == kind)


def _mixed_source():
    document = Document()
    document.add_heading("第一章 研究", level=1)
    paragraph = document.add_paragraph("图片前")
    picture = BytesIO()
    Image.new("RGB", (8, 8), color="red").save(picture, format="PNG")
    paragraph.add_run().add_picture(BytesIO(picture.getvalue()))
    paragraph.add_run("图片之间")
    paragraph.add_run().add_picture(BytesIO(picture.getvalue()))
    paragraph.add_run("图片后")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_independent_inventory_counts_distinct_image_occurrences_and_cells():
    data = _source()
    inventory = inventory_docx_source(data)
    entries = inventory["entries"]
    pictures = [item for item in entries if item["kind"] == "image"]
    assert len(pictures) == 2
    assert len({item["location"] for item in pictures}) == 2
    assert len({item["media_sha256"] for item in pictures}) == 1
    assert sum(item["kind"] == "table_cell" for item in entries) == 2

    imported = import_docx_project(data)
    assert sum(block.get("type") == "image" for section in imported["sections"]
               for block in section["paragraphs"]) == 2
    report = reconcile_docx_import(inventory, imported)
    assert report["status"] == "complete", report["unresolved"]


def test_import_preserves_text_with_explicit_no_highlight():
    document = Document()
    document.add_heading("第一章 研究", level=1)
    paragraph = document.add_paragraph()
    no_highlight = paragraph.add_run("无高亮")
    highlight = OxmlElement("w:highlight")
    highlight.set(qn("w:val"), "none")
    no_highlight._r.get_or_add_rPr().append(highlight)
    yellow = paragraph.add_run("有高亮")
    highlight = OxmlElement("w:highlight")
    highlight.set(qn("w:val"), "yellow")
    yellow._r.get_or_add_rPr().append(highlight)
    output = BytesIO()
    document.save(output)

    data = output.getvalue()
    imported = import_docx_project(data)
    paragraph_block = _find(imported, "paragraph")
    assert paragraph_block["text"] == "无高亮有高亮"
    assert [run["highlight"] for run in paragraph_block["inline_runs"]] == [None, "yellow"]
    assert reconcile_docx_import(inventory_docx_source(data), imported)["status"] == "complete"


def _smartart_source(
    size=1050,
    *,
    accent=None,
    dk2=None,
    clr_map_tx1=None,
    scheme_ref=None,
    minor_latin=None,
    major_ea=None,
    east_asia_lang=None,
    theme_font_ref=None,
    drop_theme=False,
):
    dgm = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
    dsp = "http://schemas.microsoft.com/office/drawing/2008/diagram"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    document = Document()
    document.add_heading("第一章 研究", level=1)
    label = f'<a:r xmlns:a="{a}"><a:rPr sz="{size}"/><a:t>图形文字</a:t></a:r>'
    data_label = (
        f'<a:r xmlns:a="{a}"><a:rPr sz="{size}">'
        f'<a:latin typeface="+mn-lt"/><a:ea typeface="+mj-ea"/></a:rPr><a:t>图形文字</a:t></a:r>'
        if theme_font_ref else label
    )
    drawing_children = (
        f'<dsp:sp xmlns:dsp="{dsp}" xmlns:a="{a}">'
        f'<dsp:style><a:fontRef idx="{theme_font_ref}"/></dsp:style>'
        f'<dsp:txBody><a:bodyPr/><a:lstStyle/><a:p>{label}</a:p></dsp:txBody></dsp:sp>'
        if theme_font_ref else label
    )
    style_children = ""
    colors_children = ""
    if scheme_ref:
        drawing_children += f'<a:solidFill xmlns:a="{a}"><a:schemeClr val="{scheme_ref}"/></a:solidFill>'
        colors_children = f'<d:fillClrLst><a:schemeClr xmlns:a="{a}" val="accent1"><a:tint val="50000"/></a:schemeClr></d:fillClrLst>'
        style_children = f'<a:fontRef xmlns:a="{a}" idx="minor"><a:schemeClr val="phClr"/></a:fontRef>'
    refs = {}
    for key, name, root, relation, ctype, children in (
        ("dr", "drawing", "drawing", "diagramDrawing", "ms-office.drawingml.diagramDrawing", drawing_children),
        ("dm", "data", "dataModel", "diagramData", "openxmlformats-officedocument.drawingml.diagramData", data_label),
        ("lo", "layout", "layoutDef", "diagramLayout", "openxmlformats-officedocument.drawingml.diagramLayout", ""),
        ("qs", "style", "styleDef", "diagramQuickStyle", "openxmlformats-officedocument.drawingml.diagramStyle", style_children),
        ("cs", "colors", "colorsDef", "diagramColors", "openxmlformats-officedocument.drawingml.diagramColors", colors_children),
    ):
        ns = dsp if key == "dr" else dgm
        if key == "dm":
            children += f'<dsp:dataModelExt xmlns:dsp="{dsp}" relId="{refs["dr"]}"/>'
        part = Part(PackURI(f"/word/diagrams/{name}1.xml"), f"application/vnd.{ctype}+xml",
                    f'<d:{root} xmlns:d="{ns}">{children}</d:{root}>'.encode(), document.part.package)
        prefix = "http://schemas.microsoft.com/office/2007/relationships" if key == "dr" else r
        refs[key] = document.part.relate_to(part, f"{prefix}/{relation}")
    document.add_paragraph().add_run()._r.append(parse_xml(f'''
        <w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
            xmlns:a="{a}" xmlns:r="{r}" xmlns:dgm="{dgm}">
          <wp:inline><wp:extent cx="3600000" cy="1800000"/><wp:docPr id="1" name="Diagram 1"/>
            <a:graphic><a:graphicData uri="{dgm}">
              <dgm:relIds r:dm="{refs['dm']}" r:lo="{refs['lo']}" r:qs="{refs['qs']}" r:cs="{refs['cs']}"/>
            </a:graphicData></a:graphic>
          </wp:inline>
        </w:drawing>'''))
    document.add_paragraph("图1.1 系统结构")
    output = BytesIO()
    document.save(output)
    if not any((accent, dk2, clr_map_tx1, minor_latin, major_ea, east_asia_lang, drop_theme)):
        return output.getvalue()
    from lxml import etree

    patched = BytesIO()
    with ZipFile(output) as source_zip, ZipFile(patched, "w", ZIP_DEFLATED) as target_zip:
        for info in source_zip.infolist():
            blob = source_zip.read(info.filename)
            if info.filename == "word/theme/theme1.xml":
                if drop_theme:
                    continue
                theme_root = etree.fromstring(blob)
                if accent:
                    slot = theme_root.find(f".//{{{a}}}clrScheme/{{{a}}}accent1/{{{a}}}srgbClr")
                    assert slot is not None
                    slot.set("val", accent)
                if dk2:
                    slot = theme_root.find(f".//{{{a}}}clrScheme/{{{a}}}dk2/{{{a}}}srgbClr")
                    assert slot is not None
                    slot.set("val", dk2)
                if minor_latin:
                    node = theme_root.find(f".//{{{a}}}fontScheme/{{{a}}}minorFont/{{{a}}}latin")
                    assert node is not None
                    node.set("typeface", minor_latin)
                if major_ea:
                    node = theme_root.find(f".//{{{a}}}fontScheme/{{{a}}}majorFont/{{{a}}}ea")
                    assert node is not None
                    node.set("typeface", major_ea)
                blob = etree.tostring(theme_root)
            elif info.filename == "word/settings.xml" and (clr_map_tx1 or east_asia_lang):
                settings_root = etree.fromstring(blob)
                w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                if clr_map_tx1:
                    clr_map = settings_root.find(f"{{{w_ns}}}clrSchemeMapping")
                    if clr_map is None:
                        clr_map = etree.SubElement(settings_root, f"{{{w_ns}}}clrSchemeMapping")
                    clr_map.set(f"{{{w_ns}}}t1", clr_map_tx1)
                if east_asia_lang:
                    lang_node = settings_root.find(f"{{{w_ns}}}themeFontLang")
                    if lang_node is None:
                        lang_node = etree.SubElement(settings_root, f"{{{w_ns}}}themeFontLang")
                    lang_node.set(f"{{{w_ns}}}eastAsia", east_asia_lang)
                blob = etree.tostring(settings_root)
            elif info.filename == "word/_rels/document.xml.rels" and drop_theme:
                rel_root = etree.fromstring(blob)
                for rel in list(rel_root):
                    if rel.get("Type", "").endswith("/theme"):
                        rel_root.remove(rel)
                blob = etree.tostring(rel_root)
            target_zip.writestr(info, blob)
    return patched.getvalue()


def test_import_accounts_for_inline_smartart_and_its_caption():
    data = _smartart_source()
    imported = import_docx_project(data)
    assert _find(imported, "image")["image"]["caption"] == "图1.1 系统结构"
    report = reconcile_docx_import(inventory_docx_source(data), imported)
    assert report["status"] == "complete", report["unresolved"]


def test_import_resolves_smartart_theme_colors_against_source_theme():
    from lxml import etree

    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    data = _smartart_source(accent="E06666", dk2="123456", clr_map_tx1="dark2", scheme_ref="tx1")
    imported = import_docx_project(data)
    smartart = _find(imported, "image")["image"]["smartart"]
    dr_colors = [el.get("val") for el in etree.fromstring(smartart["dr"].encode()).iter(f"{{{a}}}srgbClr")]
    cs_srgb = list(etree.fromstring(smartart["cs"].encode()).iter(f"{{{a}}}srgbClr"))
    assert dr_colors == ["123456"]
    assert len(cs_srgb) == 1 and cs_srgb[0].get("val") == "E06666"
    assert cs_srgb[0][0].tag == f"{{{a}}}tint" and cs_srgb[0][0].get("val") == "50000"
    assert [el.get("val") for el in etree.fromstring(smartart["qs"].encode()).iter(f"{{{a}}}schemeClr")] == ["phClr"]
    assert [el.get("idx") for el in etree.fromstring(smartart["qs"].encode()).iter(f"{{{a}}}fontRef")] == ["none"]
    assert "schemeClr" not in smartart["dr"]
    assert "schemeClr" not in smartart["cs"]
    report = reconcile_docx_import(inventory_docx_source(data), imported)
    assert report["status"] == "complete", report["unresolved"]


def test_import_resolves_smartart_theme_fonts_against_source_theme():
    from lxml import etree

    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    data = _smartart_source(
        minor_latin="Georgia", major_ea="黑体", east_asia_lang="zh-CN", theme_font_ref="minor",
    )
    imported = import_docx_project(data)
    smartart = _find(imported, "image")["image"]["smartart"]
    dm_root = etree.fromstring(smartart["dm"].encode())
    dr_root = etree.fromstring(smartart["dr"].encode())
    assert [el.get("typeface") for el in dm_root.iter(f"{{{a}}}latin")] == ["Georgia"]
    assert [el.get("typeface") for el in dm_root.iter(f"{{{a}}}ea")] == ["黑体"]
    assert [el.get("typeface") for el in dr_root.iter(f"{{{a}}}latin")] == ["Georgia"]
    assert [el.get("typeface") for el in dr_root.iter(f"{{{a}}}ea")] == ["宋体"]
    assert [el.get("idx") for el in dr_root.iter(f"{{{a}}}fontRef")] == ["none"]
    report = reconcile_docx_import(inventory_docx_source(data), imported)
    assert report["status"] == "complete", report["unresolved"]


@pytest.mark.parametrize("params", [
    {"scheme_ref": "accent1", "drop_theme": True},
    {"theme_font_ref": "minor", "drop_theme": True},
])
def test_smartart_with_unresolvable_theme_dependency_is_rejected(params):
    data = _smartart_source(**params)
    imported = import_docx_project(data)
    assert "含有无法完整导入的 SmartArt" in imported["warnings"]
    assert not any(b.get("type") == "image" for s in imported["sections"] for b in s["paragraphs"])
    report = reconcile_docx_import(inventory_docx_source(data), imported)
    assert report["status"] == "incomplete"
    assert any("SmartArt" in item.get("reason", "") for item in report["unresolved"])


@pytest.mark.parametrize("missing_kind", ["paragraph", "table_cell", "image"])
def test_reconciliation_finds_source_content_removed_from_candidate(missing_kind):
    data = _source()
    inventory = inventory_docx_source(data)
    imported = deepcopy(import_docx_project(data))
    if missing_kind == "paragraph":
        section = imported["sections"][0]
        section["paragraphs"] = [block for block in section["paragraphs"] if block.get("text") != "正文内容"]
    elif missing_kind == "table_cell":
        _find(imported, "table")["table"]["cells"][0][1] = ""
    else:
        section = next(section for section in imported["sections"]
                       if any(block.get("type") == "image" for block in section["paragraphs"]))
        image_index = next(index for index, block in enumerate(section["paragraphs"]) if block.get("type") == "image")
        section["paragraphs"].pop(image_index)

    report = reconcile_docx_import(inventory, imported)
    assert report["status"] == "incomplete"
    assert any(item["kind"] == missing_kind and item["location"] for item in report["unresolved"])


def test_unhandled_field_bookmark_and_relationship_are_not_silently_accepted():
    data = _source(unsupported=True)
    inventory = inventory_docx_source(data)
    report = reconcile_docx_import(inventory, import_docx_project(data))
    assert {"field", "bookmark", "relationship"} <= {item["kind"] for item in report["unresolved"]}
    assert report["status"] == "incomplete"


def test_reconciliation_checks_text_and_image_order_within_one_paragraph():
    data = _mixed_source()
    inventory = inventory_docx_source(data)
    imported = import_docx_project(data)
    assert reconcile_docx_import(inventory, imported)["status"] == "complete"
    blocks = imported["sections"][0]["paragraphs"]
    blocks[0], blocks[1] = blocks[1], blocks[0]
    report = reconcile_docx_import(inventory, imported)
    assert report["status"] == "incomplete"
    assert any(item["kind"] == "order" and "/p[" in item["location"] for item in report["unresolved"])


@pytest.mark.parametrize("mutation", ["unknown_location", "extra_cell", "extra_row", "duplicate_title"])
def test_reconciliation_rejects_candidate_content_without_source_occurrence(mutation):
    data = _source()
    inventory = inventory_docx_source(data)
    imported = import_docx_project(data)
    if mutation == "unknown_location":
        imported["sections"][0]["paragraphs"].append({
            "type": "paragraph", "text": "不属于源文件", "_source_location": "word/document.xml#body/p[999]",
        })
    elif mutation == "extra_cell":
        _find(imported, "table")["table"]["cells"][0].append("多出的单元格")
    elif mutation == "extra_row":
        _find(imported, "table")["table"]["cells"].append(["多出的行"])
    else:
        section = imported["sections"][0]
        section["paragraphs"].insert(0, {
            "type": "paragraph", "text": section["title"], "_source_location": section["_source_location"],
        })
    report = reconcile_docx_import(inventory, imported)
    assert report["status"] == "incomplete"
    assert report["unresolved"]


def test_each_shared_image_relationship_is_bound_to_its_own_occurrence():
    data = _mixed_source()
    inventory = inventory_docx_source(data)
    imported = import_docx_project(data)
    section = imported["sections"][0]
    images = [block for block in section["paragraphs"] if block.get("type") == "image"]
    section["paragraphs"].remove(images[1])
    report = reconcile_docx_import(inventory, imported)
    assert report["status"] == "incomplete"
    assert any(item["kind"] == "image" for item in report["unresolved"])
    assert any(item["kind"] == "relationship" for item in report["unresolved"])


def test_unknown_table_cell_content_cannot_pass_silently():
    document = Document()
    document.add_heading("第一章 研究", level=1)
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "已有文字"
    unknown = OxmlElement("w:customUnknown")
    table.cell(0, 0)._tc.append(unknown)
    output = BytesIO()
    document.save(output)
    data = output.getvalue()
    report = reconcile_docx_import(inventory_docx_source(data), import_docx_project(data))
    assert report["status"] == "incomplete"
    assert any(item["kind"] == "structure" and "/tc[1]" in item["location"] for item in report["unresolved"])
