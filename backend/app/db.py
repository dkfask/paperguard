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
from app.services import secret_store

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
    "0009_ai_usage_quota",
    "0010_tenant_system_settings",
    "0011_runtime_tenancy",
    "0012_ai_request_ledger",
    "0013_saas_approval_pilot",
    "0014_ai_tenant_idempotency",
)


def data_dir() -> Path:
    """数据目录：与数据库同源，保证归档/附件等持久化产物与 DB 落在同一备份域。

    优先级与 `_default_db_path` 一致：PAPERGUARD_DB_PATH 的父目录 > PAPERGUARD_DATA_DIR > 仓库默认目录。
    历史实现里归档目录按 `__file__` 解析为 backend/data/archives，与实际配置的 DB 位置脱钩，
    导致发布切换代码目录时归档被带走、且不在按数据目录做的备份范围内。
    """
    value = os.environ.get("PAPERGUARD_DB_PATH", "").strip()
    if value:
        return Path(value).expanduser().resolve().parent
    return Path(os.environ.get("PAPERGUARD_DATA_DIR", str(_DEFAULT_DB_PATH.parent))).expanduser().resolve()


def _default_db_path() -> Path:
    """数据库路径, 可用 PAPERGUARD_DB_PATH 覆盖(测试用/运维配置)。"""
    value = os.environ.get("PAPERGUARD_DB_PATH", "")
    if value:
        return Path(value)
    return data_dir() / "paperguard.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class AiQuotaExceeded(RuntimeError):
    """Raised after an atomic AI request/reservation transaction rejects quota."""

    def __init__(self, code: str, message: str, *, request_id: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


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
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    auth_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS platform_role_bindings (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE,
                    role_code TEXT NOT NULL CHECK (role_code IN ('platform_admin')),
                    status TEXT NOT NULL CHECK (status IN ('active','revoked')),
                    auth_version INTEGER NOT NULL CHECK (auth_version > 0),
                    approval_ref TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saas_provisioning (
                    command_key TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL UNIQUE REFERENCES tenants(id),
                    actor_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    approval TEXT NOT NULL,
                    first_owner_id TEXT,
                    owner_request_version INTEGER,
                    owner_auth_version INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_memberships (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    auth_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (tenant_id, user_id),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memberships_user_status
                    ON tenant_memberships (user_id, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memberships_tenant_status
                    ON tenant_memberships (tenant_id, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    password_set INTEGER NOT NULL DEFAULT 1,
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
                CREATE TABLE IF NOT EXISTS email_verification_codes (
                    email TEXT PRIMARY KEY,
                    code_hash TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
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
                    openid_cipher TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS mini_program_bind_tickets (
                    id TEXT PRIMARY KEY,
                    ticket_hash TEXT UNIQUE NOT NULL,
                    openid_hash TEXT NOT NULL,
                    openid_cipher TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mp_bind_tickets_expires
                    ON mini_program_bind_tickets (expires_at, used_at);
                CREATE TABLE IF NOT EXISTS mini_program_push_quotas (
                    user_id TEXT NOT NULL,
                    template_id TEXT NOT NULL,
                    remaining INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, template_id)
                );
                CREATE TABLE IF NOT EXISTS mini_program_push_log (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    recipient_id TEXT NOT NULL,
                    template_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                -- AI 用量记账。LLM 是按量计费资源，没有这张表就无法回答
                -- 「谁花了多少」，也无法按租户分摊成本。
                -- organization_id 在**调用时**从用户的 organization_context
                -- 落库（而不是查询时再关联）：用户的学校归属之后可能变，
                -- 变了会让历史用量的归属跟着漂移，计费口径就不成立了。
                CREATE TABLE IF NOT EXISTS ai_usage (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    organization_id TEXT,
                    action TEXT NOT NULL,
                    source TEXT NOT NULL,
                    model TEXT NOT NULL,
                    base_url_host TEXT,
                    input_chars INTEGER NOT NULL DEFAULT 0,
                    output_chars INTEGER NOT NULL DEFAULT 0,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_usage_user_created
                    ON ai_usage (user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_usage_org_created
                    ON ai_usage (organization_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS ai_requests (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT,
                    request_hash TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    tenant_id TEXT,
                    membership_id TEXT,
                    action TEXT NOT NULL,
                    idempotency_domain TEXT NOT NULL DEFAULT 'historical_legacy',
                    idempotency_scope TEXT,
                    auth_version INTEGER NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT '',
                    base_url_host TEXT,
                    model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    reservation_status TEXT NOT NULL DEFAULT 'reserved',
                    response_text TEXT,
                    error_code TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_requests_idempotency_domain
                    ON ai_requests (idempotency_domain, idempotency_scope, action, idempotency_key)
                    WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
                CREATE INDEX IF NOT EXISTS idx_ai_requests_tenant_status
                    ON ai_requests (tenant_id, status, created_at DESC);
                -- 每用户每日配额计数。period 用 UTC 日期，避免跨时区当天额度错乱。
                CREATE TABLE IF NOT EXISTS ai_quota (
                    user_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, period)
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
            if "requested_role" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN requested_role TEXT")
            if "requested_organization_context" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN requested_organization_context TEXT")
            if "role_application_status" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN role_application_status TEXT NOT NULL DEFAULT 'unselected'")
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
            mp_binding_columns = {row[1] for row in conn.execute("PRAGMA table_info(mini_program_bindings)")}
            if "openid_cipher" not in mp_binding_columns:
                conn.execute("ALTER TABLE mini_program_bindings ADD COLUMN openid_cipher TEXT NOT NULL DEFAULT ''")
            mp_ticket_columns = {row[1] for row in conn.execute("PRAGMA table_info(mini_program_bind_tickets)")}
            if "openid_cipher" not in mp_ticket_columns:
                conn.execute("ALTER TABLE mini_program_bind_tickets ADD COLUMN openid_cipher TEXT NOT NULL DEFAULT ''")
            user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "email" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
                user_columns.add("email")
            if "email_verified" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
            if "password_set" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN password_set INTEGER NOT NULL DEFAULT 1")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
                "ON users(email) WHERE email IS NOT NULL AND email != ''"
            )
            # 0010: system_settings 增加 organization_id 维度。
            # 修复前该表是全局键值表（key 为主键），意味着**一套部署服务多个
            # 学校**时，任一学校用的 AI 模型配置是同一个——所有租户的论文正文
            # 流向同一个外部处理者，而平台无从按租户区分。
            #
            # SQLite 无法直接给已有表改主键，只能重建。存量行一律归到
            # organization_id=''（平台全局默认），因此升级后行为不回退：
            # 原来生效的配置仍然生效，只是从此可以被单校覆盖。
            # 0011: materialize tenant and membership runtime storage.
            # Existing organization_context remains a legacy compatibility view;
            # no user supplied tenant id is trusted until a membership exists.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memberships_capability ON tenant_memberships(tenant_id, status)")

            # 0014: widen AI idempotency from the historical (user, key)
            # tuple to an explicit authorization domain. Existing rows are
            # preserved as historical facts and are never guessed into a
            # tenant/member scope.
            self._migrate_ai_request_idempotency(conn)

            settings_columns = {row[1] for row in conn.execute("PRAGMA table_info(system_settings)")}
            if "organization_id" not in settings_columns:
                conn.execute("ALTER TABLE system_settings RENAME TO system_settings_legacy")
                conn.execute(
                    "CREATE TABLE system_settings ("
                    "  key TEXT NOT NULL,"
                    "  organization_id TEXT NOT NULL DEFAULT '',"
                    "  value TEXT NOT NULL,"
                    "  updated_at TEXT NOT NULL,"
                    "  PRIMARY KEY (key, organization_id)"
                    ")"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO system_settings (key, organization_id, value, updated_at) "
                    "SELECT key, '', value, updated_at FROM system_settings_legacy"
                )
                conn.execute("DROP TABLE system_settings_legacy")
            for version in SCHEMA_MIGRATIONS:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )

    def _migrate_ai_request_idempotency(self, conn: sqlite3.Connection) -> None:
        # A prior interrupted/fixture rebuild can leave named indexes attached
        # to a renamed table. Remove only those stale names so the new table
        # receives the intended indexes below; data rows remain untouched.
        for index_name in (
            "uq_ai_requests_idempotency_domain",
            "idx_ai_requests_tenant_status",
            "idx_ai_requests_idempotency_lookup",
        ):
            index = conn.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
                (index_name,),
            ).fetchone()
            if index and index[0] != "ai_requests":
                conn.execute(f"DROP INDEX IF EXISTS {index_name}")

        columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_requests)")}
        table = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_requests'"
        ).fetchone()
        table_sql = str(table[0] or "") if table else ""
        needs_rebuild = (
            not {"idempotency_domain", "idempotency_scope", "auth_version"}.issubset(columns)
            or "UNIQUE (user_id, idempotency_key)" in table_sql
        )
        if needs_rebuild:
            conn.execute("ALTER TABLE ai_requests RENAME TO ai_requests_legacy_0014")
            conn.execute(
                """CREATE TABLE ai_requests (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT,
                    request_hash TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    tenant_id TEXT,
                    membership_id TEXT,
                    action TEXT NOT NULL,
                    idempotency_domain TEXT NOT NULL DEFAULT 'historical_legacy',
                    idempotency_scope TEXT,
                    auth_version INTEGER NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT '',
                    base_url_host TEXT,
                    model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    reservation_status TEXT NOT NULL DEFAULT 'reserved',
                    response_text TEXT,
                    error_code TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            old_columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_requests_legacy_0014)")}
            required = {
                "id", "idempotency_key", "request_hash", "user_id", "tenant_id",
                "membership_id", "action", "provider", "base_url_host", "model",
                "status", "reservation_status", "response_text", "error_code",
                "input_tokens", "output_tokens", "created_at", "updated_at",
            }
            if not required.issubset(old_columns):
                raise RuntimeError("ai_requests legacy schema is incomplete")
            conn.execute(
                """INSERT INTO ai_requests (
                    id, idempotency_key, request_hash, user_id, tenant_id,
                    membership_id, action, idempotency_domain, idempotency_scope,
                    auth_version, provider, base_url_host, model, status,
                    reservation_status, response_text, error_code, input_tokens,
                    output_tokens, created_at, updated_at
                )
                SELECT id, idempotency_key, request_hash, user_id, tenant_id,
                       membership_id, action, 'historical_legacy',
                       'historical:' || user_id, 0, provider, base_url_host, model,
                       status, reservation_status, response_text, error_code,
                       input_tokens, output_tokens, created_at, updated_at
                  FROM ai_requests_legacy_0014"""
            )
            conn.execute("DROP TABLE ai_requests_legacy_0014")
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_requests_idempotency_domain
               ON ai_requests (idempotency_domain, idempotency_scope, action, idempotency_key)
             WHERE idempotency_key IS NOT NULL AND idempotency_key != ''"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_requests_tenant_status "
            "ON ai_requests (tenant_id, status, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_requests_idempotency_lookup "
            "ON ai_requests (idempotency_domain, idempotency_scope, action, idempotency_key)"
        )

    # ------------------------------ users --------------------------------

    def save_user(self, user: dict) -> dict:
        """保存用户, 兼容旧数据库新增的账号与组织字段。"""
        sql = """
            INSERT INTO users (id, username, email, email_verified, password_set,
                               password_hash, salt, role, display_name, token,
                               must_change_password, status, teacher_id, ai_config,
                               organization_context, requested_role, requested_organization_context,
                               role_application_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username = excluded.username,
                email = excluded.email,
                email_verified = excluded.email_verified,
                password_set = excluded.password_set,
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
                requested_role = excluded.requested_role,
                requested_organization_context = excluded.requested_organization_context,
                role_application_status = excluded.role_application_status,
                created_at = excluded.created_at
        """
        params = (
            user["id"], user["username"], user.get("email") or "", int(user.get("email_verified", 0)),
            int(user.get("password_set", 1)),
            user["password_hash"], user["salt"],
            user["role"], user["display_name"], user.get("token"),
            int(user.get("must_change_password", 0)),
            user.get("status", "active"),
            user.get("teacher_id"),
            user.get("ai_config"),
            json.dumps(user.get("organization_context") or {}, ensure_ascii=False),
            user.get("requested_role"),
            json.dumps(user.get("requested_organization_context") or {}, ensure_ascii=False),
            user.get("role_application_status", "unselected"),
            user["created_at"],
        )
        with self._connect() as conn:
            conn.execute(sql, params)
        return user

    def get_user_by_email(self, email: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return self._hydrate_user(dict(row)) if row else None

    def get_user_by_identifier(self, identifier: str) -> dict | None:
        """用户名或邮箱任一命中即视为同一账号(邮箱注册场景)。"""
        user = self.get_user_by_username(identifier)
        if user is None and "@" in identifier:
            user = self.get_user_by_email(identifier)
        return user

    # ------------------------- tenancy runtime -------------------------

    @staticmethod
    def _membership_row(row: sqlite3.Row | dict | None) -> dict | None:
        if row is None:
            return None
        data = dict(row)
        try:
            caps = json.loads(data.get("capabilities") or "[]")
        except (TypeError, ValueError):
            caps = []
        data["capabilities"] = [str(item) for item in caps] if isinstance(caps, list) else []
        return data

    def create_tenant(self, tenant: dict) -> dict:
        now = tenant.get("updated_at") or tenant.get("created_at") or _now()
        payload = {
            "id": tenant["id"], "slug": tenant["slug"], "name": tenant["name"],
            "status": tenant.get("status", "active"),
            "auth_version": int(tenant.get("auth_version", 1)),
            "created_at": tenant.get("created_at", now), "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tenants (id, slug, name, status, auth_version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "slug=excluded.slug, name=excluded.name, status=excluded.status, "
                "auth_version=excluded.auth_version, updated_at=excluded.updated_at",
                tuple(payload.values()),
            )
        return payload

    def get_tenant(self, tenant_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        return dict(row) if row else None

    def list_tenants(self, *, status: str | None = "active") -> list[dict]:
        with self._connect() as conn:
            if status is None:
                rows = conn.execute("SELECT * FROM tenants ORDER BY created_at, id").fetchall()
            else:
                rows = conn.execute("SELECT * FROM tenants WHERE status = ? ORDER BY created_at, id", (status,)).fetchall()
        return [dict(row) for row in rows]

    def save_membership(self, membership: dict) -> dict:
        now = membership.get("updated_at") or membership.get("created_at") or _now()
        payload = {
            "id": membership["id"], "tenant_id": membership["tenant_id"],
            "user_id": membership["user_id"], "role": membership["role"],
            "capabilities": json.dumps(sorted({str(x) for x in (membership.get("capabilities") or [])}), ensure_ascii=False),
            "status": membership.get("status", "active"),
            "auth_version": int(membership.get("auth_version", 1)),
            "created_at": membership.get("created_at", now), "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tenant_memberships (id, tenant_id, user_id, role, capabilities, status, auth_version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(tenant_id, user_id) DO UPDATE SET "
                "role=excluded.role, capabilities=excluded.capabilities, status=excluded.status, "
                "auth_version=tenant_memberships.auth_version + CASE WHEN "
                "tenant_memberships.role != excluded.role OR "
                "tenant_memberships.capabilities != excluded.capabilities OR "
                "tenant_memberships.status != excluded.status THEN 1 ELSE 0 END, "
                "updated_at=excluded.updated_at",
                tuple(payload.values()),
            )
            row = conn.execute(
                "SELECT * FROM tenant_memberships WHERE tenant_id = ? AND user_id = ?",
                (payload["tenant_id"], payload["user_id"]),
            ).fetchone()
        return self._membership_row(row)

    def get_membership(self, membership_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tenant_memberships WHERE id = ?", (membership_id,)).fetchone()
        return self._membership_row(row)

    def get_membership_for_user(self, user_id: str, tenant_id: str | None = None) -> dict | None:
        with self._connect() as conn:
            if tenant_id:
                row = conn.execute(
                    "SELECT * FROM tenant_memberships WHERE user_id = ? AND tenant_id = ? AND status = 'active'",
                    (user_id, tenant_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM tenant_memberships WHERE user_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                    (user_id,),
                ).fetchone()
        return self._membership_row(row)

    def list_memberships_for_user(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenant_memberships WHERE user_id = ? ORDER BY updated_at DESC, id",
                (user_id,),
            ).fetchall()
        return [self._membership_row(row) for row in rows]

    def list_memberships_for_tenant(self, tenant_id: str, *, active_only: bool = True) -> list[dict]:
        with self._connect() as conn:
            query = "SELECT * FROM tenant_memberships WHERE tenant_id = ?"
            params: list[object] = [tenant_id]
            if active_only:
                query += " AND status = 'active'"
            query += " ORDER BY created_at, id"
            rows = conn.execute(query, params).fetchall()
        return [self._membership_row(row) for row in rows]

    def ensure_legacy_tenant_membership(self, user: dict) -> dict | None:
        """Bridge legacy school context without trusting client input as auth."""
        context = user.get("organization_context") or {}
        school_id = str(context.get("school_id") or "").strip()
        if not school_id:
            return None
        # The compatibility helper may inspect an existing row only. A school
        # hint/global role is not an approved mapping or authority to create it.
        return next((item for item in self.list_memberships_for_user(user["id"])
                     if item["tenant_id"] == f"tenant-legacy-{school_id}"), None)

    # -------------------------- 邮箱验证码 --------------------------

    def save_email_code(self, email: str, code_hash: str, purpose: str, expires_at: str, now: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO email_verification_codes (email, code_hash, purpose, expires_at, attempts, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?) "
                "ON CONFLICT(email) DO UPDATE SET code_hash = excluded.code_hash, purpose = excluded.purpose, "
                "expires_at = excluded.expires_at, attempts = 0, created_at = excluded.created_at",
                (email.strip().lower(), code_hash, purpose, expires_at, now),
            )

    def get_email_code(self, email: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_verification_codes WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return dict(row) if row else None

    def delete_email_code(self, email: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email.strip().lower(),))

    def bump_email_code_attempts(self, email: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE email = ?",
                (email.strip().lower(),),
            )

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
        requested = user.get("requested_organization_context")
        if isinstance(requested, str):
            try:
                parsed = json.loads(requested)
                user["requested_organization_context"] = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                user["requested_organization_context"] = {}
        elif not isinstance(requested, dict):
            user["requested_organization_context"] = {}
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
                "(id, ticket_hash, openid_hash, openid_cipher, created_at, expires_at, used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    ticket["id"], ticket["ticket_hash"], ticket["openid_hash"],
                    ticket.get("openid_cipher", ""), ticket["created_at"], ticket["expires_at"],
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
                          openid_hash: str, openid_cipher: str, now: str) -> dict | None:
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
                "(id, user_id, openid_hash, openid_cipher, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (binding_id, user_id, openid_hash, openid_cipher, now, now),
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

    # ----------------------- 小程序订阅消息配额/推送日志 -----------------------

    def grant_push_quota(self, user_id: str, template_id: str, amount: int, now: str) -> int:
        """用户在小程序内接受订阅后 +1(一次性订阅, 每次授权对应一次推送机会)。"""
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO mini_program_push_quotas (user_id, template_id, remaining, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, template_id) DO UPDATE SET "
                "remaining = MIN(remaining + ?, 5), updated_at = ?",
                (user_id, template_id, amount, now, amount, now),
            )
            after = conn.execute(
                "SELECT remaining FROM mini_program_push_quotas WHERE user_id = ? AND template_id = ?",
                (user_id, template_id),
            ).fetchone()
        return int(after["remaining"]) if after else 0

    def reset_push_quota(self, user_id: str, template_id: str, now: str) -> None:
        """微信返回未订阅(43101)时说明账本与实际授权不一致, 清零纠偏。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mini_program_push_quotas (user_id, template_id, remaining, updated_at) "
                "VALUES (?, ?, 0, ?) "
                "ON CONFLICT(user_id, template_id) DO UPDATE SET remaining = 0, updated_at = ?",
                (user_id, template_id, now, now),
            )

    def consume_push_quota(self, user_id: str, template_id: str, now: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE mini_program_push_quotas SET remaining = remaining - 1, updated_at = ? "
                "WHERE user_id = ? AND template_id = ? AND remaining > 0",
                (now, user_id, template_id),
            )
        return cur.rowcount == 1

    def record_mini_program_push(self, entry: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mini_program_push_log "
                "(id, event_id, recipient_id, template_id, status, error_code, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["id"], entry["event_id"], entry["recipient_id"], entry["template_id"],
                    entry["status"], entry.get("error_code"), entry["created_at"],
                ),
            )

    def has_mini_program_push(self, event_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM mini_program_push_log WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row is not None

    def list_delivered_push_events(self, since: str, limit: int = 100) -> list[dict]:
        """取最近已投递、可尝试微信订阅推送的事件(调用方按模板映射与日志去重过滤)。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_events WHERE delivery_status = 'delivered' "
                "AND COALESCE(delivered_at, created_at) >= ? ORDER BY created_at DESC LIMIT ?",
                (since, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def _member_ai_config_id_conn(
        self, conn: sqlite3.Connection, user_id: str, tenant_id: str,
        membership_id: str | None,
    ) -> str:
        row = conn.execute(
            "SELECT m.id, m.capabilities FROM tenant_memberships m "
            "JOIN tenants t ON t.id=m.tenant_id JOIN users u ON u.id=m.user_id "
            "WHERE m.user_id=? AND m.tenant_id=? AND m.status='active' "
            "AND t.status='active' AND u.status='active' AND u.must_change_password=0",
            (user_id, tenant_id),
        ).fetchone()
        if (not row or (membership_id and row['id'] != membership_id)
                or 'tenant:read' not in json.loads(row['capabilities'] or '[]')):
            raise PermissionError("AI member configuration scope denied")
        # JSON tuple is unambiguous even for IDs containing punctuation.
        return json.dumps([tenant_id, row['id'], user_id], separators=(',', ':'))

    def get_user_ai_config(self, user_id: str, *, tenant_id: str | None = None,
                           membership_id: str | None = None) -> dict | None:
        if tenant_id:
            with self.workflow_transaction() as conn:
                scope_id = self._member_ai_config_id_conn(conn, user_id, tenant_id, membership_id)
                row = conn.execute(
                    "SELECT data FROM workflow_records WHERE kind='ai_member_config' AND id=?",
                    (scope_id,),
                ).fetchone()
                # Native members must never inherit the global user's BYOK.
                return secret_store.reveal_config(json.loads(row['data'])['config']) if row else None
        user = self.get_user(user_id)
        if not user or not user.get("ai_config"):
            return None
        try:
            data = json.loads(user["ai_config"])
        except (TypeError, ValueError):
            return None
        # Plaintext/invalid historical credentials fail closed; no implicit migration.
        return secret_store.reveal_config(data) if isinstance(data, dict) else None

    def set_user_ai_config(self, user_id: str, cfg: dict | None, *,
                           tenant_id: str | None = None, membership_id: str | None = None) -> None:
        if tenant_id:
            with self.workflow_transaction() as conn:
                scope_id = self._member_ai_config_id_conn(conn, user_id, tenant_id, membership_id)
                if cfg is None:
                    conn.execute("DELETE FROM workflow_records WHERE kind='ai_member_config' AND id=?", (scope_id,))
                else:
                    now = _now()
                    record = {'tenant_id': tenant_id, 'user_id': user_id,
                              'config': secret_store.protect_config(cfg)}
                    conn.execute(
                        "INSERT INTO workflow_records(kind,id,data,created_at,updated_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
                        ('ai_member_config', scope_id, self._workflow_payload(record), now, now),
                    )
                self.save_audit_event_conn(conn, {
                    'id': new_id('audit'), 'actor_id': user_id, 'organization_id': tenant_id,
                    'action': 'ai.config.saved' if cfg else 'ai.config.cleared',
                    'resource_type': 'ai_config:member', 'resource_id': scope_id,
                })
            return
        payload = json.dumps(secret_store.protect_config(cfg), ensure_ascii=False) if cfg else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET ai_config = ? WHERE id = ?",
                (payload, user_id),
            )

    # ----------------------- AI 用量与配额 -----------------------

    def record_ai_usage(self, entry: dict) -> None:
        """记录一次 AI 调用。失败也记——失败同样消耗上游额度与成本。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ai_usage (id, user_id, organization_id, action, source, model,"
                " base_url_host, input_chars, output_chars, result, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["id"],
                    entry["user_id"],
                    entry.get("organization_id"),
                    entry["action"],
                    entry["source"],
                    entry["model"],
                    entry.get("base_url_host"),
                    int(entry.get("input_chars") or 0),
                    int(entry.get("output_chars") or 0),
                    entry["result"],
                    entry["created_at"],
                ),
            )

    def get_ai_quota_used(self, user_id: str, period: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT used FROM ai_quota WHERE user_id = ? AND period = ?",
                (user_id, period),
            ).fetchone()
        return int(row["used"]) if row else 0

    def consume_ai_quota(self, user_id: str, period: str, now: str, limit: int) -> bool:
        """原子地占用一次配额；超限返回 False 且不占用。

        必须用一条 INSERT ... ON CONFLICT 带条件更新完成，不能「先查后写」——
        那是经典的并发竞态：两个请求同时读到 used=limit-1，双双通过检查。
    """
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO ai_quota (user_id, period, used, updated_at) VALUES (?, ?, 1, ?)"
                " ON CONFLICT(user_id, period) DO UPDATE SET"
                "   used = used + 1, updated_at = excluded.updated_at"
                " WHERE used < ?",
                (user_id, period, now, limit),
            )
            return cur.rowcount > 0

    @staticmethod
    def _consume_ai_quota_conn(
        conn: sqlite3.Connection, quota_scope: str, period: str, now: str, limit: int
    ) -> bool:
        cur = conn.execute(
            "INSERT INTO ai_quota (user_id, period, used, updated_at) VALUES (?, ?, 1, ?)"
            " ON CONFLICT(user_id, period) DO UPDATE SET"
            "   used = used + 1, updated_at = excluded.updated_at"
            " WHERE used < ?",
            (quota_scope, period, now, limit),
        )
        return cur.rowcount > 0

    @staticmethod
    def _release_ai_quota_conn(
        conn: sqlite3.Connection, quota_scope: str, period: str, now: str
    ) -> None:
        conn.execute(
            "UPDATE ai_quota SET used = MAX(0, used - 1), updated_at = ?"
            " WHERE user_id = ? AND period = ?",
            (now, quota_scope, period),
        )

    def release_ai_quota(self, user_id: str, period: str, now: str) -> None:
        """回退一次占用。请求在上游报错**之前**就被拒绝时不该占额度。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE ai_quota SET used = MAX(0, used - 1), updated_at = ?"
                " WHERE user_id = ? AND period = ?",
                (now, user_id, period),
            )

    def ai_usage_summary(self, *, since: str | None = None, limit: int = 100) -> list[dict]:
        """用量汇总（按用户 + 学校），供管理端查看与成本分摊。"""
        with self._connect() as conn:
            if since:
                rows = conn.execute(
                    "SELECT user_id, organization_id, COUNT(*) AS calls,"
                    " SUM(input_chars) AS input_chars, SUM(output_chars) AS output_chars"
                    " FROM ai_usage WHERE created_at >= ?"
                    " GROUP BY user_id, organization_id ORDER BY calls DESC LIMIT ?",
                    (since, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT user_id, organization_id, COUNT(*) AS calls,"
                    " SUM(input_chars) AS input_chars, SUM(output_chars) AS output_chars"
                    " FROM ai_usage"
                    " GROUP BY user_id, organization_id ORDER BY calls DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    # ----------------------- AI request ledger -----------------------

    def get_ai_request_by_idempotency(
        self, user_id: str, idempotency_key: str, *,
        idempotency_domain: str | None = None, idempotency_scope: str | None = None,
        action: str | None = None, include_historical: bool = True,
    ) -> dict | None:
        """Read one request without treating legacy rows as tenant evidence.

        The two-argument form is retained for old operational callers/tests, but
        new dispatch code must provide the explicit domain/scope/action tuple.
        """
        if not idempotency_key:
            return None
        with self._connect() as conn:
            if idempotency_domain is not None and idempotency_scope is not None and action is not None:
                row = conn.execute(
                    "SELECT * FROM ai_requests WHERE idempotency_domain=? AND idempotency_scope=? "
                    "AND action=? AND idempotency_key=?",
                    (idempotency_domain, idempotency_scope, action, idempotency_key),
                ).fetchone()
            else:
                sql = "SELECT * FROM ai_requests WHERE user_id=? AND idempotency_key=?"
                params: list[object] = [user_id, idempotency_key]
                if not include_historical:
                    sql += " AND idempotency_domain != 'historical_legacy'"
                sql += " ORDER BY created_at DESC, id DESC LIMIT 1"
                row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def register_ai_request(self, entry: dict) -> tuple[dict, bool]:
        """Atomically find-or-register one dispatch request.

        BEGIN IMMEDIATE plus the scoped partial unique index means concurrent
        callers cannot both become dispatchers. Historical rows are deliberately
        outside the new domain and do not block a tenant request.
        """
        now = entry.get("created_at") or _now()
        payload = {
            "id": entry["id"], "idempotency_key": entry.get("idempotency_key") or None,
            "request_hash": entry["request_hash"], "user_id": entry["user_id"],
            "tenant_id": entry.get("tenant_id"), "membership_id": entry.get("membership_id"),
            "action": entry["action"],
            "idempotency_domain": entry.get("idempotency_domain", "legacy"),
            "idempotency_scope": entry.get("idempotency_scope") or entry["user_id"],
            "auth_version": int(entry.get("auth_version") or 0),
            "provider": entry.get("provider", ""), "base_url_host": entry.get("base_url_host"),
            "model": entry.get("model", ""), "status": entry.get("status", "reserved"),
            "reservation_status": entry.get("reservation_status", "reserved"),
            "response_text": entry.get("response_text"), "error_code": entry.get("error_code"),
            "input_tokens": int(entry.get("input_tokens") or 0),
            "output_tokens": int(entry.get("output_tokens") or 0),
            "created_at": now, "updated_at": entry.get("updated_at") or now,
        }
        with self.workflow_transaction() as conn:
            key = payload["idempotency_key"]
            if key:
                existing = conn.execute(
                    "SELECT * FROM ai_requests WHERE idempotency_domain=? AND idempotency_scope=? "
                    "AND action=? AND idempotency_key=?",
                    (payload["idempotency_domain"], payload["idempotency_scope"], payload["action"], key),
                ).fetchone()
                if existing:
                    return dict(existing), False
            conn.execute(
                "INSERT INTO ai_requests (id, idempotency_key, request_hash, user_id, tenant_id, membership_id, action, "
                "idempotency_domain, idempotency_scope, auth_version, provider, base_url_host, model, status, "
                "reservation_status, response_text, error_code, input_tokens, output_tokens, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(payload.values()),
            )
            return payload, True

    def register_ai_request_with_reservation(
        self,
        entry: dict,
        *,
        quota_scope: str,
        day_period: str,
        minute_period: str,
        daily_limit: int,
        burst_limit: int,
        quota_source: str,
        quota_config_keys: list[str] | tuple[str, ...],
    ) -> tuple[dict, bool]:
        """Register one request and reserve both quota windows atomically.

        The request row, both quota counters, reservation sidecar and audit event
        share one ``BEGIN IMMEDIATE`` transaction. A quota rejection is retained
        as a durable pre-dispatch fact, but no quota counter or reservation is
        left behind. The sidecar deliberately reuses ``workflow_records`` so this
        SQLite/local slice does not introduce a financial-ledger table.
        """
        if any(type(value) is not int or value < 1 for value in (daily_limit, burst_limit)):
            raise ValueError("quota limits must be positive integers")
        expected_scope = f"tenant:{entry['idempotency_scope']}" if entry.get('idempotency_domain') == 'tenant' else entry['user_id']
        if quota_scope != expected_scope or day_period == minute_period:
            raise ValueError("quota snapshot scope/windows do not match request")
        now = entry.get("created_at") or _now()
        payload = {
            "id": entry["id"], "idempotency_key": entry.get("idempotency_key") or None,
            "request_hash": entry["request_hash"], "user_id": entry["user_id"],
            "tenant_id": entry.get("tenant_id"), "membership_id": entry.get("membership_id"),
            "action": entry["action"],
            "idempotency_domain": entry.get("idempotency_domain", "legacy"),
            "idempotency_scope": entry.get("idempotency_scope") or entry["user_id"],
            "auth_version": int(entry.get("auth_version") or 0),
            "provider": entry.get("provider", ""), "base_url_host": entry.get("base_url_host"),
            "model": entry.get("model", ""), "status": "reserved",
            "reservation_status": "reserved", "response_text": None,
            "error_code": None, "input_tokens": int(entry.get("input_tokens") or 0),
            "output_tokens": int(entry.get("output_tokens") or 0),
            "created_at": now, "updated_at": entry.get("updated_at") or now,
        }
        quota_error: tuple[str, str] | None = None
        with self.workflow_transaction() as conn:
            key = payload["idempotency_key"]
            if key:
                existing = conn.execute(
                    "SELECT * FROM ai_requests WHERE idempotency_domain=? AND idempotency_scope=? "
                    "AND action=? AND idempotency_key=?",
                    (payload["idempotency_domain"], payload["idempotency_scope"], payload["action"], key),
                ).fetchone()
                if existing:
                    return dict(existing), False
            self._validate_ai_request_authorization_conn(conn, payload)
            conn.execute(
                "INSERT INTO ai_requests (id, idempotency_key, request_hash, user_id, tenant_id, membership_id, action, "
                "idempotency_domain, idempotency_scope, auth_version, provider, base_url_host, model, status, "
                "reservation_status, response_text, error_code, input_tokens, output_tokens, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(payload.values()),
            )
            stamp = now
            if not self._consume_ai_quota_conn(conn, quota_scope, minute_period, stamp, burst_limit):
                quota_error = (
                    "AI_RATE_LIMITED",
                    f"操作过于频繁，请稍后再试（每分钟最多 {burst_limit} 次）",
                )
            elif not self._consume_ai_quota_conn(conn, quota_scope, day_period, stamp, daily_limit):
                self._release_ai_quota_conn(conn, quota_scope, minute_period, stamp)
                quota_error = (
                    "AI_DAILY_QUOTA_EXCEEDED",
                    f"今日 AI 调用次数已达上限（{daily_limit} 次），明日 0 时（UTC）重置",
                )
            if quota_error:
                code, _message = quota_error
                conn.execute(
                    "UPDATE ai_requests SET status='failed_final', reservation_status='released', "
                    "error_code=?, updated_at=? WHERE id=?",
                    (code, stamp, payload["id"]),
                )
                self.save_audit_event_conn(conn, {
                    "id": new_id("audit"), "actor_id": payload["user_id"],
                    "action": "ai.quota.rejected", "resource_type": "ai_request",
                    "resource_id": payload["id"],
                    "reason": json.dumps({"code": code, "quota_scope": quota_scope}, sort_keys=True),
                })
            else:
                reservation = {
                    "id": payload["id"], "request_id": payload["id"],
                    "tenant_id": payload["tenant_id"], "membership_id": payload["membership_id"],
                    "user_id": payload["user_id"], "idempotency_domain": payload["idempotency_domain"],
                    "idempotency_scope": payload["idempotency_scope"], "action": payload["action"],
                    "unit": "request", "quantity": 1, "state": "reserved", "revision": 0,
                    "auth_version": payload["auth_version"],
                    "quota_scope": quota_scope, "quota_source": quota_source,
                    "quota_config_keys": list(quota_config_keys),
                    "daily_limit": daily_limit, "burst_limit": burst_limit,
                    "day_period": day_period, "minute_period": minute_period,
                    "billable": False, "created_at": now, "updated_at": now,
                }
                conn.execute(
                    "INSERT INTO workflow_records (kind,id,data,created_at,updated_at) VALUES (?,?,?,?,?)",
                    ("ai_quota_reservation", payload["id"], self._workflow_payload(reservation), now, now),
                )
                self.save_audit_event_conn(conn, {
                    "id": new_id("audit"), "actor_id": payload["user_id"],
                    "action": "ai.quota.reserved", "resource_type": "ai_request",
                    "resource_id": payload["id"],
                    "reason": json.dumps({"quota_scope": quota_scope, "billable": False}, sort_keys=True),
                })
        if quota_error:
            code, message = quota_error
            raise AiQuotaExceeded(code, message, request_id=payload["id"])
        return payload, True

    def get_ai_quota_reservation(self, request_id: str) -> dict | None:
        return self.get_workflow_record("ai_quota_reservation", request_id)

    def _validate_ai_request_authorization_conn(self, conn: sqlite3.Connection, request: dict) -> None:
        user = conn.execute("SELECT status,must_change_password FROM users WHERE id=?", (request['user_id'],)).fetchone()
        domain = request['idempotency_domain']
        if (user and (user['status'] != 'active' or (domain in {'tenant', 'platform'} and user['must_change_password']))) or (domain in {'tenant', 'platform'} and not user):
            raise PermissionError("AI request account authorization changed")
        if domain == 'tenant':
            member = conn.execute(
                "SELECT m.auth_version,m.capabilities FROM tenant_memberships m JOIN tenants t ON t.id=m.tenant_id "
                "WHERE m.id=? AND m.tenant_id=? AND m.user_id=? AND m.status='active' AND t.status='active'",
                (request['membership_id'], request['tenant_id'], request['user_id']),
            ).fetchone()
            if (not member or member['auth_version'] != request['auth_version']
                    or 'tenant:read' not in json.loads(member['capabilities'] or '[]')):
                raise PermissionError("AI request membership authorization changed")
        elif domain == 'platform':
            binding = conn.execute(
                "SELECT auth_version FROM platform_role_bindings WHERE user_id=? AND status='active' AND role_code='platform_admin'",
                (request['user_id'],),
            ).fetchone()
            if not binding or binding['auth_version'] != request['auth_version']:
                raise PermissionError("AI request platform authorization changed")

    @staticmethod
    def _validate_ai_reservation_binding(request: dict, reservation: dict) -> None:
        fields = ('user_id', 'tenant_id', 'membership_id', 'action', 'idempotency_domain', 'idempotency_scope')
        expected_scope = f"tenant:{request['idempotency_scope']}" if request['idempotency_domain'] == 'tenant' else request['user_id']
        if (reservation.get('request_id') != request['id']
                or any(reservation.get(key) != request[key] for key in fields)
                or reservation.get('quota_scope') != expected_scope
                or reservation.get('auth_version', request['auth_version']) != request['auth_version']
                or reservation.get('quantity') != 1 or reservation.get('unit') != 'request'
                or reservation.get('billable') is not False):
            raise ValueError("AI reservation scope binding mismatch")

    def _update_ai_quota_reservation_conn(
        self, conn: sqlite3.Connection, request_id: str, *, state: str,
        expected_state: str | None = None, **extra: object,
    ) -> dict | None:
        row = conn.execute(
            "SELECT data FROM workflow_records WHERE kind='ai_quota_reservation' AND id=?",
            (request_id,),
        ).fetchone()
        if not row:
            return None
        record = json.loads(row["data"])
        if expected_state is not None and record.get("state") != expected_state:
            raise ValueError("AI reservation transition conflict")
        record["state"] = state
        record["revision"] = int(record.get("revision") or 0) + 1
        record.update(extra)
        record["updated_at"] = _now()
        cur = conn.execute(
            "UPDATE workflow_records SET data=?, updated_at=? WHERE kind='ai_quota_reservation' AND id=?",
            (self._workflow_payload(record), record["updated_at"], request_id),
        )
        return record if cur.rowcount == 1 else None

    def mark_ai_request_dispatching(self, request_id: str) -> dict | None:
        with self.workflow_transaction() as conn:
            row = conn.execute("SELECT * FROM ai_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                return None
            if row["status"] != "reserved":
                raise ValueError("AI request cannot be dispatched twice")
            self._validate_ai_request_authorization_conn(conn, dict(row))
            sidecar = conn.execute("SELECT data FROM workflow_records WHERE kind='ai_quota_reservation' AND id=?", (request_id,)).fetchone()
            if not sidecar:
                raise ValueError("AI reservation missing")
            self._validate_ai_reservation_binding(dict(row), json.loads(sidecar['data']))
            reservation = self._update_ai_quota_reservation_conn(
                conn, request_id, state="dispatching", expected_state="reserved"
            )
            if reservation is None or reservation.get("state") != "dispatching":
                raise RuntimeError("AI reservation missing or already consumed")
            conn.execute(
                "UPDATE ai_requests SET status='dispatching', reservation_status='consumed', updated_at=? WHERE id=?",
                (_now(), request_id),
            )
            return dict(conn.execute("SELECT * FROM ai_requests WHERE id=?", (request_id,)).fetchone())

    def mark_ai_request_outcome(
        self, request_id: str, *, status: str, reservation_status: str,
        error_code: str | None = None, input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> dict | None:
        with self.workflow_transaction() as conn:
            row = conn.execute("SELECT * FROM ai_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                return None
            if row["status"] == "completed":
                return dict(row)
            if status not in {'unknown', 'reconciliation_required', 'failed_final'}:
                raise ValueError("invalid AI outcome")
            if row['status'] in {'unknown', 'reconciliation_required'} and status == 'failed_final':
                raise ValueError("unknown AI outcome must stay frozen")
            if row['reservation_status'] == 'released':
                raise ValueError("released AI reservation cannot be consumed")
            state = "unknown" if status in {"unknown", "reconciliation_required"} else "consumed"
            reservation = self._update_ai_quota_reservation_conn(
                conn, request_id, state=state,
                expected_state="dispatching" if row["status"] == "dispatching" else None,
                error_code=error_code,
            )
            changes = ["status=?", "reservation_status=?", "error_code=?", "updated_at=?"]
            params: list[object] = [status, reservation_status, error_code, _now()]
            if input_tokens is not None:
                changes.append("input_tokens=?"); params.append(input_tokens)
            if output_tokens is not None:
                changes.append("output_tokens=?"); params.append(output_tokens)
            params.append(request_id)
            conn.execute(f"UPDATE ai_requests SET {', '.join(changes)} WHERE id=?", params)
            return dict(conn.execute("SELECT * FROM ai_requests WHERE id=?", (request_id,)).fetchone())

    def release_ai_request_reservation(
        self, request_id: str, *, reason: str, proven_not_dispatched: bool,
    ) -> bool:
        """Release only a still-reserved request; repeat calls are idempotent."""
        if not proven_not_dispatched:
            raise ValueError("explicit proof of non-dispatch is required")
        with self.workflow_transaction() as conn:
            row = conn.execute("SELECT * FROM ai_requests WHERE id=?", (request_id,)).fetchone()
            reservation_row = conn.execute(
                "SELECT data FROM workflow_records WHERE kind='ai_quota_reservation' AND id=?", (request_id,)
            ).fetchone()
            if not row or not reservation_row:
                return False
            reservation = json.loads(reservation_row["data"])
            self._validate_ai_reservation_binding(dict(row), reservation)
            if reservation.get("state") == "released":
                return True
            if reservation.get("state") != "reserved" or row["status"] != "reserved":
                return False
            self._release_ai_quota_conn(conn, reservation["quota_scope"], reservation["minute_period"], _now())
            self._release_ai_quota_conn(conn, reservation["quota_scope"], reservation["day_period"], _now())
            self._update_ai_quota_reservation_conn(
                conn, request_id, state="released", expected_state="reserved", release_reason=reason,
            )
            conn.execute(
                "UPDATE ai_requests SET status='failed_final', reservation_status='released', "
                "error_code='AI_PRE_DISPATCH_RELEASED', updated_at=? WHERE id=?",
                (_now(), request_id),
            )
            self.save_audit_event_conn(conn, {
                "id": new_id("audit"), "actor_id": row["user_id"],
                "action": "ai.quota.released", "resource_type": "ai_request",
                "resource_id": request_id, "reason": reason,
            })
            return True

    def create_ai_request(self, entry: dict) -> dict:
        """Compatibility insert for fixtures and offline migration tools."""
        payload = dict(entry)
        if payload.get("idempotency_key") and "idempotency_domain" not in payload:
            payload["idempotency_domain"] = "legacy"
            payload["idempotency_scope"] = payload.get("user_id")
        return self.register_ai_request(payload)[0]

    def update_ai_request(self, request_id: str, **changes: object) -> None:
        allowed = {
            "status", "reservation_status", "response_text", "error_code",
            "input_tokens", "output_tokens", "updated_at",
        }
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return
        changes.setdefault("updated_at", _now())
        assignments = ", ".join(f"{key} = ?" for key in changes)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE ai_requests SET {assignments} WHERE id = ?",
                (*changes.values(), request_id),
            )

    def list_ai_requests(self, *, tenant_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            if tenant_id is None:
                rows = conn.execute(
                    "SELECT * FROM ai_requests ORDER BY created_at DESC, id DESC LIMIT ?",
                    (max(1, min(limit, 1000)),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ai_requests WHERE tenant_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (tenant_id, max(1, min(limit, 1000))),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_scoped_ai_request(self, request_id: str, *, tenant_id: str, user_id: str | None, membership_id: str | None) -> dict | None:
        if not tenant_id:
            raise ValueError("tenant scope required")
        sql = "SELECT * FROM ai_requests WHERE id=? AND tenant_id=? AND membership_id IS NOT NULL"
        params = [request_id, tenant_id]
        if user_id is not None:
            sql += " AND user_id=? AND membership_id=?"
            params.extend([user_id, membership_id])
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def list_scoped_ai_requests(self, *, tenant_id: str, user_id: str | None, membership_id: str | None, limit: int = 100) -> list[dict]:
        if not tenant_id:
            raise ValueError("tenant scope required")
        sql = "SELECT * FROM ai_requests WHERE tenant_id=? AND membership_id IS NOT NULL"
        params = [tenant_id]
        if user_id is not None:
            sql += " AND user_id=? AND membership_id=?"
            params.extend([user_id, membership_id])
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def mark_ai_reconciliation(self, request_id: str, *, tenant_id: str, actor_id: str, membership_id: str, auth_version: int, request_correlation_id: str):
        """CAS unknown -> reconciliation_required + append audit in ONE transaction.

        No refund, dispatch, response text change or final supplier assertion.
        Audit tenant identity is metadata, NOT the legacy organization_id.
        """
        if not tenant_id or not actor_id or not membership_id or auth_version < 1:
            raise ValueError("resolved member identity required")
        with self.workflow_transaction() as conn:
            current = conn.execute(
                "SELECT m.id FROM tenant_memberships m JOIN users u ON u.id=m.user_id JOIN tenants t ON t.id=m.tenant_id "
                "WHERE m.id=? AND m.tenant_id=? AND m.user_id=? AND m.auth_version=? "
                "AND m.status='active' AND u.status='active' AND u.must_change_password=0 AND t.status='active'",
                (membership_id, tenant_id, actor_id, auth_version),
            ).fetchone()
            if current is None:
                raise PermissionError("authorization snapshot is stale")
            row = conn.execute("SELECT * FROM ai_requests WHERE id=? AND tenant_id=? AND membership_id IS NOT NULL", (request_id, tenant_id)).fetchone()
            if row is None:
                return None
            row = dict(row)
            if row["status"] != "unknown":
                return row, row["status"] == "reconciliation_required"
            changed = conn.execute("UPDATE ai_requests SET status='reconciliation_required', updated_at=? WHERE id=? AND tenant_id=? AND status='unknown'", (_now(), request_id, tenant_id))
            if changed.rowcount != 1:
                raise RuntimeError("AI reconciliation compare-and-swap failed")
            self.save_audit_event_conn(conn, {
                "actor_id": actor_id, "action": "ai.request_reconciliation", "resource_type": "ai_request", "resource_id": request_id,
                "request_id": request_correlation_id, "reason": json.dumps({"tenant_id": tenant_id, "membership_id": membership_id,
                    "auth_version": auth_version, "before": "unknown", "after": "reconciliation_required", "reason_code": "unknown_external_result"}, sort_keys=True),
            })
            updated = conn.execute("SELECT * FROM ai_requests WHERE id=? AND tenant_id=?", (request_id, tenant_id)).fetchone()
            return dict(updated), False

    # ----------------------- system settings -----------------------

    def get_system_setting(self, key: str, organization_id: str = "") -> str | None:
        """读取系统设置。

        `organization_id` 非空时只读**该学校自己的**配置，读不到不回退——
        调用方需要显式决定是否回退到全局（见 `get_tenant_system_setting`）。
        两者混在一处会让人误以为「A 校读不到就自动用平台配置」，
        而那正是 S2 要修的跨租户数据外流。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM system_settings WHERE key = ? AND organization_id = ?",
                (key, organization_id or ""),
            ).fetchone()
        return row["value"] if row else None

    def get_tenant_system_setting(self, key: str, organization_id: str = "") -> str | None:
        """先读本租户配置，回退到平台全局默认。"""
        value = self.get_system_setting(key, organization_id)
        if value is not None:
            return value
        if organization_id:
            return self.get_system_setting(key, "")
        return None

    def set_system_setting(self, key: str, value: str | None, organization_id: str = "") -> None:
        org = organization_id or ""
        with self._connect() as conn:
            if value is None:
                conn.execute(
                    "DELETE FROM system_settings WHERE key = ? AND organization_id = ?",
                    (key, org),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO system_settings (key, organization_id, value, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (key, org, value, _now()),
                )

    def list_system_settings_organizations(self, key: str) -> list[str]:
        """已单独配置过该设置的组织列表（管理端可见）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT organization_id FROM system_settings WHERE key = ?"
                " AND organization_id != '' ORDER BY organization_id",
                (key,),
            ).fetchall()
        return [r["organization_id"] for r in rows]

    def get_system_ai_config(self, organization_id: str = "") -> dict | None:
        """按学校读取默认模型配置，未单独配置时回退到平台全局默认。"""
        raw = self.get_tenant_system_setting("ai_default_config", organization_id)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        # 存量明文原样返回；密文在此解密
        return secret_store.reveal_config(data) if isinstance(data, dict) else None

    def set_system_ai_config(self, cfg: dict | None, organization_id: str = "") -> None:
        payload = (
            json.dumps(secret_store.protect_config(cfg), ensure_ascii=False) if cfg else None
        )
        self.set_system_setting("ai_default_config", payload, organization_id)

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
            "INSERT INTO audit_events "
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

    def list_workflow_records(self, kind: str, limit: int | None = 500) -> list[dict]:
        with self._connect() as conn:
            if limit is None:
                rows = conn.execute(
                    "SELECT data FROM workflow_records WHERE kind=? ORDER BY updated_at DESC, id", (kind,),
                ).fetchall()
            else:
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
            # W-09 LE-5/LE-6：**精确匹配** lease_token，不留 `IS NULL OR = ''` 通配。
            # 旧代码的通配分支不是为「未领取」设计的，是图省事——代价是
            # 任何人都能提交别人的任务。便利性不应由安全性买单。
            cur = conn.execute(
                "UPDATE jobs SET state = 'succeeded', lease_until = NULL, lease_token = NULL, leased_by = NULL, "
                "completed_at = ?, updated_at = ? WHERE id = ? AND state = 'running' "
                "AND lease_token = ?",
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
        """Acknowledge an event, requiring the active lease token.

        W-09 LE-6：**精确匹配** lease_token，不留 `IS NULL OR = ''` 通配。
        本处比 `complete_job` 更要紧——它的条件不含「已被领取」，
        所以修前**任何 `pending` 的事件都能被无令牌 ack 成 delivered**（INC-105 实测）。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE workflow_events SET delivery_status = 'delivered', delivered_at = COALESCE(delivered_at, ?), "
                "lease_until = NULL, lease_token = NULL, leased_by = NULL "
                "WHERE id = ? AND delivery_status != 'delivered' "
                "AND lease_token = ?",
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
        if proj.tenant_id or proj.membership_id:
            raise PermissionError("tenant projects require scoped repository")
        proj.updated_at = _now()
        with self.workflow_transaction() as conn:
            existing = conn.execute("SELECT data FROM projects WHERE id=?", (proj.id,)).fetchone()
            if existing:
                body = json.loads(existing["data"])
                if body.get("tenant_id") or body.get("membership_id"):
                    raise PermissionError("legacy project write cannot replace tenant ownership")
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
        if proj.tenant_id or proj.membership_id:
            raise PermissionError("tenant projects require scoped repository")
        next_revision = expected_revision + 1
        proj.revision = next_revision
        proj.updated_at = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE projects SET template_id = ?, data = ?, updated_at = ? "
                "WHERE id = ? AND json_extract(data, '$.revision') = ? "
                "AND coalesce(json_extract(data, '$.tenant_id'), '') = '' "
                "AND coalesce(json_extract(data, '$.membership_id'), '') = ''",
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
                "WHERE id = ? AND json_extract(data, '$.revision') = ? "
                "AND coalesce(json_extract(data, '$.tenant_id'), '') = '' "
                "AND coalesce(json_extract(data, '$.membership_id'), '') = ''",
                (project.model_dump_json(), project.updated_at, project.id, expected_revision),
            )
            if cur.rowcount != 1:
                return None
        return project

    def get_project(self, proj_id: str) -> WritingProject | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM projects WHERE id = ? AND coalesce(json_extract(data, '$.tenant_id'), '') = '' AND coalesce(json_extract(data, '$.membership_id'), '') = ''", (proj_id,)
            ).fetchone()
        return WritingProject.model_validate_json(row["data"]) if row else None

    def list_projects(self) -> list[WritingProject]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM projects WHERE coalesce(json_extract(data, '$.tenant_id'), '') = '' AND coalesce(json_extract(data, '$.membership_id'), '') = '' ORDER BY updated_at DESC").fetchall()
        return [WritingProject.model_validate_json(r["data"]) for r in rows]

    def delete_project(self, proj_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ? AND coalesce(json_extract(data, '$.tenant_id'), '') = '' AND coalesce(json_extract(data, '$.membership_id'), '') = ''", (proj_id,))
            return cur.rowcount > 0


def template_to_json(tpl: PaperTemplate) -> str:
    return json.dumps(tpl.model_dump(), ensure_ascii=False, indent=2)
