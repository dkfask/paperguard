"""Sprint 5 mutator maturity smoke script."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document as DocxDocument

from app.core.engine import validate_docx
from app.core.ir import Document, DocumentMetadata, Paragraph, Run, Section
from app.core.loaders.docx_loader import load_docx
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes


def _rule_ids(findings) -> set[str]:
    return {finding.rule_id for finding in findings}


def _assert_rule_fixed(ir: Document, pack_id: str, rule_id: str, output_name: str) -> None:
    findings = validate_docx(ir, pack_id)
    assert rule_id in _rule_ids(findings), f"{rule_id} did not trigger before fix"
    fixed, skipped = apply_all_fixes(ir, findings)
    assert not skipped, f"unexpected skipped fixes: {[f.rule_id for f in skipped]}"

    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / output_name
        write_ir_to_docx(fixed, str(output_path))
        loaded = load_docx(output_path)
        if rule_id == "PG-TABLE-CELL-FONT-SIZE":
            assert len(DocxDocument(output_path).tables) == 1

    remaining = validate_docx(loaded, pack_id)
    assert rule_id not in _rule_ids(remaining), f"{rule_id} remained after roundtrip"


def main() -> int:
    print("=" * 60)
    print("Sprint 5 smoke")
    print("=" * 60)

    print("[1/4] GB/T 7713 multi-run body formatting")
    body_ir = Document(
        sections=(Section(),),
        paragraphs=(
            Paragraph(
                style_id="Body Text",
                text="good bad",
                level=None,
                runs=(
                    Run(text="good ", font_eastasia="宋体", font_ascii="Times New Roman", font_size_pt=12.0),
                    Run(text="bad", font_eastasia="Arial", font_ascii="Arial", font_size_pt=10.5),
                ),
                line_spacing=1.5,
                space_before_pt=0.0,
                space_after_pt=0.0,
                first_line_indent_cm=0.74,
                alignment="justify",
            ),
        ),
        metadata=DocumentMetadata(title="sprint5 body"),
    )
    _assert_rule_fixed(body_ir, "GB-T-7713-1-2006", "PG-BODY-FONT-SIZE", "body.docx")

    print("[2/4] GB/T 7714 reference hanging indent")
    reference_ir = Document(
        sections=(Section(),),
        paragraphs=(
            Paragraph(
                style_id="Bibliography",
                text="[1] Paper.",
                level=None,
                runs=(Run(text="[1] Paper.", font_eastasia="宋体", font_ascii="Times New Roman", font_size_pt=10.5),),
                line_spacing=1.5,
                space_before_pt=0.0,
                space_after_pt=0.0,
                first_line_indent_cm=0.0,
                hanging_indent_cm=0.0,
                alignment="left",
            ),
        ),
        metadata=DocumentMetadata(title="sprint5 reference"),
    )
    _assert_rule_fixed(reference_ir, "GB-T-7714-1-2015", "PG-REF-HANGING-INDENT", "reference.docx")

    print("[3/4] SIM-UNIV table cell formatting")
    table_ir = Document(
        sections=(Section(),),
        paragraphs=(
            Paragraph(
                style_id="Table Text",
                text="cell",
                level=None,
                runs=(Run(text="cell", font_size_pt=12.0),),
                line_spacing=1.0,
                space_before_pt=0.0,
                space_after_pt=0.0,
                first_line_indent_cm=0.0,
                table_index=0,
                table_row_index=0,
                table_cell_index=0,
            ),
        ),
        metadata=DocumentMetadata(title="sprint5 table"),
    )
    _assert_rule_fixed(table_ir, "SIM-UNIV-generic", "PG-TABLE-CELL-FONT-SIZE", "table.docx")

    print("[4/4] SIM-UNIV list paragraph formatting")
    list_ir = Document(
        sections=(Section(),),
        paragraphs=(
            Paragraph(
                style_id="List Number",
                text="item",
                level=None,
                runs=(Run(text="item"),),
                line_spacing=1.0,
                space_before_pt=0.0,
                space_after_pt=0.0,
                first_line_indent_cm=0.0,
                list_type="number",
                list_level=0,
            ),
        ),
        metadata=DocumentMetadata(title="sprint5 list"),
    )
    _assert_rule_fixed(list_ir, "SIM-UNIV-generic", "PG-LIST-LINE-SPACING", "list.docx")

    print("=" * 60)
    print("OK Sprint 5 smoke passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
