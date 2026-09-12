# Phase 11: Staging

## Scope

Phase 11 provides a disposable staging topology for the DOCX-first generic
document platform. It reuses the existing backend and frontend Dockerfiles,
the Nginx `/api` and `/media` proxy, the SQLite data volume, the standalone
worker, and the existing live/ready probes. The default production
`docker-compose.yml` and `deploy.sh` are unchanged.

`docker-compose.staging.yml` intentionally has no repository `.env` loading.
The staging admin password is required through
`PAPERGUARD_STAGING_ADMIN_PASSWORD`, the HTTP port defaults to `18080`, and
the data volume defaults to `paperguard_staging_data`. This prevents a local
staging rehearsal from silently reusing production-style credentials or the
default application volume.

## Release Manifest

| Identity | Phase 11 value |
|---|---|
| Source revision | Recorded after the staging branch commit |
| Target revision | `phase/11-staging` PR head |
| Artifact identity | Docker image digests recorded after build |
| Configuration revision | `docker-compose.staging.yml` plus environment variable names only |
| Schema | Existing migration ledger, clean volume and existing-data path both required |
| Backup identity | Staging backup manifest recorded before destructive rehearsal, if used |
| Rollback target | Previous staging image digest and previous compose revision |
| Runtime/public endpoint | Disposable local staging endpoint `http://127.0.0.1:18080` unless overridden |
| Authorization | User-authorized staging phase; production deployment is out of scope |

No secret, token, database contents, or private user document is recorded in
this document.

## Staging Gates

Run from the repository root with a generated or externally managed password:

```bash
export PAPERGUARD_STAGING_ADMIN_PASSWORD='use-a-local-secret-with-letters-and-digits'
docker compose -p paperguard-staging -f docker-compose.staging.yml config
docker compose -p paperguard-staging -f docker-compose.staging.yml up -d --build
curl -fsS http://127.0.0.1:18080/api/health/live
curl -fsS http://127.0.0.1:18080/api/health/ready
docker compose -p paperguard-staging -f docker-compose.staging.yml ps
```

The staging browser gate can target the built Nginx endpoint without starting
Vite or a second FastAPI process:

```bash
TEST_BASE_URL=http://127.0.0.1:18080 \
E2E_API_BASE_URL=http://127.0.0.1:18080 \
E2E_ADMIN_PASSWORD="$PAPERGUARD_STAGING_ADMIN_PASSWORD" \
npm --prefix frontend run test:e2e -- e2e/document-platform.spec.ts --project=chromium --project=mobile-chrome
```

`TEST_BASE_URL` disables Playwright's local `webServer` entries. The API
fixture then uses the same public staging proxy, so the browser and setup both
exercise the built frontend route rather than bypassing it.

`.github/workflows/staging.yml` runs the same topology on a disposable GitHub
runner for the Phase 11 pull request or a later manual dispatch. It builds both images, verifies the two health probes through Nginx,
runs the migration ledger command in the backend container, runs desktop and
mobile generic E2E through port 18080, captures container identity and logs,
then removes the disposable containers and volume. This is a staging
verification workflow, not a production deployment.

Before applying any schema change to a non-disposable staging database, run
the existing backup command and record the backup manifest. Validate both a
clean schema and a representative existing-data copy. Code rollback alone is
not a database rollback.

## Local Evidence

The following must be filled with output from the exact branch head before
declaring the phase complete:

- Compose config validation: PASS locally.
- Docker image build: **NOT RUN locally**; the Docker registry timed out while
  resolving `python:3.12-slim` and `nginx:alpine`.
- Backend live/ready probes through Nginx: **NOT RUN locally**.
- Staging browser E2E through the built frontend: **NOT RUN locally**.
- Clean-volume migration: **NOT RUN locally**.
- Representative existing-data migration/restore: **NOT RUN**
- Staging rollback rehearsal: **NOT RUN**
- Production deployment and online verification: **NOT RUN** by authorization

## Phase Gate

Phase 11 is **NO-GO** until the disposable staging compose, health probes,
generic browser flow, clean migration, and rollback evidence are recorded on
the exact PR head. Phase 12 production release remains blocked until separate
production authorization, backup, immutable artifact, and rollback approvals
exist.
