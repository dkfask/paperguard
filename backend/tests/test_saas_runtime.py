"""Runtime SaaS foundation: context, scope fail-closed, ledger and audit append-only."""

from __future__ import annotations

import socket
import sqlite3
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import ai as ai_module
from app.db import Store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "0123456789abcdef" * 4)
    monkeypatch.setenv("PAPERGUARD_AI_DAILY_QUOTA", "10000")
    monkeypatch.setenv("PAPERGUARD_AI_BURST_PER_MINUTE", "10000")


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )


def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _user(role: str = "student") -> tuple[str, str, dict[str, str]]:
    username, password = f"saas_{uuid.uuid4().hex[:8]}", _pwd()
    assert client.post("/api/auth/register", json={"username": username, "password": password, "role": role}).status_code == 201
    token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
    return username, password, {"Authorization": f"Bearer {token}"}


def test_saas_context_contains_server_resolved_membership():
    username, _, headers = _user()
    store = Store()
    user = store.get_user_by_username(username)
    user["organization_context"] = {"school_id": f"school-{uuid.uuid4().hex[:8]}"}
    store.save_user(user)
    # Existing session reads the same user row on every request.
    response = client.get("/api/saas/v1/context", headers=headers)
    assert response.status_code == 200, response.text
    context = response.json()["context"]
    assert context["tenant_id"].startswith("tenant-legacy-")
    assert context["membership_id"]
    assert "tenant:read" in context["capabilities"]
    assert context["auth_version"] >= 1


def test_unknown_explicit_tenant_cannot_borrow_platform_ai(monkeypatch):
    username, _, headers = _user()
    store = Store()
    user = store.get_user_by_username(username)
    user["organization_context"] = {"tenant_id": "tenant-does-not-exist"}
    store.save_user(user)
    store.set_system_ai_config({"base_url": "https://platform.example/v1", "api_key": "sk-platform", "model": "platform"})
    response = client.get("/api/ai/status", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TENANT_CONTEXT_INVALID"


def test_ai_idempotency_replays_completed_result(monkeypatch):
    username, _, headers = _user()
    store = Store()
    user = store.get_user_by_username(username)
    store.set_user_ai_config(user["id"], {"base_url": "https://vendor.example/v1", "api_key": "sk-idem", "model": "m"})
    calls = []
    monkeypatch.setattr(ai_module, "_dispatch_completion", lambda *args, **kwargs: calls.append(1) or "same result")
    payload = {"action": "polish", "text": "same request"}
    first = client.post("/api/ai/assist", headers={**headers, "Idempotency-Key": "idem-1"}, json=payload)
    second = client.post("/api/ai/assist", headers={**headers, "Idempotency-Key": "idem-1"}, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"text": "same result"}
    assert len(calls) == 1
    assert Store().list_ai_requests()[-1]["status"] == "completed"


def test_audit_ledger_is_append_only():
    store = Store()
    event = {"id": f"audit-test-{uuid.uuid4().hex}", "action": "test", "resource_type": "test", "resource_id": "1"}
    store.save_audit_event(event | {"reason": "first"})
    with pytest.raises(sqlite3.IntegrityError):
        store.save_audit_event(event | {"reason": "overwrite"})
