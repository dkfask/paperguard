"""Measured TOC refresh with an isolated Office engine and an artifact-bound receipt."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import copy
import shutil
import signal
import platform
import stat
import subprocess
import tempfile
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path

from lxml import etree
from pypdf import PdfReader

from app.core.extract.xml_utils import NS, load_part, qn, safe_xml_parser
from app.services.digit_font_rules import HEADING_KINDS, numbering_has_digits
from app.services.export_time_budget import (
    check_export_budget, ensure_export_budget, remaining_export_seconds,
)
from app.services.master_layout_anchors import required_layout_bookmarks, required_table_row_targets
from app.services.project_versions import project_content_hash
from app.services.secret_store import _require_master_key
from app.services.source_template_profile import ProfileError, digest, package_manifest

WORKER = Path(__file__).with_name("office_worker") / "PaperGuardLayout.java"
DEPENDENCY = WORKER.with_name("dependency.json")
RECEIPT_PART = "customXml/paperguard-layout.json"
LEGACY_VERSION = "office-layout/10"
LEGACY_NATIVE_TOC_VERSION = "office-layout/11"
VERSION = "office-layout/12"
NATIVE_TOC_VERSION = "office-layout/13"

# Pagination costs vary with document size and Word response times. The outer
# export deadline and worker lease remain the authoritative hard limits.
_NATIVE_LAYOUT_STAGE_SECONDS = 540
_NATIVE_PAGINATION_SECONDS = 180


def heading_name(text: str) -> str:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC",text)).lower()
    text = re.sub(r"^第[0-9一二三四五六七八九十百]+[章节]", "",text)
    return re.sub(r"^[0-9]+(?:\.[0-9]+)*[.、)]?", "",text)


def heading_matches(label: str, title: str) -> bool:
    """Match cached TOC labels to source headings with explanatory suffixes."""
    def number(text):
        value = unicodedata.normalize("NFKC",text).strip()
        match = re.match(r"^(?:第([0-9一二三四五六七八九十百]+)章|([0-9]+(?:\.[0-9]+)*))", value)
        return (match.group(1) or match.group(2)) if match else ""
    left, right = number(label), number(title)
    if left and right and left != right:
        return False
    a, b = heading_name(label), heading_name(title)
    return a == b or bool(a and b and b.startswith(a))


def _environment():
    # Office never inherits application credentials or the server signing key.
    env = {k:v for k,v in os.environ.items() if k in {"PATH","HOME","LANG","LC_ALL","TMPDIR"}}
    fontconfig = os.environ.get("PAPERGUARD_OFFICE_FONTCONFIG", "")
    if fontconfig:
        if not Path(fontconfig).is_file():
            raise ProfileError("服务器排版字体配置不存在")
        env["FONTCONFIG_FILE"] = fontconfig
    return env


def _office_roundtrip(input_path: Path, directory: Path):
    converter = os.environ.get("PAPERGUARD_SOFFICE_PATH") or shutil.which("soffice") or shutil.which("libreoffice")
    java = os.environ.get("PAPERGUARD_JAVA_PATH") or shutil.which("java")
    jar = Path(os.environ.get("PAPERGUARD_OFFICE_JAR","/app/office/libreoffice.jar"))
    dependency = json.loads(DEPENDENCY.read_text())
    if not converter or not java or not jar.is_file():
        raise ProfileError("后台目录排版组件未就绪，请配置 LibreOffice、Java 17 及已校验的排版依赖")
    if hashlib.sha256(jar.read_bytes()).hexdigest() != dependency["sha256"]:
        raise ProfileError("后台排版依赖摘要不符，已拒绝执行")
    directory.mkdir(parents=True,exist_ok=True)
    profile = directory/"profile";profile.mkdir(mode=0o700)
    name = "pg_"+uuid.uuid4().hex
    environment = _environment()
    check_export_budget()
    with (directory/"office.log").open("wb") as log:
        process = subprocess.Popen([converter,"--headless","--nologo","--norestore","--nodefault",
            f"-env:UserInstallation={profile.resolve().as_uri()}",f"--accept=pipe,name={name};urp;StarOffice.ComponentContext"],
            stdout=log,stderr=log,env=environment,start_new_session=True,umask=0o077)
        pipe = None
        try:
            deadline = time.monotonic()+20
            while time.monotonic()<deadline:
                check_export_budget()
                found = [p for base in (Path("/tmp"),Path("/var/tmp")) for p in base.glob("OSL_PIPE_*"+name)]
                if found:
                    pipe=found[0]
                    info=pipe.stat()
                    if info.st_uid!=os.getuid() or not stat.S_ISSOCK(info.st_mode):
                        raise ProfileError("排版任务的私有连接无效")
                    pipe.chmod(0o600)
                    break
                if process.poll() is not None:
                    break
                time.sleep(min(.1, remaining_export_seconds()))
            if pipe is None:
                raise ProfileError("排版组件启动失败，目录结果未获验证")
            result=subprocess.run([java,"-Xmx256m","-cp",str(jar),str(WORKER),str(pipe),str(input_path),str(directory)],
                capture_output=True,text=True,timeout=remaining_export_seconds(120),env=environment)
            check_export_budget()
            if result.returncode!=0 or result.stdout.strip()!="OK" or not all((directory/name).is_file() for name in ("before.pdf","after.pdf","updated.docx")):
                raise ProfileError("排版组件未能完成目录刷新，未提供未经验证的文件")
        except (OSError,subprocess.TimeoutExpired) as exc:
            check_export_budget()
            raise ProfileError("目录排版失败或超时，请重试或由管理员检查排版组件") from exc
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait()
            if pipe is not None and pipe.exists() and pipe.stat().st_uid==os.getuid():
                pipe.unlink()


def _pdf_state(path: Path):
    reader=PdfReader(path)
    if not 0<len(reader.pages)<=500:
        raise ProfileError("排版结果为空或超过单次 500 页预算")
    destinations=[]
    def walk(items):
        for item in items:
            if isinstance(item,list):walk(item)
            else:destinations.append([heading_name(str(item.title)),reader.get_destination_page_number(item)+1])
    walk(reader.outline)
    fonts=set();texts=[]
    for page in reader.pages:
        def visitor(text,cm,tm,font,size):
            if text.strip() and font:
                fonts.add(str(font.get("/BaseFont","")))
        texts.append(page.extract_text(visitor_text=visitor) or "")
    return {"page_count":len(reader.pages),"destinations":destinations,"fonts":sorted(fonts),"text":"\n".join(texts)}


def _font_name(value):
    value=re.sub(r"^/?[A-Z]{6}\+","",value).lower()
    aliases={"宋体":"simsun","新宋体":"nsimsun","黑体":"simhei","楷体":"kaiti","仿宋":"fangsong"}
    value=aliases.get(value,value)
    value=re.sub(r"[^a-z0-9\u3400-\u9fff]","",value)
    value=re.sub(r"(?:ps)?(?:bolditalic|bold|italic|regular)?(?:mt)?$","",value)
    return value


def _required_fonts(project,profile,parts):
    values=set(profile.get("toc_font_requirements", []))
    if profile.get("body_page_number"):
        values.add(profile["body_page_number"]["font"])
    digit_rule = profile.get("digit_font_rule")
    if digit_rule:
        root = etree.fromstring(parts["word/document.xml"])
        styles = etree.fromstring(parts["word/styles.xml"]) if "word/styles.xml" in parts else None
        numbering = etree.fromstring(parts["word/numbering.xml"]) if "word/numbering.xml" in parts else None
        for heading in profile["headings"]:
            if heading.get("kind") not in HEADING_KINDS:
                continue
            marks = root.xpath(".//w:bookmarkStart[@w:name=$name]", namespaces=NS,
                               name=heading.get("bookmark_name", ""))
            paragraphs = marks[0].xpath("ancestor::w:p[1]", namespaces=NS) if len(marks) == 1 else []
            if len(paragraphs) != 1:
                raise ProfileError("标题编号缺少可核验的位置")
            try:
                if numbering_has_digits(paragraphs[0], numbering, styles):
                    values.add(digit_rule["font"])
            except ValueError as exc:
                raise ProfileError(str(exc)) from exc
    def add(text,properties):
        if re.search(r"[\u3400-\u9fff]",text) and isinstance(properties.get("font.eastAsia"),str):values.add(properties["font.eastAsia"])
        if re.search(r"[a-zA-Z0-9]",text) and isinstance(properties.get("font.ascii"),str):values.add(properties["font.ascii"])
    def add_object(text, role):
        styles = profile.get("object_properties")
        if not isinstance(styles, dict) or not isinstance(styles.get(role), dict):
            raise ProfileError("图表样式缺失，无法核验字体")
        add(text, styles[role])
    for heading in profile["headings"]:
        section=next((s for s in project.sections if s.key==heading["key"]),None)
        if section:
            for block in section.paragraphs:
                if block.type.value=="paragraph":
                    add(block.text,heading["properties"])
                elif block.type.value=="image" and block.image:
                    # Captions always include a Chinese label and numeric chapter/object number.
                    add_object("图 0.0 " + block.image.caption, "figure_caption")
                elif block.type.value=="table" and block.table:
                    add_object("表 0.0 " + block.table.caption, "table_caption")
                    for row in block.table.cells:
                        for text in row:
                            add_object(text, "table_text")
            if digit_rule and heading.get("kind") in {"heading1", "heading2", "heading3", "body"}:
                if re.search(r"[0-9]", heading.get("title", "")) or any(
                        block.type.value == "paragraph" and re.search(r"[0-9]", block.text)
                        for block in section.paragraphs):
                    values.add(digit_rule["font"])
    for field in profile["fields"]:add(project.meta.get(field["key"],""),field["properties"])
    return values


def _toc_level(name):
    match=re.search(r"(?:toc|contents|目录)\s*([1-9])",name,re.I)
    return int(match.group(1)) if match else None


def _text_content(paragraph):
    pieces=[]
    for node in paragraph.iter():
        if node.tag==qn("w:t"):
            pieces.append(node.text or "")
        elif node.tag==qn("w:tab"):
            pieces.append("\t")
        elif node.tag in {qn("w:br"),qn("w:cr")}:
            pieces.append("\n")
    return "".join(pieces)


def _install_bookmark_link(paragraph, bookmark):
    for hyperlink in list(paragraph.findall(qn("w:hyperlink"))):
        index=paragraph.index(hyperlink)
        for child in list(hyperlink):
            hyperlink.remove(child)
            paragraph.insert(index,child);index+=1
        paragraph.remove(hyperlink)
    runs=paragraph.findall(qn("w:r"))
    if not runs:raise ProfileError("刷新后的目录行没有可链接文字")
    hyperlink=etree.Element(qn("w:hyperlink"));hyperlink.set(qn("w:anchor"),bookmark)
    first=min(paragraph.index(run) for run in runs)
    for run in runs:
        paragraph.remove(run);hyperlink.append(run)
    paragraph.insert(first,hyperlink)


def _match_heading(label, headings, required):
    candidates=[heading for heading in headings if heading.get("kind")!="toc" and heading_matches(label,heading["title"])]
    exact=[heading for heading in candidates if heading_name(label)==heading_name(heading["title"])]
    mapped=[heading for heading in exact if heading["key"] in required]
    if len(mapped)==1:return mapped
    if len(exact)==1:return exact
    mapped=[heading for heading in candidates if heading["key"] in required]
    if len(mapped)==1:return mapped
    return exact or candidates


def _read_toc(path: Path, profile):
    with zipfile.ZipFile(path) as archive:
        root=load_part(archive,"word/document.xml")
        styles=load_part(archive,"word/styles.xml")
    body=root.find(qn("w:body"))
    _remove_toc_field_controls(body,profile.get("toc_style_ids",[]))
    names={n.get(qn("w:styleId")):n.find(qn("w:name")).get(qn("w:val"),n.get(qn("w:styleId"),"")) if n.find(qn("w:name")) is not None else n.get(qn("w:styleId"),"") for n in styles.findall(qn("w:style"))}
    headings=profile.get("headings",[])
    required={entry["target_key"]:entry for entry in profile.get("toc_entries",[])}
    heading_order={heading["key"]:index for index,heading in enumerate(headings)}
    rows=[]
    previous_by_parent={}
    ordinals_by_parent={}
    for paragraph in root.findall(".//"+qn("w:p")):
        if not any(parent is body for parent in paragraph.iterancestors()):
            continue
        if any(parent.xpath("./w:sdtPr/w:tag[@w:val='pg-toc:placeholder']",namespaces=NS) for parent in paragraph.iterancestors() if parent.tag==qn("w:sdt")):
            continue
        style=paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
        if style is None:continue
        style_id=style.get(qn("w:val"),"");level=_toc_level(names.get(style_id,style_id))
        if level is None:continue
        anchors=paragraph.xpath(".//w:hyperlink/@w:anchor",namespaces=NS)
        runs=[run for run in paragraph.findall(".//"+qn("w:r")) if _text_content(run).strip()]
        if len(runs)<2:raise ProfileError("刷新后的目录行缺少独立标题与页码文本")
        page=_text_content(runs[-1]).strip()
        if not re.fullmatch(r"[1-9][0-9]*|[ivxlcdmIVXLCDM]+",page):
            raise ProfileError("刷新后的目录页码无法识别")
        label="".join(_text_content(run) for run in runs[:-1]).rstrip(" .\t\u2026·")
        matches=_match_heading(label,headings,required)
        if not matches:
            if any(heading_name(label)==heading_name(stale) for stale in profile.get("removed_toc_entries",[])):
                continue
            raise ProfileError("刷新后的目录含有未确认的章节："+label[:80])
        parent_id=paragraph.getparent().getroottree().getpath(paragraph.getparent())
        previous=previous_by_parent.get(parent_id)
        if len(matches)>1 and previous and previous["key"] in {candidate["key"] for candidate in matches}:
            heading=next(candidate for candidate in matches if candidate["key"]==previous["key"])
        else:
            candidates=[candidate for candidate in matches if heading_order[candidate["key"]]>(heading_order[previous["key"]] if previous else -1)]
            if not candidates:candidates=matches
            heading=candidates[0]
        if not heading_matches(label,heading["title"]):raise ProfileError("目录行与跳转书签目标不一致："+label[:60]+" -> "+heading["title"][:60])
        inferred=re.fullmatch(r"heading([1-9])",heading.get("kind",""))
        expected_level=required.get(heading["key"],{}).get("level")
        if expected_level is None and inferred:
            expected_level=int(inferred.group(1))
        if expected_level is not None and level!=expected_level:raise ProfileError("目录层级与母版章节层级不一致："+label[:80])
        source_style=profile.get("toc_style_by_level",{}).get(str(level)) or profile.get("toc_style_by_level",{}).get(level)
        if not source_style or style_id!=source_style:
            raise ProfileError("刷新后的目录样式与母版样式不一致："+label[:80])
        bookmark=heading.get("bookmark_name","")
        if not bookmark:raise ProfileError("章节缺少稳定跳转书签")
        source_anchor=anchors[0] if len(anchors)==1 else ""
        _install_bookmark_link(paragraph,bookmark)
        # Imported index runs can inherit hidden source heading fragments.
        # A published directory must display its complete confirmed labels.
        for run in paragraph.findall(".//"+qn("w:r")):
            properties=run.find(qn("w:rPr"))
            if properties is None:
                properties=etree.Element(qn("w:rPr"));run.insert(0,properties)
            for tag in ("w:vanish","w:webHidden"):
                flag=properties.find(qn(tag))
                if flag is None:flag=etree.SubElement(properties,qn(tag))
                flag.set(qn("w:val"),"0")
        # The source boundary already remains in the master. Office can move
        # it into the last refreshed row and rewrite its relationship IDs.
        # Copying that boundary duplicates sections and breaks the package.
        for section in paragraph.findall(f"{qn('w:pPr')}/{qn('w:sectPr')}"):
            section.getparent().remove(section)
        rows.append({"key":heading["key"],"target_title":heading["title"],"label":label,"page":page,
                     "level":level,"style_id":style_id,"bookmark":bookmark,"source_anchor":source_anchor,
                     "parent_id":parent_id,"ordinal":ordinals_by_parent.get(parent_id,0),"paragraph":copy.deepcopy(paragraph)})
        ordinals_by_parent[parent_id]=ordinals_by_parent.get(parent_id,0)+1
        previous_by_parent[parent_id]=rows[-1]
    expected=set(required)
    candidates=[]
    for parent_id in {row["parent_id"] for row in rows}:
        source_rows=[row for row in rows if row["parent_id"]==parent_id]
        by_key={}
        for row in source_rows:by_key.setdefault(row["key"],[]).append(row)
        selected=[]
        for group in by_key.values():
            stable=[row for row in group if row["source_anchor"]==row["bookmark"]]
            selected.append(stable[-1] if stable else group[-1])
        selected.sort(key=lambda row:row["ordinal"])
        found={row["key"] for row in selected}
        candidates.append((expected.issubset(found),len(expected&found),len(found),len(source_rows),selected))
    rows=max(candidates,key=lambda item:item[:4])[-1] if candidates else []
    actual={row["key"] for row in rows}
    if not rows and profile.get("toc_active"):raise ProfileError("目录域刷新后没有生成目录项")
    if expected-actual:raise ProfileError("目录刷新后缺少已确认章节："+"、".join(required[key]["target_title"] for key in sorted(expected-actual))+"；当前映射："+"、".join(row["key"]+"="+row["label"] for row in rows[:8]))
    positions=[heading_order[row["key"]] for row in rows]
    if positions!=sorted(positions):raise ProfileError("刷新后的目录顺序与正文顺序不一致")
    return rows,root


# These are the Word-generated directory structures admitted for the first
# native adapter.  Other structures must be explicitly supported and tested;
# they must not be copied from an Office save into the final master silently.
_NATIVE_TOC_ELEMENTS = {
    qn("w:" + name) for name in (
        "p", "pPr", "pStyle", "tabs", "tab", "ind", "spacing", "jc",
        "r", "rPr", "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps",
        "smallCaps", "sz", "szCs", "noProof", "webHidden", "vanish",
        "color", "u", "vertAlign", "t", "hyperlink", "fldChar", "instrText",
    )
} | {qn("w14:ligatures")}
_NATIVE_TOC_ATTRIBUTES = {
    qn("w:" + name) for name in (
        "val", "anchor", "history", "fldCharType", "ascii", "asciiTheme",
        "hAnsi", "hAnsiTheme", "eastAsia", "eastAsiaTheme", "cs", "cstheme",
        "firstLine", "left", "right", "hanging", "before", "after", "line",
        "lineRule", "pos", "leader", "rsidR", "rsidRDefault", "rsidRPr",
    )
} | {qn("w14:paraId"), qn("w14:textId"), qn("w14:val"),
     "{http://www.w3.org/XML/1998/namespace}space"}


def _word_generated_toc_rows(parts, updated: bytes, profile, scratch: Path):
    """Extract validated TOC rows only, retaining the original package/styles."""
    from app.services.source_template_safety import audit_generated_package

    audit_generated_package(updated)
    original = etree.fromstring(parts["word/document.xml"], safe_xml_parser())
    original_body = original.find(qn("w:body"))
    placeholders = original.xpath(
        ".//w:sdt[w:sdtPr/w:tag/@w:val='pg-toc:placeholder']", namespaces=NS,
    )
    if original_body is None or len(placeholders) != 1 or placeholders[0].getparent() is not original_body:
        raise ProfileError("母版目录锚点缺失、重复或不在正文中")
    with zipfile.ZipFile(io.BytesIO(updated)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"), safe_xml_parser())
    body = root.find(qn("w:body"))
    controls = root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val='pg-toc:placeholder']", namespaces=NS)
    if body is None or len(controls) != 1 or controls[0].getparent() is not body:
        raise ProfileError("Word 目录锚点缺失、重复或不在正文中")
    containers = controls[0].findall(qn("w:sdtContent"))
    if len(containers) != 1:
        raise ProfileError("Word 目录内容区域无效")
    content = containers[0]
    paragraphs = list(content)
    if not paragraphs or len(paragraphs) > 257 or any(node.tag != qn("w:p") for node in paragraphs):
        raise ProfileError("Word 目录段落结构无效或超过上限")
    for node in content.iterdescendants():
        if node.tag not in _NATIVE_TOC_ELEMENTS or any(key not in _NATIVE_TOC_ATTRIBUTES for key in node.attrib):
            raise ProfileError("Word 目录含有尚未支持的结构")
        if node.tag == qn("w:instrText"):
            instruction = (node.text or "").strip()
            if instruction and not re.match(r"^(?:TOC|PAGEREF)(?:\s|$)", instruction, re.I):
                raise ProfileError("Word 目录包含不支持的域")
    # Use only returned directory paragraphs and the original styles.  No other
    # Word-modified content, relationships, headers or settings are imported.
    for child in list(original_body):
        original_body.remove(child)
    for paragraph in paragraphs:
        original_body.append(copy.deepcopy(paragraph))
    isolated_parts = {**parts, "word/document.xml": etree.tostring(
        original, xml_declaration=True, encoding="UTF-8", standalone=True,
    )}
    with zipfile.ZipFile(scratch, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in isolated_parts.items():
            archive.writestr(name, data)
    rows, _ = _read_toc(scratch, profile)
    visible = [p for p in paragraphs if p.xpath(".//w:t[normalize-space(.)]", namespaces=NS)]
    if len(rows) != len(visible) or len({row["key"] for row in rows}) != len(rows):
        raise ProfileError("Word 目录含有重复、未识别或未确认的目录项")
    return rows


def _source_toc_state(root,profile):
    # Return None until the initial stale/static rows have been replaced.
    try:
        body=root.find(qn("w:body"))
        style_levels={str(style):int(level) for level,style in profile.get("toc_style_by_level",{}).items()}
        style_levels.update({str(style):int(level) for level,style in profile.get("toc_style_by_level",{}).items() if str(level).isdigit()})
        rows=[]
        for paragraph in root.findall(".//"+qn("w:p")):
            if not any(parent is body for parent in paragraph.iterancestors()):continue
            if any(parent.xpath("./w:sdtPr/w:tag[@w:val='pg-toc:placeholder']",namespaces=NS) for parent in paragraph.iterancestors() if parent.tag==qn("w:sdt")):
                continue
            style=paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
            if style is None:continue
            style_id=style.get(qn("w:val"),"")
            if style_id not in profile.get("toc_style_ids",[]):continue
            text=_text_content(paragraph).strip()
            match=re.search(r"([1-9][0-9]*|[ivxlcdmIVXLCDM]+)$",text)
            if not match:return None
            label=text[:match.start()].rstrip(" .\t\u2026·")
            anchors=paragraph.xpath(".//w:hyperlink/@w:anchor",namespaces=NS)
            bookmark_map={heading.get("bookmark_name"):heading for heading in profile.get("headings",[]) if heading.get("bookmark_name")}
            if len(anchors)!=1 or anchors[0] not in bookmark_map:continue
            heading=bookmark_map[anchors[0]]
            if not heading_matches(label,heading["title"]):return None
            row={"key":heading["key"],"target_title":heading["title"],"label":label,"page":match.group(1),"level":style_levels.get(style_id),"style_id":style_id,"bookmark":anchors[0]}
            rows.append(row)
        return rows
    except Exception:
        return None


def _replace_toc_rows(parts, rows, profile, *, character_styles_root=None):
    root=etree.fromstring(parts["word/document.xml"])
    placeholder=root.xpath(".//w:sdt[w:sdtPr/w:tag/@w:val='pg-toc:placeholder']",namespaces=NS)
    if len(placeholder)>1:raise ProfileError("母版目录锚点重复")
    body=root.find(qn("w:body"))
    _remove_toc_field_controls(body,profile.get("toc_style_ids",[]))
    toc_styles=set(profile.get("toc_style_ids",[]))
    nodes=[]
    for paragraph in root.findall(".//"+qn("w:p")):
        if not any(parent is body for parent in paragraph.iterancestors()):continue
        if any(parent.xpath("./w:sdtPr/w:tag[@w:val='pg-toc:placeholder']",namespaces=NS) for parent in paragraph.iterancestors() if parent.tag==qn("w:sdt")):
            continue
        style=paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
        if style is not None and style.get(qn("w:val")) in toc_styles:
            nodes.append(paragraph)
    if placeholder:
        anchor=placeholder[0];parent=anchor.getparent()
        if parent is None:raise ProfileError("目录锚点已脱离正文")
        first=parent.index(anchor);parent.remove(anchor)
    elif nodes:
        parent=nodes[0].getparent()
        if any(node.getparent() is not parent for node in nodes):raise ProfileError("母版目录区域分散，无法安全替换")
        first=min(parent.index(node) for node in nodes)
    else:
        raise ProfileError("母版目录锚点或目录行已变化")
    for node in nodes:parent.remove(node)
    from app.services.toc_appearance import apply_toc_appearance
    styles = etree.fromstring(parts["word/styles.xml"])
    for offset,row in enumerate(rows):
        paragraph=copy.deepcopy(row["paragraph"])
        _install_page_reference(paragraph,row["bookmark"],row["page"])
        rule = profile.get("toc_appearance_by_level", {}).get(str(row["level"]))
        apply_toc_appearance(paragraph, rule, styles, character_styles_root=character_styles_root)
        parent.insert(first+offset,paragraph)
    parts["word/document.xml"]=etree.tostring(root,xml_declaration=True,encoding="UTF-8",standalone=True)
    _preserve_measured_fields(parts)


def _install_page_reference(paragraph, bookmark, page):
    """Keep the measured result while allowing Word/WPS to repaginate it."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}",bookmark):
        raise ProfileError("目录页码引用的章节书签无效")
    runs=[run for run in paragraph.findall(".//"+qn("w:r")) if _text_content(run).strip()]
    if len(runs)<2 or _text_content(runs[-1]).strip()!=str(page):
        raise ProfileError("目录页码引用缺少独立的实测页码")
    # Keep the source run properties, tab leaders and title hyperlink intact.
    # Only the last, numeric run becomes an updatable bookmark page reference.
    run=runs[-1];parent=run.getparent();index=parent.index(run)
    result=copy.deepcopy(run)
    # Office may put the right-aligned tab and page text in the same run.
    # Keep that tab OUTSIDE the field: recalculation replaces field contents.
    for node in list(result):
        if node.tag not in {qn("w:rPr"),qn("w:t")}:
            result.remove(node)
    for node in list(run.findall(qn("w:t"))):run.remove(node)
    field=etree.Element(qn("w:fldSimple"))
    field.set(qn("w:instr"),f" PAGEREF {bookmark} \\h ")
    field.set(qn("w:dirty"),"false")
    field.append(result)
    if any(node.tag!=qn("w:rPr") for node in run):
        parent.insert(index+1,field)
    else:
        parent.remove(run);parent.insert(index,field)


def _preserve_measured_fields(parts):
    if "word/settings.xml" not in parts:
        raise ProfileError("目录页码引用缺少文档设置")
    settings=etree.fromstring(parts["word/settings.xml"])
    updates=settings.findall(qn("w:updateFields"))
    if len(updates)>1:
        raise ProfileError("文档包含重复的目录域更新设置")
    update=updates[0] if updates else etree.SubElement(settings,qn("w:updateFields"))
    # Recalculation during opening can run before Word has paginated the
    # document and replace every PAGEREF result with 1. Preserve the measured
    # cache on delivery; the fields remain unlocked for later editing.
    update.set(qn("w:val"),"false")
    parts["word/settings.xml"]=etree.tostring(settings,xml_declaration=True,encoding="UTF-8",standalone=True)


def _remove_toc_field_controls(body,toc_style_ids=()):
    """Staticize complex/simple TOC fields without changing their cached row text."""
    fields=[];stack=[];unmatched=[]
    for paragraph in list(body.findall(".//"+qn("w:p"))):
        for node in paragraph.iter():
            if node.tag==qn("w:fldSimple"):
                field={"simple":node,"controls":[],"instructions":[],
                       "toc":bool((stack and stack[-1]["toc"]) or re.match(r"^\s*TOC(?:\s|$)",node.get(qn("w:instr"),""),re.I))}
                fields.append(field)
                continue
            if node.tag==qn("w:fldChar"):
                kind=node.get(qn("w:fldCharType"),"")
                if kind=="begin":
                    field={"controls":[node],"instructions":[],"toc":bool(stack and stack[-1]["toc"])}
                    fields.append(field);stack.append(field)
                elif kind in {"separate","end"} and stack:
                    stack[-1]["controls"].append(node)
                    if kind=="end":stack.pop()
                elif kind in {"separate","end"}:
                    unmatched.append(node)
            elif node.tag==qn("w:instrText") and stack:
                stack[-1]["instructions"].append(node)
                if re.match(r"^\s*TOC(?:\s|$)",node.text or "",re.I):
                    stack[-1]["toc"]=True
    touched=set()
    for field in fields:
        if not field["toc"]:continue
        simple=field.get("simple")
        if simple is not None and simple.getparent() is not None:
            parent=simple.getparent();index=parent.index(simple)
            touched.add(next((ancestor for ancestor in simple.iterancestors() if ancestor.tag==qn("w:p")),None))
            for child in list(simple):
                simple.remove(child);parent.insert(index,child);index+=1
            parent.remove(simple)
        for node in field.get("controls",[]):
            if node.getparent() is not None:
                touched.add(next((parent for parent in node.iterancestors() if parent.tag==qn("w:p")),None))
                node.getparent().remove(node)
        # Strip all nested field instructions in the TOC range, including
        # PAGEREF instructions, but keep their cached display text.
        for node in field.get("instructions",[]):
            if node.getparent() is not None:
                touched.add(next((ancestor for ancestor in node.iterancestors() if ancestor.tag==qn("w:p")),None))
                node.getparent().remove(node)
    # Some Word files leave an unmatched field-end paragraph immediately after
    # the TOC domain. Associate only that empty adjacent marker with the TOC;
    # unrelated fields elsewhere in the document remain untouched.
    direct_children=list(body)
    toc_positions=[]
    for index,node in enumerate(direct_children):
        if node.tag==qn("w:sdt") and node.xpath("./w:sdtPr/w:tag[@w:val='pg-toc:placeholder']",namespaces=NS):
            toc_positions.append(index)
        elif node.tag==qn("w:p"):
            style=node.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
            if style is not None and style.get(qn("w:val"),"") in set(toc_style_ids):
                toc_positions.append(index)
    for node in unmatched:
        paragraph=next((ancestor for ancestor in node.iterancestors() if ancestor.tag==qn("w:p")),None)
        if paragraph is None or paragraph.getparent() is not body:continue
        index=body.index(paragraph)
        if not any(abs(index-position)==1 for position in toc_positions):continue
        if paragraph.xpath(".//w:t[normalize-space(.)]",namespaces=NS):continue
        for control_node in paragraph.findall(".//"+qn("w:fldChar")):
            if control_node.getparent() is not None:control_node.getparent().remove(control_node)
        touched.add(paragraph)
    # Office sometimes leaves an empty paragraph containing only a dangling
    # end marker after rewriting the field. Remove empty field-only shells.
    for paragraph in [item for item in touched if item is not None]:
        for run in list(paragraph.findall(".//"+qn("w:r"))):
            if not any(child.tag!=qn("w:rPr") for child in run):
                run.getparent().remove(run)
        meaningful=[node for node in paragraph if node.tag!=qn("w:pPr")]
        carries_section=paragraph.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None
        if not meaningful and not carries_section and paragraph.getparent() is not None:
            paragraph.getparent().remove(paragraph)


def _toc_state(rows):
    return [{k:row[k] for k in ("key","target_title","label","page","level","style_id","bookmark")} for row in rows]


def _layout_destinations(destinations, rows):
    """Bind every generated TOC target to an observed PDF outline page.

    The cached page text in ``word/document.xml`` is only an input to the
    layout pass.  A PDF outline is independent evidence that the office
    engine produced a navigable target at a concrete physical page.  Keep
    duplicate headings order-sensitive so two equal titles cannot silently
    satisfy one another.
    """
    observed = []
    for item in destinations or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        label, page = item
        if not isinstance(label, str) or not isinstance(page, int) or page < 1:
            continue
        observed.append((label, page))
    if not observed:
        raise ProfileError("排版 PDF 缺少可核验的目录目标页定位")
    used = set()
    bound = []
    for row in rows:
        title = row.get("target_title", "")
        match = next((index for index, (label, page) in enumerate(observed)
                      if index not in used and heading_matches(label, title)), None)
        if match is None:
            raise ProfileError(f"排版 PDF 缺少章节目标页定位：{title}")
        used.add(match)
        label, page = observed[match]
        bound.append({"key": row["key"], "title": title, "label": label, "page": page})
    return bound


def _layout_input_parts(parts,profile):
    """Refresh page references on every pass, including already static rows."""
    root=etree.fromstring(parts["word/document.xml"])
    if root.xpath(".//w:sdtPr/w:tag[@w:val='pg-toc:placeholder']",namespaces=NS):
        return parts
    body=root.find(qn("w:body"));styles=set(profile.get("toc_style_ids",[]))
    rows=[p for p in body.findall(qn("w:p"))
          if p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}") is not None
          and p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}").get(qn("w:val")) in styles]
    if not rows:raise ProfileError("目录排版输入缺少已确认目录行")
    controls=[]
    for kind in ("begin","instruction","separate"):
        run=etree.Element(qn("w:r"))
        if kind=="instruction":
            instruction=etree.SubElement(run,qn("w:instrText"))
            instruction.set("{http://www.w3.org/XML/1998/namespace}space","preserve")
            instruction.text=' TOC \\o "1-3" \\h \\z \\u '
        else:
            etree.SubElement(run,qn("w:fldChar")).set(qn("w:fldCharType"),kind)
        controls.append(run)
    first=1 if rows[0].find(qn("w:pPr")) is not None else 0
    for offset,run in enumerate(controls):rows[0].insert(first+offset,run)
    end=etree.SubElement(rows[-1],qn("w:r"))
    etree.SubElement(end,qn("w:fldChar")).set(qn("w:fldCharType"),"end")
    return {**parts,"word/document.xml":etree.tostring(root,xml_declaration=True,encoding="UTF-8",standalone=True)}


def _mac(payload, version=VERSION):
    return hmac.new(_require_master_key(),(version+digest(payload)).encode(),hashlib.sha256).hexdigest()


def _measure_native_layout(path: Path, deadline: float, extra_bookmarks: list[str] | None = None) -> dict:
    """Measure an actual candidate with Word inside one bounded time budget."""
    from app.services.word_native_layout import WordNativeLayoutError, inspect_word_pagination
    remaining = int(remaining_export_seconds(min(_NATIVE_PAGINATION_SECONDS, max(0, deadline - time.monotonic()))))
    if remaining < 1:
        raise ProfileError("Word 目录分页超过总时限，请稍后重试")
    try:
        result = inspect_word_pagination(path, timeout=remaining, extra_bookmarks=extra_bookmarks)
        check_export_budget()
        return result
    except WordNativeLayoutError as exc:
        check_export_budget()
        raise ProfileError("Word 目录分页无法核验：" + str(exc)) from exc


def _cover_policy(profile):
    policy = profile.get("cover_pagination")
    if policy is None:
        return None
    headings = profile.get("headings") or []
    first = min(headings, key=lambda item: item.get("index", -1)) if headings else None
    offsets = policy.get("allowed_blank_offsets") if isinstance(policy, dict) else None
    source = policy.get("source") if isinstance(policy, dict) else None
    if (not isinstance(policy, dict) or not first or first.get("kind") == "toc" or first.get("inline")
            or not profile.get("toc_active") or policy.get("boundary_key") != first.get("key")
            or policy.get("boundary_bookmark") != first.get("bookmark_name")
            or type(policy.get("require_boundary_new_page")) is not bool
            or not isinstance(offsets, list) or len(offsets) > 64
            or any(type(item) is not int or not 1 <= item <= 63 for item in offsets)
            or offsets != sorted(set(offsets))
            or source != {"kind": "explicit_template_confirmation", "source_sha256": profile.get("source_sha256"),
                          "specification_sha256": profile.get("specification_sha256")}):
        raise ProfileError("封面分页规则与已确认模板不一致")
    return policy


def _measure_native_cover(path: Path, deadline: float, policy: dict | None) -> dict | None:
    if policy is None:
        return None
    from app.services.word_native_layout import WordNativeLayoutError, inspect_word_cover
    remaining = int(remaining_export_seconds(min(150, max(0, deadline - time.monotonic()))))
    if remaining < 1:
        raise ProfileError("Word 封面分页超过总时限，请稍后重试")
    try:
        result = inspect_word_cover(path, policy["boundary_bookmark"], timeout=remaining)
        check_export_budget()
        return result
    except WordNativeLayoutError as exc:
        check_export_budget()
        raise ProfileError("Word 封面分页无法核验：" + str(exc)) from exc


def _validated_cover_evidence(evidence, policy, word, source_sha256):
    if policy is None:
        if evidence is not None:
            raise ProfileError("Word 封面分页证据包含未请求的规则")
        return
    from app.services.word_native_layout import _WORD_COVER_SCRIPT
    if (not isinstance(evidence, dict) or evidence.get("scope") != "word_cover_main_story_pages_only"
            or evidence.get("source_sha256") != source_sha256
            or evidence.get("probe_sha256") != hashlib.sha256(_WORD_COVER_SCRIPT.encode()).hexdigest()
            or evidence.get("pagination_stable") is not True
            or type(evidence.get("plain_text_context")) is not bool
            or any(evidence.get(key) != word.get(key) for key in ("word_version", "macos_version", "machine", "page_count"))
            or evidence.get("boundary_bookmark") != policy["boundary_bookmark"]
            or type(evidence.get("boundary_page")) is not int
            or not 1 <= evidence["boundary_page"] <= min(64, evidence.get("page_count", 0))):
        raise ProfileError("Word 封面分页证据缺失、过期或环境不匹配")
    positions = [item for item in word.get("extra_bookmarks", [])
                 if isinstance(item, dict) and item.get("bookmark") == policy["boundary_bookmark"]]
    if len(positions) != 1 or positions[0].get("physical_page") != evidence["boundary_page"]:
        raise ProfileError("Word 封面分页与正文边界书签不一致")
    pages = evidence.get("pages")
    if not isinstance(pages, list) or len(pages) != evidence["boundary_page"]:
        raise ProfileError("Word 封面逐页证据不完整")
    for number, row in enumerate(pages, 1):
        if (not isinstance(row, dict) or set(row) != {"page", "text_present", "plain_text_candidate", "inline_shapes", "tables", "floating_shapes"}
                or type(row.get("page")) is not int or row["page"] != number
                or type(row.get("text_present")) is not bool
                or type(row.get("plain_text_candidate")) is not bool
                or row["plain_text_candidate"] and not row["text_present"]
                or any(type(row.get(key)) is not int or row[key] < 0
                       for key in ("inline_shapes", "tables", "floating_shapes"))):
            raise ProfileError("Word 封面逐页证据无效")


def cover_pagination_result(profile, receipt):
    """Evaluate configured blank-page policy only against previously verified Word evidence."""
    policy = _cover_policy(profile)
    if policy is None:
        return None
    evidence = (receipt or {}).get("word_cover")
    if not isinstance(evidence, dict):
        return {"ok": False, "reason": "缺少与成品绑定的 Word 封面分页证据"}
    pages = evidence["pages"]
    def occupancy(row):
        if row["plain_text_candidate"] and evidence["plain_text_context"]:
            return "content"
        if row["text_present"] or row["inline_shapes"] or row["tables"] or row["floating_shapes"]:
            return "unknown"
        return "blank"
    boundary_page = evidence["boundary_page"]
    cover_pages = pages[:-1]
    unexpected = [row["page"] for row in cover_pages if occupancy(row) == "blank"
                  and boundary_page - row["page"] not in policy["allowed_blank_offsets"]]
    uncertain = [row["page"] for row in cover_pages if occupancy(row) == "unknown"
                 and boundary_page - row["page"] not in policy["allowed_blank_offsets"]]
    reasons = []
    content_pages = cover_pages if policy["require_boundary_new_page"] else pages
    if not any(occupancy(row) == "content" for row in content_pages):
        reasons.append("未检测到封面内容")
    if unexpected:
        reasons.append("封面存在未声明的空白页")
    if uncertain:
        reasons.append("封面页内容是否可见无法验证")
    if policy["require_boundary_new_page"]:
        if occupancy(pages[-1]) == "content":
            reasons.append("正文首标题与封面内容位于同一页")
        elif occupancy(pages[-1]) == "unknown":
            reasons.append("正文首标题前是否存在可见内容无法验证")
    return {"ok": not reasons, "boundary_page": boundary_page, "unexpected_blank_pages": unexpected,
            "unverifiable_pages": uncertain, "allowed_blank_offsets": policy["allowed_blank_offsets"],
            "reason": "；".join(reasons)}


def _measure_cover_evidence(path, deadline, profile, word):
    policy = _cover_policy(profile)
    evidence = _measure_native_cover(path, deadline, policy)
    _validated_cover_evidence(evidence, policy, word, word["source_sha256"])
    return evidence


def _remeasure_final_cover(path, deadline, profile, word, previous):
    policy = _cover_policy(profile)
    if policy is None:
        return
    final = _measure_native_cover(path, deadline, policy)
    _validated_cover_evidence(final, policy, word, hashlib.sha256(path.read_bytes()).hexdigest())
    if any(final.get(key) != previous.get(key) for key in
           ("word_version", "macos_version", "machine", "probe_sha256", "page_count", "plain_text_context", "boundary_bookmark", "boundary_page", "pages")):
        raise ProfileError("最终 Word 文件与封面分页证据不一致")


def _measure_native_table_rows(path: Path, deadline: float, targets: list[dict]) -> dict | None:
    """Locate table rows under the same total export budget as pagination."""
    if not targets:
        return None
    from app.services.word_native_layout import WordNativeLayoutError, inspect_word_table_rows
    remaining = int(remaining_export_seconds(min(90, max(0, deadline - time.monotonic()))))
    if remaining < 1:
        raise ProfileError("Word 表格分页超过总时限，请稍后重试")
    try:
        result = inspect_word_table_rows(path, targets, timeout=remaining)
        check_export_budget()
        return result
    except WordNativeLayoutError as exc:
        check_export_budget()
        raise ProfileError("Word 表格分页无法核验：" + str(exc)) from exc


def _validated_table_row_evidence(evidence, targets, word, source_sha256):
    """Validate row locations without treating location as a visibility check."""
    from app.services.word_native_layout import _WORD_TABLE_ROWS_SCRIPT
    if not targets:
        if evidence is not None:
            raise ProfileError("Word 表格分页证据包含未请求的表格")
        return
    if (not isinstance(evidence, dict) or evidence.get("scope") != "word_table_row_locations_only"
            or evidence.get("source_sha256") != source_sha256
            or evidence.get("probe_sha256") != hashlib.sha256(_WORD_TABLE_ROWS_SCRIPT.encode()).hexdigest()
            or evidence.get("pagination_stable") is not True
            or evidence.get("word_version") != word.get("word_version")
            or evidence.get("macos_version") != word.get("macos_version")
            or evidence.get("machine") != word.get("machine")
            or type(evidence.get("page_count")) is not int
            or evidence.get("page_count") != word.get("page_count")):
        raise ProfileError("Word 表格分页证据缺失、过期或环境不匹配")
    tables = evidence.get("tables")
    if not isinstance(tables, list) or len(tables) != len(targets):
        raise ProfileError("Word 表格分页证据不完整")
    positions = {row.get("bookmark"): row.get("physical_page")
                 for row in word.get("extra_bookmarks", []) if isinstance(row, dict)}
    for table, target in zip(tables, targets):
        if not isinstance(table, dict) or table.get("bookmark") != target["bookmark"]:
            raise ProfileError("Word 表格分页证据表格顺序无效")
        rows = table.get("rows")
        if not isinstance(rows, list) or len(rows) != target["row_count"]:
            raise ProfileError("Word 表格分页证据行数不完整")
        previous_end = 0
        for number, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or type(row.get("row")) is not int or row["row"] != number
                    or type(row.get("start_page")) is not int or type(row.get("end_page")) is not int
                    or not 1 <= row["start_page"] <= row["end_page"] <= evidence["page_count"]
                    or row["start_page"] < previous_end):
                raise ProfileError("Word 表格分页证据行号或页码无效")
            previous_end = row["end_page"]
        start = positions.get(target["bookmark"])
        end = positions.get(target["end_bookmark"])
        if (type(start) is not int or type(end) is not int or rows[0]["start_page"] != start
                or not rows[-1]["start_page"] <= end <= rows[-1]["end_page"]):
            raise ProfileError("Word 表格分页证据与内容边界不一致")


def _measure_table_evidence(path, deadline, targets, word):
    evidence = _measure_native_table_rows(path, deadline, targets)
    _validated_table_row_evidence(evidence, targets, word, hashlib.sha256(path.read_bytes()).hexdigest())
    return evidence


def _remeasure_final_tables(path, deadline, targets, word, previous):
    final = _measure_table_evidence(path, deadline, targets, word)
    if previous is not None and any(final.get(key) != previous.get(key) for key in
                                    ("scope", "word_version", "macos_version", "machine", "probe_sha256",
                                     "page_count", "pagination_stable", "tables")):
        raise ProfileError("最终 Word 文件与表格分页证据不一致")


def _validated_layout_bookmarks(rows, expected_names: list[str], page_count: int,
                                toc: list[dict] | None = None) -> list[dict]:
    """Reject incomplete, reordered, or out-of-range native page measurements."""
    if (not isinstance(rows, list) or len(rows) != len(expected_names)
            or type(page_count) is not int or page_count < 1):
        raise ProfileError("Word 内容布局分页证据不完整")
    toc_by_name = {row.get("bookmark"): row for row in (toc or []) if isinstance(row, dict)}
    for row, name in zip(rows, expected_names):
        if (not isinstance(row, dict) or row.get("bookmark") != name
                or type(row.get("physical_page")) is not int
                or not 1 <= row["physical_page"] <= page_count
                or type(row.get("logical_page")) is not int or row["logical_page"] < 1):
            raise ProfileError("Word 内容布局分页证据无效")
        reference = toc_by_name.get(name)
        if reference and (reference.get("physical_page") != row["physical_page"]
                          or reference.get("logical_page") != row["logical_page"]):
            raise ProfileError("Word 目录与内容书签分页证据不一致")
    return rows


def _native_word_toc(parts, profile, rows, directory: Path, write, deadline: float,
                     extra_bookmarks: list[str]):
    """Bind TOC caches to Word's stable logical pages, then remeasure them."""
    from app.services.word_native_layout import WordNativeLayoutError, update_word_directory_cache
    target_names = [row["bookmark"] for row in rows]
    path = directory / "word-layout-candidate.docx"
    for _ in range(4):
        write(path)
        # A cache that needs correction cannot provide final content evidence.
        # Measure only TOC references until it agrees, then measure every
        # required content anchor against the same exact candidate bytes.
        measurement = _measure_native_layout(path, deadline)
        for with_content in (False, True):
            if with_content:
                if not measurement.get("toc_matches") or not extra_bookmarks:
                    break
                measurement = _measure_native_layout(path, deadline, extra_bookmarks)
            actual = measurement.get("toc")
            if (not isinstance(actual, list)
                    or [row.get("bookmark") for row in actual] != target_names
                    or measurement.get("source_sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
                    or not measurement.get("pagination_stable")):
                raise ProfileError("Word 目录分页与已确认章节不匹配")
        if measurement.get("toc_matches"):
            final_state = _source_toc_state(etree.fromstring(parts["word/document.xml"]), profile)
            if not final_state or [row["key"] for row in final_state] != [row["key"] for row in rows]:
                raise ProfileError("Word 目录页码更新后章节结构发生变化")
            if [row["page"] for row in final_state] != [row.get("cached_page") for row in actual]:
                raise ProfileError("Word 分页证据与目录缓存不一致")
            _validated_layout_bookmarks(measurement.get("extra_bookmarks"), extra_bookmarks,
                                        measurement.get("page_count"), actual)
            return final_state, measurement
        try:
            updated = update_word_directory_cache(path.read_bytes(), measurement)
        except WordNativeLayoutError as exc:
            raise ProfileError("Word 目录页码无法安全更新：" + str(exc)) from exc
        with zipfile.ZipFile(io.BytesIO(updated)) as archive:
            revised = {name: archive.read(name) for name in archive.namelist()}
        if set(revised) != set(parts) or any(
                revised[name] != parts[name] for name in parts if name != "word/document.xml"):
            raise ProfileError("Word 页码更新修改了非目录文件")
        if revised["word/document.xml"] == parts["word/document.xml"]:
            raise ProfileError("Word 目录页码未能更新")
        parts["word/document.xml"] = revised["word/document.xml"]
    raise ProfileError("Word 目录页码未能在重试上限内收敛")


def _native_word_font_inventory(data: bytes, directory: Path, deadline: float) -> dict:
    """Collect coarse Word font evidence; it does not establish per-run fidelity."""
    from app.services.word_optional_pdf import WordPdfError, _SCRIPT, create_optional_pdf

    source_sha256 = hashlib.sha256(data).hexdigest()
    observations = []
    for attempt in range(2):
        remaining = int(remaining_export_seconds(min(110, max(0, deadline - time.monotonic()))))
        if remaining < 1:
            raise ProfileError("Word 字体证据采集超过总时限")
        try:
            pdf, evidence = create_optional_pdf(data, timeout=remaining)
        except (WordPdfError, ProfileError) as exc:
            raise ProfileError("Word 字体证据采集失败") from exc
        if (evidence.get("source_sha256") != source_sha256
                or evidence.get("pdf_sha256") != hashlib.sha256(pdf).hexdigest()):
            raise ProfileError("Word 字体证据与原文件不匹配")
        path = directory / f"word-fonts-{attempt}.pdf"
        path.write_bytes(pdf)
        observed = _pdf_state(path)
        if observed.get("page_count") != evidence.get("page_count"):
            raise ProfileError("Word 字体证据分页不一致")
        observations.append({"pdf_sha256": evidence["pdf_sha256"],
                             "page_count": evidence["page_count"],
                             "word_version": evidence["word_version"],
                             "fonts": observed.get("fonts")})
        check_export_budget()
    return {"source_sha256": source_sha256, "script_sha256": hashlib.sha256(_SCRIPT.encode()).hexdigest(),
            "observations": observations}


def _refresh_master_layout_native(output_path: Path, project, profile):
    """Prepare TOC with Word and keep its temporary font evidence non-authorizing."""
    from app.services.source_template_safety import audit_generated_package
    from app.services.word_native_layout import WordNativeLayoutError, _WORD_TOC_SCRIPT, refresh_word_toc

    with zipfile.ZipFile(output_path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    def write(path, contents=None):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in (parts if contents is None else contents).items():
                archive.writestr(name, data)
    with tempfile.TemporaryDirectory(prefix="paperguard-native-layout-") as temp:
        directory = Path(temp)
        deadline = time.monotonic() + remaining_export_seconds(_NATIVE_LAYOUT_STAGE_SECONDS)
        source = directory / "toc-source.docx"
        write(source)
        remaining = int(remaining_export_seconds(min(90, max(0, deadline - time.monotonic()))))
        if remaining < 1:
            raise ProfileError("Word 目录更新超过总时限")
        try:
            updated = refresh_word_toc(source, timeout=remaining)
        except (WordNativeLayoutError, OSError) as exc:
            raise ProfileError("Word 目录更新失败：" + str(exc)) from exc
        check_export_budget()
        rows = _word_generated_toc_rows(parts, updated, profile, directory / "toc-only.docx")
        _replace_toc_rows(parts, rows, profile)
        content_types = etree.fromstring(parts["[Content_Types].xml"], safe_xml_parser())
        if not any(node.get("Extension") == "json" for node in content_types):
            etree.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Default",
                             Extension="json", ContentType="application/json")
        parts["[Content_Types].xml"] = etree.tostring(content_types, xml_declaration=True,
                                                        encoding="UTF-8", standalone=True)
        extra_bookmarks = required_layout_bookmarks(project, profile)
        final_state, word = _native_word_toc(parts, profile, rows, directory, write, deadline, extra_bookmarks)
        candidate = directory / "word-layout-candidate.docx"
        candidate_data = candidate.read_bytes()
        if hashlib.sha256(candidate_data).hexdigest() != word.get("source_sha256"):
            raise ProfileError("Word 目录分页证据与候选文件不匹配")
        fonts = _native_word_font_inventory(candidate_data, directory, deadline)
        observations = fonts.get("observations")
        if (fonts.get("source_sha256") != word.get("source_sha256")
                or not isinstance(observations, list) or len(observations) != 2):
            raise ProfileError("Word 字体证据与候选文件不匹配")
        required_fonts = _required_fonts(project, profile, parts)
        for observed in observations:
            values = observed.get("fonts") if isinstance(observed, dict) else None
            if (not isinstance(values, list) or observed.get("word_version") != word.get("word_version")
                    or observed.get("page_count") != word.get("page_count")):
                raise ProfileError("Word 字体证据环境或分页不一致")
            actual = {_font_name(value) for value in values}
            missing = [font for font in required_fonts if _font_name(font) not in actual]
            if missing:
                raise ProfileError("Word 排版结果未使用所需字体：" + "、".join(sorted(missing)))
        table_targets = required_table_row_targets(project, profile)
        table_evidence = _measure_table_evidence(candidate, deadline, table_targets, word)
        cover_evidence = _measure_cover_evidence(candidate, deadline, profile, word)
        manifest_sha256 = digest({name: hashlib.sha256(data).hexdigest() for name, data in sorted(parts.items())})
        receipt = {
            "version": NATIVE_TOC_VERSION, "page_reference_mode": "bookmark_pageref",
            "pagination_source": "word_native", "toc_preparation_source": "word_native",
            "toc_script_sha256": hashlib.sha256(_WORD_TOC_SCRIPT.encode()).hexdigest(),
            "source_profile_sha256": profile["profile_sha256"],
            "project_content_hash": project_content_hash(project), "toc": final_state,
            "page_count": word["page_count"], "state_stable": True,
            "word_font_inventory": {**fonts, "manifest_sha256": manifest_sha256},
            "word_native": {"version": word["word_version"], "macos_version": word["macos_version"],
                            "machine": word["machine"], "probe_sha256": word["probe_sha256"],
                            "measured_sha256": word["source_sha256"], "manifest_sha256": manifest_sha256,
                            "toc": word["toc"], "extra_bookmarks": word["extra_bookmarks"],
                            "table_rows": ({**table_evidence, "manifest_sha256": manifest_sha256}
                                           if table_evidence is not None else None)},
            "document_manifest_sha256": manifest_sha256,
        }
        if cover_evidence is not None:
            receipt["word_cover"] = {**cover_evidence, "manifest_sha256": manifest_sha256}
        receipt["signature"] = _mac(receipt, NATIVE_TOC_VERSION)
        parts[RECEIPT_PART] = json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()
        final_candidate = directory / "word-final-candidate.docx"
        write(final_candidate)
        final_word = _measure_native_layout(final_candidate, deadline, extra_bookmarks)
        final_bytes = final_candidate.read_bytes()
        if (not final_word.get("toc_matches") or not final_word.get("pagination_stable")
                or final_word.get("source_sha256") != hashlib.sha256(final_bytes).hexdigest()
                or any(final_word.get(key) != word.get(key) for key in
                       ("word_version", "macos_version", "machine", "probe_sha256", "page_count", "toc", "extra_bookmarks"))):
            raise ProfileError("最终 Word 文件与分页证据不一致")
        _remeasure_final_tables(final_candidate, deadline, table_targets, final_word, table_evidence)
        _remeasure_final_cover(final_candidate, deadline, profile, final_word, cover_evidence)
        audit_generated_package(final_bytes)
        check_export_budget()
        output_path.write_bytes(final_bytes)


def refresh_master_layout(output_path: Path,project,profile):
    with ensure_export_budget():
        if os.environ.get("PAPERGUARD_NATIVE_WORD_TOC", "").strip().lower() in {"1", "true", "yes", "on"}:
            if profile.get("toc_active"):
                _refresh_master_layout_native(output_path, project, profile)
        else:
            _refresh_master_layout(output_path, project, profile)


def _refresh_master_layout(output_path: Path,project,profile):
    entries=profile.get("toc_entries",[])
    if not profile.get("toc_active"):return
    with zipfile.ZipFile(output_path) as archive:parts={n:archive.read(n) for n in archive.namelist()}
    def write(path,contents=None):
        with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as archive:
            for name,data in (parts if contents is None else contents).items():archive.writestr(name,data)
    stable=False
    final_rows=[]
    with tempfile.TemporaryDirectory(prefix="paperguard-layout-") as temp:
        directory=Path(temp)
        previous_layout=None
        for attempt in range(4):
            check_export_budget()
            input_path=directory/f"input-{attempt}.docx";write(input_path,_layout_input_parts(parts,profile))
            run=directory/f"round-{attempt}"
            _office_roundtrip(input_path,run)
            check_export_budget()
            before,after=_pdf_state(run/"before.pdf"),_pdf_state(run/"after.pdf")
            rows,_=_read_toc(run/"updated.docx",profile)
            # The layout engine may create character styles (e.g. IndexLink)
            # absent from the master. Resolve them from the measured document,
            # then flatten them onto the copied TOC runs; never import styles
            # into the master or change its paragraph-style inheritance.
            with zipfile.ZipFile(run/"updated.docx") as measured:
                character_styles = load_part(measured,"word/styles.xml")
            values=_toc_state(rows)
            current_root=etree.fromstring(parts["word/document.xml"])
            has_placeholder=bool(current_root.xpath(".//w:sdtPr/w:tag[@w:val='pg-toc:placeholder']",namespaces=NS))
            current=None if has_placeholder else _source_toc_state(current_root,profile)
            before_layout=(before["page_count"],before["destinations"])
            after_layout=(after["page_count"],after["destinations"])
            converged=(current is not None and current==values and before_layout==after_layout and previous_layout==after_layout)
            _replace_toc_rows(parts,rows,profile,character_styles_root=character_styles)
            final_rows=rows
            stable=bool(converged)
            previous_layout=after_layout
            if stable:break
        if not stable:raise ProfileError("目录与最终母版分页未能收敛，请调整目录样式后重试")
        pdf_destinations = _layout_destinations(after.get("destinations"), final_rows)
        actual_before={_font_name(f) for f in before["fonts"]}
        actual_after={_font_name(f) for f in after["fonts"]}
        missing=[f for f in _required_fonts(project,profile,parts)
                 if _font_name(f) not in actual_before or _font_name(f) not in actual_after]
        if missing:raise ProfileError("排版结果未使用所需字体，请补齐服务器字体："+"、".join(sorted(missing)))
        content_types=etree.fromstring(parts["[Content_Types].xml"])
        if not any(n.get("Extension")=="json" for n in content_types):
            etree.SubElement(content_types,"{http://schemas.openxmlformats.org/package/2006/content-types}Default",Extension="json",ContentType="application/json")
        parts["[Content_Types].xml"]=etree.tostring(content_types,xml_declaration=True,encoding="UTF-8",standalone=True)
        # LibreOffice prepares TOC rows and checks fonts; Word is the authority
        # for final pagination.  A cache mismatch must be corrected and measured
        # again before any signed delivery evidence is created.
        # The same bounded budget as native TOC preparation must cover the
        # candidate and final-document pagination plus both table-row probes.
        deadline = time.monotonic() + remaining_export_seconds(_NATIVE_LAYOUT_STAGE_SECONDS)
        extra_bookmarks = required_layout_bookmarks(project, profile)
        final_state, word = _native_word_toc(parts, profile, final_rows, directory, write, deadline,
                                             extra_bookmarks)
        table_targets = required_table_row_targets(project, profile)
        table_evidence = _measure_table_evidence(directory / "word-layout-candidate.docx", deadline,
                                                 table_targets, word)
        cover_evidence = _measure_cover_evidence(directory / "word-layout-candidate.docx", deadline, profile, word)
        manifest_sha256 = digest({n:hashlib.sha256(v).hexdigest() for n,v in sorted(parts.items())})
        receipt={"version":VERSION,"page_reference_mode":"bookmark_pageref",
            "pagination_source":"word_native",
            "worker_sha256":hashlib.sha256(WORKER.read_bytes()).hexdigest(),
            "dependency_sha256":json.loads(DEPENDENCY.read_text())["sha256"],"source_profile_sha256":profile["profile_sha256"],
            "project_content_hash":project_content_hash(project),"toc":final_state,"page_count":word["page_count"],
            "preliminary_pdf_scope":"libreoffice_toc_preparation", "preliminary_page_count":before["page_count"],
            "pdf_sha256":hashlib.sha256((run/"after.pdf").read_bytes()).hexdigest(),"fonts":after["fonts"],
            "pdf_destinations":pdf_destinations,"state_stable":True,
            "word_native":{"version":word["word_version"], "macos_version":word["macos_version"],
                "machine":word["machine"], "probe_sha256":word["probe_sha256"],
                "measured_sha256":word["source_sha256"], "manifest_sha256":manifest_sha256,
                "toc":word["toc"], "extra_bookmarks":word["extra_bookmarks"],
                "table_rows":({**table_evidence, "manifest_sha256":manifest_sha256}
                              if table_evidence is not None else None)},
            "document_manifest_sha256":manifest_sha256}
        if cover_evidence is not None:
            receipt["word_cover"] = {**cover_evidence, "manifest_sha256": manifest_sha256}
        receipt["signature"]=_mac(receipt)
        parts[RECEIPT_PART]=json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
        final_candidate = directory / "word-final-candidate.docx"
        write(final_candidate)
        # The receipt itself changes the package bytes. Measure those exact
        # final bytes before placing them at the caller's output path.
        final_word = _measure_native_layout(final_candidate, deadline, extra_bookmarks)
        final_bytes = final_candidate.read_bytes()
        if (not final_word.get("toc_matches") or not final_word.get("pagination_stable")
                or final_word.get("source_sha256") != hashlib.sha256(final_bytes).hexdigest()
                or final_word.get("word_version") != word["word_version"]
                or final_word.get("macos_version") != word["macos_version"]
                or final_word.get("machine") != word["machine"]
                or final_word.get("probe_sha256") != word["probe_sha256"]
                or final_word.get("page_count") != word["page_count"]
                or final_word.get("toc") != word["toc"]
                or final_word.get("extra_bookmarks") != word["extra_bookmarks"]):
            raise ProfileError("最终 Word 文件与分页证据不一致")
        _remeasure_final_tables(final_candidate, deadline, table_targets, final_word, table_evidence)
        _remeasure_final_cover(final_candidate, deadline, profile, final_word, cover_evidence)
        check_export_budget()
        output_path.write_bytes(final_bytes)


def verify_layout_receipt(data,project,profile):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if RECEIPT_PART not in archive.namelist():raise ProfileError("缺少本次内容的目录排版证据")
        raw=archive.read(RECEIPT_PART)
        if len(raw)>2_000_000:raise ProfileError("排版证据超出预算")
        receipt=json.loads(raw)
        signature=receipt.pop("signature","")
        version=receipt.get("version")
        mac_version=version if version in {LEGACY_VERSION, LEGACY_NATIVE_TOC_VERSION, VERSION, NATIVE_TOC_VERSION} else VERSION
        if not hmac.compare_digest(str(signature),_mac(receipt,mac_version)):raise ProfileError("目录排版证据签名无效")
        part_data={n:archive.read(n) for n in sorted(archive.namelist()) if n!=RECEIPT_PART}
        expected=digest({n:hashlib.sha256(data).hexdigest() for n,data in part_data.items()})
        if version not in {LEGACY_VERSION,LEGACY_NATIVE_TOC_VERSION,VERSION,NATIVE_TOC_VERSION} or receipt.get("page_reference_mode")!="bookmark_pageref" or not receipt.get("state_stable") or receipt.get("document_manifest_sha256")!=expected or receipt.get("source_profile_sha256")!=profile["profile_sha256"] or receipt.get("project_content_hash")!=project_content_hash(project):
            raise ProfileError("排版证据与当前文件、内容或排版组件不匹配")
        if (receipt.get("pagination_source") != "word_native"
                or type(receipt.get("page_count")) is not int or receipt["page_count"] < 1):
            raise ProfileError("排版证据缺少有效的 Word 页数")
        destinations = None
        if version in {LEGACY_VERSION, VERSION}:
            dependency = json.loads(DEPENDENCY.read_text())
            if (receipt.get("worker_sha256")!=hashlib.sha256(WORKER.read_bytes()).hexdigest()
                    or receipt.get("dependency_sha256")!=dependency.get("sha256")):
                raise ProfileError("排版证据与当前文件、内容或排版组件不匹配")
            if (receipt.get("preliminary_pdf_scope") != "libreoffice_toc_preparation"
                    or type(receipt.get("preliminary_page_count")) is not int or receipt["preliminary_page_count"] < 1
                    or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("pdf_sha256", "")))):
                raise ProfileError("排版证据缺少有效的 PDF 摘要或页数")
            destinations = receipt.get("pdf_destinations")
            if not isinstance(destinations, list) or not destinations:
                raise ProfileError("排版证据缺少 PDF 目录目标页定位")
            for item in destinations:
                if (not isinstance(item, dict) or not isinstance(item.get("key"), str)
                        or not isinstance(item.get("title"), str)
                        or not isinstance(item.get("label"), str)
                        or not isinstance(item.get("page"), int)
                        or item["page"] < 1 or item["page"] > receipt["preliminary_page_count"]):
                    raise ProfileError("排版证据中的 PDF 目录目标页定位无效")
            fonts = receipt.get("fonts")
            if (not isinstance(fonts, list) or len(fonts) > 1000
                    or any(not isinstance(font, str) or len(font) > 200 for font in fonts)):
                raise ProfileError("排版字体证据无效")
            actual_fonts = {_font_name(font) for font in fonts}
            if any(_font_name(font) not in actual_fonts for font in _required_fonts(project, profile, part_data)):
                raise ProfileError("排版证据缺少所需字体")
        toc=receipt.get("toc")
        if not isinstance(toc,list):raise ProfileError("目录排版证据格式无效")
        keys=[row.get("key") for row in toc if isinstance(row,dict)]
        expected_keys={e["target_key"] for e in profile.get("toc_entries",[])}
        if len(keys)!=len(toc) or len(keys)!=len(set(keys)) or not expected_keys.issubset(set(keys)):
            raise ProfileError("目录排版证据不完整或包含重复章节")
        heading_by_key={h["key"]:h for h in profile.get("headings",[])}
        for row in toc:
            heading=heading_by_key.get(row.get("key"))
            if not heading or row.get("target_title")!=heading.get("title") or row.get("bookmark")!=heading.get("bookmark_name") or not re.fullmatch(r"[1-9][0-9]*|[ivxlcdmIVXLCDM]+",str(row.get("page",""))):
                raise ProfileError("目录排版证据与已确认章节不匹配")
        # The signed native evidence is independently checked against the live
        # package and this configured runtime; no PDF is needed for this check.
        from app.services.word_native_layout import (
            WordNativeLayoutError, _WORD_SCRIPT, _directory_references, _page_value,
            _validate_extra_bookmarks,
        )
        native = receipt.get("word_native")
        if (not isinstance(native, dict)
                or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", str(native.get("version", "")))
                or native.get("macos_version") != platform.mac_ver()[0]
                or native.get("machine") != platform.machine()
                or native.get("probe_sha256") != hashlib.sha256(_WORD_SCRIPT.encode()).hexdigest()
                or native.get("manifest_sha256") != expected
                or not re.fullmatch(r"[0-9a-f]{64}", str(native.get("measured_sha256", "")))):
            raise ProfileError("Word 原生分页证据缺失、过期或环境不匹配")
        if version in {LEGACY_NATIVE_TOC_VERSION, NATIVE_TOC_VERSION}:
            from app.services.word_native_layout import _WORD_TOC_SCRIPT
            from app.services.word_optional_pdf import _SCRIPT as word_pdf_script
            inventory = receipt.get("word_font_inventory")
            if (receipt.get("toc_preparation_source") != "word_native"
                    or receipt.get("toc_script_sha256") != hashlib.sha256(_WORD_TOC_SCRIPT.encode()).hexdigest()
                    or not isinstance(inventory, dict) or inventory.get("manifest_sha256") != expected
                    or inventory.get("source_sha256") != native.get("measured_sha256")
                    or inventory.get("script_sha256") != hashlib.sha256(word_pdf_script.encode()).hexdigest()):
                raise ProfileError("Word 目录或字体证据缺失、过期或与文件不匹配")
            observations = inventory.get("observations")
            if not isinstance(observations, list) or len(observations) != 2:
                raise ProfileError("Word 字体证据不完整")
            needed = _required_fonts(project, profile, part_data)
            for observation in observations:
                fonts = observation.get("fonts") if isinstance(observation, dict) else None
                if (not isinstance(fonts, list) or len(fonts) > 1000
                        or any(not isinstance(font, str) or len(font) > 200 for font in fonts)
                        or observation.get("word_version") != native["version"]
                        or observation.get("page_count") != receipt["page_count"]
                        or not re.fullmatch(r"[0-9a-f]{64}", str(observation.get("pdf_sha256", "")))):
                    raise ProfileError("Word 字体证据无效")
                actual = {_font_name(font) for font in fonts}
                if any(_font_name(font) not in actual for font in needed):
                    raise ProfileError("Word 排版证据缺少所需字体")
        native_rows = native.get("toc")
        refs = _directory_references(data)
        if not isinstance(native_rows, list) or len(native_rows) != len(toc) or len(refs) != len(toc):
            raise ProfileError("Word 原生目录分页证据不完整")
        for row, (bookmark, cached), observed in zip(toc, refs, native_rows):
            if (not isinstance(observed, dict) or observed.get("bookmark") != bookmark
                    or bookmark != row.get("bookmark") or cached != row.get("page")
                    or observed.get("cached_page") != cached or observed.get("matches") is not True
                    or type(observed.get("physical_page")) is not int
                    or not 1 <= observed["physical_page"] <= receipt["page_count"]
                    or type(observed.get("logical_page")) is not int
                    or _page_value(cached) != observed["logical_page"]):
                raise ProfileError("Word 原生分页与当前目录不一致")
        extra_bookmarks = required_layout_bookmarks(project, profile)
        try:
            _validate_extra_bookmarks(data, extra_bookmarks)
        except (WordNativeLayoutError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise ProfileError("Word 内容布局书签与当前文件不一致") from exc
        _validated_layout_bookmarks(native.get("extra_bookmarks"), extra_bookmarks,
                                    receipt["page_count"], native_rows)
        if version in {VERSION, NATIVE_TOC_VERSION}:
            from app.services.word_native_layout import _validate_table_targets
            targets = required_table_row_targets(project, profile)
            try:
                _validate_table_targets(data, targets)
            except (WordNativeLayoutError, KeyError, ValueError, zipfile.BadZipFile) as exc:
                raise ProfileError("Word 表格分页书签与当前文件不一致") from exc
            row_evidence = native.get("table_rows")
            if "table_rows" not in native or (row_evidence is not None
                    and (not isinstance(row_evidence, dict) or row_evidence.get("manifest_sha256") != expected)):
                raise ProfileError("Word 表格分页证据与当前文件不一致")
            expected_word = {"word_version": native["version"], "macos_version": native["macos_version"],
                             "machine": native["machine"], "page_count": receipt["page_count"],
                             "extra_bookmarks": native["extra_bookmarks"]}
            _validated_table_row_evidence(row_evidence, targets, expected_word, native["measured_sha256"])
        cover_policy = _cover_policy(profile)
        cover_evidence = receipt.get("word_cover")
        if cover_policy is not None:
            if not isinstance(cover_evidence, dict) or cover_evidence.get("manifest_sha256") != expected:
                raise ProfileError("Word 封面分页证据与当前文件不一致")
            expected_word = {"word_version": native["version"], "macos_version": native["macos_version"],
                             "machine": native["machine"], "page_count": receipt["page_count"],
                             "extra_bookmarks": native["extra_bookmarks"]}
            _validated_cover_evidence(cover_evidence, cover_policy, expected_word, native["measured_sha256"])
        elif cover_evidence is not None:
            raise ProfileError("Word 封面分页证据包含未请求的规则")
        if destinations is not None:
            destination_keys = [item["key"] for item in destinations]
            if destination_keys != keys:
                raise ProfileError("排版 PDF 目录目标与目录行顺序不一致")
        return receipt
