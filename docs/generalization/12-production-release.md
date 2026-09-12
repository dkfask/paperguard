# Phase 12: Production Release Preparation

## Scope

Phase 12 prepares a production release without deploying it. The release
preflight uses the existing production `docker-compose.yml`, backend/frontend
Dockerfiles, migration CLI, health endpoints, backup/restore implementation,
authentication bootstrap, and worker topology. It runs in an isolated Compose
project with a disposable volume on a hosted runner.

The manual workflow is `.github/workflows/release-preflight.yml`. It does not
push an image, change a production database, open a public endpoint, or invoke
`deploy.sh`. It only validates the production topology as a disposable
release candidate and uploads a manifest containing source/configuration
hashes and local image identity.

## Release Identity

Every real release must bind the following values before approval:

| Identity | Required evidence |
|---|---|
| Source | Full 40-character Git revision and reviewed PR head |
| Configuration | Production Compose and Dockerfile SHA-256 values |
| Artifact | Immutable registry image digests, not mutable `latest` tags |
| Schema | Migration ledger before and after the change |
| Backup | Backup manifest, timestamp, storage location, and restore check |
| Runtime | Host, Compose project, port, worker, and health probe results |
| Approval | Named production deploy approval and rollback owner |

The workflow manifest schema is
`docs/generalization/production-release-manifest.schema.json`. A preflight
manifest with local Docker image IDs is evidence for build validation only; it
is not a production artifact identity.

## Controlled Sequence

1. Freeze the source revision, configuration revision, image digests, migration
   ledger, and release owner.
2. Build and scan immutable backend, worker, and frontend images in the chosen
   registry. Do not promote an unreviewed mutable tag.
3. Run the hosted release preflight and retain its manifest, logs, and test
   report.
4. On the target host, verify the backup destination and create a fresh
   application backup before any schema or container change.
5. Record the current running image digests and compose revision as the
   rollback target.
6. Apply the release with the existing deployment mechanism only after explicit
   production authorization. Run the migration command and wait for the
   ready probe.
7. Verify login, template/package listing, generic project creation, autosave,
   validation, DOCX rendering/download, audit visibility, and worker health.
8. Stop and roll back to the recorded image/configuration pair when a release
   gate fails. Restore the pre-release backup only when data/schema state
   requires it, then re-run the ready probe and smoke checks.

Code rollback alone is not a database rollback. A backup restore must be
performed with services stopped and the existing restore command's explicit
confirmation; the restore implementation retains pre-restore paths for local
recovery.

## Test And Evidence Status

- Production Compose configuration: PASS locally with an ephemeral password.
- Release manifest schema: validated structurally by review; automated JSON
  Schema validation: **NOT RUN** because no JSON Schema validator is pinned in
  the repository.
- `deploy.sh` shell syntax: PASS locally.
- Hosted disposable production-topology preflight: **NOT RUN** in this phase;
  it is manual-only and has not been authorized for this turn.
- Production image registry push and immutable digest capture: **NOT RUN**.
- Target-host backup, representative restore, and rollback rehearsal:
  **NOT RUN**.
- Production deployment and online verification: **NOT RUN** by authorization.

## Phase Gate

Phase 12 is **GO for release-preflight implementation** and **NO-GO for
production release**. The remaining gates require a target host, immutable
registry artifacts, a real backup/restore rehearsal, explicit production
authorization, and a separately recorded Phase 13 online verification. No
production state has been changed by this phase.
