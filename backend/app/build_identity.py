"""Read immutable backend build identity without Git, environment or storage access."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Literal

BUILD_INFO_NAME = "_build_info.json"
_MAX_INFO_BYTES = 16 * 1024
_FIELDS = {"format", "format_version", "component", "product_version", "source_revision",
           "release_id", "build_id", "built_at", "source_tree_sha256"}
_PUBLIC_FIELDS = {"component", "product_version", "source_revision", "release_id",
                  "build_id", "built_at", "identity_status"}


@dataclass(frozen=True)
class BuildIdentity:
    component: Literal["backend"] = "backend"
    product_version: str | None = None
    source_revision: str | None = None
    release_id: str | None = None
    build_id: str | None = None
    built_at: str | None = None
    identity_status: Literal["missing", "invalid", "declared"] = "missing"

    @property
    def status_code(self) -> int:
        return 200 if self.identity_status == "declared" else 503

    def public_dict(self) -> dict:
        return asdict(self)


def source_files(app_dir: Path) -> dict[str, str]:
    """Hash source bytes; generated identity and interpreter caches are not source."""
    if app_dir.is_symlink() or not app_dir.is_dir():
        raise ValueError("unsafe application directory")
    files = {}
    for path in sorted(app_dir.rglob("*")):
        relative = path.relative_to(app_dir)
        if path.is_symlink():
            raise ValueError("source symlink rejected")
        if path.is_dir():
            continue
        if relative.as_posix() == BUILD_INFO_NAME or "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if not path.is_file():
            raise ValueError("source special file rejected")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        files[relative.as_posix()] = digest.hexdigest()
    if not files:
        raise ValueError("empty application source")
    return files


def source_tree_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_identity_values(data: dict) -> None:
    if data["component"] != "backend":
        raise ValueError("wrong component")
    revision = data["source_revision"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("full source revision required")
    build = data["build_id"]
    if not isinstance(build, str) or not re.fullmatch(r"\d{8}T\d{6}Z", build, flags=re.ASCII):
        raise ValueError("invalid build id")
    datetime.strptime(build, "%Y%m%dT%H%M%SZ")
    release = data["release_id"]
    if not isinstance(release, str) or len(release) > 160 or not re.fullmatch(r"[0-9a-f]{7,40}-[A-Za-z0-9._-]+", release):
        raise ValueError("invalid release id")
    if not revision.startswith(release.split("-", 1)[0]) or not release.endswith("-b" + build):
        raise ValueError("release identity conflict")
    product = data["product_version"]
    if product is not None and (not isinstance(product, str) or not re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", product, flags=re.ASCII)):
        raise ValueError("invalid product version")
    if product is not None and "-" in product.split("+", 1)[0]:
        prerelease = product.split("+", 1)[0].split("-", 1)[1]
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")):
            raise ValueError("invalid numeric prerelease")
    stamp = data["built_at"]
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp, flags=re.ASCII):
        raise ValueError("UTC build timestamp required")
    datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def validate_declaration(data: dict) -> None:
    if not isinstance(data, dict) or set(data) != _FIELDS:
        raise ValueError("invalid build identity fields")
    if data["format"] != "paperguard-build-identity" or type(data["format_version"]) is not int or data["format_version"] != 1:
        raise ValueError("unsupported build identity")
    _validate_identity_values(data)
    digest = data["source_tree_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid source digest")


def validate_public_identity(data: dict) -> None:
    """Use the declaration's date/SemVer rules for release acceptance as well."""
    if not isinstance(data, dict) or set(data) != _PUBLIC_FIELDS or data["identity_status"] != "declared":
        raise ValueError("invalid public build identity fields")
    _validate_identity_values(data)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate build identity field")
        result[key] = value
    return result


def load_build_identity(app_dir: Path | None = None) -> BuildIdentity:
    """Called once at application creation; resolve the installed module, not cwd."""
    directory = app_dir if app_dir is not None else Path(__file__).resolve().parent
    path = directory / BUILD_INFO_NAME
    try:
        if directory.is_symlink() or path.is_symlink():
            raise ValueError("identity symlink rejected")
        try:
            with path.open("rb") as stream:
                raw = stream.read(_MAX_INFO_BYTES + 1)
        except FileNotFoundError:
            return BuildIdentity()
        if len(raw) > _MAX_INFO_BYTES:
            raise ValueError("identity document too large")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
        validate_declaration(data)
        if source_tree_digest(source_files(directory)) != data["source_tree_sha256"]:
            raise ValueError("installed source differs from build declaration")
        return BuildIdentity(**{name: data[name] for name in (
            "component", "product_version", "source_revision", "release_id", "build_id", "built_at")},
            identity_status="declared")
    except (OSError, ValueError, TypeError, UnicodeError):
        return BuildIdentity(identity_status="invalid")


def main() -> int:
    """Docker may omit identity for development; a bad declaration always fails."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", choices=("0", "1"), default="0")
    parser.add_argument("--validate-public", type=Path, help="Validate a version API response without loading local identity")
    args = parser.parse_args()
    if args.validate_public is not None:
        try:
            with args.validate_public.open("rb") as stream:
                raw = stream.read(_MAX_INFO_BYTES + 1)
            if len(raw) > _MAX_INFO_BYTES:
                raise ValueError("identity document too large")
            validate_public_identity(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields))
        except (OSError, ValueError, TypeError, UnicodeError):
            return 1
        return 0
    identity = load_build_identity()
    print(json.dumps(identity.public_dict()))
    return int(identity.identity_status == "invalid" or
               (args.require == "1" and identity.identity_status != "declared"))


if __name__ == "__main__":
    sys.exit(main())
