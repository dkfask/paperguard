# Phase 12: Production Release Preparation

## Scope

Phase 12 prepares a production release without deploying it. The release
preflight uses the existing production `docker-compose.yml`, backend/frontend
Dockerfiles, migration CLI, health endpoints, backup/restore implementation,
authentication bootstrap, and worker topology. It runs in an isolated Compose
project with a disposable volume on a hosted runner.

The workflow is `.github/workflows/release-preflight.yml`. It runs on the Phase
12 pull request and can also be manually dispatched with an explicit source
ref. It does not push an image, change a production database, open a public
endpoint, or invoke `deploy.sh`. It only validates the production topology as
a disposable release candidate and uploads a manifest containing
source/configuration hashes and local image identity.

The production Compose services explicitly bind the application data root and
SQLite database to `/app/data` (`PAPERGUARD_DATA_DIR` and
`PAPERGUARD_DB_PATH`). This is required because the named volume is mounted at
that path; a release must not fall back to an image-layer database.

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
- Persisted data-root binding: PASS locally; backend and worker point at the
  mounted `/app/data` volume.
- Release manifest schema: automated JSON Schema validation **PASS** locally
  with the pinned `ops/requirements.txt` validator and
  `ops/validate-release-manifest.py`; a valid manifest was accepted and a
  manifest with `production_deployment: true` was rejected.
- `deploy.sh` shell syntax: PASS locally.
- Hosted disposable production-topology preflight run `34690470963`:
  **success** on final executable source revision
  `e0c889d605e0c77090756130154c2218207f8f08`; production Compose config,
  three-image build/start, live/ready probes, real package migration command,
  manifest generation plus JSON Schema validation, secret-free identity/log
  capture, artifact upload, and teardown all passed.
- The uploaded manifest was independently parsed and checked for its required
  fields; it records three image entries and `production_deployment: false`.
- Hosted staging run `34690470772`: **success**; probes, clean migration,
  desktop/mobile generic E2E, capture, upload, and teardown passed.
- Hosted standalone generic E2E run `34690470830`: **success**.
- Existing CI matrix run `34690485516`: **success**; frontend build and Python
  3.11/3.12 backend tests all passed.
- Current Phase 13 head `2ff07d2802031e430d448cdb16cbdf27f7a4867b` was rechecked
  by CI run `34692470703`, staging run `34692442641`, and production-topology
  preflight run `34692442655`; all completed successfully. The CI frontend
  checks use Node 24 to match the locked dependency engine requirements.
- Production image registry push and immutable digest capture: **NOT RUN**.
- Target-host backup, representative restore, and rollback rehearsal:
  **NOT RUN**. The corresponding isolated local backup/restore/rollback
  rehearsal is recorded in Phase 11; no target host or production data was
  touched.
- Production deployment and online verification: **NOT RUN** by authorization.

## Phase Gate

Phase 12 is **GO for release-preflight implementation and hosted disposable
verification** and **NO-GO for production release**. The remaining gates
require a target host, immutable registry artifacts, a real backup/restore
rehearsal, explicit production authorization, and a separately recorded Phase
13 online verification. No production state has been changed by this phase.
