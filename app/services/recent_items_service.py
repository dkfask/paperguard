"""Persistence for recently used templates and projects."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


APP_DATA_DIR = Path.home() / ".thesis_template_tool"
RECENTS_PATH = APP_DATA_DIR / "recent_items.json"


@dataclass
class RecentItem:
    title: str
    path: str
    subtitle: str = ""
    last_opened_at: str = ""


class RecentItemsService:
    """Store and retrieve recent files."""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or RECENTS_PATH
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def list_templates(self, limit: int = 6) -> list[RecentItem]:
        return self._list_items("templates", limit)

    def list_projects(self, limit: int = 6) -> list[RecentItem]:
        return self._list_items("projects", limit)

    def add_template(self, path: str, title: str, subtitle: str = ""):
        self._add_item("templates", path, title, subtitle)

    def add_project(self, path: str, title: str, subtitle: str = ""):
        self._add_item("projects", path, title, subtitle)

    def has_history(self) -> bool:
        data = self._load()
        return bool(data["templates"] or data["projects"])

    def _list_items(self, key: str, limit: int) -> list[RecentItem]:
        data = self._load()
        items = [RecentItem(**item) for item in data[key]]
        return items[:limit]

    def _add_item(self, key: str, path: str, title: str, subtitle: str):
        path_value = str(path)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        data = self._load()
        items = [
            item
            for item in data[key]
            if item.get("path") != path_value
        ]
        items.insert(
            0,
            asdict(
                RecentItem(
                    title=title,
                    path=path_value,
                    subtitle=subtitle,
                    last_opened_at=now,
                )
            ),
        )
        data[key] = items[:12]
        self._save(data)

    def _load(self) -> dict[str, list[dict]]:
        if not self.storage_path.exists():
            return {"templates": [], "projects": []}
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except Exception:
            return {"templates": [], "projects": []}
        return {
            "templates": data.get("templates", []),
            "projects": data.get("projects", []),
        }

    def _save(self, data: dict[str, list[dict]]):
        self.storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
