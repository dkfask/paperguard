"""AI 密钥静态加密（S4）的测试。

修复前 `set_system_ai_config` / `set_user_ai_config` 把 api_key 原样
JSON 序列化进 SQLite。数据库文件一旦被备份误传、误挂载或被拖库，上游的
付费额度就直接暴露——这不是「内部可见」，而是可被直接变现的凭据。

这些用例要锁住的性质：
1. 库里**不出现**明文密钥
2. 存进去再取出来是同一个值（功能没坏）
3. 缺少主密钥或存量明文时必须 fail-closed，不允许静默降级
4. 密文被篡改会被 AEAD 拒绝
"""

from __future__ import annotations

import base64
import uuid

import pytest

from app.db import Store
from app.services import secret_store
from tests._helpers import register_fixture

_SECRET = "sk-live-DO-NOT-LEAK-0123456789abcdef"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "0123456789abcdef" * 4)


def _pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def _raw_user_ai_config(username: str) -> str | None:
    conn = Store()._connect()
    try:
        row = conn.execute(
            "SELECT ai_config FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    return row["ai_config"] if row else None


def _raw_system_setting(key: str) -> str | None:
    return Store().get_system_setting(key)


# --------------------------------------------------------------------------
# 模块行为
# --------------------------------------------------------------------------

def test_roundtrip_preserves_value(monkeypatch):
    cipher = secret_store.encrypt_secret(_SECRET)
    assert cipher != _SECRET
    assert secret_store.is_encrypted(cipher)
    assert secret_store.decrypt_secret(cipher) == _SECRET


def test_ciphertext_is_not_reversible_without_key(monkeypatch):
    """换一把密钥必须解不开——否则等于没加密。"""
    cipher = secret_store.encrypt_secret(_SECRET)
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "ffffffffffffffff" * 4)
    with pytest.raises(secret_store.SecretDecryptError):
        secret_store.decrypt_secret(cipher)


def test_tampered_ciphertext_is_rejected():
    """encrypt-then-MAC 的核心断言：改一字节就解不开。

    纯 XOR 流加密做不到这点——API Key 会被原样拼进 HTTP header，
    攻击者改一字节不会报错，只会让请求打向他自己的地址。
    """
    cipher = secret_store.encrypt_secret(_SECRET)
    prefix, nonce, payload, tag = cipher.split("$", 3)
    flipped = bytearray(base64.urlsafe_b64decode(payload))
    flipped[0] ^= 0x01
    tampered = "$".join([
        prefix,
        nonce,
        base64.urlsafe_b64encode(bytes(flipped)).decode("ascii"),
        tag,
    ])
    with pytest.raises(secret_store.SecretDecryptError):
        secret_store.decrypt_secret(tampered)


def test_plaintext_or_missing_master_key_fails_closed(monkeypatch):
    monkeypatch.delenv("PAPERGUARD_SECRET_KEY", raising=False)
    with pytest.raises(secret_store.SecretDecryptError):
        secret_store.decrypt_secret(_SECRET)
    with pytest.raises(secret_store.SecretDecryptError):
        secret_store.encrypt_secret(_SECRET)


def test_reveal_config_fails_closed(monkeypatch):
    cipher = secret_store.encrypt_secret(_SECRET)
    monkeypatch.setenv("PAPERGUARD_SECRET_KEY", "ffffffffffffffff" * 4)
    with pytest.raises(secret_store.SecretDecryptError):
        secret_store.reveal_config({"base_url": "https://x.example", "api_key": cipher})


# --------------------------------------------------------------------------
# 落库不变量（真正的 S4 目标）
# --------------------------------------------------------------------------

def test_user_ai_config_is_encrypted_at_rest():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    username = f"sec_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    register_fixture(client, username, password)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]

    resp = client.put(
        "/api/ai/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_url": "https://api.vendor.example/v1", "api_key": _SECRET, "model": "m-x"},
    )
    assert resp.status_code == 200, resp.text

    raw = _raw_user_ai_config(username)
    assert raw is not None
    assert _SECRET not in raw, "API Key 以明文落库了"
    assert "pgsec1$" in raw
    # 功能没坏：读出来还是同一个值
    user_id = Store().get_user_by_username(username)["id"]
    assert Store().get_user_ai_config(user_id)["api_key"] == _SECRET


def test_system_ai_config_is_encrypted_at_rest():
    store = Store()
    store.set_system_ai_config(
        {"base_url": "https://api.vendor.example/v1", "api_key": _SECRET, "model": "m-x"}
    )
    raw = _raw_system_setting("ai_default_config")
    assert _SECRET not in raw, "系统级 API Key 以明文落库了"
    assert "pgsec1$" in raw
    assert store.get_system_ai_config()["api_key"] == _SECRET


def test_masked_response_never_leaks_plaintext():
    """掩码接口不能因为解密而把明文吐回去。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    store = Store()
    username = f"sec_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    register_fixture(client, username, password)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    client.put(
        "/api/ai/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_url": "https://api.vendor.example/v1", "api_key": _SECRET, "model": "m-x"},
    )
    resp = client.get("/api/ai/config", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert _SECRET not in resp.text
    user_id = store.get_user_by_username(username)["id"]
    assert store.get_user_ai_config(user_id)["api_key"] == _SECRET


def test_save_user_does_not_downgrade_ciphertext(monkeypatch):
    """不变量：`save_user` 整行回写不能把密文改回明文。

    `get_user` 返回的是原始列（密文），`save_user` 原样回写。
    若将来有人让 `get_user` 返回解密后的值，这条会立刻失败——
    那正是加密被静默绕过的路径。
    """
    store = Store()
    store.set_system_ai_config(
        {"base_url": "https://api.vendor.example/v1", "api_key": _SECRET, "model": "m-x"}
    )
    # 直接验证列值：系统配置不经 save_user 路径，单独覆盖用户列
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    username = f"sec_{uuid.uuid4().hex[:8]}"
    password = _pwd()
    register_fixture(client, username, password)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    client.put(
        "/api/ai/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_url": "https://api.vendor.example/v1", "api_key": _SECRET, "model": "m-x"},
    )
    user_id = store.get_user_by_username(username)["id"]

    user = store.get_user(user_id)
    user["display_name"] = "改名触发整行回写"
    store.save_user(user)

    raw = _raw_user_ai_config(username)
    assert _SECRET not in raw, "save_user 把密文降级回了明文"
    assert store.get_user_ai_config(user_id)["api_key"] == _SECRET
