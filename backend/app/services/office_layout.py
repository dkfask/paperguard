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

from app.core.extract.xml_utils import NS, load_part, qn
from app.services.project_versions import project_content_hash
from app.services.secret_store import _require_master_key
from app.services.source_template_profile import ProfileError, digest, package_manifest

WORKER = Path(__file__).with_name("office_worker") / "PaperGuardLayout.java"
DEPENDENCY = WORKER.with_name("dependency.json")
RECEIPT_PART = "customXml/paperguard-layout.json"
VERSION = "office-layout/4"


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
    with (directory/"office.log").open("wb") as log:
        process = subprocess.Popen([converter,"--headless","--nologo","--norestore","--nodefault",
            f"-env:UserInstallation={profile.resolve().as_uri()}",f"--accept=pipe,name={name};urp;StarOffice.ComponentContext"],
            stdout=log,stderr=log,env=environment,start_new_session=True,umask=0o077)
        pipe = None
        try:
            deadline = time.monotonic()+20
            while time.monotonic()<deadline:
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
                time.sleep(.1)
            if pipe is None:
                raise ProfileError("排版组件启动失败，目录结果未获验证")
            result=subprocess.run([java,"-Xmx256m","-cp",str(jar),str(WORKER),str(pipe),str(input_path),str(directory)],
                capture_output=True,text=True,timeout=120,env=environment)
            if result.returncode!=0 or result.stdout.strip()!="OK" or not all((directory/name).is_file() for name in ("before.pdf","after.pdf","updated.docx")):
                raise ProfileError("排版组件未能完成目录刷新，未提供未经验证的文件")
        except (OSError,subprocess.TimeoutExpired) as exc:
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
    # macOS system CJK substitutes are deliberately mapped by the isolated
    # fontconfig used for layout trials: Songti SC for 宋体 and Heiti SC for
    # 黑体. PDF embeds these PostScript family names.
    return {"songtisc":"simsun","stsongtisc":"simsun",
            "heitisc":"simhei","stheitimedium":"simhei","stheitiscmedium":"simhei",
            "kaitisc":"kaiti"}.get(value,value)


def _required_fonts(project,profile):
    values=set(profile.get("toc_font_requirements", []))
    def add(text,properties):
        if re.search(r"[\u3400-\u9fff]",text) and isinstance(properties.get("font.eastAsia"),str):values.add(properties["font.eastAsia"])
        if re.search(r"[a-zA-Z0-9]",text) and isinstance(properties.get("font.ascii"),str):values.add(properties["font.ascii"])
    for heading in profile["headings"]:
        section=next((s for s in project.sections if s.key==heading["key"]),None)
        if section:
            for block in section.paragraphs:
                if block.type.value=="paragraph":add(block.text,heading["properties"])
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


def _replace_toc_rows(parts, rows, profile):
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
    for offset,row in enumerate(rows):
        paragraph=copy.deepcopy(row["paragraph"])
        _install_page_reference(paragraph,row["bookmark"],row["page"])
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


def _mac(payload):
    return hmac.new(_require_master_key(),(VERSION+digest(payload)).encode(),hashlib.sha256).hexdigest()


def refresh_master_layout(output_path: Path,project,profile):
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
            input_path=directory/f"input-{attempt}.docx";write(input_path,_layout_input_parts(parts,profile))
            run=directory/f"round-{attempt}"
            _office_roundtrip(input_path,run)
            before,after=_pdf_state(run/"before.pdf"),_pdf_state(run/"after.pdf")
            rows,_=_read_toc(run/"updated.docx",profile)
            values=_toc_state(rows)
            current_root=etree.fromstring(parts["word/document.xml"])
            has_placeholder=bool(current_root.xpath(".//w:sdtPr/w:tag[@w:val='pg-toc:placeholder']",namespaces=NS))
            current=None if has_placeholder else _source_toc_state(current_root,profile)
            before_layout=(before["page_count"],before["destinations"])
            after_layout=(after["page_count"],after["destinations"])
            converged=(current is not None and current==values and before_layout==after_layout and previous_layout==after_layout)
            _replace_toc_rows(parts,rows,profile)
            final_rows=rows
            stable=bool(converged)
            previous_layout=after_layout
            if stable:break
        if not stable:raise ProfileError("目录与最终母版分页未能收敛，请调整目录样式后重试")
        actual={_font_name(f) for f in before["fonts"]}
        missing=[f for f in _required_fonts(project,profile) if _font_name(f) not in actual]
        if missing:raise ProfileError("排版结果未使用所需字体，请补齐服务器字体："+"、".join(sorted(missing)))
        receipt={"version":VERSION,"page_reference_mode":"bookmark_pageref",
            "worker_sha256":hashlib.sha256(WORKER.read_bytes()).hexdigest(),
            "dependency_sha256":json.loads(DEPENDENCY.read_text())["sha256"],"source_profile_sha256":profile["profile_sha256"],
            "project_content_hash":project_content_hash(project),"toc":_toc_state(final_rows),"page_count":before["page_count"],
            "pdf_sha256":hashlib.sha256((run/"after.pdf").read_bytes()).hexdigest(),"fonts":after["fonts"],"state_stable":True}
        content_types=etree.fromstring(parts["[Content_Types].xml"])
        if not any(n.get("Extension")=="json" for n in content_types):
            etree.SubElement(content_types,"{http://schemas.openxmlformats.org/package/2006/content-types}Default",Extension="json",ContentType="application/json")
        parts["[Content_Types].xml"]=etree.tostring(content_types,xml_declaration=True,encoding="UTF-8",standalone=True)
        receipt["document_manifest_sha256"]=digest({n:hashlib.sha256(v).hexdigest() for n,v in sorted(parts.items())})
        receipt["signature"]=_mac(receipt)
        parts[RECEIPT_PART]=json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
        write(output_path)


def verify_layout_receipt(data,project,profile):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if RECEIPT_PART not in archive.namelist():raise ProfileError("缺少本次内容的目录排版证据")
        raw=archive.read(RECEIPT_PART)
        if len(raw)>2_000_000:raise ProfileError("排版证据超出预算")
        receipt=json.loads(raw)
        signature=receipt.pop("signature","")
        if not hmac.compare_digest(str(signature),_mac(receipt)):raise ProfileError("目录排版证据签名无效")
        expected=digest({n:hashlib.sha256(archive.read(n)).hexdigest() for n in sorted(archive.namelist()) if n!=RECEIPT_PART})
        dependency = json.loads(DEPENDENCY.read_text())
        if receipt.get("version")!=VERSION or receipt.get("page_reference_mode")!="bookmark_pageref" or not receipt.get("state_stable") or receipt.get("document_manifest_sha256")!=expected or receipt.get("source_profile_sha256")!=profile["profile_sha256"] or receipt.get("project_content_hash")!=project_content_hash(project) or receipt.get("worker_sha256")!=hashlib.sha256(WORKER.read_bytes()).hexdigest() or receipt.get("dependency_sha256")!=dependency.get("sha256"):
            raise ProfileError("排版证据与当前文件、内容或排版组件不匹配")
        if not isinstance(receipt.get("page_count"), int) or receipt["page_count"] < 1 or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("pdf_sha256", ""))):
            raise ProfileError("排版证据缺少有效的 PDF 摘要或页数")
        toc=receipt.get("toc")
        if not isinstance(toc,list):raise ProfileError("目录排版证据格式无效")
        keys=[row.get("key") for row in toc if isinstance(row,dict)]
        expected={e["target_key"] for e in profile.get("toc_entries",[])}
        if len(keys)!=len(toc) or len(keys)!=len(set(keys)) or not expected.issubset(set(keys)):
            raise ProfileError("目录排版证据不完整或包含重复章节")
        heading_by_key={h["key"]:h for h in profile.get("headings",[])}
        for row in toc:
            heading=heading_by_key.get(row.get("key"))
            if not heading or row.get("target_title")!=heading.get("title") or row.get("bookmark")!=heading.get("bookmark_name") or not re.fullmatch(r"[1-9][0-9]*|[ivxlcdmIVXLCDM]+",str(row.get("page",""))):
                raise ProfileError("目录排版证据与已确认章节不匹配")
        return receipt
