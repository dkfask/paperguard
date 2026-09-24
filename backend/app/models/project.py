"""PaperGuard 写作项目数据模型。

项目 = 模板实例化后的章节树 + 每章内容(内容块)。
内容块支持文本段落 / 图片 / 表格 / Office 公式, 排版样式完全由模板决定。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

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


class TableBlock(BaseModel):
    """表格块: 单元格文本矩阵 + 题注。"""
    rows: int = Field(default=2, ge=1, le=50)
    cols: int = Field(default=2, ge=1, le=20)
    cells: list[list[str]] = []   # rows x cols
    caption: str = ""             # 题注文本 (表 3.1 xxx)
    source_xml: str = Field(default="", max_length=600_000)


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
    owner_id: str = ""             # 归属用户 (空 = 旧数据, 视为公开/管理员)
    revision: int = Field(default=0, ge=0)
    template_snapshot: dict | None = None
    template_source_snapshot: dict | None = None
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
    sections: list[ProjectSection] = []
    allow_structure_edit: bool = True  # 是否允许学生修改整体论文的框架
    deleted_at: str = ""               # 软删除时间；进入回收站后保留引用和附件
    created_at: str = ""
    updated_at: str = ""
