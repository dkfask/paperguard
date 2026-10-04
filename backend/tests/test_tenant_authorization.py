"""Fail-closed tenant authorization and request-context regression coverage."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.api import ai, saas
from app.core.context import context_for_user, get_request_context
from app.db import Store
from app.main import add_request_context
from app.services.tenant_access import (
    assert_tenant_scope, effective_capabilities, require_capability, resolve_membership,
)


@pytest.fixture
def identity(tmp_path):
    store = Store(tmp_path / "auth.db")
    user = store.save_user({
        "id": "user-test", "username": "teacher-test", "password_hash": "unused",
        "salt": "unused", "role": "teacher", "status": "active",
        "display_name": "Test teacher", "created_at": "2026-10-04T00:00:00+00:00",
        "organization_context": {"school_id": "school-test"},
    })
    tenant = store.create_tenant({"id": "tenant-test", "slug": "tenant-test", "name": "Test"})
    return store, user, tenant


def _membership(store, user, tenant, **changes):
    return store.save_membership({
        "id": "member-test", "tenant_id": tenant["id"], "user_id": user["id"],
        "role": "teacher", "capabilities": ["tenant:read"], **changes,
    })


def _client(store, user, monkeypatch):
    probe = FastAPI()
    probe.middleware("http")(add_request_context)
    probe.dependency_overrides[auth.get_store] = lambda: store
    probe.dependency_overrides[saas.get_store] = lambda: store
    probe.dependency_overrides[ai.get_store] = lambda: store
    monkeypatch.setattr(store, "get_session_user", lambda *args, **kwargs: dict(user))
    probe.include_router(saas.router)
    probe.include_router(ai.router)

    def context_payload():
        ctx = get_request_context()
        return {"actor": ctx.actor_id, "tenant": ctx.tenant_id, "membership": ctx.membership_id,
                "caps": ctx.capabilities, "request_id": ctx.request_id}

    @probe.get("/sync")
    def sync_endpoint(actor=Depends(auth.current_user)):
        return context_payload()

    @probe.get("/async")
    async def async_endpoint(actor=Depends(auth.current_user)):
        return context_payload()

    @probe.get("/anonymous")
    def anonymous_endpoint():
        return context_payload()

    return TestClient(probe)


HEADERS = {"Authorization": "Bearer synthetic-test-token"}


def test_empty_capabilities_are_an_explicit_deny(identity):
    store, user, tenant = identity
    member = _membership(store, user, tenant, role="admin", capabilities=[])
    assert effective_capabilities(user, member) == frozenset()
    with pytest.raises(HTTPException) as exc:
        assert_tenant_scope(tenant["id"], user=user, store=store)
    assert exc.value.detail["code"] == "CAPABILITY_REQUIRED"


def test_tenant_admin_has_no_platform_operations_capabilities(identity):
    _, user, _ = identity
    caps = effective_capabilities(user, {"role": "admin"})
    assert "backup:global" not in caps
    assert "operations:manage" not in caps


def test_authenticated_resolution_does_not_write_legacy_mapping(identity):
    store, user, _ = identity
    assert resolve_membership(store, user) is None
    assert store.list_memberships_for_user(user["id"]) == []
    assert store.get_tenant("tenant-legacy-school-test") is None


def test_revoked_membership_is_not_resurrected(identity):
    store, user, tenant = identity
    tenant = store.create_tenant(tenant | {"id": "tenant-legacy-school-test", "slug": "school-test"})
    member = _membership(store, user, tenant, status="revoked")
    assert resolve_membership(store, user) is None
    assert store.get_membership(member["id"])["status"] == "revoked"
    assert len(store.list_memberships_for_user(user["id"])) == 1


def test_explicit_bridge_cannot_restore_revoked_membership(identity):
    store, user, tenant = identity
    tenant = store.create_tenant(tenant | {"id": "tenant-legacy-school-test", "slug": "school-test"})
    member = _membership(store, user, tenant, status="revoked")
    bridged = store.ensure_legacy_tenant_membership(user)
    assert bridged["id"] == member["id"]
    assert bridged["status"] == "revoked"


def test_multiple_memberships_require_explicit_selection(identity):
    store, user, tenant = identity
    _membership(store, user, tenant)
    other = store.create_tenant({"id": "tenant-other", "slug": "other", "name": "Other"})
    _membership(store, user, other, id="member-other")
    assert resolve_membership(store, user) is None
    user["organization_context"]["tenant_id"] = tenant["id"]
    assert resolve_membership(store, user)["tenant_id"] == tenant["id"]


@pytest.mark.parametrize("status", ["suspended", "inactive", "deleted"])
def test_inactive_tenant_denies_scope_and_capability(identity, status):
    store, user, tenant = identity
    _membership(store, user, tenant)
    store.create_tenant(tenant | {"status": status})
    assert resolve_membership(store, user) is None
    for check in [lambda: assert_tenant_scope(tenant["id"], user=user, store=store),
                  lambda: require_capability("tenant:read", user=user, store=store)]:
        with pytest.raises(HTTPException):
            check()


@pytest.mark.parametrize("path", ["/sync", "/async"])
def test_context_propagates_and_does_not_leak(identity, monkeypatch, path):
    store, user, tenant = identity
    member = _membership(store, user, tenant)
    client = _client(store, user, monkeypatch)
    result = client.get(path, headers=HEADERS)
    assert result.status_code == 200
    assert result.json() == {"actor": user["id"], "tenant": tenant["id"],
                             "membership": member["id"], "caps": ["tenant:read"],
                             "request_id": result.headers["X-Request-ID"]}
    anonymous = client.get("/anonymous")
    assert anonymous.json()["actor"] == anonymous.json()["tenant"] == ""
    assert anonymous.headers["X-Request-ID"] != result.headers["X-Request-ID"]


def test_unverified_context_hints_do_not_become_authorization_evidence():
    context = context_for_user({"id": "u", "tenant_id": "unverified", "membership_id": "unverified",
                                "capabilities": ["tenant:manage"], "organization_context": {"tenant_id": "unverified"}})
    assert context.tenant_id == context.membership_id == ""
    assert context.capabilities == ()


def test_membership_empty_caps_cannot_inherit_user_caps():
    context = context_for_user({"id": "u", "capabilities": ["backup:global"]},
                               membership={"id": "m", "tenant_id": "t", "role": "teacher", "capabilities": []})
    assert context.capabilities == ()


@pytest.mark.parametrize("tenant_id", ["unknown", "tenant-legacy-unknown", "tenant-test"])
def test_invalid_tenant_checked_before_byok(identity, monkeypatch, tenant_id):
    store, user, _ = identity
    user["tenant_id"] = tenant_id
    reads = []
    monkeypatch.setattr(store, "get_user_ai_config", lambda *args: reads.append(True) or {
        "base_url": "https://example.com/v1", "api_key": "synthetic-key", "model": "test"})
    with pytest.raises(HTTPException) as exc:
        ai._resolve_cfg(store, user)
    assert exc.value.detail["code"] == "TENANT_CONTEXT_INVALID"
    assert reads == []


def test_membership_update_preserves_id_and_bumps_authorization_version(identity):
    store, user, tenant = identity
    first = _membership(store, user, tenant)
    changed = _membership(store, user, tenant, id="replacement-id", capabilities=[], auth_version=1)
    assert changed["id"] == first["id"]
    assert changed["auth_version"] == first["auth_version"] + 1
    assert _membership(store, user, tenant, capabilities=[])["auth_version"] == changed["auth_version"]
    assert _membership(store, user, tenant, status="revoked", capabilities=[])["auth_version"] == changed["auth_version"] + 1


@pytest.mark.parametrize("status", ["pending", "suspended", "disabled"])
def test_inactive_global_admin_is_denied(status):
    with pytest.raises(HTTPException):
        auth.require_admin({"role": "admin", "status": status})


def test_saas_api_rejects_platform_grants_in_membership(identity, monkeypatch):
    store, user, tenant = identity
    user["role"] = "admin"
    client = _client(store, user, monkeypatch)
    for payload in [{"role": "platform_admin", "capabilities": []},
                    {"role": "teacher", "capabilities": ["backup:global"]}]:
        response = client.post(f'/api/saas/v1/tenants/{tenant["id"]}/memberships', headers=HEADERS,
                               json={"user_id": user["id"], **payload})
        assert response.status_code == 422
    assert store.list_memberships_for_user(user["id"]) == []


def test_existing_session_sees_revocation(identity, monkeypatch):
    store, user, tenant = identity
    member = _membership(store, user, tenant)
    user["organization_context"]["tenant_id"] = tenant["id"]
    client = _client(store, user, monkeypatch)
    assert client.get("/api/saas/v1/context", headers=HEADERS).json()["context"]["membership_id"] == member["id"]
    store.save_membership(member | {"status": "revoked"})
    assert client.get("/api/saas/v1/context", headers=HEADERS).status_code == 403


@pytest.mark.parametrize("state", ["revoked", "suspended", "ambiguous"])
def test_missing_tenant_does_not_bypass_existing_membership_boundary(identity, monkeypatch, state):
    store, user, tenant = identity
    _membership(store, user, tenant, status="revoked" if state == "revoked" else "active")
    if state == "suspended":
        store.create_tenant(tenant | {"status": "suspended"})
    if state == "ambiguous":
        other = store.create_tenant({"id": "other", "slug": "other", "name": "Other"})
        _membership(store, user, other, id="other-member")
    reads = []
    monkeypatch.setattr(store, "get_user_ai_config", lambda *args: reads.append(True))
    with pytest.raises(HTTPException):
        ai._resolve_cfg(store, user)
    assert not reads


def test_valid_membership_can_resolve_byok(identity, monkeypatch):
    store, user, tenant = identity
    _membership(store, user, tenant)
    user["tenant_id"] = tenant["id"]
    cfg = {"base_url": "https://example.com/v1", "api_key": "synthetic", "model": "test"}
    monkeypatch.setattr(store, "get_user_ai_config", lambda *args: cfg)
    assert ai._resolve_cfg(store, user) == (cfg, "user")


def test_tenant_admin_role_cannot_create_tenant(identity, monkeypatch):
    store, user, tenant = identity
    _membership(store, user, tenant, role="admin", capabilities=["tenant:manage"])
    client = _client(store, user, monkeypatch)
    response = client.post("/api/saas/v1/tenants", headers=HEADERS, json={"slug": "new", "name": "New"})
    assert response.status_code == 403
    assert len(store.list_tenants()) == 1


def test_stored_platform_capabilities_are_stripped_from_membership(identity):
    store, user, tenant = identity
    member = _membership(store, user, tenant, capabilities=["tenant:read", "backup:global", "operations:manage"])
    assert effective_capabilities(user, member) == frozenset({"tenant:read"})


@pytest.mark.parametrize("inactive", [False, True])
def test_tenant_list_respects_account_status_and_explicit_empty_grants(identity, monkeypatch, inactive):
    store, user, tenant = identity
    _membership(store, user, tenant, capabilities=["tenant:read"] if inactive else [])
    if inactive:
        user["status"] = "suspended"
    response = _client(store, user, monkeypatch).get("/api/saas/v1/tenants", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("state", ["revoked", "suspended", "ambiguous"])
@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/ai/config", None),
    ("GET", "/api/ai/status", None),
    ("POST", "/api/ai/config/test", {"base_url": "https://example.com/v1", "api_key": "synthetic", "model": "m"}),
    ("PUT", "/api/ai/config", {"base_url": "https://example.com/v1", "api_key": "synthetic", "model": "m"}),
    ("DELETE", "/api/ai/config", None),
    ("POST", "/api/ai/assist", {"action": "polish", "text": "synthetic"}),
])
def test_unselected_or_revoked_identity_denied_before_ai_credentials_and_dispatch(
    identity, monkeypatch, state, method, path, payload,
):
    store, user, tenant = identity
    _membership(store, user, tenant, status="revoked" if state == "revoked" else "active")
    if state == "suspended":
        store.create_tenant(tenant | {"status": "suspended"})
    if state == "ambiguous":
        other = store.create_tenant({"id": "other", "slug": "other", "name": "Other"})
        _membership(store, user, other, id="other-member")
    reads, calls = [], []
    monkeypatch.setattr(store, "get_user_ai_config", lambda *args: reads.append(True))
    monkeypatch.setattr(ai, "_dispatch_completion", lambda *args, **kwargs: calls.append(True))
    client = _client(store, user, monkeypatch)
    response = client.request(method, path, headers=HEADERS, **({"json": payload} if payload else {}))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "TENANT_CONTEXT_INVALID"
    assert reads == calls == []



def test_verified_legacy_member_keeps_school_config_alias_without_bypassing_auth(identity, monkeypatch):
    store, user, tenant = identity
    tenant = store.create_tenant(tenant | {"id": "tenant-legacy-school-test", "slug": "school-test"})
    member = _membership(store, user, tenant)
    user["tenant_id"] = tenant["id"]
    scopes = []
    cfg = {"base_url": "https://school.example/v1", "api_key": "synthetic", "model": "school-model"}
    monkeypatch.setattr(store, "get_user_ai_config", lambda *args: None)
    monkeypatch.setattr(ai, "_system_cfg", lambda s, scope: scopes.append(scope) or (cfg, "system_db"))
    assert ai._resolve_cfg(store, user) == (cfg, "system")
    assert scopes == ["school-test"]
    store.save_membership(member | {"status": "revoked"})
    with pytest.raises(HTTPException):
        ai._resolve_cfg(store, user)
    assert scopes == ["school-test"]
