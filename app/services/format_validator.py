"""Validation rules for projects against templates."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.project import Project
from app.models.template import Template


@dataclass
class ValidationIssue:
    code: str
    section_id: str
    message: str
    severity: str = "warning"
    target: str = ""
    fix_hint: str = ""

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] {self.code} {self.section_id}: {self.message}"


def validate_project(project: Project, template: Template) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if project.title.strip() in {"", "未命名论文"}:
        issues.append(
            ValidationIssue(
                code="metadata.title.required",
                section_id="cover",
                target="title",
                severity="error",
                message="论文标题未填写。",
                fix_hint="请在右侧项目信息面板中填写论文标题。",
            )
        )
    if not project.author.strip():
        issues.append(
            ValidationIssue(
                code="metadata.author.missing",
                section_id="cover",
                target="author",
                severity="warning",
                message="作者姓名未填写。",
                fix_hint="建议补充作者姓名，以便生成完整封面。",
            )
        )
    if not project.student_id.strip():
        issues.append(
            ValidationIssue(
                code="metadata.student_id.missing",
                section_id="cover",
                target="student_id",
                severity="warning",
                message="学号未填写。",
                fix_hint="建议补充学号。",
            )
        )
    if not project.major.strip():
        issues.append(
            ValidationIssue(
                code="metadata.major.missing",
                section_id="cover",
                target="major",
                severity="warning",
                message="专业未填写。",
                fix_hint="建议补充专业信息。",
            )
        )
    if not project.supervisor.strip():
        issues.append(
            ValidationIssue(
                code="metadata.supervisor.missing",
                section_id="cover",
                target="supervisor",
                severity="warning",
                message="指导教师未填写。",
                fix_hint="建议补充指导教师信息。",
            )
        )

    for _, node, _ in template.flatten_structure():
        if node.auto_generate or node.type == "cover":
            continue

        content = project.sections.get(node.id)
        has_content = bool(content and content.has_meaningful_content())

        if node.required and not has_content:
            issues.append(
                ValidationIssue(
                    code="section.required.empty",
                    section_id=node.id,
                    target=node.id,
                    severity="error",
                    message=f"必填章节“{node.label}”尚未完成。",
                    fix_hint="请在左侧章节树中定位该章节并补充正文、图片、表格或列表内容。",
                )
            )
            continue

        if not node.required and content is not None and content.blocks and not has_content:
            issues.append(
                ValidationIssue(
                    code="section.optional.empty",
                    section_id=node.id,
                    target=node.id,
                    severity="warning",
                    message=f"章节“{node.label}”当前为空。",
                    fix_hint="如该章节需要保留，建议补充有效内容；否则可维持空白。",
                )
            )

    return issues
