"""Student content must not gain unverified direct Word shading."""
from __future__ import annotations

import pytest
from docx import Document
from docx.shared import RGBColor
from lxml import etree

from app.core.extract.xml_utils import qn
from tests.test_master_content_visibility import _add_objects, _target
from tests.test_master_cover_label_visibility import _recompile
from tests.test_source_template_profile import inspect, master, rendered, rewrite


def _black_shading(properties):
    assert properties.find(qn('w:shd')) is None
    element = etree.SubElement(properties, qn('w:shd'))
    element.set(qn('w:val'), 'clear')
    element.set(qn('w:color'), 'auto')
    element.set(qn('w:fill'), '000000')


@pytest.mark.parametrize(('target', 'owner'), [
    ('body', 'run'),
    ('body', 'paragraph'),
    ('field', 'run'),
    ('figure_caption', 'run'),
    ('table_caption', 'paragraph'),
    ('table_cell', 'cell'),
    ('table_cell', 'table'),
])
def test_unrequested_black_shading_is_rejected(master, tmp_path, monkeypatch, target, owner):
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(tmp_path / 'data'))
    source = master[0]
    document = Document(source)
    document.styles['Normal'].font.color.rgb = RGBColor(0, 0, 0)
    document.save(source)
    case = _recompile(master)
    _add_objects(case)
    data = rendered(case, tmp_path)
    assert inspect(case, data)['status'] == 'pass'

    def shade(root):
        run, _ = _target(root, case, target)
        if owner == 'run':
            properties = run.find(qn('w:rPr'))
        else:
            kind, property_name = {
                'paragraph': ('w:p', 'w:pPr'),
                'cell': ('w:tc', 'w:tcPr'),
                'table': ('w:tbl', 'w:tblPr'),
            }[owner]
            parent = next(node for node in run.iterancestors() if node.tag == qn(kind))
            properties = parent.find(qn(property_name))
        assert properties is not None
        _black_shading(properties)

    report = inspect(case, rewrite(data, 'word/document.xml', shade))
    assert report['status'] == 'fail'
    assert any(item['rule'] == 'master.content_direct_shading' and item['status'] == 'fail'
               for item in report['checks'])


def test_shading_on_fixed_template_content_is_preserved(master, tmp_path):
    source = master[0]
    document = Document(source)
    run = document.paragraphs[0].runs[0]
    _black_shading(run._element.get_or_add_rPr())
    document.save(source)
    case = _recompile(master)
    data = rendered(case, tmp_path)
    report = inspect(case, data)
    assert report['status'] == 'pass', [check for check in report['checks'] if check['status'] != 'pass']
