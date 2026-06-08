"""Tests for app.core.engine.visitor — Sprint 2 IR 遍历 + 规则命中.

12 RED tests cover：
- 空 doc / 无规则匹配 / 一规则一命中 / 一规则多命中
- scope 过滤 + when 短路双重过滤
- Fix 透传（rule.fix → Finding.fix）
- paragraph_index 正确
- 多 rule 结果排序稳定
- Severity 枚举保留

设计契约（参考 docs/sprints/sprint-2-plan.md §3.1 visitor.py）：
- visit_document(document, pack) -> list[Finding]
- 对每条 rule：scope 过滤 → when 求值 → 命中节点构造 Finding
- Finding.location.paragraph_index 来自 evaluate_scope 注入的 __index__
- Finding.actual / expected = None（MVP 简化；YAML rule 不显式提供 expected）
- 排序：(paragraph_index, rule_id) — 与 stub_engine 行为一致
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine.loader import Rule, RulePack
from app.core.engine.visitor import visit_document
from app.core.ir import (
    Document,
    DocumentMetadata,
    Fix,
    Paragraph,
    Run,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(paragraphs: list[Paragraph]) -> Document:
    return Document(
        sections=(),
        paragraphs=tuple(paragraphs),
        metadata=DocumentMetadata(title="test"),
    )


def _make_para(
    style_id: str = "Body Text",
    line_spacing: float = 1.5,
    text: str = "段落文本",
    font_eastasia: str | None = "宋体",
    font_size_pt: float | None = 12.0,
) -> Paragraph:
    return Paragraph(
        style_id=style_id,
        text=text,
        level=None,
        runs=(Run(text=text, font_eastasia=font_eastasia, font_size_pt=font_size_pt),),
        line_spacing=line_spacing,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.74,
    )


def _make_rule(
    rule_id: str = "PG-TEST",
    severity: Severity = Severity.WARNING,
    scope: str = "paragraphs[*]",
    when: dict | None = None,
    message: str = "test rule",
    fix: Fix | None = None,
) -> Rule:
    if when is None:
        when = {"==": [1, 1]}  # 永远命中
    return Rule(
        id=rule_id,
        severity=severity,
        scope=scope,
        when=when,
        message=message,
        fix=fix,
    )


def _make_pack(rules: list[Rule]) -> RulePack:
    return RulePack(
        id="TEST-PACK",
        name="测试",
        version="1.0.0",
        rules=tuple(rules),
    )


# ---------------------------------------------------------------------------
# 1. empty document
# ---------------------------------------------------------------------------

class TestEmptyDocument(unittest.TestCase):
    def test_empty_document_returns_empty_findings(self):
        doc = _make_doc([])
        pack = _make_pack([_make_rule()])
        findings = visit_document(doc, pack)
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------------
# 2. no rules / no match
# ---------------------------------------------------------------------------

class TestNoMatch(unittest.TestCase):
    def test_no_rules_in_pack_returns_empty(self):
        doc = _make_doc([_make_para()])
        pack = _make_pack([])
        findings = visit_document(doc, pack)
        self.assertEqual(findings, [])

    def test_scope_no_match_returns_empty(self):
        doc = _make_doc([_make_para(style_id="Body Text")])
        rule = _make_rule(scope="paragraphs[?style_id == 'NonExistent']")
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------------
# 3-4. one rule matches
# ---------------------------------------------------------------------------

class TestOneRuleMatch(unittest.TestCase):
    def test_one_rule_matches_one_para(self):
        doc = _make_doc([_make_para(line_spacing=1.0)])
        rule = _make_rule(
            rule_id="PG-LINE",
            scope="paragraphs[*]",
            when={"!=": [{"var": "line_spacing"}, 1.5]},
            message="行距不对",
        )
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "PG-LINE")

    def test_one_rule_matches_multiple_paras(self):
        doc = _make_doc([
            _make_para(line_spacing=1.0),
            _make_para(line_spacing=1.0),
            _make_para(line_spacing=1.5),  # 合规，跳过
        ])
        rule = _make_rule(
            scope="paragraphs[*]",
            when={"!=": [{"var": "line_spacing"}, 1.5]},
        )
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(len(findings), 2)


# ---------------------------------------------------------------------------
# 5. scope filter + when 双重过滤
# ---------------------------------------------------------------------------

class TestScopeFilterPlusWhen(unittest.TestCase):
    def test_scope_filter_reduces_candidates(self):
        """scope 限定 Body Text；其他段落不参与 when 检查。"""
        doc = _make_doc([
            _make_para(style_id="Body Text", line_spacing=1.0),  # 候选 + 违规
            _make_para(style_id="Heading 1", line_spacing=1.0),  # scope 过滤掉
            _make_para(style_id="Body Text", line_spacing=1.5),  # 候选 + 合规
        ])
        rule = _make_rule(
            scope="paragraphs[?style_id == 'Body Text']",
            when={"!=": [{"var": "line_spacing"}, 1.5]},
        )
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(len(findings), 1)


# ---------------------------------------------------------------------------
# 6. when false skips
# ---------------------------------------------------------------------------

class TestWhenFalseSkips(unittest.TestCase):
    def test_when_false_skips(self):
        doc = _make_doc([_make_para(line_spacing=1.5)])
        rule = _make_rule(
            scope="paragraphs[*]",
            when={"!=": [{"var": "line_spacing"}, 1.5]},
        )
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------------
# 7-8. Fix 透传
# ---------------------------------------------------------------------------

class TestFixPropagation(unittest.TestCase):
    def test_fix_attached_to_finding(self):
        fix = Fix(action="set", target="paragraph.line_spacing", value=1.5)
        rule = _make_rule(fix=fix)
        doc = _make_doc([_make_para()])
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(len(findings), 1)
        self.assertIsNotNone(findings[0].fix)
        self.assertEqual(findings[0].fix.target, "paragraph.line_spacing")
        self.assertEqual(findings[0].fix.value, 1.5)

    def test_rule_with_no_fix_finding_has_no_fix(self):
        rule = _make_rule(fix=None)
        doc = _make_doc([_make_para()])
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0].fix)


# ---------------------------------------------------------------------------
# 9. paragraph_index 正确
# ---------------------------------------------------------------------------

class TestParagraphIndex(unittest.TestCase):
    def test_paragraph_index_correct(self):
        doc = _make_doc([
            _make_para(style_id="A", line_spacing=1.0),  # idx 0 → 命中
            _make_para(style_id="B", line_spacing=1.5),  # idx 1 → 合规
            _make_para(style_id="A", line_spacing=1.0),  # idx 2 → 命中
        ])
        rule = _make_rule(
            scope="paragraphs[?style_id == 'A']",
            when={"!=": [{"var": "line_spacing"}, 1.5]},
        )
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(len(findings), 2)
        indices = sorted(f.location.paragraph_index for f in findings)
        self.assertEqual(indices, [0, 2])


# ---------------------------------------------------------------------------
# 10. 多 rules 排序
# ---------------------------------------------------------------------------

class TestMultiRulesSorted(unittest.TestCase):
    def test_multi_rules_findings_sorted(self):
        """两条 rules 在同一段命中，结果按 (paragraph_index, rule_id) 排序。"""
        doc = _make_doc([
            _make_para(line_spacing=1.0, font_size_pt=14.0),  # 违 R001 + R002
        ])
        r1 = _make_rule(
            rule_id="PG-ZZZ-RULE",  # rule_id 排序在后
            scope="paragraphs[*]",
            when={"!=": [{"var": "line_spacing"}, 1.5]},
        )
        r2 = _make_rule(
            rule_id="PG-AAA-RULE",  # rule_id 排序在前
            scope="paragraphs[*]",
            when={"!=": [{"var": "runs.0.font_size_pt"}, 12.0]},
        )
        pack = _make_pack([r1, r2])
        findings = visit_document(doc, pack)
        # 排序后 AAA 在前
        self.assertEqual(findings[0].rule_id, "PG-AAA-RULE")
        self.assertEqual(findings[1].rule_id, "PG-ZZZ-RULE")


# ---------------------------------------------------------------------------
# 11. Severity 枚举保留
# ---------------------------------------------------------------------------

class TestSeverityPreserved(unittest.TestCase):
    def test_severity_preserved_as_enum(self):
        rule = _make_rule(severity=Severity.ERROR)
        doc = _make_doc([_make_para()])
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(findings[0].severity, Severity.ERROR)


# ---------------------------------------------------------------------------
# 12. location.text_snippet 填充
# ---------------------------------------------------------------------------

class TestLocationTextSnippet(unittest.TestCase):
    def test_location_text_snippet_filled(self):
        """Finding.location.text_snippet 应来自 paragraph.text，给 PyQt sidebar 用。"""
        doc = _make_doc([_make_para(text="本节研究主要内容")])
        rule = _make_rule()
        pack = _make_pack([rule])
        findings = visit_document(doc, pack)
        self.assertEqual(findings[0].location.text_snippet, "本节研究主要内容")


if __name__ == "__main__":
    unittest.main()
