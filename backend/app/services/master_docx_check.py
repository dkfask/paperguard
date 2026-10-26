"""Inspect emitted master-filled DOCX independently of its content renderer."""
from __future__ import annotations

import copy
import hashlib
import io
import posixpath
import zipfile

from lxml import etree

from app.core.extract.effective_format import EffectiveFormatResolver
from app.core.extract.xml_utils import NS, load_part, qn
from app.models.project import BlockType
from app.services.document_assets import resolve_document_image_path
from app.services.source_template_profile import verify_profile, digest, commentless_source, package_manifest

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"


def semantic(node):
    """Compare expanded names, attributes, text and order, independent of prefixes."""
    return (node.tag, tuple(sorted(node.attrib.items())), node.text or "", tuple(semantic(c) for c in node))


def paragraph_text(node):
    pieces = []
    for e in node.iter():
        if e.tag == qn("w:t"):
            pieces.append(e.text or "")
        elif e.tag == qn("w:tab"):
            pieces.append("\t")
        elif e.tag in {qn("w:br"), qn("w:cr")}:
            pieces.append("\n")
    return "".join(pieces)


def _valid_toc_page_reference(paragraph, bookmark, page):
    fields=paragraph.findall(".//"+qn("w:fldSimple"))
    return bool(bookmark and len(fields)==1
        and fields[0].get(qn("w:instr"),"").split()==["PAGEREF",bookmark,"\\h"]
        and fields[0].get(qn("w:dirty")) in {"0","false","off"}
        and fields[0].get(qn("w:fldLock"),"false") in {"0","false","off"}
        and not fields[0].findall(".//"+qn("w:tab"))
        and paragraph_text(fields[0]).strip()==page)


def inspect_master_output(data, project, template, source, report):
    def check(rule, good, obj, expected=None, actual=None):
        report["checks"].append({"rule": "master."+rule, "status": "pass" if good else "fail", "object":obj,
                                 "expected":expected,"actual":actual,"evidence":"independent inspection of emitted OOXML and frozen source/profile"})
    try:
        profile = verify_profile(template, source)
        source = commentless_source(source)
        check("prepared_source",digest(package_manifest(source))==profile["prepared_source_manifest_sha256"],"comment-free source")
        with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(io.BytesIO(data)) as output:
            if len(output.infolist()) > 10000 or sum(n.file_size for n in output.infolist()) > 250_000_000 or len({n.filename for n in output.infolist()}) != len(output.infolist()):
                raise ValueError("invalid or excessive package entries")
            source_root, root = load_part(original,"word/document.xml"), load_part(output,"word/document.xml")
            if any(n.getroottree().docinfo.doctype for n in (source_root,root)):
                raise ValueError("DTD is forbidden")
            resolver = EffectiveFormatResolver(load_part(output,"word/styles.xml"),load_part(output,"word/theme/theme1.xml"),load_part(output,"word/settings.xml"))
            source_names, output_names = set(original.namelist()),set(output.namelist())
            mutable = {"word/document.xml","word/_rels/document.xml.rels","[Content_Types].xml","word/settings.xml"}
            check("protected_parts",all(n in output_names and original.read(n)==output.read(n) for n in source_names-mutable),"source package")
            from app.services.office_layout import RECEIPT_PART, verify_layout_receipt, heading_name
            toc_entries = profile.get("toc_entries", [])
            toc_active = profile.get("toc_active", False)
            added_parts = output_names-source_names
            check("new_parts", all(n.startswith("word/media/pg-") or n==RECEIPT_PART for n in added_parts) and (RECEIPT_PART in output_names if toc_active else RECEIPT_PART not in output_names), "package parts")
            layout_receipt = None
            if toc_active:
                try:
                    layout_receipt = verify_layout_receipt(data, project, profile)
                    check("layout_receipt", True, "directory pagination", expected="signed receipt bound to current output, project, template and worker")
                except Exception as exc:
                    check("layout_receipt", False, "directory pagination", expected="valid signed measured receipt", actual=type(exc).__name__)
            else:
                check("layout_receipt", RECEIPT_PART not in output_names, "directory pagination", expected="no receipt for templates without mapped TOC")
            source_rels = load_part(original,"word/_rels/document.xml.rels")
            out_rels = load_part(output,"word/_rels/document.xml.rels")
            relations = {n.get("Id"):n for n in out_rels}
            check("relationship_ids",len(relations)==len(out_rels),"relationships")
            check("source_relationships",all(n.get("Id") in relations and semantic(n)==semantic(relations[n.get("Id")]) for n in source_rels),"source relationships")
            original_ids = {n.get("Id") for n in source_rels}
            check("new_relationships",all(n.get("Type", "").endswith("/image") and n.get("TargetMode") is None and posixpath.normpath(posixpath.join("word", n.get("Target", ""))).startswith("word/media/pg-") for key,n in relations.items() if key not in original_ids),"new relationships")
            for name in ("[Content_Types].xml","word/settings.xml"):
                before,after = load_part(original,name),load_part(output,name)
                if before is None:
                    check("protected_configuration",after is None,name); continue
                if name.endswith("settings.xml"):
                    for parent in (before,after):
                        for node in parent.findall(qn("w:updateFields")):
                            parent.remove(node)
                    check("protected_configuration",semantic(before)==semantic(after),name)
                else:
                    prior = [semantic(c) for c in before]
                    remaining = list(after)
                    good = True
                    for item in prior:
                        match = next((c for c in remaining if semantic(c)==item),None)
                        if match is None:
                            good=False; break
                        remaining.remove(match)
                    allowed = lambda c: c.tag.endswith("}Default") and (c.get("ContentType", "").startswith("image/") or (toc_active and c.get("Extension")=="json" and c.get("ContentType")=="application/json"))
                    check("protected_configuration",good and all(allowed(c) for c in remaining),name)
            # Structural oracle: source XML plus ONLY the reviewed mutation boundaries.
            # This does not generate expected student paragraphs or their formatting.
            actual_static = copy.deepcopy(root)
            for doc in (actual_static,):
                for content in doc.xpath(".//w:sdt[starts-with(w:sdtPr/w:tag/@w:val,'pg-region:') or starts-with(w:sdtPr/w:tag/@w:val,'pg-field:')]/w:sdtContent",namespaces=NS):
                    for item in list(content):
                        content.remove(item)
                if doc is actual_static:
                    toc_styles=set(profile.get("toc_style_ids", []))
                    body=doc.find(qn("w:body"))
                    for paragraph in list(doc.findall(".//"+qn("w:p"))):
                        style=paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
                        if style is not None and style.get(qn("w:val")) in toc_styles and paragraph.getparent() is body:
                            body.remove(paragraph)
            def structural(node):
                return [node.tag,sorted(node.attrib.items()),node.text or "",[structural(c) for c in node]]
            check("fixed_body_and_sections",digest(structural(actual_static))==profile["protected_document_sha256"],"cover, fixed text, section breaks, heading order")
            if profile.get("manuscript_headings_sha256"):
                check("manuscript_structure", digest([(s.type, s.title) for s in project.sections
                    if s.type in {"heading1", "heading2", "heading3"}]) == profile["manuscript_headings_sha256"],
                    "source manuscript chapter hierarchy and order")
            controls = root.xpath(".//w:sdt[w:sdtPr/w:tag]",namespaces=NS)
            tags = [n.find(f"{qn('w:sdtPr')}/{qn('w:tag')}").get(qn("w:val")) for n in controls]
            check("unique_bindings",len(tags)==len(set(tags)),"content bindings")
            appearance="{http://schemas.microsoft.com/office/word/2012/wordml}"
            for control,tag in zip(controls,tags):
                if not tag.startswith("pg-"):continue
                settings=control.findall(qn("w:sdtPr")+'/'+appearance+'appearance')
                check("content_control_appearance",len(settings)==1 and settings[0].get(appearance+'val')=='hidden',tag)

            if toc_active:
                updates=load_part(output,"word/settings.xml").findall(qn("w:updateFields"))
                check("toc_preserve_measured_cache",len(updates)==1 and updates[0].get(qn("w:val")) in {"0","false","off"},
                      "directory page references",expected="preserve measured fields until document pagination")
                receipt_rows=(layout_receipt or {}).get("toc",[])
                receipt_by_key={row.get("key"):row for row in receipt_rows if isinstance(row,dict)}
                actual_rows=[]
                bookmarks={n.get(qn("w:name")):n.get(qn("w:id")) for n in root.findall(".//"+qn("w:bookmarkStart"))}
                style_levels={str(style):int(level) for level,style in profile.get("toc_style_by_level",{}).items()}
                style_levels.update({str(style):int(level) for level,style in profile.get("toc_style_by_level",{}).items() if str(level).isdigit()})
                for paragraph in root.findall(".//"+qn("w:p")):
                    if paragraph.getparent() is not root.find(qn("w:body")):
                        continue
                    style=paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
                    if style is None or style.get(qn("w:val")) not in set(profile.get("toc_style_ids", [])):
                        continue
                    runs=[run for run in paragraph.findall(".//"+qn("w:r")) if paragraph_text(run).strip()]
                    good=len(runs)>=2
                    page=paragraph_text(runs[-1]).strip() if runs else ""
                    label="".join(paragraph_text(run) for run in runs[:-1]).rstrip(" .\t\u2026·") if runs else ""
                    matches=[heading for heading in profile["headings"] if heading.get("kind")!="toc" and heading_name(label)==heading_name(heading["title"])]
                    good &= len(matches)==1
                    row=receipt_by_key.get(matches[0]["key"]) if len(matches)==1 else None
                    anchors=paragraph.xpath("./w:hyperlink/@w:anchor",namespaces=NS)
                    good &= bool(row) and page==str(row.get("page","")) and label==row.get("label")
                    good &= style.get(qn("w:val"))==row.get("style_id") and style_levels.get(style.get(qn("w:val")))==row.get("level") if row else False
                    good &= anchors==[row.get("bookmark")] and row.get("bookmark")==matches[0].get("bookmark_name") if row and len(matches)==1 else False
                    good &= bool(row and row.get("bookmark") in bookmarks)
                    fields=paragraph.findall(".//"+qn("w:fldSimple"))
                    instructions=[field.get(qn("w:instr"),"").split() for field in fields]
                    reference_good=_valid_toc_page_reference(paragraph,(row or {}).get("bookmark"),page)
                    check("toc_page_reference",reference_good,label or "unmapped TOC row",
                          expected=(row or {}).get("bookmark"),actual=instructions)
                    good &= paragraph.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None
                    good &= all(run.xpath('./w:rPr/w:vanish/@w:val',namespaces=NS)==['0']
                                and run.xpath('./w:rPr/w:webHidden/@w:val',namespaces=NS)==['0']
                                for run in runs)
                    actual_rows.append(matches[0]["key"] if len(matches)==1 else "")
                    check("toc_row",bool(good),label or "unmapped TOC row",expected=(row or {}).get("page"),actual=page)
                expected_order=[row.get("key") for row in receipt_rows]
                check("toc_rows_complete",actual_rows==expected_order and len(actual_rows)==len(receipt_rows),"refreshed directory order",expected=expected_order,actual=actual_rows)

            def get(tag):
                matches = root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent",namespaces=NS,tag=tag)
                return matches[0] if len(matches)==1 else None

            def style(p, expected, obj, inline=None):
                properties,_,runs,_ = resolver.paragraph(p)
                good = True
                for key,value in expected.items():
                    if key.startswith("paragraph."):
                        good &= properties.get(key)==value
                    elif key.startswith("font."):
                        for index,run in enumerate(runs):
                            wanted = value
                            if key in {"font.bold","font.italic"} and inline and index < len(inline):
                                wanted = value or getattr(inline[index],key.split(".")[1],False)
                            good &= run["properties"].get(key)==wanted
                check("effective_format",bool(good),obj)

            for field in profile["fields"]:
                content = get("pg-field:"+field["key"])
                check("field_value",content is not None and paragraph_text(content)==project.meta.get(field["key"],""),field["key"])
                if content is not None:
                    # Resolve the enclosing source paragraph, including field runs inside SDT.
                    p = next((n for n in content.iterancestors() if n.tag==qn("w:p")),None)
                    if p is not None:
                        style(p,{k:v for k,v in field["properties"].items() if k.startswith("font.")},field["key"])
            expected_regions = [h["key"] for h in profile["headings"] if h["kind"]!="toc"]
            check("project_sections",[s.key for s in project.sections]==expected_regions,"project section order")
            sections = {s.key:s for s in project.sections}
            chapter=figure=table_number=0
            for heading in profile["headings"]:
                if heading["kind"]=="toc":
                    continue
                if heading["kind"]=="heading1":
                    chapter+=1; figure=table_number=0
                region=get("pg-region:"+heading["key"])
                blocks=sections.get(heading["key"])
                if region is None or blocks is None:
                    check("region",False,heading["key"]); continue
                block_tags = [n.find(f"{qn('w:sdtPr')}/{qn('w:tag')}").get(qn("w:val")) if n.tag==qn("w:sdt") and n.find(f"{qn('w:sdtPr')}/{qn('w:tag')}") is not None else "" for n in region]
                check("block_order",block_tags==["pg-block:"+b.id for b in blocks.paragraphs],heading["key"])
                maximum_width, maximum_height=heading["page_box_cm"]
                for block in blocks.paragraphs:
                    content=get("pg-block:"+block.id)
                    if content is None:
                        check("block",False,block.id); continue
                    nodes=list(content)
                    props=heading["properties"]
                    if block.type==BlockType.PARAGRAPH:
                        text="".join(run.text for run in block.inline_runs) if block.inline_runs else block.text
                        if heading.get("inline"):
                            actual_p=next((n for n in content.iterancestors() if n.tag==qn("w:p")),None)
                            check("inline_slot",len(blocks.paragraphs)==1 and not block.inline_runs and '\n' not in text and bool(nodes) and all(n.tag==qn("w:r") for n in nodes) and paragraph_text(content)==text,block.id)
                            if actual_p is not None:
                                virtual=etree.Element(qn("w:p"))
                                ppr=actual_p.find(qn("w:pPr"))
                                if ppr is not None:virtual.append(copy.deepcopy(ppr))
                                virtual.extend(copy.deepcopy(n) for n in nodes)
                                style(virtual,props,block.id,[block])
                            continue
                        check("text",len(nodes)==1 and nodes[0].tag==qn("w:p") and paragraph_text(nodes[0])==text,block.id)
                        if len(nodes)==1 and nodes[0].tag==qn("w:p"):
                            flags=block.inline_runs or [block]
                            style(nodes[0],props,block.id,flags)
                            check("text_only",not nodes[0].xpath(".//w:drawing | .//w:object | .//w:instrText | .//w:fldSimple | .//w:fldChar",namespaces=NS),block.id)
                    elif block.type==BlockType.IMAGE and block.image:
                        path=resolve_document_image_path(project.id,block.image.url)
                        embeds=content.findall(".//{"+A+"}blip")
                        actual=[]
                        for embed in embeds:
                            rel=relations.get(embed.get(qn("r:embed")))
                            if rel is not None:
                                target=posixpath.normpath(posixpath.join("word",rel.get("Target", "")))
                                actual.append(hashlib.sha256(output.read(target)).hexdigest() if target in output_names else "")
                        check("image_asset",path is not None and actual==[hashlib.sha256(path.read_bytes()).hexdigest()],block.id)
                        extents=content.findall(".//{"+WP+"}extent")
                        if path is not None and len(extents)==1:
                            from docx.image.image import Image
                            image=Image.from_file(str(path)); ratio=image.width/image.height
                            w,h=[int(extents[0].get(k,"0"))/360000 for k in ("cx","cy")]
                            wanted_w=block.image.width_cm or (block.image.height_cm*ratio if block.image.height_cm else min(maximum_width,image.width/360000))
                            wanted_h=block.image.height_cm or wanted_w/ratio
                            scale=min(1,maximum_width/wanted_w,maximum_height/wanted_h)
                            check("image_geometry",w>0 and h>0 and abs(w-wanted_w*scale)<.01 and abs(h-wanted_h*scale)<.01,block.id)
                        else:
                            check("image_geometry",False,block.id)
                        if block.image.caption:
                            figure+=1
                            import re
                            caption=re.sub(r"^图\s*\d+(?:[.\-]\d+)*\s*","",block.image.caption)
                            check("figure_caption",len(nodes)==2 and paragraph_text(nodes[-1])==f"图 {chapter}.{figure} {caption}",block.id)
                            if len(nodes)==2:
                                style(nodes[-1],{**profile["object_properties"]["figure_caption"],"paragraph.align":"center","paragraph.indent.first_line":{"value":0,"unit":"character"}},block.id+":caption")
                        else:
                            check("image_structure",len(nodes)==1,block.id)
                    elif block.type==BlockType.TABLE and block.table:
                        tables=[n for n in nodes if n.tag==qn("w:tbl")]
                        actual=[[paragraph_text(c) for c in r.findall(qn("w:tc"))] for r in tables[0].findall(qn("w:tr"))] if len(tables)==1 else []
                        check("table_cells",not block.table.source_xml and actual==block.table.cells,block.id)
                        if len(tables)==1:
                            table=tables[0]; grid=table.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")
                            width=sum(int(c.get(qn("w:w"),"0")) for c in grid)/567
                            check("table_geometry",len(grid)==block.table.cols and abs(width-maximum_width)<.03 and all(abs(int(c.get(qn("w:w"),"0"))/567-maximum_width/block.table.cols)<.01 for c in grid),block.id)
                            for p in table.findall(".//"+qn("w:p")):
                                style(p,{**profile["object_properties"]["table_text"],"paragraph.indent.first_line":{"value":0,"unit":"character"}},block.id)
                            check("table_header",bool(table.findall(f"{qn('w:tr')}/{qn('w:trPr')}/{qn('w:tblHeader')}")),block.id)
                        if block.table.caption:
                            table_number+=1
                            import re
                            caption=re.sub(r"^表\s*\d+(?:[.\-]\d+)*\s*","",block.table.caption)
                            check("table_caption",len(nodes)==2 and paragraph_text(nodes[0])==f"表 {chapter}.{table_number} {caption}" and nodes[-1].tag==qn("w:tbl"),block.id)
                            if len(nodes)==2:
                                style(nodes[0],{**profile["object_properties"]["table_caption"],"paragraph.align":"center","paragraph.indent.first_line":{"value":0,"unit":"character"}},block.id+":caption")
                        else:
                            check("table_structure",len(nodes)==1 and len(tables)==1,block.id)
                    else:
                        check("unsupported_dynamic_block",False,block.id)
            report["source_profile_sha256"]=profile["profile_sha256"]
            report["office_review_required"]=True
            report["manual_checks"]=["在目标版本 Word/WPS 打开文件，核对实测分页、目录跳转、字体、图表及跨页表格；后台排版回执不替代目标 Office 版本验收。"]
    except Exception as exc:
        check("inspectable",False,"document",actual=type(exc).__name__)
    report["check_count"]=len(report["checks"])
    report["passed_count"]=sum(c["status"]=="pass" for c in report["checks"])
    report["status"]="pass" if report["check_count"] and report["check_count"]==report["passed_count"] else "fail"
    return report
