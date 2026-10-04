"""AEAD protection for provider credentials at rest.

New writes use AES-256-GCM with a deployment-provided master key.  Plaintext values are rejected on read.  The authenticated pre-AEAD ciphertext
format is read for compatibility only and is upgraded to AES-GCM on the next
controlled save.  A missing master key or
a failed authentication tag is deliberately fail-closed and never falls back
to another provider or to plaintext.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("paperguard.secrets")

_PREFIX = "pgsec1"
_AAD = b"paperguard:provider-credential:v1"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_LEGACY_NONCE_BYTES = 16
_LEGACY_TAG_BYTES = 32


class SecretDecryptError(RuntimeError):
    """密文无法解密：主密钥缺失、密钥不匹配或数据被篡改。"""


def _master_key() -> bytes:
    raw = os.environ.get("PAPERGUARD_SECRET_KEY", "").strip()
    if not raw:
        return b""
    try:
        decoded = bytes.fromhex(raw)
        if len(decoded) in {16, 24, 32}:
            return decoded if len(decoded) == 32 else hashlib.sha256(decoded).digest()
        if len(decoded) > 32:
            return decoded[:32]
    except ValueError:
        pass
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _require_master_key() -> bytes:
    key = _master_key()
    if not key:
        raise SecretDecryptError(
            "PAPERGUARD_SECRET_KEY 未配置，凭据读写已拒绝；请先配置部署主密钥"
        )
    return key


def _legacy_keystream(nonce: bytes, key: bytes, length: int) -> bytes:
    out = b""
    block = 0
    while len(out) < length:
        out += hashlib.sha256(nonce + block.to_bytes(4, "big") + key).digest()
        block += 1
    return out[:length]


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(f"{_PREFIX}$")


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential; never return plaintext when the key is absent."""
    if not plaintext:
        return plaintext
    if is_encrypted(plaintext):
        # Preserve current AEAD ciphertext; transparently upgrade the legacy
        # authenticated stream form when a config is explicitly saved.
        try:
            _, nonce_b64, _, tag_b64 = plaintext.split("$", 3)
            if len(_b64d(nonce_b64)) == _NONCE_BYTES and len(_b64d(tag_b64)) == _TAG_BYTES:
                return plaintext
        except (ValueError, TypeError):
            pass
        plaintext = decrypt_secret(plaintext)
    key = _require_master_key()
    nonce = secrets.token_bytes(_NONCE_BYTES)
    encrypted = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    cipher, tag = encrypted[:-_TAG_BYTES], encrypted[-_TAG_BYTES:]
    return f"{_PREFIX}${_b64e(nonce)}${_b64e(cipher)}${_b64e(tag)}"


def decrypt_secret(value: str) -> str:
    """Decrypt an AEAD value; plaintext and malformed values fail closed."""
    if not value:
        return value
    if not is_encrypted(value):
        raise SecretDecryptError("检测到未加密凭据；禁止以明文继续运行，请完成迁移")
    key = _require_master_key()
    try:
        _, nonce_b64, cipher_b64, tag_b64 = value.split("$", 3)
        nonce, cipher, tag = _b64d(nonce_b64), _b64d(cipher_b64), _b64d(tag_b64)
        if len(nonce) == _LEGACY_NONCE_BYTES and len(tag) == _LEGACY_TAG_BYTES:
            # Compatibility-only reader for the pre-AEAD authenticated stream
            # format.  It is never used for new writes and remains fail-closed
            # on a bad MAC; saving the revealed config upgrades it to AES-GCM.
            expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, tag):
                raise ValueError("legacy credential authentication failed")
            plain = bytes(a ^ b for a, b in zip(cipher, _legacy_keystream(nonce, key, len(cipher))))
            return plain.decode("utf-8")
        if len(nonce) != _NONCE_BYTES or len(tag) != _TAG_BYTES:
            raise ValueError("invalid nonce or tag length")
        plain = AESGCM(key).decrypt(nonce, cipher + tag, _AAD)
        return plain.decode("utf-8")
    except (ValueError, TypeError, InvalidTag, UnicodeDecodeError) as exc:
        raise SecretDecryptError("凭据 AEAD 校验失败或密文格式损坏") from exc


def protect_config(cfg: dict | None, secret_fields: tuple[str, ...] = ("api_key",)) -> dict | None:
    if not isinstance(cfg, dict):
        return cfg
    out = dict(cfg)
    for field in secret_fields:
        value = out.get(field)
        if isinstance(value, str) and value:
            out[field] = encrypt_secret(value)
    return out


def reveal_config(cfg: dict | None, secret_fields: tuple[str, ...] = ("api_key",)) -> dict | None:
    if not isinstance(cfg, dict):
        return cfg
    out = dict(cfg)
    for field in secret_fields:
        value = out.get(field)
        if isinstance(value, str) and value:
            out[field] = decrypt_secret(value)
    return out
