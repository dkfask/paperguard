from app.core.loaders.docx_loader import load_docx
from app.models.template import PaperTemplate, TextStyle
from app.services.template_compare import compare_document_to_template


def test_template_compare_reports_typed_style_difference(tmp_path):
    from docx import Document
    from docx.shared import Pt

    path = tmp_path / "paper.docx"
    doc = Document()
    p = doc.add_paragraph("测试段落")
    p.style = doc.styles["Normal"]
    p.runs[0].font.size = Pt(10)
    doc.save(path)

    template = PaperTemplate(
        id="uploaded",
        name="上传模板",
        styles={"body": TextStyle(size_pt=12)},
        sections=[],
    )
    findings, context = compare_document_to_template(load_docx(path), template)
    assert context.style_count == 1
    size_findings = [f for f in findings if f.rule_id.endswith("run.font_size_pt")]
    assert size_findings
    assert size_findings[0].actual == 10.0
    assert size_findings[0].expected == 12.0


def test_template_compare_returns_clean_for_matching_paragraph(tmp_path):
    from docx import Document
    from docx.shared import Pt

    path = tmp_path / "paper.docx"
    doc = Document()
    p = doc.add_paragraph("测试段落")
    p.style = doc.styles["Normal"]
    p.paragraph_format.line_spacing = 1.5
    p.runs[0].font.size = Pt(12)
    doc.save(path)

    template = PaperTemplate(
        id="uploaded",
        name="上传模板",
        styles={"body": TextStyle(size_pt=12, line_spacing=1.5)},
        sections=[],
    )
    findings, _ = compare_document_to_template(load_docx(path), template)
    assert not [f for f in findings if f.rule_id.endswith(("run.font_size_pt", "paragraph.line_spacing"))]
