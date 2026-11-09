"""Source-bound removal of instructional text inside template headings."""
import copy
import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.shared import RGBColor
from lxml import etree
from app.core.export.docx_exporter import export_project_to_docx
from app.core.extract.specification import extract_writing_specification
from app.core.extract.xml_utils import NS, qn
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import PaperTemplate
from app.services.docx_output_check import inspect_docx_output
from app.services.import_template import extract_section_tree, import_template_styles
from app.services.source_template_profile import (
    ConfirmWritingProfile, ProfileError, compile_profile, digest, execution_basis,
    flatten, sign_profile, verify_profile,
)
from tests.test_source_template_profile import rewrite


def build_template(tmp_path, *, toc_label=None, red=True):
    doc = Document()
    if toc_label is not None:
        for level in (1, 2, 3):
            doc.styles.add_style(f'TOC {level}', WD_STYLE_TYPE.PARAGRAPH)
        doc.add_heading('目录', 1)
        field = OxmlElement('w:fldSimple')
        field.set(qn('w:instr'), ' TOC \\o "1-3" \\h ')
        doc.add_paragraph()._p.append(field)
        doc.add_paragraph('第1章 系统分析\t1', style='TOC 1')
        doc.add_paragraph('1.1 功能分析\t1', style='TOC 2')
        doc.add_paragraph(toc_label + '\t1', style='TOC 3')
    doc.add_heading('第1章 系统分析', 1)
    doc.add_paragraph('这是普通正文的示例，系统应使用其样式。' * 12)
    doc.add_heading('1.1 功能分析', 2)
    heading = doc.add_heading('1.1.1 数据采集', 3)
    start = OxmlElement('w:bookmarkStart')
    start.set(qn('w:id'), '77'); start.set(qn('w:name'), 'source-bookmark')
    end = OxmlElement('w:bookmarkEnd'); end.set(qn('w:id'), '77')
    heading._p.append(start)
    heading.add_run('（')
    run = heading.add_run('按实际实物实现功能进行描述')
    if red:
        run.font.color.rgb = RGBColor(255, 0, 0)
    heading.add_run('）')
    heading._p.append(end)
    doc.add_paragraph('数据采集正文。' * 20)
    doc.add_heading('1.1.2 模型（实验组）', 3)
    doc.add_paragraph('模型内容。' * 20)
    path = tmp_path / 'source.docx'
    doc.save(path)
    spec = extract_writing_specification(path)
    draft = PaperTemplate(id='heading-instruction', name='母版', source_docx=path.name,
                          sections=extract_section_tree(path), styles=import_template_styles(path)['styles'])
    request = ConfirmWritingProfile(expected_specification_sha256=spec.specification_sha256,
        accept_recommended=True, accept_new_object_defaults=True, remove_template_instructions=True)
    return path, draft, request


def test_template_instruction_removed_from_titles_output_and_independent_check(tmp_path):
    source, draft, request = build_template(tmp_path)
    original = source.read_bytes()
    published = compile_profile(draft, source, request, actor='teacher-test')
    headings = published.source_execution['headings']
    cleaned = next(h for h in headings if h['kind'] == 'heading3')
    assert cleaned['title'] == '1.1.1 数据采集'
    assert next(h for h in headings if '模型' in h['title'])['title'] == '1.1.2 模型（实验组）'
    assert cleaned['instruction_removal']['removed_text'] == '（按实际实物实现功能进行描述）'
    assert '按实际实物' not in next(n.title for n in flatten(published.sections) if n.key == cleaned['key'])
    assert source.read_bytes() == original
    project = WritingProject(id='proj-test', template_id=published.id,
        sections=[ProjectSection(key=n.key, type=n.type, title=n.title,
                  paragraphs=[ParagraphBlock(id=n.key + '-p', text='学生正文')])
                  for n in flatten(published.sections) if n.type.value not in {'cover', 'toc'}])
    output = tmp_path / 'output.docx'
    export_project_to_docx(project, published, str(output), template_path=str(source))
    doc = Document(output)
    assert any(p.text == '1.1.1 数据采集' for p in doc.paragraphs)
    assert any(p.text == '1.1.2 模型（实验组）' for p in doc.paragraphs)
    assert all('按实际实物实现功能进行描述' not in p.text for p in doc.paragraphs)
    assert inspect_docx_output(output.read_bytes(), project, published, source_template_bytes=original)['status'] == 'pass'

    def insert_hint(root):
        paragraph = root.xpath('.//w:p[w:bookmarkStart[@w:name=$name]]',
                               namespaces=NS, name=cleaned['bookmark_name'])[0]
        run = etree.SubElement(paragraph, qn('w:r'))
        etree.SubElement(run, qn('w:t')).text = '（按实际实物实现功能进行描述）'
    corrupted = rewrite(output.read_bytes(), 'word/document.xml', insert_hint)
    report = inspect_docx_output(corrupted, project, published, source_template_bytes=original)
    assert any(c['rule'] == 'master.fixed_body_and_sections' and c['status'] == 'fail'
               for c in report['checks'])


@pytest.mark.parametrize('label', ['1.1.1 数据采集', '1.1.1 数据采集（按实际实物实现功能进行描述）'])
def test_source_toc_with_clean_or_original_label_maps_to_clean_title(tmp_path, label):
    source, draft, request = build_template(tmp_path, toc_label=label)
    published = compile_profile(draft, source, request, actor='teacher-test')
    heading = next(h for h in published.source_execution['headings'] if h['kind'] == 'heading3')
    entry = next(e for e in published.source_execution['toc_entries'] if e['target_key'] == heading['key'])
    assert entry['target_title'] == '1.1.1 数据采集'
    assert entry['source_label'] == label


def test_heading_instruction_requires_removal_confirmation(tmp_path):
    source, draft, request = build_template(tmp_path)
    request.remove_template_instructions = False
    with pytest.raises(ProfileError, match='填写说明'):
        compile_profile(draft, source, request, actor='teacher-test')


def test_same_phrase_without_red_instruction_style_is_preserved(tmp_path):
    source, draft, request = build_template(tmp_path, red=False)
    published = compile_profile(draft, source, request, actor='teacher-test')
    heading = next(h for h in published.source_execution['headings'] if h['kind'] == 'heading3')
    assert heading['title'] == '1.1.1 数据采集（按实际实物实现功能进行描述）'
    assert 'instruction_removal' not in heading


def test_previously_signed_profile_with_unremoved_instruction_is_rejected(tmp_path):
    source, draft, request = build_template(tmp_path)
    published = compile_profile(draft, source, request, actor='teacher-test')
    legacy = copy.deepcopy(published)
    heading = next(h for h in legacy.source_execution['headings'] if h['kind'] == 'heading3')
    original_title = '1.1.1 数据采集（按实际实物实现功能进行描述）'
    heading['title'] = original_title
    heading.pop('instruction_removal')
    next(n for n in flatten(legacy.sections) if n.key == heading['key']).title = original_title
    legacy.source_execution['template_basis_sha256'] = digest(execution_basis(legacy))
    legacy.source_execution = sign_profile(legacy.source_execution)
    with pytest.raises(ProfileError, match='标题.*说明'):
        verify_profile(legacy, source.read_bytes())


def test_derived_manuscript_keeps_student_authored_heading_text(tmp_path):
    from app.services.docx_project_import import import_docx_project
    from app.services.manuscript_template import derive_manuscript_template
    import io

    source, draft, request = build_template(tmp_path)
    request.preserve_manuscript_structure = True
    parent = compile_profile(draft, source, request, actor='teacher-test')
    manuscript = Document()
    manuscript.add_heading('第1章 学生原题', 1)
    manuscript.add_paragraph('学生自己编写的正文。')
    manuscript.add_heading('1.1 学生分析', 2)
    manuscript.add_heading('1.1.1 数据采集（按实际实物实现功能进行描述）', 3)
    buffer = io.BytesIO(); manuscript.save(buffer)
    derived, payload = derive_manuscript_template(
        parent, source, import_docx_project(buffer.getvalue()), actor='student-test')
    title = next(h['title'] for h in derived.source_execution['headings'] if h['kind'] == 'heading3')
    assert title == '1.1.1 数据采集（按实际实物实现功能进行描述）'
    verify_profile(derived, payload)
