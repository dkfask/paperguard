"""Sprint 1.5 stub engine — 5 条硬编码规则，纯函数实现。

Q1 决策：Sprint 1.5 自带 5 条规则 stub engine，让性能基线可被真测试。
Sprint 2 在此基础上加 YAML pack loader + 25+ 规则（替换/扩展此处）。

约束：
- 纯 Python，**严禁** import PyQt（app/core/ 是无 GUI 依赖的核心层）
- 5 条规则 = 5 个纯函数 + validate_docx 入口
- 每条 Finding 附带 Fix（action/target/value），供 live_mutator 写回 IR
- 每条规则每个 paragraph 最多产生 1 条 Finding（避免噪音）

规则清单（GB-T-7713-1-2006 stub pack）：
  R001 line_spacing           paragraph.line_spacing 必须 1.5
  R002 font_size              run.font_size_pt 必须 12.0（小四）
  R003 first_line_indent      paragraph.first_line_indent_cm 必须 0.74（2 字符）
  R004 font_family            run.font_eastasia 必须 "宋体"
  R005 paragraph_spacing      paragraph.space_before_pt / space_after_pt 必须 0
"""

from __future__ import annotations

from typing import Callable

from app.core.ir import Document, Finding, Fix, Location, Paragraph, Run, Severity

# ---------------------------------------------------------------------------
# 规则常量 — 期望值集中在此，方便 Sprint 2 替换为 YAML pack loader
# ---------------------------------------------------------------------------

EXPECTED_LINE_SPACING = 1.5
EXPECTED_FONT_SIZE_PT = 12.0
EXPECTED_FIRST_LINE_INDENT_CM = 0.74
EXPECTED_FONT_EASTASIA = "宋体"
EXPECTED_PARAGRAPH_SPACING_PT = 0.0


# ---------------------------------------------------------------------------
# 5 个规则函数 — 每个签名一致：(paragraph, paragraph_index) -> Finding | None
# 返回 None 表示合规；返回 Finding 表示违规（已带 Fix）。
# ---------------------------------------------------------------------------

def _check_r001_line_spacing(p: Paragraph, idx: int) -> Finding | None:
    """R001：行距必须 1.5。"""
    if p.line_spacing == EXPECTED_LINE_SPACING:
        return None
    return Finding(
        rule_id="R001",
        severity=Severity.WARNING,
        message=f"行距应为 {EXPECTED_LINE_SPACING}，当前为 {p.line_spacing}",
        location=Location(paragraph_index=idx, text_snippet=p.text),
        actual=p.line_spacing,
        expected=EXPECTED_LINE_SPACING,
        fix=Fix(
            action="set",
            target="paragraph.line_spacing",
            value=EXPECTED_LINE_SPACING,
        ),
    )


def _check_r002_font_size(p: Paragraph, idx: int) -> Finding | None:
    """R002：正文字号必须 12pt。检查 paragraph 的**第一个**有 font_size 的 run。
    若整段无字号信息则跳过（用 style_id 默认值即可）。
    """
    target_run: Run | None = None
    for r in p.runs:
        if r.font_size_pt is not None:
            target_run = r
            break
    if target_run is None or target_run.font_size_pt == EXPECTED_FONT_SIZE_PT:
        return None
    actual = target_run.font_size_pt
    return Finding(
        rule_id="R002",
        severity=Severity.WARNING,
        message=f"正文字号应为 {EXPECTED_FONT_SIZE_PT}pt，当前为 {actual}pt",
        location=Location(paragraph_index=idx, text_snippet=p.text),
        actual=actual,
        expected=EXPECTED_FONT_SIZE_PT,
        fix=Fix(
            action="set",
            target="run.font_size_pt",
            value=EXPECTED_FONT_SIZE_PT,
        ),
    )


def _check_r003_first_line_indent(p: Paragraph, idx: int) -> Finding | None:
    """R003：首行缩进必须 0.74cm（2 字符）。"""
    if p.first_line_indent_cm == EXPECTED_FIRST_LINE_INDENT_CM:
        return None
    return Finding(
        rule_id="R003",
        severity=Severity.WARNING,
        message=f"首行缩进应为 {EXPECTED_FIRST_LINE_INDENT_CM}cm，当前为 {p.first_line_indent_cm}cm",
        location=Location(paragraph_index=idx, text_snippet=p.text),
        actual=p.first_line_indent_cm,
        expected=EXPECTED_FIRST_LINE_INDENT_CM,
        fix=Fix(
            action="set",
            target="paragraph.first_line_indent_cm",
            value=EXPECTED_FIRST_LINE_INDENT_CM,
        ),
    )


def _check_r004_font_family(p: Paragraph, idx: int) -> Finding | None:
    """R004：中文字体必须是 "宋体"。检查 paragraph 的**第一个**有 font_eastasia 的 run。"""
    target_run: Run | None = None
    for r in p.runs:
        if r.font_eastasia is not None:
            target_run = r
            break
    if target_run is None or target_run.font_eastasia == EXPECTED_FONT_EASTASIA:
        return None
    actual = target_run.font_eastasia
    return Finding(
        rule_id="R004",
        severity=Severity.WARNING,
        message=f'中文字体应为 "{EXPECTED_FONT_EASTASIA}"，当前为 "{actual}"',
        location=Location(paragraph_index=idx, text_snippet=p.text),
        actual=actual,
        expected=EXPECTED_FONT_EASTASIA,
        fix=Fix(
            action="set",
            target="run.font_eastasia",
            value=EXPECTED_FONT_EASTASIA,
        ),
    )


def _check_r005_paragraph_spacing(p: Paragraph, idx: int) -> Finding | None:
    """R005：段前/段后必须 0pt。"""
    if p.space_before_pt == EXPECTED_PARAGRAPH_SPACING_PT and \
       p.space_after_pt == EXPECTED_PARAGRAPH_SPACING_PT:
        return None
    return Finding(
        rule_id="R005",
        severity=Severity.WARNING,
        message=(
            f"段前/段后应为 {EXPECTED_PARAGRAPH_SPACING_PT}pt，"
            f"当前段前 {p.space_before_pt}pt、段后 {p.space_after_pt}pt"
        ),
        location=Location(paragraph_index=idx, text_snippet=p.text),
        actual={"space_before_pt": p.space_before_pt, "space_after_pt": p.space_after_pt},
        expected={"space_before_pt": 0.0, "space_after_pt": 0.0},
        fix=Fix(
            action="set",
            target="paragraph.space_before_pt",
            value=EXPECTED_PARAGRAPH_SPACING_PT,
        ),
    )


# 规则注册表 — Sprint 2 可用 YAML pack loader 动态替换
RULES: list[Callable[[Paragraph, int], Finding | None]] = [
    _check_r001_line_spacing,
    _check_r002_font_size,
    _check_r003_first_line_indent,
    _check_r004_font_family,
    _check_r005_paragraph_spacing,
]


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def validate_docx(ir: Document, pack: str = "stub-GB-T-7713-1-2006") -> list[Finding]:
    """对 IR Document 跑全部规则，返回所有违规 Finding 列表。

    Args:
        ir: 不可变 IR Document（frozen dataclass）
        pack: 规则包 ID；Sprint 1.5 仅 stub-GB-T-7713-1-2006；Sprint 2 用真实 pack。

    Returns:
        list[Finding]：每条 Finding 必带 Fix（除非引擎无法生成修复动作）。
        列表按 (paragraph_index, rule_id) 稳定排序，便于 UI diff。
    """
    findings: list[Finding] = []
    for idx, paragraph in enumerate(ir.paragraphs):
        for rule in RULES:
            finding = rule(paragraph, idx)
            if finding is not None:
                findings.append(finding)
    findings.sort(
        key=lambda f: (
            f.location.paragraph_index if f.location.paragraph_index is not None else -1,
            f.rule_id,
        )
    )
    return findings
