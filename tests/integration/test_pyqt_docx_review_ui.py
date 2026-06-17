"""Integration tests for the PyQt DOCX review UI flow."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.core.engine import validate_docx  # noqa: E402
from app.core.loaders.docx_loader import load_docx  # noqa: E402
from app.views.main_window import MainWindow  # noqa: E402
from tests.synthetic_regression_samples import generate_samples  # noqa: E402


class TestPyQtDocxReviewUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_docx_review_flow_imports_checks_fixes_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)
            sample, source_path = next(
                item for item in generated if item[0].name == "gb7713-violating"
            )

            window = MainWindow()
            self.addCleanup(window.close)

            window._open_docx_review_path(source_path, pack_id=sample.pack_id)
            self.assertIsNotNone(window.docx_review_session)
            self.assertIn(source_path.name, window.review_workspace.docx_source_label.text())

            findings = window._check_docx_review_session()
            self.assertTrue(set(sample.expected_rule_ids).issubset({f.rule_id for f in findings}))
            self.assertIn("发现", window.review_workspace.docx_status_label.text())
            self.assertGreaterEqual(len(window.review_workspace.docx_issue_buttons), 1)
            self.assertTrue(
                any(
                    "PG-BODY-LINE-SPACING" in label.text()
                    for label in window.review_workspace.docx_issue_labels
                )
            )

            summary = window._fix_docx_review_session()
            self.assertEqual(summary.skipped_count, 0)
            self.assertTrue(set(sample.expected_rule_ids).isdisjoint(summary.remaining_rule_ids))
            self.assertIn("自动修复", window.review_workspace.docx_status_label.text())

            fixed_path = output_dir / "ui-fixed.docx"
            exported = window._export_fixed_docx_review_path(fixed_path)
            self.assertEqual(exported, fixed_path)
            self.assertTrue(fixed_path.exists())
            self.assertIn(fixed_path.name, window.review_workspace.docx_export_label.text())
            self.assertIn("导出完成", window.review_workspace.docx_export_summary_label.text())
            self.assertIn(fixed_path.name, window.review_workspace.docx_export_summary_label.text())
            self.assertIn("剩余问题：0", window.review_workspace.docx_export_summary_label.text())
            self.assertIn("时间：", window.review_workspace.docx_export_summary_label.text())

            reloaded = load_docx(fixed_path)
            remaining = {finding.rule_id for finding in validate_docx(reloaded, sample.pack_id)}
            self.assertTrue(remaining.isdisjoint(sample.expected_rule_ids))

    def test_main_window_docx_review_export_requires_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow()
            self.addCleanup(window.close)

            with self.assertRaisesRegex(ValueError, "请先导入"):
                window._export_fixed_docx_review_path(Path(tmp) / "fixed.docx")

    def test_main_window_docx_review_can_fix_one_rendered_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)
            sample, source_path = next(
                item for item in generated if item[0].name == "sim-univ-violating"
            )

            window = MainWindow()
            self.addCleanup(window.close)
            window._open_docx_review_path(source_path, pack_id=sample.pack_id)
            findings = window._check_docx_review_session()
            self.assertTrue(findings)
            self.assertEqual(len(window.review_workspace.docx_issue_buttons), len(findings))

            window.review_workspace.docx_issue_buttons[0].click()
            self.app.processEvents()

            remaining_rule_ids = {
                finding.rule_id for finding in window.docx_review_session.last_findings
            }
            self.assertTrue(remaining_rule_ids.isdisjoint(sample.expected_rule_ids))
            self.assertEqual(window.review_workspace.docx_issue_buttons, [])
            self.assertTrue(window.review_workspace.btn_docx_export.isEnabled())

    def test_main_window_docx_review_can_locate_rendered_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)
            sample, source_path = next(
                item for item in generated if item[0].name == "gb7713-violating"
            )

            window = MainWindow()
            self.addCleanup(window.close)
            window._open_docx_review_path(source_path, pack_id=sample.pack_id)
            findings = window._check_docx_review_session()
            self.assertTrue(findings)
            self.assertEqual(len(window.review_workspace.docx_locate_buttons), len(findings))

            window.review_workspace.docx_locate_buttons[0].click()
            self.app.processEvents()

            self.assertEqual(window.current_docx_review_finding_index, 0)
            self.assertIn("第 1 段", window.review_workspace.docx_location_label.text())
            self.assertIn(findings[0].location.text_snippet, window.review_workspace.docx_location_label.text())
            self.assertIn("已定位", window.status_label.text())

    # --- 异常路径：show_error 接入 (Sprint 6) ---

    def test_main_window_docx_review_open_nonexistent_shows_friendly_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow()
            self.addCleanup(window.close)
            fake_path = str(Path(tmp) / "nonexistent.docx")
            with patch(
                "app.views.main_window.QFileDialog.getOpenFileName",
                return_value=(fake_path, ""),
            ), patch(
                "app.views.main_window.DocxReviewSession.open",
                side_effect=FileNotFoundError(f"docx 文件不存在: {fake_path}"),
            ), patch("app.views.common.QMessageBox.warning") as warning_box:
                window._import_docx_review_dialog()
                warning_box.assert_called_once()
                args = warning_box.call_args[0]
                self.assertEqual(args[1], "DOCX 导入 文件未找到")
                self.assertIn(fake_path, args[2])

    def test_main_window_docx_review_check_without_session_shows_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow()
            self.addCleanup(window.close)
            # docx_review_session 默认为 None → ValueError
            with patch("app.views.common.QMessageBox.warning") as warning_box:
                window._check_docx_review_from_ui()
                warning_box.assert_called_once()
                args = warning_box.call_args[0]
                # ValueError 不在翻译表 → 走 fallback
                self.assertEqual(args[1], "DOCX 检查 操作失败")
                self.assertIn("请先导入", args[2])

    def test_main_window_docx_review_export_to_unwritable_shows_critical(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
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
                self.assertEqual(args[1], "DOCX 导出 权限不足")
                self.assertIn(bad_path, args[2])

    def test_main_window_docx_review_error_label_appears_on_error(self):
        """error label 在 error path 后文字含关键信息。"""
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow()
            self.addCleanup(window.close)
            with patch("app.views.common.QMessageBox.warning"):
                # 触发 check without session → ValueError
                window._check_docx_review_from_ui()
            # error label 文字含"DOCX 检查"和"请先导入"
            label_text = window.review_workspace._docx_error_label.text()
            self.assertIn("DOCX 检查", label_text)
            self.assertIn("请先导入", label_text)

    def test_main_window_docx_review_error_label_clears_on_success(self):
        """happy path 调 set_docx_review_state 后 error label 文字清空。"""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)
            sample, source_path = next(
                item for item in generated if item[0].name == "gb7713-violating"
            )
            window = MainWindow()
            self.addCleanup(window.close)
            # 先设个错误状态
            window.review_workspace.set_docx_error_state("之前的错误")
            self.assertIn("之前的错误", window.review_workspace._docx_error_label.text())
            # happy path：open 成功，set_docx_review_state 自动清错误
            window._open_docx_review_path(source_path, pack_id=sample.pack_id)
            self.assertEqual(window.review_workspace._docx_error_label.text(), "")


if __name__ == "__main__":
    unittest.main()
