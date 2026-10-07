"""Local synthetic ownership inputs. No source database or production mapping."""
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "docs/tenancy/tools"
sys.path.insert(0,str(TOOLS))
preflight_module = importlib.import_module("ownership_preflight")


@pytest.fixture
def source(tmp_path):
    db=tmp_path/'source.db';mapping=tmp_path/'map.json'
    with sqlite3.connect(db) as conn:
        conn.executescript('''CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT,email TEXT,role TEXT,status TEXT,organization_context TEXT);
        CREATE TABLE organization_units(id TEXT PRIMARY KEY,data TEXT,updated_at TEXT);
        CREATE TABLE projects(id TEXT PRIMARY KEY,data TEXT,updated_at TEXT);
        CREATE TABLE tenants(id TEXT PRIMARY KEY,status TEXT);
        CREATE TABLE tenant_memberships(id TEXT PRIMARY KEY,user_id TEXT,tenant_id TEXT,status TEXT);
        CREATE TABLE schema_migrations(version TEXT PRIMARY KEY);''')
        conn.execute("INSERT INTO schema_migrations VALUES ('fixture_v1')")
        for sid in ['s1','s2']:
            conn.execute('INSERT INTO organization_units VALUES (?,?,?)',(sid,json.dumps({'type':'school','parent_id':None,'status':'active'}),'fixed'))
        conn.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',('u1','identity_fixture','private@example.invalid','student','active',json.dumps({'school_id':'s1'})))
        for tid in ['t1','t2']:conn.execute('INSERT INTO tenants VALUES (?,?)',(tid,'active'))
        conn.execute('INSERT INTO tenant_memberships VALUES (?,?,?,?)',('m1','u1','t1','active'))
        conn.execute('INSERT INTO projects VALUES (?,?,?)',('p1',json.dumps({'owner_id':'u1','organization_context':{'school_id':'s1'},'title':'private manuscript'}),'fixed'))
    mapping.write_text(json.dumps({'_status':'CONFIRMED','approval_ref':'SYNTHETIC-APPROVAL','tenant_map':{'s1':'t1','s2':'t2'}}))
    return db,mapping


def edit(db,sql,args=()):
    with sqlite3.connect(db) as conn:conn.execute(sql,args)


def test_repeatable_side_effect_free_redacted_admission(source):
    db,mapping=source;before=preflight_module.snapshot(db);map_bytes=mapping.read_bytes()
    a=preflight_module.preflight(db,mapping);b=preflight_module.preflight(db,mapping)
    assert a==b and a['decision']=='GO_REHEARSAL' and not a['production_authorized']
    assert preflight_module.snapshot(db)==before and mapping.read_bytes()==map_bytes
    assert not Path(str(db)+'-wal').exists() and not Path(str(db)+'-shm').exists()
    text=json.dumps(a)
    for private in ['identity_fixture','private@example.invalid','private manuscript','"u1"','"p1"']:assert private not in text
    assert preflight_module.verify_admission(a,db,mapping)


@pytest.mark.parametrize('change',['mapping','source','rules','schema','source_sha','forged_go'])
def test_stale_or_forged_admission_never_authorizes(source,change,monkeypatch):
    db,mapping=source;r=preflight_module.preflight(db,mapping)
    if change=='mapping':
        value=json.loads(mapping.read_text());value['approval_ref']='CHANGED';mapping.write_text(json.dumps(value))
    elif change=='source':edit(db,"UPDATE projects SET updated_at='changed'")
    elif change=='rules':monkeypatch.setattr(preflight_module,'VERSION','mutated-rule-version')
    elif change=='schema':edit(db,'CREATE TABLE extra_table(id TEXT)')
    elif change=='source_sha':r['binding']['source_sha']='0'*40
    else:r['counts']['projects']=999
    assert not preflight_module.verify_admission(r,db,mapping)


def test_same_account_multitenant_is_not_identity_conflict(source):
    db,mapping=source;edit(db,'INSERT INTO tenant_memberships VALUES (?,?,?,?)',('m2','u1','t2','active'))
    r=preflight_module.preflight(db,mapping)
    assert r['decision']=='GO_REHEARSAL' and not any(f['code']=='ACCOUNT_IDENTITY_COLLISION' for f in r['findings'])
    edit(db,'UPDATE projects SET data=?',(json.dumps({'owner_id':'u1'}),))
    r=preflight_module.preflight(db,mapping)
    assert r['decision']=='BLOCKED' and any(f['code']=='RESOURCE_OWNERSHIP_AMBIGUOUS' and f['candidate_count']==2 for f in r['findings'])


@pytest.mark.parametrize('case,code',[
    ('missing_owner','RESOURCE_OWNERSHIP_MISSING_OR_CONFLICT'),('organization_disabled','ORGANIZATION_UNRESOLVED_OR_INACTIVE'),
    ('account_conflict','ACCOUNT_IDENTITY_COLLISION'),('legacy_admin','LEGACY_ADMIN_APPROVAL_REQUIRED'),
    ('mapping_pending','MAPPING_APPROVAL_REQUIRED'),('organization_conflict','ACCOUNT_ORGANIZATION_CONFLICT'),
    ('tenant_missing','MEMBERSHIP_TENANT_INVALID'),('unknown_resource','UNREGISTERED_NONEMPTY_RESOURCE')])
def test_uncertain_ownership_fails_closed(source,case,code):
    db,mapping=source
    if case=='missing_owner':edit(db,'UPDATE projects SET data=?',(json.dumps({'owner_id':'not-known'}),))
    elif case=='organization_disabled':edit(db,'UPDATE organization_units SET data=? WHERE id=?',(json.dumps({'type':'school','status':'archived'}),'s1'))
    elif case=='account_conflict':edit(db,'INSERT INTO users SELECT ?,username,email,role,status,organization_context FROM users',('another-user',))
    elif case=='legacy_admin':edit(db,"UPDATE users SET role='admin'")
    elif case=='mapping_pending':
        v=json.loads(mapping.read_text());v['_status']='PENDING_HUMAN_CONFIRMATION';mapping.write_text(json.dumps(v))
    elif case=='organization_conflict':edit(db,'UPDATE users SET organization_context=?',(json.dumps({'school_id':'s1','college_id':'s2'}),))
    elif case=='tenant_missing':edit(db,"UPDATE tenants SET status='closed' WHERE id='t1'")
    else:
        edit(db,'CREATE TABLE surprise(id TEXT)');edit(db,"INSERT INTO surprise VALUES ('must-review')")
    r=preflight_module.preflight(db,mapping)
    assert r['decision']=='BLOCKED' and any(f['code']==code for f in r['findings'])
    assert not preflight_module.verify_admission(r,db,mapping)


def test_apply_without_admission_has_no_output(source,tmp_path):
    db,mapping=source;dst=tmp_path/'no.db';before=preflight_module.snapshot(db)
    run=subprocess.run([sys.executable,str(TOOLS/'backfill_migrate.py'),'--source',str(db),'--tenant-map',str(mapping),'--output',str(dst),'--apply'],capture_output=True,text=True)
    assert run.returncode==1 and not dst.exists() and preflight_module.snapshot(db)==before


def test_source_with_wal_requires_offline_snapshot(source):
    db,mapping=source;wal=Path(str(db)+'-wal');wal.write_bytes(b'not a consistent offline snapshot')
    with pytest.raises(ValueError,match='OFFLINE_SNAPSHOT'):preflight_module.preflight(db,mapping)


def test_output_cannot_overwrite_source_or_mapping(source):
    db,mapping=source;before=preflight_module.snapshot(db);mapping_bytes=mapping.read_bytes()
    for path in [db,mapping]:
        r=subprocess.run([sys.executable,str(TOOLS/'ownership_preflight.py'),'--source',str(db),'--tenant-map',str(mapping),'--output',str(path)],capture_output=True,text=True)
        assert r.returncode==2
    assert preflight_module.snapshot(db)==before and mapping.read_bytes()==mapping_bytes


def test_hardlinked_output_cannot_overwrite_source(source,tmp_path):
    db,mapping=source;alias=tmp_path/'alias.db';alias.hardlink_to(db);before=preflight_module.snapshot(db)
    r=subprocess.run([sys.executable,str(TOOLS/'ownership_preflight.py'),'--source',str(db),'--tenant-map',str(mapping),'--output',str(alias)],capture_output=True,text=True)
    assert r.returncode==2 and preflight_module.snapshot(db)==before


@pytest.mark.parametrize('field,value,code', [('role','unreviewed-role','ACCOUNT_ROLE_MIGRATION_UNDEFINED'),('status','unhandled-state','ACCOUNT_STATE_MIGRATION_UNDEFINED')])
def test_no_implicit_role_or_status_migration(source,field,value,code):
    db,mapping=source
    if field=='role':edit(db,'DELETE FROM tenant_memberships')
    edit(db,'UPDATE users SET '+field+'=?',(value,))
    r=preflight_module.preflight(db,mapping)
    assert r['decision']=='BLOCKED' and any(f['code']==code for f in r['findings'])


@pytest.mark.parametrize('name',['unreviewed table','private"table'])
def test_unregistered_nonempty_quoted_tables_fail_closed_without_name_leak(source,name):
    db,mapping=source
    identifier='"'+name.replace('"','""')+'"'
    edit(db,'CREATE TABLE '+identifier+' (id TEXT)')
    edit(db,'INSERT INTO '+identifier+' VALUES (?)',('synthetic-review-required',))
    before=preflight_module.snapshot(db)
    report=preflight_module.preflight(db,mapping)
    assert report['decision']=='BLOCKED'
    assert any(f['code']=='UNREGISTERED_NONEMPTY_RESOURCE' for f in report['findings'])
    assert name not in json.dumps(report) and 'synthetic-review-required' not in json.dumps(report)
    assert not preflight_module.verify_admission(report,db,mapping)
    assert preflight_module.snapshot(db)==before
