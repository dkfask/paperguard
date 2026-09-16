"""Prepare legacy binary Word documents for the DOCX-based parsers."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class LegacyWordConversionError(RuntimeError):
    """Raised when a legacy ``.doc`` cannot be converted safely."""


@dataclass(frozen=True)
class PreparedDocx:
    """A parser-ready DOCX path and the filename used by the parser."""

    path: Path
    parser_filename: str


def _find_converter() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def prepare_docx(data: bytes, filename: str, workdir: Path) -> PreparedDocx:
    """Write a DOCX directly or convert a legacy DOC with LibreOffice.

    The conversion runs in an isolated temporary LibreOffice profile and
    without a shell so uploaded filenames cannot become command arguments.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    source_name = Path(filename or "document").name
    suffix = Path(source_name).suffix.lower()
    if suffix == ".docx":
        path = workdir / "document.docx"
        path.write_bytes(data)
        return PreparedDocx(path=path, parser_filename=source_name)
    if suffix != ".doc":
        raise LegacyWordConversionError("只支持 .doc 或 .docx 文件")

    converter = _find_converter()
    if not converter:
        raise LegacyWordConversionError(
            "服务器暂未安装旧版 Word 文档转换组件，请将 .doc 另存为 .docx 后重试"
        )

    source = workdir / "source.doc"
    output_dir = workdir / "converted"
    profile_dir = workdir / "libreoffice-profile"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)

    command = [
        converter,
        "--headless",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--norestore",
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--convert-to",
        "docx",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LegacyWordConversionError("旧版 Word 文档转换失败，请改用 .docx 文件") from exc

    converted = output_dir / "source.docx"
    if completed.returncode != 0 or not converted.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail[:240]}" if detail else ""
        raise LegacyWordConversionError(f"旧版 Word 文档转换失败，请改用 .docx 文件{suffix}")

    return PreparedDocx(
        path=converted,
        parser_filename=f"{Path(source_name).stem}.docx",
    )


def convert_doc_bytes(data: bytes, filename: str, workdir: Path) -> tuple[bytes, str]:
    """Return parser-ready bytes and a parser filename for DOC/DOCX input."""
    prepared = prepare_docx(data, filename, workdir)
    return prepared.path.read_bytes(), prepared.parser_filename


__all__ = ["LegacyWordConversionError", "PreparedDocx", "convert_doc_bytes", "prepare_docx"]
