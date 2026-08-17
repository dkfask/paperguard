"""PaperGuard 写作项目数据模型。

项目 = 模板实例化后的章节树 + 每章内容(段落块)。
段落只有文本和少量行内格式, 排版样式完全由模板决定。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParagraphBlock(BaseModel):
    """一个正文段落。样式来自模板, 内容由学生填写。"""
    id: str = Field(min_length=1)
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False


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
    meta: dict[str, str] = {}          # 封面字段值 key -> value
    sections: list[ProjectSection] = []
    created_at: str = ""
    updated_at: str = ""
