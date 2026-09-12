"""Phase 8 binary storage adapter contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.storage import LocalStorageAdapter, S3StorageAdapter, configured_storage


def test_local_storage_is_atomic_and_rejects_traversal(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path / "objects")
    stored = storage.put_bytes("artifacts/result.docx", b"PK-template", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    assert stored["size_bytes"] == len(b"PK-template")
    assert storage.get_bytes("artifacts/result.docx") == b"PK-template"
    assert storage.exists("artifacts/result.docx") is True
    assert not list((tmp_path / "objects" / "artifacts").glob(".upload-*"))
    with pytest.raises(ValueError):
        storage.put_bytes("../outside.docx", b"no")

    assert storage.delete("artifacts/result.docx") is True
    assert storage.get_bytes("artifacts/result.docx") is None


class _FakeBody:
    def __init__(self, data: bytes):
        self.data = data

    def read(self) -> bytes:
        return self.data


class _NotFound(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 404}}


class _FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, *, Bucket: str, Key: str):
        if (Bucket, Key) not in self.objects:
            raise _NotFound(Key)
        return {"Body": _FakeBody(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket: str, Key: str):
        if (Bucket, Key) not in self.objects:
            raise _NotFound(Key)
        return {}

    def delete_object(self, *, Bucket: str, Key: str):
        self.objects.pop((Bucket, Key), None)


def test_s3_storage_adapter_uses_s3_compatible_client() -> None:
    client = _FakeS3()
    storage = S3StorageAdapter("bucket", prefix="paperguard", client=client)
    stored = storage.put_bytes("artifact.docx", b"result", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    assert stored["checksum"]
    assert client.objects[("bucket", "paperguard/artifact.docx")] == b"result"
    assert storage.exists("artifact.docx") is True
    assert storage.get_bytes("artifact.docx") == b"result"
    assert storage.delete("artifact.docx") is True
    assert storage.exists("artifact.docx") is False


def test_s3_storage_adapter_rejects_escaping_prefix() -> None:
    with pytest.raises(ValueError):
        S3StorageAdapter("bucket", prefix="../outside", client=_FakeS3())


def test_configured_storage_defaults_to_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PAPERGUARD_STORAGE_BACKEND", raising=False)
    storage = configured_storage(tmp_path)
    assert isinstance(storage, LocalStorageAdapter)
