"""PR34 merge blockers; temporary databases and synthetic providers only."""
import json
import ipaddress
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.api import ai, teacher, workflow
from app.db import Store
from app.services import ai_governance, backup_restore, outbound_guard, secret_store


@pytest.fixture
def scope(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db' / 'fixture.db')
    user = store.save_user({'id': 'user', 'username': 'synthetic-user', 'role': 'teacher',
        'status': 'active', 'must_change_password': 0, 'password_hash': 'fixture',
        'salt': 'fixture', 'display_name': 'Fixture', 'created_at': '2026-10-06T00:00:00+00:00'})
    for tid in ['a', 'b']:
        store.create_tenant({'id': tid, 'slug': tid, 'name': tid})
        store.save_membership({'id': 'm-' + tid, 'tenant_id': tid, 'user_id': 'user',
            'role': 'teacher', 'capabilities': ['tenant:read']})
    monkeypatch.setenv('PAPERGUARD_SECRET_KEY', '0123456789abcdef' * 4)
    monkeypatch.setattr(store, 'get_session_user', lambda *a, **kw: store.get_user('user'))
    probe = FastAPI()
    probe.dependency_overrides[auth.get_store] = lambda: store
    probe.dependency_overrides[ai.get_store] = lambda: store
    probe.include_router(ai.router)
    cfg = {'base_url': 'https://provider.invalid/v1', 'model': 'synthetic', 'api_key': 'synthetic-only'}
    return store, user, TestClient(probe), cfg


def test_native_byok_never_inherits_other_tenant_or_legacy_key(scope):
    store, user, client, cfg = scope
    store.set_user_ai_config('user', cfg | {'model': 'legacy'})
    store.set_user_ai_config('user', cfg, tenant_id='a', membership_id='m-a')
    assert store.get_user_ai_config('user', tenant_id='a')['model'] == 'synthetic'
    assert store.get_user_ai_config('user', tenant_id='b') is None
    raw = store.list_workflow_records('ai_member_config')[0]
    assert secret_store.is_encrypted(raw['config']['api_key'])
    store.set_user_ai_config('user', None, tenant_id='b')
    assert store.get_user_ai_config('user', tenant_id='a') == cfg
    with pytest.raises(PermissionError):
        store.get_user_ai_config('user', tenant_id='b', membership_id='m-a')


def test_member_config_audit_failure_rolls_back(scope, monkeypatch):
    store, user, client, cfg = scope
    monkeypatch.setattr(store, 'save_audit_event_conn', lambda *args: (_ for _ in ()).throw(RuntimeError('fixture')))
    with pytest.raises(RuntimeError):
        store.set_user_ai_config('user', cfg, tenant_id='a')
    assert store.get_user_ai_config('user', tenant_id='a') is None


def test_ai_tenant_header_is_selector_not_authorization(scope):
    store, user, client, cfg = scope
    headers = {'Authorization': 'Bearer fixture'}
    assert client.get('/api/ai/config', headers=headers).status_code == 403
    for tid in ['a', 'b']:
        h = headers | {'X-Expected-Tenant': tid}
        assert client.put('/api/ai/config', headers=h, json=cfg | {'model': tid}).status_code == 200
        assert client.get('/api/ai/config', headers=h).json()['model'] == tid
    assert client.get('/api/ai/config', headers=headers | {'X-Expected-Tenant': 'foreign'}).status_code == 403
    store.save_membership(store.get_membership('m-a') | {'status': 'revoked'})
    assert client.get('/api/ai/config', headers=headers | {'X-Expected-Tenant': 'a'}).status_code == 403


def test_full_replacement_can_overwrite_plaintext_legacy_config(scope, monkeypatch):
    store, user, client, cfg = scope
    with store._connect() as conn:
        conn.execute('DELETE FROM tenant_memberships')
        conn.execute('UPDATE users SET ai_config=? WHERE id=?', (json.dumps(cfg), 'user'))
    with pytest.raises(secret_store.SecretDecryptError):
        store.get_user_ai_config('user')
    r = client.put('/api/ai/config', headers={'Authorization': 'Bearer fixture'}, json=cfg)
    assert r.status_code == 200
    assert store.get_user_ai_config('user') == cfg


def test_former_teacher_and_supervision_beyond_500_are_not_authorization(scope):
    store, user, client, cfg = scope
    for i in range(510):
        store.save_workflow_record('supervision', {'id': f'filler-{i}', 'student_id': str(i),
            'cohort_id': 'other', 'teacher_id': 'other', 'active': True})
    store.save_workflow_record('supervision', {'id': 'old', 'cohort_id': 'cohort', 'student_id': 'student',
        'teacher_id': 'teacher-a', 'active': True, 'effective_from': '2020-01-01T00:00:00+00:00'})
    store.save_workflow_record('supervision', {'id': 'new', 'cohort_id': 'cohort', 'student_id': 'student',
        'teacher_id': 'teacher-b', 'active': True, 'effective_from': '2021-01-01T00:00:00+00:00'})
    assert 'student' not in workflow.supervised_student_ids(store, 'teacher-a')
    assert workflow.supervised_student_ids(store, 'teacher-b') == {'student': 'cohort'}
    with store._connect() as conn:
        conn.execute("UPDATE workflow_records SET updated_at='2000-01-01' WHERE id IN ('old','new')")
    assert workflow.supervised_student_ids(store, 'teacher-b') == {'student': 'cohort'}


def test_teacher_cannot_reset_promoted_student(scope):
    store, user, client, cfg = scope
    target = store.save_user(user | {'id': 'promoted', 'username': 'promoted', 'role': 'admin', 'teacher_id': 'user'})
    with pytest.raises(HTTPException) as exc:
        teacher.reset_student_password('promoted', teacher.ResetStudentPasswordRequest(new_password='FixturePassword123'), user, store)
    assert exc.value.status_code == 403
    assert store.get_user(target['id'])['password_hash'] == 'fixture'


@pytest.mark.parametrize('address', ['100.64.0.1', '100.127.255.254', '127.0.0.1', '::1'])
def test_guard_rejects_non_global_addresses(address):
    assert outbound_guard._blocked_ip_reason(ipaddress.ip_address(address))


def test_connection_pins_validated_ip_and_rejects_rebinding(monkeypatch):
    monkeypatch.delenv('PAPERGUARD_AI_ALLOW_PRIVATE_NETWORK', raising=False)
    monkeypatch.delenv('PAPERGUARD_AI_ALLOWED_HOSTS', raising=False)
    backend = outbound_guard._ValidatedNetworkBackend()
    calls = []
    monkeypatch.setattr(backend.backend, 'connect_tcp', lambda host, *a, **kw: calls.append(host) or 'stream')
    monkeypatch.setattr(outbound_guard, '_resolve', lambda host: [ipaddress.ip_address('93.184.216.34')])
    assert backend.connect_tcp('provider.invalid', 443) == 'stream'
    assert calls == ['93.184.216.34']  # hostname never goes to socket resolver
    monkeypatch.setattr(outbound_guard, '_resolve', lambda host: [ipaddress.ip_address('127.0.0.1')])
    with pytest.raises(outbound_guard.OutboundBlocked):
        backend.connect_tcp('provider.invalid', 443)
    assert len(calls) == 1


def test_backup_default_preserves_separate_asset_root(scope, monkeypatch, tmp_path):
    store, user, client, cfg = scope
    assets = tmp_path / 'assets'
    (assets / 'uploads').mkdir(parents=True)
    (assets / 'uploads' / 'fixture.png').write_bytes(b'synthetic')
    monkeypatch.setenv('PAPERGUARD_DB_PATH', str(store._db_path))
    monkeypatch.setenv('PAPERGUARD_DATA_DIR', str(assets))
    output = tmp_path / 'backup.zip'
    backup_restore.create_backup(output)
    with zipfile.ZipFile(output) as bundle:
        assert 'data/uploads/fixture.png' in bundle.namelist()
        assert 'database/paperguard.db' in bundle.namelist()


def test_native_usage_attribution_uses_selected_tenant():
    assert ai_governance._user_organization_id({'tenant_id': 'a'}) == 'a'


def test_disclosure_includes_non_paragraph_ai_payloads(scope):
    store, user, client, cfg = scope
    store.set_user_ai_config('user', cfg, tenant_id='a')
    d = client.get('/api/ai/disclosure', headers={'Authorization': 'Bearer fixture', 'X-Expected-Tenant': 'a'}).json()
    assert all(word in d['content_sent'] for word in ['正文', '上传文档', '核查'])
    assert cfg['api_key'] not in json.dumps(d)


def test_saas_unexpected_error_is_correlated_without_logging_payload(caplog):
    from app.core.saas_contract import SaasRoute
    probe = FastAPI()
    probe.router.route_class = SaasRoute
    @probe.middleware('http')
    async def correlation(request, call_next):
        request.state.request_id = 'fixture-correlation'
        return await call_next(request)
    @probe.get('/fixture-error')
    def failure():
        raise RuntimeError('private-body-and-credential')
    response = TestClient(probe).get('/fixture-error')
    assert response.status_code == 500
    assert 'fixture-correlation' in caplog.text
    assert 'RuntimeError' in caplog.text and 'failure' in caplog.text
    assert 'private-body-and-credential' not in caplog.text + response.text
