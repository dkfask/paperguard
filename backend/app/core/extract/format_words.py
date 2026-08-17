"""FORMAT_WORDS 启发式 — Sprint 2.5 commit #5.

从 docx 模板段落文本检测"显式格式说明文字"，命中段落被标记为
``category="explicit-instruction"`` Requirement（人工审查类）。

设计参考：
- 参考项目 ``references/format-thesis-docx/scripts/extract_requirements.py:20-24``
  （32 词词典；MIT 协议，借鉴重写）
- 本项目 ADR-4 A：硬编码词典（V1+ 升级 YAML 配置）
- 总词典 34 词 = 英文 11 + 中文 23

公共 API:
    FORMAT_WORDS          编译后的 re.Pattern
    match_format_words    判断段落文本是否包含格式说明词
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# 词典 — 34 词（英文 11 + 中文 23）
# ---------------------------------------------------------------------------

FORMAT_WORDS = re.compile(
    r"(font|size|spacing|margin|header|footer|page|caption|reference|format|requirement|"
    r"标题|字体|字号|行距|缩进|页眉|页脚|页码|格式|要求|示例|不得|应|须|按|"
    r"目录|图|表|公式|参考文献|摘要|关键词|附录)",
    re.IGNORECASE,
)


def match_format_words(text: str) -> bool:
    """检测 ``text`` 是否包含任一 FORMAT_WORDS 关键词。

    Args:
        text: 段落文本（可空）

    Returns:
        ``True`` 命中至少 1 个关键词；空字符串或无命中 → ``False``
    """
    if not text:
        return False
    return bool(FORMAT_WORDS.search(text))


__all__ = ["FORMAT_WORDS", "match_format_words"]
