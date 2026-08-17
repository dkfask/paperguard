"""PaperGuard 模板数据模型。

模板 = 章节结构树 + 样式规范 + 封面元信息字段。
学生写作项目基于模板实例化, 格式由模板约束。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SectionType(str, Enum):
    """章节类型。"""
    COVER = "cover"                 # 封面
    ABSTRACT = "abstract"           # 摘要
    TOC = "toc"                     # 目录(自动生成)
    HEADING1 = "heading1"           # 一级标题(章)
    HEADING2 = "heading2"           # 二级标题(节)
    HEADING3 = "heading3"           # 三级标题(小节)
    BODY = "body"                   # 正文容器(内含段落)
    REFERENCES = "references"       # 参考文献
    ACKNOWLEDGEMENT = "acknowledgement"  # 致谢
    APPENDIX = "appendix"           # 附录


class Align(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class TextStyle(BaseModel):
    """一类元素的排版样式。写作时由模板锁定, 学生不可修改。"""
    font_zh: str = "宋体"
    font_en: str = "Times New Roman"
    size_pt: float = 12.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: Align = Align.JUSTIFY
    line_spacing: float = 1.5
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    first_line_indent_chars: float = 0.0   # 首行缩进(字符)
    page_break_before: bool = False        # 该元素前分页


class TemplateSection(BaseModel):
    """章节树节点。"""
    key: str = Field(min_length=1)
    type: SectionType
    title: str
    required: bool = True
    style: TextStyle | None = None          # 覆盖该类元素的默认样式
    children: list["TemplateSection"] = []


class MetaField(BaseModel):
    """封面元信息字段(题目/作者/学号/专业/导师/日期等)。"""
    key: str
    label: str
    required: bool = True


class PaperTemplate(BaseModel):
    """论文模板: 结构 + 样式 + 封面字段。"""
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    sections: list[TemplateSection] = []
    styles: dict[str, TextStyle] = {}       # SectionType.value -> 默认样式
    meta_fields: list[MetaField] = []


# ---------------------------------------------------------------------------
# 内置默认样式(GB/T 7713 学位论文常见规范), 新模板的起点
# ---------------------------------------------------------------------------

DEFAULT_STYLES: dict[str, TextStyle] = {
    "cover_title": TextStyle(font_zh="黑体", size_pt=22.0, bold=True, align=Align.CENTER,
                             space_before_pt=24.0, space_after_pt=24.0),
    "cover_meta": TextStyle(font_zh="宋体", size_pt=16.0, align=Align.CENTER,
                            line_spacing=2.0),
    "abstract": TextStyle(font_zh="黑体", size_pt=18.0, bold=True, align=Align.CENTER,
                          space_before_pt=24.0, space_after_pt=18.0,
                          page_break_before=True),
    "toc": TextStyle(font_zh="黑体", size_pt=18.0, bold=True, align=Align.CENTER,
                     space_before_pt=24.0, space_after_pt=18.0, page_break_before=True),
    "heading1": TextStyle(font_zh="黑体", size_pt=18.0, bold=True, align=Align.CENTER,
                          space_before_pt=24.0, space_after_pt=18.0,
                          page_break_before=True),
    "heading2": TextStyle(font_zh="黑体", size_pt=16.0, bold=True, align=Align.LEFT,
                          space_before_pt=18.0, space_after_pt=12.0),
    "heading3": TextStyle(font_zh="黑体", size_pt=14.0, bold=True, align=Align.LEFT,
                          space_before_pt=12.0, space_after_pt=6.0),
    "body": TextStyle(font_zh="宋体", size_pt=12.0, align=Align.JUSTIFY,
                      line_spacing=1.5, first_line_indent_chars=2.0),
    "references": TextStyle(font_zh="宋体", size_pt=12.0, align=Align.JUSTIFY,
                            line_spacing=1.5, page_break_before=True),
    "acknowledgement": TextStyle(font_zh="宋体", size_pt=12.0, align=Align.JUSTIFY,
                                 line_spacing=1.5, page_break_before=True),
    "appendix": TextStyle(font_zh="宋体", size_pt=12.0, align=Align.JUSTIFY,
                          line_spacing=1.5, page_break_before=True),
    # 图表规范
    "figure_caption": TextStyle(font_zh="宋体", size_pt=10.5, align=Align.CENTER,
                                space_before_pt=6.0, space_after_pt=12.0),
    "table_caption": TextStyle(font_zh="宋体", size_pt=10.5, align=Align.CENTER,
                               space_before_pt=12.0, space_after_pt=6.0),
    "table_cell": TextStyle(font_zh="宋体", size_pt=10.5, align=Align.CENTER),
}
