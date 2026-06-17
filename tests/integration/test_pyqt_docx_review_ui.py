"""Integration tests for the PyQt DOCX review UI flow."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
