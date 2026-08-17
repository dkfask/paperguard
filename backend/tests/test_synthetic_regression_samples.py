"""Regression tests for synthetic PaperGuard sample documents."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.engine import validate_docx
from app.core.loaders.docx_loader import load_docx
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes
from tests.synthetic_regression_samples import SAMPLE_SPECS, generate_samples


class TestSyntheticRegressionSamples(unittest.TestCase):
    def test_sample_matrix_has_three_states_for_each_pack(self):
        matrix = {(sample.pack_id, sample.state) for sample in SAMPLE_SPECS}
        self.assertEqual(len(SAMPLE_SPECS), 9)
        for pack_id in ("GB-T-7713-1-2006", "GB-T-7714-1-2015", "SIM-UNIV-generic"):
            self.assertIn((pack_id, "compliant"), matrix)
            self.assertIn((pack_id, "violating"), matrix)
            self.assertIn((pack_id, "fixed"), matrix)

    def test_generated_samples_validate_with_expected_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)

            self.assertEqual(len(generated), 9)
            for sample, path in generated:
                self.assertTrue(path.exists(), str(path))
                loaded = load_docx(path)
                rule_ids = {finding.rule_id for finding in validate_docx(loaded, sample.pack_id)}
                if sample.state in ("compliant", "fixed"):
                    self.assertTrue(
                        rule_ids.isdisjoint(sample.expected_rule_ids),
                        f"{sample.name} unexpectedly has {rule_ids & sample.expected_rule_ids}",
                    )
                else:
                    self.assertTrue(
                        set(sample.expected_rule_ids).issubset(rule_ids),
                        f"{sample.name} missing {set(sample.expected_rule_ids) - rule_ids}",
                    )

    def test_violating_samples_can_be_fixed_through_docx_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            generated = generate_samples(output_dir)

            for sample, path in generated:
                if sample.state != "violating":
                    continue

                loaded = load_docx(path)
                findings = validate_docx(loaded, sample.pack_id)
                fixed, skipped = apply_all_fixes(loaded, findings)
                self.assertEqual(
                    [finding.rule_id for finding in skipped if finding.rule_id in sample.expected_rule_ids],
                    [],
                    sample.name,
                )

                fixed_path = output_dir / f"{path.stem}.roundtrip.docx"
                write_ir_to_docx(fixed, str(fixed_path))
                reloaded = load_docx(fixed_path)
                remaining = {finding.rule_id for finding in validate_docx(reloaded, sample.pack_id)}
                self.assertTrue(
                    remaining.isdisjoint(sample.expected_rule_ids),
                    f"{sample.name} still has {remaining & sample.expected_rule_ids}",
                )


if __name__ == "__main__":
    unittest.main()
