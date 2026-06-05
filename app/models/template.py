"""Template data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .format_config import FormatConfig


SECTION_TYPES = [
    "cover",
    "abstract",
    "toc",
    "chapter",
    "section",
    "references",
    "appendix",
    "acknowledgements",
]


@dataclass
class SectionNode:
    """One node in the thesis structure tree."""

    id: str
    label: str
    type: str = "section"
    required: bool = False
    auto_generate: bool = False
    style_id: str = "body_text"
    subsections: list["SectionNode"] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "auto_generate": self.auto_generate,
            "style_id": self.style_id,
            "description": self.description,
        }
        if self.subsections:
            result["subsections"] = [section.to_dict() for section in self.subsections]
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "SectionNode":
        return cls(
            id=data["id"],
            label=data.get("label", ""),
            type=data.get("type", "section"),
            required=data.get("required", False),
            auto_generate=data.get("auto_generate", False),
            style_id=data.get("style_id", "body_text"),
            description=data.get("description", ""),
            subsections=[cls.from_dict(item) for item in data.get("subsections", [])],
        )


@dataclass
class Template:
    """Paper template."""

    name: str = "未命名模板"
    version: str = "1.0"
    author: str = ""
    description: str = ""
    format_config: FormatConfig = field(default_factory=FormatConfig)
    structure: list[SectionNode] = field(default_factory=lambda: default_structure())
    file_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "metadata": {
                "name": self.name,
                "author": self.author,
                "description": self.description,
            },
            "format_config": self.format_config.to_dict(),
            "structure": [section.to_dict() for section in self.structure],
        }

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        metadata = data.get("metadata", {})
        return cls(
            name=metadata.get("name", "未命名模板"),
            version=data.get("version", "1.0"),
            author=metadata.get("author", ""),
            description=metadata.get("description", ""),
            format_config=FormatConfig.from_dict(data.get("format_config", {})),
            structure=[SectionNode.from_dict(item) for item in data.get("structure", [])]
            or default_structure(),
        )

    def flatten_structure(self) -> list[tuple[str, SectionNode, int]]:
        result: list[tuple[str, SectionNode, int]] = []

        def walk(nodes: list[SectionNode], parent_id: str, depth: int):
            for node in nodes:
                result.append((parent_id, node, depth))
                if node.subsections:
                    walk(node.subsections, node.id, depth + 1)

        walk(self.structure, "", 0)
        return result

    def find_section(self, section_id: str) -> Optional[SectionNode]:
        for _, node, _ in self.flatten_structure():
            if node.id == section_id:
                return node
        return None

    def find_parent_section(self, section_id: str) -> Optional[SectionNode]:
        for parent_id, _, _ in self.flatten_structure():
            if section_id and parent_id:
                parent = self.find_section(parent_id)
                child = parent and any(item.id == section_id for item in parent.subsections)
                if child:
                    return parent
        return None


def default_structure() -> list[SectionNode]:
    return [
        SectionNode(
            id="cover",
            label="封面",
            type="cover",
            required=True,
            description="自动生成，包含论文标题、作者、学号、专业和导师信息。",
        ),
        SectionNode(
            id="abstract_cn",
            label="中文摘要",
            type="abstract",
            required=True,
            description="概述研究背景、方法、结果和结论，建议 300 到 500 字。",
        ),
        SectionNode(
            id="abstract_en",
            label="英文摘要",
            type="abstract",
            description="中文摘要的英文对应版本，建议与关键词保持一致。",
        ),
        SectionNode(
            id="toc",
            label="目录",
            type="toc",
            required=True,
            auto_generate=True,
            description="导出后在 Word 中更新目录域即可生成目录。",
        ),
        SectionNode(
            id="chapter_1",
            label="第1章 绪论",
            type="chapter",
            required=True,
            style_id="heading_1",
            description="介绍研究背景、研究意义和国内外研究现状。",
            subsections=[
                SectionNode(id="ch1_sec1", label="1.1 研究背景", type="section", style_id="heading_2"),
                SectionNode(id="ch1_sec2", label="1.2 研究意义", type="section", style_id="heading_2"),
                SectionNode(id="ch1_sec3", label="1.3 国内外研究现状", type="section", style_id="heading_2"),
            ],
        ),
        SectionNode(
            id="chapter_2",
            label="第2章 相关技术介绍",
            type="chapter",
            required=True,
            style_id="heading_1",
            description="介绍论文涉及的核心理论、技术和方法。",
        ),
        SectionNode(
            id="chapter_3",
            label="第3章 系统设计与实现",
            type="chapter",
            required=True,
            style_id="heading_1",
            description="详细说明系统设计思路、模块划分与实现过程。",
        ),
        SectionNode(
            id="chapter_4",
            label="第4章 实验结果与分析",
            type="chapter",
            required=True,
            style_id="heading_1",
            description="展示实验结果，并对结果进行分析与讨论。",
        ),
        SectionNode(
            id="chapter_5",
            label="第5章 总结与展望",
            type="chapter",
            required=True,
            style_id="heading_1",
            description="总结全文工作，说明不足并提出后续方向。",
        ),
        SectionNode(
            id="references",
            label="参考文献",
            type="references",
            required=True,
            description="列出论文引用的文献资料，按学校规范编排。",
        ),
        SectionNode(
            id="acknowledgements",
            label="致谢",
            type="acknowledgements",
            required=True,
            style_id="heading_1",
            description="感谢导师、同学和家人的帮助与支持。",
        ),
    ]
