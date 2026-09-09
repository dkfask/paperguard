"""模板逆向解析识别测试 (含英文摘要 Abstract 及英文关键词识别)。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.services.import_template import extract_section_tree


def test_extract_section_tree_recognizes_english_abstract_and_excludes_keywords_from_toc():
    doc = docx.Document()
    doc.add_paragraph("摘  要")
    doc.add_paragraph("这是中文摘要内容...")
    doc.add_paragraph("关键词：物联网, 软件架构, 自动化")
    doc.add_paragraph("Abstract")
    doc.add_paragraph("This is english abstract content...")
    doc.add_paragraph("Key Words: IoT, Software Architecture, Automation")
    doc.add_paragraph("目  录")
    doc.add_paragraph("第1章 绪论")
    doc.add_paragraph("参考文献")
    doc.add_paragraph("致  谢")

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        doc.save(tmp_path)
        sections = extract_section_tree(tmp_path)
        types = [s["type"] for s in sections]
        keys = [s["key"] for s in sections]

        assert "abstract" in types
        assert "english_abstract" in types
        # 关键词是摘要正文元信息，不作为独立章节树节点进入目录
        assert "keywords" not in types
        assert "english_keywords" not in types
        assert "toc" in types
        assert "heading1" in types

        abstract_sec = next(s for s in sections if s["type"] == "abstract")
        assert abstract_sec["key"] == "abstract"

        en_abstract_sec = next(s for s in sections if s["type"] == "english_abstract")
        assert en_abstract_sec["key"] == "english_abstract"
        assert en_abstract_sec["title"] == "Abstract"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_extract_section_tree_ignores_pageref_field_result():
    """章节标题后带目录页码域时, 导入标题不应拼入页码。"""
    doc = docx.Document()
    para = doc.add_paragraph(style="Heading 1")
    para.add_run("第1章 绪论")
    para.add_run().add_tab()
    run = para.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGEREF _Toc1 \\h "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    result = OxmlElement("w:t")
    result.text = "12"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instr, separate, result, end):
        run._r.append(element)

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        doc.save(tmp_path)
        sections = extract_section_tree(tmp_path)
        assert sections[0]["title"] == "第1章 绪论"
    finally:
        Path(tmp_path).unlink(missing_ok=True)
