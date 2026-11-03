from docx import Document

from app.core.export.docx_exporter import export_project_to_docx
import pytest

from app.api.projects import _parse_par
from app.models.project import BlockType, ParagraphBlock, ProjectSection, ReferenceEntry, TableBlock, WritingProject
from app.models.template import DEFAULT_STYLES, PaperTemplate, SectionType
from app.services.docx_output_check import _expected_text
from app.services.references import format_reference, reference_block_text
from app.services.project_versions import project_content_hash


def test_structured_journal_reference_formats_and_numbers_fields():
    entry = ReferenceEntry(
        kind="journal", authors="张三", title="论文题名", source="计算机学报",
        year="2024", volume="10", issue="2", pages="20-30",
    )
    assert format_reference(entry, 1) == "[1] 张三. 论文题名[J]. 计算机学报, 2024, 10(2): 20-30"


def test_structured_journal_reference_keeps_issue_without_volume():
    entry = ReferenceEntry(
        kind="journal", authors="李四", title="另一项研究", source="示例期刊",
        year="2025", issue="17", pages="1-4",
        original_pasted_text="[2] 李四. 另一项研究[J]. 示例期刊, 2025,(17):1-4.",
    )
    assert format_reference(entry, 2) == "[2] 李四. 另一项研究[J]. 示例期刊, 2025, (17): 1-4"


def test_online_reference_does_not_render_hidden_fields_from_a_previous_type():
    entry = ReferenceEntry(
        kind="online", authors="张三", title="网页标题", url="https://example.test/article",
        organization="旧标准发布机构", year="2024",
    )
    assert format_reference(entry, 1) == "[1] 张三. 网页标题[EB/OL]. https://example.test/article"


def test_save_paragraph_keeps_structured_reference_fields():
    block = _parse_par({"id": "ref-1", "type": "paragraph", "text": "", "reference": {
        "kind": "standard", "title": "参考文献著录规则", "number": "GB/T 7714-2015",
    }}, "project-1")
    assert block.reference is not None
    assert block.reference.number == "GB/T 7714-2015"
    assert format_reference(block.reference, 2) == "[2] 参考文献著录规则[S]. GB/T 7714-2015"


def test_publication_year_confirmation_is_server_owned_and_invalidates_on_identity_change():
    first = _parse_par({"id": "ref-year", "type": "paragraph", "text": "", "reference": {
        "kind": "journal", "authors": "张三", "title": "论文题名", "year": "2024",
        "year_fact": {"value": "2024", "evidence": "期刊首页", "status": "confirmed",
                       "confirmed_by": "forged", "confirmed_at": "forged"},
    }}, "project-1", actor_id="student-1")
    assert first.reference.year_fact.status == "unconfirmed"
    confirmed = _parse_par({"id": first.id, "type": "paragraph", "text": "", "reference": {
        **first.reference.model_dump(mode="json"), "year_confirmation_requested": True,
    }}, "project-1", existing=first, actor_id="student-1")
    assert confirmed.reference.year_fact.status == "confirmed"
    assert confirmed.reference.year_fact.confirmed_by == "student-1"
    changed = _parse_par({"id": first.id, "type": "paragraph", "text": "", "reference": {
        **confirmed.reference.model_dump(mode="json"), "title": "另一篇论文",
    }}, "project-1", existing=confirmed, actor_id="student-1")
    assert changed.reference.year_fact.status == "unconfirmed"
    assert changed.reference.year_fact.confirmed_by == ""


def test_non_reference_text_keeps_its_whitespace_and_old_snapshot_hash():
    block = ParagraphBlock(id="body", text="  保留空格  ")
    assert reference_block_text(block) == "  保留空格  "
    project = WritingProject(id="old", template_id="tpl", sections=[
        ProjectSection(key="body", type="body", title="正文", paragraphs=[block]),
    ])
    old = project.model_dump(mode="json")
    old.pop("reference_number_owners", None)
    for section in old["sections"]:
        for paragraph in section["paragraphs"]:
            paragraph.pop("reference", None)
            paragraph.pop("reference_number", None)
    assert project_content_hash(project) == project_content_hash(old)


def test_existing_structured_reference_hash_is_stable_without_new_paste_metadata():
    project = WritingProject(id="old-ref", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献", paragraphs=[
            ParagraphBlock(id="ref", reference=ReferenceEntry(kind="journal", title="已有题名")),
        ],
    )])
    old = project.model_dump(mode="json")
    old["sections"][0]["paragraphs"][0]["reference"].pop("original_pasted_text")
    assert project_content_hash(project) == project_content_hash(old)


def test_reference_language_defaults_do_not_change_existing_snapshot_hash():
    project = WritingProject(id="old-language", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献", paragraphs=[
            ParagraphBlock(id="ref", reference=ReferenceEntry(kind="journal", title="已有题名")),
        ],
    )])
    old = project.model_dump(mode="json")
    old["sections"][0]["paragraphs"][0]["reference"].pop("language_fact")
    assert project_content_hash(project) == project_content_hash(old)


def test_reference_year_defaults_do_not_change_existing_snapshot_hash():
    project = WritingProject(id="old-year", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献", paragraphs=[
            ParagraphBlock(id="ref", reference=ReferenceEntry(kind="journal", title="已有题名", year="2024")),
        ],
    )])
    old = project.model_dump(mode="json")
    old["sections"][0]["paragraphs"][0]["reference"].pop("year_fact")
    assert project_content_hash(project) == project_content_hash(old)


def test_pasted_original_is_preserved_and_its_number_does_not_claim_an_owner():
    from app.services.references import reconcile_reference_numbers

    old = WritingProject(id="pasted", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献", paragraphs=[ParagraphBlock(id="old", text="[1] 已有文献")],
    )])
    updated = old.model_copy(deep=True)
    updated.sections[0].paragraphs.extend([
        _parse_par({"id": "new-one", "reference": {"kind": "unparsed", "unformatted_text": "[1] 原文甲", "original_pasted_text": "[1] 原文甲"}}),
        _parse_par({"id": "new-two", "reference": {"kind": "unparsed", "unformatted_text": "［1］ 原文乙", "original_pasted_text": "［1］ 原文乙"}}),
    ])
    reconcile_reference_numbers(old, updated)
    assert [block.reference_number for block in updated.sections[0].paragraphs] == [1, 2, 3]
    assert reference_block_text(updated.sections[0].paragraphs[2], 3) == "[3] 原文乙"

    first = updated.sections[0].paragraphs[1]
    edited = _parse_par({"id": first.id, "reference": {
        "kind": "journal", "title": "新题名", "unformatted_text": "修改后的文本", "original_pasted_text": "被覆盖的原文",
    }}, existing=first)
    assert edited.reference.original_pasted_text == "[1] 原文甲"
    assert edited.reference.unformatted_text == "修改后的文本"


def test_unparsed_pasted_references_block_preflight_but_not_draft_completeness_of_other_entries():
    from app.services.project_validation import validate_project_completeness

    template = PaperTemplate(id="tpl", name="模板", sections=[
        {"key": "references", "type": "references", "title": "参考文献"},
    ])
    project = WritingProject(id="project", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献", paragraphs=[ParagraphBlock(
            id="pending", reference=ReferenceEntry(kind="unparsed", unformatted_text="[1] 原文", original_pasted_text="[1] 原文"),
        )],
    )])
    assert any("第 1 条" in issue for issue in validate_project_completeness(project, template))
    project.sections[0].paragraphs[0].reference.kind = "journal"
    assert validate_project_completeness(project, template)
    project.sections[0].paragraphs[0].reference.unformatted_text = ""
    assert validate_project_completeness(project, template)
    project.sections[0].paragraphs[0].reference.title = "题名"
    assert any("自动核验" in issue for issue in validate_project_completeness(project, template))
    project.sections[0].paragraphs[0].reference.authors = "张三"
    project.sections[0].paragraphs[0].reference.source = "期刊"
    project.sections[0].paragraphs[0].reference.year = "2024"
    assert any("自动核验" in issue for issue in validate_project_completeness(project, template))


@pytest.mark.parametrize("original, facts", [
    ("[8] 张三. 研究题名[J]. 示例期刊, 2024, 12(3): 1-9.",
     {"authors": "张三", "title": "研究题名", "source": "示例期刊", "year": "2024", "volume": "12", "issue": "3", "pages": "1-9"}),
    ("［3］ LI X, WANG Y. A research title[J]. Journal of A, B, 2025,(17): 1-4. DOI: 10.1234/abc.5678.",
     {"authors": "LI X, WANG Y", "title": "A research title", "source": "Journal of A, B", "year": "2025", "issue": "17", "pages": "1-4", "doi": "10.1234/abc.5678"}),
    ("[2] 作者. 网络研究[J]. 示例期刊, 2025. DOI: 10.1234/test.1",
     {"authors": "作者", "title": "网络研究", "source": "示例期刊", "year": "2025", "doi": "10.1234/test.1"}),
])
def test_complete_pasted_journal_is_independently_verified_and_exportable(original, facts):
    from app.services.project_validation import validate_project_completeness

    entry = ReferenceEntry(kind="journal", original_pasted_text=original, unformatted_text=original, **facts)
    project = WritingProject(id="paper", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献",
        paragraphs=[ParagraphBlock(id="ref", reference=entry, reference_number=1)],
    )])
    assert validate_project_completeness(project, PaperTemplate(id="tpl", name="模板")) == []
    assert entry.original_pasted_text == original
    assert format_reference(entry, 1).startswith("[1] ")


@pytest.mark.parametrize("kind, original, facts", [
    ("book", "[1] 张三. 示例书名[M]. 成都: 示例出版社, 2024: 1-9.",
     {"authors": "张三", "title": "示例书名", "place": "成都", "publisher": "示例出版社", "year": "2024", "pages": "1-9"}),
    ("thesis", "[1] 李四. 示例论文[D]. 成都: 示例大学, 2023.",
     {"authors": "李四", "title": "示例论文", "place": "成都", "institution": "示例大学", "year": "2023"}),
])
def test_complete_pasted_book_and_thesis_preserve_source_facts(kind, original, facts):
    from app.services.references import validate_reference_facts

    entry = ReferenceEntry(kind=kind, original_pasted_text=original, **facts)
    assert validate_reference_facts(entry) == []


def test_reference_gate_reports_missing_facts_source_mismatch_and_lost_fields():
    from app.services.references import validate_reference_facts

    entry = ReferenceEntry(kind="journal", title="研究", original_pasted_text="张三. 研究[J]. 期刊, 2025: 1-4.")
    problems = validate_reference_facts(entry)
    assert any("缺少作者" in item for item in problems)
    assert any("缺少来源" in item for item in problems)
    assert any("缺少年份" in item for item in problems)
    assert any("页码与粘贴原文不一致" in item for item in problems)
    entry.authors, entry.source, entry.year, entry.pages = "张三", "期刊", "2025", "1-4"
    assert validate_reference_facts(entry) == []
    entry.pages = "1-3"
    assert any("页码与粘贴原文不一致" in item for item in validate_reference_facts(entry))
    entry.pages = "1-4"
    entry.publisher = "不应丢弃的出版社"
    assert any("出版社未能保留" in item for item in validate_reference_facts(entry))
    entry.publisher = ""
    entry.collection = "期刊"  # Same text does not make a silently omitted field preserved.
    assert any("文集未能保留" in item for item in validate_reference_facts(entry))


def test_unsupported_pasted_remainder_cannot_be_hidden_by_client_fields():
    from app.services.references import validate_reference_facts

    entry = ReferenceEntry(kind="journal", authors="张三", title="研究", source="期刊", year="2025",
                           original_pasted_text="张三. 研究[J]. 期刊, 2025. 未识别的事实")
    assert any("不能完成自动核验" in item for item in validate_reference_facts(entry))
    entry.original_pasted_text = "李四. 研究[J]. 期刊, 2025."
    assert any("作者与粘贴原文不一致" in item for item in validate_reference_facts(entry))


@pytest.mark.parametrize("original, extra", [
    ("张三. 示例[J]", {"source": "示例期刊", "year": "2024"}),
    ("张三. 示例[J]. 示例期刊.", {"source": "示例期刊", "year": "2024"}),
])
def test_missing_pasted_facts_can_be_added_without_discarding_the_original(original, extra):
    from app.services.references import validate_reference_facts

    entry = ReferenceEntry(kind="journal", authors="张三", title="示例", original_pasted_text=original)
    assert any("缺少年份" in item for item in validate_reference_facts(entry))
    for key, value in extra.items():
        setattr(entry, key, value)
    assert validate_reference_facts(entry) == []
    assert entry.original_pasted_text == original


def test_unparsed_source_can_be_recovered_by_adding_all_of_its_facts():
    from app.services.references import validate_reference_facts

    original = "[1] A. Smith. An example[J]. Journal, 2024: 1-4."
    entry = ReferenceEntry(kind="journal", authors="A. Smith", title="An example", source="Journal", year="2024",
                           original_pasted_text=original)
    assert any("不能完成自动核验" in item for item in validate_reference_facts(entry))
    entry.pages = "1-4"
    assert validate_reference_facts(entry) == []
    assert entry.original_pasted_text == original
    entry.pages = "1-3"
    assert any("不能完成自动核验" in item for item in validate_reference_facts(entry))
    entry.pages = "1-4"
    entry.kind = "book"
    assert any("不能完成自动核验" in item for item in validate_reference_facts(entry))


def test_fallback_does_not_ignore_meaningful_symbols_or_swapped_author_and_title():
    from app.services.references import validate_reference_facts

    entry = ReferenceEntry(kind="journal", authors="A. Smith", title="C", source="Journal", year="2024",
                           original_pasted_text="A. Smith. C++[J]. Journal, 2024.")
    assert any("不能完成自动核验" in item for item in validate_reference_facts(entry))
    entry.title = "C++"
    assert validate_reference_facts(entry) == []
    entry.authors, entry.title = "C++", "A. Smith"
    assert any("不能完成自动核验" in item for item in validate_reference_facts(entry))
    entry.authors, entry.title = "A. Smith", "C++"
    entry.original_pasted_text = "A. Smith. C++[J][J]. Journal, 2024."
    assert any("不能完成自动核验" in item for item in validate_reference_facts(entry))
    entry.original_pasted_text = "A. Smith. C++[J]. Journal, 202X."
    entry.year = "202X"
    assert any("四位年份" in item for item in validate_reference_facts(entry))


def test_reference_numbers_survive_reordering_deletion_and_legacy_collisions():
    from app.services.references import ReferenceNumberError, reconcile_reference_numbers

    old = WritingProject(id="numbered", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献", paragraphs=[
            ParagraphBlock(id="one", text="[1] 甲"), ParagraphBlock(id="two", text="[2] 乙"),
        ],
    )])
    first = old.model_copy(deep=True)
    reconcile_reference_numbers(old, first)
    assert [b.reference_number for b in first.sections[0].paragraphs] == [1, 2]

    changed = first.model_copy(deep=True)
    changed.sections[0].paragraphs = [changed.sections[0].paragraphs[1], ParagraphBlock(id="three", text="丙")]
    reconcile_reference_numbers(first, changed)
    assert [b.reference_number for b in changed.sections[0].paragraphs] == [2, 3]
    assert changed.reference_number_owners == {"1": "one", "2": "two", "3": "three"}

    duplicate = changed.model_copy(deep=True)
    duplicate.sections[0].paragraphs.append(ParagraphBlock(id="two", text="重复 ID"))
    with pytest.raises(ReferenceNumberError):
        reconcile_reference_numbers(changed, duplicate)
    collision = changed.model_copy(deep=True)
    collision.sections[0].paragraphs.append(ParagraphBlock(id="four", text="[1] 不同文献"))
    with pytest.raises(ReferenceNumberError):
        reconcile_reference_numbers(changed, collision)


def test_legacy_reference_prefix_is_renumbered_and_blank_entries_are_skipped():
    first = ParagraphBlock(id="legacy", text="[9] 旧作者. 旧文献. 2020")
    second = ParagraphBlock(id="blank", reference=ReferenceEntry(kind="journal"))
    third = ParagraphBlock(id="structured", reference=ReferenceEntry(
        kind="book", authors="李四", title="新书", place="成都", publisher="东软出版社", year="2025",
    ))
    assert reference_block_text(first, 1) == "[1] 旧作者. 旧文献. 2020"
    assert reference_block_text(second, 2) == ""
    assert reference_block_text(third, 2) == "[2] 李四. 新书[M]. 成都: 东软出版社，2025"


def test_output_check_reference_numbering_ignores_non_reference_blocks():
    section = ProjectSection(
        key="references", type="references", title="参考文献",
        paragraphs=[
            ParagraphBlock(id="first", reference=ReferenceEntry(kind="journal", authors="张三", title="甲")),
            ParagraphBlock(id="table", type="table", table=TableBlock(rows=1, cols=1, cells=[["表格"]])),
            ParagraphBlock(id="second", reference=ReferenceEntry(kind="journal", authors="李四", title="乙")),
        ],
    )
    actual = [text for text, section_key, _ in _expected_text(type("Project", (), {"sections": [section]})()) if section_key == "references" and text.startswith("[")]
    assert actual == ["[1] 张三. 甲[J]", "[2] 李四. 乙[J]"]


def test_docx_export_preserves_structured_and_legacy_citation_numbers(tmp_path):
    template = PaperTemplate(
        id="reference-template", name="参考文献测试", styles=DEFAULT_STYLES,
        sections=[{"key": "references", "type": SectionType.REFERENCES, "title": "参考文献"}],
    )
    project = WritingProject(
        id="reference-project", template_id=template.id, template_name=template.name,
        sections=[ProjectSection(
            key="references", type="references", title="参考文献",
            paragraphs=[
                ParagraphBlock(id="ref-1", reference=ReferenceEntry(kind="journal", authors="张三", title="论文甲", source="期刊", year="2024")),
                ParagraphBlock(id="table", type=BlockType.TABLE, table=TableBlock(rows=1, cols=1, cells=[["表格"]])),
                ParagraphBlock(id="ref-2", text="[8] 李四. 论文乙. 2025."),
            ],
        )],
    )
    output_path = tmp_path / "references.docx"

    export_project_to_docx(project, template, str(output_path))

    paragraphs = [paragraph.text for paragraph in Document(output_path).paragraphs]
    assert "[1] 张三. 论文甲[J]. 期刊, 2024" in paragraphs
    assert "[8] 李四. 论文乙. 2025." in paragraphs


def test_numbering_does_not_remove_bracketed_numbers_inside_reference_text():
    text = "作者. 文献引用 [8] 相关研究."
    block = ParagraphBlock(id="legacy", text=text)
    assert reference_block_text(block, 1) == f"[1] {text}"


@pytest.mark.parametrize("prefix", ["[8]", "[ 8 ]", "［8］", " ［ 8 ］ "])
@pytest.mark.parametrize("reference_entry", [False, True])
def test_legacy_number_survives_export_save_and_reload(tmp_path, prefix, reference_entry):
    from app.services.references import reconcile_reference_numbers

    text = f"{prefix} 李四. 论文乙. 2025."
    block = ParagraphBlock(id="legacy", text=text)
    if reference_entry:
        block.reference = ReferenceEntry(kind="unparsed", unformatted_text=text)
    project = WritingProject(id="legacy-project", template_id="tpl", sections=[
        ProjectSection(key="references", type="references", title="参考文献", paragraphs=[block]),
    ])
    template = PaperTemplate(id="tpl", name="模板", styles=DEFAULT_STYLES,
        sections=[{"key": "references", "type": SectionType.REFERENCES, "title": "参考文献"}])

    def assert_export_has_original_number(snapshot, name):
        output = tmp_path / name
        export_project_to_docx(snapshot, template, str(output))
        assert "[8] 李四. 论文乙. 2025." in [paragraph.text for paragraph in Document(output).paragraphs]

    assert_export_has_original_number(project, "before.docx")
    saved = project.model_copy(deep=True)
    reconcile_reference_numbers(project, saved)
    reloaded = WritingProject.model_validate_json(saved.model_dump_json())
    assert reloaded.sections[0].paragraphs[0].reference_number == 8
    assert reloaded.reference_number_owners == {"8": "legacy"}
    assert_export_has_original_number(reloaded, "after.docx")


@pytest.mark.parametrize("prefix", ["[ 8 ]", "［8］"])
def test_normalized_legacy_number_conflicts_are_rejected(prefix):
    from app.services.references import ReferenceNumberError, validate_reference_numbers

    for paragraphs in (
        [ParagraphBlock(id="a", text=f"{prefix} 甲"), ParagraphBlock(id="b", text="[8] 乙")],
        [ParagraphBlock(id="a", reference_number=7, text=f"{prefix} 甲")],
    ):
        project = WritingProject(id="conflict", template_id="tpl", sections=[
            ProjectSection(key="references", type="references", title="参考文献", paragraphs=paragraphs),
        ])
        with pytest.raises(ReferenceNumberError):
            validate_reference_numbers(project)


def test_legacy_positional_numbers_stay_with_their_owners_when_first_saved():
    from app.services.references import reconcile_reference_numbers
    old = WritingProject(id="legacy", template_id="tpl", sections=[ProjectSection(
        key="references", type="references", title="参考文献", paragraphs=[
            ParagraphBlock(id="one", text="甲"),
            ParagraphBlock(id="blank", text=""),
            ParagraphBlock(id="two", text="[8] 乙"),
        ],
    )])
    updated = old.model_copy(deep=True)
    updated.sections[0].paragraphs.reverse()
    reconcile_reference_numbers(old, updated)
    assert {b.id: b.reference_number for b in updated.sections[0].paragraphs} == {"two": 8, "blank": None, "one": 1}


@pytest.mark.parametrize("owners, blocks", [
    ({}, [ParagraphBlock(id="one", text="甲"), ParagraphBlock(id="two", text="[1] 乙")]),
    ({"1": "deleted"}, [ParagraphBlock(id="one", text="甲")]),
    ({}, [ParagraphBlock(id="one", reference_number=2, text="[1] 甲")]),
])
@pytest.mark.parametrize("master", [False, True])
def test_ambiguous_legacy_numbering_blocks_every_renderer_before_producing_a_file(tmp_path, owners, blocks, master):
    from app.services.references import ReferenceNumberError, validate_reference_numbers
    project = WritingProject(id="bad", template_id="tpl", reference_number_owners=owners, sections=[
        ProjectSection(key="references", type="references", title="参考文献", paragraphs=blocks),
    ])
    before = project.model_dump(mode="json")
    template = PaperTemplate(id="tpl", name="模板", styles=DEFAULT_STYLES)
    if master:
        template = template.model_copy(update={"source_execution": {"test": "must not render"}})
    with pytest.raises(ReferenceNumberError):
        validate_reference_numbers(project)
    with pytest.raises(ReferenceNumberError):
        export_project_to_docx(project, template, str(tmp_path / "bad.docx"))
    assert not (tmp_path / "bad.docx").exists()
    assert project.model_dump(mode="json") == before
