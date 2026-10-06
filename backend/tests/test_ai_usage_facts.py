"""Mock-only provider usage + atomic tenant fact storage; no supplier network."""
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.api import ai, saas
from app.db import Store
from app.services import ai_usage_facts as facts


@pytest.mark.parametrize('protocol,raw,expected',[
    ('openai',{'prompt_tokens':12,'completion_tokens':3,'total_tokens':15},(12,3,15,None,None)),
    ('openai',{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},(0,0,0,None,None)),
    ('openai',{'prompt_tokens':12,'completion_tokens':3,'prompt_tokens_details':{'cached_tokens':8}},(12,3,None,8,None)),
    ('anthropic',{'input_tokens':12,'output_tokens':3,'cache_read_input_tokens':10,'cache_creation_input_tokens':8},(12,3,None,10,8)),
])
def test_explicit_provider_numbers_preserved_not_estimated(protocol,raw,expected):
    usage=facts.provider_usage(protocol,{'id':'synthetic-provider-ref','usage':raw,'secret':'never copy this'})
    assert usage.usage_quality=='provider_reported'
    assert (usage.input_tokens,usage.output_tokens,usage.total_tokens,usage.cache_read_tokens,usage.cache_creation_tokens)==expected
    assert usage.provider_request_ref and len(usage.provider_request_ref)==64
    assert 'synthetic-provider-ref' not in repr(usage) and 'never copy this' not in repr(usage)


@pytest.mark.parametrize('raw',[
    None,{},[],{'prompt_tokens':1},{'prompt_tokens':True,'completion_tokens':1},
    {'prompt_tokens':'4','completion_tokens':1},{'prompt_tokens':1.5,'completion_tokens':1},
    {'prompt_tokens':-1,'completion_tokens':1},{'prompt_tokens':2**63,'completion_tokens':1},
    {'prompt_tokens':2,'completion_tokens':3,'total_tokens':1},
    {'prompt_tokens':2,'completion_tokens':3,'total_tokens':None},
    {'prompt_tokens':2,'completion_tokens':3,'prompt_tokens_details':[]},
    {'prompt_tokens':2,'completion_tokens':3,'prompt_tokens_details':{'cached_tokens':3}},
])
def test_partial_invalid_or_missing_usage_is_nullable_unavailable(raw):
    usage=facts.provider_usage('openai',{'usage':raw,'id':'synthetic-id'})
    assert usage.usage_quality=='unavailable'
    assert usage.input_tokens is usage.output_tokens is usage.total_tokens is None


@pytest.mark.parametrize('field,value',[('input_tokens',True),('output_tokens',None),('cache_read_input_tokens',-1),('cache_creation_input_tokens','3')])
def test_anthropic_invalid_cache_or_token_is_not_provider_reported(field,value):
    raw={'input_tokens':2,'output_tokens':3,field:value}
    assert facts.provider_usage('anthropic',{'usage':raw}).usage_quality=='unavailable'


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    store=Store(tmp_path/'usage.db')
    user=store.save_user({'id':'u','username':'usage-fixture','role':'student','status':'active','must_change_password':0,'password_hash':'fixture','salt':'fixture','display_name':'fixture','created_at':'2026-10-05T00:00:00+00:00'})
    for tid in ['a','b']:
        store.create_tenant({'id':tid,'slug':tid,'name':tid})
        store.save_membership({'id':'m-'+tid,'user_id':'u','tenant_id':tid,'role':'student','capabilities':['tenant:read']})
    user=user|{'tenant_id':'a'}
    cfg={'base_url':'https://provider.invalid/v1','api_key':'synthetic-only','model':'synthetic-model'}
    monkeypatch.setattr(ai,'assert_safe_outbound_url',lambda value,**kwargs:value)
    monkeypatch.setattr(ai.ai_governance,'begin_call',lambda *args:None)
    monkeypatch.setattr(ai.ai_governance,'record_usage',lambda *args,**kwargs:None)
    return store,user,cfg


def call(scenario,key='fixture'):
    store,user,cfg=scenario
    return ai._metered_chat(store,user,cfg,[{'role':'user','content':'synthetic-input'}],0.2,action='polish',source='user',idempotency_key=key)


def provider_mock(monkeypatch,protocol,usage,*,on_response=None,timeout=False):
    original=httpx.Client;requests=[]
    def handler(request):
        requests.append(request)
        if timeout:raise httpx.ReadTimeout('synthetic timeout',request=request)
        if on_response:on_response()
        data={'id':'synthetic-response-id','usage':usage,'extra_private':'never copy this'}
        if protocol=='openai':data['choices']=[{'message':{'content':'synthetic output'}}]
        else:data['content']=[{'type':'text','text':'synthetic output'}]
        return httpx.Response(200,json=data)
    # ★ 生产代码现在显式传 `transport=GuardedTransport()`（出站守卫）。
    #   **无条件覆盖**它，而不是 `setdefault`：
    #   若沿用调用方的 `GuardedTransport`，请求会真的去连 `provider.invalid`
    #   → DNS 解析失败 → `OutboundBlocked(400)`
    #   ⇒ 被测的不是「上游超时如何记账」，而是「域名解析不了」，
    #   而 `len(requests)==1` 这类断言**照样可能通过**——
    #   **测试看起来在跑，实际测的是另一件事**。
    #   ⚠ 反过来也不能用 `setdefault`：调用方已给 transport 时它不生效，
    #   而 httpx.Client 收到重复 transport 关键字会直接 TypeError，
    #   同样让 `requests` 恒空 ⇒ `len(requests)==1` 变成零证明力的假信号。
    def _client(**kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return original(**kwargs)
    monkeypatch.setattr(ai.httpx, 'Client', _client)
    return requests


@pytest.mark.parametrize('protocol',['openai','anthropic'])
def test_mock_transport_retains_usage_across_actual_adapter_meter_and_replay(scenario,monkeypatch,protocol):
    store,user,cfg=scenario
    if protocol=='anthropic':cfg['base_url']='https://provider.invalid/anthropic'
    raw={'prompt_tokens':19,'completion_tokens':7} if protocol=='openai' else {'input_tokens':19,'output_tokens':7}
    requests=provider_mock(monkeypatch,protocol,raw)
    result=call(scenario)
    assert result=='synthetic output' and isinstance(result,str)
    row=store.get_ai_request_by_idempotency('u','fixture');fact=store.get_workflow_record(facts.KIND,row['id'])
    assert (fact['tenant_id'],fact['membership_id'],fact['input_tokens'],fact['output_tokens'])==('a','m-a',19,7)
    assert fact['usage_quality']=='provider_reported' and not fact['billable'] and not fact['metering_review_required']
    assert row['status']=='completed' and row['input_tokens']!=fact['input_tokens']
    assert call(scenario)==result and len(requests)==1
    assert 'never copy this' not in json.dumps(fact) and 'synthetic-input' not in json.dumps(fact)


def test_success_without_usage_is_completed_but_requires_metering_review(scenario,monkeypatch):
    store,_,_=scenario;requests=provider_mock(monkeypatch,'openai',None)
    call(scenario);row=store.get_ai_request_by_idempotency('u','fixture')
    fact=store.get_workflow_record(facts.KIND,row['id'])
    assert row['status']=='completed' and fact['usage_quality']=='unavailable'
    assert fact['input_tokens'] is fact['output_tokens'] is None and fact['metering_review_required']
    assert call(scenario)=='synthetic output' and len(requests)==1


@pytest.mark.parametrize('failure',['audit','fact','revoke'])
def test_provider_success_local_failure_becomes_unknown_without_duplicate_dispatch(scenario,monkeypatch,failure):
    store,_,_=scenario
    def mutate():
        if failure=='audit':
            monkeypatch.setattr(store,'save_audit_event_conn',lambda *args:(_ for _ in ()).throw(RuntimeError('synthetic audit failure')))
        elif failure=='revoke':
            with store._connect() as conn:conn.execute("UPDATE tenant_memberships SET status='revoked',auth_version=auth_version+1 WHERE id='m-a'")
        else:
            row=store.get_ai_request_by_idempotency('u','fixture')
            store.save_workflow_record(facts.KIND,{'id':row['id'],'synthetic':'existing immutable sentinel'})
    requests=provider_mock(monkeypatch,'openai',{'prompt_tokens':19,'completion_tokens':7},on_response=mutate)
    with pytest.raises(HTTPException):call(scenario)
    row=store.get_ai_request_by_idempotency('u','fixture')
    assert row['status']==row['reservation_status']=='unknown'
    assert not [e for e in store.list_audit_events() if e['action']=='ai.usage.recorded']
    if failure!='fact':assert store.get_workflow_record(facts.KIND,row['id']) is None
    with pytest.raises(HTTPException):call(scenario)
    assert len(requests)==1


def test_provider_timeout_does_not_create_or_invent_token_fact(scenario,monkeypatch):
    store,_,_=scenario;requests=provider_mock(monkeypatch,'openai',None,timeout=True)
    with pytest.raises(HTTPException):call(scenario)
    row=store.get_ai_request_by_idempotency('u','fixture')
    assert row['status']=='unknown' and store.get_workflow_record(facts.KIND,row['id']) is None
    with pytest.raises(HTTPException):call(scenario)
    assert len(requests)==1


def test_scope_bound_metadata_projection_hides_other_tenant_and_raw_payload(scenario,monkeypatch):
    store,user,cfg=scenario
    provider_mock(monkeypatch,'openai',{'prompt_tokens':19,'completion_tokens':7});call(scenario)
    probe=FastAPI();probe.include_router(saas.router)
    probe.dependency_overrides[auth.get_store]=lambda:store;probe.dependency_overrides[saas.get_store]=lambda:store
    monkeypatch.setattr(store,'get_session_user',lambda *args,**kwargs:store.get_user('u'))
    client=TestClient(probe);header={'Authorization':'Bearer synthetic','X-Expected-Tenant':'a'}
    response=client.get('/api/saas/v1/ai/requests',headers=header)
    assert response.status_code==200,response.text
    metadata=response.json()['data'][0]
    assert metadata['usage_quality']=='provider_reported' and metadata['provider_input_tokens']==19
    assert 'synthetic output' not in response.text and 'synthetic-input' not in response.text
    hidden=client.get('/api/saas/v1/ai/requests',headers=header|{'X-Expected-Tenant':'b'})
    assert hidden.json()['data']==[]
    row=store.get_ai_request_by_idempotency('u','fixture')
    fact=store.get_workflow_record(facts.KIND,row['id']);store.save_workflow_record(facts.KIND,fact|{'tenant_id':'b'})
    fields=facts.view_fields(store,row)
    assert fields['provider_input_tokens'] is None and fields['usage_quality']=='legacy_chars'


def test_string_only_wrapper_cannot_claim_trusted_measurement(scenario,monkeypatch):
    store,_,_=scenario;monkeypatch.setattr(ai,'_chat_completion',lambda *args,**kwargs:'synthetic output')
    call(scenario);row=store.get_ai_request_by_idempotency('u','fixture')
    assert facts.view_fields(store,row)['usage_quality']=='unavailable'


@pytest.mark.parametrize('mutation',[{'status':'disabled'},{'must_change_password':1}])
def test_account_change_after_dispatch_refuses_fact_settlement(scenario,monkeypatch,mutation):
    store,user,_=scenario
    requests=provider_mock(monkeypatch,'openai',{'prompt_tokens':19,'completion_tokens':7},on_response=lambda:store.save_user(store.get_user('u')|mutation))
    with pytest.raises(HTTPException):call(scenario)
    row=store.get_ai_request_by_idempotency('u','fixture')
    assert row['status']=='unknown' and store.get_workflow_record(facts.KIND,row['id']) is None
    assert len(requests)==1


def test_absent_legacy_user_cannot_become_a_usage_fact_owner(scenario,monkeypatch):
    store,user,cfg=scenario;requests=provider_mock(monkeypatch,'openai',{'prompt_tokens':19,'completion_tokens':7})
    with pytest.raises(HTTPException):call((store,{'id':'absent'},cfg))
    row=store.get_ai_request_by_idempotency('absent','fixture')
    assert row['status']=='unknown' and store.get_workflow_record(facts.KIND,row['id']) is None
    assert len(requests)==1


def test_same_key_other_tenant_isolated_and_dispatches_once_per_domain(scenario,monkeypatch):
    store,user,cfg=scenario;requests=provider_mock(monkeypatch,'openai',{'prompt_tokens':19,'completion_tokens':7})
    assert call(scenario)=="synthetic output"
    assert call((store,user|{'tenant_id':'b'},cfg))=="synthetic output"
    assert len(requests)==2
    rows=store.list_ai_requests()
    assert {(row['tenant_id'], row['membership_id'], row['action'], row['idempotency_domain']) for row in rows} == {
        ('a','m-a','polish','tenant'), ('b','m-b','polish','tenant')
    }
    assert all(store.get_workflow_record(facts.KIND,row['id'])['billable'] is False for row in rows)


def test_old_completed_rows_keep_legacy_estimates_not_invented_provider_tokens(scenario):
    store,user,_=scenario
    row=store.create_ai_request({'id':'old','request_hash':'synthetic','user_id':'u','tenant_id':'a','membership_id':'m-a','action':'polish','status':'completed','input_tokens':99,'output_tokens':55})
    fields=facts.view_fields(store,row)
    assert fields['usage_quality']=='legacy_chars' and fields['provider_input_tokens'] is fields['provider_output_tokens'] is None
    assert fields['metering_review_required'] and row['input_tokens']==99


def test_actual_assist_http_cannot_inject_provider_usage_or_bypass_meter(scenario,monkeypatch):
    store,user,cfg=scenario
    requests=provider_mock(monkeypatch,'openai',{'prompt_tokens':19,'completion_tokens':7})
    probe=FastAPI();probe.include_router(ai.router)
    probe.dependency_overrides[auth.get_store]=lambda:store;probe.dependency_overrides[ai.get_store]=lambda:store
    monkeypatch.setattr(store,'get_session_user',lambda *args,**kwargs:user)
    monkeypatch.setattr(ai,'_resolve_cfg',lambda *args:(cfg,'user'))
    response=TestClient(probe).post('/api/ai/assist',headers={'Authorization':'Bearer synthetic','Idempotency-Key':'http-fixture'},json={'action':'polish','text':'synthetic input','usage':{'prompt_tokens':999},'usage_quality':'provider_reported'})
    assert response.status_code==200,response.text
    assert response.json()['text']=='synthetic output'
    rows=store.list_ai_requests(tenant_id='a');assert len(rows)==1
    fact=store.get_workflow_record(facts.KIND,rows[0]['id'])
    assert fact['input_tokens']==19 and fact['output_tokens']==7 and not fact['billable']
    assert len(requests)==1
