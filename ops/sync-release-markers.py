#!/usr/bin/env python3
"""Verify deployed bytes before reconciling current-release.json; never deploy code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from datetime import datetime, timezone
import uuid


class VerificationError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_path(raw: str) -> str:
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or str(path) != raw:
        raise VerificationError("manifest contains an unsafe relative path")
    return raw


def source_manifest(repo: Path, revision: str) -> dict:
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"], text=True
    ).strip()
    names = subprocess.check_output(
        ["git", "-C", str(repo), "ls-tree", "-rz", "--name-only", revision, "--", "backend/app"]
    ).decode().split("\0")
    files = {}
    for name in filter(None, names):
        files[relative_path(name)] = digest(subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{revision}:{name}"]
        ))
    return {"format": "paperguard-source-checksums", "format_version": 1,
            "source_revision": revision, "files": files}


def verify_files(base: Path, files: dict) -> int:
    if not files:
        raise VerificationError("empty file manifest")
    base = base.resolve(strict=True)
    for name, expected in files.items():
        name = relative_path(name)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise VerificationError("invalid SHA-256 digest")
        path = base / name
        if path.is_symlink() or not path.resolve().is_relative_to(base) or not path.is_file():
            raise VerificationError(f"missing or unsafe deployed file: {name}")
        if digest(path.read_bytes()) != expected:
            raise VerificationError(f"deployed checksum mismatch: {name}")
    return len(files)


def synchronize(root: Path, webroot: Path, expected_release: str, manifest: dict,
                *, write: bool = False) -> dict:
    if not re.fullmatch(r"[0-9a-f]{7,40}(?:-[A-Za-z0-9._-]+)?", expected_release):
        raise VerificationError("invalid release identity")
    if manifest.get("format") != "paperguard-source-checksums" or manifest.get("format_version") != 1:
        raise VerificationError("unsupported source manifest")
    revision = manifest.get("source_revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise VerificationError("full source revision is required")
    if not revision.startswith(expected_release.split("-", 1)[0]):
        raise VerificationError("release and source revision disagree")
    files = manifest.get("files")
    if not isinstance(files, dict) or not {"backend/app/main.py", "backend/app/db.py"} <= files.keys():
        raise VerificationError("source manifest lacks application entrypoints")
    if any(not name.startswith("backend/app/") for name in files):
        raise VerificationError("source manifest exceeds backend application scope")
    marker = root / "current-release"
    metadata = root / "current-release.json"
    if marker.is_symlink() or metadata.is_symlink():
        raise VerificationError("release markers must not be symlinks")
    if marker.read_text().strip() != expected_release:
        raise VerificationError("current release changed or does not match the expected release")
    old_bytes = metadata.read_bytes() if metadata.exists() else None
    old = json.loads(old_bytes) if old_bytes is not None else {}
    if not isinstance(old, dict):
        raise VerificationError("existing release metadata is not an object")
    backend_count = verify_files(root, files)
    frontend_manifest_path = root / "releases" / expected_release / "frontend.sha256"
    if frontend_manifest_path.is_symlink() or not frontend_manifest_path.resolve().is_relative_to(root.resolve()):
        raise VerificationError("unsafe frontend manifest")
    frontend_bytes = frontend_manifest_path.read_bytes()
    frontend_files = {}
    for line in frontend_bytes.decode().splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            raise VerificationError("invalid frontend manifest line")
        sha, name = fields
        name = relative_path(name)
        if name in frontend_files:
            raise VerificationError("duplicate frontend manifest file")
        frontend_files[name] = sha
    if "index.html" not in frontend_files:
        raise VerificationError("frontend manifest lacks index.html")
    frontend_count = verify_files(webroot, frontend_files)
    stable = {"format": "paperguard-current-release", "format_version": 2,
              "release_id": expected_release, "commit": revision, "source_revision": revision,
              "backend": {"verified_files": backend_count,
                          "source_manifest_sha256": digest(json.dumps(manifest, sort_keys=True).encode())},
              "frontend": {"verified_files": frontend_count,
                           "manifest_sha256": digest(frontend_bytes)}}
    synced = all(old.get(key) == value for key, value in stable.items())
    result = {"result": "verified", "release_id": expected_release, "source_revision": revision,
              "backend_files": backend_count, "frontend_files": frontend_count,
              "metadata_current": synced, "written": False}
    if not write or synced:
        return result
    # Recheck both markers before replacing any metadata; the caller may be racing a deployment.
    if marker.read_text().strip() != expected_release or (metadata.read_bytes() if metadata.exists() else None) != old_bytes:
        raise VerificationError("release metadata changed during verification")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {**stable, "marker_updated_at": stamp,
              "runtime_verified_at": stamp, "deployed_at": None,
              "deployment_timestamp_note": "metadata reconciliation; not a new deployment"}
    if old_bytes is not None:
        backup = root / f"current-release.json.backup-{uuid.uuid4().hex}"
        with backup.open("xb") as stream:
            os.chmod(backup, 0o600)
            stream.write(old_bytes)
        record["previous_metadata_backup"] = backup.name
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=root, prefix=".release-metadata-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, metadata)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return {**result, "metadata_current": True, "written": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("source-manifest")
    generate.add_argument("--repo", type=Path, required=True)
    generate.add_argument("--revision", required=True)
    sync = commands.add_parser("sync")
    sync.add_argument("--root", type=Path, required=True)
    sync.add_argument("--webroot", type=Path, required=True)
    sync.add_argument("--expected-release", required=True)
    sync.add_argument("--source-manifest", type=Path, required=True)
    sync.add_argument("--write", action="store_true", help="reconcile metadata only after all checks pass")
    args = parser.parse_args()
    try:
        if args.command == "source-manifest":
            result = source_manifest(args.repo, args.revision)
        else:
            result = synchronize(args.root, args.webroot, args.expected_release,
                                 json.loads(args.source_manifest.read_text()), write=args.write)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (VerificationError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"result": "blocked", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
