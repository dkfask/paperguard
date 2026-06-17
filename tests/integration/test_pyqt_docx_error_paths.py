"""Sprint 6: PyQt DOCX 6 个错误路径集成测试。

每个测试调用一个 ``_*_from_ui`` 入口，触发 show_error，
验证 ``QMessageBox.warning/critical`` 被调用且标题/正文含中文关键字。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.views.main_window import MainWindow  # noqa: E402
from tests.synthetic_regression_samples import generate_samples  # noqa: E402


class TestDocxReviewErrorPaths(unittest.TestCase):
    """6 个 from_ui 入口的错误路径覆盖。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    # 1. _import_docx_review_dialog 入口 → 损坏的 docx
    def test_open_corrupt_docx_shows_friendly_warning(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        corrupt_path = str(Path(self._tmp()) / "corrupt.docx")
        Path(corrupt_path).write_bytes(b"not a real docx bytes")
        with patch(
            "app.views.main_window.QFileDialog.getOpenFileName",
            return_value=(corrupt_path, ""),
        ), patch("app.views.common.QMessageBox.warning") as warning_box:
            window._import_docx_review_dialog()
            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            # ValueError 不在翻译表 → fallback "操作失败"
            self.assertEqual(args[1], "DOCX 导入 操作失败")
            self.assertIn("无法解析", args[2])

    # 2. _check_docx_review_from_ui 入口 → 任意异常
    def test_check_session_runtime_error_shows_warning(self) -> None:
        output_dir = Path(self._tmp())
        generated = generate_samples(output_dir)
        sample, source_path = next(
            item for item in generated if item[0].name == "gb7713-violating"
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window._open_docx_review_path(source_path, pack_id=sample.pack_id)
        with patch.object(
            window.docx_review_session,
            "check",
            side_effect=RuntimeError("engine crashed"),
        ), patch("app.views.common.QMessageBox.warning") as warning_box:
            window._check_docx_review_from_ui()
            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            self.assertEqual(args[1], "DOCX 检查 操作失败")
            self.assertIn("engine crashed", args[2])

    # 3. _fix_docx_review_from_ui 入口 → 任意异常
    def test_fix_session_runtime_error_shows_warning(self) -> None:
        output_dir = Path(self._tmp())
        generated = generate_samples(output_dir)
        sample, source_path = next(
            item for item in generated if item[0].name == "gb7713-violating"
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window._open_docx_review_path(source_path, pack_id=sample.pack_id)
        window._check_docx_review_session()
        with patch.object(
            window.docx_review_session,
            "apply_all_fixes",
            side_effect=RuntimeError("fix failed"),
        ), patch("app.views.common.QMessageBox.warning") as warning_box:
            window._fix_docx_review_from_ui()
            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            self.assertEqual(args[1], "DOCX 修复 操作失败")
            self.assertIn("fix failed", args[2])

    # 4. _fix_one_docx_review_from_ui 入口 → 越界 IndexError
    def test_fix_one_session_index_error_shows_warning(self) -> None:
        output_dir = Path(self._tmp())
        generated = generate_samples(output_dir)
        sample, source_path = next(
            item for item in generated if item[0].name == "gb7713-violating"
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window._open_docx_review_path(source_path, pack_id=sample.pack_id)
        window._check_docx_review_session()
        with patch("app.views.common.QMessageBox.warning") as warning_box:
            window._fix_one_docx_review_from_ui(999)
            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            # IndexError 在翻译表 → "索引越界"
            self.assertEqual(args[1], "DOCX 逐条修复 索引越界")
            self.assertIn("无效的修复编号", args[2])

    # 5. _locate_docx_review_finding_from_ui 入口 → 越界 IndexError
    def test_locate_session_index_error_shows_warning(self) -> None:
        output_dir = Path(self._tmp())
        generated = generate_samples(output_dir)
        sample, source_path = next(
            item for item in generated if item[0].name == "gb7713-violating"
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window._open_docx_review_path(source_path, pack_id=sample.pack_id)
        window._check_docx_review_session()
        with patch("app.views.common.QMessageBox.warning") as warning_box:
            window._locate_docx_review_finding_from_ui(999)
            warning_box.assert_called_once()
            args = warning_box.call_args[0]
            # _locate_docx_review_finding 抛 IndexError("DOCX 问题索引超出范围。")
            # IndexError 在翻译表 → "索引越界"
            self.assertEqual(args[1], "DOCX 定位 索引越界")
            self.assertIn("无效的修复编号", args[2])

    # 6. _export_docx_review_dialog 入口 → PermissionError
    def test_export_session_permission_error_shows_critical(self) -> None:
        output_dir = Path(self._tmp())
        generated = generate_samples(output_dir)
        sample, source_path = next(
            item for item in generated if item[0].name == "gb7713-violating"
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window._open_docx_review_path(source_path, pack_id=sample.pack_id)
        bad_path = str(output_dir / "subdir" / "fixed.docx")
        with patch(
            "app.views.main_window.QFileDialog.getSaveFileName",
            return_value=(bad_path, ""),
        ), patch(
            "app.services.docx_review_session.os.access",
            return_value=False,
        ), patch("app.views.common.QMessageBox.critical") as critical_box:
            window._export_docx_review_dialog("")
            critical_box.assert_called_once()
            args = critical_box.call_args[0]
            # PermissionError 在翻译表 → "权限不足" + critical
            self.assertEqual(args[1], "DOCX 导出 权限不足")
            self.assertIn(bad_path, args[2])

    # ---- helpers ----

    def _tmp(self) -> str:
        import tempfile

        if not hasattr(self, "_tmpdir"):
            self._tmpdir = tempfile.mkdtemp()
            self.addCleanup(self._cleanup_tmp)
        return self._tmpdir

    def _cleanup_tmp(self) -> None:
        import shutil

        if hasattr(self, "_tmpdir"):
            shutil.rmtree(self._tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
