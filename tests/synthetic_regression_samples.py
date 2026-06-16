"""Synthetic regression sample generator for PaperGuard.

The samples are generated from IR instead of committed as real papers. They are
safe to share and stable enough for pack-level regression tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.engine import validate_docx
from app.core.ir import Document, DocumentMetadata, Paragraph, Run, Section
from app.core.writers.docx_writer import write_ir_to_docx
from app.core.writers.fix_applier import apply_all_fixes


@dataclass(frozen=True)
class SyntheticSampleSpec:
    name: str
    pack_id: str
    state: str
    expected_rule_ids: frozenset[str]
    document: Document


def _run(
    text: str,
    *,
    font_eastasia: str = "宋体",
    font_ascii: str = "Times New Roman",
    font_hansi: str = "Times New Roman",
    font_size_pt: float = 12.0,
    bold: bool = False,
    italic: bool = False,
    underline: str | None = None,
    color_hex: str | None = None,
) -> Run:
    return Run(
        text=text,
        font_eastasia=font_eastasia,
        font_ascii=font_ascii,
        font_hansi=font_hansi,
        font_size_pt=font_size_pt,
        bold=bold,
        italic=italic,
        underline=underline,
        color_hex=color_hex,
    )


def _paragraph(
    *,
    style_id: str,
    text: str,
    runs: tuple[Run, ...],
    line_spacing: float,
    first_line_indent_cm: float,
    hanging_indent_cm: float = 0.0,
    space_before_pt: float = 0.0,
    space_after_pt: float = 0.0,
    alignment: str | None = None,
    table_index: int | None = None,
    table_row_index: int | None = None,
    table_cell_index: int | None = None,
    list_type: str | None = None,
    list_level: int | None = None,
) -> Paragraph:
    return Paragraph(
        style_id=style_id,
        text=text,
        level=None,
        runs=runs,
        line_spacing=line_spacing,
        space_before_pt=space_before_pt,
        space_after_pt=space_after_pt,
        first_line_indent_cm=first_line_indent_cm,
        hanging_indent_cm=hanging_indent_cm,
        alignment=alignment,
        table_index=table_index,
        table_row_index=table_row_index,
        table_cell_index=table_cell_index,
        list_type=list_type,
        list_level=list_level,
    )


def _document(title: str, paragraphs: tuple[Paragraph, ...]) -> Document:
    return Document(
        sections=(Section(),),
        paragraphs=paragraphs,
        metadata=DocumentMetadata(title=title, author="PaperGuard synthetic regression"),
    )


def _gb7713_body(line_spacing: float = 1.5) -> Document:
    return _document(
        "GB7713 synthetic body",
        (
            _paragraph(
                style_id="Body Text",
                text="This synthetic body paragraph is privacy safe.",
                runs=(
                    _run(
                        "This synthetic body paragraph is privacy safe.",
                        font_size_pt=12.0,
                    ),
                ),
                line_spacing=line_spacing,
                first_line_indent_cm=0.74,
                alignment="justify",
            ),
        ),
    )


def _gb7714_reference(hanging_indent_cm: float = 0.74) -> Document:
    return _document(
        "GB7714 synthetic reference",
        (
            _paragraph(
                style_id="Bibliography",
                text="[1] PaperGuard Team. Synthetic regression sample[J]. 2026.",
                runs=(
                    _run(
                        "[1] PaperGuard Team. Synthetic regression sample[J]. 2026.",
                        font_size_pt=10.5,
                        bold=False,
                        underline=None,
                    ),
                ),
                line_spacing=1.5,
                first_line_indent_cm=0.0,
                hanging_indent_cm=hanging_indent_cm,
                alignment="left",
            ),
        ),
    )


def _sim_univ_list(line_spacing: float = 1.5) -> Document:
    return _document(
        "SIM UNIV synthetic list",
        (
            _paragraph(
                style_id="List Number",
                text="Synthetic numbered item",
                runs=(_run("Synthetic numbered item", font_size_pt=12.0),),
                line_spacing=line_spacing,
                first_line_indent_cm=0.0,
                list_type="number",
                list_level=0,
            ),
        ),
    )


def _fixed_document(document: Document, pack_id: str) -> Document:
    findings = validate_docx(document, pack_id)
    fixed, skipped = apply_all_fixes(document, findings)
    if skipped:
        skipped_ids = ", ".join(finding.rule_id for finding in skipped)
        raise AssertionError(f"synthetic sample has skipped fixes: {skipped_ids}")
    return fixed


_GB7713_VIOLATING = _gb7713_body(line_spacing=1.0)
_GB7714_VIOLATING = _gb7714_reference(hanging_indent_cm=0.0)
_SIM_UNIV_VIOLATING = _sim_univ_list(line_spacing=1.0)

SAMPLE_SPECS: tuple[SyntheticSampleSpec, ...] = (
    SyntheticSampleSpec(
        name="gb7713-compliant",
        pack_id="GB-T-7713-1-2006",
        state="compliant",
        expected_rule_ids=frozenset({"PG-BODY-LINE-SPACING"}),
        document=_gb7713_body(),
    ),
    SyntheticSampleSpec(
        name="gb7713-violating",
        pack_id="GB-T-7713-1-2006",
        state="violating",
        expected_rule_ids=frozenset({"PG-BODY-LINE-SPACING"}),
        document=_GB7713_VIOLATING,
    ),
    SyntheticSampleSpec(
        name="gb7713-fixed",
        pack_id="GB-T-7713-1-2006",
        state="fixed",
        expected_rule_ids=frozenset({"PG-BODY-LINE-SPACING"}),
        document=_fixed_document(_GB7713_VIOLATING, "GB-T-7713-1-2006"),
    ),
    SyntheticSampleSpec(
        name="gb7714-compliant",
        pack_id="GB-T-7714-1-2015",
        state="compliant",
        expected_rule_ids=frozenset({"PG-REF-HANGING-INDENT"}),
        document=_gb7714_reference(),
    ),
    SyntheticSampleSpec(
        name="gb7714-violating",
        pack_id="GB-T-7714-1-2015",
        state="violating",
        expected_rule_ids=frozenset({"PG-REF-HANGING-INDENT"}),
        document=_GB7714_VIOLATING,
    ),
    SyntheticSampleSpec(
        name="gb7714-fixed",
        pack_id="GB-T-7714-1-2015",
        state="fixed",
        expected_rule_ids=frozenset({"PG-REF-HANGING-INDENT"}),
        document=_fixed_document(_GB7714_VIOLATING, "GB-T-7714-1-2015"),
    ),
    SyntheticSampleSpec(
        name="sim-univ-compliant",
        pack_id="SIM-UNIV-generic",
        state="compliant",
        expected_rule_ids=frozenset({"PG-LIST-LINE-SPACING"}),
        document=_sim_univ_list(),
    ),
    SyntheticSampleSpec(
        name="sim-univ-violating",
        pack_id="SIM-UNIV-generic",
        state="violating",
        expected_rule_ids=frozenset({"PG-LIST-LINE-SPACING"}),
        document=_SIM_UNIV_VIOLATING,
    ),
    SyntheticSampleSpec(
        name="sim-univ-fixed",
        pack_id="SIM-UNIV-generic",
        state="fixed",
        expected_rule_ids=frozenset({"PG-LIST-LINE-SPACING"}),
        document=_fixed_document(_SIM_UNIV_VIOLATING, "SIM-UNIV-generic"),
    ),
)


def generate_samples(output_dir: Path) -> list[tuple[SyntheticSampleSpec, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[tuple[SyntheticSampleSpec, Path]] = []
    for sample in SAMPLE_SPECS:
        path = output_dir / f"{sample.name}.docx"
        write_ir_to_docx(sample.document, str(path))
        generated.append((sample, path))
    return generated
