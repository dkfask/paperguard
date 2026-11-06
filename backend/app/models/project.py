"""PaperGuard 写作项目数据模型。

项目 = 模板实例化后的章节树 + 每章内容(内容块)。
内容块支持文本段落 / 图片 / 表格 / Office 公式, 排版样式完全由模板决定。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    """内容块类型。"""
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    TABLE = "table"
    EQUATION = "equation"


class ImageBlock(BaseModel):
    """图片块: 文件路径、题注及可选的源文档几何信息。"""
    url: str = ""            # 静态资源路径, 如 /media/uploads/xxx.png
    caption: str = ""        # 题注文本 (图 3.1 xxx)
    width_cm: float | None = Field(default=None, gt=0, le=100)
    height_cm: float | None = Field(default=None, gt=0, le=100)
    alignment: Literal["left", "center", "right", "justify", "distribute"] | None = None


class TableBorderEdge(BaseModel):
    style: Literal["nil", "none", "single", "double", "dotted", "dashed", "dashSmallGap",
                   "dotDash", "dotDotDash", "triple", "thick", "thinThickSmallGap",
                   "thickThinSmallGap", "thinThickThinSmallGap", "thinThickMediumGap",
                   "thickThinMediumGap", "thinThickThinMediumGap", "thinThickLargeGap",
                   "thickThinLargeGap", "thinThickThinLargeGap"]
    color: str = Field(default="auto", pattern=r"^(?:auto|[0-9A-Fa-f]{6})$")
    size: int = Field(default=4, ge=0, le=96)
    space: int = Field(default=0, ge=0, le=31)


class TableBorders(BaseModel):
    top: TableBorderEdge
    bottom: TableBorderEdge
    left: TableBorderEdge
    right: TableBorderEdge
    insideH: TableBorderEdge
    insideV: TableBorderEdge


class TableBlock(BaseModel):
    """表格块: 单元格文本矩阵 + 题注。"""
    rows: int = Field(default=2, ge=1, le=50)
    cols: int = Field(default=2, ge=1, le=20)
    cells: list[list[str]] = []   # rows x cols
    caption: str = ""             # 题注文本 (表 3.1 xxx)
    source_xml: str = Field(default="", max_length=600_000)
    borders: TableBorders | None = None


class EquationBlock(BaseModel):
    """Imported Office Math block; OMML is retained for lossless export."""
    omml: str = ""
    alt_text: str = ""


class InlineTextRun(BaseModel):
    """Character-level formatting retained inside one Word paragraph."""
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    highlight: str | None = None
    vert_align: str | None = None


class ReferenceLanguageFact(BaseModel):
    """Language information is a factual claim; confirmation is server-owned."""
    languages: list[Annotated[str, Field(pattern=r"^[a-z]{2,3}$")]] = Field(default_factory=list, max_length=8)
    evidence: str = Field(default="", max_length=2000)
    source: Literal["student", "system"] = "student"
    suggested_languages: list[Annotated[str, Field(pattern=r"^[a-z]{2,3}$")]] = Field(default_factory=list, max_length=8)
    status: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    confirmed_by: str = ""
    confirmed_at: str = ""


class ReferenceYearFact(BaseModel):
    """Publication year evidence; confirmation is server-owned and bound to ``year``."""
    value: str = Field(default="", max_length=32)
    evidence: str = Field(default="", max_length=2000)
    source: Literal["student", "system"] = "student"
    suggested_year: str = Field(default="", max_length=32)
    status: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    confirmed_by: str = ""
    confirmed_at: str = ""


class ReferenceEntry(BaseModel):
    """Structured bibliography fields used to generate a formatted reference line."""
    kind: Literal["unparsed", "journal", "book", "thesis", "conference", "online", "standard"]
    authors: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=2000)
    source: str = Field(default="", max_length=2000)
    collection: str = Field(default="", max_length=2000)
    place: str = Field(default="", max_length=500)
    publisher: str = Field(default="", max_length=1000)
    institution: str = Field(default="", max_length=1000)
    organization: str = Field(default="", max_length=1000)
    number: str = Field(default="", max_length=500)
    year: str = Field(default="", max_length=32)
    volume: str = Field(default="", max_length=100)
    issue: str = Field(default="", max_length=100)
    pages: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=4000)
    publish_date: str = Field(default="", max_length=32)
    access_date: str = Field(default="", max_length=32)
    doi: str = Field(default="", max_length=500)
    unformatted_text: str = Field(default="", max_length=20000)
    original_pasted_text: str | None = Field(default=None, max_length=20000)
    language_fact: ReferenceLanguageFact | None = None
    year_fact: ReferenceYearFact | None = None


class ParagraphBlock(BaseModel):
    """一个内容块。文本/图片/表格三选一, 样式来自模板。"""
    id: str = Field(min_length=1)
    type: BlockType = BlockType.PARAGRAPH
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    highlight: str | None = None
    vert_align: str | None = None
    inline_runs: list[InlineTextRun] = Field(default_factory=list)
    reference: ReferenceEntry | None = None
    reference_number: int | None = Field(default=None, ge=1)
    # Server-owned: a pasted numeric prefix is source text, not the current display number.
    reference_number_managed: bool = Field(default=False, json_schema_extra={"readOnly": True})
    image: ImageBlock | None = None
    table: TableBlock | None = None
    equation: EquationBlock | None = None


class ProjectSection(BaseModel):
    """项目中的一节。"""
    key: str = Field(min_length=1)
    type: str
    title: str
    required: bool = True
    paragraphs: list[ParagraphBlock] = []


class WritingProject(BaseModel):
    """写作项目。"""
    id: str = Field(min_length=1)
    template_id: str
    template_name: str = ""
    title: str = ""
    tenant_id: str | None = Field(default=None, json_schema_extra={"readOnly": True})
    membership_id: str | None = Field(default=None, json_schema_extra={"readOnly": True})
    owner_id: str = ""             # 归属用户 (空 = 旧数据, 视为公开/管理员)
    revision: int = Field(default=0, ge=0)
    template_snapshot: dict | None = None
    template_source_snapshot: dict | None = None
    # Original approved layout before a manuscript-specific chapter instance.
    layout_template_snapshot: dict | None = Field(default=None, json_schema_extra={"readOnly": True})
    requirement_snapshot: dict | None = None  # 项目创建时冻结的有效规范与版本
    organization_context: dict[str, str] = {}  # school/college/major/batch/course 等作用域
    stage: str = ""
    waivers: list[dict] = []
    requirement_disputes: list[dict] = []
    requirement_acknowledgements: list[dict] = []
    requirement_upgrades: list[dict] = []
    # 每条人工规范检查项保留一份最新的 AI 辅助结果, 按 requirement_item_id 对应。
    ai_assist_results: list[dict] = []
    check_run_ids: list[str] = []
    meta: dict[str, str] = {}          # 封面字段值 key -> value
    reference_number_owners: dict[str, str] = Field(default_factory=dict)
    sections: list[ProjectSection] = []
    allow_structure_edit: bool = True  # 是否允许学生修改整体论文的框架
    deleted_at: str = ""               # 软删除时间；进入回收站后保留引用和附件
    created_at: str = ""
    updated_at: str = ""
