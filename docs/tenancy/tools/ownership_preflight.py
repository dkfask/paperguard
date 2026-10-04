#!/usr/bin/env python3
"""Read-only ownership admission for an offline rehearsal, never a production grant.

Reuses the existing source readers/root resolver. Reports only opaque references,
counts and reason codes. Verification recomputes the complete decision; a stored
GO or an input fingerprint alone is not trusted authorization.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import backfill_migrate as migration
import tenant_map_candidates as candidates

VERSION = "ownership-preflight-v1"
GLOBAL_TABLES = {"schema_migrations", "users", "tenants", "tenant_memberships",
                 "platform_role_bindings", "sessions", "auth_sessions",
                 "email_verification_codes", "mini_program_bindings", "mini_program_tickets"}
RESOURCE_TABLES = set(migration.TABLE_MAP) - {"organization_units"}
RESOURCE_TABLES |= {"document_packages", "document_projects", "validation_runs", "check_runs",
                   "workflow_events", "policy_audit_events", "jobs", "audit_events", "ai_requests",
                   "ai_usage", "ai_quota_reservations", "system_settings", "push_quotas", "saas_provisioning"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def reference(entity, identifier) -> str:
    return digest([entity, str(identifier)])[:24]


def snapshot(source: Path) -> dict:
    files = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(source) + suffix)
        if path.exists():
            st = path.stat()
            files[suffix or "db"] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return files


def source_identity(source: Path) -> dict:
    # Does not instantiate Store (its constructor executes DDL). Immutable URI
    # avoids creation of SQLite WAL/SHM files; nonempty journals are rejected.
    with sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        schema = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        names = {r[0] for r in schema}
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")] if "schema_migrations" in names else []
    return {"schema_digest": digest(schema), "schema_versions": versions, "tables": sorted(names)}


def preflight(source: str | Path, mapping: str | Path) -> dict:
    source, mapping = Path(source).resolve(), Path(mapping).resolve()
    if not source.is_file() or not mapping.is_file() or source == mapping:
        raise ValueError("INPUT_INVALID")
    before = snapshot(source)
    if any(before.get(s, {}).get("size", 0) for s in ("-wal", "-journal")):
        raise ValueError("SOURCE_REQUIRES_OFFLINE_SNAPSHOT")
    raw = json.loads(mapping.read_text())
    if not isinstance(raw, dict): raise ValueError("MAPPING_INVALID")
    identity = source_identity(source)
    if not {"users", "organization_units"} <= set(identity["tables"]):
        raise ValueError("SOURCE_SCHEMA_INCOMPLETE")
    findings = []
    def finding(entity, identifier, code, bucket="quarantine", candidate_count=0):
        findings.append({"entity": entity, "ref": reference(entity, identifier), "code": code,
                         "bucket": bucket, "candidate_count": candidate_count})
    tmap = raw.get("tenant_map", {})
    if not isinstance(tmap, dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v for k,v in tmap.items()):
        raise ValueError("MAPPING_INVALID")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        mapping_ok = candidates.check(str(source), str(mapping), quiet=True) == 0
    if not mapping_ok: finding("mapping", "structure", "MAPPING_INVALID")
    if raw.get("_status") != "CONFIRMED" or not isinstance(raw.get("approval_ref"), str) or not raw["approval_ref"].strip():
        finding("mapping", "approval", "MAPPING_APPROVAL_REQUIRED", "pending_ruling")
    orgs = migration.read_orgs(str(source)); users = migration.read_users(str(source))
    # Existing reader now retains organization state as well as the parent chain.
    def org_scope(oid):
        tid, _ = migration.org_to_tenant(str(oid), orgs, tmap)
        return tid
    for oid in sorted(orgs):
        if not org_scope(oid): finding("organization_units", oid, "ORGANIZATION_UNRESOLVED_OR_INACTIVE")
    rows = {name: migration.read_scoped(str(source), name) for name in identity["tables"]}
    tenants = {r["id"]: r for r in rows.get("tenants", [])}
    memberships = rows.get("tenant_memberships", [])
    bindings = rows.get("platform_role_bindings", [])
    scopes = {}; identities = {}
    for u in users:
        uid = u["id"]; scope = set()
        own_members = [m for m in memberships if m.get("user_id") == uid]
        approved_platform = any(b.get("user_id")==uid and b.get("status")=="active" and b.get("approval_ref") and b.get("approved_by") for b in bindings)
        if not own_members and not approved_platform and u.get("role") not in migration.ROLE_CODE and not (u.get("role")=="user" and u.get("role_application_status") in {"unselected","rejected"}):
            finding("users", uid, "ACCOUNT_ROLE_MIGRATION_UNDEFINED")
        if u.get("status") not in {"active","pending","rejected"}:
            finding("users", uid, "ACCOUNT_STATE_MIGRATION_UNDEFINED")
        if u.get("teacher_id"):
            finding("users", uid, "SUPERVISION_RULE_APPROVAL_REQUIRED", "pending_ruling")
        for field in ("username", "email"):
            value = str(u.get(field) or "").strip().casefold()
            if value:
                key = (field, value)
                if key in identities and identities[key] != uid:
                    finding("users", uid, "ACCOUNT_IDENTITY_COLLISION")
                    finding("users", identities[key], "ACCOUNT_IDENTITY_COLLISION")
                identities[key] = uid
        if u.get("role") == "admin" and not any(b.get("user_id")==uid and b.get("status")=="active" and b.get("approval_ref") and b.get("approved_by") for b in bindings):
            finding("users", uid, "LEGACY_ADMIN_APPROVAL_REQUIRED", "pending_ruling")
        context = migration._json(u.get("organization_context")); hints = {context[k] for k in ("school_id","college_id","major_id") if context.get(k)}
        hinted = {org_scope(h) for h in hints}
        if None in hinted or len(hinted)>1:
            finding("users", uid, "ACCOUNT_ORGANIZATION_CONFLICT", candidate_count=len(hinted-{None}))
        else: scope |= hinted
        for m in memberships:
            if m.get("user_id") != uid: continue
            tid = m.get("tenant_id")
            if tid not in set(tmap.values()) or tid not in tenants or tenants[tid].get("status") not in {"active","draft"}:
                finding("tenant_memberships", m.get("id"), "MEMBERSHIP_TENANT_INVALID")
            else: scope.add(tid)  # Multiple legitimate memberships != duplicate accounts.
        if not scope:
            bucket = "unestablished" if (u.get("role")=="user" and u.get("role_application_status") in {"rejected","unselected"}) or approved_platform else "quarantine"
            finding("users", uid, "ACCOUNT_OWNERSHIP_MISSING", bucket)
        scopes[uid] = scope
    resolved = {}; counts = {"users": len(users), "organization_units": len(orgs)}
    def row_scope(table, row):
        body = migration._json(row.get("data")); hints = []
        direct = row.get("tenant_id") or body.get("tenant_id")
        if direct: hints.append({direct} if direct in set(tmap.values()) else set())
        ctx = migration._json(body.get("organization_context"))
        org_hints = [ctx[k] for k in ("school_id","college_id","major_id") if ctx.get(k)]
        if body.get("scope_id") and body.get("scope") in {"school","college","major"}: org_hints.append(body["scope_id"])
        for oid in org_hints:
            tid = org_scope(oid); hints.append({tid} if tid else set())
        owner = body.get("owner_id") or row.get("owner_id") or row.get("user_id")
        if owner: hints.append(scopes.get(owner,set()))
        parent = body.get("project_id") or row.get("project_id")
        if parent: hints.append({resolved[("projects",parent)]} if ("projects",parent) in resolved else set())
        if not hints: return set()
        intersection = set.intersection(*hints)
        return intersection
    ordered = ["projects"] + sorted(RESOURCE_TABLES - {"projects"})
    for table in ordered:
        if table not in rows: continue
        counts[table] = len(rows[table])
        for row in rows[table]:
            scope = row_scope(table,row)
            if len(scope) != 1:
                finding(table, row.get("id",row.get("key","unknown")), "RESOURCE_OWNERSHIP_AMBIGUOUS" if len(scope)>1 else "RESOURCE_OWNERSHIP_MISSING_OR_CONFLICT", candidate_count=len(scope))
            else: resolved[(table,row.get("id"))] = next(iter(scope))
    for table in sorted(set(rows)-GLOBAL_TABLES-RESOURCE_TABLES-{"organization_units"}):
        if rows[table]: finding(table,"unclassified","UNREGISTERED_NONEMPTY_RESOURCE")
    # Detect source or mapping races; no previous snapshot can authorize new data.
    if before != snapshot(source): raise ValueError("SOURCE_CHANGED_DURING_PREFLIGHT")
    map_hash = hashlib.sha256(mapping.read_bytes()).hexdigest()
    if digest(raw) != digest(json.loads(mapping.read_text())): raise ValueError("MAPPING_CHANGED_DURING_PREFLIGHT")
    root = Path(__file__).resolve().parents[3]
    sha = subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"], text=True).strip()
    rule_files = [Path(__file__),Path(migration.__file__),Path(candidates.__file__)]
    binding = {"source_files":before,"source_path_digest":digest(str(source)),"schema_digest":identity["schema_digest"],
               "schema_versions":identity["schema_versions"],"mapping_sha256":map_hash,"source_sha":sha,
               "rule_version":VERSION,"rules_digest":digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in rule_files})}
    findings = sorted({digest(f):f for f in findings}.values(),key=lambda f:(f["entity"],f["ref"],f["code"]))
    blockers = [f for f in findings if f["bucket"] != "unestablished"]
    report = {"version":VERSION,"purpose":"offline_rehearsal_only","production_authorized":False,
              "decision":"GO_REHEARSAL" if not blockers else "BLOCKED","binding":binding,"counts":counts,
              "resolved_resource_count":len(resolved),"findings":findings}
    report["admission_digest"] = digest(report)
    return report


def verify_admission(report: dict, source, mapping) -> bool:
    current = preflight(source,mapping)
    return current["decision"] == "GO_REHEARSAL" and report == current


def main():
    if "--self-test" in sys.argv:
        root = Path(__file__).resolve().parents[3]
        run = subprocess.run([sys.executable, "-m", "pytest", str(root/"backend/tests/test_ownership_preflight.py"), "-q"], cwd=root, capture_output=True, text=True)
        print(run.stdout)
        import re
        counts = {name: sum(int(n) for n in re.findall(r"(\d+) " + name + r"\b", run.stdout.splitlines()[-1] if run.stdout.splitlines() else "")) for name in ("passed", "failed", "error", "errors", "skipped")}
        total = sum(counts.values())
        if total < 1:
            print("自检结果不可解析，禁止空洞通过")
            return 2
        print(f"自检: {counts['passed']}/{total}")
        return run.returncode
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--source",required=True);p.add_argument("--tenant-map",required=True);p.add_argument("--output");p.add_argument("--verify")
    args=p.parse_args()
    try:
        protected = {Path(args.source).resolve(),Path(args.tenant_map).resolve(),*[Path(str(Path(args.source).resolve())+s) for s in ("-wal","-shm","-journal")]}
        if args.output:
            output = Path(args.output).resolve()
            if output in protected or (output.exists() and any(p.exists() and output.samefile(p) for p in protected)):
                raise ValueError("OUTPUT_WOULD_OVERWRITE_INPUT")
        result=preflight(args.source,args.tenant_map)
        if args.verify:
            valid=verify_admission(json.loads(Path(args.verify).read_text()),args.source,args.tenant_map)
            print(json.dumps({"admission_valid":valid,"production_authorized":False}));return 0 if valid else 1
        encoded=json.dumps(result,ensure_ascii=False,indent=2)
        if args.output:Path(args.output).write_text(encoded)
        else:print(encoded)
        return 0 if result["decision"]=="GO_REHEARSAL" else 1
    except (ValueError,OSError,sqlite3.DatabaseError,TypeError,KeyError,json.JSONDecodeError):
        print(json.dumps({"decision":"BLOCKED","code":"PREFLIGHT_INPUT_OR_SNAPSHOT_INVALID","production_authorized":False}));return 2

if __name__=="__main__":raise SystemExit(main())
