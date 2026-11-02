"""Visible text must not disappear into the Word highlight behind it."""
from __future__ import annotations

import pytest
from docx import Document
from docx.shared import RGBColor

from app.models.project import InlineTextRun
from tests.test_master_cover_label_visibility import _recompile
from tests.test_source_template_profile import inspect, master, rendered


def _with_font_color(master, value):
    source = master[0]
    document = Document(source)
    if value is None:
        document.styles['Normal'].font.color.rgb = None
    else:
        document.styles['Normal'].font.color.rgb = RGBColor.from_string(value)
    document.save(source)
    return _recompile(master)


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
    case = _with_font_color(master, foreground)
    block = case[2].sections[-1].paragraphs[0]
    if inline:
        block.text = '前文需要强调后文'
        block.inline_runs = [InlineTextRun(text='前文'), InlineTextRun(text='需要强调', highlight=highlight),
                             InlineTextRun(text='后文')]
    else:
        block.highlight = highlight
    report = inspect(case, rendered(case, tmp_path))
    relevant = [item for item in report['checks']
                if item['rule'] == 'master.highlight_color_distinct' and item['object'] == block.id]
    assert relevant, 'independent output inspection must cover the effective text/highlight pair'
    assert all(item['status'] == ('pass' if allowed else 'fail') for item in relevant)
    assert (report['status'] == 'pass') is allowed, [item for item in report['checks'] if item['status'] != 'pass']


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
