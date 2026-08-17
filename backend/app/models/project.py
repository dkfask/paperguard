"""PaperGuard 写作项目数据模型。

项目 = 模板实例化后的章节树 + 每章内容(内容块)。
内容块支持三种类型: 文本段落 / 图片 / 表格, 排版样式完全由模板决定。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    """内容块类型。"""
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    TABLE = "table"


class ImageBlock(BaseModel):
    """图片块: 文件路径 + 题注。"""
    url: str = ""            # 静态资源路径, 如 /media/uploads/xxx.png
    caption: str = ""        # 题注文本 (图 3.1 xxx)


class TableBlock(BaseModel):
    """表格块: 单元格文本矩阵 + 题注。"""
    rows: int = Field(default=2, ge=1, le=50)
    cols: int = Field(default=2, ge=1, le=20)
    cells: list[list[str]] = []   # rows x cols
    caption: str = ""             # 题注文本 (表 3.1 xxx)


class ParagraphBlock(BaseModel):
    """一个内容块。文本/图片/表格三选一, 样式来自模板。"""
    id: str = Field(min_length=1)
    type: BlockType = BlockType.PARAGRAPH
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    image: ImageBlock | None = None
    table: TableBlock | None = None


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
    meta: dict[str, str] = {}          # 封面字段值 key -> value
    sections: list[ProjectSection] = []
    created_at: str = ""
    updated_at: str = ""
