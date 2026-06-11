"""Sprint 4 end-to-end smoke script."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from click.testing import CliRunner

from app.cli import cli


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "sample_paper.docx"


def run_cli(runner: CliRunner, *args: str):
    return runner.invoke(cli, list(args), catch_exceptions=False)


def main() -> int:
    print("=" * 60)
    print("Sprint 4 smoke")
    print("=" * 60)

    runner = CliRunner()

    print("[1/8] paperguard --version")
    result = run_cli(runner, "--version")
    assert result.exit_code == 0, result.output
    assert "0.1.0" in result.output

    print("[2/8] paperguard version")
    result = run_cli(runner, "version")
    assert result.exit_code == 0, result.output
    assert "PaperGuard 0.1.0" in result.output

    print("[3/8] paperguard doctor")
    result = run_cli(runner, "doctor")
    assert result.exit_code == 0, result.output
    assert "Network: skipped" in result.output

    print("[4/8] paperguard list-packs --format json")
    result = run_cli(runner, "list-packs", "--format", "json")
    assert result.exit_code == 0, result.output
    packs = json.loads(result.output)
    assert any(pack["id"] == "GB-T-7713-1-2006" for pack in packs)

    print("[5/8] paperguard check --format html")
    result = run_cli(runner, "check", str(FIXTURE), "--format", "html")
    assert result.exit_code in (0, 1), result.output
    assert "<!DOCTYPE html>" in result.output

    print("[6/8] paperguard check --format sarif")
    result = run_cli(runner, "check", str(FIXTURE), "--format", "sarif")
    assert result.exit_code in (0, 1), result.output
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"

    print("[7/8] paperguard check --output report.html")
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.html"
        result = run_cli(
            runner,
            "check",
            str(FIXTURE),
            "--format",
            "html",
            "--output",
            str(report_path),
        )
        assert result.exit_code in (0, 1), result.output
        assert report_path.exists()

    print("[8/8] paperguard fix writes changes.md")
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "fixed.docx"
        result = run_cli(runner, "fix", str(FIXTURE), "--output", str(output_path))
        assert result.exit_code in (0, 1), result.output
        assert output_path.exists()
        changes_path = Path(tmp) / "changes.md"
        assert changes_path.exists()
        assert "| Metric | Count |" in changes_path.read_text(encoding="utf-8")

    print("=" * 60)
    print("OK Sprint 4 smoke passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
