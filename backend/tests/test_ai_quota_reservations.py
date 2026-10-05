"""W-08/C1: atomic non-monetary quota reservations and frozen unknown holds."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.api import ai
from app.db import Store
from app.services import ai_governance


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    store = Store(tmp_path / "quota.db")
    user = store.save_user({
        "id": "u", "username": "quota-fixture", "role": "student",
        "status": "active", "must_change_password": 0,
        "password_hash": "fixture", "salt": "fixture", "display_name": "fixture",
        "created_at": "2026-10-05T00:00:00+00:00",
    })
    for tenant_id in ("a", "b"):
        store.create_tenant({"id": tenant_id, "slug": tenant_id, "name": tenant_id})
        store.save_membership({
            "id": f"m-{tenant_id}", "user_id": "u", "tenant_id": tenant_id,
            "role": "student", "capabilities": ["tenant:read"],
        })
    cfg = {"base_url": "https://provider.invalid/v1", "api_key": "synthetic-only", "model": "synthetic-model"}
    monkeypatch.setattr(ai, "assert_safe_outbound_url", lambda value, **kwargs: value)
    monkeypatch.setattr(ai, "_chat_completion", lambda *args, **kwargs: "response")
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "200")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "200")
    return store, user, cfg


def dispatch(scenario, *, tenant="a", text="same", key="key"):
    store, user, cfg = scenario
    return ai._metered_chat(
        store, user | {"tenant_id": tenant}, cfg,
        [{"role": "user", "content": text}], 0.2,
        action="polish", source="user", idempotency_key=key,
    )


def _reservation_for(store, tenant="a", key=None):
    rows = store.list_ai_requests(tenant_id=tenant)
    row = next((item for item in rows if key is None or item["idempotency_key"] == key), rows[0])
    return row, store.get_ai_quota_reservation(row["id"])


def test_duplicate_replay_has_one_reservation_and_one_quota_charge(scenario):
    assert dispatch(scenario) == "response"
    assert dispatch(scenario) == "response"
    row, reservation = _reservation_for(scenario[0])
    assert reservation["state"] == "settled"
    assert reservation["unit"] == "request"
    assert reservation["quantity"] == 1
    assert reservation["billable"] is False
    with scenario[0]._connect() as conn:
        counters = conn.execute(
            "SELECT used FROM ai_quota WHERE user_id=? AND period IN (?, ?)",
            (reservation["quota_scope"], reservation["day_period"], reservation["minute_period"]),
        ).fetchall()
    assert sorted(int(item["used"]) for item in counters) == [1, 1]
    assert row["reservation_status"] == "settled"


def test_same_key_different_payload_does_not_consume_another_reservation(scenario):
    assert dispatch(scenario, text="first") == "response"
    with pytest.raises(HTTPException) as exc:
        dispatch(scenario, text="second")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(scenario[0].list_ai_requests(tenant_id="a")) == 1


def test_same_account_two_tenants_have_independent_quota_scopes(scenario, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "10")
    assert dispatch(scenario, tenant="a", key="a-key") == "response"
    assert dispatch(scenario, tenant="b", key="b-key") == "response"
    assert len(scenario[0].list_ai_requests(tenant_id="a")) == 1
    assert len(scenario[0].list_ai_requests(tenant_id="b")) == 1


def test_concurrent_different_keys_cannot_overcommit_one_scope(scenario, monkeypatch):
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "1")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "10")

    def attempt(key):
        try:
            return (200, dispatch(scenario, key=key))
        except HTTPException as exc:
            return (exc.status_code, exc.detail["code"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("first", "second")))
    assert sorted(results) == [(200, "response"), (429, "AI_DAILY_QUOTA_EXCEEDED")]
    assert len(scenario[0].list_ai_requests(tenant_id="a")) == 2
    assert sum(row["status"] == "completed" for row in scenario[0].list_ai_requests(tenant_id="a")) == 1


def test_audit_failure_rolls_back_request_reservation_and_quota(scenario, monkeypatch):
    store, user, cfg = scenario
    original = store.save_audit_event_conn

    def fail(conn, event):
        if event.get("action") == "ai.quota.reserved":
            raise RuntimeError("audit unavailable")
        return original(conn, event)

    monkeypatch.setattr(store, "save_audit_event_conn", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        dispatch(scenario)
    assert store.list_ai_requests(tenant_id="a") == []
    assert store.list_workflow_records("ai_quota_reservation") == []
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_quota").fetchone()[0] == 0


def test_pre_dispatch_release_is_one_shot_and_unknown_is_frozen(scenario, monkeypatch):
    store, user, cfg = scenario
    identity = {"domain": "tenant", "scope": "a:m-a"}
    entry = {
        "id": "pre-dispatch", "idempotency_key": "pre-key", "request_hash": "hash",
        "user_id": "u", "tenant_id": "a", "membership_id": "m-a", "action": "polish",
        "idempotency_domain": "tenant", "idempotency_scope": "a:m-a", "auth_version": 1,
        "provider": "provider.invalid", "model": "synthetic-model",
    }
    row, created = ai_governance.reserve_call(store, user, domain=identity["domain"], scope=identity["scope"], entry=entry)
    assert created and row["status"] == "reserved"
    assert store.release_ai_request_reservation(row["id"], reason="proved not sent", proven_not_dispatched=True)
    assert store.release_ai_request_reservation(row["id"], reason="repeat", proven_not_dispatched=True)
    assert store.get_ai_quota_reservation(row["id"])["state"] == "released"
    assert store.get_ai_request_by_idempotency("u", "pre-key", idempotency_domain="tenant", idempotency_scope="a:m-a", action="polish")["reservation_status"] == "released"

    def timeout(*args, **kwargs):
        raise HTTPException(status_code=504, detail="timeout")

    monkeypatch.setattr(ai, "_chat_completion", timeout)
    with pytest.raises(HTTPException):
        dispatch(scenario, key="unknown-key")
    unknown, frozen = _reservation_for(store, key="unknown-key")
    assert unknown["status"] == unknown["reservation_status"] == "unknown"
    assert frozen["state"] == "unknown"
    with pytest.raises(HTTPException) as exc:
        dispatch(scenario, key="unknown-key")
    assert exc.value.detail["code"] == "AI_RECONCILIATION_REQUIRED"
