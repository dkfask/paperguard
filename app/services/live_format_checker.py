"""Live format checker — QObject + QTimer debounce + Qt signal。

Sprint 1.5 + 2 实时约束核心：把 ``Project`` 模型的某个 section 转成 IR → YAML pack
engine（``app.core.engine.validate_docx``，Sprint 2 commit #13 后走 GB-T-7713-1-2006 25 规则）
→ 投影成 ``QtIssue[]`` → Qt signal 推到 UI。

设计要点（参考 docs/sprints/sprint-1.5-plan.md §3 + §4 ADR-2/5 + sprint-2-plan.md C8）：
- 500ms 固定 debounce（Q3 决策"自适应"被用户推翻，回退为固定 500ms）。
- QTimer.setSingleShot(True) + start(debounce_ms) 实现 debounce：
  学生每按一键 reset timer，0.5s 内未继续输入则触发 ``_run_check``。
- 全主线程（Qt event loop）—— 同步 ``project_to_ir`` + ``engine.validate``；
  失败容忍（``try/except`` → ``check_failed.emit``）。
- Fix 缓存 ``dict[(rule_id, paragraph_index), Fix]`` 供 ``LiveMutator`` 反查；
  ``no_issues`` 时清空。
- QObject 生命周期：``QTimer(self)`` → checker 销毁时 timer 自动清理。

Public API:
    LiveFormatChecker   QObject 子类，发 3 个 Qt signal
    request_check()     启动/重置 debounce timer（ContentEditor.textChanged 调）
    get_fix(issue)      根据 QtIssue 反查原始 Fix（LiveMutator 调）
    set_section_id(id)  显式设置要检查的 section（ContentEditor 切换时调）
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from app.core.engine import validate_docx
from app.core.ir.finding import Fix
from app.core.loaders.project_to_ir import project_to_ir
from app.core.reporters.pyqt_reporter import QtIssue, to_qt_issues

logger = logging.getLogger(__name__)


class LiveFormatChecker(QObject):
    """实时格式检查器：QObject + QTimer debounce + Qt signal。

    Signals:
        issues_found(list):  发现 issues 时发，payload 是 ``list[QtIssue]``
        no_issues():         全合规时发（无 issues）
        check_failed(str):   引擎/转换异常时发（失败容忍，不抛给 UI）
    """

    issues_found = pyqtSignal(list)
    no_issues = pyqtSignal()
    check_failed = pyqtSignal(str)

    def __init__(
        self,
        project: Any,
        pack_name: str = "GB-T-7713-1-2006",
        debounce_ms: int = 500,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._pack_name = pack_name
        self._debounce_ms = debounce_ms
        # 自动选择 project 的第一个 section；可用 set_section_id() 显式切换
        section_keys = list(project.sections.keys())
        self._section_id: str | None = section_keys[0] if section_keys else None
        # Fix 缓存：key = (rule_id, paragraph_index)；no_issues 时清空
        self._fix_cache: dict[tuple[str, int], Fix] = {}
        # QTimer（parent=self → checker 销毁时自动清理）
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._run_check)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def set_section_id(self, section_id: str) -> None:
        """显式设置要检查的 section（ContentEditor 切换 section 时调用）。"""
        self._section_id = section_id

    def request_check(self) -> None:
        """学生每次按键调用：start/restart debounce timer。"""
        self._timer.start(self._debounce_ms)

    def get_fix(self, issue: QtIssue) -> Fix | None:
        """根据 QtIssue 反查原始 Fix（供 LiveMutator 用）。

        缓存 key 是 ``(issue.rule_id, issue.paragraph_index)``。
        未知 issue / cache 为空（如 no_issues 后被清空）→ 返回 ``None``。
        """
        return self._fix_cache.get((issue.rule_id, issue.paragraph_index))

    # ------------------------------------------------------------------
    # 内部：timer 触发后同步跑 check
    # ------------------------------------------------------------------
    def _run_check(self) -> None:
        try:
            if self._section_id is None:
                # 无 section 可检查 → 当作 no_issues
                self._fix_cache = {}
                self.no_issues.emit()
                return
            ir = project_to_ir(self._project, self._section_id)
            findings = validate_docx(ir, self._pack_name)
        except Exception as exc:  # noqa: BLE001 — 失败容忍，UI 不崩
            logger.exception("LiveFormatChecker 跑 check 失败")
            self.check_failed.emit(f"{type(exc).__name__}: {exc}")
            return

        issues = to_qt_issues(findings)
        if issues:
            # 重建 fix 缓存（key: (rule_id, paragraph_index)）
            self._fix_cache = {
                (f.rule_id, f.location.paragraph_index): f.fix
                for f in findings
                if f.fix is not None and f.location.paragraph_index is not None
            }
            self.issues_found.emit(issues)
        else:
            # 合规 → 清空缓存（避免 stale fix 被 Mutator 误用）
            self._fix_cache = {}
            self.no_issues.emit()


__all__ = ["LiveFormatChecker"]
