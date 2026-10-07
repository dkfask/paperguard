"""W-08 tenant/member idempotency and dispatch-concurrency slice."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.api import ai
from app.db import Store
from app.services import saas_identity


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    store = Store(tmp_path / "idempotency.db")
    user = store.save_user({
        "id": "u", "username": "idempotency-fixture", "role": "student",
        "status": "active", "must_change_password": 0,
        "password_hash": "fixture", "salt": "fixture", "display_name": "fixture",
        "created_at": "2026-10-04T00:00:00+00:00",
    })
    for tenant_id in ("a", "b"):
        store.create_tenant({"id": tenant_id, "slug": tenant_id, "name": tenant_id})
        store.save_membership({
            "id": f"m-{tenant_id}", "user_id": "u", "tenant_id": tenant_id,
            "role": "student", "capabilities": ["tenant:read"],
        })
    cfg = {"base_url": "https://provider.invalid/v1", "api_key": "synthetic-only", "model": "synthetic-model"}
    monkeypatch.setattr(ai, "assert_safe_outbound_url", lambda value, **kwargs: value)
    monkeypatch.setattr(ai.ai_governance, "begin_call", lambda *args: None)
    monkeypatch.setattr(ai.ai_governance, "record_usage", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(ai, "_chat_completion", lambda *args, **kwargs: calls.append(1) or "response")
    return store, user, cfg, calls


def dispatch(scenario, *, tenant="a", action="polish", text="same", key="key"):
    store, user, cfg, _ = scenario
    return ai._metered_chat(
        store, user | {"tenant_id": tenant}, cfg,
        [{"role": "user", "content": text}], 0.2,
        action=action, source="user", idempotency_key=key,
    )


def test_same_tenant_same_operation_and_payload_has_one_dispatch(scenario):
    assert dispatch(scenario) == "response"
    assert dispatch(scenario) == "response"
    assert scenario[3] == [1]
    rows = scenario[0].list_ai_requests(tenant_id="a")
    assert len(rows) == 1
    assert rows[0]["idempotency_domain"] == "tenant"
    assert rows[0]["idempotency_scope"] == "a:m-a"
    assert rows[0]["auth_version"] == 1


def test_same_key_different_payload_is_conflict(scenario):
    assert dispatch(scenario, text="first") == "response"
    with pytest.raises(HTTPException) as exc:
        dispatch(scenario, text="second")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert scenario[3] == [1]


def test_same_key_different_operation_is_not_reused(scenario):
    assert dispatch(scenario, action="polish") == "response"
    assert dispatch(scenario, action="continue") == "response"
    assert scenario[3] == [1, 1]
    assert {(row["action"], row["idempotency_domain"]) for row in scenario[0].list_ai_requests()} == {
        ("polish", "tenant"), ("continue", "tenant")
    }


def test_old_historical_user_key_does_not_block_new_tenant_domain(scenario):
    store, user, _, _ = scenario
    old = store.create_ai_request({
        "id": "old", "idempotency_key": "key", "request_hash": "old-hash",
        "user_id": "u", "tenant_id": "a", "membership_id": "m-a", "action": "polish",
        "idempotency_domain": "historical_legacy", "idempotency_scope": "historical:u",
        "status": "completed", "response_text": "old response",
    })
    assert old["idempotency_domain"] == "historical_legacy"
    assert dispatch(scenario, tenant="a") == "response"
    assert scenario[3] == [1]
    rows = store.list_ai_requests()
    assert len(rows) == 2
    assert {row["idempotency_domain"] for row in rows} == {"historical_legacy", "tenant"}


def test_membership_auth_version_change_blocks_replay(scenario):
    assert dispatch(scenario) == "response"
    store, _, _, calls = scenario
    store.save_membership({
        "id": "m-a", "user_id": "u", "tenant_id": "a", "role": "student",
        "capabilities": ["tenant:read", "project:read"],
    })
    with pytest.raises(HTTPException) as exc:
        dispatch(scenario)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "AUTHORIZATION_STALE"
    assert calls == [1]


def test_concurrent_same_key_has_one_persistent_request_and_dispatch(scenario):
    def invoke():
        try:
            return ("ok", dispatch(scenario))
        except HTTPException as exc:
            return ("error", exc.detail["code"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))
    assert scenario[3] == [1]
    assert len(scenario[0].list_ai_requests(tenant_id="a")) == 1
    assert {item[0] for item in results} == {"ok", "error"} or {item[0] for item in results} == {"ok"}
    assert all(item[1] in {"response", "REQUEST_IN_PROGRESS"} for item in results)


def test_platform_domain_requires_explicit_binding_not_admin_role(scenario):
    store, _, cfg, calls = scenario
    platform = store.save_user({
        "id": "p", "username": "platform", "role": "admin", "status": "active",
        "password_hash": "fixture", "salt": "fixture", "display_name": "platform",
        "created_at": "2026-10-04T00:00:00+00:00",
    })
    ai._metered_chat(store, platform, cfg, [{"role": "user", "content": "x"}], 0.2,
                     action="polish", source="user", idempotency_key="platform-key")
    row = store.get_ai_request_by_idempotency("p", "platform-key")
    assert row["idempotency_domain"] == "legacy"
    saas_identity.approve_platform_role(store, "p", approval_ref="offline-1", approved_by="operator-1")
    ai._metered_chat(store, platform, cfg, [{"role": "user", "content": "x"}], 0.2,
                     action="polish", source="user", idempotency_key="bound-key")
    bound = store.get_ai_request_by_idempotency("p", "bound-key")
    assert bound["idempotency_domain"] == "platform"
    assert bound["tenant_id"] is None and bound["membership_id"] is None
    assert len(calls) == 2


def test_0014_migration_preserves_old_rows_without_tenant_backfill(tmp_path):
    path = tmp_path / "legacy-migration.db"
    initial = Store(path)
    with initial._connect() as conn:
        conn.execute("ALTER TABLE ai_requests RENAME TO ai_requests_legacy_fixture")
        conn.execute("""CREATE TABLE ai_requests (
            id TEXT PRIMARY KEY, idempotency_key TEXT, request_hash TEXT NOT NULL,
            user_id TEXT NOT NULL, tenant_id TEXT, membership_id TEXT, action TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '', base_url_host TEXT, model TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, reservation_status TEXT NOT NULL DEFAULT 'reserved',
            response_text TEXT, error_code TEXT, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (user_id, idempotency_key)
        )""")
        conn.execute(
            "INSERT INTO ai_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old", "same-key", "old-hash", "u", "a", "m-a", "polish", "mock",
             "provider.invalid", "model", "completed", "settled", "old response", None, 1, 1,
             "2026-10-04T00:00:00+00:00", "2026-10-04T00:00:00+00:00"),
        )
    migrated = Store(path)
    row = migrated.get_ai_request_by_idempotency("u", "same-key")
    assert row["idempotency_domain"] == "historical_legacy"
    assert row["idempotency_scope"] == "historical:u"
    assert row["tenant_id"] == "a" and row["membership_id"] == "m-a"
    with migrated._connect() as conn:
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(ai_requests)")}
    assert "uq_ai_requests_idempotency_domain" in indexes
