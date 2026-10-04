"""Ledger safety regressions: canonical scope replay and unknown failures."""
import socket

import pytest
from fastapi import HTTPException

from app.api import ai
from app.db import Store


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))])
    monkeypatch.setattr(ai.ai_governance,"begin_call",lambda *a:None)
    monkeypatch.setattr(ai.ai_governance,"record_usage",lambda *a,**kw:None)
    store=Store(tmp_path/"ledger.db")
    user=store.save_user({"id":"u","username":"u","password_hash":"unused","salt":"unused","display_name":"Test","created_at":"2026-10-04T00:00:00+00:00","status":"active","role":"teacher"})
    for tid in ["a","b"]:
        store.create_tenant({"id":tid,"slug":tid,"name":tid})
        store.save_membership({"id":"m-"+tid,"tenant_id":tid,"user_id":"u","role":"teacher","capabilities":["tenant:read"]})
    cfg={"base_url":"https://provider.example/v1","api_key":"test-only","model":"m"}
    calls=[]
    monkeypatch.setattr(ai,"_chat_completion",lambda *a,**kw:calls.append(1) or "response")
    return store,user,cfg,calls


def call(scenario,tenant="a",messages=None,**kwargs):
    store,user,cfg,_=scenario
    return ai._metered_chat(store,user|{"tenant_id":tenant},cfg,messages or [{"role":"user","content":"same"}],kwargs.pop("temperature",0.2),action="polish",source="user",idempotency_key="key",**kwargs)


def test_replay_is_canonical_but_binds_tenant_membership_and_settings(scenario):
    assert call(scenario)=="response"
    assert call(scenario,messages=[{"content":"same","role":"user"}])=="response"
    for options in [{"tenant":"b"},{"temperature":0.8},{"max_tokens":100}]:
        with pytest.raises(HTTPException) as exc:
            call(scenario,**options)
        assert exc.value.status_code==409
    assert scenario[3]==[1]


def test_invalid_tenant_is_denied_before_dispatch(scenario):
    with pytest.raises(HTTPException) as exc:
        call(scenario,tenant="guessed")
    assert exc.value.status_code==403 and not scenario[3]


def test_legacy_school_hint_is_not_persisted_as_a_proven_tenant(scenario):
    store,user,cfg,_=scenario
    ai._metered_chat(store,{"id":"legacy","organization_context":{"school_id":"a"}},cfg,[],0.2,action="test",source="user",idempotency_key="key")
    row=store.get_ai_request_by_idempotency("legacy","key")
    assert row["tenant_id"] is None and row["membership_id"] is None


@pytest.mark.parametrize("failure",[RuntimeError("private failure"),HTTPException(504,"timeout")])
def test_unexpected_provider_failure_keeps_unknown_and_never_redispatches(scenario,monkeypatch,failure):
    def fail(*a,**kw):
        scenario[3].append(1)
        raise failure
    monkeypatch.setattr(ai,"_chat_completion",fail)
    with pytest.raises(HTTPException):
        call(scenario)
    row=scenario[0].get_ai_request_by_idempotency("u","key")
    assert row["status"]==row["reservation_status"]=="unknown"
    with pytest.raises(HTTPException) as exc:
        call(scenario)
    assert exc.value.detail["code"]=="AI_RECONCILIATION_REQUIRED" and scenario[3]==[1]


def test_record_usage_failure_after_provider_success_is_not_stranded_dispatching(scenario,monkeypatch):
    def fail(*a,**kw):
        raise RuntimeError("usage unavailable")
    monkeypatch.setattr(ai.ai_governance,"record_usage",fail)
    with pytest.raises(HTTPException):
        call(scenario)
    assert scenario[0].get_ai_request_by_idempotency("u","key")["status"]=="unknown"
