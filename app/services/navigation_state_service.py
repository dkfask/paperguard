"""Navigation state shared across workspaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NavigationState:
    current_workspace: str = "dashboard"
    current_role: str = "student"
    current_section_id: str = ""
    current_target: str = ""


class NavigationStateService:
    def __init__(self):
        self.state = NavigationState()

    def set_workspace(self, workspace: str):
        self.state.current_workspace = workspace

    def set_role(self, role: str):
        self.state.current_role = role

    def set_section(self, section_id: str):
        self.state.current_section_id = section_id

    def set_target(self, target: str):
        self.state.current_target = target
