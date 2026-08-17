"""paperguard extract — Sprint 3 commit #14 完整实现.

从 docx 模板抽取格式约束，输出 requirements.json + YAML pack 骨架。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from app.core.extract import extract_template, requirements_to_pack


@click.command()
@click.argument("template", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--out", "-o", type=click.Path(dir_okay=False, path_type=str),
              default=None, help="输出 YAML 路径")
@click.option("--json-out", type=click.Path(dir_okay=False, path_type=str),
              default=None, help="输出 JSON 路径")
def extract(template: str, out: str | None, json_out: str | None) -> None:
    """从 TEMPLATE 抽取格式约束, 输出 YAML pack 骨架."""
    try:
        reqs = extract_template(template)
        if json_out:
            payload = [
                {
                    "id": r.id,
                    "category": r.category,
                    "target": r.target,
                    "property": r.property,
                    "expected": r.expected,
                    "source_part": r.source_part,
                    "evidence": r.evidence,
                    "auto_applicable": r.auto_applicable,
                }
                for r in reqs
            ]
            Path(json_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            click.echo(f"JSON written to {json_out} ({len(reqs)} requirements)")
        if out:
            pack = requirements_to_pack(reqs, pack_id="EXTRACTED",
                                        name="Extracted Pack", version="0.1.0")
            # RulePack dataclass → dict for yaml dump
            rules_payload = []
            for r in pack.rules:
                rules_payload.append({
                    "id": r.id,
                    "severity": r.severity.value,
                    "scope": r.scope,
                    "when": r.when,
                    "message": r.message,
                    "fix": {
                        "action": r.fix.action,
                        "target": r.fix.target,
                        "value": r.fix.value,
                    } if r.fix else None,
                })
            pack_dict = {
                "id": pack.id,
                "name": pack.name,
                "version": pack.version,
                "rules": rules_payload,
            }
            Path(out).write_text(
                yaml.dump(pack_dict, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            click.echo(f"YAML pack written to {out} ({len(rules_payload)} rules)")
        if not out and not json_out:
            click.echo(f"Extracted {len(reqs)} requirements (no output file specified)")
        sys.exit(0)
    except Exception as exc:
        click.echo(f"Error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(2)


__all__ = ["extract"]
