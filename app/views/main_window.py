"""Main application window with product-style workspaces."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAction,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
)

from app.controllers.export_ctrl import ExportController
from app.controllers.project_ctrl import ProjectController
from app.controllers.review_ctrl import ReviewController
from app.controllers.template_ctrl import TemplateController
from app.controllers.workspace_ctrl import WorkspaceController
from app.services.demo_seed_service import DemoSeedService
from app.services.docx_review_session import DocxReviewSession
from app.services.navigation_state_service import NavigationStateService
from app.services.recent_items_service import RecentItemsService
from app.services.template_parser import load_template, save_template
from app.services.word_template_importer import import_word_template
from app.views.dashboard_view import DashboardView
from app.views.help_workspace_view import HelpWorkspaceView
from app.views.project_workspace_view import ProjectWorkspaceView
from app.views.review_export_view import ReviewExportView
from app.views.template_workspace_view import TemplateWorkspaceView


class MainWindow(QMainWindow):
    """Top-level window that coordinates all workspaces."""

    def __init__(self):
        super().__init__()
        self.template_ctrl = TemplateController()
        self.project_ctrl = ProjectController()
        self.export_ctrl = ExportController()
        self.review_ctrl = ReviewController()
        self.workspace_ctrl = WorkspaceController()
        self.navigation_state = NavigationStateService()
        self.recent_service = RecentItemsService()
        self.demo_service = DemoSeedService()

        self.current_template_path: str = ""
        self.last_export_path: str = ""
        self.last_saved_label = "尚未保存"
        self.is_dirty = False
        self.docx_review_session: DocxReviewSession | None = None
        self.last_docx_review_export_path: str = ""

        self._setup_ui()
        self._create_menus()
        self._connect_signals()
        self._refresh_all()

    def _setup_ui(self):
        self.setWindowTitle("论文模板规范软件")
        self.resize(1400, 900)

        splitter = QSplitter(Qt.Horizontal)

        self.nav_list = QListWidget()
        self.nav_list.setMaximumWidth(180)
        for workspace_id, label in self.workspace_ctrl.WORKSPACES.items():
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, workspace_id)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(0)
        splitter.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        self.dashboard_view = DashboardView()
        self.template_workspace = TemplateWorkspaceView()
        self.project_workspace = ProjectWorkspaceView()
        self.review_workspace = ReviewExportView()
        self.help_workspace = HelpWorkspaceView()

        for widget in [
            self.dashboard_view,
            self.template_workspace,
            self.project_workspace,
            self.review_workspace,
            self.help_workspace,
        ]:
            self.stack.addWidget(widget)
        splitter.addWidget(self.stack)
        splitter.setSizes([180, 1200])
        self.setCentralWidget(splitter)

        self.status_bar = QStatusBar()
        self.status_label = QLabel("就绪")
        self.object_label = QLabel("当前工作：首页工作台")
        self.save_state_label = QLabel("状态：已保存")
        self.status_bar.addWidget(self.status_label, 1)
        self.status_bar.addPermanentWidget(self.object_label)
        self.status_bar.addPermanentWidget(self.save_state_label)
        self.setStatusBar(self.status_bar)

    def _create_menus(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("文件")

        action_new_template = QAction("新建模板", self)
        action_new_template.triggered.connect(self._new_template)
        file_menu.addAction(action_new_template)

        action_import_word = QAction("导入 Word 模板...", self)
        action_import_word.triggered.connect(self._import_word_template_dialog)
        file_menu.addAction(action_import_word)

        action_load_template = QAction("加载模板...", self)
        action_load_template.triggered.connect(self._load_template_dialog)
        file_menu.addAction(action_load_template)

        action_new_project = QAction("从模板创建项目", self)
        action_new_project.triggered.connect(self._new_project)
        file_menu.addAction(action_new_project)

        action_open_project = QAction("打开项目...", self)
        action_open_project.triggered.connect(self._open_project)
        file_menu.addAction(action_open_project)

        file_menu.addSeparator()

        action_save_project = QAction("保存项目", self)
        action_save_project.setShortcut("Ctrl+S")
        action_save_project.triggered.connect(self._save_project)
        file_menu.addAction(action_save_project)

        action_save_project_as = QAction("项目另存为...", self)
        action_save_project_as.triggered.connect(self._save_project_as)
        file_menu.addAction(action_save_project_as)

        action_publish_template = QAction("发布模板...", self)
        action_publish_template.triggered.connect(self._publish_template)
        file_menu.addAction(action_publish_template)

        file_menu.addSeparator()

        action_open_demo = QAction("打开示例项目", self)
        action_open_demo.triggered.connect(self._open_demo_project)
        file_menu.addAction(action_open_demo)

        tools_menu = menu_bar.addMenu("工具")
        action_validate = QAction("格式检查", self)
        action_validate.triggered.connect(self._validate_project)
        tools_menu.addAction(action_validate)

        action_export = QAction("导出 DOCX", self)
        action_export.triggered.connect(self._export_docx)
        tools_menu.addAction(action_export)

    def _connect_signals(self):
        self.nav_list.currentItemChanged.connect(self._on_workspace_changed)

        self.dashboard_view.create_template_requested.connect(self._new_template)
        self.dashboard_view.create_project_requested.connect(self._new_project)
        self.dashboard_view.open_demo_requested.connect(self._open_demo_project)
        self.dashboard_view.open_recent_template_requested.connect(self._open_template_path)
        self.dashboard_view.open_recent_project_requested.connect(self._open_project_path)
        self.dashboard_view.role_selected.connect(self._on_role_selected)

        self.template_workspace.create_template_requested.connect(self._new_template)
        self.template_workspace.import_word_template_requested.connect(self._import_word_template_dialog)
        self.template_workspace.open_template_requested.connect(self._open_template_path)
        self.template_workspace.publish_template_requested.connect(self._publish_template)
        self.template_workspace.apply_template_requested.connect(self._on_template_applied)
        self.template_workspace.filter_combo.currentTextChanged.connect(
            lambda _: self._refresh_template_sources()
        )

        self.project_workspace.create_project_requested.connect(self._new_project)
        self.project_workspace.content_changed.connect(self._on_content_changed)
        self.project_workspace.metadata_changed.connect(self._on_metadata_changed)
        self.project_workspace.section_selected.connect(self._on_section_selected)

        self.review_workspace.validate_requested.connect(self._validate_project)
        self.review_workspace.export_requested.connect(self._export_docx_with_name)
        self.review_workspace.jump_to_target_requested.connect(self._jump_to_target)
        self.review_workspace.docx_import_requested.connect(self._import_docx_review_dialog)
        self.review_workspace.docx_check_requested.connect(self._check_docx_review_from_ui)
        self.review_workspace.docx_fix_requested.connect(self._fix_docx_review_from_ui)
        self.review_workspace.docx_fix_one_requested.connect(self._fix_one_docx_review_from_ui)
        self.review_workspace.docx_export_requested.connect(self._export_docx_review_dialog)

        self.help_workspace.open_demo_requested.connect(self._open_demo_project)
        self.help_workspace.open_sample_template_requested.connect(self._load_sample_template)

    def _on_workspace_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None):
        if current is None:
            return
        workspace_id = current.data(Qt.UserRole)
        self.workspace_ctrl.set_workspace(workspace_id)
        self.navigation_state.set_workspace(workspace_id)
        mapping = {
            "dashboard": self.dashboard_view,
            "template": self.template_workspace,
            "project": self.project_workspace,
            "review": self.review_workspace,
            "help": self.help_workspace,
        }
        self.stack.setCurrentWidget(mapping[workspace_id])
        self.object_label.setText(f"当前工作：{self.workspace_ctrl.current_title()}")

    def _on_role_selected(self, role: str):
        self.navigation_state.set_role(role)
        self.dashboard_view.set_role(role)
        self._show_workspace("template" if role == "teacher" else "project")

    def _show_workspace(self, workspace_id: str):
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            if item.data(Qt.UserRole) == workspace_id:
                self.nav_list.setCurrentRow(row)
                break

    def _refresh_all(self):
        self.dashboard_view.set_role(self.navigation_state.state.current_role)
        self._refresh_dashboard()
        self._refresh_template_sources()
        self._refresh_project_workspace()
        self._refresh_review_workspace()
        self._update_title()

    def _refresh_dashboard(self):
        self.dashboard_view.set_recent_items(
            self.recent_service.list_templates(),
            self.recent_service.list_projects(),
            self.recent_service.has_history(),
        )

    def _refresh_template_sources(self):
        builtin = list((Path(__file__).parent.parent / "resources" / "samples").glob("*.ptpl"))
        self.template_workspace.set_template_sources(
            builtin_templates=builtin,
            recent_templates=self.recent_service.list_templates(),
        )
        self.template_workspace.load_template(self.project_ctrl.template)

    def _build_review_summary(self):
        return self.review_ctrl.build_summary(self.project_ctrl.project, self.project_ctrl.template)

    def _refresh_project_workspace(self):
        summary = self._build_review_summary()
        progress_text = (
            f"必填章节完成度：{summary.completed_required}/{summary.total_required}；"
            f"必须修复 {len(summary.errors)} 项，建议修复 {len(summary.warnings)} 项；"
            f"{self.last_saved_label}"
        )
        self.project_workspace.set_context(
            template=self.project_ctrl.template,
            project=self.project_ctrl.project,
            statuses=summary.statuses,
            completed_required=summary.completed_required,
            total_required=summary.total_required,
            review_text=progress_text,
        )
        current_section = self.navigation_state.state.current_section_id
        if (
            current_section
            and self.project_ctrl.template
            and self.project_ctrl.template.find_section(current_section)
        ):
            self.project_workspace.show_section(current_section)

    def _refresh_review_workspace(self):
        summary = self._build_review_summary()
        self.review_workspace.set_context(
            template_name=self.project_ctrl.template.name if self.project_ctrl.template else "",
            project_title=self.project_ctrl.project.title,
            summary=summary,
            export_filename=self.export_ctrl.get_default_filename(self.project_ctrl.project),
            last_export_path=self.last_export_path,
        )

    def _mark_dirty(self, dirty: bool = True):
        self.is_dirty = dirty
        self.save_state_label.setText("状态：未保存" if dirty else "状态：已保存")

    def _set_status(self, message: str):
        self.status_label.setText(message)

    def _update_title(self):
        title = "论文模板规范软件"
        if self.project_ctrl.project.title:
            title += f" - {self.project_ctrl.project.title}"
        self.setWindowTitle(title)

    def _new_template(self):
        template = self.template_ctrl.create_new("新模板")
        self._apply_template(template, reset_project=False)
        self._mark_dirty(True)
        self._set_status("已创建新模板")
        self._show_workspace("template")

    def _import_word_template_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Word 模板",
            "",
            "Word 模板 (*.docx *.dotx)",
        )
        if not path:
            return
        self._import_word_template(path)

    def _import_word_template(self, path: str):
        try:
            template = import_word_template(path)
        except Exception as error:
            QMessageBox.critical(self, "导入失败", str(error))
            return

        self._apply_template(template, reset_project=False)
        self._mark_dirty(True)
        self._set_status(f"已导入 Word 模板：{Path(path).name}")
        self._show_workspace("template")
        QMessageBox.information(
            self,
            "导入成功",
            "已导入页面设置与样式。章节结构仍使用系统默认结构，请在模板中心按需继续编辑并保存为 .ptpl。",
        )

    def _load_template_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开模板",
            "",
            "论文模板 (*.ptpl);;所有文件 (*)",
        )
        if path:
            self._open_template_path(path)

    def _open_template_path(self, path: str):
        try:
            template = load_template(path)
            self._apply_template(template, reset_project=False)
            self.recent_service.add_template(path, template.name, "模板中心")
            self._mark_dirty(False)
            self._set_status(f"已加载模板：{template.name}")
            self._show_workspace("template")
        except Exception as error:
            QMessageBox.critical(self, "加载模板失败", str(error))

    def _load_sample_template(self):
        assets = self.demo_service.ensure_demo_assets()
        self._open_template_path(assets["template_path"])

    def _on_template_applied(self):
        template = self.template_workspace.current_template
        if template is None:
            return
        self._apply_template(template, reset_project=False)
        self._mark_dirty(True)
        self._set_status("模板修改已应用到当前工作区")

    def _apply_template(self, template, reset_project: bool = False):
        self.project_ctrl.set_template(template, reset_project=reset_project)
        self.current_template_path = template.file_path or ""
        self.template_workspace.load_template(template)
        self._refresh_project_workspace()
        self._refresh_review_workspace()
        self._update_title()

    def _publish_template(self):
        template = self.project_ctrl.template or self.template_workspace.current_template
        if template is None:
            QMessageBox.warning(self, "提示", "请先创建、导入或加载模板。")
            return
        default_name = template.file_path or template.name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "发布模板",
            default_name,
            "论文模板 (*.ptpl)",
        )
        if not path:
            return
        save_template(template, path)
        self.project_ctrl.set_template(template, reset_project=False)
        self.recent_service.add_template(path, template.name, "已发布模板")
        self._mark_dirty(False)
        self._set_status(f"模板已发布：{Path(path).name}")
        self._refresh_all()

    def _new_project(self):
        if not self.project_ctrl.template:
            QMessageBox.warning(self, "提示", "请先在模板中心创建、导入或加载模板。")
            return
        self.project_ctrl.new_from_template(self.project_ctrl.template)
        self.navigation_state.set_section("")
        self.last_saved_label = "尚未保存到文件，当前编辑已自动记录到内存"
        self._mark_dirty(True)
        self._set_status("已基于当前模板创建论文项目")
        self._refresh_project_workspace()
        self._refresh_review_workspace()
        self._show_workspace("project")

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目",
            "",
            "论文项目 (*.pprj);;所有文件 (*)",
        )
        if path:
            self._open_project_path(path)

    def _open_project_path(self, path: str):
        try:
            self.project_ctrl.open_project(path)
            template_loaded = self._restore_template_for_project()
            self.recent_service.add_project(
                path,
                self.project_ctrl.project.title or Path(path).stem,
                "论文项目",
            )
            self.last_saved_label = f"最近保存文件：{Path(path).name}"
            self._mark_dirty(False)
            self._set_status(f"已打开项目：{Path(path).name}")
            self._refresh_project_workspace()
            self._refresh_review_workspace()
            self._show_workspace("project")
            if not template_loaded:
                QMessageBox.warning(
                    self,
                    "模板未找到",
                    "项目已打开，但未能自动找到关联模板，请到模板中心手动加载模板。",
                )
        except Exception as error:
            QMessageBox.critical(self, "打开项目失败", str(error))

    def _open_demo_project(self):
        assets = self.demo_service.ensure_demo_assets()
        self._open_project_path(assets["project_path"])

    def _restore_template_for_project(self) -> bool:
        project = self.project_ctrl.project
        if project.template_path and Path(project.template_path).exists():
            self._apply_template(load_template(project.template_path), reset_project=False)
            return True
        if project.template_name and self._find_and_load_template(project.template_name):
            return True
        self.project_ctrl.template = None
        return False

    def _find_and_load_template(self, name: str) -> bool:
        search_dirs = [Path(__file__).parent.parent / "resources" / "samples"]
        for directory in search_dirs:
            for path in directory.glob("*.ptpl"):
                try:
                    template = load_template(path)
                except Exception:
                    continue
                if template.name == name:
                    self._apply_template(template, reset_project=False)
                    return True
        return False

    def _save_project(self):
        path = self.project_ctrl.project.file_path
        if not path:
            self._save_project_as()
            return
        self.project_ctrl.save_project(path)
        self.recent_service.add_project(path, self.project_ctrl.project.title or Path(path).stem, "论文项目")
        self.last_saved_label = f"最近保存文件：{Path(path).name}"
        self._mark_dirty(False)
        self._set_status(f"项目已保存：{Path(path).name}")
        self._refresh_all()

    def _save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存项目",
            "",
            "论文项目 (*.pprj)",
        )
        if not path:
            return
        self.project_ctrl.save_project(path)
        self.recent_service.add_project(path, self.project_ctrl.project.title or Path(path).stem, "论文项目")
        self.last_saved_label = f"最近保存文件：{Path(path).name}"
        self._mark_dirty(False)
        self._set_status(f"项目已保存：{Path(path).name}")
        self._refresh_all()

    def _on_content_changed(self, section_id: str, html: str):
        self.project_ctrl.update_content(section_id, html)
        self.navigation_state.set_section(section_id)
        self._mark_dirty(True)
        self._refresh_project_workspace()
        self._refresh_review_workspace()

    def _on_metadata_changed(self, metadata: dict):
        self.project_ctrl.set_metadata(**metadata)
        self._mark_dirty(True)
        self._refresh_project_workspace()
        self._refresh_review_workspace()
        self._update_title()

    def _on_section_selected(self, section_id: str):
        self.navigation_state.set_section(section_id)
        self.project_workspace.show_section(section_id)

    def _jump_to_target(self, target: str):
        self.navigation_state.set_target(target)
        self._show_workspace("project")
        self.project_workspace.focus_target(target)

    def _validate_project(self):
        self._refresh_project_workspace()
        self._refresh_review_workspace()
        self._set_status("已更新检查结果")
        self._show_workspace("review")

    def _import_docx_review_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 DOCX 批改",
            "",
            "Word 文档 (*.docx)",
        )
        if not path:
            return
        try:
            self._open_docx_review_path(path, pack_id=self.review_workspace.selected_docx_pack())
        except Exception as error:
            QMessageBox.critical(self, "导入 DOCX 失败", str(error))

    def _open_docx_review_path(
        self,
        path: str | Path,
        *,
        pack_id: str = "GB-T-7713-1-2006",
    ) -> DocxReviewSession:
        session = DocxReviewSession.open(path, pack_id=pack_id)
        self.docx_review_session = session
        self.last_docx_review_export_path = ""
        source = Path(path)
        self.review_workspace.set_docx_review_state(
            source_path=str(source),
            status=f"已导入，规则包：{pack_id}。点击“检查”查看问题。",
            can_check=True,
            can_fix=False,
            can_export=False,
        )
        self.review_workspace.docx_export_name.setText(f"{source.stem}.fixed.docx")
        self._set_status(f"已导入 DOCX：{source.name}")
        self._show_workspace("review")
        return session

    def _check_docx_review_from_ui(self):
        try:
            self._check_docx_review_session()
        except Exception as error:
            QMessageBox.warning(self, "DOCX 检查失败", str(error))

    def _check_docx_review_session(self):
        if self.docx_review_session is None:
            raise ValueError("请先导入 DOCX 文件。")
        findings = self.docx_review_session.check()
        source_path = self.docx_review_session.source_path
        if findings:
            status = f"发现 {len(findings)} 个格式问题，可尝试自动修复。"
        else:
            status = "未发现格式问题，可直接导出副本。"
        self.review_workspace.set_docx_review_state(
            source_path=str(source_path),
            status=status,
            export_path=self.last_docx_review_export_path,
            can_check=True,
            can_fix=bool(findings),
            can_export=not bool(findings),
            findings=findings,
        )
        self._set_status(status)
        return findings

    def _fix_docx_review_from_ui(self):
        try:
            self._fix_docx_review_session()
        except Exception as error:
            QMessageBox.warning(self, "DOCX 修复失败", str(error))

    def _fix_docx_review_session(self):
        if self.docx_review_session is None:
            raise ValueError("请先导入 DOCX 文件。")
        summary = self.docx_review_session.apply_all_fixes()
        source_path = self.docx_review_session.source_path
        status = (
            f"已自动修复 {summary.auto_fixed_count} 项；"
            f"跳过 {summary.skipped_count} 项；"
            f"剩余 {summary.remaining_count} 项。"
        )
        self.review_workspace.set_docx_review_state(
            source_path=str(source_path),
            status=status,
            export_path=self.last_docx_review_export_path,
            can_check=True,
            can_fix=summary.remaining_count > 0,
            can_export=True,
            findings=self.docx_review_session.last_findings,
        )
        self._set_status(status)
        return summary

    def _fix_one_docx_review_from_ui(self, index: int):
        try:
            self._fix_one_docx_review_finding(index)
        except Exception as error:
            QMessageBox.warning(self, "DOCX 单项修复失败", str(error))

    def _fix_one_docx_review_finding(self, index: int):
        if self.docx_review_session is None:
            raise ValueError("请先导入 DOCX 文件。")
        result = self.docx_review_session.apply_fix_at(index)
        source_path = self.docx_review_session.source_path
        action = "已修复" if result.applied else "未能自动修复"
        status = f"{action} {result.finding.rule_id}；剩余 {result.remaining_count} 项。"
        self.review_workspace.set_docx_review_state(
            source_path=str(source_path),
            status=status,
            export_path=self.last_docx_review_export_path,
            can_check=True,
            can_fix=result.remaining_count > 0,
            can_export=True,
            findings=self.docx_review_session.last_findings,
        )
        self._set_status(status)
        return result

    def _export_docx_review_dialog(self, file_name: str):
        if self.docx_review_session is None:
            QMessageBox.warning(self, "提示", "请先导入 DOCX 文件。")
            return
        suggested = file_name or f"{self.docx_review_session.source_path.stem}.fixed.docx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出修复后 DOCX",
            suggested,
            "Word 文档 (*.docx)",
        )
        if not path:
            return
        try:
            self._export_fixed_docx_review_path(path)
            QMessageBox.information(self, "导出成功", f"修复后 DOCX 已导出到：\n{path}")
        except Exception as error:
            QMessageBox.critical(self, "导出失败", str(error))

    def _export_fixed_docx_review_path(self, path: str | Path) -> Path:
        if self.docx_review_session is None:
            raise ValueError("请先导入 DOCX 文件。")
        exported = self.docx_review_session.export(path)
        self.last_docx_review_export_path = str(exported)
        self.review_workspace.set_docx_review_state(
            source_path=str(self.docx_review_session.source_path),
            status="已导出修复后 DOCX。",
            export_path=str(exported),
            can_check=True,
            can_fix=bool(self.docx_review_session.last_findings),
            can_export=True,
            findings=self.docx_review_session.last_findings,
        )
        self._set_status(f"已导出修复后 DOCX：{exported.name}")
        self._show_workspace("review")
        return exported

    def _export_docx_with_name(self, file_name: str):
        self._export_docx(file_name=file_name)

    def _export_docx(self, file_name: str | None = None):
        if not self.project_ctrl.template:
            QMessageBox.warning(self, "提示", "请先加载模板。")
            return

        summary = self._build_review_summary()
        if summary.errors:
            QMessageBox.warning(
                self,
                "无法导出",
                "当前存在必须修复的问题，请先进入“检查与导出”工作区处理。",
            )
            self._show_workspace("review")
            return

        if summary.warnings:
            reply = QMessageBox.question(
                self,
                "继续导出？",
                "当前仍有建议修复的问题，是否继续导出？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._show_workspace("review")
                return

        suggested = file_name or self.export_ctrl.get_default_filename(self.project_ctrl.project)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 DOCX",
            suggested,
            "Word 文档 (*.docx)",
        )
        if not path:
            return

        try:
            result = self.export_ctrl.export_to_docx(
                self.project_ctrl.template,
                self.project_ctrl.project,
                path,
            )
            self.last_export_path = result
            self._set_status(f"已导出：{Path(result).name}")
            self._refresh_review_workspace()
            self._show_workspace("review")
            QMessageBox.information(
                self,
                "导出成功",
                (
                    f"论文已导出到：\n{result}\n\n"
                    "当前支持基础富文本、列表、引用、图片、表格、章节标题和目录导出。"
                ),
            )
        except Exception as error:
            QMessageBox.critical(self, "导出失败", str(error))
