"""DOCX generation service."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor

from app.models.format_config import StyleConfig
from app.models.project import ImageBlock, ListBlock, ParagraphBlock, Project, SectionContent, TableBlock
from app.models.template import SectionNode, Template
from app.services.content_blocks import clean_text, parse_paragraph_html


ALIGNMENT_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

STYLE_MAP = {
    "heading_1": "Heading 1",
    "heading_2": "Heading 2",
    "heading_3": "Heading 3",
    "body_text": "Normal",
    "cover_title": "Normal",
}


class DocxGenerator:
    """Render a project into a DOCX file."""

    def __init__(self, template: Template, project: Project):
        self.template = template
        self.project = project
        self.doc = Document()

    def generate(self, output_path: str | Path) -> str:
        self._setup_page()
        self._configure_styles()
        for node in self.template.structure:
            self._render_section(node)
        self.doc.save(str(output_path))
        return str(output_path)

    def _setup_page(self):
        page = self.template.format_config.page_setup
        section = self.doc.sections[0]
        if page.orientation == "landscape":
            section.page_width = Cm(page.height_cm)
            section.page_height = Cm(page.width_cm)
            section.orientation = WD_ORIENT.LANDSCAPE
        else:
            section.page_width = Cm(page.width_cm)
            section.page_height = Cm(page.height_cm)

        section.top_margin = Cm(page.margin_top_cm)
        section.bottom_margin = Cm(page.margin_bottom_cm)
        section.left_margin = Cm(page.margin_left_cm)
        section.right_margin = Cm(page.margin_right_cm)
        section.header_distance = Cm(page.header_margin_cm)
        section.footer_distance = Cm(page.footer_margin_cm)

        if self.template.format_config.header_text:
            section.header.paragraphs[0].text = self.template.format_config.header_text
        if self.template.format_config.footer_text:
            section.footer.paragraphs[0].text = self.template.format_config.footer_text

    def _configure_styles(self):
        for style_id, config in self.template.format_config.styles.items():
            word_name = STYLE_MAP.get(style_id)
            if not word_name:
                continue
            try:
                word_style = self.doc.styles[word_name]
            except KeyError:
                continue

            word_style.font.name = config.font.name_en
            word_style.font.size = Pt(config.font.size_pt)
            word_style.font.bold = config.font.bold
            word_style.font.italic = config.font.italic

            rpr = word_style.element.find(qn("w:rPr"))
            if rpr is None:
                rpr = parse_xml(f'<w:rPr {nsdecls("w")}></w:rPr>')
                word_style.element.append(rpr)
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = parse_xml(f'<w:rFonts {nsdecls("w")}/>')
                rpr.append(rfonts)
            rfonts.set(qn("w:eastAsia"), config.font.name_cn)
            rfonts.set(qn("w:ascii"), config.font.name_en)
            rfonts.set(qn("w:hAnsi"), config.font.name_en)

            paragraph = word_style.paragraph_format
            paragraph.alignment = ALIGNMENT_MAP.get(config.paragraph.alignment, WD_ALIGN_PARAGRAPH.JUSTIFY)
            paragraph.space_before = Pt(config.paragraph.space_before_pt)
            paragraph.space_after = Pt(config.paragraph.space_after_pt)
            paragraph.line_spacing = config.paragraph.line_spacing
            paragraph.first_line_indent = Cm(config.paragraph.first_line_indent_cm)

    def _render_section(self, node: SectionNode):
        if node.auto_generate:
            self._render_toc()
            return
        if node.type == "cover":
            self._render_cover()
            return

        content = self.project.sections.get(node.id, SectionContent(section_id=node.id))
        style_id = node.style_id or self._infer_style(node)

        if node.type in {"chapter", "acknowledgements", "references", "appendix"}:
            self._add_heading(node.label, style_id)
        elif node.type == "section":
            self._add_heading(node.label, style_id)

        self._render_content(content)

        for child in node.subsections:
            self._render_section(child)

    def _infer_style(self, node: SectionNode) -> str:
        return {
            "chapter": "heading_1",
            "section": "heading_2",
            "appendix": "heading_1",
            "acknowledgements": "heading_1",
            "references": "heading_1",
        }.get(node.type, "body_text")

    def _add_heading(self, text: str, style_id: str):
        word_style = STYLE_MAP.get(style_id, "Normal")
        paragraph = self.doc.add_paragraph(text, style=word_style)
        config = self.template.format_config.styles.get(style_id, StyleConfig.body_text())
        for run in paragraph.runs:
            self._apply_run_style(run, config)

    def _render_content(self, content: SectionContent):
        for block in content.blocks:
            if isinstance(block, ParagraphBlock):
                self._render_paragraph(block)
            elif isinstance(block, ImageBlock):
                self._render_image(block)
            elif isinstance(block, TableBlock):
                self._render_table(block)
            elif isinstance(block, ListBlock):
                self._render_list(block)

    def _render_paragraph(self, block: ParagraphBlock):
        parsed = parse_paragraph_html(block.html)
        if not parsed.runs:
            text = clean_text(block.html)
            if not text:
                return
            parsed_runs = [(text, None, None, None, None, None, None)]
        else:
            parsed_runs = [
                (run.text, run.bold, run.italic, run.underline, run.font_size_pt, run.font_name, run.color_hex)
                for run in parsed.runs
            ]

        config = self.template.format_config.styles.get("body_text", StyleConfig.body_text())
        paragraph = self.doc.add_paragraph(style="Normal")
        paragraph.paragraph_format.alignment = ALIGNMENT_MAP.get(
            parsed.alignment or config.paragraph.alignment,
            WD_ALIGN_PARAGRAPH.JUSTIFY,
        )
        paragraph.paragraph_format.line_spacing = config.paragraph.line_spacing
        paragraph.paragraph_format.first_line_indent = Cm(config.paragraph.first_line_indent_cm)
        paragraph.paragraph_format.space_before = Pt(config.paragraph.space_before_pt)
        paragraph.paragraph_format.space_after = Pt(config.paragraph.space_after_pt)

        if parsed.quote_level:
            paragraph.paragraph_format.left_indent = Cm(0.8 * parsed.quote_level)
            paragraph.paragraph_format.right_indent = Cm(0.3 * parsed.quote_level)
            paragraph.paragraph_format.first_line_indent = Cm(0)

        for text, is_bold, is_italic, is_underline, font_size_pt, font_name, color_hex in parsed_runs:
            parts = text.split("\n")
            run = paragraph.add_run(parts[0])
            self._apply_run_style(
                run,
                config,
                bold=is_bold,
                italic=is_italic,
                underline=is_underline,
                font_size_pt=font_size_pt,
                font_name=font_name,
                color_hex=color_hex,
            )
            for continuation in parts[1:]:
                run.add_break()
                if continuation:
                    next_run = paragraph.add_run(continuation)
                    self._apply_run_style(
                        next_run,
                        config,
                        bold=is_bold,
                        italic=is_italic,
                        underline=is_underline,
                        font_size_pt=font_size_pt,
                        font_name=font_name,
                        color_hex=color_hex,
                    )

    def _render_image(self, block: ImageBlock):
        image_path = Path(block.path)
        if not block.path or not image_path.exists():
            self.doc.add_paragraph(f"[图片缺失: {block.caption or block.path}]")
            return
        paragraph = self.doc.add_paragraph()
        paragraph.alignment = ALIGNMENT_MAP.get(block.alignment, WD_ALIGN_PARAGRAPH.CENTER)
        paragraph.add_run().add_picture(str(image_path), width=Cm(block.width_cm))
        if block.caption:
            caption = self.doc.add_paragraph(f"图 {block.caption}")
            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _apply_run_style(
        self,
        run,
        config: StyleConfig,
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
        font_size_pt: float | None = None,
        font_name: str | None = None,
        color_hex: str | None = None,
    ):
        run.font.name = font_name or config.font.name_en
        run.font.size = Pt(font_size_pt or config.font.size_pt)
        run.font.bold = config.font.bold if bold is None else bold
        run.font.italic = config.font.italic if italic is None else italic
        run.font.underline = bool(underline)
        if color_hex:
            run.font.color.rgb = RGBColor.from_string(color_hex)
        rpr = run._element.find(qn("w:rPr"))
        if rpr is not None:
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is not None:
                east_asia_font = font_name or config.font.name_cn
                rfonts.set(qn("w:eastAsia"), east_asia_font)
                rfonts.set(qn("w:ascii"), font_name or config.font.name_en)
                rfonts.set(qn("w:hAnsi"), font_name or config.font.name_en)

    def _render_list(self, block: ListBlock):
        style_name = "List Number" if block.ordered else "List Bullet"
        config = self.template.format_config.styles.get("body_text", StyleConfig.body_text())
        for item_html in block.items_html:
            parsed = parse_paragraph_html(item_html)
            if not parsed.runs:
                text = clean_text(item_html)
                if not text:
                    continue
                parsed_runs = [(text, None, None, None, None, None, None)]
            else:
                parsed_runs = [
                    (run.text, run.bold, run.italic, run.underline, run.font_size_pt, run.font_name, run.color_hex)
                    for run in parsed.runs
                ]

            paragraph = self.doc.add_paragraph(style=style_name)
            paragraph.paragraph_format.space_before = Pt(config.paragraph.space_before_pt)
            paragraph.paragraph_format.space_after = Pt(config.paragraph.space_after_pt)
            paragraph.paragraph_format.line_spacing = config.paragraph.line_spacing
            paragraph.paragraph_format.alignment = ALIGNMENT_MAP.get(
                parsed.alignment or config.paragraph.alignment,
                WD_ALIGN_PARAGRAPH.LEFT,
            )
            if parsed.quote_level:
                paragraph.paragraph_format.left_indent = Cm(0.8 * parsed.quote_level)

            for text, is_bold, is_italic, is_underline, font_size_pt, font_name, color_hex in parsed_runs:
                run = paragraph.add_run(text)
                self._apply_run_style(
                    run,
                    config,
                    bold=is_bold,
                    italic=is_italic,
                    underline=is_underline,
                    font_size_pt=font_size_pt,
                    font_name=font_name,
                    color_hex=color_hex,
                )

    def _render_table(self, block: TableBlock):
        rows = len(block.data) or block.rows
        cols = max((len(row) for row in block.data), default=block.cols)
        table = self.doc.add_table(rows=rows, cols=cols, style="Table Grid")
        for row_index, row in enumerate(block.data):
            for col_index, value in enumerate(row):
                table.cell(row_index, col_index).text = str(value)
        if block.caption:
            caption = self.doc.add_paragraph(f"表 {block.caption}")
            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _render_cover(self):
        config = self.template.format_config.styles.get("cover_title", StyleConfig.cover_title())
        for _ in range(6):
            self.doc.add_paragraph()

        title = self.doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run(self.project.title or "论文题目")
        run.font.size = Pt(config.font.size_pt)
        run.font.bold = True
        self._apply_run_style(run, config, bold=True, font_size_pt=config.font.size_pt)

        for _ in range(4):
            self.doc.add_paragraph()

        info_items = [
            ("专业", self.project.major),
            ("学生姓名", self.project.author),
            ("学号", self.project.student_id),
            ("指导教师", self.project.supervisor),
        ]
        info_style = self.template.format_config.styles.get("body_text", StyleConfig.body_text())
        for label, value in info_items:
            paragraph = self.doc.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(f"{label}：{value}")
            self._apply_run_style(run, info_style)

        self.doc.add_page_break()

    def _render_toc(self):
        heading = self.doc.add_paragraph("目录", style="Heading 1")
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

        paragraph = self.doc.add_paragraph()
        paragraph.add_run()._element.append(
            parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>')
        )
        paragraph.add_run()._element.append(
            parse_xml(
                f'<w:instrText {nsdecls("w")} xml:space="preserve">'
                ' TOC \\o "1-3" \\h \\z \\u '
                "</w:instrText>"
            )
        )
        paragraph.add_run()._element.append(
            parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>')
        )
        paragraph.add_run('（请在 Word 中右键点击此处，选择“更新域”以生成目录）')
        paragraph.add_run()._element.append(
            parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>')
        )
        self.doc.add_page_break()
