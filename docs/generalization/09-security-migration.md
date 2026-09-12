# Phase 9: Security and Migration

## Scope

This phase hardens the generic document path introduced in Phases 1-8. It
does not change the thesis routes or replace the existing authentication,
organization, audit, SQLite, workflow, or DOCX export implementations.

The phase covers:

- generic package and project organization-scope authorization;
- content-free audit evidence for denied project access;
- safe public projections that do not expose template or artifact storage keys;
- revalidation of the source DOCX asset at package publication;
- symlink/path escape protection for template sources;
- render snapshot integrity and generated DOCX resource limits;
- an idempotent SQLite schema ledger/index migration.

## Authorization Boundary

Generic resources now use one shared authorization predicate:

```text
request actor
    |
    +-- admin ------------------------------> allow
    +-- package ----------------------------> global, same named scope,
    |                                         or scoped college-admin descendant
    +-- project ----------------------------> owner, admin, or scoped college-admin
    +-- render job/artifact/validation -----> project access predicate
    |
    +-- otherwise --------------------------> 404 + metadata-only denial audit
```

Scope values are compared only under the same key (`school_id` with
`school_id`, `college_id` with `college_id`, and so on). A matching string in
two different scope fields is not treated as authorization. A normal student
or teacher cannot use the organization-ID fallback; that fallback is reserved
for `college_admin`, whose visible organization set already includes allowed
descendants through the existing organization service.

The existing owner-only student project behavior remains valid. A scoped
`college_admin` can list and inspect projects in its visible organization,
while a different organization receives the same not-found response as an
unknown resource. Denials store only actor, resource type/id, organization
scope, action, result, and a fixed reason; document content and storage paths
are not recorded.

## File and Asset Controls

Template sources continue to use generated flat `.docx` keys. Resolution now
requires the resolved file's parent to be the resolved template directory, so
a symlink cannot redirect a valid-looking key outside the controlled root.

Publication rechecks all of the following against the stored source file:

- `scan_status == validated`;
- template-source purpose;
- OOXML DOCX content type;
- definition/source-asset key equality;
- exact stored byte length;
- SHA-256 checksum;
- DOCX ZIP structure, entry count, decompressed size, and compression ratio.

The parse endpoint computes the asset metadata from the stored bytes. This is
important because comment stripping may make stored bytes differ from the
original upload bytes.

The public package/editor projection omits `source_docx` and source asset
storage keys. The durable render job already omitted artifact storage keys;
Phase 9 also validates the rendered artifact as a bounded, structurally valid
DOCX before publishing it.

## Snapshot Integrity

`RenderJob.snapshot_hash` is the canonical SHA-256 hash of the full typed
project/package/template/spec snapshot. A new job stores it at queue time and
the worker recomputes it before reconstructing the typed snapshots. A mismatch
fails the job and publishes no artifact. Legacy records without the field are
bootstrapped on their first execution for backward compatibility; newly queued
records always contain the hash.

This is an integrity and accidental-corruption check, not a substitute for
database access control or encrypted-at-rest storage.

## Migration

`0007_document_scope_indexes` is appended to the existing migration ledger.
Store initialization applies the following idempotent indexes to both new and
existing databases:

- `idx_document_projects_package_owner` on package/owner/update ordering;
- `idx_workflow_records_document_project` on workflow kind, JSON project id,
  and update ordering.

No data rewrite, destructive DDL, or production migration was performed. The
existing JSON aggregate format remains the source of truth. Before a future
relational split, a migration must add a dual-read/dual-write period,
row-count and checksum reconciliation, backup/restore rehearsal, and a
rollback decision point. A SQLite backup remains required before applying a
future production migration.

## Reuse and Non-Goals

Reused capabilities:

- `Auth` and `Organization` context/visibility services;
- `Audit` metadata ledger;
- `Store` schema migration ledger and workflow transactions;
- generic `DocumentPackage`/`DocumentProject` snapshots;
- the existing upload validator and generic DOCX renderer;
- the existing durable job/lease worker.

Not included in this phase:

- production or staging deployment;
- online verification;
- object storage, artifact retention, or CDN;
- changing thesis authorization semantics;
- a new SQL table for generic assets/jobs;
- AI calls or AI-generated authorization decisions.

## Verification Evidence

Executed locally on the Phase 9 branch:

- Backend full suite: `584 passed, 2 warnings`.
- Phase 9 security/migration suite: `5 passed`.
- Frontend unit suite: `24 passed` across `9` files.
- Frontend typecheck: `PASS`.
- Frontend production build: `PASS`.
- Frontend paragraph checks: `PASS`.
- Python compile check: `PASS`.
- `git diff --check`: `PASS` before the final list-filter adjustment; it must
  be rerun for the final commit.
- Hosted GitHub Actions for the Phase 9 branch: NOT RUN yet; it is required
  after the branch is pushed.
- Staging/production deployment and online verification: NOT RUN by scope and
  authorization policy.

## Phase Gate

Local evidence supports `GO` to open the Phase 9 PR and run the hosted CI
matrix. The phase is not release-ready until the remote head has a green
Python 3.11/3.12 and frontend build run. Phase 10 remains blocked until that
gate is satisfied.
