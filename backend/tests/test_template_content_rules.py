from app.models.project import ParagraphBlock, ProjectSection, TableBlock, WritingProject
from app.models.template import SectionType, TemplateSection
from app.models.writing_specification import SpecificationEvidence, WritingRule, WritingSpecification
from app.services.requirement_check import check_requirements
from app.services.template_content_rules import (
    compile_confirmed_content_requirements,
    review_content_instructions,
)


def _specification(text: str, *, region_title: str = "摘要") -> WritingSpecification:
    instruction = SpecificationEvidence(
        node_id="instruction-1", part="word/document.xml", path="/w:document/w:body/w:p[4]",
        role="instruction", region="body", text=text,
    )
    heading = SpecificationEvidence(
        node_id="heading-1", part="word/document.xml", path="/w:document/w:body/w:p[1]",
        role="heading1", region="body", text=region_title,
    )
    rule = WritingRule(
        id="rule-1", scope={"region_title": region_title}, property="instruction.text",
        expected=text, evidence_kind="explicit_instruction", confidence="needs_review",
        evidence_node_ids=[instruction.node_id],
    )
    return WritingSpecification(
        source_sha256="source-sha", specification_sha256="spec-sha",
        nodes=[heading, instruction], rules=[rule],
    )


def test_explicit_character_bound_compiles_with_provenance_and_is_checked():
    specification = _specification("摘要正文不少于1,000字")
    template_sections = [TemplateSection(key="abstract", type=SectionType.ABSTRACT, title="摘要")]
    compiled = compile_confirmed_content_requirements(
        specification, template_sections, ["rule-1"], template_id="tpl-1",
        template_name="学校模板", template_version="word.3",
    )
    item = compiled["requirements"][0]
    assert item["checker"] == "abstract.word_count"
    assert item["params"] == {"min": 1000, "count_mode": "characters"}
    assert item["enforce"] == "hard"
    assert item["provenance"]["source_sha256"] == "source-sha"
    assert item["provenance"]["specification_sha256"] == "spec-sha"
    assert item["provenance"]["source_node_ids"] == ["instruction-1"]

    project = WritingProject(
        id="project-1", template_id="tpl-1",
        sections=[ProjectSection(key="abstract", type="abstract", title="摘要",
                                 paragraphs=[ParagraphBlock(id="p1", text="短摘要")])],
    )
    findings, not_checked = check_requirements(project, {"resolved_items": [item]})
    assert len(findings) == 1
    assert findings[0]["requirement_item_id"] == item["id"]
    assert not not_checked


def test_table_instruction_compiles_and_hard_fails_without_table():
    specification = _specification("功能测试部分要用表格的形式进行表示", region_title="系统功能测试")
    template_sections = [TemplateSection(key="testing", type=SectionType.BODY, title="系统功能测试")]
    result = compile_confirmed_content_requirements(
        specification, template_sections, ["rule-1"], template_id="tpl-1",
        template_name="学校模板", template_version="word.3",
    )
    item = result["requirements"][0]
    assert item["checker"] == "section.table_required"
    assert item["params"]["section_key"] == "testing"

    project = WritingProject(
        id="project-1", template_id="tpl-1",
        sections=[ProjectSection(key="testing", type="body", title="系统功能测试",
                                 paragraphs=[ParagraphBlock(id="p1", text="输入、处理、结果")])],
    )
    findings, not_checked = check_requirements(project, {"resolved_items": [item]})
    assert findings[0]["requirement_item_id"] == item["id"]
    assert findings[0]["actual"] == 0
    assert not not_checked

    project.sections[0].paragraphs.append(
        ParagraphBlock(id="table-1", type="table", table=TableBlock(rows=2, cols=2))
    )
    findings, not_checked = check_requirements(project, {"resolved_items": [item]})
    assert findings == []
    assert not not_checked


def test_ambiguous_numbers_unsupported_word_count_and_keywords_units_stay_manual():
    sections = [TemplateSection(key="abstract", type=SectionType.ABSTRACT, title="摘要")]
    for text in (
        "摘要300字，正文不少于1,000字",
        "摘要不少于1,,000字",
        "摘要不少于300词",
        "摘要关键词不少于3字",
    ):
        review = review_content_instructions(_specification(text), sections)
        assert not review["candidates"], text
        assert review["manual_items"], text


def test_keyword_entry_count_compiles_only_for_explicit_item_unit():
    specification = _specification("关键词不少于3个", region_title="关键词")
    sections = [TemplateSection(key="keywords", type=SectionType.KEYWORDS, title="关键词")]
    result = compile_confirmed_content_requirements(
        specification, sections, ["rule-1"], template_id="tpl-1",
        template_name="学校模板", template_version="word.3",
    )
    assert result["requirements"][0]["checker"] == "keywords.count"
    assert result["requirements"][0]["params"] == {"min": 3, "count_mode": "entries"}
