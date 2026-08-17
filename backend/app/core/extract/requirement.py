"""Requirement IR — 从 docx 模板抽取的一条格式约束.

字段与参考项目 add_item() (extract_requirements.py:131-143) 对齐，
便于互相迁移。设计参考 docs/design-proposal.md §3.2 + Sprint 2.5 plan §3.1。

7 categories（REQUIREMENT_CATEGORIES）：
    style                — Word 样式定义（styles.xml）
    section              — 节属性（页边距/纸张/方向）
    paragraph-example    — 模板内段落样例（提供 fix 参考值）
    table                — 表格样式
    numbering            — 列表/编号
    explicit-instruction — FORMAT_WORDS 命中的自然语言指令段
    document             — 文档级元数据（title/author 等）
"""

from __future__ import annotations

from dataclasses import dataclass


REQUIREMENT_CATEGORIES: tuple[str, ...] = (
    "style",
    "section",
    "paragraph-example",
    "table",
    "numbering",
    "explicit-instruction",
    "document",
)


@dataclass(frozen=True)
class Requirement:
    """从 docx 模板抽取的一条格式约束（不可变）。

    Attributes:
        id: 唯一标识，形如 ``R0001`` 4 位序号（与参考项目一致）
        category: 7 categories 之一
        target: 定位字符串，如 ``"style:Normal"`` / ``"section:1"`` / ``"paragraph-example:word/document.xml:3"``
        property: 点分属性名，与 IR v1.1 约定一致，如 ``"font.eastAsia"`` / ``"paragraph.spacing.line"``
        expected: 期望值（强制 str 存储，调用方按 property 类型解释，如 half-points 需 /2）
        source_part: OOXML part 路径，如 ``"word/styles.xml"`` / ``"word/document.xml"``
        evidence: 证据文本（前 300 字符），用于人工审查与报告
        auto_applicable: 该规则是否能自动 fix。MVP True；V1+ 按 category 细化
        fix: 候选 Fix 动作（可选）。explicit-instruction 等不可自动 fix 的为 None
    """

    id: str
    category: str
    target: str
    property: str
    expected: str
    source_part: str
    evidence: str
    auto_applicable: bool = True
    fix: "Fix | None" = None  # forward ref to avoid circular import


__all__ = ["REQUIREMENT_CATEGORIES", "Requirement"]
