#!/usr/bin/env python3
"""Validate a PaperGuard release manifest against the pinned JSON Schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON: {path}") from exc


def validate_manifest(manifest_path: Path, schema_path: Path) -> None:
    schema = _read_json(schema_path)
    manifest = _read_json(manifest_path)
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(manifest)
    except SchemaError as exc:
        raise ValueError(f"release manifest schema is invalid: {exc.message}") from exc
    except ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path) or "$"
        raise ValueError(f"release manifest is invalid at {location}: {exc.message}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("schema", type=Path)
    args = parser.parse_args()
    try:
        validate_manifest(args.manifest, args.schema)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("release manifest schema validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
