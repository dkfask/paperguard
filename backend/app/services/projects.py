"""模板与项目服务: 内置模板、项目实例化、内容校验。"""

from __future__ import annotations

from app.db import Store, new_id
from app.models.project import ParagraphBlock, ProjectSection, WritingProject
from app.models.template import (
    DEFAULT_STYLES,
    MetaField,
    PaperTemplate,
    SectionType,
    TemplateSection,
)


def default_template() -> PaperTemplate:
    """内置学位论文模板(GB/T 7713 结构 + 默认样式)。"""
    tpl = PaperTemplate(
        id="default-gb7713",
        name="学位论文模板 (GB/T 7713)",
        description="内置默认模板: 封面 / 摘要 / 目录 / 正文 / 参考文献 / 致谢",
        styles={k: v for k, v in DEFAULT_STYLES.items()},
        meta_fields=[
            MetaField(key="school", label="学校名称"),
            MetaField(key="title", label="论文题目"),
            MetaField(key="author", label="作者"),
            MetaField(key="student_id", label="学号"),
            MetaField(key="major", label="专业"),
            MetaField(key="advisor", label="指导教师"),
            MetaField(key="date", label="日期"),
        ],
        sections=[
            TemplateSection(key="cover", type=SectionType.COVER, title="封面", required=True),
            TemplateSection(key="abstract", type=SectionType.ABSTRACT, title="摘要", required=True),
            TemplateSection(key="toc", type=SectionType.TOC, title="目录", required=True),
            TemplateSection(
                key="ch1", type=SectionType.HEADING1, title="第一章 绪论", required=True,
                children=[
                    TemplateSection(key="ch1_1", type=SectionType.HEADING2,
                                    title="1.1 研究背景与意义", required=False),
                    TemplateSection(key="ch1_2", type=SectionType.HEADING2,
                                    title="1.2 国内外研究现状", required=False),
                    TemplateSection(key="ch1_3", type=SectionType.HEADING2,
                                    title="1.3 本文主要工作", required=False),
                ],
            ),
            TemplateSection(
                key="ch2", type=SectionType.HEADING1, title="第二章 相关技术", required=True,
                children=[
                    TemplateSection(key="ch2_1", type=SectionType.HEADING2,
                                    title="2.1 技术概述", required=False),
                    TemplateSection(key="ch2_2", type=SectionType.HEADING2,
                                    title="2.2 关键技术", required=False),
                ],
            ),
            TemplateSection(
                key="ch3", type=SectionType.HEADING1, title="第三章 系统设计", required=True,
            ),
            TemplateSection(
                key="ch4", type=SectionType.HEADING1, title="第四章 系统实现与测试", required=True,
            ),
            TemplateSection(
                key="ch5", type=SectionType.HEADING1, title="第五章 总结与展望", required=True,
            ),
            TemplateSection(key="references", type=SectionType.REFERENCES,
                            title="参考文献", required=True),
            TemplateSection(key="acknowledgement", type=SectionType.ACKNOWLEDGEMENT,
                            title="致谢", required=False),
        ],
    )
    return tpl


def create_project_from_template(store: Store, template: PaperTemplate) -> WritingProject:
    """基于模板实例化写作项目: 展开章节树, 每节生成空段落容器。"""
    sections: list[ProjectSection] = []

    def walk(node: TemplateSection) -> None:
        # 封面由 meta 字段管理, 目录自动生成 —— 都不需要学生写段落
        if node.type in (SectionType.COVER, SectionType.TOC):
            for child in node.children:
                walk(child)
            return
        # 所有需要写作的节(标题节/正文容器/摘要/参考文献等)都带段落列表
        sections.append(ProjectSection(
            key=node.key, type=node.type.value, title=node.title,
            required=node.required,
            paragraphs=[ParagraphBlock(id=f"{node.key}-p1", text="")],
        ))
        for child in node.children:
            walk(child)

    for root in template.sections:
        walk(root)

    return WritingProject(
        id=new_id("proj"),
        template_id=template.id,
        template_name=template.name,
        title=template.name,
        meta={f.key: "" for f in template.meta_fields},
        sections=sections,
    )


def seed_default_template(store: Store) -> None:
    """首次启动时写入内置模板。"""
    if store.get_template("default-gb7713") is None:
        store.save_template(default_template())
