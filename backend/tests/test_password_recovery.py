"""Password recovery: verified mailbox, one-use code, atomic session revocation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import _email_code_hash, _password_reset_code_hash, hash_password
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
    store.save_email_code(email, _password_reset_code_hash(email, CODE), "password_reset",
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
    assert store.get_email_code(user["email"])["code_hash"] == _password_reset_code_hash(user["email"], captured["code"])


def test_disabled_account_cannot_recover_or_authenticate_and_client_cannot_choose_target(recovery):
    store, user, payload = recovery
    token = client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).json()["token"]
    store.save_user({**user, "status": "disabled"})
    response = client.post("/api/auth/reset-password", json={**payload, "user_id": "user-admin"})
    assert response.status_code == 422
    assert store.get_email_code(user["email"]) is not None
    assert client.post("/api/auth/reset-password", json=payload).status_code == 400
    assert store.get_user(user["id"])["status"] == "disabled"
    assert store.get_email_code(user["email"]) is not None
    assert client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).status_code == 403
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_old_password_login_cannot_issue_session_after_reset(recovery, monkeypatch):
    import app.auth as auth
    store, user, payload = recovery
    started, resume = Event(), Event()
    original_verify = auth.verify_password
    def delayed_verify(password, password_hash, salt):
        valid = original_verify(password, password_hash, salt)
        if password == OLD_PASSWORD:
            started.set()
            assert resume.wait(10)
        return valid
    monkeypatch.setattr(auth, "verify_password", delayed_verify)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old_login = pool.submit(client.post, "/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD})
        assert started.wait(10)
        try:
            assert client.post("/api/auth/reset-password", json=payload).status_code == 200
        finally:
            resume.set()
        assert old_login.result().status_code == 401
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ? AND revoked_at IS NULL", (user["id"],)).fetchone()[0] == 0


def test_reset_verifier_is_keyed_and_bound_to_mailbox(recovery, monkeypatch):
    store, user, payload = recovery
    record = store.get_email_code(user["email"])
    assert record["code_hash"].startswith("pgrst1$")
    assert record["code_hash"] != _email_code_hash(CODE)
    assert record["code_hash"] != _password_reset_code_hash("other@example.edu", CODE)
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "different-isolated-fixture-key")
    response = client.post("/api/auth/reset-password", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "EMAIL_CODE_MISMATCH"


def test_recovery_without_master_key_does_not_send_or_mutate(recovery, monkeypatch):
    store, user, payload = recovery
    monkeypatch.delenv("PAPERGUARD_SECRET_KEY", raising=False)
    sent = []
    monkeypatch.setattr(emailer, "send_verification_code", lambda *args: sent.append(args))
    before = store.get_user(user["id"])
    original_code = store.get_email_code(user["email"])
    response = client.post("/api/auth/reset-password", json=payload)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PASSWORD_RECOVERY_NOT_CONFIGURED"
    assert store.get_user(user["id"]) == before
    assert store.get_email_code(user["email"]) == original_code
    store.delete_email_code(user["email"])
    response = client.post("/api/auth/email/code", json={"email": user["email"], "purpose": "password_reset"})
    assert response.status_code == 503
    assert not sent
    assert store.get_email_code(user["email"]) is None


def test_authenticated_password_change_cannot_overwrite_recovery(recovery, monkeypatch):
    import app.auth as auth
    store, user, payload = recovery
    token = client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).json()["token"]
    started, resume = Event(), Event()
    original_verify = auth.verify_password
    def delayed_verify(password, password_hash, salt):
        valid = original_verify(password, password_hash, salt)
        if password == OLD_PASSWORD:
            started.set()
            assert resume.wait(10)
        return valid
    monkeypatch.setattr(auth, "verify_password", delayed_verify)
    attacker_password = "AttackerFixturePassword789"
    with ThreadPoolExecutor(max_workers=1) as pool:
        change = pool.submit(client.post, "/api/auth/change-password",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"current_password": OLD_PASSWORD, "new_password": attacker_password})
        assert started.wait(10)
        try:
            assert client.post("/api/auth/reset-password", json=payload).status_code == 200
        finally:
            resume.set()
        assert change.result().status_code == 401
    monkeypatch.setattr(auth, "verify_password", original_verify)
    assert client.post("/api/auth/login", json={"username": user["email"], "password": NEW_PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"username": user["email"], "password": attacker_password}).status_code == 401


def test_stale_first_login_cannot_overwrite_recovered_credentials(recovery, monkeypatch):
    import app.auth as auth
    store, user, payload = recovery
    store.save_user({**user, "must_change_password": 1})
    token = client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).json()["token"]
    other_email = f"first-{uuid.uuid4().hex}@example.edu"
    now = datetime.now(timezone.utc)
    store.save_email_code(other_email, _email_code_hash(CODE), "first_login",
                          (now + timedelta(minutes=10)).isoformat(), now.isoformat())
    attacker_password = "AttackerFixturePassword789"
    started, resume = Event(), Event()
    original_hash = auth.hash_password
    def delayed_hash(password, salt=None):
        result = original_hash(password, salt)
        if password == attacker_password:
            started.set()
            assert resume.wait(10)
        return result
    monkeypatch.setattr(auth, "hash_password", delayed_hash)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first_login = pool.submit(client.post, "/api/auth/first-login/complete",
                                  headers={"Authorization": f"Bearer {token}"},
                                  json={"email": other_email, "email_code": CODE, "new_password": attacker_password})
        assert started.wait(10)
        try:
            assert client.post("/api/auth/reset-password", json=payload).status_code == 200
        finally:
            resume.set()
        assert first_login.result().status_code == 401
    assert store.get_user(user["id"])["email"] == user["email"]
    assert client.post("/api/auth/login", json={"username": user["email"], "password": NEW_PASSWORD}).status_code == 200


def test_inflight_identity_update_cannot_restore_an_old_password(recovery, monkeypatch):
    import app.api.auth as auth_routes
    store, user, payload = recovery
    store.save_user({**user, "role": "user", "role_application_status": "unselected"})
    token = client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).json()["token"]
    started, resume = Event(), Event()
    original_check = auth_routes._require_unselected_user
    def delayed_check(account):
        original_check(account)
        started.set()
        assert resume.wait(10)
    monkeypatch.setattr(auth_routes, "_require_unselected_user", delayed_check)
    with ThreadPoolExecutor(max_workers=1) as pool:
        update = pool.submit(client.post, "/api/auth/role-certification",
                             headers={"Authorization": f"Bearer {token}"}, json={"role": "user"})
        assert started.wait(10)
        try:
            assert client.post("/api/auth/reset-password", json=payload).status_code == 200
        finally:
            resume.set()
        assert update.result().status_code == 401
    assert client.post("/api/auth/login", json={"username": user["email"], "password": NEW_PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"username": user["email"], "password": OLD_PASSWORD}).status_code == 401
