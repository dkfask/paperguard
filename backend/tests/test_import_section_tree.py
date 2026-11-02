"""模板逆向解析识别测试 (含英文摘要 Abstract 及英文关键词识别)。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.services.import_template import extract_document_structure, extract_section_tree


def test_numeric_third_level_headings_stay_under_their_second_level_parent(tmp_path):
    for styled in (True, False):
        document = docx.Document()
        for text, level in [("第3章 系统分析", 1), ("3.1 系统功能分析", 2),
                            ("3.1.1 数据采集功能分析", 3), ("3.1.2 数据查询功能分析", 3),
                            ("3.2 物联网系统流程分析", 2), ("3.2.1 系统流程图", 3)]:
            document.add_paragraph(text, style=f"Heading {level}" if styled else None)
        path = tmp_path / f"chapter-tree-{styled}.docx"
        document.save(path)
        roots = extract_section_tree(path)
        assert len(roots) == 1
        chapter = roots[0]
        assert chapter["type"] == "heading1"
        assert [n["title"] for n in chapter["children"]] == ["3.1 系统功能分析", "3.2 物联网系统流程分析"]
        assert [n["type"] for n in chapter["children"]] == ["heading2", "heading2"]
        assert [n["title"] for n in chapter["children"][0]["children"]] == ["3.1.1 数据采集功能分析", "3.1.2 数据查询功能分析"]
        assert all(n["type"] == "heading3" for p in chapter["children"] for n in p["children"])
        assert chapter["children"][1]["children"][0]["title"] == "3.2.1 系统流程图"


def test_extract_section_tree_recognizes_english_abstract_and_excludes_keywords_from_toc():
    doc = docx.Document()
    doc.add_paragraph("摘  要")
    doc.add_paragraph("这是中文摘要内容...")
    doc.add_paragraph("关键词：物联网, 软件架构, 自动化", style="Heading 1")
    doc.add_paragraph("Abstract", style="Heading 1")
    doc.add_paragraph("This is english abstract content...")
    doc.add_paragraph("Key Words: IoT, Software Architecture, Automation", style="Heading 1")
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
        assert [s['title'] for s in sections if s['type'].startswith('heading')] == ['第1章 绪论']

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


def test_extracts_chinese_form_sections_and_table_structure(tmp_path):
    doc = docx.Document()
    doc.add_paragraph("研究会课题结题报告", style="Heading 1")
    doc.add_paragraph("课题名称")
    doc.add_paragraph("一、课题信息表")
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "课题名称"
    table.cell(0, 1).merge(table.cell(0, 2)).text = ""
    table.cell(1, 0).text = "负责人姓名"
    table.cell(1, 1).text = ""
    table.cell(1, 2).text = "所在单位"
    doc.add_paragraph("二、课题完成情况表")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "1. 预期成果及完成情况"

    path = tmp_path / "application-form.docx"
    doc.save(path)

    sections = extract_section_tree(path)
    assert [section["title"] for section in sections] == ["一、课题信息表", "二、课题完成情况表"]

    structure = extract_document_structure(path)
    assert structure["table_count"] == 2
    assert structure["tables"][0]["rows"] == 2
    assert structure["tables"][0]["has_merged_cells"] is True
    assert structure["tables"][0]["section_title"] == "一、课题信息表"
    assert structure["tables"][0]["non_empty_cells"] == 3


def test_explicit_third_level_number_corrects_heading_style_but_not_decimal_prose(tmp_path):
    from app.core.extract.specification import extract_writing_specification

    document = docx.Document()
    document.add_heading('第5章 系统实现', level=1)
    document.add_heading('5.1 硬件系统实现', level=2)
    document.add_heading('5.1.1 温湿度模块实现', level=2)
    document.add_paragraph('3.14 ℃为本次测试的测量值。')
    document.add_heading('5.2 软件系统实现', level=2)
    document.add_heading('5.2.1 环境数据显示功能', level=2)
    path = tmp_path / 'heading-style-conflict.docx'
    document.save(path)

    sections = extract_section_tree(path)
    assert [child['title'] for child in sections[0]['children']] == ['5.1 硬件系统实现', '5.2 软件系统实现']
    assert [child['children'][0]['type'] for child in sections[0]['children']] == ['heading3', 'heading3']
    assert [child['children'][0]['title'] for child in sections[0]['children']] == [
        '5.1.1 温湿度模块实现', '5.2.1 环境数据显示功能'
    ]

    spec = extract_writing_specification(path)
    roles = {node.text: node.role for node in spec.nodes}
    assert roles['5.1.1 温湿度模块实现'] == 'heading3'
    assert roles['5.2.1 环境数据显示功能'] == 'heading3'
    assert roles['3.14 ℃为本次测试的测量值。'] == 'body'
