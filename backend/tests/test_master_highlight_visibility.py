"""Visible text must not disappear into the Word highlight behind it."""
from __future__ import annotations

import pytest
from lxml import etree

from app.models.project import InlineTextRun
from app.core.extract.xml_utils import NS, qn
from tests.test_master_content_visibility import _target
from tests.test_source_template_profile import inspect, master, rendered, rewrite


@pytest.mark.parametrize(('foreground', 'highlight', 'allowed'), [
    ('000000', 'black', False),
    (None, 'black', False),
    ('FF0000', 'red', False),
    ('000000', 'yellow', True),
    ('000000', 'red', True),
    ('FFFFFF', 'black', True),
])
@pytest.mark.parametrize('inline', [False, True])
def test_highlight_does_not_hide_student_text(master, tmp_path, foreground, highlight, allowed, inline):
    block = master[2].sections[-1].paragraphs[0]
    if inline:
        block.text = '前文需要强调后文'
        block.inline_runs = [InlineTextRun(text='前文'), InlineTextRun(text='需要强调', highlight=highlight),
                             InlineTextRun(text='后文')]
    else:
        block.highlight = highlight
    data = rendered(master, tmp_path)
    if foreground not in (None, '000000'):
        def set_foreground(root):
            tag = f'pg-block:{block.id}'
            contents = root.xpath('.//w:sdt[w:sdtPr/w:tag/@w:val=$tag]/w:sdtContent',
                                  namespaces=NS, tag=tag)
            assert len(contents) == 1
            for run in contents[0].xpath('.//w:r[w:t]', namespaces=NS):
                properties = run.find(qn('w:rPr'))
                if properties is None:
                    properties = etree.Element(qn('w:rPr'))
                    run.insert(0, properties)
                color = properties.find(qn('w:color'))
                if color is None:
                    color = etree.SubElement(properties, qn('w:color'))
                color.set(qn('w:val'), foreground)
        data = rewrite(data, 'word/document.xml', set_foreground)
    report = inspect(master, data)
    relevant = [item for item in report['checks']
                if item['rule'] == 'master.highlight_color_distinct' and item['object'] == block.id]
    assert relevant, 'independent output inspection must cover the effective text/highlight pair'
    assert all(item['status'] == ('pass' if allowed else 'fail') for item in relevant), relevant
    if not allowed:
        assert report['status'] == 'fail'
    elif foreground in (None, '000000'):
        assert report['status'] == 'pass'


@pytest.mark.parametrize(('direct_color', 'effective_color', 'white_document', 'allowed'), [
    ('auto', 'FFFFFF', True, False),  # direct auto resets inherited white
    (None, 'FFFFFF', True, True),
    (None, None, False, False),  # automatic color needs a known page background
    ('000000', 'FFFFFF', True, False),
    ('FFFFFF', '000000', True, True),
])
def test_direct_color_precedence_and_unknown_background(direct_color, effective_color, white_document, allowed):
    from lxml import etree
    from app.core.extract.xml_utils import qn

    from app.services.master_docx_check import _highlight_is_distinct_from_text

    run = etree.Element(qn('w:r'))
    properties = etree.SubElement(run, qn('w:rPr'))
    if direct_color is not None:
        etree.SubElement(properties, qn('w:color')).set(qn('w:val'), direct_color)
    effective = {'font.highlight': 'black', 'font.color': effective_color}
    assert _highlight_is_distinct_from_text(run, effective, white_document=white_document) is allowed
