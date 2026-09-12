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

The backend and worker explicitly use `/app/data/paperguard.db` and
`/app/data` for the database and persisted assets. This matches the mounted
volume; the path binding is part of the staging and release configuration.

## Release Manifest

| Identity | Phase 11 value |
|---|---|
| Source revision | `8ad97a9cbbdc49be6eeeaf171dadbcbdc8b6a009` |
| Target revision | Executable PR #18 head used for hosted verification |
| Artifact identity | Captured by hosted run `34679103458` before teardown |
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
runner for the Phase 11 pull request or a later manual dispatch. It builds both
images, verifies the two health probes through Nginx,
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

## Hosted Evidence

The first Phase 11 staging run `34678675574` failed during the production
frontend image build because `frontend/Dockerfile` omitted the tracked
`frontend/public` directory. Vite therefore could not resolve `/logo.png` and
`/avatar.png`. The failure was fixed in the one-line Dockerfile change and was
not bypassed by removing tests.

The final evidence is bound to the executable PR #18 head used for hosted
verification, `8ad97a9cbbdc49be6eeeaf171dadbcbdc8b6a009`. The documentation
commit containing this record is report-only and follows that tested source
revision.

- Hosted staging run `34679103458`: **success**. Image build/start, live/ready
  probes, clean migration ledger, built-frontend desktop/mobile generic E2E,
  identity/log capture, report upload, and teardown all passed.
- Hosted standalone generic E2E run `34679103450`: **success**; the four
  generic tests passed in both configured browser projects.
- Existing CI matrix run `34679215196`: **success**; frontend build and Python
  3.11/3.12 backend tests all passed.
- The staging runner captured the container/image identity and Playwright
  report before removing the disposable containers and named volume. Password
  output remained masked.

## Phase Gate

The disposable staging execution is **GO** on the exact PR head. Phase 11 as a
production release gate remains **NO-GO** because representative existing-data
restore and rollback rehearsal are still **NOT RUN**. Phase 12 may prepare the
release and rollback controls, but production deployment and online verification
remain blocked until separate authorization, backup, immutable artifact, and
rollback approvals exist.