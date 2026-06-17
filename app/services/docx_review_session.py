"""PyQt-facing DOCX review session built on the core pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.engine import validate_docx
from app.core.ir import Document, Finding
from app.core.loaders.docx_loader import load_docx
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes


@dataclass(frozen=True)
class DocxFixSummary:
    """Summary returned after applying available fixes in a DOCX session."""

    pre_fix_count: int
    auto_fixed_count: int
    skipped_count: int
    remaining_count: int
    remaining_rule_ids: frozenset[str]
    skipped_rule_ids: frozenset[str]


class DocxReviewSession:
    """Stateful review session for one imported ``.docx`` file.

    This service is intentionally UI-agnostic. PyQt can keep one session per
    opened file and bind buttons to ``check()``, ``apply_all_fixes()``, and
    ``export()`` without touching core loader/writer internals.
    """

    def __init__(self, source_path: Path, document: Document, pack_id: str) -> None:
        self.source_path = source_path
        self.document = document
        self.pack_id = pack_id
        self.last_findings: list[Finding] = []

    @classmethod
    def open(
        cls,
        source_path: str | Path,
        *,
        pack_id: str = "GB-T-7713-1-2006",
    ) -> "DocxReviewSession":
        path = Path(source_path)
        document = load_docx(path)
        return cls(source_path=path, document=document, pack_id=pack_id)

    def check(self) -> list[Finding]:
        self.last_findings = validate_docx(self.document, self.pack_id)
        return list(self.last_findings)

    def apply_all_fixes(self) -> DocxFixSummary:
        findings = self.check()
        fixed_document, skipped = apply_all_fixes(self.document, findings)
        self.document = fixed_document
        remaining = validate_docx(self.document, self.pack_id)
        self.last_findings = list(remaining)
        return DocxFixSummary(
            pre_fix_count=len(findings),
            auto_fixed_count=len(findings) - len(skipped),
            skipped_count=len(skipped),
            remaining_count=len(remaining),
            remaining_rule_ids=frozenset(finding.rule_id for finding in remaining),
            skipped_rule_ids=frozenset(finding.rule_id for finding in skipped),
        )

    def export(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        if path.resolve() == self.source_path.resolve():
            raise ValueError("不会覆盖源文件；请指定新的 .docx 输出路径")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_ir_to_docx(self.document, str(path))
        return path


__all__ = ["DocxFixSummary", "DocxReviewSession"]
