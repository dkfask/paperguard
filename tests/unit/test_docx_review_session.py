"""Tests for the PyQt-facing DOCX review session service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.engine import validate_docx
from app.core.loaders.docx_loader import load_docx
from app.services.docx_review_session import DocxReviewSession
from tests.synthetic_regression_samples import generate_samples


class TestDocxReviewSession(unittest.TestCase):
    def test_open_check_fix_export_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)
            sample, source_path = next(
                (item for item in generated if item[0].name == "gb7713-violating")
            )

            session = DocxReviewSession.open(source_path, pack_id=sample.pack_id)
            findings = session.check()
            rule_ids = {finding.rule_id for finding in findings}
            self.assertTrue(set(sample.expected_rule_ids).issubset(rule_ids))

            summary = session.apply_all_fixes()
            self.assertGreater(summary.pre_fix_count, 0)
            self.assertGreater(summary.auto_fixed_count, 0)
            self.assertEqual(summary.skipped_count, 0)
            self.assertTrue(set(sample.expected_rule_ids).isdisjoint(summary.remaining_rule_ids))

            fixed_path = output_dir / "gb7713-violating.fixed.docx"
            exported = session.export(fixed_path)
            self.assertEqual(exported, fixed_path)
            self.assertTrue(fixed_path.exists())

            reloaded = load_docx(fixed_path)
            remaining = {finding.rule_id for finding in validate_docx(reloaded, sample.pack_id)}
            self.assertTrue(remaining.isdisjoint(sample.expected_rule_ids))

    def test_export_refuses_to_overwrite_source_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)
            sample, source_path = next(
                (item for item in generated if item[0].name == "gb7714-violating")
            )
            session = DocxReviewSession.open(source_path, pack_id=sample.pack_id)

            with self.assertRaisesRegex(ValueError, "不会覆盖源文件"):
                session.export(source_path)


if __name__ == "__main__":
    unittest.main()
