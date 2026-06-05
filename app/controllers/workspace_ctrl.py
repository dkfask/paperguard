"""Controller for top-level workspace switching."""

from __future__ import annotations


class WorkspaceController:
    WORKSPACES = {
        "dashboard": "首页工作台",
        "template": "模板中心",
        "project": "项目写作",
        "review": "检查与导出",
        "help": "资源与帮助",
    }

    def __init__(self):
        self.current_workspace = "dashboard"

    def set_workspace(self, workspace: str):
        if workspace in self.WORKSPACES:
            self.current_workspace = workspace

    def current_title(self) -> str:
        return self.WORKSPACES[self.current_workspace]
