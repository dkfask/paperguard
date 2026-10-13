"""Password recovery: verified mailbox, one-use code, atomic session revocation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import _email_code_hash, hash_password
from app.db import Store, new_id
from app.main import app
from app.services import emailer

client = TestClient(app)
OLD_PASSWORD = "OldFixturePassword123"
NEW_PASSWORD = "NewFixturePassword456"
CODE = "123456"


@pytest.fixture
def recovery():
    store = Store()
    user_id = new_id("user")
    email = f"{uuid.uuid4().hex}@example.edu"
    password_hash, salt = hash_password(OLD_PASSWORD)
    user = {
        "id": user_id, "username": f"recovery-{uuid.uuid4().hex}",
        "email": email, "email_verified": 1, "password_set": 1,
        "password_hash": password_hash, "salt": salt, "role": "student",
        "display_name": "恢复流程测试", "status": "active",
        "role_application_status": "approved",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store.save_user(user)
    now = datetime.now(timezone.utc)
    store.save_email_code(email, _email_code_hash(CODE), "password_reset",
                          (now + timedelta(minutes=10)).isoformat(), now.isoformat())
    return store, user, {"email": email, "email_code": CODE, "new_password": NEW_PASSWORD}


def test_reset_consumes_code_revokes_all_sessions_and_preserves_identity(recovery):
    store, user, payload = recovery
    tokens = [client.post("/api/auth/login", json={
        "username": user["username"], "password": OLD_PASSWORD,
    }).json()["token"] for _ in range(2)]
    other = client.post("/api/auth/register", json={
        "username": f"other-{uuid.uuid4().hex}", "password": OLD_PASSWORD,
    }).json()
    other_token = client.post("/api/auth/login", json={
        "username": other["username"], "password": OLD_PASSWORD,
    }).json()["token"]
    response = client.post("/api/auth/reset-password", json={
        **payload, "email": "  " + user["email"].upper() + "  ",
    })
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert store.get_email_code(user["email"]) is None
    for token in tokens:
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {other_token}"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"username": user["email"], "password": NEW_PASSWORD}).status_code == 200
    updated = store.get_user(user["id"])
    for key in ("username", "email", "email_verified", "role", "status", "role_application_status"):
        assert updated[key] == user[key]
    assert client.post("/api/auth/reset-password", json=payload).status_code == 400
    with store._connect() as conn:
        event = conn.execute("SELECT * FROM audit_events WHERE action = 'auth.password_reset' AND actor_id = ?",
                             (user["id"],)).fetchone()
    assert event is not None
    for secret in (CODE, user["email"], NEW_PASSWORD, OLD_PASSWORD):
        assert secret not in str(dict(event))


@pytest.mark.parametrize("purpose,expired,expected", [
    ("register", False, "EMAIL_CODE_INVALID"),
    ("bind", False, "EMAIL_CODE_INVALID"),
    ("first_login", False, "EMAIL_CODE_INVALID"),
    ("password_reset", True, "EMAIL_CODE_EXPIRED"),
])
def test_reset_rejects_wrong_purpose_and_expiry(recovery, purpose, expired, expected):
    store, user, payload = recovery
    now = datetime.now(timezone.utc)
    store.save_email_code(user["email"], _email_code_hash(CODE), purpose,
                          (now + timedelta(seconds=-1 if expired else 600)).isoformat(), now.isoformat())
    before = store.get_user(user["id"])
    response = client.post("/api/auth/reset-password", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == expected
    assert store.get_user(user["id"]) == before
    assert store.get_email_code(user["email"]) is not None


def test_wrong_attempts_persist_and_lock_even_with_correct_code(recovery):
    store, user, payload = recovery
    for _ in range(5):
        response = client.post("/api/auth/reset-password", json={**payload, "email_code": "000000"})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "EMAIL_CODE_MISMATCH"
    assert store.get_email_code(user["email"])["attempts"] == 5
    response = client.post("/api/auth/reset-password", json=payload)
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "EMAIL_CODE_LOCKED"
    assert client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).status_code == 200


@pytest.mark.parametrize("unverified", [False, True])
def test_unknown_or_unverified_mailbox_cannot_reset(recovery, unverified):
    store, user, payload = recovery
    if unverified:
        store.save_user({**user, "email_verified": 0})
    else:
        store.save_user({**user, "email": ""})
    before = store.get_user(user["id"])
    response = client.post("/api/auth/reset-password", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PASSWORD_RECOVERY_UNAVAILABLE"
    assert store.get_user(user["id"]) == before
    assert store.get_email_code(user["email"]) is not None


@pytest.mark.parametrize("password", ["short1", "OnlyLettersLongEnough", OLD_PASSWORD])
def test_invalid_or_unchanged_password_keeps_code_for_retry(recovery, password):
    store, user, payload = recovery
    response = client.post("/api/auth/reset-password", json={**payload, "new_password": password})
    assert response.status_code == 400
    assert store.get_email_code(user["email"])["attempts"] == 0
    assert client.post("/api/auth/reset-password", json=payload).status_code == 200


def test_concurrent_replay_has_only_one_success(recovery):
    _, _, payload = recovery
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/auth/reset-password", json=payload), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 400]


def test_audit_failure_rolls_back_password_code_and_sessions(recovery, monkeypatch):
    store, user, payload = recovery
    token = client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).json()["token"]
    before = store.get_user(user["id"])
    with monkeypatch.context() as patch:
        def fail_audit(*args, **kwargs):
            raise RuntimeError("synthetic audit failure")
        patch.setattr(Store, "save_audit_event_conn", fail_audit)
        response = TestClient(app, raise_server_exceptions=False).post("/api/auth/reset-password", json=payload)
        assert response.status_code == 500
    assert store.get_user(user["id"]) == before
    assert store.get_email_code(user["email"]) is not None
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_recovery_mail_failure_and_cooldown_are_explicit(recovery, monkeypatch):
    store, user, _ = recovery
    store.delete_email_code(user["email"])
    def unavailable(*args, **kwargs):
        raise emailer.EmailNotConfigured()
    monkeypatch.setattr(emailer, "send_verification_code", unavailable)
    response = client.post("/api/auth/email/code", json={"email": user["email"], "purpose": "password_reset"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EMAIL_NOT_CONFIGURED"
    assert "重置密码" in response.json()["detail"]["message"]
    assert store.get_email_code(user["email"]) is None
    captured = {}
    monkeypatch.setattr(emailer, "send_verification_code", lambda email, code, minutes: captured.update(code=code))
    assert client.post("/api/auth/email/code", json={"email": user["email"], "purpose": "password_reset"}).status_code == 200
    assert client.post("/api/auth/email/code", json={"email": user["email"], "purpose": "password_reset"}).status_code == 429
    assert store.get_email_code(user["email"])["purpose"] == "password_reset"
    assert store.get_email_code(user["email"])["code_hash"] == _email_code_hash(captured["code"])


def test_disabled_account_is_not_reactivated_and_client_cannot_choose_target(recovery):
    store, user, payload = recovery
    store.save_user({**user, "status": "disabled"})
    response = client.post("/api/auth/reset-password", json={**payload, "user_id": "user-admin"})
    assert response.status_code == 422
    assert store.get_email_code(user["email"]) is not None
    assert client.post("/api/auth/reset-password", json=payload).status_code == 200
    assert store.get_user(user["id"])["status"] == "disabled"
