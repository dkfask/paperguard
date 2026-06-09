"""PaperGuard 模板抽取层（Sprint 2.5）.

把用户上传的 docx 模板抽取为格式约束 Requirement[] → YAML pack 骨架。

所有抽取代码**严禁** import PyQt — app/core/ 是无 GUI 依赖的核心层。

Based on format-thesis-docx (MIT License, Copyright (c) 2026 manbo)
— borrowed FORMAT_WORDS heuristic + 7 category 设计；实现完全重写。
"""
