from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _admin_headers():
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': 'TestAdminPassword123'})
    assert response.status_code == 200
    return {'Authorization': f"Bearer {response.json()['token']}"}


def test_email_registration_is_identity_neutral_and_role_certification(monkeypatch):
    import app.auth as auth_module

    monkeypatch.setattr(auth_module.emailer, 'email_configured', lambda: True)
    monkeypatch.setattr(auth_module, '_verify_email_code', lambda *args, **kwargs: None)
    suffix = 'role-cert-user'
    response = client.post('/api/auth/register', json={
        'email': f'{suffix}@example.com', 'password': 'RoleCertPassword123',
        'display_name': '统一注册用户', 'role': 'teacher', 'email_code': '000000',
    })
    assert response.status_code == 201, response.text
    user = response.json()
    assert user['role'] == 'user'
    assert user['role_application_status'] == 'unselected'

    login = client.post('/api/auth/login', json={'username': user['username'], 'password': 'RoleCertPassword123'})
    headers = {'Authorization': f"Bearer {login.json()['token']}"}
    options = client.get('/api/auth/role-certification', headers=headers)
    assert options.status_code == 200
    assert options.json()['application_status'] == 'unselected'

    social = client.post('/api/auth/role-certification', headers=headers, json={'role': 'user'})
    assert social.status_code == 422
    current = client.get('/api/auth/me', headers=headers).json()
    assert current['role'] == 'user'
    assert current['role_application_status'] == 'unselected'
    assert client.get('/api/document-projects', headers=headers).status_code == 403


def test_school_role_requires_valid_organization_and_admin_approval(monkeypatch):
    import app.auth as auth_module

    monkeypatch.setattr(auth_module.emailer, 'email_configured', lambda: True)
    monkeypatch.setattr(auth_module, '_verify_email_code', lambda *args, **kwargs: None)
    admin = _admin_headers()
    suffix = 'role-cert-school'
    school = client.post('/api/organizations', headers=admin, json={
        'id': suffix, 'name': '认证测试学校', 'type': 'school',
    })
    assert school.status_code == 201
    register = client.post('/api/auth/register', json={
        'email': f'{suffix}@example.com', 'password': 'RoleCertPassword123',
        'display_name': '待认证教师', 'email_code': '000000',
    })
    assert register.status_code == 201
    login = client.post('/api/auth/login', json={
        'username': register.json()['username'], 'password': 'RoleCertPassword123',
    })
    headers = {'Authorization': f"Bearer {login.json()['token']}"}
    invalid = client.post('/api/auth/role-certification', headers=headers, json={
        'role': 'teacher', 'organization_context': {'school_id': 'missing'},
    })
    assert invalid.status_code == 422
    pending = client.post('/api/auth/role-certification', headers=headers, json={
        'role': 'teacher', 'organization_context': {'school_id': suffix},
    })
    assert pending.status_code == 200
    assert pending.json()['role'] == 'user'
    assert pending.json()['role_application_status'] == 'pending'
    user_id = pending.json()['id']
    approved = client.post(f'/api/auth/admin/users/{user_id}/role-certification', headers=admin, json={'decision': 'approve'})
    assert approved.status_code == 200
    assert approved.json()['role'] == 'teacher'
    assert approved.json()['organization_context']['school_id'] == suffix
