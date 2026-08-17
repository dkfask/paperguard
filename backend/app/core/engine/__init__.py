"""PaperGuard 规则引擎入口（Sprint 2 commit #10 + #13 落地）.

Sprint 2 完成后：``validate_docx`` 走 YAML pack pipeline：
``load_pack(pack) → visit_document(ir, rule_pack) → list[Finding]``。

Sprint 1.5 的硬编码 ``stub_engine.py`` 已在 commit #13 删除；
LiveFormatChecker 当前 ``from app.core.engine import validate_docx``
并默认 pack ``"GB-T-7713-1-2006"``（25 条 YAML 规则）。

所有引擎代码**严禁** import PyQt — app/core/ 是无 GUI 依赖的核心层。
"""

from __future__ import annotations

from pathlib import Path

from app.core.engine.loader import load_pack
from app.core.engine.visitor import visit_document
from app.core.ir import Document, Finding


def validate_docx(
    ir: Document,
    pack: str = "GB-T-7713-1-2006",
    packs_dir: Path | None = None,
) -> list[Finding]:
    """加载 YAML pack 并对 IR Document 跑全部规则，返回 Finding 列表。

    Args:
        ir: IR Document（不可变）
        pack: pack id，对应 ``{packs_dir}/{pack}.yaml`` 文件
              默认 ``"GB-T-7713-1-2006"``（GB/T 7713.1—2006 学位论文编写规则）
        packs_dir: pack 目录，默认 ``app/core/rules/packs/``（注入便于测试）

    Returns:
        ``list[Finding]``：按 (paragraph_index, rule_id) 排序

    Raises:
        FileNotFoundError: pack 文件不存在
        其他: load_pack / visit_document 内部抛出的异常（fail-fast）
    """
    rule_pack = load_pack(pack, packs_dir=packs_dir)
    return visit_document(ir, rule_pack)


__all__ = ["validate_docx"]
