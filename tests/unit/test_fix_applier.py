"""Tests for app.core.writers.fix_applier — Sprint 3.

Sprint 3 commit #5: 8 RED tests for fix_applier.

设计契约（plan §3.5）：
- apply_fix(ir, finding) -> Document（不可变，原 IR 不变）
- apply_all_fixes(ir, findings) -> (Document, list[Finding])  返回 (fixed_ir, remaining)
- 支持 set action（insert/delete/wrap 留 Sprint 5）
- target 点分解析: 'paragraph.line_spacing' / 'run.font_size_pt'
- 解析失败的 target → 返原 IR（fail-safe）
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.ir import (
    Document,
    DocumentMetadata,
    Finding,
    Fix,
    Location,
    Paragraph,
    Run,
    Section,
    Severity,
)
from app.core.writers.fix_applier import apply_fix, apply_all_fixes


def _doc(paragraphs: tuple[Paragraph, ...] = ()) -> Document:
    return Document(
        sections=(Section(),),
        paragraphs=paragraphs,
        metadata=DocumentMetadata(title="t"),
    )


def _para(
    line_spacing: float = 1.5,
    font_size: float = 12.0,
    runs: tuple[Run, ...] | None = None,
) -> Paragraph:
    return Paragraph(
        style_id="Body Text",
        text="测试段",
        level=None,
        runs=runs if runs is not None else (
            Run(text="测试段", font_eastasia="宋体", font_size_pt=font_size),
        ),
        line_spacing=line_spacing,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.74,
    )


def _finding(target: str, value, paragraph_index: int = 0,
             action: str = "set") -> Finding:
    return Finding(
        rule_id="R-TEST",
        severity=Severity.WARNING,
        message="test",
        location=Location(paragraph_index=paragraph_index, text_snippet="t"),
        actual=None,
        expected=None,
        fix=Fix(action=action, target=target, value=value),
    )


class TestApplyFix(unittest.TestCase):

    def test_apply_fix_paragraph_line_spacing(self):
        """set paragraph.line_spacing=1.5 修复 line_spacing=1.0 段"""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        fixed = apply_fix(ir, _finding("paragraph.line_spacing", 1.5))
        self.assertEqual(fixed.paragraphs[0].line_spacing, 1.5)

    def test_apply_fix_run_font_size(self):
        """set run.font_size_pt=12.0 修复 font_size=10.0 段"""
        para = _para(font_size=10.0)
        ir = _doc((para,))
        fixed = apply_fix(ir, _finding("run.font_size_pt", 12.0))
        self.assertEqual(fixed.paragraphs[0].runs[0].font_size_pt, 12.0)

    def test_apply_fix_runs_wildcard_sets_all_runs(self):
        """set runs.*.bold=False 清理同段所有 run 的加粗。"""
        para = _para(runs=(
            Run(text="甲", bold=True),
            Run(text="乙", bold=True),
        ))
        ir = _doc((para,))
        fixed = apply_fix(ir, _finding("runs.*.bold", False))

        self.assertEqual([r.bold for r in fixed.paragraphs[0].runs], [False, False])
        self.assertEqual([r.bold for r in ir.paragraphs[0].runs], [True, True])

    def test_apply_fix_accepts_page_break_before_alias(self):
        """set paragraph.page_break_before=True 兼容旧 pack target."""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        fixed = apply_fix(ir, _finding("paragraph.page_break_before", True))
        self.assertTrue(fixed.paragraphs[0].is_page_break_before)

    def test_apply_fix_paragraph_alignment(self):
        """set paragraph.alignment=center 修复段落对齐方式."""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        fixed = apply_fix(ir, _finding("paragraph.alignment", "center"))
        self.assertEqual(fixed.paragraphs[0].alignment, "center")

    def test_apply_fix_unknown_target_returns_original(self):
        """未知 target（不在 mapping 表里）→ 返原 IR（fail-safe）"""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        fixed = apply_fix(ir, _finding("paragraph.unknown_attr", 1.5))
        # 返原 IR，line_spacing 不变
        self.assertEqual(fixed.paragraphs[0].line_spacing, 1.0)

    def test_apply_fix_non_set_action_returns_original(self):
        """action != 'set' → 返原 IR（MVP 只支持 set）"""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        fixed = apply_fix(ir, _finding("paragraph.line_spacing", 1.5, action="insert"))
        self.assertEqual(fixed.paragraphs[0].line_spacing, 1.0)

    def test_apply_fix_preserves_immutability(self):
        """原 IR 不可变：apply_fix 后原 IR 段属性不变"""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        _ = apply_fix(ir, _finding("paragraph.line_spacing", 1.5))
        # 关键：原 IR 的段 line_spacing 仍是 1.0（不可变）
        self.assertEqual(ir.paragraphs[0].line_spacing, 1.0)

    def test_apply_fix_preserves_header_and_footer_paragraphs(self):
        para = _para(line_spacing=1.0)
        header = Paragraph(
            style_id="Header", text="header", level=None, runs=(),
            line_spacing=1.0, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        footer = Paragraph(
            style_id="Footer", text="footer", level=None, runs=(),
            line_spacing=1.0, space_before_pt=0.0, space_after_pt=0.0,
            first_line_indent_cm=0.0,
        )
        ir = Document(
            sections=(Section(),),
            paragraphs=(para,),
            metadata=DocumentMetadata(title="t"),
            header_paragraphs=(header,),
            footer_paragraphs=(footer,),
        )

        fixed = apply_fix(ir, _finding("paragraph.line_spacing", 1.5))

        self.assertEqual(fixed.header_paragraphs, (header,))
        self.assertEqual(fixed.footer_paragraphs, (footer,))

    def test_apply_all_fixes_applies_each_set(self):
        """apply_all_fixes 顺序应用多条 set fix"""
        para = _para(line_spacing=1.0, font_size=10.0)
        ir = _doc((para,))
        fixed, remaining = apply_all_fixes(ir, [
            _finding("paragraph.line_spacing", 1.5),
            _finding("run.font_size_pt", 12.0),
            _finding("runs.*.bold", False),
            _finding("paragraph.page_break_before", True),
            _finding("paragraph.alignment", "center"),
        ])
        self.assertEqual(fixed.paragraphs[0].line_spacing, 1.5)
        self.assertEqual(fixed.paragraphs[0].runs[0].font_size_pt, 12.0)
        self.assertFalse(fixed.paragraphs[0].runs[0].bold)
        self.assertTrue(fixed.paragraphs[0].is_page_break_before)
        self.assertEqual(fixed.paragraphs[0].alignment, "center")
        self.assertEqual(remaining, [])

    def test_apply_all_fixes_returns_remaining_no_fix(self):
        """apply_all_fixes 跳过 fix=None 的 finding，加入 remaining"""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        no_fix_finding = Finding(
            rule_id="R-MANUAL",
            severity=Severity.INFO,
            message="manual",
            location=Location(paragraph_index=0, text_snippet="t"),
            actual=None,
            expected=None,
            fix=None,
        )
        fixed, remaining = apply_all_fixes(ir, [no_fix_finding])
        # 原 IR line_spacing 不变（fix=None 跳过）
        self.assertEqual(fixed.paragraphs[0].line_spacing, 1.0)
        # remaining 含 R-MANUAL
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].rule_id, "R-MANUAL")

    def test_apply_all_fixes_handles_unknown_target_safely(self):
        """apply_all_fixes 遇到未知 target 不抛错，返原 IR + remaining 含原 finding"""
        para = _para(line_spacing=1.0)
        ir = _doc((para,))
        unknown_finding = _finding("paragraph.nonexistent", 99.0)
        fixed, remaining = apply_all_fixes(ir, [unknown_finding])
        # 原 IR 不变（line_spacing=1.0）
        self.assertEqual(fixed.paragraphs[0].line_spacing, 1.0)
        # remaining 含原 finding（无法 fix 但记录在 remaining）
        self.assertEqual(len(remaining), 1)


if __name__ == "__main__":
    unittest.main()
