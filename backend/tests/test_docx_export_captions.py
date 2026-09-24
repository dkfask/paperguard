from __future__ import annotations

import struct
import zlib

from docx import Document

from app.core.export.docx_exporter import export_project_to_docx
from app.models.project import ImageBlock, ParagraphBlock, ProjectSection, TableBlock, WritingProject
from app.models.template import PaperTemplate, TextStyle


def _minimal_png() -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + name + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


def test_export_does_not_duplicate_existing_figure_caption(tmp_path, monkeypatch):
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(_minimal_png())
    monkeypatch.setattr(
        "app.core.export.docx_exporter._resolve_image_path",
        lambda _url: image_path,
    )

    project = WritingProject(
        id="caption-regression",
        template_id="caption-template",
        sections=[
            ProjectSection(key="chapter-1", type="heading1", title="第一章"),
            ProjectSection(
                key="body", type="body", title="正文",
                paragraphs=[
                    ParagraphBlock(
                        id="image-with-existing-caption", type="image",
                        image=ImageBlock(url="/media/figure.png"),
                    ),
                    ParagraphBlock(
                        id="original-caption",
                        text="图3.2.2-1 业务流程图（自动模式）",
                    ),
                    ParagraphBlock(
                        id="image-without-caption", type="image",
                        image=ImageBlock(url="/media/figure.png"),
                    ),
                    ParagraphBlock(id="body-text", text="后续正文。"),
                ],
            ),
        ],
    )
    template = PaperTemplate(
        id="caption-template", name="图注回归测试",
        styles={"body": TextStyle(), "figure_caption": TextStyle()},
    )
    output_path = tmp_path / "caption-regression.docx"

    export_project_to_docx(project, template, str(output_path))

    paragraphs = Document(output_path).paragraphs
    texts = [paragraph.text for paragraph in paragraphs]
    assert texts.count("图3.2.2-1 业务流程图（自动模式）") == 1
    assert "图 1.1" not in texts
    # The suppressed caption still consumes the figure number; subsequent numbering stays stable.
    assert "图 1.2" in texts
    assert sum(bool(paragraph._p.xpath(".//w:drawing")) for paragraph in paragraphs) == 2


def test_export_does_not_duplicate_adjacent_table_caption(tmp_path):
    project = WritingProject(
        id="table-caption-regression",
        template_id="table-caption-template",
        sections=[
            ProjectSection(key="chapter-1", type="heading1", title="第一章"),
            ProjectSection(
                key="body", type="body", title="正文",
                paragraphs=[
                    ParagraphBlock(id="existing-table-caption", text="表4.1 硬件设备清单"),
                    ParagraphBlock(
                        id="table-with-existing-caption", type="table",
                        table=TableBlock(rows=1, cols=1, cells=[["主控芯片"]]),
                    ),
                    ParagraphBlock(
                        id="table-without-caption", type="table",
                        table=TableBlock(rows=1, cols=1, cells=[["传感器"]]),
                    ),
                ],
            ),
        ],
    )
    template = PaperTemplate(
        id="table-caption-template", name="表注回归测试",
        styles={"body": TextStyle(), "table_caption": TextStyle()},
    )
    output_path = tmp_path / "table-caption-regression.docx"

    export_project_to_docx(project, template, str(output_path))

    texts = [paragraph.text for paragraph in Document(output_path).paragraphs]
    assert texts.count("表4.1 硬件设备清单") == 1
    assert "表 1.1" not in texts
    # The table number advances even when its generated caption is suppressed.
    assert "表 1.2" in texts
