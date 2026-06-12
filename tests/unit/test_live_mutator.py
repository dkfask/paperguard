"""Tests for app.services.live_mutator — Sprint 1.5 LiveMutator.

Sprint 1.5 TDD step 4（见 docs/sprints/sprint-1.5-plan.md §4.1）。

本测试覆盖段落级 Fix、单 run Fix，以及 Sprint 5 新增的 ``runs.*.<attr>``
wildcard run 修复。

12 个测试：
- 3 类 fix 各自 set block
- wildcard runs.* 字号 / 颜色修复
- 单参数签名（Must-fix #1）
- 修改 blocks 不修改 content_html 直接路径（Must-fix #3）
- can_apply 已知 / 未知
- apply_fix 返回 True / False
- paragraph_index 越界返回 False

约束：
- 1 个 Project + 1 个 Section + N 个 ParagraphBlock
- 假 checker 提供 get_fix(issue) -> Fix
- 不依赖 PyQt（mutator 纯改 model，UI 后续重读）
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.ir.finding import Fix
from app.core.reporters.pyqt_reporter import QtIssue
from app.models.project import ImageBlock, ParagraphBlock, Project, SectionContent
from app.services.live_mutator import LiveMutator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_issue(
    rule_id: str,
    paragraph_index: int = 0,
    severity: str = "warning",
    message: str = "test issue",
) -> QtIssue:
    """构造 QtIssue（最小有效 payload）。"""
    return QtIssue(
        rule_id=rule_id,
        severity=severity,
        message=message,
        location_text=f"第 {paragraph_index} 段",
        paragraph_index=paragraph_index,
        fix_suggestion=None,
    )


class _FakeChecker:
    """测试用 fake checker：仿造 ``LiveFormatChecker.get_fix`` 的行为。

    支持两种 key 风格（向后兼容老测试）：
    - key == ``issue.rule_id``：直接命中
    - key 是任意字符串，``fix.target`` 的 leaf 段 == ``issue.rule_id``：也命中

    这样既可以测老风格（``{"line_spacing": Fix(target="line_spacing", ...)}`` + issue.rule_id="line_spacing"），
    也可以测新风格（``{"R001": Fix(target="paragraph.line_spacing", ...)}`` + issue.rule_id="R001"）。
    """

    def __init__(self, fixes: dict[str, Fix] | None = None) -> None:
        self.fixes: dict[str, Fix] = fixes or {}

    def get_fix(self, issue: QtIssue) -> Fix | None:
        if issue.rule_id in self.fixes:
            return self.fixes[issue.rule_id]
        # Fallback: scan by target leaf
        for fix in self.fixes.values():
            if fix.target.rsplit(".", 1)[-1] == issue.rule_id:
                return fix
        return None


def _make_project(blocks_html: list[str], section_id: str = "ch1") -> Project:
    """构造一个单 section 的 Project，每段一个 ParagraphBlock。"""
    return Project(
        title="测试论文",
        sections={
            section_id: SectionContent(
                section_id=section_id,
                blocks=[ParagraphBlock(html=h) for h in blocks_html],
            )
        },
    )


# ---------------------------------------------------------------------------
# 10 tests
# ---------------------------------------------------------------------------

class TestApplyFixSetsBlock(unittest.TestCase):
    """3 类支持的 Fix：apply_fix 必须把目标值写入对应 block。"""

    def test_apply_fix_line_spacing_sets_block(self):
        # Given 1 段 <p>text</p>，line_spacing fix = 1.5
        project = _make_project(["<p>第一段</p>"])
        checker = _FakeChecker({
            "line_spacing": Fix(action="set", target="line_spacing", value=1.5),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("line_spacing", paragraph_index=0)

        # When
        ok = mutator.apply_fix(issue)

        # Then
        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        # line-height 1.5 必须出现在 block.html 中（具体编码到 <p> style）
        self.assertIn("line-height: 1.5", block.html)

    def test_apply_fix_font_size_sets_block(self):
        # Given 1 段 <p><span>text</span></p>，font_size_pt fix = 12.0
        project = _make_project(["<p><span>第一段文字</span></p>"])
        checker = _FakeChecker({
            "font_size_pt": Fix(action="set", target="font_size_pt", value=12.0),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("font_size_pt", paragraph_index=0)

        # When
        ok = mutator.apply_fix(issue)

        # Then
        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        # 首个 run 的 font-size 必须更新为 12pt
        self.assertIn("font-size: 12.0pt", block.html)

    def test_apply_fix_runs_wildcard_font_size_sets_all_runs(self):
        project = _make_project([
            '<p><span style="font-size: 10pt;">甲</span><span style="font-size: 14pt;">乙</span></p>'
        ])
        checker = _FakeChecker({
            "R001": Fix(action="set", target="runs.*.font_size_pt", value=12.0),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("R001", paragraph_index=0)

        ok = mutator.apply_fix(issue)

        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        self.assertEqual(block.html.count("font-size: 12.0pt"), 2)
        self.assertNotIn("font-size: 10pt", block.html)
        self.assertNotIn("font-size: 14pt", block.html)

    def test_apply_fix_runs_wildcard_color_sets_all_runs(self):
        project = _make_project([
            '<p><span style="color: #ff0000;">甲</span><span style="color: blue;">乙</span></p>'
        ])
        checker = _FakeChecker({
            "R002": Fix(action="set", target="runs.*.color_hex", value="000000"),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("R002", paragraph_index=0)

        ok = mutator.apply_fix(issue)

        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        self.assertEqual(block.html.count("color: #000000"), 2)
        self.assertNotIn("#ff0000", block.html.lower())
        self.assertNotIn("color: blue", block.html.lower())

    def test_apply_fix_runs_wildcard_highlight_clears_all_runs(self):
        project = _make_project([
            '<p><span style="background-color: yellow;">A</span>'
            '<span style="background-color: yellow;">B</span></p>'
        ])
        checker = _FakeChecker({
            "R004": Fix(action="set", target="runs.*.highlight", value=None),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("R004", paragraph_index=0)

        ok = mutator.apply_fix(issue)

        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        self.assertEqual(block.html.count("background-color: transparent"), 2)
        self.assertNotIn("background-color: yellow", block.html.lower())

    def test_apply_fix_runs_wildcard_vert_align_clears_all_runs(self):
        project = _make_project([
            '<p><span style="vertical-align: super;">A</span>'
            '<span style="vertical-align: sub;">B</span></p>'
        ])
        checker = _FakeChecker({
            "R005": Fix(action="set", target="runs.*.vert_align", value=None),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("R005", paragraph_index=0)

        ok = mutator.apply_fix(issue)

        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        self.assertEqual(block.html.count("vertical-align: baseline"), 2)
        self.assertNotIn("vertical-align: super", block.html.lower())
        self.assertNotIn("vertical-align: sub", block.html.lower())

    def test_apply_fix_first_line_indent_sets_block(self):
        # Given 1 段 <p>text</p>，first_line_indent_cm fix = 0.74
        project = _make_project(["<p>第一段</p>"])
        checker = _FakeChecker({
            "first_line_indent_cm": Fix(action="set", target="first_line_indent_cm", value=0.74),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("first_line_indent_cm", paragraph_index=0)

        # When
        ok = mutator.apply_fix(issue)

        # Then
        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        # text-indent: 0.74cm 必须出现在 block.html 中
        self.assertIn("text-indent: 0.74cm", block.html)

    def test_apply_fix_alignment_sets_paragraph_text_align(self):
        project = _make_project(['<p style="text-align: center;">第一段</p>'])
        checker = _FakeChecker({
            "R003": Fix(action="set", target="paragraph.alignment", value="justify"),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("R003", paragraph_index=0)

        ok = mutator.apply_fix(issue)

        self.assertTrue(ok)
        block = project.sections["ch1"].blocks[0]
        self.assertIn("text-align: justify", block.html)
        self.assertNotIn("text-align: center", block.html)


class TestApplyFixSignature(unittest.TestCase):
    """apply_fix 必须是单参数签名（Must-fix #1）。"""

    def test_apply_fix_single_arg_signature(self):
        # Given mutator + issue（仅 issue 一参）
        project = _make_project(["<p>x</p>"])
        checker = _FakeChecker({
            "line_spacing": Fix(action="set", target="line_spacing", value=1.5),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("line_spacing", paragraph_index=0)

        # When: 只传 issue（不传 fix）
        result = mutator.apply_fix(issue)

        # Then: 必须返回 True（fix 从 checker.get_fix 内部取）
        self.assertTrue(result)


class TestApplyFixModifiesBlocksNotHtml(unittest.TestCase):
    """mutator 必须修改 SectionContent.blocks，不是直接写 content_html
    （Must-fix #3：content_html 是 computed property）。"""

    def test_apply_fix_modifies_blocks_not_html(self):
        # Given 1 段 + line_spacing fix
        project = _make_project(["<p>原文本</p>"])
        checker = _FakeChecker({
            "line_spacing": Fix(action="set", target="line_spacing", value=1.5),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("line_spacing", paragraph_index=0)

        # 记录原 block.html + 原 content_html
        original_block_html = project.sections["ch1"].blocks[0].html
        original_content_html = project.sections["ch1"].content_html

        # When
        ok = mutator.apply_fix(issue)

        # Then
        self.assertTrue(ok)
        # 1) blocks[0] 已被修改
        new_block_html = project.sections["ch1"].blocks[0].html
        self.assertNotEqual(new_block_html, original_block_html)
        self.assertIn("line-height: 1.5", new_block_html)
        # 2) content_html 是 computed property，blocks 改 → content_html 跟着变
        #    （这里也确认 mutator 没绕过 blocks 直接改 content_html 字段，
        #    因为该字段是 property，不可写）
        new_content_html = project.sections["ch1"].content_html
        self.assertNotEqual(new_content_html, original_content_html)
        self.assertIn("line-height: 1.5", new_content_html)


class TestCanApply(unittest.TestCase):
    """can_apply 通过 checker.get_fix(issue) 取 Fix，再看 target leaf 是不是支持。"""

    def test_can_apply_unknown_rule_returns_false(self):
        project = _make_project(["<p>x</p>"])
        checker = _FakeChecker({
            "page_margin": Fix(action="set", target="page_margin", value=2.0),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("R009_page_margin", paragraph_index=0)

        self.assertFalse(mutator.can_apply(issue))

    def test_can_apply_known_rule_returns_true(self):
        project = _make_project(["<p>x</p>"])
        checker = _FakeChecker({
            "R001": Fix(action="set", target="paragraph.line_spacing", value=1.5),
        })
        mutator = LiveMutator(project, checker=checker)
        # rule_id 是 R001，mutator 通过 fix.target 的 leaf 段判断可应用性
        issue = _make_issue("R001", paragraph_index=0)

        self.assertTrue(mutator.can_apply(issue))


class TestApplyFixReturnValue(unittest.TestCase):
    """apply_fix 的成功 / 失败返回值。"""

    def test_apply_fix_returns_true_on_success(self):
        # Given line_spacing fix 走完整链路
        project = _make_project(["<p>x</p>"])
        checker = _FakeChecker({
            "line_spacing": Fix(action="set", target="line_spacing", value=1.5),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("line_spacing", paragraph_index=0)

        # When
        result = mutator.apply_fix(issue)

        # Then
        self.assertTrue(result)

    def test_apply_fix_returns_false_on_unknown_rule(self):
        # Given 不支持的 rule
        project = _make_project(["<p>x</p>"])
        checker = _FakeChecker({
            "page_margin": Fix(action="set", target="page_margin", value=2.54),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("page_margin", paragraph_index=0)

        # When
        result = mutator.apply_fix(issue)

        # Then: 不支持的 rule 必须返 False，blocks 不动
        self.assertFalse(result)
        self.assertEqual(project.sections["ch1"].blocks[0].html, "<p>x</p>")


class TestParagraphIndexOutOfRange(unittest.TestCase):
    """paragraph_index 越界 → 返 False。"""

    def test_paragraph_index_out_of_range_returns_false(self):
        # Given 2 段，paragraph_index=5 越界
        project = _make_project(["<p>第一段</p>", "<p>第二段</p>"])
        checker = _FakeChecker({
            "line_spacing": Fix(action="set", target="line_spacing", value=1.5),
        })
        mutator = LiveMutator(project, checker=checker)
        issue = _make_issue("line_spacing", paragraph_index=5)

        # When
        result = mutator.apply_fix(issue)

        # Then
        self.assertFalse(result)
        # blocks 全部原样
        self.assertEqual(project.sections["ch1"].blocks[0].html, "<p>第一段</p>")
        self.assertEqual(project.sections["ch1"].blocks[1].html, "<p>第二段</p>")


if __name__ == "__main__":
    unittest.main()
