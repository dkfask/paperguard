"""No network/payment: isolated tenant metadata recovery and contract proof."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.api import saas
from app.core.context import RequestContext
from app.db import Store
from app.main import add_request_context
from app.services import ai_recovery


@pytest.fixture
def environment(tmp_path, monkeypatch):
    store = Store(tmp_path / "recovery.db")
    user = store.save_user({"id":"u","username":"test","password_hash":"unused","salt":"unused","role":"teacher","status":"active","display_name":"Test","created_at":"2026-10-04T00:00:00+00:00"})
    for tenant in ["a","b"]:
        store.create_tenant({"id":tenant,"slug":tenant,"name":tenant})
        store.save_membership({"id":"m-"+tenant,"user_id":"u","tenant_id":tenant,"role":"admin","capabilities":["tenant:read","ai:manage"]})
    for id,tenant,uid,mid in [("r-a","a","u","m-a"),("r-b","b","u","m-b"),("r-other","a","other","m-other"),("r-legacy","a","u",None)]:
        store.create_ai_request({"id":id,"request_hash":"hash","user_id":uid,"tenant_id":tenant,"membership_id":mid,"action":"polish","status":"unknown","reservation_status":"unknown","response_text":"private provider text"})
    app = FastAPI()
    app.middleware("http")(add_request_context)
    app.dependency_overrides[auth.get_store] = lambda: store
    app.dependency_overrides[saas.get_store] = lambda: store
    monkeypatch.setattr(store,"get_session_user",lambda *a,**kw:dict(user))
    app.include_router(saas.router)
    return store,user,TestClient(app),app


def headers(tenant="a"):
    return {"Authorization":"Bearer fake", "X-Expected-Tenant":tenant}


def ctx(caps=("tenant:read","ai:manage")):
    return RequestContext(actor_kind="member",actor_id="u",tenant_id="a",membership_id="m-a",auth_version=1,capabilities=caps,request_id="correlation")


def test_typed_envelope_error_correlation_and_openapi(environment):
    _,_,client,app=environment
    good=client.get("/api/saas/v1/context",headers=headers())
    assert good.status_code==200 and good.json()["data"]["context"]["tenant_id"]=="a"
    assert good.json()["meta"]["request_id"]==good.headers["X-Request-ID"]
    bad=client.get("/api/saas/v1/context")
    assert bad.status_code==401 and bad.json()["error"]["code"]=="AUTH_SESSION_INVALID"
    assert bad.json()["error"]["request_id"]==bad.headers["X-Request-ID"]
    spec=app.openapi()
    for path,ops in spec["paths"].items():
        for method,op in ops.items():
            assert op["security"] == [{"bearerAuth":[]}]
            assert "ErrorBody" in str(op["responses"]["422"])
    assert spec["components"]["schemas"]["AiRequestView"]["properties"]["tenant_id"]["readOnly"]


def test_scoped_list_and_direct_id_hide_other_tenant_and_unproven_legacy(environment):
    _,_,client,_=environment
    rows=client.get("/api/saas/v1/ai/requests",headers=headers()).json()["data"]
    assert {r["id"] for r in rows}=={"r-a","r-other"}
    assert "private provider text" not in json.dumps(rows)
    for request in ["r-b","r-legacy","missing"]:
        resp=client.get(f"/api/saas/v1/ai/requests/{request}/recovery-plan",headers=headers())
        assert resp.status_code==404 and resp.json()["error"]["code"]=="AI_REQUEST_NOT_FOUND"
    plan=client.get("/api/saas/v1/ai/requests/r-a/recovery-plan",headers=headers()).json()["data"]
    assert not plan["automatic_retry_allowed"] and not plan["automatic_refund_allowed"]
    assert plan["scope"]=="ai_request_metadata_only"


def test_ordinary_member_only_sees_own_membership_requests(environment):
    store,_,_,_=environment
    assert [r.id for r in ai_recovery.list_requests(ctx(("tenant:read",)),store,limit=100)]==["r-a"]
    for context in [RequestContext(),RequestContext(actor_kind="platform",actor_id="p",capabilities=("backup:global",))]:
        with pytest.raises(Exception) as exc:
            ai_recovery.list_requests(context,store,limit=100)
        assert exc.value.status_code==403


def test_quarantine_is_atomic_idempotent_audited_not_supplier_reconciliation(environment):
    store,_,client,_=environment
    payload={"expected_status":"unknown","reason_code":"unknown_external_result"}
    path="/api/saas/v1/ai/requests/r-a/reconciliation"
    first=client.post(path,headers=headers(),json=payload)
    replay=client.post(path,headers=headers(),json=payload)
    assert first.status_code==replay.status_code==200
    assert first.json()["meta"]["command"]["idempotent_replay"] is False
    assert replay.json()["meta"]["command"]["idempotent_replay"] is True
    row=store.get_scoped_ai_request("r-a",tenant_id="a",user_id=None,membership_id=None)
    assert row["status"]=="reconciliation_required" and row["reservation_status"]=="unknown"
    assert row["response_text"]=="private provider text"
    events=store.list_audit_events()
    assert len(events)==1 and events[0]["organization_id"]==""
    assert json.loads(events[0]["reason"])["tenant_id"]=="a"
    assert "private provider text" not in json.dumps(events)
    assert client.post(path.replace("r-a","r-b"),headers=headers(),json=payload).status_code==404


def test_failed_audit_rolls_back_state(environment, monkeypatch):
    store,_,_,_=environment
    def fail(*a,**kw):
        raise RuntimeError("simulated audit storage failure")
    monkeypatch.setattr(store,"save_audit_event_conn",fail)
    with pytest.raises(RuntimeError):
        ai_recovery.request_reconciliation(ctx(),store,"r-a")
    assert store.get_scoped_ai_request("r-a",tenant_id="a",user_id=None,membership_id=None)["status"]=="unknown"


def test_two_workers_mark_once(environment):
    store,_,_,_=environment
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs=list(pool.map(lambda _:ai_recovery.request_reconciliation(ctx(),store,"r-a"),range(2)))
    assert sorted(replay for _,replay in outputs)==[False,True]
    assert len(store.list_audit_events())==1


@pytest.mark.parametrize("extra",["tenant_id","membership_id","actor_id","capabilities","token"])
def test_command_forbids_forged_identity_fields_without_echo(environment,extra):
    _,_,client,_=environment
    response=client.post("/api/saas/v1/ai/requests/r-a/reconciliation",headers=headers(),json={"expected_status":"unknown","reason_code":"unknown_external_result",extra:"not-to-be-echoed"})
    assert response.status_code==422 and "not-to-be-echoed" not in response.text


def test_non_unknown_not_marked_and_revoked_member_denied(environment):
    store,user,client,_=environment
    store.update_ai_request("r-a",status="completed")
    payload={"expected_status":"unknown","reason_code":"unknown_external_result"}
    assert client.post("/api/saas/v1/ai/requests/r-a/reconciliation",headers=headers(),json=payload).status_code==409
    member=store.get_membership("m-a")
    store.save_membership(member | {"status":"revoked"})
    denied=client.get("/api/saas/v1/ai/requests",headers=headers())
    assert denied.status_code==403 and denied.json()["error"]["code"]=="TENANT_MEMBERSHIP_INACTIVE"
    assert store.list_audit_events()==[]


@pytest.mark.parametrize("change", [{"status":"revoked"},{"capabilities":[]},{"role":"student"}])
def test_mid_request_authorization_change_rejected_in_write_transaction(environment,change):
    store,_,_,_=environment
    stale=ctx()
    member=store.get_membership("m-a")
    store.save_membership(member | change)
    with pytest.raises(Exception) as exc:
        ai_recovery.request_reconciliation(stale,store,"r-a")
    assert exc.value.status_code==403 and store.list_audit_events()==[]
    assert store.get_scoped_ai_request("r-a",tenant_id="a",user_id=None,membership_id=None)["status"]=="unknown"


def test_saas_selector_and_existing_idempotency_headers_pass_cors_preflight():
    from app.main import app
    response=TestClient(app).options("/api/saas/v1/context",headers={
        "Origin":"http://localhost:5173","Access-Control-Request-Method":"GET",
        "Access-Control-Request-Headers":"authorization,x-expected-tenant,idempotency-key",
    })
    assert response.status_code==200
    allowed=response.headers["Access-Control-Allow-Headers"].lower()
    assert "x-expected-tenant" in allowed and "idempotency-key" in allowed
