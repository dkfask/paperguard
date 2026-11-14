"""Empty student blocks must not split a heading from its next content."""
from app.core.extract.xml_utils import NS, qn
from app.models.project import ParagraphBlock, ProjectSection
from app.services.master_layout_anchors import leading_blank_heading_blocks
from tests.test_source_template_profile import master, rendered, inspect, rewrite


def test_only_leading_blanks_link_to_following_content_or_child():
    section = ProjectSection(key='a', type='heading1', title='章', paragraphs=[
        ParagraphBlock(id='blank', text=' '), ParagraphBlock(id='body', text='正文'),
        ParagraphBlock(id='later', text='')])
    heading = {'kind': 'heading1'}
    assert leading_blank_heading_blocks(heading, section, None) == {'blank'}
    section.paragraphs = section.paragraphs[:1]
    assert leading_blank_heading_blocks(heading, section, {'kind': 'heading2'}) == {'blank'}
    assert leading_blank_heading_blocks(heading, section, {'kind': 'heading1'}) == set()
    assert leading_blank_heading_blocks(heading, section, None) == set()


def test_rendered_heading_blank_stays_with_child_and_tampering_is_detected(master, tmp_path):
    _, _, project, _ = master
    index = next(i for i, section in enumerate(project.sections[:-1])
                 if section.type == 'heading1' and project.sections[i + 1].type == 'heading2')
    project.sections[index].paragraphs = [ParagraphBlock(id='heading-blank', text='')]
    data = rendered(master, tmp_path)
    report = inspect(master, data)
    assert report['status'] == 'pass'

    def remove_keep(root):
        ppr = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val="pg-block:heading-blank"]//w:pPr',
                         namespaces=NS)[0]
        keep = ppr.find(qn('w:keepNext'))
        assert keep is not None and keep.get(qn('w:val')) == '1'
        ppr.remove(keep)

    damaged = rewrite(data, 'word/document.xml', remove_keep)
    result = inspect(master, damaged)
    assert any(c['rule'] == 'master.effective_format' and c['object'] == 'heading-blank'
               and c['status'] == 'fail' for c in result['checks'])
