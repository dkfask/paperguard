"""Generate and validate synthetic regression samples."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.engine import validate_docx
from app.core.loaders.docx_loader import load_docx
from tests.synthetic_regression_samples import generate_samples


def main() -> int:
    print("=" * 60)
    print("Synthetic regression samples smoke")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        generated = generate_samples(Path(tmp))
        assert len(generated) == 9, f"expected 9 generated samples, got {len(generated)}"
        for sample, path in generated:
            loaded = load_docx(path)
            rule_ids = {finding.rule_id for finding in validate_docx(loaded, sample.pack_id)}
            expected = set(sample.expected_rule_ids)
            if sample.state == "violating":
                assert expected.issubset(rule_ids), (
                    f"{sample.name} missing expected findings: {expected - rule_ids}"
                )
                status = "violates expected rules"
            else:
                assert rule_ids.isdisjoint(expected), (
                    f"{sample.name} has unexpected findings: {rule_ids & expected}"
                )
                status = "clean for expected rules"
            print(f"{sample.name:22s} {sample.pack_id:18s} {sample.state:10s} {status}")

    print("=" * 60)
    print("OK synthetic regression samples smoke passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
