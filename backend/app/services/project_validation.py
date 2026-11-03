"""项目完整性校验 — 导出前检查必填章节与封面字段。

模板约束的一部分: 必填章节未完成 / 必填封面字段为空时阻止导出,
返回缺失清单供前端展示。
"""

from __future__ import annotations

from app.models.project import WritingProject
from app.models.template import PaperTemplate, SectionType
from app.services.references import reference_block_text


def _section_complete(proj_sec) -> bool:
    """一节是否算完成: 有标题, 且有任一有内容的内容块。"""
    if not proj_sec:
        return False
    if not proj_sec.title or not proj_sec.title.strip():
        return False
    if not proj_sec.paragraphs:
        return False
    for par in proj_sec.paragraphs:
        if par.type.value == "image":
            if par.image and par.image.url:
                return True
        elif par.type.value == "table":
            if par.table and any(
                str(c).strip() for row in par.table.cells for c in row
            ):
                return True
        else:
            if reference_block_text(par).strip():
                return True
    return False


def section_complete_with_children(index: int, sections: list) -> bool:
    """判断一节是否完成, 允许一级标题由下属内容节承载正文。

    写作项目把模板树展开成扁平列表。一级标题常只有标题, 正文写在随后
    的二级/三级小节中; 不能因此把整章判定为未完成。
    """
    section = sections[index]
    if _section_complete(section):
        return True
    if not section.title.strip() or section.type != SectionType.HEADING1.value:
        return False

    for child in sections[index + 1:]:
        if child.type == SectionType.HEADING1.value:
            break
        if child.type in (SectionType.HEADING2.value, SectionType.HEADING3.value) \
                and _section_complete(child):
            return True
    return False


def validate_heading_hierarchy(sections: list) -> list[str]:
    """Check visible chapter headings without preventing draft edits.

    Body containers do not start a new outline. References, acknowledgements,
    and appendices are top-level parents; front matter closes the prior outline.
    Untitled headings are not visible parents in the exported document.
    """
    issues: list[str] = []
    has_chapter = False
    has_section = False
    for section in sections:
        section_type = section.type
        title = (section.title or "").strip()
        if section_type == SectionType.HEADING1.value:
            has_chapter = bool(title)
            has_section = False
        elif section_type == SectionType.HEADING2.value:
            if title and not has_chapter:
                issues.append(f"二级标题「{title}」缺少有效的一级标题，请补充上级标题或调整级别")
            has_section = bool(title) and has_chapter
        elif section_type == SectionType.HEADING3.value:
            if title and not has_section:
                parent = "二级" if has_chapter else "一级和二级"
                issues.append(f"三级标题「{title}」缺少有效的{parent}标题，请补充上级标题或调整级别")
        elif section_type in (SectionType.REFERENCES.value, SectionType.ACKNOWLEDGEMENT.value, SectionType.APPENDIX.value):
            has_chapter = bool(title)
            has_section = False
        elif section_type != SectionType.BODY.value:
            has_chapter = False
            has_section = False
    return issues


def validate_project_completeness(
    proj: WritingProject, tpl: PaperTemplate
) -> list[str]:
    """返回缺失项清单; 空列表表示可以导出。

    检查:
    - 封面必填字段
    - 模板中 required=True 的章节(标题节与内容节)
    - 标题是否缺少有效的上级标题
    """
    missing = validate_heading_hierarchy(proj.sections)

    # 封面必填字段
    for f in tpl.meta_fields:
        if f.required and not proj.meta.get(f.key, "").strip():
            missing.append(f"封面「{f.label}」未填写")

    # 模板快照中的必填项不能被项目中的删除或 required=False 绕过。
    required = {}

    def collect(nodes):
        for node in nodes:
            if node.required:
                required[node.key] = node
            collect(node.children)

    collect(tpl.sections)
    for sec in proj.sections:
        if sec.required:
            required.setdefault(sec.key, sec)
    indices = {sec.key: index for index, sec in enumerate(proj.sections)}
    for key, sec in required.items():
        if sec.type in (SectionType.COVER.value, SectionType.TOC.value):
            continue  # 封面走字段, 目录自动生成
        index = indices.get(key)
        if index is None or proj.sections[index].type != sec.type or not section_complete_with_children(index, proj.sections):
            missing.append(f"「{sec.title or sec.key}」未完成(标题或内容为空)")

    for section in proj.sections:
        if section.type != SectionType.REFERENCES.value and section.key != "references":
            continue
        for index, block in enumerate(section.paragraphs, start=1):
            reference = block.reference
            if not reference:
                continue
            has_fact = any(str(getattr(reference, field) or "").strip() for field in (
                "authors", "title", "source", "collection", "publisher", "institution", "organization",
            ))
            if reference.kind == "unparsed" or (reference.original_pasted_text is not None and not has_fact):
                label = f"[{block.reference_number}]" if block.reference_number else f"第 {index} 条"
                missing.append(f"参考文献 {label} 尚未确认类型和补录事实字段")
            elif reference.original_pasted_text is not None:
                label = f"[{block.reference_number}]" if block.reference_number else f"第 {index} 条"
                missing.append(f"参考文献 {label} 的标准著录尚未完成自动核验，原文已保留，请等待系统适配")

    return missing
