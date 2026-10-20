"""Behavioral tests for explicit onboarding and one isolated writing slice."""
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import auth
from app.api import saas, projects, teacher, operations
from app.api import auth as auth_api
from app.main import add_request_context
from app.db import Store
from app.models.project import WritingProject
from app.services import saas_identity, tenant_projects
from app.services.tenant_access import resolve_request_context

CAPS=['project:read','project:write','tenant:read']


@pytest.fixture
def pilot(tmp_path,monkeypatch):
    store=Store(tmp_path/'pilot.db')
    for uid,role in [('shared','student'),('operator','user'),('old-admin','admin'),('tenant-admin','teacher'),('new-member','student')]:
        store.save_user({'id':uid,'username':uid,'display_name':uid,'password_hash':'fixture','salt':'fixture','role':role,'status':'active','email_verified':1,'password_set':1,'must_change_password':0,'created_at':'2026-10-04T00:00:00+00:00'})
    for tid in ['t1','t2']:store.create_tenant({'id':tid,'slug':tid,'name':tid})
    for tid,role in [('t1','student'),('t2','teacher')]:store.save_membership({'id':'m-'+tid,'tenant_id':tid,'user_id':'shared','role':role,'capabilities':CAPS})
    store.save_membership({'id':'m-admin','tenant_id':'t1','user_id':'tenant-admin','role':'admin','capabilities':['tenant:read','tenant:manage','membership:manage']})
    saas_identity.approve_platform_role(store,'operator',approval_ref='LOCAL-FIXTURE-APPROVAL',approved_by='offline-reviewer')
    with store._connect() as conn:conn.execute('INSERT INTO organization_units VALUES (?,?,?)',('root',json.dumps({'id':'root','name':'root','type':'school','parent_id':'','status':'active'}),'fixed'))
    probe=FastAPI();probe.middleware('http')(add_request_context)
    for dependency in {auth.get_store,saas.get_store,projects.get_store,teacher.get_store,auth_api.get_store,operations.get_store}:probe.dependency_overrides[dependency]=lambda:store
    tokens={auth._token_hash('fixture-'+uid):uid for uid in ['shared','operator','old-admin','tenant-admin','new-member']}
    monkeypatch.setattr(store,'get_session_user',lambda key,*args,**kwargs:store.get_user(tokens[key]) if key in tokens else None)
    probe.include_router(operations.router);probe.include_router(saas.router);probe.include_router(projects.router);probe.include_router(teacher.router);probe.include_router(auth_api.router)
    return store,TestClient(probe)


def headers(actor='shared',tenant='t1',key=None):
    h={'Authorization':'Bearer fixture-'+actor}
    if tenant is not None:h['X-Expected-Tenant']=tenant
    if key:h['Idempotency-Key']=key
    return h


def platform():return headers('operator',None)


def context(store,actor='shared',tenant='t1'):
    return resolve_request_context(store,store.get_user(actor),request_id='fixture-request',expected_tenant=tenant)


def approval_request():
    return {'slug':'draft-school','name':'Draft','approval':{'approval_ref':'SYNTHETIC-APPROVAL','responsible_user_id':'shared','organization_root_id':'root','outbound_policy':'disabled','pilot_expires_at':'2099-01-01T00:00:00+00:00','exit_contact_user_id':'shared'}}


def new_draft(client):
    r=client.post('/api/saas/v1/tenants',headers=platform()|{'Idempotency-Key':'fixture-create'},json=approval_request())
    assert r.status_code==201,r.text
    return r.json()['data']


def project(client,tenant='t1'):
    r=client.post('/api/saas/v1/projects',headers=headers(tenant=tenant),json={'title':'Local writing fixture','sections':[{'key':'intro','type':'body','title':'Introduction','paragraphs':[{'id':'block','text':'Local synthetic text'}]}]})
    assert r.status_code==201,r.text
    return r.json()['data']


def test_legacy_admin_never_equals_platform_approval(pilot):
    store,client=pilot
    r=client.get('/api/saas/v1/context',headers=headers('old-admin',None))
    assert r.status_code==403
    r=client.get('/api/saas/v1/context',headers=platform())
    assert r.status_code==200 and r.json()['data']['context']['actor_kind']=='platform'
    assert store.list_memberships_for_user('operator')==[]


def test_explicit_platform_binding_preserves_legacy_role_session_and_access(pilot):
    store, client = pilot
    store.save_user(store.get_user('operator') | {'role': 'college_admin'})
    saas_identity.approve_platform_role(store, 'operator', approval_ref='LEGACY-PLATFORM-APPROVAL', approved_by='offline-reviewer')
    session = auth._issue_session(store, store.get_user('operator'))
    assert session['user']['role'] == 'college_admin'
    assert client.get('/api/saas/v1/context', headers=platform()).status_code == 200
    assert client.get('/api/operations/retention', headers=platform()).status_code == 200


def test_platform_can_rediscover_new_draft_without_exposing_it_to_members(pilot):
    store, client = pilot
    draft = new_draft(client)
    response = client.get('/api/saas/v1/tenants', headers=platform())
    assert response.status_code == 200
    assert draft['id'] in {item['id'] for item in response.json()['data']}
    response = client.get('/api/saas/v1/tenants', headers=headers())
    assert draft['id'] not in {item['id'] for item in response.json()['data']}


@pytest.mark.parametrize('actor',['shared','tenant-admin','old-admin'])
def test_nonplatform_cannot_create_tenant_or_assign_owner(pilot,actor):
    store,client=pilot
    for path,body in [('/tenants',approval_request()),('/tenants/t1/first-owner',{'user_id':'shared','expected_version':1}),('/tenants/t1/activate',{'expected_version':1})]:
        r=client.post('/api/saas/v1'+path,headers=headers(actor,key='forged'),json=body)
        assert r.status_code==403,r.text
    assert len(store.list_tenants(status=None))==2


def test_create_draft_requires_approval_and_idempotency(pilot):
    store,client=pilot
    r=client.post('/api/saas/v1/tenants',headers=platform(),json=approval_request());assert r.status_code==422
    r=client.post('/api/saas/v1/tenants',headers=platform()|{'Idempotency-Key':'x'},json={'slug':'x','name':'x'});assert r.status_code==422
    first=new_draft(client);second=new_draft(client)
    assert first==second and first['status']=='draft' and len(store.list_tenants(status=None))==3
    changed=approval_request()|{'name':'Other'}
    assert client.post('/api/saas/v1/tenants',headers=platform()|{'Idempotency-Key':'fixture-create'},json=changed).status_code==409


@pytest.mark.parametrize('change',['unknown_root','inactive_root','unknown_contact','expired','naive_time','enabled_outbound'])
def test_approval_inputs_are_not_server_facts(pilot,change):
    store,client=pilot;req=approval_request()
    if change=='unknown_root':req['approval']['organization_root_id']='forged'
    elif change=='inactive_root':
        with store._connect() as conn:conn.execute('UPDATE organization_units SET data=?',(json.dumps({'type':'school','status':'archived'}),))
    elif change=='unknown_contact':req['approval']['responsible_user_id']='forged'
    elif change=='expired':req['approval']['pilot_expires_at']='2000-01-01T00:00:00+00:00'
    elif change=='naive_time':req['approval']['pilot_expires_at']='2099-01-01T00:00:00'
    else:req['approval']['outbound_policy']='enabled'
    r=client.post('/api/saas/v1/tenants',headers=platform()|{'Idempotency-Key':'fixture-invalid'},json=req)
    assert r.status_code in {409,422} and len(store.list_tenants(status=None))==2


def test_first_owner_exact_replay_and_revocation_never_revives(pilot):
    store,client=pilot;t=new_draft(client);req={'user_id':'shared','expected_version':t['auth_version']};path=f"/api/saas/v1/tenants/{t['id']}/first-owner"
    first=client.post(path,headers=platform(),json=req);second=client.post(path,headers=platform(),json=req)
    assert first.status_code==second.status_code==200 and first.json()['data']==second.json()['data']
    m=first.json()['data'];assert m['role']=='admin' and 'operations:manage' not in m['capabilities']
    assert len(store.list_memberships_for_tenant(t['id']))==1
    assert client.get('/api/saas/v1/context',headers=headers(tenant=t['id'])).status_code==403
    current=store.get_tenant(t['id']);r=client.post(f"/api/saas/v1/tenants/{t['id']}/activate",headers=platform(),json={'expected_version':current['auth_version']})
    assert r.status_code==409 and r.json()['error']['code']=='TENANT_ACTIVATION_BLOCKED' and store.get_tenant(t['id'])['status']=='draft'
    assert r.json()['error']['checks']['owner']=='passed' and len(r.json()['error']['checks'])==7
    store.save_membership(m|{'status':'revoked'})
    assert client.post(path,headers=platform(),json=req).status_code==409
    assert store.get_membership(m['id'])['status']=='revoked'


@pytest.mark.parametrize('case',['unverified','inactive','version','existing_member'])
def test_first_owner_requires_verified_account_and_fresh_version(pilot,case):
    store,client=pilot;t=new_draft(client);req={'user_id':'shared','expected_version':1}
    if case=='unverified':store.save_user(store.get_user('shared')|{'email_verified':0})
    elif case=='inactive':store.update_user_status('shared','disabled')
    elif case=='version':req['expected_version']=999
    else:store.save_membership({'id':'existing','user_id':'shared','tenant_id':t['id'],'role':'student','capabilities':[]})
    r=client.post(f"/api/saas/v1/tenants/{t['id']}/first-owner",headers=platform(),json=req)
    assert r.status_code==409 and store.get_tenant(t['id'])['status']=='draft'


def test_membership_creation_is_exact_replay_not_upsert(pilot):
    store,client=pilot;path='/api/saas/v1/tenants/t1/memberships';body={'user_id':'new-member','role':'student','capabilities':CAPS}
    first=client.post(path,headers=platform(),json=body);second=client.post(path,headers=platform(),json=body)
    assert first.status_code==second.status_code==201 and first.json()['data']==second.json()['data']
    m=first.json()['data']
    assert client.post(path,headers=platform(),json=body|{'role':'admin'}).status_code==409
    store.save_membership(m|{'status':'revoked'})
    assert client.post(path,headers=platform(),json=body).status_code==409
    assert store.get_membership(m['id'])['status']=='revoked'


def test_concurrent_member_creation_once(pilot):
    store,_=pilot;ctx=context(store,'operator',None);req=saas.MembershipCreate(user_id='new-member',role='student',capabilities=CAPS)
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:saas_identity.add_member(store,ctx,'t1',req),range(4)))
    assert len({r[0]['id'] for r in results})==1 and sum(not r[1] for r in results)==1


def test_legacy_mapping_helper_cannot_guess_new_membership(pilot):
    store,_=pilot;u=store.get_user('new-member')|{'organization_context':{'school_id':'unapproved-school'}}
    assert store.ensure_legacy_tenant_membership(u) is None
    assert not store.get_tenant('tenant-legacy-unapproved-school') and not store.list_memberships_for_user(u['id'])


def test_platform_revoked_between_context_and_write_is_denied(pilot):
    store,_=pilot;ctx=context(store,'operator',None)
    with store._connect() as conn:conn.execute("UPDATE platform_role_bindings SET status='revoked',auth_version=auth_version+1 WHERE user_id='operator'")
    with pytest.raises(HTTPException) as exc:saas_identity.create_draft(store,ctx,saas.TenantCreate(**approval_request()),'stale-approval')
    assert exc.value.status_code==403 and len(store.list_tenants(status=None))==2


def test_create_audit_failure_rolls_back(pilot,monkeypatch):
    store,_=pilot;ctx=context(store,'operator',None)
    monkeypatch.setattr(store,'save_audit_event_conn',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('fixture audit failure')))
    with pytest.raises(RuntimeError):saas_identity.create_draft(store,ctx,saas.TenantCreate(**approval_request()),'rollback')
    assert len(store.list_tenants(status=None))==2
    with store._connect() as conn:assert conn.execute('SELECT count(*) FROM saas_provisioning').fetchone()[0]==0


def test_same_global_account_distinct_member_projects(pilot):
    store,client=pilot;a=project(client,'t1');b=project(client,'t2')
    assert a['owner_id']==b['owner_id']=='shared' and a['membership_id']!=b['membership_id']
    for tid,own,foreign in [('t1',a,b),('t2',b,a)]:
        h=headers(tenant=tid);rows=client.get('/api/saas/v1/projects',headers=h).json()['data']
        assert [r['id'] for r in rows]==[own['id']] and 'sections' not in rows[0]
        assert client.get('/api/saas/v1/projects/'+own['id'],headers=h).status_code==200
        r=client.get('/api/saas/v1/projects/'+foreign['id'],headers=h)
        assert r.status_code==404 and 'Local synthetic text' not in r.text
        r=client.patch('/api/saas/v1/projects/'+foreign['id'],headers=h,json={'expected_revision':foreign['revision'],'title':'forged'})
        assert r.status_code==404
    r=client.patch('/api/saas/v1/projects/'+a['id'],headers=headers(),json={'expected_revision':a['revision'],'title':'Updated'})
    assert r.status_code==200 and r.json()['data']['revision']==2
    assert client.patch('/api/saas/v1/projects/'+a['id'],headers=headers(),json={'expected_revision':1,'title':'stale'}).status_code==409


@pytest.mark.parametrize('field',['tenant_id','membership_id','owner_id','organization_context','role','capabilities'])
def test_body_selectors_are_not_authority(pilot,field):
    _,client=pilot
    r=client.post('/api/saas/v1/projects',headers=headers(),json={'title':'spoof',field:'forged'})
    assert r.status_code==422
    assert client.get('/api/saas/v1/projects',headers=headers(tenant='forged')).status_code==403


def test_same_account_member_role_grants_are_not_global(pilot):
    store,client=pilot;a=project(client);b=project(client,'t2');m=store.get_membership('m-t2');store.save_membership(m|{'capabilities':['tenant:read','project:read']})
    assert client.get('/api/saas/v1/projects/'+b['id'],headers=headers(tenant='t2')).status_code==200
    assert client.patch('/api/saas/v1/projects/'+b['id'],headers=headers(tenant='t2'),json={'expected_revision':1,'title':'denied'}).status_code==403
    assert client.patch('/api/saas/v1/projects/'+a['id'],headers=headers(),json={'expected_revision':1,'title':'allowed'}).status_code==200


@pytest.mark.parametrize('change',['revoke','disable_account','disable_tenant','auth_version','capabilities'])
def test_midflight_authorization_change_blocks_project_write(pilot,change):
    store,client=pilot;p=project(client);ctx=context(store)
    if change=='revoke':store.save_membership(store.get_membership('m-t1')|{'status':'revoked'})
    elif change=='disable_account':store.update_user_status('shared','disabled')
    elif change=='disable_tenant':store.create_tenant(store.get_tenant('t1')|{'status':'suspended_security'})
    elif change=='auth_version':store.save_membership(store.get_membership('m-t1')|{'role':'teacher'})
    else:store.save_membership(store.get_membership('m-t1')|{'capabilities':[]})
    with pytest.raises(HTTPException) as exc:tenant_projects.update_project(store,ctx,p['id'],saas.ProjectUpdate(expected_revision=1,title='denied'))
    assert exc.value.status_code==403
    with store._connect() as conn:assert json.loads(conn.execute('SELECT data FROM projects WHERE id=?',(p['id'],)).fetchone()[0])['revision']==1


def test_legacy_repository_cannot_read_replace_update_or_delete_pilot(pilot):
    store,client=pilot;p=project(client);pid=p['id']
    assert store.get_project(pid) is None and store.list_projects()==[] and not store.delete_project(pid)
    forged=WritingProject(id=pid,template_id='',title='legacy overwrite')
    with pytest.raises(PermissionError):store.save_project(forged)
    assert store.update_project_if_revision(forged,1) is None
    assert store.update_project_check_runs(pid,['forged'],1) is None
    with pytest.raises(PermissionError):store.save_project(WritingProject(**p))
    # Unmapped legacy admin cannot see a tenant-bound record through old API.
    assert client.get('/api/projects/'+pid,headers=headers('old-admin',None)).status_code==404
    from app.services.projects import default_template
    template=store.save_template(default_template())
    legacy=store.save_project(WritingProject(id='legacy',template_id=template.id,owner_id='old-admin',title='single-instance'))
    assert store.get_project(legacy.id) and len(store.list_projects())==1
    assert client.get('/api/projects/'+legacy.id,headers=headers('old-admin',None)).status_code==200


@pytest.mark.parametrize('path',['/api/projects','/api/projects/trash','/api/teacher/students'])
def test_registered_members_cannot_fall_back_to_legacy_role_api(pilot,path):
    _,client=pilot;r=client.get(path,headers=headers())
    assert r.status_code==403


@pytest.mark.parametrize('action',['exports','jobs'])
def test_unisolated_async_paths_are_explicitly_blocked(pilot,action):
    store,client=pilot;p=project(client);path=f"/api/saas/v1/projects/{p['id']}/{action}"
    own=client.post(path,headers=headers());foreign=client.post(path,headers=headers(tenant='t2'))
    assert own.status_code==409 and own.json()['error']['code']=='PROJECT_ASYNC_ISOLATION_PENDING'
    assert foreign.status_code==404
    assert store.list_jobs()==[]


def test_project_audit_failure_rolls_back(pilot,monkeypatch):
    store,_=pilot;ctx=context(store)
    monkeypatch.setattr(store,'save_audit_event_conn',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('fixture audit failure')))
    with pytest.raises(RuntimeError):tenant_projects.create_project(store,ctx,saas.ProjectCreate(title='rollback'))
    assert tenant_projects.list_projects(store,ctx)==[]


def test_registration_rejects_tenant_platform_selectors(pilot):
    store,client=pilot
    r=client.post('/api/auth/register',json={'username':'fixture-register','password':'FixturePassword123','tenant_id':'t1','capabilities':['operations:manage']})
    assert r.status_code==422 and not store.get_user_by_username('fixture-register')


def test_legacy_export_service_and_worker_refuse_tenant_snapshots(pilot):
    from app.services.export_jobs import create_export_job, run_project_export
    from app.services.projects import default_template
    from app.db import new_id
    store,client=pilot;p=project(client)
    with pytest.raises(PermissionError):
        create_export_job(store,WritingProject(**p),default_template(),force=False,actor_id='shared')
    assert store.list_jobs()==[]
    eid=new_id('export')
    saved=store.save_workflow_record('project_export',{'id':eid,'status':'queued','project_snapshot':p})
    before=store.get_workflow_record('project_export',eid)
    result=run_project_export(store,eid)
    assert result=={'status':'failed','error_code':'PROJECT_ASYNC_ISOLATION_PENDING'}
    assert store.get_workflow_record('project_export',eid)==before


def test_no_selector_on_two_memberships_never_guesses_tenant(pilot):
    _,client=pilot;r=client.get('/api/saas/v1/projects',headers=headers(tenant=None))
    assert r.status_code==403 and r.json()['error']['code']=='TENANT_MEMBERSHIP_REQUIRED'


def test_platform_bound_user_cannot_forge_member_selector(pilot):
    _,client=pilot;r=client.get('/api/saas/v1/projects',headers=headers('operator','t1'))
    assert r.status_code==403


def test_registration_creates_global_account_only(pilot):
    store,client=pilot;r=client.post('/api/auth/register',json={'username':'global-fixture','password':'FixturePassword123','role':'admin'})
    assert r.status_code==201
    user=store.get_user_by_username('global-fixture')
    assert user['role']=='user' and user['role_application_status']=='unselected' and store.list_memberships_for_user(user['id'])==[]
    assert saas_identity.platform_binding(store,user['id']) is None


def test_shared_mode_rejects_unmapped_legacy_business_path(pilot,monkeypatch):
    _,client=pilot;monkeypatch.setenv('PAPERGUARD_TENANCY_MODE','shared')
    response=client.get('/api/projects',headers=headers('old-admin',None))
    assert response.status_code==403 and response.json()['detail']['code']=='LEGACY_TENANT_PATH_DISABLED'


@pytest.mark.parametrize('field,value',[('status','disabled'),('must_change_password',1)])
def test_ineligible_account_cannot_become_member(pilot,field,value):
    store,client=pilot;store.save_user(store.get_user('new-member')|{field:value})
    r=client.post('/api/saas/v1/tenants/t1/memberships',headers=platform(),json={'user_id':'new-member','role':'student','capabilities':CAPS})
    assert r.status_code==409 and store.list_memberships_for_user('new-member')==[]


@pytest.mark.parametrize('actor',['old-admin','tenant-admin','shared'])
def test_operations_never_trust_global_or_member_admin(pilot,monkeypatch,actor):
    store,client=pilot
    if actor=='tenant-admin':
        store.save_user(store.get_user(actor)|{'role':'admin'})
    calls=[]
    monkeypatch.setattr(operations,'create_backup',lambda *a,**k:calls.append('backup'))
    for method,path,body in [('get','/retention',None),('put','/retention',{}),('post','/backup',None)]:
        r=getattr(client,method)('/api/operations'+path,headers=headers(actor,None),**({'json':body} if body is not None else {}))
        assert r.status_code==403,r.text
    assert calls==[] and store.get_system_setting('retention_policy') is None


def test_operations_platform_approval_is_current_and_audit_atomic(pilot,monkeypatch):
    store,client=pilot
    assert client.get('/api/operations/retention',headers=platform()).status_code==200
    original=operations.check_platform
    calls=[]
    def revoke_after_dependency(conn,ctx):
        original(conn,ctx)
        calls.append(1)
        if len(calls)==1:
            conn.execute("UPDATE platform_role_bindings SET status='revoked' WHERE user_id='operator'")
    monkeypatch.setattr(operations,'check_platform',revoke_after_dependency)
    body={k:30 for k in ['draft_days','attachment_days','submission_days','archive_days','audit_days','local_cache_days']}
    r=client.put('/api/operations/retention',headers=platform(),json=body)
    assert r.status_code==403 and store.get_system_setting('retention_policy') is None


def test_operations_retention_rolls_back_on_audit_failure(pilot,monkeypatch):
    store,client=pilot
    monkeypatch.setattr(store,'save_audit_event_conn',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('fixture audit failure')))
    body=operations.RetentionPolicyUpdate(**{k:30 for k in ['draft_days','attachment_days','submission_days','archive_days','audit_days','local_cache_days']})
    with pytest.raises(RuntimeError):operations.set_retention(body,store,context(store,'operator',None))
    assert store.get_system_setting('retention_policy') is None


def test_backup_discarded_on_platform_revocation(pilot,monkeypatch,tmp_path):
    store,client=pilot;outputs=[]
    def synthetic_backup(output,**kwargs):
        outputs.append(output);output.write_bytes(b'synthetic-backup')
        with store._connect() as conn:
            conn.execute("UPDATE platform_role_bindings SET status='revoked' WHERE user_id='operator'")
        return {'format_version':1}
    monkeypatch.setattr(operations,'create_backup',synthetic_backup)
    r=client.post('/api/operations/backup',headers=platform())
    assert r.status_code==403 and outputs and not outputs[0].exists()


def test_legacy_audit_cannot_project_saas_or_platform_events(pilot):
    from app.services.audit_service import list_audit_events
    store,client=pilot;project(client)
    store.save_audit_event({'actor_id':'old-admin','action':'fixture.legacy','resource_type':'legacy','resource_id':'legacy-id'})
    events=list_audit_events(store,store.get_user('old-admin'))
    assert any(e['action']=='fixture.legacy' for e in events)
    assert all(e['resource_type'] not in {'saas','platform_role_binding','operations'} for e in events)


def test_missing_member_account_returns_not_found(pilot):
    store,client=pilot
    r=client.post('/api/saas/v1/tenants/t1/memberships',headers=platform(),json={'user_id':'missing','role':'student','capabilities':CAPS})
    assert r.status_code==404 and r.json()['error']['code']=='USER_NOT_FOUND'


@pytest.mark.parametrize('field,value',[('image',{'url':'/media/uploads/other-tenant.png'}),('table',{'source_xml':'synthetic-untrusted-xml'}),('equation',{'omml':'synthetic-untrusted-omml'})])
@pytest.mark.parametrize('operation',['create','update'])
def test_manual_pilot_rejects_unisolated_assets_or_imported_blocks(pilot,field,value,operation):
    store,client=pilot
    sections=[{'key':'intro','type':'body','title':'Introduction','paragraphs':[{'id':'block','text':'manual',field:value}]}]
    # Even a nominal paragraph cannot smuggle a secondary asset/import field.
    if operation=='create':
        r=client.post('/api/saas/v1/projects',headers=headers(),json={'sections':sections})
        assert store.list_projects()==[]
    else:
        p=project(client);path='/api/saas/v1/projects/'+p['id']
        hidden=client.patch(path,headers=headers(tenant='t2'),json={'expected_revision':1,'sections':sections})
        assert hidden.status_code==404
        r=client.patch(path,headers=headers(),json={'expected_revision':1,'sections':sections})
        assert client.get(path,headers=headers()).json()['data']==p
    assert r.status_code==409 and r.json()['error']['code']=='PROJECT_ASYNC_ISOLATION_PENDING'
