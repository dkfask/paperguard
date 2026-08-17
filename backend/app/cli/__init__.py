"""PaperGuard CLI — Click 8.x 框架入口.

Subcommands:
    paperguard check          — 跑 validate_docx, 输出 findings
    paperguard fix            — 应用 fix, 输出新文件
    paperguard extract        — 用 Sprint 2.5 extract 包
    paperguard init           — 生成空白 pack
    paperguard validate-pack  — schema 校验
    paperguard list-packs     — 列出内置规则包
    paperguard doctor         — 诊断本地环境
    paperguard version        — 显示版本 / 可选检查更新

Exit codes:
    0 — success (no findings)
    1 — has findings
    2 — error
"""

from app.cli.main import cli

__all__ = ["cli"]
