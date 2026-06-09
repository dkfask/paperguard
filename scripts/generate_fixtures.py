"""Sprint 2.5 fixture 生成脚本 — 5 个自制 docx 模板.

用 python-docx 程序生成 5 个模板，覆盖不同格式风格。**零版权风险**（全自制 + 国标公开内容）。

每个 fixture 含：
- 标题层级（1-3 级）
- 正文段落（含中英文字体、字号、行距、首行缩进、段距）
- 表格（含标题）
- 页眉页脚
- 章节属性（页边距、纸张大小）

用法：
    python scripts/generate_fixtures.py

输出：tests/fixtures/templates/{minimal,gb7713-official,tsinghua-style,zhejiang-style,wuhan-style}.docx

来源声明（重要 — 法律安全）：
- minimal.docx：完全自制
- gb7713-official.docx：GB/T 7713.1—2006 公开国标格式（公开标准内容，可引用）
- tsinghua-style.docx / zhejiang-style.docx / wuhan-style.docx：**自制模拟样式**，
  非来自任何真实学校官方模板；仅为 PaperGuard 单元测试 fixture 用途
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


_OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "templates"


def _set_east_asia_font(run, font_name: str) -> None:
    """python-docx 不暴露 eastAsia 字体，用 lxml 直接设 <w:rFonts w:eastAsia>。"""
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), font_name)


def _add_styled_para(doc, text: str, *, font_name: str = "Times New Roman",
                     east_asia: str = "宋体", size_pt: float = 12.0,
                     line_spacing: float = 1.5, first_line_indent_cm: float = 0.74,
                     bold: bool = False, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY):
    """添加一个带完整格式的段落。"""
    p = doc.add_paragraph()
    p.alignment = alignment
    pf = p.paragraph_format
    pf.line_spacing = line_spacing
    pf.first_line_indent = Cm(first_line_indent_cm)
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    run = p.add_run(text)
    run.font.name = font_name
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    _set_east_asia_font(run, east_asia)
    return p


def _add_heading(doc, text: str, level: int, *, east_asia: str = "黑体",
                 size_pt: float = 18.0):
    """添加标题段，含中文字体（黑体）和字号控制。"""
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(size_pt)
        _set_east_asia_font(run, east_asia)
    return h


def _set_section_margins(section, top_cm=2.54, bottom_cm=2.54,
                         left_cm=3.17, right_cm=3.17):
    section.top_margin = Cm(top_cm)
    section.bottom_margin = Cm(bottom_cm)
    section.left_margin = Cm(left_cm)
    section.right_margin = Cm(right_cm)


# ---------------------------------------------------------------------------
# Fixture 1: minimal — 最小可用模板（标题 + 正文 + 页眉页脚）
# ---------------------------------------------------------------------------

def make_minimal() -> Document:
    doc = Document()
    _set_section_margins(doc.sections[0])
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = "PaperGuard 最小模板 - 页眉"
    sec.footer.paragraphs[0].text = "第 1 页"
    _add_heading(doc, "第一章 引言", level=1, size_pt=18.0)
    for i in range(3):
        _add_styled_para(doc, f"这是第 {i + 1} 段正文，本文研究 PaperGuard 论文格式校验系统。")
    return doc


# ---------------------------------------------------------------------------
# Fixture 2: GB/T 7713.1—2006 国标官方格式（公开标准内容）
# ---------------------------------------------------------------------------

def make_gb7713_official() -> Document:
    doc = Document()
    _set_section_margins(doc.sections[0])  # 国标边距 2.54/3.17

    # 封面（模拟）
    _add_heading(doc, "学位论文（GB/T 7713.1-2006）", level=0, size_pt=22.0)
    _add_styled_para(doc, "作者：测试 / 学号：202600001 / 日期：2026-06-09",
                     alignment=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()

    # 摘要
    _add_heading(doc, "摘要", level=1, size_pt=18.0)
    _add_styled_para(doc, "本文按照 GB/T 7713.1—2006《学位论文编写规则》要求编写格式。"
                          "中文摘要不超过 300 字。")
    _add_styled_para(doc, "关键词：论文格式；GB/T 7713；规范检查；YAML 规则")

    # 正文
    _add_heading(doc, "第 1 章 引言", level=1, size_pt=18.0)
    _add_heading(doc, "1.1 研究背景", level=2, size_pt=15.0)
    _add_styled_para(doc, "学位论文格式直接影响答辩与归档。要求：行距 1.5 倍、字号小四（12pt）、"
                          "中文宋体、首行缩进 2 字符。")
    _add_heading(doc, "1.2 研究意义", level=2, size_pt=15.0)
    _add_styled_para(doc, "本文自动化处理 GB/T 7713 规则，减轻教务负担。")

    # 表格
    _add_heading(doc, "1.3 相关工作", level=2, size_pt=15.0)
    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = "工具"
    table.rows[0].cells[1].text = "支持"
    table.rows[1].cells[0].text = "PaperGuard"
    table.rows[1].cells[1].text = "GB/T 7713 + 7714"

    # 参考文献
    _add_heading(doc, "参考文献", level=1, size_pt=18.0)
    _add_styled_para(doc, "[1] GB/T 7713.1-2006. 学位论文编写规则[S]. 北京：中国标准出版社, 2006.",
                     first_line_indent_cm=0.0)
    return doc


# ---------------------------------------------------------------------------
# Fixture 3-5: 模拟学校风格（自制，非真实模板）
# ---------------------------------------------------------------------------

def make_tsinghua_style() -> Document:
    """模拟清华风格（自制 — 非真实清华模板）：中文宋体小四 / 1.5 行距 / 黑体标题。"""
    doc = Document()
    _set_section_margins(doc.sections[0], top_cm=2.5, bottom_cm=2.5, left_cm=2.5, right_cm=2.0)
    _add_heading(doc, "硕士学位论文（模拟清华风格 — 自制 fixture）", level=0, size_pt=22.0)
    _add_heading(doc, "第一章 绪论", level=1, size_pt=16.0)
    _add_heading(doc, "1.1 选题背景", level=2, size_pt=14.0)
    _add_styled_para(doc, "本文研究模板自动抽取与规则生成方法。")
    _add_styled_para(doc, "现有方法对中文字体（如宋体）支持不足。")
    return doc


def make_zhejiang_style() -> Document:
    """模拟浙大风格（自制 — 非真实浙大模板）：仿宋正文 / 黑体标题 / 页边距偏窄。"""
    doc = Document()
    _set_section_margins(doc.sections[0], top_cm=2.0, bottom_cm=2.0, left_cm=2.5, right_cm=2.5)
    _add_heading(doc, "博士学位论文（模拟浙大风格 — 自制 fixture）", level=0, size_pt=20.0)
    _add_heading(doc, "第一章 引言", level=1, size_pt=18.0, east_asia="黑体")
    _add_styled_para(doc, "本文研究 PaperGuard YAML 引擎在浙大风格论文上的适配。",
                     east_asia="仿宋")
    _add_styled_para(doc, "实验表明，自动抽取的 YAML pack 与手写规则等价。",
                     east_asia="仿宋")
    return doc


def make_wuhan_style() -> Document:
    """模拟武大风格（自制 — 非真实武大模板）：楷体正文 / 边距 3cm。"""
    doc = Document()
    _set_section_margins(doc.sections[0], top_cm=3.0, bottom_cm=3.0, left_cm=3.0, right_cm=3.0)
    _add_heading(doc, "学位论文（模拟武大风格 — 自制 fixture）", level=0, size_pt=22.0)
    _add_heading(doc, "第一章 概述", level=1, size_pt=18.0, east_asia="黑体")
    _add_styled_para(doc, "本文研究 PaperGuard 抽取模块在武大风格论文上的覆盖率。",
                     east_asia="楷体_GB2312")
    table = doc.add_table(rows=2, cols=3)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = "维度"
    table.rows[0].cells[1].text = "目标"
    table.rows[0].cells[2].text = "实测"
    table.rows[1].cells[0].text = "Coverage"
    table.rows[1].cells[1].text = "85%"
    table.rows[1].cells[2].text = "TBD"
    return doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

FIXTURES = {
    "minimal.docx": make_minimal,
    "gb7713-official.docx": make_gb7713_official,
    "tsinghua-style.docx": make_tsinghua_style,
    "zhejiang-style.docx": make_zhejiang_style,
    "wuhan-style.docx": make_wuhan_style,
}


def main() -> int:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[generate_fixtures] 输出目录: {_OUT_DIR}")
    for name, factory in FIXTURES.items():
        out_path = _OUT_DIR / name
        doc = factory()
        doc.save(str(out_path))
        size_kb = out_path.stat().st_size / 1024
        print(f"  OK {name:30s} ({size_kb:.1f} KB)")
    print(f"[generate_fixtures] 共生成 {len(FIXTURES)} 个 fixture")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
