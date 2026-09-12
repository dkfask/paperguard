"""毕业设计过程材料目录与记录契约。

目录来源于学校下发的过程材料、管理表和制度文件。记录本身使用工作流
JSON 存储，目录只负责定义稳定的材料类型、阶段和审批责任。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProcessRecordCreate(BaseModel):
    artifact_type: str = Field(min_length=1, max_length=80)
    data: dict[str, Any] = Field(default_factory=dict)
    note: str = Field(default="", max_length=4000)
    structured_form: bool = False


class ProcessRecordUpdate(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    note: str = Field(default="", max_length=4000)
    base_revision: int | None = None
    structured_form: bool = False


class ProcessDecision(BaseModel):
    decision: str = Field(pattern="^(approved|returned|rejected)$")
    reason: str = Field(default="", max_length=4000)


def _item(
    artifact_type: str,
    name: str,
    phase: str,
    source: str,
    *,
    approvals: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
    scope: str = "participant",
    description: str = "",
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "name": name,
        "phase": phase,
        "source": source,
        "required_approvals": list(approvals),
        "aliases": list(aliases),
        "scope": scope,
        "description": description,
    }


# 学生过程材料（过程清单 1-9 以及答辩、例外和成果材料）。
PROCESS_ARTIFACT_CATALOG: tuple[dict[str, Any], ...] = (
    _item("task_book", "任务书", "选题与开题", "1.学号+姓名+任务书-计软学院.docx", approvals=("teacher",), aliases=("毕业设计（论文）任务书",)),
    _item("opening_report", "开题报告", "选题与开题", "2.学号+姓名+开题报告-计软学院.docx", approvals=("teacher",)),
    _item("guidance_log", "指导过程记录表", "过程指导", "3.学号+姓名+指导过程记录表.docx", approvals=("teacher",), description="资料收集与开题、课题研究与初稿、修改完善与定稿三个阶段。"),
    _item("midterm_check", "中期检查记录", "中期检查", "管理5：XXX学院毕业设计（论文）中期质量检查表（指导教师填报）.xlsx", approvals=("teacher",)),
    _item("final_report", "毕业设计报告（论文）", "定稿与检测", "4.学号+姓名+毕业设计报告（论文）题目全称.docx.txt", aliases=("毕业论文", "毕业设计报告", "终稿")),
    _item("similarity_report", "相似性检测报告（简明版）", "定稿与检测", "5.学号+姓名+毕业设计报告（论文）题目全称简明检测报告.pdf.txt", approvals=("teacher",), aliases=("简明检测报告", "查重报告"), description="A 类 R<40%；B 类须修改后进行第二轮检测；C 类 R>60% 取消答辩资格；优秀成果 R 不超过 15%。"),
    _item("academic_integrity_case", "学术诚信处理记录", "学术诚信", "阅读1-中华人民共和国教育部第40号令：高等学校预防与处理学术不端行为办法.docx", approvals=("college_admin", "admin"), description="记录举报、调查、认定、申诉和处理结果；确认学术不端时取消答辩资格。"),
    _item("result_package", "成果物打包", "定稿与检测", "6.学号+姓名+成果物（包括作品、翻译原件、调查报告等打包）.zip.txt", aliases=("成果物", "作品包")),
    _item("advisor_score", "指导教师成绩评定书", "评阅与答辩资格", "7.学号+姓名+指导教师成绩评定书-计软学院.docx", approvals=("teacher",)),
    _item("reviewer_score", "评阅教师成绩评定书", "评阅与答辩资格", "8.学号+姓名+评阅教师成绩评定书-计软学院.docx", approvals=("teacher",)),
    _item("defense_record", "答辩过程记录表", "答辩", "9.学号+姓名+答辩过程记录表.docx", approvals=("defense_group",)),
    _item("defense_score", "答辩小组成绩评定书", "答辩", "10.学号+姓名+答辩小组成绩评定书-计软学院.docx", approvals=("defense_group",)),
    _item("comprehensive_score", "综合成绩表", "答辩", "11.学号+姓名+综合成绩表-计软学院.docx", approvals=("college_admin",)),
    _item("defense_presentation", "答辩 PPT", "答辩", "12.学号+姓名+答辩PPT.pptx", aliases=("PPT", "答辩演示")),
    _item("defense_application", "答辩申请表", "评阅与答辩资格", "13.学号+姓名+答辩申请表.docx", approvals=("teacher", "college_admin")),
    _item("internship_identification", "实习单位实习鉴定表", "实习与调研", "14.学号+姓名+实习单位实习鉴定表.doc", aliases=("实习鉴定", "校外实习鉴定表")),
    _item("excellence_application", "优秀毕业设计（论文）申请书", "评优", "15.学号+姓名+优秀毕业设计（论文）申请书.docx", approvals=("teacher", "college_admin", "admin")),
    _item("topic_change", "课题变更申请表", "例外审批", "16.学号+姓名课题变更申请表.docx", approvals=("teacher", "college_admin")),
    _item("extension_application", "延期答辩审批表", "例外审批", "17.学号+姓名+特殊情况申请延期毕业设计（论文）答辩审批表.docx", approvals=("teacher", "college_admin", "admin")),
    _item("process_cover", "过程材料封面", "归档", "0-过程性文档封面.doc", aliases=("过程材料", "档案封面")),
)

# 学院/学校管理材料，记录挂在 cohort 上而不是某一名学生身上。
MANAGEMENT_ARTIFACT_CATALOG: tuple[dict[str, Any], ...] = (
    _item("process_material_list", "过程材料清单", "归档", "0-过程材料清单.xlsx", scope="cohort", approvals=("college_admin",)),
    _item("topic_direction_review", "选题（方向）审核汇总", "选题管理", "管理1：XXX学院毕业设计（论文）选题(方向)审核汇总表-计软学院.xlsx", scope="cohort", approvals=("college_admin",)),
    _item("topic_review_summary", "选题审核汇总表", "选题管理", "管理2：XXX学院毕业设计（论文）选题审核汇总表.xlsx", scope="cohort", approvals=("college_admin",)),
    _item("topic_summary", "选题汇总表", "选题管理", "管理3：XXX学院毕业设计（论文）选题汇总表.xlsx", scope="cohort", approvals=("college_admin",)),
    _item("internship_statistics", "校外实习统计表", "实习与调研", "管理4：XXX学院毕业设计（论文）校外实习统计表.xlsx", scope="cohort", approvals=("college_admin",)),
    _item("midterm_quality_check", "中期质量检查表（批次）", "中期检查", "管理5：XXX学院毕业设计（论文）中期质量检查表（指导教师填报）.xlsx", scope="cohort", approvals=("teacher", "college_admin"), description="原始表按学生逐行汇总；学生级中期记录可由此形成。"),
    _item("defense_schedule", "答辩安排表", "答辩", "管理6：XXX学院毕业设计（论文）答辩安排表.xls", scope="cohort", approvals=("college_admin",)),
    _item("defense_inspection", "答辩巡查情况记录表", "答辩", "管理7：XXX学院毕业设计（论文）答辩巡查情况记录表.docx", scope="cohort", approvals=("college_admin",)),
    _item("grade_summary", "成绩汇总表", "成绩与归档", "管理8：XXX学院毕业设计（论文）成绩汇总表.xlsx", scope="cohort", approvals=("college_admin", "admin")),
    _item("college_excellence_application", "优秀毕业设计（论文）申请书（管理）", "评优", "管理9：XXX学院优秀毕业设计（论文）申请书.docx", scope="cohort", approvals=("college_admin", "admin")),
    _item("excellent_supervisor_application", "优秀指导教师申请书", "评优", "管理10：XXX学院毕业设计（论文）优秀指导教师申请书.docx", scope="cohort", approvals=("college_admin", "admin")),
    _item("work_summary", "毕业设计（论文）工作总结", "成绩与归档", "管理11：XXX学院毕业设计（论文）工作总结.docx", scope="cohort", approvals=("college_admin", "admin")),
)

# 制度、通知和样例是规范依据，不是学生或学院可提交的过程材料。
REFERENCE_DOCUMENT_CATALOG: tuple[dict[str, Any], ...] = (
    _item("academic_integrity_policy", "教育部第40号令：学术不端处理办法", "制度依据", "阅读1-中华人民共和国教育部第40号令：高等学校预防与处理学术不端行为办法.docx", scope="reference"),
    _item("sampling_policy", "本科毕业论文抽检办法（试行）", "制度依据", "阅读2-教育部关于印发《本科毕业论文（设计）抽检办法（试行）》的通知.pdf", scope="reference"),
    _item("university_management_policy", "成都东软学院毕业设计（论文）工作管理办法", "制度依据", "阅读3-成都东软学院毕业设计（论文）工作管理办法.pdf", scope="reference"),
    _item("university_writing_standard", "成都东软学院本科生毕业设计报告（论文）撰写规范", "规范依据", "阅读4-成都东软学院本科生毕业设计报告（论文）撰写规范.pdf", scope="reference"),
    _item("similarity_notice", "本科毕业论文相似性检测工作通知", "制度依据", "阅读5-成都东软学院本科毕业论文（报告）“相似性检测”工作通知.docx", scope="reference"),
    _item("thesis_sample", "本科毕业论文样例", "参考样例", "阅读6-成都东软学院本科毕业论文样例.docx", scope="reference"),
    _item("design_report_sample", "本科毕业设计报告样例", "参考样例", "阅读7-成都东软学院本科毕业设计报告样例.docx", scope="reference"),
)

PROCESS_ARTIFACTS = PROCESS_ARTIFACT_CATALOG + MANAGEMENT_ARTIFACT_CATALOG
ARTIFACT_BY_TYPE = {item["artifact_type"]: item for item in PROCESS_ARTIFACTS}
ARTIFACT_ALIASES = {
    alias: item["artifact_type"]
    for item in PROCESS_ARTIFACTS
    for alias in (item["artifact_type"], item["name"], *item["aliases"])
}


def catalog_item(artifact_type: str) -> dict[str, Any] | None:
    canonical = ARTIFACT_ALIASES.get(str(artifact_type).strip(), str(artifact_type).strip())
    return ARTIFACT_BY_TYPE.get(canonical)


def canonical_artifact_type(artifact_type: str) -> str:
    value = str(artifact_type).strip()
    return ARTIFACT_ALIASES.get(value, value)


def _field(key: str, label: str, field_type: str = "text", *, required: bool = False,
           options: tuple[str, ...] = (), help_text: str = "") -> dict[str, Any]:
    return {
        "key": key, "label": label, "type": field_type, "required": required,
        "options": list(options), "help": help_text,
    }


def _table_field(key: str, label: str, columns: tuple[dict[str, Any], ...], *,
                required: bool = False, help_text: str = "") -> dict[str, Any]:
    return {
        **_field(key, label, "table", required=required, help_text=help_text),
        "columns": [dict(column) for column in columns],
    }


_PROCESS_MATERIAL_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"},
    {"key": "file_name", "label": "文件名"},
    {"key": "note", "label": "备注", "type": "textarea"},
)
_TOPIC_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"},
    {"key": "college", "label": "学院"}, {"key": "department", "label": "系"},
    {"key": "major", "label": "专业"}, {"key": "class_name", "label": "行政班号"},
    {"key": "student_id", "label": "学号"}, {"key": "student_name", "label": "姓名"},
    {"key": "topic", "label": "设计（论文）题目"}, {"key": "keywords", "label": "论文关键词"},
    {"key": "research_direction", "label": "论文研究方向"}, {"key": "language", "label": "论文撰写语种"},
    {"key": "thesis_type", "label": "论文类型"}, {"key": "topic_source", "label": "论文选题来源"},
    {"key": "topic_nature", "label": "选题性质"}, {"key": "advisor", "label": "指导教师姓名"},
    {"key": "advisor_title", "label": "指导教师职称"}, {"key": "second_advisor", "label": "第二指导教师姓名"},
    {"key": "second_advisor_title", "label": "第二指导教师职称"}, {"key": "note", "label": "备注", "type": "textarea"},
)
_INTERNSHIP_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"},
    {"key": "college", "label": "学院（部门）名称"}, {"key": "department", "label": "承担单位（系）"},
    {"key": "student_id", "label": "学号"}, {"key": "student_name", "label": "姓名"},
    {"key": "major", "label": "专业"}, {"key": "class_name", "label": "班级"},
    {"key": "internship_unit", "label": "校外实习单位"}, {"key": "address", "label": "实习单位通讯地址"},
    {"key": "contact", "label": "单位联系人"}, {"key": "contact_phone", "label": "单位联系方式"},
    {"key": "position", "label": "岗位"}, {"key": "student_phone", "label": "学生电话"},
    {"key": "parent_phone", "label": "家长电话"}, {"key": "start_at", "label": "实习开始时间"},
    {"key": "planned_end_at", "label": "实习计划结束时间"}, {"key": "health_status", "label": "学生健康状况"},
    {"key": "campus_advisor", "label": "校内指导教师"}, {"key": "counselor", "label": "素质老师"},
    {"key": "note", "label": "备注", "type": "textarea"},
)
_DEFENSE_SCHEDULE_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"}, {"key": "department", "label": "系部"},
    {"key": "class_name", "label": "班级"}, {"key": "student_id", "label": "学号"},
    {"key": "student_name", "label": "姓名"}, {"key": "topic", "label": "毕业设计（论文）题目"},
    {"key": "advisor", "label": "第一指导教师"}, {"key": "second_advisor", "label": "第二指导教师"},
    {"key": "group", "label": "答辩组"}, {"key": "mode", "label": "答辩方式"},
    {"key": "venue", "label": "答辩地点"}, {"key": "defense_at", "label": "答辩时间"},
    {"key": "defense_sequence", "label": "答辩序号", "type": "number"}, {"key": "note", "label": "备注", "type": "textarea"},
)
_DEFENSE_GROUP_COLUMNS = (
    {"key": "department", "label": "系部"}, {"key": "group", "label": "组别"},
    {"key": "chair", "label": "主席"}, {"key": "members", "label": "委员", "type": "textarea"},
    {"key": "secretary", "label": "秘书"}, {"key": "student_count", "label": "答辩学生数", "type": "number"},
    {"key": "schedule", "label": "答辩时间安排"}, {"key": "venue", "label": "地点"},
    {"key": "note", "label": "备注", "type": "textarea"},
)
_DEFENSE_STUDENT_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"}, {"key": "department", "label": "系别"},
    {"key": "major", "label": "专业"}, {"key": "class_name", "label": "班级"},
    {"key": "student_id", "label": "学号"}, {"key": "student_name", "label": "姓名"},
    {"key": "topic", "label": "设计（论文）题目"}, {"key": "topic_nature", "label": "选题性质"},
    {"key": "topic_source", "label": "课题来源"}, {"key": "advisor", "label": "第一指导教师"},
    {"key": "advisor_title", "label": "第一指导教师职称"}, {"key": "second_advisor", "label": "第二指导教师"},
    {"key": "second_advisor_title", "label": "第二指导教师职称"}, {"key": "disqualification_reason", "label": "取消答辩资格以及原因", "type": "textarea"},
    {"key": "note", "label": "备注", "type": "textarea"},
)
_GRADE_COLUMNS = _TOPIC_COLUMNS[:-1] + (
    {"key": "score", "label": "成绩", "type": "number"},
    {"key": "note", "label": "备注", "type": "textarea"},
)
_MIDTERM_COLUMNS = (
    {"key": "student_name", "label": "学生姓名"}, {"key": "student_id", "label": "学号"},
    {"key": "major", "label": "专业"}, {"key": "topic", "label": "毕业设计（论文）题目"},
    {"key": "literature_review", "label": "文献查阅、调研情况", "type": "textarea"},
    {"key": "foreign_literature_relevance", "label": "外文文献内容与课题的相关性", "type": "textarea"},
    {"key": "internship_report", "label": "毕业实习（调研）报告完成情况", "type": "textarea"},
    {"key": "task_progress", "label": "是否按任务书安排完成前阶段的工作"},
    {"key": "opening_report", "label": "开题报告完成情况"}, {"key": "weekly_contact", "label": "是否每周与指导老师联系"},
    {"key": "attitude", "label": "学生工作态度"}, {"key": "guidance_count", "label": "已完成指导次数", "type": "number"},
    {"key": "overall_evaluation", "label": "总体评价", "type": "textarea"},
    {"key": "problems", "label": "存在的主要问题", "type": "textarea"},
    {"key": "next_focus", "label": "下一阶段工作重点", "type": "textarea"},
)
_DIRECTION_COLUMNS = (
    {"key": "major", "label": "适用专业"}, {"key": "direction", "label": "选题（方向）"},
    {"key": "overview", "label": "选题（方向）概述", "type": "textarea"},
)
_TEACHER_COLUMNS = (
    {"key": "teacher", "label": "毕业指导老师"}, {"key": "phone", "label": "手机"},
    {"key": "qq", "label": "QQ"}, {"key": "email", "label": "电子邮件"},
)
_RUBRIC_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"},
    {"key": "item", "label": "评审项目"}, {"key": "indicator", "label": "评审指标", "type": "textarea"},
    {"key": "max_score", "label": "满分", "type": "number"}, {"key": "score", "label": "评分", "type": "number"},
)
_QUESTION_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"},
    {"key": "question", "label": "提问", "type": "textarea"},
    {"key": "answer", "label": "回答", "type": "textarea"},
)
_SCORE_COMPONENT_COLUMNS = (
    {"key": "component", "label": "成绩构成"}, {"key": "score", "label": "成绩", "type": "number"},
    {"key": "weight", "label": "占总成绩比例"}, {"key": "weighted_score", "label": "折算成绩", "type": "number"},
)
_INSPECTION_COLUMNS = (
    {"key": "item", "label": "检查项目"}, {"key": "content", "label": "检查内容", "type": "textarea"},
    {"key": "result", "label": "检查结果", "type": "textarea"}, {"key": "action", "label": "整改要求", "type": "textarea"},
)
_EXCELLENCE_COLUMNS = (
    {"key": "sequence", "label": "序号", "type": "number"}, {"key": "student_id", "label": "学号"},
    {"key": "student_name", "label": "学生姓名"}, {"key": "major", "label": "专业"},
    {"key": "class_name", "label": "班级"}, {"key": "topic", "label": "题目", "type": "textarea"},
    {"key": "advisor", "label": "指导教师"}, {"key": "copy_ratio", "label": "文字复制比", "type": "number"},
    {"key": "recommendation", "label": "推荐意见", "type": "textarea"},
)
_PACKAGE_COLUMNS = (
    {"key": "file_name", "label": "文件名"}, {"key": "file_type", "label": "文件类型"},
    {"key": "description", "label": "内容说明", "type": "textarea"}, {"key": "sha256", "label": "摘要"},
)


# These schemas are the digital contract behind the supplied Word/Excel forms.
# Files remain evidence attachments; the JSON data makes the workflow searchable,
# validatable and exportable without relying on cell coordinates.
PROCESS_FORM_SCHEMAS: dict[str, dict[str, Any]] = {
    "task_book": {
        "title": "毕业设计（论文）任务书", "format": "docx",
        "fields": [
            _field("college", "学院"), _field("department", "系别"), _field("major", "专业"),
            _field("class_name", "班级"), _field("student_id", "学号", required=True),
            _field("student_name", "学生姓名", required=True), _field("advisor", "指导教师", required=True),
            _field("advisor_title", "职称"), _field("topic", "设计（论文）选题方向", required=True),
            _field("topic_nature", "选题性质", "select", required=True, options=("理论性课题", "实践性课题")),
            _field("topic_source", "题目来源", "select", options=("企业项目", "科研课题", "自拟题目", "学校自选项目", "其他")),
            _field("content", "主要内容、任务及要求", "textarea", required=True),
            _field("schedule", "进度安排", "textarea"), _field("references", "主要参考文献", "textarea"),
        ],
    },
    "opening_report": {
        "title": "毕业设计（论文）开题报告", "format": "docx",
        "fields": [
            _field("college", "学院"), _field("department", "系别"), _field("major", "专业"),
            _field("class_name", "班级"), _field("student_id", "学号", required=True),
            _field("student_name", "学生姓名", required=True), _field("advisor", "指导教师", required=True),
            _field("topic", "设计（论文）题目", required=True),
            _field("background", "选题背景", "textarea", required=True),
            _field("purpose", "选题目的", "textarea", required=True),
            _field("significance", "选题意义", "textarea", required=True),
            _field("domestic_status", "国内外研究现状", "textarea"),
            _field("objectives", "研究/设计目标", "textarea", required=True),
            _field("methods", "研究方法与技术路线", "textarea", required=True),
            _field("schedule", "进度安排", "textarea"), _field("references", "参考文献", "textarea"),
        ],
    },
    "guidance_log": {
        "title": "指导过程记录表", "format": "docx", "repeatable": True,
        "fields": [
            _field("college", "学院"), _field("major", "专业"), _field("class_name", "班级"),
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("advisor", "指导教师", required=True), _field("topic", "题目", required=True),
            _field("entries", "三个阶段记录", "json", required=True,
                   help_text="数组元素包含 stage、date、guidance_content、student_feedback、teacher_comment"),
        ],
    },
    "midterm_check": {
        "title": "中期检查记录", "format": "xlsx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("progress", "已完成工作", "textarea", required=True),
            _field("problems", "存在问题", "textarea"), _field("next_plan", "下一步计划", "textarea", required=True),
            _field("advisor_comment", "指导教师意见", "textarea"), _field("score", "中期评价分数", "number"),
        ],
    },
    "similarity_report": {
        "title": "相似性检测报告（简明版）", "format": "pdf",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目"), _field("copy_ratio", "文字复制比", "number", required=True,
                   help_text="0-100；A 类 <40%，B 类 40%-60%，C 类 >60%"),
            _field("report_number", "报告编号"), _field("checked_at", "检测时间", "datetime"),
            _field("engine", "检测系统"),
        ],
    },
    "academic_integrity_case": {
        "title": "学术诚信处理记录", "format": "docx",
        "fields": [
            _field("case_number", "案件编号", required=True), _field("reported_at", "报告时间", "datetime", required=True),
            _field("status", "处理结果", "select", required=True, options=("investigating", "cleared", "confirmed", "cancel_defense")),
            _field("finding", "调查与认定", "textarea", required=True), _field("decision", "处理决定", "textarea", required=True),
            _field("appeal", "申诉处理", "textarea"),
        ],
    },
    "advisor_score": {
        "title": "指导教师成绩评定书", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("score", "指导教师成绩", "number", required=True),
            _field("comment", "指导教师评语", "textarea", required=True),
            _table_field("rubric", "评分指标明细", _RUBRIC_COLUMNS,
                         help_text="对应模板中的序号、评审项目、评审指标、满分和评分。"),
        ],
    },
    "reviewer_score": {
        "title": "评阅教师成绩评定书", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("score", "评阅教师成绩", "number", required=True),
            _field("comment", "评阅意见", "textarea", required=True), _field("reviewer", "评阅教师"),
            _table_field("rubric", "评分指标明细", _RUBRIC_COLUMNS,
                         help_text="对应模板中的序号、评审项目、评审指标、满分和评分。"),
        ],
    },
    "defense_application": {
        "title": "毕业设计（论文）答辩申请表", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("completed_items", "阶段工作完成情况", "json", required=True),
            _field("absence_count", "缺席次数", "number"), _field("total_sessions", "应参加活动次数", "number"),
            _field("statement", "答辩申请承诺", "textarea", required=True),
            _field("teacher_opinion", "指导教师意见", "textarea"),
        ],
    },
    "defense_record": {
        "title": "答辩过程记录表", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("defense_date", "答辩日期", "datetime", required=True),
            _field("venue", "答辩地点"), _field("committee_members", "答辩小组成员", "json", required=True),
            _field("questions", "提问与回答", "json", required=True), _field("conclusion", "答辩结论", "textarea", required=True),
            _field("secretary", "答辩秘书"),
            _table_field("question_rows", "提问与回答明细", _QUESTION_COLUMNS,
                         help_text="逐行记录答辩小组的提问和学生回答。"),
        ],
    },
    "defense_score": {
        "title": "答辩小组成绩评定书", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("paper_score", "论文成绩", "number"),
            _field("preparation_score", "准备过程成绩", "number"), _field("defense_score", "答辩成绩", "number"),
            _field("score", "答辩小组总成绩", "number", required=True), _field("comment", "答辩小组意见", "textarea", required=True),
            _table_field("rubric", "答辩评分标准明细", _RUBRIC_COLUMNS,
                         help_text="对应模板中的论文、准备过程、创新性和答辩表现评分。"),
        ],
    },
    "comprehensive_score": {
        "title": "毕业设计（论文）综合成绩表", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("advisor_score", "指导教师成绩", "number", required=True),
            _field("reviewer_score", "评阅教师成绩", "number", required=True), _field("defense_score", "答辩小组成绩", "number", required=True),
            _field("total_score", "综合成绩", "number", required=True), _field("grade", "等级"),
            _table_field("components", "成绩构成明细", _SCORE_COMPONENT_COLUMNS,
                         help_text="对应指导教师、评阅教师、答辩小组三项成绩及折算比例。"),
        ],
    },
    "topic_change": {
        "title": "课题变更申请表", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("original_topic", "原课题名称", required=True), _field("original_nature", "原选题性质"),
            _field("original_source", "原课题来源"), _field("new_topic", "变更后课题名称", required=True),
            _field("new_nature", "变更后选题性质"), _field("new_source", "变更后课题来源"),
            _field("reason", "变更原因", "textarea", required=True),
        ],
    },
    "extension_application": {
        "title": "延期答辩审批表", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("effective_deadline_at", "延期至", "datetime", required=True), _field("contact", "联系方式"),
            _field("reason", "延期原因", "textarea", required=True),
            _field("counselor_opinion", "素质教师审批意见", "textarea"),
            _field("advisor_opinion", "指导教师审批意见", "textarea"), _field("department_opinion", "系审批意见", "textarea"),
            _field("college_opinion", "学院审批意见", "textarea"),
        ],
    },
    "excellence_application": {
        "title": "优秀毕业设计（论文）申请书", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("copy_ratio", "文字复制比", "number", required=True),
            _field("recommendation", "指导教师推荐意见", "textarea", required=True),
        ],
    },
    "final_report": {
        "title": "毕业设计报告（论文）", "format": "docx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("version_label", "版本标识"),
            _field("content", "终稿说明", "textarea"),
        ],
    },
    "result_package": {
        "title": "成果物打包清单", "format": "zip",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目"), _table_field("files", "成果物文件清单", _PACKAGE_COLUMNS),
            _field("description", "成果物说明", "textarea"),
        ],
    },
    "defense_presentation": {
        "title": "答辩 PPT", "format": "pptx",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("topic", "题目", required=True), _field("slide_count", "幻灯片页数", "number"),
            _field("speaker_notes", "答辩讲稿/备注", "textarea"),
        ],
    },
    "internship_identification": {
        "title": "实习单位实习鉴定表", "format": "doc",
        "fields": [
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("internship_unit", "实习单位", required=True), _field("position", "实习岗位"),
            _field("start_at", "实习开始时间", "date"), _field("end_at", "实习结束时间", "date"),
            _field("unit_evaluation", "单位鉴定意见", "textarea", required=True),
            _field("unit_contact", "单位联系人"), _field("signed_at", "签章日期", "date"),
        ],
    },
    "process_cover": {
        "title": "过程材料封面", "format": "doc",
        "fields": [
            _field("college", "学院"), _field("graduation_year", "毕业届别"),
            _field("student_id", "学号", required=True), _field("student_name", "学生姓名", required=True),
            _field("major", "专业"), _field("class_name", "班级"), _field("advisor", "指导教师"),
            _table_field("materials", "过程材料清单", _PROCESS_MATERIAL_COLUMNS), _field("archive_number", "档案编号"),
        ],
    },
}

MANAGEMENT_FORM_SCHEMAS: dict[str, dict[str, Any]] = {
    "process_material_list": {"title": "过程材料清单", "format": "xlsx", "fields": [_table_field("rows", "学生过程材料明细", _PROCESS_MATERIAL_COLUMNS, required=True), _field("generated_by", "编制人", required=True), _field("conclusion", "完成情况", "textarea")]},
    "topic_direction_review": {"title": "选题（方向）审核汇总", "format": "xlsx", "fields": [_table_field("direction_rows", "选题（方向）明细", _DIRECTION_COLUMNS, required=True), _table_field("teacher_rows", "指导教师明细", _TEACHER_COLUMNS), _field("review_date", "审核日期", "date", required=True), _field("reviewer", "审核负责人", required=True), _field("conclusion", "汇总结论", "textarea", required=True)]},
    "topic_review_summary": {"title": "选题审核汇总表", "format": "xlsx", "fields": [_table_field("rows", "选题审核明细", _TOPIC_COLUMNS, required=True), _field("review_date", "审核日期", "date", required=True), _field("conclusion", "汇总结论", "textarea", required=True)]},
    "topic_summary": {"title": "选题汇总表", "format": "xlsx", "fields": [_table_field("rows", "选题汇总明细", _TOPIC_COLUMNS, required=True), _field("generated_by", "汇总人", required=True)]},
    "internship_statistics": {"title": "校外实习统计表", "format": "xlsx", "fields": [_table_field("rows", "实习学生明细", _INTERNSHIP_COLUMNS, required=True), _field("total", "实习人数", "number", required=True)]},
    "midterm_quality_check": {"title": "中期质量检查表（批次）", "format": "xlsx", "fields": [_field("college", "学院"), _field("department", "系别"), _field("graduation_year", "毕业届别"), _field("advisor", "指导教师"), _field("review_date", "检查日期", "date", required=True), _table_field("rows", "学生中期检查明细", _MIDTERM_COLUMNS, required=True, help_text="每行填写一名学生的中期检查结果"), _field("conclusion", "总体评价", "textarea")]},
    "defense_schedule": {"title": "答辩安排表", "format": "xlsx", "fields": [_table_field("schedule_rows", "答辩名单与安排", _DEFENSE_SCHEDULE_COLUMNS), _table_field("group_rows", "答辩委员会分组安排", _DEFENSE_GROUP_COLUMNS), _table_field("student_rows", "答辩学生名单", _DEFENSE_STUDENT_COLUMNS), _field("defense_date", "答辩日期", "date", required=True), _field("groups", "兼容旧版答辩小组数据", "json"), _field("secretary", "答辩秘书", required=True)]},
    "defense_inspection": {"title": "答辩巡查情况记录表", "format": "docx", "fields": [_field("inspection_date", "巡查日期", "date", required=True), _field("inspector", "巡查人", required=True), _table_field("checklist", "巡查检查明细", _INSPECTION_COLUMNS), _field("findings", "巡查情况", "textarea", required=True), _field("actions", "整改要求", "textarea")]},
    "grade_summary": {"title": "成绩汇总表", "format": "xlsx", "fields": [_table_field("rows", "学生成绩明细", _GRADE_COLUMNS, required=True), _field("total", "学生人数", "number", required=True), _field("approved_by", "审核人", required=True)]},
    "college_excellence_application": {"title": "优秀毕业设计（论文）申请书（管理）", "format": "docx", "fields": [_table_field("rows", "推荐学生明细", _EXCELLENCE_COLUMNS, required=True), _field("college_opinion", "学院意见", "textarea", required=True), _field("teaching_affairs_opinion", "教务处意见", "textarea")]},
    "excellent_supervisor_application": {"title": "优秀指导教师申请书", "format": "docx", "fields": [_field("teacher_name", "指导教师姓名"), _field("department", "所在系部"), _field("title", "职称"), _field("education", "学历学位"), _field("years", "指导年限", "number"), _field("student_results", "指导学生成果", "textarea"), _field("work_effects", "工作成效", "textarea"), _field("recommendation", "学院推荐意见", "textarea", required=True), _field("college_opinion", "学院意见", "textarea")]},
    "work_summary": {"title": "毕业设计（论文）工作总结", "format": "docx", "fields": [_field("college", "学院"), _field("graduation_year", "毕业届别"), _field("basic_situation", "基本情况", "textarea"), _field("execution", "工作执行情况", "textarea"), _field("measures", "工作措施与成效", "textarea"), _field("summary", "工作总结", "textarea", required=True), _field("problems", "存在问题", "textarea"), _field("improvement", "改进措施", "textarea"), _field("next_plan", "后续计划", "textarea")]},
}

REFERENCE_DOCUMENTS = REFERENCE_DOCUMENT_CATALOG


def artifact_form_schema(artifact_type: str) -> dict[str, Any]:
    canonical = canonical_artifact_type(artifact_type)
    schema = PROCESS_FORM_SCHEMAS.get(canonical) or MANAGEMENT_FORM_SCHEMAS.get(canonical)
    if schema is None:
        return {"title": catalog_item(canonical)["name"] if catalog_item(canonical) else canonical,
                "format": "docx", "fields": [
                    _field("title", "材料标题"), _field("content", "结构化内容", "textarea"),
                ]}
    return {"artifact_type": canonical, **schema}
