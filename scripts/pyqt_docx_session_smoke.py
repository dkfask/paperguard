"""Smoke test for the PyQt-facing DOCX review session."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.docx_review_session import DocxReviewSession
from tests.synthetic_regression_samples import generate_samples


def _smoke_error_paths(output_dir: Path) -> None:
    """6 个错误路径验证（不弹真实 QMessageBox）。

    覆盖：
      1. open 不存在的文件  → FileNotFoundError
      2. open 损坏的 .docx   → ValueError "无法解析"
      3. open 非 .docx 文件  → ValueError "仅支持"
      4. apply_fix_at 越界   → IndexError "无效的修复编号"
      5. export 覆盖源文件   → ValueError "不会覆盖源文件"
      6. export 到无写权限目录 → PermissionError "目标目录无写入权限"
    """
    print("--- error paths ---")

    # 1. open 不存在的文件
    try:
        DocxReviewSession.open(output_dir / "nonexistent.docx")
    except FileNotFoundError as exc:
        assert "docx 文件不存在" in str(exc), f"#1 unexpected message: {exc}"
        print("  [1] open_nonexistent_file       -> FileNotFoundError OK")
    else:
        raise AssertionError("#1 expected FileNotFoundError")

    # 2. open 损坏的 .docx（写入非 docx 字节后改名 .docx）
    corrupt = output_dir / "corrupt.docx"
    corrupt.write_bytes(b"not a real docx, just plain text bytes")
    try:
        DocxReviewSession.open(corrupt)
    except ValueError as exc:
        assert "无法解析" in str(exc), f"#2 unexpected message: {exc}"
        print("  [2] open_corrupt_docx          -> ValueError OK")
    else:
        raise AssertionError("#2 expected ValueError")

    # 3. open 非 .docx 文件（.txt）
    txt_file = output_dir / "readme.txt"
    txt_file.write_text("hello world", encoding="utf-8")
    try:
        DocxReviewSession.open(txt_file)
    except ValueError as exc:
        assert "仅支持" in str(exc), f"#3 unexpected message: {exc}"
        print("  [3] open_non_docx_file         -> ValueError OK")
    else:
        raise AssertionError("#3 expected ValueError")

    # 4-6 需要一个有效的 session；复用 happy path 阶段生成的样本
    generated = generate_samples(output_dir)
    sample, source_path = next(
        item for item in generated if item[0].name == "gb7713-violating"
    )
    session = DocxReviewSession.open(source_path, pack_id=sample.pack_id)
    session.check()  # 填充 last_findings

    # 4. apply_fix_at 越界
    try:
        session.apply_fix_at(999)
    except IndexError as exc:
        assert "无效的修复编号" in str(exc), f"#4 unexpected message: {exc}"
        print("  [4] apply_fix_at_out_of_range  -> IndexError OK")
    else:
        raise AssertionError("#4 expected IndexError")

    # 5. export 覆盖源文件
    try:
        session.export(source_path)
    except ValueError as exc:
        assert "不会覆盖源文件" in str(exc), f"#5 unexpected message: {exc}"
        print("  [5] export_overwrite_source    -> ValueError OK")
    else:
        raise AssertionError("#5 expected ValueError")

    # 6. export 到无写权限目录（mock os.access 返回 False）
    bad_target = output_dir / "fixed.docx"
    with patch(
        "app.services.docx_review_session.os.access", return_value=False
    ):
        try:
            session.export(bad_target)
        except PermissionError as exc:
            assert "目标目录无写入权限" in str(exc), f"#6 unexpected message: {exc}"
            print("  [6] export_unwritable_dir      -> PermissionError OK")
        else:
            raise AssertionError("#6 expected PermissionError")


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

        # 6 个错误路径验证（在 happy path 之后跑）
        _smoke_error_paths(output_dir)

    print("=" * 60)
    print("OK PyQt DOCX review session smoke passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
