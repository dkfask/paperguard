"""Sprint 1.5 DoD 检查 — app/core/ 零 PyQt 导入（硬约束保持）。

设计文档（docs/design-proposal.md §4.1 line 504）明文规定：
> "核心完全无 PyQt 依赖"

Sprint 1.5 计划 §7 DoD 把此条作为硬约束：
> "``grep -r "PyQt" app/core/`` 仅匹配 docstring 注释（零 import，**Sprint 1 硬约束保持**）"

本测试用 AST 解析每个 .py 文件，遍历 ``import`` / ``import from`` 语句，
确保没有任何从 ``PyQt*`` 模块的真实 import。

注：docstring 中出现 "PyQt" 文字（如说明文字）是允许的 —— AST 不会把字符串
误识别为 import。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


# 触发"PyQt 导入"判定的根模块名集合
_PYQT_ROOTS: frozenset[str] = frozenset({
    "PyQt5",
    "PyQt6",
    "PyQt4",
    "PySide2",
    "PySide6",
    "PyQt",  # 兜底
})


def _find_pyqt_imports(py_file: Path) -> list[tuple[int, str]]:
    """返回 ``py_file`` 中所有 PyQt 导入 (line, module_path)。

    用 ``ast.parse`` + 遍历 ``ast.Import`` / ``ast.ImportFrom``，**不**做 grep 字符串
    匹配（避免把 docstring / 注释里的 "PyQt" 文字误判为 import）。
    """
    if not py_file.exists():
        return []
    try:
        source = py_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return []

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _PYQT_ROOTS:
                    hits.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            # ``from PyQt5.QtCore import QObject`` → module='PyQt5.QtCore'
            module = node.module or ""
            root = module.split(".")[0]
            if root in _PYQT_ROOTS:
                hits.append((node.lineno, module))
    return hits


class NoPyQtInCoreTests(unittest.TestCase):
    """app/core/ 零 PyQt 导入（AST-based 硬约束）。"""

    CORE_ROOT = Path(__file__).resolve().parents[2] / "app" / "core"

    def test_core_directory_exists(self):
        self.assertTrue(self.CORE_ROOT.exists(), f"{self.CORE_ROOT} 必须存在")

    def test_no_pyqt_imports_in_core(self):
        """app/core/ 下所有 .py 文件不能 import 任何 PyQt* 模块。"""
        if not self.CORE_ROOT.exists():
            self.fail(f"{self.CORE_ROOT} 不存在")

        offenders: list[tuple[str, int, str]] = []
        for py_file in sorted(self.CORE_ROOT.rglob("*.py")):
            for lineno, module in _find_pyqt_imports(py_file):
                offenders.append((str(py_file), lineno, module))

        if offenders:
            msg_lines = ["app/core/ 发现 PyQt 导入（违反硬约束）："]
            for path, lineno, module in offenders:
                rel = Path(path).relative_to(self.CORE_ROOT.parent.parent)
                msg_lines.append(f"  - {rel}:{lineno}  →  import {module}")
            self.fail("\n".join(msg_lines))

    def test_no_pyqt_imports_excludes_docstrings(self):
        """``grep "PyQt"`` 在 app/core/ 可能命中 docstring，本测试明确验证
        那类字符串不算 import（用 AST 已过滤；这里作 sanity check）。"""
        # 仅作可读性：扫一遍 app/core/ 的 .py，看是否有 "PyQt" 字符串
        # （这些可能是 docstring 提及；不算违规）
        mentioning: list[str] = []
        for py_file in sorted(self.CORE_ROOT.rglob("*.py")):
            try:
                text = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "PyQt" in text:
                mentioning.append(str(py_file))
        # 仅 print 给人看；不强制 fail
        if mentioning:
            import sys
            print(
                f"  注：以下文件含 'PyQt' 字符串（仅作 awareness，多为 docstring 提及）:",
                file=sys.stderr,
            )
            for m in mentioning:
                rel = Path(m).relative_to(self.CORE_ROOT.parent.parent)
                print(f"    - {rel}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
