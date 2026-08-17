"""PaperGuard 存储层 — SQLite + JSON 列。

- templates: 模板对象整体 JSON 存储
- projects:  写作项目整体 JSON 存储
结构简单, 单用户/小规模场景足够。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models.project import WritingProject
from app.models.template import PaperTemplate

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "paperguard.db"


def _default_db_path() -> Path:
    """数据库路径, 可用环境变量 PAPERGUARD_DB_PATH 覆盖(测试用)。"""
    return Path(os.environ.get("PAPERGUARD_DB_PATH", str(_DEFAULT_DB_PATH)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    """模板与项目的 SQLite 存储。"""

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS templates (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    template_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'student',
                    display_name TEXT NOT NULL DEFAULT '',
                    token TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    # ------------------------------ users --------------------------------

    def save_user(self, user: dict) -> dict:
        """user: {id, username, password_hash, salt, role, display_name, token, created_at}"""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users "
                "(id, username, password_hash, salt, role, display_name, token, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user["id"], user["username"], user["password_hash"], user["salt"],
                 user["role"], user["display_name"], user.get("token"), user["created_at"]),
            )
        return user

    def get_user_by_username(self, username: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_token(self, token: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE token = ?", (token,)
            ).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def set_user_token(self, user_id: str, token: str | None) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET token = ? WHERE id = ?", (token, user_id))

    # ---------------------------- templates ----------------------------

    def save_template(self, tpl: PaperTemplate) -> PaperTemplate:
        tpl.updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO templates (id, data, updated_at) VALUES (?, ?, ?)",
                (tpl.id, tpl.model_dump_json(), tpl.updated_at),
            )
        return tpl

    def get_template(self, tpl_id: str) -> PaperTemplate | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM templates WHERE id = ?", (tpl_id,)
            ).fetchone()
        return PaperTemplate.model_validate_json(row["data"]) if row else None

    def list_templates(self) -> list[PaperTemplate]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM templates ORDER BY updated_at DESC").fetchall()
        return [PaperTemplate.model_validate_json(r["data"]) for r in rows]

    def delete_template(self, tpl_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM templates WHERE id = ?", (tpl_id,))
            return cur.rowcount > 0

    # ---------------------------- projects -----------------------------

    def save_project(self, proj: WritingProject) -> WritingProject:
        proj.updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO projects (id, template_id, data, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (proj.id, proj.template_id, proj.model_dump_json(), proj.updated_at),
            )
        return proj

    def get_project(self, proj_id: str) -> WritingProject | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM projects WHERE id = ?", (proj_id,)
            ).fetchone()
        return WritingProject.model_validate_json(row["data"]) if row else None

    def list_projects(self) -> list[WritingProject]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM projects ORDER BY updated_at DESC").fetchall()
        return [WritingProject.model_validate_json(r["data"]) for r in rows]

    def delete_project(self, proj_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (proj_id,))
            return cur.rowcount > 0


def template_to_json(tpl: PaperTemplate) -> str:
    return json.dumps(tpl.model_dump(), ensure_ascii=False, indent=2)
