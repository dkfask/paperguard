"""Build provenance, fail-closed public identity and startup caching contracts."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.version import router
from app.build_identity import BUILD_INFO_NAME, BuildIdentity, load_build_identity

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("freeze_build_identity", REPO / "ops/freeze-build-identity.py")
freeze_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freeze_module)
BUILD = "20261009T010000Z"


@pytest.fixture(scope="module")
def committed_source(tmp_path_factory):
    """Read a real commit without creating commits or stamping dirty source."""
    revision = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    archive = subprocess.check_output(["git", "-C", str(REPO), "archive", revision, "backend/app"])
    root = tmp_path_factory.mktemp("committed-backend")
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream.getmembers():
            if member.isfile():
                path = root / member.name
                assert path.resolve().is_relative_to(root.resolve())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(stream.extractfile(member).read())
    return revision, root / "backend/app"


@pytest.fixture
def declaration(committed_source, tmp_path):
    revision, source = committed_source
    directory = tmp_path / "app"
    shutil.copytree(source, directory)
    record = freeze_module.freeze(REPO, revision, directory, BUILD)
    return directory, record


def test_freeze_matches_real_commit_and_never_requires_runtime_git(declaration, monkeypatch, tmp_path):
    directory, record = declaration
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPERGUARD_SOURCE_REVISION", "f" * 40)
    monkeypatch.setenv("PAPERGUARD_RELEASE_ID", "untrusted-runtime-value")
    monkeypatch.setattr(subprocess, "check_output", lambda *_a, **_kw: pytest.fail("runtime Git access"))
    identity = load_build_identity(directory)
    assert identity.identity_status == "declared"
    assert identity.source_revision == record["source_revision"]
    assert identity.release_id == f'{record["source_revision"][:7]}-p0-1-b{BUILD}'
    assert identity.product_version is None
    assert set(identity.public_dict()) == {"component", "product_version", "source_revision",
                                         "release_id", "build_id", "built_at", "identity_status"}
    # Interpreter caches do not change source provenance.
    cache = directory / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "main.cpython-312.pyc").write_bytes(b"cache")
    assert load_build_identity(directory) == identity


@pytest.mark.parametrize("change", ["modified", "extra", "deleted"])
def test_packaged_source_drift_refuses_identity_and_freeze(declaration, change):
    directory, record = declaration
    if change == "modified":
        with (directory / "main.py").open("a") as stream:
            stream.write("\n# uncommitted drift\n")
    elif change == "extra":
        (directory / "extra.py").write_text("# untracked source\n")
    else:
        (directory / "main.py").unlink()
    assert load_build_identity(directory) == BuildIdentity(identity_status="invalid")
    before = (directory / BUILD_INFO_NAME).read_bytes()
    with pytest.raises(ValueError, match="does not match"):
        freeze_module.freeze(REPO, record["source_revision"], directory, BUILD)
    assert (directory / BUILD_INFO_NAME).read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("source_revision", "abcdef1"), ("source_revision", "G" * 40),
    ("release_id", "fffffff-p0-1-b20261009T010000Z"),
    ("build_id", "20260230T010000Z"), ("built_at", "2026-10-09T01:00:00+08:00"),
    ("source_tree_sha256", "f" * 64), ("component", "frontend"),
    ("product_version", "0.3.0-rc.01"), ("format_version", True),
    ("product_version", "0.3０.0"),
    ("unexpected_config", "must-never-be-returned"),
])
def test_invalid_or_conflicting_declaration_hides_all_identity(declaration, field, value):
    directory, record = declaration
    record[field] = value
    (directory / BUILD_INFO_NAME).write_text(json.dumps(record))
    assert load_build_identity(directory) == BuildIdentity(identity_status="invalid")


@pytest.mark.parametrize("kind", ["duplicate", "oversized", "malformed", "symlink", "source_symlink"])
def test_unsafe_document_or_source_is_rejected(declaration, kind, tmp_path):
    directory, record = declaration
    path = directory / BUILD_INFO_NAME
    if kind == "duplicate":
        path.write_text(json.dumps(record)[:-1] + ',"component":"backend"}')
    elif kind == "oversized":
        path.write_text(" " * (16 * 1024 + 1))
    elif kind == "malformed":
        path.write_bytes(b"\xff")
    elif kind == "symlink":
        other = tmp_path / "identity.json"
        path.rename(other)
        path.symlink_to(other)
    else:
        (directory / "unsafe.py").symlink_to(directory / "main.py")
    assert load_build_identity(directory).identity_status == "invalid"


def test_missing_identity_and_short_revision_are_not_invented(committed_source):
    revision, directory = committed_source
    assert load_build_identity(directory) == BuildIdentity()
    with pytest.raises(ValueError, match="full commit"):
        freeze_module.freeze(REPO, revision[:7], directory, BUILD)
    assert not (directory / BUILD_INFO_NAME).exists()


def test_explicit_product_version_is_semver(declaration):
    directory, record = declaration
    updated = freeze_module.freeze(REPO, record["source_revision"], directory, BUILD,
                                   product_version="0.3.0-rc.1+build.7")
    assert updated["product_version"] == load_build_identity(directory).product_version


@pytest.mark.parametrize("status", ["missing", "invalid", "declared"])
def test_anonymous_version_has_no_store_dependency_and_is_cached(declaration, status, monkeypatch):
    directory, _record = declaration
    app = FastAPI()
    app.include_router(router)
    identity = load_build_identity(directory) if status == "declared" else BuildIdentity(identity_status=status)
    app.state.build_identity = identity
    monkeypatch.setattr("app.db.Store.__init__", lambda *_a, **_kw: pytest.fail("version accessed database"))
    # Changing disk metadata after startup cannot relabel an existing process.
    (directory / BUILD_INFO_NAME).write_text("invalid after startup")
    response = TestClient(app).get("/api/version")
    assert response.status_code == (200 if status == "declared" else 503)
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    if status != "declared":
        error = body.pop("error")
        assert error["code"] == f"BUILD_IDENTITY_{status.upper()}"
        assert error["request_id"] == response.headers["X-Request-ID"]
    assert body == identity.public_dict()
    schema = app.openapi()["paths"]["/api/version"]["get"]
    assert schema["x-anonymous"] is True
    assert "security" not in schema
    assert {"200", "503"} <= schema["responses"].keys()
    assert "ErrorBody" in schema["responses"]["503"]["content"]["application/json"]["schema"]["$ref"]


def test_real_app_missing_identity_uses_shared_request_id(monkeypatch):
    from app.main import app
    monkeypatch.setattr(app.state, "build_identity", BuildIdentity())
    response = TestClient(app).get("/api/version")
    assert response.status_code == 503
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
