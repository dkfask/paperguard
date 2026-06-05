"""Sprint 1 端到端冒烟脚本。

用最小 docx 跑通 "load_docx → TerminalReporter().report()" 完整流程。

用法：
    cd E:/projects/论文书写模版格式规范软件
    python scripts/sprint1_smoke.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# 让 import 找到 app/ 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.core.loaders.docx_loader import load_docx
from app.core.reporters.terminal import TerminalReporter


def _build_minimal_docx() -> bytes:
    """构造一个内存 docx 用于冒烟测试。"""
    doc = DocxDocument()

    # 页面设置（A4 + GB/T 7713 边距）
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)

    # 标题 1
    doc.add_heading("1 引言", level=1)

    # 正文（1.5 倍行距 + 0.74 cm 首行缩进 + 宋体 12pt）
    p = doc.add_paragraph("本文研究的主要内容是...")
    ppr = p._p.get_or_add_pPr()
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:line"), "360")
    spacing.set(qn("w:lineRule"), "auto")
    ppr.append(spacing)
    ind = OxmlElement("w:ind")
    ind.set(qn("w:firstLine"), "419")
    ppr.append(ind)
    run = p.runs[0]
    run.font.size = Pt(12)
    rpr = run._r.get_or_add_rPr()
    rfonts = OxmlElement("w:rFonts")
    rfonts.set(qn("w:eastAsia"), "宋体")
    rfonts.set(qn("w:ascii"), "Times New Roman")
    rpr.append(rfonts)

    # 标题 2
    doc.add_heading("1.1 背景", level=2)

    # 正文 2
    p2 = doc.add_paragraph("随着...")
    p2ppr = p2._p.get_or_add_pPr()
    sp2 = OxmlElement("w:spacing")
    sp2.set(qn("w:line"), "360")
    sp2.set(qn("w:lineRule"), "auto")
    p2ppr.append(sp2)
    ind2 = OxmlElement("w:ind")
    ind2.set(qn("w:firstLine"), "419")
    p2ppr.append(ind2)

    # 写标题
    doc.core_properties.title = "示例论文"
    doc.core_properties.author = "张三"

    # 序列化
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def main() -> int:
    # 1. 构造 docx
    print("=" * 60)
    print("Sprint 1 端到端冒烟测试")
    print("=" * 60)
    print()
    print("[1/3] 构造内存 docx...")
    docx_bytes = _build_minimal_docx()
    print(f"  OK docx 大小: {len(docx_bytes)} bytes")

    # 2. 写到 fixture
    print()
    print("[2/3] 写到 fixture + load_docx...")
    fixture_path = Path("tests/fixtures/sample_paper.docx")
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(docx_bytes)

    ir = load_docx(fixture_path)
    print(f"  OK sections: {len(ir.sections)}")
    print(f"  OK paragraphs: {len(ir.paragraphs)}")
    print(f"  OK metadata.title: {ir.metadata.title!r}")
    print(f"  OK metadata.author: {ir.metadata.author!r}")

    # 3. 渲染输出
    print()
    print("[3/3] TerminalReporter().report() 输出：")
    print()
    print("-" * 60)
    print(TerminalReporter().report(ir))
    print("-" * 60)

    print()
    print("=" * 60)
    print("OK Sprint 1 端到端跑通！")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
