"""Create and expose built-in demo assets."""

from __future__ import annotations

from pathlib import Path

from app.controllers.export_ctrl import ExportController
from app.controllers.project_ctrl import ProjectController
from app.services.template_parser import load_template


APP_DATA_DIR = Path.home() / ".thesis_template_tool"
DEMO_DIR = APP_DATA_DIR / "demo"


class DemoSeedService:
    """Prepare sample template, project, and reference export."""

    def __init__(self, base_dir: Path | None = None):
        self.demo_dir = base_dir or DEMO_DIR
        self.demo_dir.mkdir(parents=True, exist_ok=True)
        self.sample_template_path = next(
            (Path(__file__).parent.parent / "resources" / "samples").glob("*.ptpl")
        )
        self.demo_project_path = self.demo_dir / "demo_project.pprj"
        self.reference_docx_path = self.demo_dir / "demo_reference.docx"

    def ensure_demo_assets(self) -> dict[str, str]:
        template = load_template(self.sample_template_path)
        if not self.demo_project_path.exists():
            self._create_demo_project(template)
        if not self.reference_docx_path.exists():
            self._create_demo_reference(template)
        return {
            "template_path": str(self.sample_template_path),
            "project_path": str(self.demo_project_path),
            "reference_docx_path": str(self.reference_docx_path),
        }

    def _create_demo_project(self, template):
        controller = ProjectController()
        project = controller.new_from_template(template)
        project.title = "基于桌面模板系统的本科论文示例"
        project.author = "示例学生"
        project.student_id = "20260001"
        project.major = "计算机科学与技术"
        project.supervisor = "王老师"
        controller.update_content(
            "abstract_cn",
            (
                "<p>本示例项目用于演示论文模板规范软件的完整流程。</p>"
                "<p><span style=\"font-weight:600;\">用户可以直接打开示例项目查看章节状态、问题检查和 DOCX 导出效果。</span></p>"
            ),
        )
        controller.update_content(
            "chapter_1",
            (
                "<p>第一章展示正文、列表、引用和基础富文本。</p>"
                "<ul><li>打开模板创建项目</li><li>填写论文元信息</li><li>执行格式检查并导出</li></ul>"
                "<blockquote><p>这是一段演示用引用内容，用来体现版式和导出效果。</p></blockquote>"
            ),
        )
        controller.update_content(
            "ch1_sec1",
            "<p>研究背景：高校论文写作对结构和版式有较强约束，因此需要低门槛的模板化工具。</p>",
        )
        controller.update_content("references", "<p>[1] 示例参考文献</p>")
        controller.update_content("acknowledgements", "<p>感谢示例教师与学生共同完成演示数据准备。</p>")
        controller.save_project(self.demo_project_path)

    def _create_demo_reference(self, template):
        controller = ProjectController()
        project = controller.open_project(self.demo_project_path)
        ExportController.export_to_docx(template, project, self.reference_docx_path)
