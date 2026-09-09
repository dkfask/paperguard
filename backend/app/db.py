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
from app.models.organization import OrganizationUnit
from app.models.policy import CheckRun, PolicyAuditEvent, RequirementSet
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
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    teacher_id TEXT,
                    ai_config TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requirement_sets (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS organization_units (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS check_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_audit_events (
                    id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                UPDATE projects
                SET data = json_set(data, '$.revision', 0)
                WHERE json_extract(data, '$.revision') IS NULL;
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "must_change_password" not in columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
                )
            if "status" not in columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
                )
            if "teacher_id" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN teacher_id TEXT")
            if "ai_config" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN ai_config TEXT")
            if "organization_context" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN organization_context TEXT")

    # ------------------------------ users --------------------------------

    def save_user(self, user: dict) -> dict:
        """保存用户, 兼容旧数据库新增的账号与组织字段。"""
        sql = "INSERT OR REPLACE INTO users (id, username, password_hash, salt, role, display_name, token, must_change_password, status, teacher_id, ai_config, organization_context, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        params = (
            user["id"], user["username"], user["password_hash"], user["salt"],
            user["role"], user["display_name"], user.get("token"),
            int(user.get("must_change_password", 0)),
            user.get("status", "active"),
            user.get("teacher_id"),
            user.get("ai_config"),
            json.dumps(user.get("organization_context") or {}, ensure_ascii=False),
            user["created_at"],
        )
        with self._connect() as conn:
            conn.execute(sql, params)
        return user

    def get_user_by_username(self, username: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        return self._hydrate_user(dict(row)) if row else None

    def get_user_by_token(self, token: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE token = ?", (token,)
            ).fetchone()
        return self._hydrate_user(dict(row)) if row else None

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._hydrate_user(dict(row)) if row else None

    def list_users(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._hydrate_user(dict(r)) for r in rows]

    @staticmethod
    def _hydrate_user(user: dict) -> dict:
        raw = user.get("organization_context")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                user["organization_context"] = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                user["organization_context"] = {}
        elif not isinstance(raw, dict):
            user["organization_context"] = {}
        return user

    def create_session(self, session: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, revoked_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (
                    session["id"], session["user_id"], session["token_hash"],
                    session["created_at"], session["expires_at"], session["last_seen_at"],
                ),
            )

    def get_session_user(self, token_hash: str, now: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.*, s.id AS session_id FROM sessions s "
                "JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
                (token_hash, now),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                    (now, row["session_id"]),
                )
        return self._hydrate_user(dict(row)) if row else None

    def revoke_session(self, token_hash: str, revoked_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (revoked_at, token_hash),
            )

    def revoke_user_sessions(self, user_id: str, revoked_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (revoked_at, user_id),
            )

    def list_students_by_teacher(self, teacher_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE teacher_id = ? ORDER BY created_at DESC",
                (teacher_id,),
            ).fetchall()
        return [self._hydrate_user(dict(r)) for r in rows]

    def unbind_teacher_student(self, teacher_id: str, student_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE users SET teacher_id = NULL WHERE id = ? AND teacher_id = ?",
                (student_id, teacher_id),
            )
            return cur.rowcount > 0

    def update_user_status(self, user_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET status = ? WHERE id = ?",
                (status, user_id),
            )

    def update_user_role_and_status(self, user_id: str, role: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET role = ?, status = ? WHERE id = ?",
                (role, status, user_id),
            )

    def set_must_change_password(self, user_id: str, value: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET must_change_password = ? WHERE id = ?",
                (int(value), user_id),
            )

    def get_user_ai_config(self, user_id: str) -> dict | None:
        user = self.get_user(user_id)
        if not user or not user.get("ai_config"):
            return None
        try:
            return json.loads(user["ai_config"])
        except (TypeError, ValueError):
            return None

    def set_user_ai_config(self, user_id: str, cfg: dict | None) -> None:
        payload = json.dumps(cfg, ensure_ascii=False) if cfg else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET ai_config = ? WHERE id = ?",
                (payload, user_id),
            )

    # ----------------------- system settings -----------------------

    def get_system_setting(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM system_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_system_setting(self, key: str, value: str | None) -> None:
        with self._connect() as conn:
            if value is None:
                conn.execute("DELETE FROM system_settings WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, _now()),
                )

    def get_system_ai_config(self) -> dict | None:
        raw = self.get_system_setting("ai_default_config")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (TypeError, ValueError):
            pass
        return None

    def set_system_ai_config(self, cfg: dict | None) -> None:
        payload = json.dumps(cfg, ensure_ascii=False) if cfg else None
        self.set_system_setting("ai_default_config", payload)

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

    # ------------------------- requirement sets ------------------------

    def save_requirement_set(self, requirement_set: RequirementSet) -> RequirementSet:
        requirement_set.updated_at = _now()
        if not requirement_set.created_at:
            requirement_set.created_at = requirement_set.updated_at
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO requirement_sets (id, data, updated_at) VALUES (?, ?, ?)",
                (requirement_set.id, requirement_set.model_dump_json(), requirement_set.updated_at),
            )
        return requirement_set

    def get_requirement_set(self, set_id: str) -> RequirementSet | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM requirement_sets WHERE id = ?", (set_id,)
            ).fetchone()
        return RequirementSet.model_validate_json(row["data"]) if row else None

    def list_requirement_sets(self) -> list[RequirementSet]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM requirement_sets ORDER BY updated_at DESC"
            ).fetchall()
        return [RequirementSet.model_validate_json(row["data"]) for row in rows]

    def delete_requirement_set(self, set_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM requirement_sets WHERE id = ?", (set_id,))
            return cur.rowcount > 0

    # ------------------------- organization units ------------------------

    def save_organization_unit(self, unit: OrganizationUnit) -> OrganizationUnit:
        unit.updated_at = _now()
        if not unit.created_at:
            unit.created_at = unit.updated_at
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organization_units (id, data, updated_at) VALUES (?, ?, ?)",
                (unit.id, unit.model_dump_json(), unit.updated_at),
            )
        return unit

    def get_organization_unit(self, unit_id: str) -> OrganizationUnit | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM organization_units WHERE id = ?", (unit_id,)
            ).fetchone()
        return OrganizationUnit.model_validate_json(row["data"]) if row else None

    def list_organization_units(self) -> list[OrganizationUnit]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM organization_units ORDER BY updated_at DESC"
            ).fetchall()
        return [OrganizationUnit.model_validate_json(row["data"]) for row in rows]

    def delete_organization_unit(self, unit_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM organization_units WHERE id = ?", (unit_id,))
            return cur.rowcount > 0

    # ---------------------------- check runs -----------------------------

    def save_check_run(self, check_run: CheckRun) -> CheckRun:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO check_runs (id, project_id, data, created_at) VALUES (?, ?, ?, ?)",
                (check_run.id, check_run.project_id, check_run.model_dump_json(), check_run.created_at),
            )
        return check_run

    def list_check_runs(self, project_id: str, limit: int = 20) -> list[CheckRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM check_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, max(1, min(limit, 100))),
            ).fetchall()
        return [CheckRun.model_validate_json(row["data"]) for row in rows]

    def get_check_run(self, run_id: str) -> CheckRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM check_runs WHERE id = ?", (run_id,)).fetchone()
        return CheckRun.model_validate_json(row["data"]) if row else None

    # -------------------------- policy audit -----------------------------

    def save_policy_audit(self, event: PolicyAuditEvent) -> PolicyAuditEvent:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO policy_audit_events (id, policy_id, data, created_at) VALUES (?, ?, ?, ?)",
                (event.id, event.policy_id, event.model_dump_json(), event.created_at),
            )
        return event

    def list_policy_audits(self, policy_id: str, limit: int = 100) -> list[PolicyAuditEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM policy_audit_events WHERE policy_id = ? ORDER BY created_at DESC LIMIT ?",
                (policy_id, max(1, min(limit, 500))),
            ).fetchall()
        return [PolicyAuditEvent.model_validate_json(row["data"]) for row in rows]

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

    def update_project_if_revision(
        self,
        proj: WritingProject,
        expected_revision: int,
    ) -> WritingProject | None:
        """保存项目的同时检查 revision，避免并发请求静默覆盖。"""
        next_revision = expected_revision + 1
        proj.revision = next_revision
        proj.updated_at = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE projects SET template_id = ?, data = ?, updated_at = ? "
                "WHERE id = ? AND json_extract(data, '$.revision') = ?",
                (
                    proj.template_id,
                    proj.model_dump_json(),
                    proj.updated_at,
                    proj.id,
                    expected_revision,
                ),
            )
            if cur.rowcount != 1:
                return None
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
