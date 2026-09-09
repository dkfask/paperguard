"""文档级自动检查器的规则覆盖。"""

from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.services.projects import default_template
from app.services.policy_parser import items_from_markdown
from app.services.requirement_check import check_requirements


def _project() -> WritingProject:
    return WritingProject(
        id="project-check",
        template_id="template-check",
        sections=[
            ProjectSection(
                key="abstract", type="abstract", title="摘要",
                paragraphs=[ParagraphBlock(id="abstract-p1", text="摘要内容")],
            ),
            ProjectSection(
                key="keywords", type="keywords", title="关键词",
                paragraphs=[ParagraphBlock(id="keywords-p1", text="关键词：系统；规范")],
            ),
            ProjectSection(
                key="references", type="references", title="参考文献",
                paragraphs=[
                    ParagraphBlock(id="ref-p1", text="作者. 研究. 2025."),
                    ParagraphBlock(id="ref-p2", text="作者. 旧研究. 2019."),
                ],
            ),
        ],
    )


def test_document_checkers_return_findings_and_manual_items():
    snapshot = {
        "resolved_items": [
            {
                "id": "abstract-min", "type": "auto", "target": "abstract",
                "checker": "abstract.word_count", "params": {"min": 10},
                "severity": "error", "enforce": "hard", "description": "摘要过短",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "keywords-min", "type": "auto", "target": "keywords",
                "checker": "keywords.count", "params": {"min": 3},
                "severity": "warning", "enforce": "soft", "description": "关键词不足",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "recent-min", "type": "auto", "target": "references",
                "checker": "references.recent_count", "params": {"years": 3, "min": 2, "current_year": 2025},
                "severity": "warning", "enforce": "soft", "description": "近年文献不足",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "manual-quality", "type": "manual", "target": "document",
                "checker": "", "params": {}, "severity": "info", "enforce": "soft",
                "description": "人工确认论证质量", "source": "教师要求", "source_version": "2",
            },
        ],
    }
    findings, not_checked = check_requirements(_project(), snapshot)
    assert {finding["requirement_item_id"] for finding in findings} == {
        "abstract-min", "keywords-min", "recent-min",
    }
    assert findings[0]["source"] == "学校规范"
    assert not_checked[0]["id"] == "manual-quality"
    assert not_checked[0]["reason"] == "manual"


def test_required_section_is_checked_for_presence_and_content():
    project = _project()
    findings, _ = check_requirements(project, {
        "resolved_items": [{
            "id": "method", "type": "auto", "target": "methodology",
            "checker": "section.exists", "params": {}, "severity": "error",
            "enforce": "hard", "description": "必须包含研究方法", "source": "专业规范",
        }],
    })
    assert findings[0]["section_key"] == "methodology"
    assert findings[0]["actual"] == 0


def test_reference_format_checker_and_chapter_page_break_checker():
    template = default_template().model_copy(deep=True)
    template.styles["heading1"].page_break_before = False
    findings, not_checked = check_requirements(_project(), {
        "resolved_items": [
            {
                "id": "reference-format", "type": "auto", "target": "references",
                "checker": "references.format", "params": {}, "severity": "warning",
                "enforce": "soft", "description": "参考文献应符合著录格式",
                "source": "学校规范", "source_version": "1",
            },
            {
                "id": "chapter-break", "type": "auto", "target": "ch1",
                "checker": "chapter.page_break", "params": {}, "severity": "error",
                "enforce": "hard", "description": "一级章节应章前分页",
                "source": "学校规范", "source_version": "1",
            },
        ],
    }, template)
    assert {finding["requirement_item_id"] for finding in findings} == {
        "reference-format", "chapter-break",
    }
    assert not not_checked

    project = _project().model_copy(deep=True)
    project.sections[-1].paragraphs[0].text = "[1] 作者. 研究. 2025."
    project.sections[-1].paragraphs[1].text = "[2] 作者. 旧研究. 2019."
    findings, _ = check_requirements(project, {
        "resolved_items": [{
            "id": "reference-format", "type": "auto", "target": "references",
            "checker": "references.format", "params": {}, "severity": "warning",
            "enforce": "soft", "description": "参考文献应符合著录格式",
        }],
    }, template)
    assert findings == []


def test_markdown_frontmatter_maps_p0_checker_aliases():
    items = items_from_markdown("""---
rules:
  - id: refs-format
    type: reference_format
    target: references
  - id: chapter-break
    type: chapter_page_break
    target: ch1
---
""")
    assert [item.checker for item in items] == ["references.format", "chapter.page_break"]
