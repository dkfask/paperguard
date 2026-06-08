"""Sprint 1.5 + 2 端到端冒烟脚本。

跑通"内存 Project → project_to_ir → YAML engine validate_docx → to_qt_issues"完整 pipeline，
打印 issue 摘要（数量、错误/警告分布、YAML pack 规则数），验证 Sprint 1.5 + Sprint 2 全栈连通。

Sprint 2 commit #13 后：默认走 GB-T-7713-1-2006 YAML pack（25 条规则），
不再用硬编码的 stub_engine（已删）。

用法：
    cd D:/projects/paperguard
    QT_QPA_PLATFORM=offscreen python scripts/sprint1_5_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 import 找到 app/ 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtWidgets import QApplication

# offscreen 模式（CI / 无显示器环境）；先 import 后 sys.argv
_app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

from app.core.engine import validate_docx
from app.core.engine.loader import load_pack
from app.core.loaders.project_to_ir import project_to_ir
from app.core.reporters.pyqt_reporter import to_qt_issues
from app.models.project import ParagraphBlock, Project, SectionContent

PACK_ID = "GB-T-7713-1-2006"


def _build_mixed_project(n: int = 30) -> Project:
    """构造 n 段混合 valid/invalid 格式的内存 Project。

    偶数段：line-height: 1.0（错，期望 1.5）
    奇数段：line-height: 1.5（合规）+ text-indent: 0.74cm（合规）
    → 期望 ~15 段触发 PG-BODY-LINE-SPACING。
    """
    blocks: list[ParagraphBlock] = []
    for i in range(n):
        if i % 2 == 0:
            html = f'<p style="line-height: 1.0;">第 {i + 1} 段正文：本文研究的内容。</p>'
        else:
            html = (
                f'<p style="line-height: 1.5; text-indent: 0.74cm;">'
                f'第 {i + 1} 段正文：本文研究的内容。</p>'
            )
        blocks.append(ParagraphBlock(html=html))
    project = Project(title="Sprint 1.5 + 2 冒烟论文", author="Test")
    project.sections["body"] = SectionContent(section_id="body", blocks=blocks)
    return project


def main() -> int:
    print("=" * 60)
    print("Sprint 1.5 + 2 端到端冒烟测试（YAML engine）")
    print("=" * 60)
    print()

    # 1. 构造内存 Project
    print("[1/4] 构造内存 Project (30 段混合格式)...")
    project = _build_mixed_project(n=30)
    print(f"  OK Project title={project.title!r}, sections={list(project.sections.keys())}")
    print(f"  OK blocks: {len(project.sections['body'].blocks)} 段")

    # 2. Project → IR
    print()
    print("[2/4] project_to_ir (Project → IR Document)...")
    try:
        ir = project_to_ir(project, "body")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL project_to_ir 抛异常: {type(exc).__name__}: {exc}")
        return 1
    print(f"  OK IR paragraphs: {len(ir.paragraphs)}")
    print(f"  OK IR metadata.title: {ir.metadata.title!r}")

    # 3. YAML pack engine 校验
    print()
    print(f"[3/4] validate_docx (YAML pack {PACK_ID})...")
    pack = load_pack(PACK_ID)
    print(f"  OK YAML pack: {pack.id} v{pack.version}, {len(pack.rules)} rules loaded")
    findings = validate_docx(ir, PACK_ID)
    print(f"  OK Findings: {len(findings)} 条")

    # 4. 投影 QtIssue
    print()
    print("[4/4] to_qt_issues (Finding[] → QtIssue[])...")
    issues = to_qt_issues(findings)
    print(f"  OK QtIssues: {len(issues)} 条")

    # 统计
    severities: dict[str, int] = {}
    rule_ids: dict[str, int] = {}
    for iss in issues:
        severities[iss.severity] = severities.get(iss.severity, 0) + 1
        rule_ids[iss.rule_id] = rule_ids.get(iss.rule_id, 0) + 1

    error_count = severities.get("error", 0)
    warning_count = severities.get("warning", 0)
    info_count = severities.get("info", 0)

    print()
    print("-" * 60)
    print(f"Issues found: {len(issues)} ({error_count} errors, "
          f"{warning_count} warnings, {info_count} info)")
    print(f"YAML pack: {pack.id}, {len(pack.rules)} rules loaded")
    if rule_ids:
        print(f"By rule_id: {rule_ids}")
    print("-" * 60)

    # 校验期望
    if len(issues) <= 0:
        print("FAIL: 期望至少 1 个 issue，实际 0")
        return 1
    if len(pack.rules) != 25:
        print(f"FAIL: 期望 25 条规则，实际 {len(pack.rules)}")
        return 1

    print()
    print("=" * 60)
    print("OK Sprint 1.5 + 2 端到端跑通")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
