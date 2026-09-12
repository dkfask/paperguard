"""Replaceable binary storage adapters for document assets and artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import posixpath
import tempfile
from typing import Any


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    checksum: str
    content_type: str = "application/octet-stream"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "content_type": self.content_type,
        }


def _stored(key: str, data: bytes, content_type: str) -> dict[str, Any]:
    return StoredObject(
        key=key,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        content_type=content_type or "application/octet-stream",
    ).as_dict()


class LocalStorageAdapter:
    """Filesystem-backed adapter with atomic writes and traversal protection."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not key or "\\" in key:
            raise ValueError("storage key is invalid")
        normalized = posixpath.normpath(key)
        if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
            raise ValueError("storage key escapes storage root")
        path = (self.root / Path(normalized)).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError("storage key escapes storage root")
        return path

    def resolve_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if path.is_file() else None

    def put_bytes(self, key: str, data: bytes, content_type: str = "") -> dict[str, Any]:
        if not isinstance(data, bytes):
            raise TypeError("storage payload must be bytes")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".upload-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        return _stored(key, data, content_type)

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def exists(self, key: str) -> bool:
        return self.resolve_path(key) is not None

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True


class S3StorageAdapter:
    """S3-compatible adapter; works with S3, MinIO, OSS, and COS clients."""

    def __init__(self, bucket: str, prefix: str = "", client: Any | None = None):
        if not bucket:
            raise ValueError("S3 bucket is required")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("boto3 is required when S3 storage is enabled") from exc
            client = boto3.client("s3")
        self.bucket = bucket
        normalized_prefix = posixpath.normpath(prefix) if prefix else ""
        if normalized_prefix in {".", ".."} or normalized_prefix.startswith("../") or normalized_prefix.startswith("/"):
            raise ValueError("S3 storage prefix is invalid")
        self.prefix = normalized_prefix.strip("/")
        self.client = client

    def _key(self, key: str) -> str:
        if not isinstance(key, str) or not key or "\\" in key:
            raise ValueError("storage key is invalid")
        normalized = posixpath.normpath(key)
        if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
            raise ValueError("storage key is invalid")
        return posixpath.join(self.prefix, normalized) if self.prefix else normalized

    def put_bytes(self, key: str, data: bytes, content_type: str = "") -> dict[str, Any]:
        if not isinstance(data, bytes):
            raise TypeError("storage payload must be bytes")
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(key), "Body": data}
        if content_type:
            params["ContentType"] = content_type
        self.client.put_object(**params)
        return _stored(key, data, content_type)

    def get_bytes(self, key: str) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            if getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception as exc:
            if getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return False
            raise

    def delete(self, key: str) -> bool:
        if not self.exists(key):
            return False
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        return True


def configured_storage(root: str | Path) -> LocalStorageAdapter | S3StorageAdapter:
    """Build the configured adapter; local storage remains the default."""
    backend = os.environ.get("PAPERGUARD_STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        return LocalStorageAdapter(root)
    if backend == "s3":
        return S3StorageAdapter(
            bucket=os.environ.get("PAPERGUARD_S3_BUCKET", "").strip(),
            prefix=os.environ.get("PAPERGUARD_S3_PREFIX", "").strip(),
        )
    raise ValueError(f"unsupported storage backend: {backend}")


__all__ = ["LocalStorageAdapter", "S3StorageAdapter", "StoredObject", "configured_storage"]
