"""Tests for app.core.engine.evaluator — Sprint 2 表达式求值器.

15 RED tests cover：
- evaluate_scope(scope, document) — JMESPath 过滤 IR + 注入 paragraph_index
- evaluate_when(when, node)        — JSONLogic 单节点布尔求值
- jmespath.compile lru_cache       — 相同 scope 不重新 parse
- 短路求值 / 缺失字段 / 嵌套 var

设计契约（参考 docs/sprints/sprint-2-plan.md §3.1 evaluator.py）：
- evaluate_scope 返回 ``list[tuple[paragraph_index | None, dict]]``
  - paragraph 节点：index = 段落在 ``document.paragraphs`` 中的下标
  - 其他节点（section / metadata / run）：index = None
  - 无匹配返回 []
- evaluate_when 接收已展开的 dict（不是 IR 对象），直接 forward 给 jsonLogic
- 异常 fail-fast：JMESPath ParseError / 非法 when 表达式 → raise（不 swallow）
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine.evaluator import evaluate_scope, evaluate_when
from app.core.ir import (
    Document,
    DocumentMetadata,
    Paragraph,
    Run,
)


def _make_doc(paragraphs: list[Paragraph]) -> Document:
    return Document(
        sections=(),
        paragraphs=tuple(paragraphs),
        metadata=DocumentMetadata(title="test"),
    )


def _make_para(
    style_id: str = "Body Text",
    line_spacing: float = 1.5,
    font_eastasia: str | None = "宋体",
    font_size_pt: float | None = 12.0,
    text: str = "test",
    level: int | None = None,
) -> Paragraph:
    return Paragraph(
        style_id=style_id,
        text=text,
        level=level,
        runs=(Run(text=text, font_eastasia=font_eastasia, font_size_pt=font_size_pt),),
        line_spacing=line_spacing,
        space_before_pt=0.0,
        space_after_pt=0.0,
        first_line_indent_cm=0.74,
    )


# ---------------------------------------------------------------------------
# evaluate_scope — JMESPath 过滤
# ---------------------------------------------------------------------------

class TestEvaluateScope(unittest.TestCase):

    def test_scope_paragraphs_filter_by_style(self):
        """``paragraphs[?style_id == 'Body Text']`` 应过滤出 Body Text 段。"""
        doc = _make_doc([
            _make_para(style_id="Body Text"),
            _make_para(style_id="Heading 1"),
            _make_para(style_id="Body Text"),
        ])
        result = evaluate_scope("paragraphs[?style_id == 'Body Text']", doc)
        self.assertEqual(len(result), 2)

    def test_scope_returns_paragraph_index(self):
        """每个匹配元素必须带原 paragraph_index（用于 Finding.location）。"""
        doc = _make_doc([
            _make_para(style_id="Body Text"),       # index 0
            _make_para(style_id="Heading 1"),       # index 1
            _make_para(style_id="Body Text"),       # index 2
        ])
        result = evaluate_scope("paragraphs[?style_id == 'Body Text']", doc)
        indices = [idx for idx, _ in result]
        self.assertIn(0, indices)
        self.assertIn(2, indices)
        self.assertNotIn(1, indices)

    def test_scope_no_match_returns_empty_list(self):
        doc = _make_doc([_make_para(style_id="Body Text")])
        result = evaluate_scope("paragraphs[?style_id == 'NonExistent']", doc)
        self.assertEqual(result, [])

    def test_scope_all_paragraphs(self):
        doc = _make_doc([
            _make_para(style_id="A"),
            _make_para(style_id="B"),
        ])
        result = evaluate_scope("paragraphs[*]", doc)
        self.assertEqual(len(result), 2)

    def test_scope_invalid_jmespath_raises(self):
        """非法 JMESPath 表达式应 raise（fail-fast）。"""
        import jmespath.exceptions
        doc = _make_doc([_make_para()])
        with self.assertRaises(jmespath.exceptions.ParseError):
            evaluate_scope("paragraphs[?? broken", doc)

    def test_scope_compile_cached(self):
        """相同 scope 多次调用，jmespath.compile 应被 lru_cache 复用。"""
        doc = _make_doc([_make_para()])
        # 调用两次相同表达式
        evaluate_scope("paragraphs[*]", doc)
        evaluate_scope("paragraphs[*]", doc)
        # 通过 _compile 函数验证 cache_info
        from app.core.engine.evaluator import _compile
        info = _compile.cache_info()
        self.assertGreater(info.hits, 0, "lru_cache 应至少命中 1 次")

    def test_scope_returns_dict_not_dataclass(self):
        """返回的节点必须是 dict（不是 Paragraph 对象），便于 when 求值。"""
        doc = _make_doc([_make_para(style_id="Body Text")])
        result = evaluate_scope("paragraphs[?style_id == 'Body Text']", doc)
        self.assertEqual(len(result), 1)
        idx, node = result[0]
        self.assertIsInstance(node, dict)
        self.assertEqual(node["style_id"], "Body Text")

    def test_scope_index_injection_does_not_leak(self):
        """__index__ 是 evaluator 内部注入字段；不应影响后续 when 求值的 var 取值。"""
        doc = _make_doc([_make_para(style_id="Body Text")])
        result = evaluate_scope("paragraphs[*]", doc)
        idx, node = result[0]
        self.assertEqual(idx, 0)
        # node 包含 __index__ 是内部实现细节，但 var "__index__" 不应是规则作者会写的
        # 该测试只确认 __index__ 在 node 里能查到 idx
        self.assertEqual(node.get("__index__"), 0)

    def test_scope_can_select_header_paragraphs_with_container_index(self):
        header = _make_para(style_id="Header", text="paper title")
        doc = Document(
            sections=(),
            paragraphs=(),
            metadata=DocumentMetadata(title="test"),
            header_paragraphs=(header,),
        )

        result = evaluate_scope("header_paragraphs[*]", doc)

        self.assertEqual(len(result), 1)
        idx, node = result[0]
        self.assertEqual(idx, 0)
        self.assertEqual(node["__section__"], "header_paragraphs")
        self.assertEqual(node["text"], "paper title")


# ---------------------------------------------------------------------------
# evaluate_when — JSONLogic 单节点求值
# ---------------------------------------------------------------------------

class TestEvaluateWhen(unittest.TestCase):

    def test_when_equals_true(self):
        self.assertTrue(evaluate_when({"==": [1, 1]}, {}))

    def test_when_not_equals_with_var(self):
        node = {"line_spacing": 1.0}
        result = evaluate_when({"!=": [{"var": "line_spacing"}, 1.5]}, node)
        self.assertTrue(result, "1.0 != 1.5 should be True")

    def test_when_equals_with_var(self):
        node = {"line_spacing": 1.5}
        result = evaluate_when({"==": [{"var": "line_spacing"}, 1.5]}, node)
        self.assertTrue(result)

    def test_when_equals_allows_docx_roundtrip_float_drift(self):
        node = {"first_line_indent_cm": 0.741}
        result = evaluate_when(
            {"==": [{"var": "first_line_indent_cm"}, 0.74]},
            node,
        )
        self.assertTrue(result)

    def test_when_not_equals_allows_docx_roundtrip_float_drift(self):
        node = {"first_line_indent_cm": 0.741}
        result = evaluate_when(
            {"!=": [{"var": "first_line_indent_cm"}, 0.74]},
            node,
        )
        self.assertFalse(result)

    def test_when_and_true(self):
        node = {"x": 5, "y": "hi"}
        expr = {"and": [
            {">": [{"var": "x"}, 1]},
            {"==": [{"var": "y"}, "hi"]},
        ]}
        self.assertTrue(evaluate_when(expr, node))

    def test_when_or_true(self):
        node = {"x": 1}
        expr = {"or": [
            {">": [{"var": "x"}, 100]},
            {"==": [{"var": "x"}, 1]},
        ]}
        self.assertTrue(evaluate_when(expr, node))

    def test_when_not(self):
        self.assertTrue(evaluate_when({"!": [False]}, {}))

    def test_when_var_dotted_path(self):
        """``{"var": "font.eastAsia"}`` 应能访问嵌套 dict。"""
        node = {"font": {"eastAsia": "宋体"}}
        result = evaluate_when(
            {"==": [{"var": "font.eastAsia"}, "宋体"]},
            node,
        )
        self.assertTrue(result)

    def test_when_var_missing_returns_none(self):
        """缺失字段时 var 应返回 None（不抛错）；与 None 比较应不等。"""
        node = {"x": 1}
        result = evaluate_when({"==": [{"var": "nonexistent"}, "anything"]}, node)
        self.assertFalse(result)

    def test_when_var_runs_index(self):
        """``runs.0.font_eastasia`` 访问 list 内嵌套 dict。"""
        node = {"runs": [{"font_eastasia": "黑体"}, {"font_eastasia": "宋体"}]}
        result = evaluate_when(
            {"==": [{"var": "runs.0.font_eastasia"}, "黑体"]},
            node,
        )
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# 组合 — visitor 实际用法
# ---------------------------------------------------------------------------

class TestScopeAndWhenCombined(unittest.TestCase):

    def test_scope_then_when_combined(self):
        """模拟 visitor: scope filter paragraphs → when check 每个节点 → 收集违规。"""
        doc = _make_doc([
            _make_para(style_id="Body Text", line_spacing=1.0),    # 违规
            _make_para(style_id="Body Text", line_spacing=1.5),    # 合规
            _make_para(style_id="Heading 1", line_spacing=1.0),    # scope 过滤掉
        ])
        scope = "paragraphs[?style_id == 'Body Text']"
        when = {"!=": [{"var": "line_spacing"}, 1.5]}

        candidates = evaluate_scope(scope, doc)
        violations = [
            (idx, node) for idx, node in candidates
            if evaluate_when(when, node)
        ]
        self.assertEqual(len(violations), 1)
        idx, node = violations[0]
        self.assertEqual(idx, 0)
        self.assertEqual(node["line_spacing"], 1.0)


if __name__ == "__main__":
    unittest.main()
