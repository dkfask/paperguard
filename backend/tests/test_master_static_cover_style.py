"""Static cover text must keep its effective style, including inheritance."""

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt

from app.core.extract.xml_utils import NS, qn
from tests.test_master_cover_label_visibility import _recompile
from tests.test_source_template_profile import inspect, master, rendered, rewrite


@pytest.mark.parametrize("changed", ["font", "size", "spacing"])
@pytest.mark.parametrize("where", ["direct", "inherited"])
def test_static_cover_style_cannot_change(master, tmp_path, changed, where):
    source = master[0]
    document = Document(source)
    paragraph = document.paragraphs[0]
    if where == "inherited":
        style = document.styles.add_style("Static Cover Test", WD_STYLE_TYPE.PARAGRAPH)
        style.font.name = "Times New Roman"
        style.font.size = Pt(26)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
        style.paragraph_format.line_spacing = 1.5
        paragraph.style = style
        style_id = style.style_id
    else:
        paragraph.runs[0].font.name = "Times New Roman"
        paragraph.runs[0].font.size = Pt(26)
        paragraph.runs[0]._r.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
        paragraph.paragraph_format.line_spacing = 1.5
    document.save(source)
    case = _recompile(master)
    data = rendered(case, tmp_path)
    baseline = inspect(case, data)
    assert baseline["status"] == "pass", [item for item in baseline["checks"] if item["status"] != "pass"]

    def alter(root):
        if where == "inherited":
            nodes = root.xpath("./w:style[@w:styleId=$id]", namespaces=NS, id=style_id)
            parent = nodes[0]
        else:
            nodes = root.xpath("./w:body/w:p[w:r/w:t='毕业设计报告']", namespaces=NS)
            parent = nodes[0]
        assert len(nodes) == 1
        run_pr = (parent.find(qn("w:rPr")) if where == "inherited"
                  else parent.find(f"{qn('w:r')}/{qn('w:rPr')}"))
        if changed == "font":
            run_pr.find(qn("w:rFonts")).set(qn("w:eastAsia"), "宋体")
        elif changed == "size":
            run_pr.find(qn("w:sz")).set(qn("w:val"), "48")
        else:
            parent.find(f"{qn('w:pPr')}/{qn('w:spacing')}").set(qn("w:line"), "240")

    report = inspect(case, rewrite(data, "word/styles.xml" if where == "inherited" else "word/document.xml", alter))
    assert report["status"] == "fail"
    rule = "master.indent_baseline_styles" if where == "inherited" else "master.fixed_body_and_sections"
    assert any(item["rule"] == rule and item["status"] == "fail" for item in report["checks"])
