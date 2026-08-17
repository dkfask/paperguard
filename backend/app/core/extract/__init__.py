"""PaperGuard 模板抽取层（Sprint 2.5）.

把用户上传的 docx 模板抽取为格式约束 Requirement[] → YAML pack 骨架。

所有抽取代码**严禁** import PyQt — app/core/ 是无 GUI 依赖的核心层。

Based on format-thesis-docx (MIT License, Copyright (c) 2026 manbo)
— borrowed FORMAT_WORDS heuristic + 7 category 设计；实现完全重写。

Public API:
    Requirement                — frozen dataclass 抽取出的格式约束
    REQUIREMENT_CATEGORIES     — 7 类常量
    extract_template(path)     — 从 docx 抽 Requirement[]
    requirements_to_pack(...)  — Requirement[] → RulePack (兼容 Sprint 2 loader)
"""

from app.core.extract.requirement import REQUIREMENT_CATEGORIES, Requirement
from app.core.extract.extractor import extract_template
from app.core.extract.to_pack import requirements_to_pack

__all__ = [
    "REQUIREMENT_CATEGORIES",
    "Requirement",
    "extract_template",
    "requirements_to_pack",
]

