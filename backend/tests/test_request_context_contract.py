"""Isolated offline verification of the trusted SaaS context boundary."""
from dataclasses import FrozenInstanceError

import pytest
from fastapi import HTTPException

from app.core.context import RequestContext
from app.db import Store


def member(**changes):
    return RequestContext(actor_kind="member", actor_id="u", tenant_id="t", membership_id="m", auth_version=1, **changes)


def test_neutral_context_is_not_an_authorization_identity():
    ctx = RequestContext()
    assert ctx.actor_kind == "unresolved" and ctx.tenant_id is None
    assert not ctx.has_capability("tenant:read")
    for values in [{"actor_id": "u"}, {"capabilities": ("tenant:read",)}, {"organization_ids": ("school",)}]:
        with pytest.raises(ValueError, match="CI-5"):
            RequestContext(**values)


@pytest.mark.parametrize("changes", [{"tenant_id": None}, {"membership_id": None}, {"auth_version": 0}, {"actor_id": ""}])
def test_member_identity_cannot_be_partial(changes):
    with pytest.raises(ValueError, match="CI-1"):
        RequestContext(**({"actor_kind": "member", "actor_id": "u", "tenant_id": "t", "membership_id": "m", "auth_version": 1} | changes))


def test_platform_cannot_carry_tenant_or_membership():
    for field in ["tenant_id", "membership_id"]:
        with pytest.raises(ValueError, match="CI-2"):
            RequestContext(actor_kind="platform", actor_id="p", **{field: "t"})


def test_unsupported_support_is_denied_not_assumed_valid():
    with pytest.raises(ValueError, match="CI-3"):
        RequestContext(actor_kind="support", actor_id="s", support_grant_id="guessed")


def test_context_deep_immutability_and_frozen_fields():
    values = ["tenant:read"]
    ctx = member(capabilities=values, organization_ids=["school"])
    values.append("tenant:manage")
    assert ctx.capabilities == ("tenant:read",) and ctx.organization_ids == ("school",)
    with pytest.raises(FrozenInstanceError):
        ctx.auth_version = 2


@pytest.fixture
def records(tmp_path):
    store = Store(tmp_path / "context.db")
    user = store.save_user({"id": "u", "username": "u", "password_hash": "unused", "salt": "unused", "role": "teacher", "status": "active", "display_name": "Test", "created_at": "2026-10-04T00:00:00+00:00"})
    t = store.create_tenant({"id": "t", "slug": "t", "name": "T"})
    m = store.save_membership({"id": "m", "user_id": "u", "tenant_id": "t", "role": "teacher", "capabilities": ["tenant:read"]})
    return store, user, t, m


def resolve(store, user, **kw):
    from app.services.tenant_access import resolve_request_context
    return resolve_request_context(store, user, request_id="request", **kw)


@pytest.mark.parametrize("user,code,status", [(None,"AUTH_SESSION_INVALID",401), ({"id":"u","status":"pending"},"AUTH_ACCOUNT_BLOCKED",403), ({"id":"u","status":"active","must_change_password":1},"AUTH_ACCOUNT_BLOCKED",403)])
def test_resolver_session_and_account_exits(records, user, code, status):
    with pytest.raises(HTTPException) as exc:
        resolve(records[0], user)
    assert exc.value.status_code == status and exc.value.detail["code"] == code


def test_selector_is_not_a_grant_and_membership_precedes_tenant_read(records, monkeypatch):
    store, user, _, _ = records
    reads = []
    original = store.get_tenant
    monkeypatch.setattr(store, "get_tenant", lambda tid: reads.append(tid) or original(tid))
    with pytest.raises(HTTPException) as exc:
        resolve(store, user, expected_tenant="other")
    assert exc.value.detail["code"] == "TENANT_HEADER_NOT_HONORED" and reads == []


@pytest.mark.parametrize("state,code", [("revoked","TENANT_MEMBERSHIP_INACTIVE"), ("suspended","TENANT_MEMBERSHIP_INACTIVE")])
def test_current_membership_state_is_authoritative(records, state, code):
    store,user,_,m = records
    store.save_membership(m | {"status":state})
    with pytest.raises(HTTPException) as exc:
        resolve(store,user,expected_tenant="t")
    assert exc.value.detail["code"] == code


@pytest.mark.parametrize("state", ["draft","suspended_security","closing","closed"])
def test_tenant_state_does_not_gain_draft_owner_bypass(records, state):
    store,user,t,_ = records
    with store._connect() as conn:
        conn.execute("UPDATE tenants SET status=? WHERE id=?", (state,t["id"]))
    with pytest.raises(HTTPException) as exc:
        resolve(store,user,expected_tenant="t")
    assert exc.value.detail["code"] == "TENANT_NOT_ACTIVE"


def test_multi_tenant_requires_selector_and_uses_membership_role(records):
    store,user,_,_ = records
    store.create_tenant({"id":"other","slug":"other","name":"Other"})
    store.save_membership({"id":"m2","user_id":"u","tenant_id":"other","role":"student","capabilities":[]})
    with pytest.raises(HTTPException) as exc:
        resolve(store,user)
    assert exc.value.detail["code"] == "TENANT_MEMBERSHIP_REQUIRED"
    ctx = resolve(store,user,expected_tenant="other")
    assert ctx.role == "student" and ctx.capabilities == () and ctx.membership_id == "m2"
    assert ctx.request_id == ctx.trace_id == "request"
    assert ctx.organization_ids == ()  # global user organization hints are not grants


def test_platform_is_explicit_and_cannot_use_header_without_membership(records):
    store,user,_,_ = records
    user = user | {"role":"admin"}
    assert resolve(store,user).actor_kind == "member"  # role alone is not a platform grant
    from app.services.saas_identity import approve_platform_role
    approve_platform_role(store, user["id"], approval_ref="SYNTHETIC-PLATFORM-APPROVAL", approved_by="offline-fixture")
    assert resolve(store,user).actor_kind == "platform"
    with pytest.raises(HTTPException):
        resolve(store,user,expected_tenant="unknown")
    ctx = resolve(store,user,expected_tenant="t")
    assert ctx.actor_kind == "member" and not ctx.has_capability("backup:global")
