"""Smoke test for the PyQt-facing DOCX review session."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.docx_review_session import DocxReviewSession
from tests.synthetic_regression_samples import generate_samples


def main() -> int:
    print("=" * 60)
    print("PyQt DOCX review session smoke")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        generated = generate_samples(output_dir)
        for sample, source_path in generated:
            if sample.state != "violating":
                continue

            session = DocxReviewSession.open(source_path, pack_id=sample.pack_id)
            findings = session.check()
            rule_ids = {finding.rule_id for finding in findings}
            expected = set(sample.expected_rule_ids)
            assert expected.issubset(rule_ids), (
                f"{sample.name} missing expected findings: {expected - rule_ids}"
            )

            summary = session.apply_all_fixes()
            assert summary.skipped_count == 0, (
                f"{sample.name} skipped fixes: {sorted(summary.skipped_rule_ids)}"
            )
            assert expected.isdisjoint(summary.remaining_rule_ids), (
                f"{sample.name} still has expected findings: "
                f"{expected & set(summary.remaining_rule_ids)}"
            )

            fixed_path = output_dir / f"{sample.name}.session-fixed.docx"
            session.export(fixed_path)
            assert fixed_path.exists(), fixed_path
            print(
                f"{sample.name:22s} {sample.pack_id:18s} "
                f"fixed={summary.auto_fixed_count:<2d} "
                f"remaining={summary.remaining_count:<2d}"
            )

    print("=" * 60)
    print("OK PyQt DOCX review session smoke passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
