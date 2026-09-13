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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.models.project import WritingProject
from app.models.organization import OrganizationUnit
from app.models.policy import CheckRun, PolicyAuditEvent, RequirementSet
from app.models.template import PaperTemplate

# Keep the default database beside the other persisted runtime assets.  The
# Docker Compose volume is mounted at /app/data, which corresponds to this
# repository-level backend/data directory in a source checkout.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "paperguard.db"
SCHEMA_MIGRATIONS = (
    "0001_runtime_schema",
    "0002_durable_jobs",
    "0003_audit_ledger",
    "0004_document_packages",
    "0005_document_projects",
    "0006_validation_runs",
    "0007_document_scope_indexes",
    "0008_miniprogram_auth",
)


def _default_db_path() -> Path:
    """数据库路径, 可用 PAPERGUARD_DB_PATH 覆盖(测试用/运维配置)。"""
    value = os.environ.get("PAPERGUARD_DB_PATH", "")
    if value:
        return Path(value)
    return Path(os.environ.get("PAPERGUARD_DATA_DIR", str(_DEFAULT_DB_PATH.parent))) / "paperguard.db"


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
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def list_schema_migrations(self) -> list[dict[str, str]]:
        """Return the applied migration ledger in deterministic order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT version, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        return [{"version": row["version"], "applied_at": row["applied_at"]} for row in rows]

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
                    source TEXT NOT NULL DEFAULT 'web',
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS mini_program_bindings (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE,
                    openid_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS mini_program_bind_tickets (
                    id TEXT PRIMARY KEY,
                    ticket_hash TEXT UNIQUE NOT NULL,
                    openid_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mp_bind_tickets_expires
                    ON mini_program_bind_tickets (expires_at, used_at);
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
                CREATE TABLE IF NOT EXISTS document_packages (
                    id TEXT PRIMARY KEY,
                    template_version_id TEXT NOT NULL,
                    spec_version_id TEXT NOT NULL,
                    renderer_version TEXT NOT NULL,
                    rule_engine_version TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (template_version_id, spec_version_id, renderer_version, rule_engine_version)
                );
                CREATE INDEX IF NOT EXISTS idx_document_packages_created
                    ON document_packages (created_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS document_projects (
                    id TEXT PRIMARY KEY,
                    package_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_document_projects_owner_updated
                    ON document_projects (owner_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS validation_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_runs_project_created
                    ON validation_runs (project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_document_projects_package_owner
                    ON document_projects (package_id, owner_id, updated_at DESC);
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
                CREATE TABLE IF NOT EXISTS workflow_records (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT 'success',
                    reason TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_records_kind_updated
                    ON workflow_records (kind, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workflow_records_document_project
                    ON workflow_records (kind, json_extract(data, '$.project_id'), updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_events_created
                    ON audit_events (created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_events_scope
                    ON audit_events (organization_id, resource_type, action, created_at DESC);
                CREATE TABLE IF NOT EXISTS workflow_idempotency (
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    idem_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, action, idem_key)
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    dedupe_key TEXT UNIQUE NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    read_at TEXT,
                    delivery_status TEXT NOT NULL DEFAULT 'pending',
                    delivered_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    lease_until TEXT,
                    lease_token TEXT,
                    leased_by TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    available_at TEXT NOT NULL,
                    lease_until TEXT,
                    lease_token TEXT,
                    leased_by TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_claim
                    ON jobs (job_type, state, available_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_resource
                    ON jobs (resource_type, resource_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_events_recipient
                    ON workflow_events (recipient_id, created_at DESC);
                UPDATE projects
                SET data = json_set(data, '$.revision', 0)
                WHERE json_extract(data, '$.revision') IS NULL;
                INSERT OR IGNORE INTO audit_events
                    (id, actor_id, action, resource_type, resource_id, organization_id, result, reason, request_id, created_at)
                SELECT
                    json_extract(data, '$.id'),
                    COALESCE(json_extract(data, '$.actor_id'), ''),
                    COALESCE(json_extract(data, '$.action'), ''),
                    COALESCE(json_extract(data, '$.resource_type'), ''),
                    COALESCE(json_extract(data, '$.resource_id'), ''),
                    COALESCE(json_extract(data, '$.organization_id'), ''),
                    COALESCE(json_extract(data, '$.result'), 'success'),
                    COALESCE(json_extract(data, '$.reason'), ''),
                    COALESCE(json_extract(data, '$.request_id'), ''),
                    COALESCE(json_extract(data, '$.created_at'), created_at)
                FROM workflow_records
                WHERE kind = 'audit' AND json_extract(data, '$.id') IS NOT NULL;
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
            session_columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "source" not in session_columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'web'")
            event_columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_events)")}
            if "delivered_at" not in event_columns:
                conn.execute("ALTER TABLE workflow_events ADD COLUMN delivered_at TEXT")
            if "attempt_count" not in event_columns:
                conn.execute("ALTER TABLE workflow_events ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
            if "lease_until" not in event_columns:
                conn.execute("ALTER TABLE workflow_events ADD COLUMN lease_until TEXT")
            if "lease_token" not in event_columns:
                conn.execute("ALTER TABLE workflow_events ADD COLUMN lease_token TEXT")
            if "leased_by" not in event_columns:
                conn.execute("ALTER TABLE workflow_events ADD COLUMN leased_by TEXT")
            if "last_error" not in event_columns:
                conn.execute("ALTER TABLE workflow_events ADD COLUMN last_error TEXT")
            for version in SCHEMA_MIGRATIONS:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )

    # ------------------------------ users --------------------------------

    def save_user(self, user: dict) -> dict:
        """保存用户, 兼容旧数据库新增的账号与组织字段。"""
        sql = """
            INSERT INTO users (id, username, password_hash, salt, role, display_name, token,
                               must_change_password, status, teacher_id, ai_config,
                               organization_context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username = excluded.username,
                password_hash = excluded.password_hash,
                salt = excluded.salt,
                role = excluded.role,
                display_name = excluded.display_name,
                token = excluded.token,
                must_change_password = excluded.must_change_password,
                status = excluded.status,
                teacher_id = excluded.teacher_id,
                ai_config = excluded.ai_config,
                organization_context = excluded.organization_context,
                created_at = excluded.created_at
        """
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
                "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, revoked_at, last_seen_at, source) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    session["id"], session["user_id"], session["token_hash"],
                    session["created_at"], session["expires_at"], session["last_seen_at"],
                    session.get("source", "web"),
                ),
            )

    def get_session_user(self, token_hash: str, now: str,
                         refresh_expires_at: str | None = None) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.*, s.id AS session_id, s.source AS session_source FROM sessions s "
                "JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
                (token_hash, now),
            ).fetchone()
            if row:
                if refresh_expires_at and row["session_source"] == "mp":
                    conn.execute(
                        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                        (now, refresh_expires_at, row["session_id"]),
                    )
                else:
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

    def get_mini_program_binding_by_openid(self, openid_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mini_program_bindings WHERE openid_hash = ?",
                (openid_hash,),
            ).fetchone()
        return dict(row) if row else None

    def get_mini_program_binding_by_user(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mini_program_bindings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_mini_program_bind_ticket(self, ticket: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mini_program_bind_tickets "
                "(id, ticket_hash, openid_hash, created_at, expires_at, used_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    ticket["id"], ticket["ticket_hash"], ticket["openid_hash"],
                    ticket["created_at"], ticket["expires_at"],
                ),
            )

    def get_mini_program_bind_ticket(self, ticket_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mini_program_bind_tickets WHERE ticket_hash = ?",
                (ticket_hash,),
            ).fetchone()
        return dict(row) if row else None

    def bind_mini_program(self, ticket_hash: str, user_id: str,
                          openid_hash: str, now: str) -> dict | None:
        """Atomically consume a valid ticket after conflict checks."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ticket = conn.execute(
                "SELECT * FROM mini_program_bind_tickets WHERE ticket_hash = ?",
                (ticket_hash,),
            ).fetchone()
            if not ticket or ticket["used_at"] or ticket["expires_at"] <= now:
                return None
            existing_openid = conn.execute(
                "SELECT user_id FROM mini_program_bindings WHERE openid_hash = ?",
                (openid_hash,),
            ).fetchone()
            if existing_openid:
                return {"conflict": "openid", "user_id": existing_openid["user_id"]}
            existing_user = conn.execute(
                "SELECT openid_hash FROM mini_program_bindings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing_user:
                return {"conflict": "user", "openid_hash": existing_user["openid_hash"]}
            binding_id = new_id("mp-binding")
            conn.execute(
                "INSERT INTO mini_program_bindings "
                "(id, user_id, openid_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (binding_id, user_id, openid_hash, now, now),
            )
            conn.execute(
                "UPDATE mini_program_bind_tickets SET used_at = ? WHERE id = ?",
                (now, ticket["id"]),
            )
            return {"id": binding_id, "user_id": user_id, "openid_hash": openid_hash}

    def delete_mini_program_binding(self, user_id: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM mini_program_bindings WHERE user_id = ?", (user_id,)
            )
        return result.rowcount > 0

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

    # ------------------------- document packages -----------------------

    @staticmethod
    def _document_package_payload(
        package: DocumentPackage,
        template: DocumentTemplate,
        template_version: TemplateVersion,
        spec_set: SpecSet,
        spec_version: SpecVersion,
    ) -> str:
        return json.dumps({
            "package": package.model_dump(mode="json"),
            "template": template.model_dump(mode="json"),
            "template_version": template_version.model_dump(mode="json"),
            "spec_set": spec_set.model_dump(mode="json"),
            "spec_version": spec_version.model_dump(mode="json"),
        }, ensure_ascii=False, separators=(",", ":"))

    def save_document_package(
        self,
        package,
        template,
        template_version,
        spec_set,
        spec_version,
    ):
        """Persist a published package and its immutable aggregate snapshot."""
        if package.template_version_id != template_version.id:
            raise ValueError("document package template version mismatch")
        if package.spec_version_id != spec_version.id:
            raise ValueError("document package spec version mismatch")
        if template.current_version_id != template_version.id:
            raise ValueError("template current version mismatch")
        if spec_set.current_version_id != spec_version.id:
            raise ValueError("spec current version mismatch")
        created_at = _now()
        payload = self._document_package_payload(
            package, template, template_version, spec_set, spec_version,
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO document_packages "
                    "(id, template_version_id, spec_version_id, renderer_version, rule_engine_version, data, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        package.id,
                        package.template_version_id,
                        package.spec_version_id,
                        package.renderer_version,
                        package.rule_engine_version,
                        payload,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("document package or version composition already exists") from exc
        return package

    def get_document_package_bundle(self, package_id: str) -> dict | None:
        from app.domain.documents.schemas import DocumentPackage, DocumentTemplate, TemplateVersion
        from app.domain.documents.specs import SpecSet, SpecVersion

        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM document_packages WHERE id = ?", (package_id,)
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["data"])
        return {
            "package": DocumentPackage.model_validate(payload["package"]),
            "template": DocumentTemplate.model_validate(payload["template"]),
            "template_version": TemplateVersion.model_validate(payload["template_version"]),
            "spec_set": SpecSet.model_validate(payload["spec_set"]),
            "spec_version": SpecVersion.model_validate(payload["spec_version"]),
        }

    def get_document_package(self, package_id: str):
        bundle = self.get_document_package_bundle(package_id)
        return bundle["package"] if bundle else None

    def list_document_package_bundles(self, *, status: str = "") -> list[dict]:
        from app.domain.documents.schemas import DocumentPackage, DocumentTemplate, TemplateVersion
        from app.domain.documents.specs import SpecSet, SpecVersion

        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT data FROM document_packages "
                    "WHERE json_extract(data, '$.package.status') = ? "
                    "ORDER BY created_at DESC, id DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM document_packages ORDER BY created_at DESC, id DESC"
                ).fetchall()
        return [
            {
                "package": DocumentPackage.model_validate((payload := json.loads(row["data"]))["package"]),
                "template": DocumentTemplate.model_validate(payload["template"]),
                "template_version": TemplateVersion.model_validate(payload["template_version"]),
                "spec_set": SpecSet.model_validate(payload["spec_set"]),
                "spec_version": SpecVersion.model_validate(payload["spec_version"]),
            }
            for row in rows
        ]

    # -------------------------- document projects -----------------------

    def save_document_project(self, project):
        """Insert a new generic project; updates must use the revision guard."""
        from app.domain.documents.schemas import DocumentProject

        if not isinstance(project, DocumentProject):
            project = DocumentProject.model_validate(project)
        now = _now()
        if not project.created_at:
            project.created_at = now
        project.updated_at = now
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO document_projects (id, package_id, owner_id, data, revision, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        project.id,
                        project.package_id,
                        project.owner_id,
                        project.model_dump_json(),
                        project.revision,
                        project.updated_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("document project already exists") from exc
        return project

    def update_document_project_if_revision(self, project, expected_revision: int):
        """Persist generic project content only when its revision is current."""
        from app.domain.documents.schemas import DocumentProject

        if not isinstance(project, DocumentProject):
            project = DocumentProject.model_validate(project)
        project.revision = expected_revision + 1
        project.updated_at = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE document_projects SET data = ?, revision = ?, updated_at = ? "
                "WHERE id = ? AND revision = ?",
                (
                    project.model_dump_json(),
                    project.revision,
                    project.updated_at,
                    project.id,
                    expected_revision,
                ),
            )
            if cur.rowcount != 1:
                return None
        return project

    def get_document_project(self, project_id: str):
        from app.domain.documents.schemas import DocumentProject

        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM document_projects WHERE id = ?", (project_id,)
            ).fetchone()
        return DocumentProject.model_validate_json(row["data"]) if row else None

    def list_document_projects(self, *, owner_id: str = "") -> list:
        from app.domain.documents.schemas import DocumentProject

        with self._connect() as conn:
            if owner_id:
                rows = conn.execute(
                    "SELECT data FROM document_projects WHERE owner_id = ? ORDER BY updated_at DESC",
                    (owner_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM document_projects ORDER BY updated_at DESC"
                ).fetchall()
        return [DocumentProject.model_validate_json(row["data"]) for row in rows]

    # -------------------------- validation runs -------------------------

    def save_validation_run(self, run):
        """Insert immutable generic validation evidence."""
        from app.domain.documents.schemas import ValidationRun

        if not isinstance(run, ValidationRun):
            run = ValidationRun.model_validate(run)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO validation_runs (id, project_id, data, created_at) VALUES (?, ?, ?, ?)",
                    (run.id, run.project_id, run.model_dump_json(), run.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("validation run already exists") from exc
        return run

    def list_validation_runs(self, project_id: str, limit: int = 20) -> list:
        from app.domain.documents.schemas import ValidationRun

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM validation_runs WHERE project_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (project_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [ValidationRun.model_validate_json(row["data"]) for row in rows]

    def get_validation_run(self, run_id: str):
        from app.domain.documents.schemas import ValidationRun

        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM validation_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return ValidationRun.model_validate_json(row["data"]) if row else None

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

    # ---------------------------- audit ledger ---------------------------

    @staticmethod
    def _audit_payload(event: dict) -> tuple:
        return (
            str(event.get("id") or new_id("audit")),
            str(event.get("actor_id") or ""),
            str(event.get("action") or ""),
            str(event.get("resource_type") or ""),
            str(event.get("resource_id") or ""),
            str(event.get("organization_id") or ""),
            str(event.get("result") or "success"),
            str(event.get("reason") or "")[:4000],
            str(event.get("request_id") or ""),
            str(event.get("created_at") or _now()),
        )

    def save_audit_event_conn(self, conn: sqlite3.Connection, event: dict) -> dict:
        """Write metadata-only audit evidence in its dedicated ledger."""
        payload = self._audit_payload(event)
        conn.execute(
            "INSERT OR REPLACE INTO audit_events "
            "(id, actor_id, action, resource_type, resource_id, organization_id, result, reason, request_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            payload,
        )
        return {
            "id": payload[0], "actor_id": payload[1], "action": payload[2],
            "resource_type": payload[3], "resource_id": payload[4],
            "organization_id": payload[5], "result": payload[6],
            "reason": payload[7], "request_id": payload[8], "created_at": payload[9],
        }

    def save_audit_event(self, event: dict) -> dict:
        with self._connect() as conn:
            return self.save_audit_event_conn(conn, event)

    def list_audit_events(self, limit: int = 5000) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, actor_id, action, resource_type, resource_id, organization_id, result, reason, request_id, created_at "
                "FROM audit_events ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), 10000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_policy_audits(self, policy_id: str, limit: int = 100) -> list[PolicyAuditEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM policy_audit_events WHERE policy_id = ? ORDER BY created_at DESC LIMIT ?",
                (policy_id, max(1, min(limit, 500))),
            ).fetchall()
        return [PolicyAuditEvent.model_validate_json(row["data"]) for row in rows]

    # --------------------------- workflow records -------------------------

    @contextmanager
    def workflow_transaction(self) -> Iterator[sqlite3.Connection]:
        """事务性地执行跨工作流对象的状态变更和事件写入。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _workflow_payload(record: dict) -> str:
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))

    def save_workflow_record_conn(
        self,
        conn: sqlite3.Connection,
        kind: str,
        record: dict,
        *,
        expected_revision: int | None = None,
    ) -> dict | None:
        """在调用方事务中保存工作流对象; expected_revision 用于乐观锁。"""
        now = _now()
        record = dict(record)
        record.setdefault("revision", 0)
        record.setdefault("created_at", now)
        record["updated_at"] = now
        if expected_revision is None:
            conn.execute(
                "INSERT OR REPLACE INTO workflow_records (kind, id, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (kind, record["id"], self._workflow_payload(record), record["created_at"], now),
            )
            return record

        next_revision = expected_revision + 1
        record["revision"] = next_revision
        cur = conn.execute(
            "UPDATE workflow_records SET data = ?, updated_at = ? WHERE kind = ? AND id = ? "
            "AND json_extract(data, '$.revision') = ?",
            (self._workflow_payload(record), now, kind, record["id"], expected_revision),
        )
        return record if cur.rowcount == 1 else None

    def save_workflow_record(
        self,
        kind: str,
        record: dict,
        *,
        expected_revision: int | None = None,
    ) -> dict | None:
        with self._connect() as conn:
            return self.save_workflow_record_conn(
                conn, kind, record, expected_revision=expected_revision
            )

    def get_workflow_record(self, kind: str, record_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM workflow_records WHERE kind = ? AND id = ?",
                (kind, record_id),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_workflow_records(self, kind: str, limit: int = 500) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM workflow_records WHERE kind = ? ORDER BY updated_at DESC LIMIT ?",
                (kind, max(1, min(limit, 5000))),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def delete_workflow_record(self, kind: str, record_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM workflow_records WHERE kind = ? AND id = ?",
                (kind, record_id),
            )
            return cur.rowcount == 1

    def get_workflow_idempotency(
        self, user_id: str, action: str, idem_key: str
    ) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_idempotency WHERE user_id = ? AND action = ? AND idem_key = ?",
                (user_id, action, idem_key),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["response"] = json.loads(result["response"])
        return result

    def save_workflow_idempotency_conn(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        action: str,
        idem_key: str,
        request_hash: str,
        response: dict,
    ) -> None:
        conn.execute(
            "INSERT INTO workflow_idempotency (user_id, action, idem_key, request_hash, response, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                action,
                idem_key,
                request_hash,
                json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )

    def save_workflow_event_conn(
        self,
        conn: sqlite3.Connection,
        *,
        event_type: str,
        recipient_id: str,
        resource_type: str,
        resource_id: str,
        dedupe_key: str,
        data: dict,
    ) -> bool:
        cur = conn.execute(
            "INSERT OR IGNORE INTO workflow_events "
            "(id, event_type, recipient_id, resource_type, resource_id, dedupe_key, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("event"), event_type, recipient_id, resource_type, resource_id,
                dedupe_key, json.dumps(data, ensure_ascii=False, separators=(",", ":")), _now(),
            ),
        )
        return cur.rowcount == 1

    # ------------------------------ durable jobs --------------------------

    def create_job_conn(
        self,
        conn: sqlite3.Connection,
        *,
        job_id: str,
        job_type: str,
        resource_type: str,
        resource_id: str,
        max_attempts: int = 5,
        available_at: str | None = None,
    ) -> dict:
        """Create a durable job in the caller's transaction."""
        now = _now()
        job = {
            "id": job_id,
            "job_type": job_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "state": "queued",
            "attempt_count": 0,
            "max_attempts": max(1, min(int(max_attempts), 20)),
            "available_at": available_at or now,
            "lease_until": None,
            "lease_token": None,
            "leased_by": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        conn.execute(
            "INSERT OR IGNORE INTO jobs "
            "(id, job_type, resource_type, resource_id, state, attempt_count, max_attempts, available_at, "
            "lease_until, lease_token, leased_by, last_error, created_at, updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(job[key] for key in (
                "id", "job_type", "resource_type", "resource_id", "state", "attempt_count",
                "max_attempts", "available_at", "lease_until", "lease_token", "leased_by",
                "last_error", "created_at", "updated_at", "completed_at",
            )),
        )
        return job

    @staticmethod
    def _job_row(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row else None

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = self.get_job_conn(conn, job_id)
        return self._job_row(row)

    @staticmethod
    def get_job_conn(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        states: tuple[str, ...] | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if job_type:
            clauses.append("job_type = ?"); params.append(job_type)
        if resource_type:
            clauses.append("resource_type = ?"); params.append(resource_type)
        if resource_id:
            clauses.append("resource_id = ?"); params.append(resource_id)
        if states:
            marks = ",".join("?" for _ in states)
            clauses.append(f"state IN ({marks})"); params.extend(states)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at ASC LIMIT ?",
                (*params, max(1, min(int(limit), 1000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_jobs(
        self,
        *,
        job_type: str,
        consumer_id: str,
        limit: int = 20,
        lease_seconds: int = 120,
    ) -> list[dict]:
        """Atomically claim queued jobs and recover expired leases."""
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds")
        lease_until = (now + timedelta(seconds=max(10, min(int(lease_seconds), 900)))).isoformat(timespec="seconds")
        claimed: list[dict] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM jobs WHERE job_type = ? AND "
                "((state = 'queued' AND available_at <= ?) OR "
                "(state = 'running' AND lease_until IS NOT NULL AND lease_until <= ?) OR "
                "(state = 'failed' AND attempt_count < max_attempts AND available_at <= ?)) "
                "ORDER BY created_at ASC LIMIT ?",
                (job_type, now_text, now_text, now_text, max(1, min(int(limit), 200))),
            ).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                attempt = int(row["attempt_count"] or 0) + 1
                cur = conn.execute(
                    "UPDATE jobs SET state = 'running', attempt_count = ?, lease_until = ?, lease_token = ?, "
                    "leased_by = ?, last_error = NULL, updated_at = ? WHERE id = ?",
                    (attempt, lease_until, token, consumer_id, now_text, row["id"]),
                )
                if cur.rowcount != 1:
                    continue
                updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
                item = dict(updated)
                claimed.append(item)
            conn.commit()
        return claimed

    def complete_job(self, job_id: str, *, lease_token: str = "") -> dict | None:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state = 'succeeded', lease_until = NULL, lease_token = NULL, leased_by = NULL, "
                "completed_at = ?, updated_at = ? WHERE id = ? AND state = 'running' "
                "AND (lease_token IS NULL OR lease_token = '' OR lease_token = ?)",
                (now, now, job_id, lease_token),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_row(row)

    def fail_job(
        self,
        job_id: str,
        *,
        error: str,
        lease_token: str = "",
        retry_delay_seconds: int = 0,
    ) -> dict | None:
        """Persist a failure and requeue until max_attempts, then park it."""
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["state"] != "running" or (row["lease_token"] and row["lease_token"] != lease_token):
                return None
            attempts = int(row["attempt_count"] or 0)
            next_state = "failed" if attempts >= int(row["max_attempts"] or 1) else "queued"
            available_at = (now + timedelta(seconds=max(0, int(retry_delay_seconds)))).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE jobs SET state = ?, available_at = ?, lease_until = NULL, lease_token = NULL, leased_by = NULL, "
                "last_error = ?, updated_at = ? WHERE id = ?",
                (next_state, available_at, error[:2000], now_text, job_id),
            )
            updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_row(updated)

    def cancel_job(self, job_id: str, reason: str = "") -> dict | None:
        with self._connect() as conn:
            return self.cancel_job_conn(conn, job_id, reason=reason)

    def cancel_job_conn(self, conn: sqlite3.Connection, job_id: str, reason: str = "") -> dict | None:
        """Cancel a queued or leased job without deleting its audit history."""
        now = _now()
        cur = conn.execute(
            "UPDATE jobs SET state = 'cancelled', lease_until = NULL, lease_token = NULL, "
            "leased_by = NULL, last_error = ?, updated_at = ? "
            "WHERE id = ? AND state IN ('queued', 'running', 'failed')",
            (reason[:2000] if reason else "cancelled", now, job_id),
        )
        if cur.rowcount != 1:
            return None
        return self._job_row(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def retry_job(self, job_id: str, reason: str = "") -> dict | None:
        with self._connect() as conn:
            return self.retry_job_conn(conn, job_id, reason)

    def retry_job_conn(self, conn: sqlite3.Connection, job_id: str, reason: str = "") -> dict | None:
        now = _now()
        cur = conn.execute(
            "UPDATE jobs SET state = 'queued', attempt_count = 0, available_at = ?, lease_until = NULL, "
            "lease_token = NULL, leased_by = NULL, last_error = ?, completed_at = NULL, updated_at = ? "
            "WHERE id = ? AND state IN ('failed', 'queued')",
            (now, reason[:2000] if reason else "管理员手动重试", now, job_id),
        )
        if cur.rowcount != 1:
            return None
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_row(row)

    def list_workflow_events(self, recipient_id: str, *, unread_only: bool = False) -> list[dict]:
        where = "AND read_at IS NULL" if unread_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM workflow_events WHERE recipient_id = ? {where} ORDER BY created_at DESC LIMIT 200",
                (recipient_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"])
            result.append(item)
        return result

    def list_pending_workflow_events(self, limit: int = 200) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_events WHERE delivery_status != 'delivered' ORDER BY created_at ASC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"])
            result.append(item)
        return result

    def mark_workflow_event_read(self, recipient_id: str, event_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE workflow_events SET read_at = COALESCE(read_at, ?) "
                "WHERE id = ? AND recipient_id = ?",
                (_now(), event_id, recipient_id),
            )
            return cur.rowcount == 1

    def mark_workflow_event_delivered(self, event_id: str) -> bool:
        """Acknowledge one durable outbox item after an external consumer accepts it."""
        return self.mark_workflow_event_delivered_with_lease(event_id)

    def mark_workflow_event_delivered_with_lease(self, event_id: str, lease_token: str = "") -> bool:
        """Acknowledge an event, requiring the active lease token when it is leased."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE workflow_events SET delivery_status = 'delivered', delivered_at = COALESCE(delivered_at, ?), "
                "lease_until = NULL, lease_token = NULL, leased_by = NULL "
                "WHERE id = ? AND delivery_status != 'delivered' "
                "AND (lease_token IS NULL OR lease_token = '' OR lease_token = ?)",
                (_now(), event_id, lease_token),
            )
            return cur.rowcount == 1

    def claim_workflow_events(
        self,
        event_ids: list[str],
        *,
        consumer_id: str,
        lease_seconds: int = 120,
        max_attempts: int = 5,
    ) -> list[dict]:
        """Atomically claim eligible events; expired leases can be recovered."""
        if not event_ids:
            return []
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds")
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        claimed: list[dict] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for event_id in dict.fromkeys(event_ids):
                row = conn.execute(
                    "SELECT * FROM workflow_events WHERE id = ?", (event_id,)
                ).fetchone()
                if row is None or row["delivery_status"] == "delivered":
                    continue
                active_lease = row["delivery_status"] == "processing" and row["lease_until"] and row["lease_until"] > now_text
                if active_lease or (
                    row["delivery_status"] == "failed" and int(row["attempt_count"] or 0) >= max_attempts
                ):
                    continue
                token = uuid.uuid4().hex
                cur = conn.execute(
                    "UPDATE workflow_events SET delivery_status = 'processing', attempt_count = ?, "
                    "lease_until = ?, lease_token = ?, leased_by = ?, last_error = NULL "
                    "WHERE id = ? AND delivery_status != 'delivered'",
                    (int(row["attempt_count"] or 0) + 1, lease_until, token, consumer_id, event_id),
                )
                if cur.rowcount != 1:
                    continue
                updated = conn.execute(
                    "SELECT * FROM workflow_events WHERE id = ?", (event_id,)
                ).fetchone()
                item = dict(updated)
                item["data"] = json.loads(item["data"])
                claimed.append(item)
            conn.commit()
        return claimed

    def fail_workflow_event(
        self,
        event_id: str,
        *,
        error: str,
        lease_token: str = "",
        max_attempts: int = 5,
    ) -> dict | None:
        """Release a lease and either return the event to pending or park it as failed."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workflow_events WHERE id = ?", (event_id,)).fetchone()
            if row is None or row["delivery_status"] == "delivered":
                return None
            if row["lease_token"] and row["lease_token"] != lease_token:
                return None
            next_status = "failed" if int(row["attempt_count"] or 0) >= max_attempts else "pending"
            conn.execute(
                "UPDATE workflow_events SET delivery_status = ?, lease_until = NULL, lease_token = NULL, "
                "leased_by = NULL, last_error = ? WHERE id = ? AND delivery_status != 'delivered'",
                (next_status, error, event_id),
            )
            updated = conn.execute("SELECT * FROM workflow_events WHERE id = ?", (event_id,)).fetchone()
        item = dict(updated)
        item["data"] = json.loads(item["data"])
        return item

    def retry_workflow_event(self, event_id: str, reason: str = "") -> dict | None:
        """Reset a parked event for an explicit operator retry."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE workflow_events SET delivery_status = 'pending', attempt_count = 0, "
                "lease_until = NULL, lease_token = NULL, leased_by = NULL, last_error = ? "
                "WHERE id = ? AND delivery_status = 'failed'",
                (reason or "管理员手动重试", event_id),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM workflow_events WHERE id = ?", (event_id,)).fetchone()
        item = dict(row)
        item["data"] = json.loads(item["data"])
        return item

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

    def update_project_check_runs(
        self,
        project_id: str,
        check_run_ids: list[str],
        expected_revision: int,
    ) -> WritingProject | None:
        """Attach check history without changing or replacing manuscript content."""
        project = self.get_project(project_id)
        if project is None or project.revision != expected_revision:
            return None
        project.check_run_ids = list(check_run_ids)
        project.updated_at = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE projects SET data = ?, updated_at = ? "
                "WHERE id = ? AND json_extract(data, '$.revision') = ?",
                (project.model_dump_json(), project.updated_at, project.id, expected_revision),
            )
            if cur.rowcount != 1:
                return None
        return project

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
