"""Controller for validation summaries and section progress."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.project import Project, SectionContent
from app.models.template import Template
from app.services.format_validator import ValidationIssue, validate_project


SectionProgressStatus = str


@dataclass
class ReviewSummary:
    issues: list[ValidationIssue]
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    statuses: dict[str, SectionProgressStatus]
    completed_required: int
    total_required: int


class ReviewController:
    """Builds user-facing review summaries."""

    def build_summary(self, project: Project, template: Template | None) -> ReviewSummary:
        if template is None:
            return ReviewSummary([], [], [], {}, 0, 0)

        issues = validate_project(project, template)
        errors = [issue for issue in issues if issue.severity == "error"]
        warnings = [issue for issue in issues if issue.severity != "error"]

        statuses: dict[str, SectionProgressStatus] = {}
        issue_by_section = {}
        for issue in issues:
            issue_by_section.setdefault(issue.section_id, []).append(issue)

        total_required = 0
        completed_required = 0

        for _, node, _ in template.flatten_structure():
            if node.auto_generate:
                continue
            content = project.sections.get(node.id)
            status = self._status_for_section(content, issue_by_section.get(node.id, []))
            statuses[node.id] = status
            if node.required and node.type != "cover":
                total_required += 1
                if status == "complete":
                    completed_required += 1

        return ReviewSummary(
            issues=issues,
            errors=errors,
            warnings=warnings,
            statuses=statuses,
            completed_required=completed_required,
            total_required=total_required,
        )

    def _status_for_section(
        self,
        content: SectionContent | None,
        issues: list[ValidationIssue],
    ) -> SectionProgressStatus:
        if any(issue.severity == "error" for issue in issues):
            return "error"
        if any(issue.severity == "warning" for issue in issues):
            return "warning"
        if content is None or not content.blocks:
            return "empty"
        if content.has_meaningful_content():
            return "complete"
        return "started"
