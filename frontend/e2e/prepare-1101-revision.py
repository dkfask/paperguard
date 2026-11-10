"""Create a test-only 1101 template revision with an explicit department row.

The source document is never modified.  The copied cover-table row keeps the
master's row formatting and only changes the label/value text needed by the
1101 required-field contract.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


def _replace_cell_text(cell_xml, text: str) -> None:
    text_nodes = cell_xml.findall(".//" + qn("w:t"))
    if not text_nodes:
        raise RuntimeError("cover cell has no text run")
    for node in text_nodes:
        node.text = ""
    text_nodes[-1].text = text


def build_revision(source: Path, destination: Path) -> None:
    document = Document(source)
    cover_table = next(
        (
            table
            for table in document.tables
            if len(table.columns) == 2
            and table.rows
            and "学院" in "".join(table.rows[0].cells[0].text.split())
        ),
        None,
    )
    if cover_table is None:
        raise RuntimeError("1101 cover table was not found")

    # The college row contains a drawing/text box with document-wide IDs.
    # Copy the plain major row instead, so the fixture does not duplicate it.
    college_row = cover_table.rows[0]._tr
    major_row = cover_table.rows[1]._tr
    if any(major_row.findall(".//" + qn(tag)) for tag in ("w:drawing", "w:pict", "w:bookmarkStart")):
        raise RuntimeError("1101 major row is not safe to copy")
    department_row = copy.deepcopy(major_row)
    cells = department_row.findall(qn("w:tc"))
    if len(cells) != 2:
        raise RuntimeError("1101 cover table row is not two columns")
    _replace_cell_text(cells[0], "系    别：")
    _replace_cell_text(cells[1], "")
    college_row.addnext(department_row)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(destination)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare-1101-revision.py SOURCE.docx DESTINATION.docx")
    build_revision(Path(sys.argv[1]), Path(sys.argv[2]))
