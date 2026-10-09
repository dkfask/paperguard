#!/usr/bin/env python3
"""Freeze identity only when every packaged app file matches an explicit commit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.build_identity import (BUILD_INFO_NAME, source_files, source_tree_digest,
                               validate_declaration)  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_release_markers", Path(__file__).with_name("sync-release-markers.py"))
_markers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_markers)


def freeze(repo: Path, revision: str, app_dir: Path, build_id: str,
           *, release_label: str = "p0-1", product_version: str | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("an explicit full commit is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", release_label):
        raise ValueError("invalid release label")
    manifest = _markers.source_manifest(repo, revision)
    if manifest["source_revision"] != revision:
        raise ValueError("revision must identify the commit itself")
    expected = {name.removeprefix("backend/app/"): digest for name, digest in manifest["files"].items()}
    actual = source_files(app_dir)
    if actual != expected:
        raise ValueError("packaged source does not match the frozen commit")
    record = {"format": "paperguard-build-identity", "format_version": 1,
              "component": "backend", "product_version": product_version,
              "source_revision": manifest["source_revision"],
              "release_id": f"{revision[:7]}-{release_label}-b{build_id}", "build_id": build_id,
              "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
              "source_tree_sha256": source_tree_digest(actual)}
    validate_declaration(record)
    target = app_dir / BUILD_INFO_NAME
    if target.is_symlink():
        raise ValueError("identity symlink rejected")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=app_dir,
                                         prefix=".build-identity-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o644)
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--app-dir", type=Path, help="Packaged app directory; defaults to repo/backend/app")
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--release-label", default="p0-1")
    parser.add_argument("--product-version")
    args = parser.parse_args()
    try:
        record = freeze(args.repo, args.revision, args.app_dir or args.repo / "backend/app",
                        args.build_id, release_label=args.release_label, product_version=args.product_version)
        print(json.dumps({"result": "declared", "source_revision": record["source_revision"],
                          "release_id": record["release_id"], "build_id": record["build_id"]}))
        return 0
    except Exception as exc:
        # Never echo arbitrary input/configuration into build logs.
        print(json.dumps({"result": "blocked", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
