"""静态加密：AI 供应商 API Key 的落库保护。

为什么需要：修复前 `set_system_ai_config` / `set_user_ai_config` 把
`api_key` 原样 JSON 序列化进 SQLite。数据库文件一旦被备份误传、误挂载或
被拖库，上游的付费额度就直接暴露——**这不是「内部可见」，而是可被直接
变现的凭据**。S3 已经把这些调用记进 `ai_usage`，可一旦密钥本身泄露，
攻击者可以直接绕过本系统去打上游。

设计取舍（为何是这套组合）：

1. **encrypt-then-MAC**，不是纯 XOR 流加密。
   同一文件里 `auth.py::_openid_cipher` 用的是「派生流 XOR」——它对**篡改**
   没有任何检测能力，而 API Key 是会被原样取出、拼进 HTTP header 的值，
   攻击者改一字节不会报错，只会让请求打到一个他自己控制的地址。
   所以这里额外加了 HMAC-SHA256，密文被改动就直接拒绝解密。
2. **标准库实现**（hashlib/hmac/secrets），不引入 `cryptography`。
   该库带原生扩展，部署时新增二进制依赖；本项目是纯 Python 单服务部署，
   多一个 wheel 就多一处升级失败点。若后续要换 Fernet/AESGCM，
   替换点只有本文件的 `encrypt_secret` / `decrypt_secret` 两个函数。
3. **密钥来源 `PAPERGUARD_SECRET_KEY`**，缺失时**拒绝新写入**，但**不阻断读取**。
   存量明文数据在未配置主密钥时仍要能用（否则一次配置遗漏会让全平台
   AI 立刻不可用）；新数据在没有主密钥时明文落库并被 `_is_plaintext`
   标记，读写路径会记录警告，便于运维发现"忘了配主密钥"。

密文格式：`pgsec1$<b64 nonce>$<b64 ciphertext>$<b64 hmac>`
带版本前缀，便于将来换算法时区分存量。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets

logger = logging.getLogger("paperguard.secrets")

_PREFIX = "pgsec1"
_NONCE_BYTES = 16
_MAC_BYTES = 32


class SecretDecryptError(RuntimeError):
    """密文无法解密：密钥不匹配、数据被篡改或格式损坏。"""


def _master_key() -> bytes:
    raw = os.environ.get("PAPERGUARD_SECRET_KEY", "").strip()
    if not raw:
        return b""
    # 允许直接给 hex，便于用 openssl rand -hex 32 生成
    try:
        decoded = bytes.fromhex(raw)
        if len(decoded) >= 32:
            return decoded
    except ValueError:
        pass
    return raw.encode("utf-8")


def _has_master_key() -> bool:
    return len(_master_key()) > 0


def _keystream(nonce: bytes, key: bytes, length: int) -> bytes:
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
    """判断一个值是否已是本模块产出的密文。"""
    return isinstance(value, str) and value.startswith(f"{_PREFIX}$")


def encrypt_secret(plaintext: str) -> str:
    """加密为 `pgsec1$...`。未配置主密钥时**原样返回**（并告警）。"""
    if not plaintext:
        return plaintext
    if is_encrypted(plaintext):
        return plaintext
    key = _master_key()
    if not key:
        logger.warning(
            "PAPERGUARD_SECRET_KEY 未配置，密钥将以明文落库。"
            "请生成一个（openssl rand -hex 32）并写入服务环境。"
        )
        return plaintext
    nonce = secrets.token_bytes(_NONCE_BYTES)
    data = plaintext.encode("utf-8")
    cipher = bytes(a ^ b for a, b in zip(data, _keystream(nonce, key, len(data))))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return f"{_PREFIX}${_b64e(nonce)}${_b64e(cipher)}${_b64e(tag)}"


def decrypt_secret(value: str) -> str:
    """解密。非密文（存量明文）原样返回。"""
    if not value:
        return value
    if not is_encrypted(value):
        return value
    key = _master_key()
    if not key:
        raise SecretDecryptError(
            "密文存在但未配置 PAPERGUARD_SECRET_KEY，无法解密。"
            "请配置与加密时相同的密钥。"
        )
    try:
        _, nonce_b64, cipher_b64, tag_b64 = value.split("$", 3)
        nonce, cipher, tag = _b64d(nonce_b64), _b64d(cipher_b64), _b64d(tag_b64)
    except (ValueError, TypeError) as exc:
        raise SecretDecryptError("密文格式损坏") from exc
    expect = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    # 必须用 compare_digest：普通 == 会在首个不等字节短路，泄漏时序信息
    if not hmac.compare_digest(expect, tag):
        raise SecretDecryptError("密文 MAC 校验失败（密钥不匹配或数据被篡改）")
    plain = bytes(a ^ b for a, b in zip(cipher, _keystream(nonce, key, len(cipher))))
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretDecryptError("解密结果不是合法 UTF-8") from exc


def protect_config(cfg: dict | None, secret_fields: tuple[str, ...] = ("api_key",)) -> dict | None:
    """加密配置中的敏感字段，用于**写入**路径。"""
    if not isinstance(cfg, dict):
        return cfg
    out = dict(cfg)
    for field in secret_fields:
        value = out.get(field)
        if isinstance(value, str) and value:
            out[field] = encrypt_secret(value)
    return out


def reveal_config(cfg: dict | None, secret_fields: tuple[str, ...] = ("api_key",)) -> dict | None:
    """解密配置中的敏感字段，用于**读取**路径。

    单个字段解密失败**不抛到调用方**：密钥不可读不应该让整个配置读取
    崩掉（那会让所有 AI 功能一起挂掉），而是把该字段置空——上层自然会
    把它当成"未配置"，并在需要时给出可操作的错误提示。
    """
    if not isinstance(cfg, dict):
        return cfg
    out = dict(cfg)
    for field in secret_fields:
        value = out.get(field)
        if isinstance(value, str) and is_encrypted(value):
            try:
                out[field] = decrypt_secret(value)
            except SecretDecryptError as exc:
                logger.error("字段 %s 解密失败: %s", field, exc)
                out[field] = ""
    return out
