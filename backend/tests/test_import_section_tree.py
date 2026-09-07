"""模板逆向解析识别测试 (含英文摘要 Abstract 及英文关键词识别)。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import docx

from app.services.import_template import extract_section_tree


def test_extract_section_tree_recognizes_english_abstract_and_keywords():
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
        assert "keywords" in types
        assert "english_keywords" in types
        assert "toc" in types
        assert "heading1" in types

        abstract_sec = next(s for s in sections if s["type"] == "abstract")
        assert abstract_sec["key"] == "abstract"

        en_abstract_sec = next(s for s in sections if s["type"] == "english_abstract")
        assert en_abstract_sec["key"] == "english_abstract"
        assert en_abstract_sec["title"] == "Abstract"

        en_keywords_sec = next(s for s in sections if s["type"] == "english_keywords")
        assert en_keywords_sec["key"] == "english_keywords"
    finally:
        Path(tmp_path).unlink(missing_ok=True)
