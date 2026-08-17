"""项目完整性校验 — 导出前检查必填章节与封面字段。

模板约束的一部分: 必填章节未完成 / 必填封面字段为空时阻止导出,
返回缺失清单供前端展示。
"""

from __future__ import annotations

from app.models.project import WritingProject
from app.models.template import PaperTemplate, SectionType


def _section_complete(proj_sec) -> bool:
    """一节是否算完成: 有标题, 且有任一有内容的内容块。"""
    if not proj_sec:
        return False
    if not proj_sec.title or not proj_sec.title.strip():
        return False
    if not proj_sec.paragraphs:
        return True  # 无内容块的节(如纯标题节)只要标题即可
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
            if par.text.strip():
                return True
    return False


def validate_project_completeness(
    proj: WritingProject, tpl: PaperTemplate
) -> list[str]:
    """返回缺失项清单; 空列表表示可以导出。

    检查:
    - 封面必填字段
    - 模板中 required=True 的章节(标题节与内容节)
    """
    missing: list[str] = []

    # 封面必填字段
    for f in tpl.meta_fields:
        if f.required and not proj.meta.get(f.key, "").strip():
            missing.append(f"封面「{f.label}」未填写")

    # 必填章节
    proj_map = {s.key: s for s in proj.sections}
    for sec in proj.sections:
        if not sec.required:
            continue
        if sec.type in (SectionType.COVER.value, SectionType.TOC.value):
            continue  # 封面走字段, 目录自动生成
        if not _section_complete(sec):
            missing.append(f"「{sec.title or sec.key}」未完成(标题或内容为空)")

    return missing
