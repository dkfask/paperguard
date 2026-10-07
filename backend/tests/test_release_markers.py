"""Release-marker reconciliation is metadata-only and fail-closed on byte drift."""
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "sync-release-markers.py"
_spec = importlib.util.spec_from_file_location("release_markers", _SCRIPT)
markers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(markers)


@pytest.fixture
def deployed(tmp_path):
    root, webroot = tmp_path / "runtime", tmp_path / "webroot"
    root.mkdir()
    webroot.mkdir()
    release, revision = "abcdef1-rc13", "abcdef1" + "0" * 33
    (root / "current-release").write_text(release + "\n")
    (root / "current-release.json").write_text('{"commit":"stale","deployed_at":"old"}')
    files = {}
    for name in ["backend/app/main.py", "backend/app/db.py"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic application\n")
        files[name] = markers.digest(path.read_bytes())
    (webroot / "index.html").write_text("synthetic frontend")
    release_dir = root / "releases" / release
    release_dir.mkdir(parents=True)
    (release_dir / "frontend.sha256").write_text(
        markers.digest((webroot / "index.html").read_bytes()) + "  index.html\n"
    )
    manifest = {"format": "paperguard-source-checksums", "format_version": 1,
                "source_revision": revision, "files": files}
    return root, webroot, release, manifest


def test_dry_run_does_not_write_or_claim_current_metadata(deployed):
    root, webroot, release, manifest = deployed
    before = (root / "current-release.json").read_bytes()
    result = markers.synchronize(root, webroot, release, manifest)
    assert result["result"] == "verified" and not result["written"]
    assert not result["metadata_current"]
    assert (root / "current-release.json").read_bytes() == before
    assert not list(root.glob("*.backup-*"))


def test_sync_backs_up_stale_metadata_and_is_idempotent(deployed):
    root, webroot, release, manifest = deployed
    before = (root / "current-release.json").read_bytes()
    result = markers.synchronize(root, webroot, release, manifest, write=True)
    assert result["written"] and result["metadata_current"]
    record = json.loads((root / "current-release.json").read_text())
    assert record["commit"] == manifest["source_revision"]
    assert record["deployed_at"] is None  # reconciliation is not a new deployment
    assert (root / record["previous_metadata_backup"]).read_bytes() == before
    assert (root / record["previous_metadata_backup"]).stat().st_mode & 0o777 == 0o600
    assert not markers.synchronize(root, webroot, release, manifest, write=True)["written"]
    assert len(list(root.glob("*.backup-*"))) == 1


@pytest.mark.parametrize("drift", ["backend", "frontend", "release", "revision", "empty", "traversal", "symlink", "missing_frontend"])
def test_mismatch_refuses_to_touch_metadata(deployed, drift, tmp_path):
    root, webroot, release, manifest = deployed
    before = (root / "current-release.json").read_bytes()
    if drift == "backend":
        (root / "backend/app/main.py").write_text("changed")
    elif drift == "frontend":
        (webroot / "index.html").write_text("changed")
    elif drift == "release":
        (root / "current-release").write_text("fffffff-other")
    elif drift == "revision":
        manifest["source_revision"] = "f" * 40
    elif drift == "empty":
        manifest["files"] = {}
    elif drift == "traversal":
        manifest["files"]["backend/app/../../outside"] = "0" * 64
    elif drift == "symlink":
        target = tmp_path / "outside"
        target.write_text("outside")
        (root / "backend/app/main.py").unlink()
        (root / "backend/app/main.py").symlink_to(target)
    elif drift == "missing_frontend":
        (root / "releases" / release / "frontend.sha256").write_text("")
    with pytest.raises(markers.VerificationError):
        markers.synchronize(root, webroot, release, manifest, write=True)
    assert (root / "current-release.json").read_bytes() == before
    assert not list(root.glob("*.backup-*"))
