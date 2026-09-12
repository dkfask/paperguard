# Phase 8: Storage and Job Abstraction

## Scope

This phase adds durable generic render jobs and artifact metadata without
changing the thesis `WritingProject` export path. It reuses the existing
`workflow_records`, `jobs`, lease, retry, audit, and standalone worker
infrastructure instead of creating a second queue implementation.

```text
POST render-jobs
      |
      +--> workflow record: document_render_job
      +--> durable queue row: document.render
      +--> metadata-only audit event
                       |
                       v
                 worker lease
                       |
                       v
          frozen project/package snapshot
                       |
                       v
             generic DOCX renderer
                       |
                       v
       controlled artifact file + metadata record
```

## Domain and storage contract

`RenderJob` is a structured, mutable lifecycle record containing the project
revision/content hash, exact template/spec/renderer/rule-engine references,
requester, force flag, retry state, and a private execution snapshot. The
snapshot includes the project, package, template, template version, and spec
version so a queued job cannot silently change behavior if current records are
later published or repaired.

`Artifact` is immutable metadata containing project/job identity, DOCX filename,
content type, size, checksum, renderer version, and a controlled `storage_key`.
The storage key and local path are removed from public projections. Downloads
resolve the key under `PAPERGUARD_ARTIFACT_DIR` and reject traversal, wrong
extensions, and paths that resolve outside the controlled directory.

No new SQL table is introduced in this phase. The existing workflow-record JSON
boundary stores the typed records and the existing jobs table supplies leases,
attempt counts, retries, and completion state. This keeps current backup and
restore behavior compatible while leaving a clear seam for a later object
storage adapter.

## API and worker behavior

| Endpoint | Behavior |
| --- | --- |
| `POST /api/document-projects/{id}/render-jobs` | Run deterministic validation, then queue a frozen render job; hard failures require `force=true` |
| `GET /api/document-projects/{id}/render-jobs` | List the project owner's/admin's render jobs without snapshots or paths |
| `GET /api/document-projects/{id}/render-jobs/{job_id}` | Read status and artifact metadata subject to project access |
| `POST /api/document-projects/{id}/render-jobs/{job_id}/retry` | Manager-only retry of a failed or queued job |
| `GET /api/document-projects/{id}/render-jobs/{job_id}/artifact` | Download a completed DOCX after identity, status, and path checks |

The existing synchronous `POST /api/document-projects/{id}/render` remains
available for Phase 7 compatibility. The new route is the durable path for
larger documents and worker-backed deployments. `run_once()` now consumes the
new `document.render` job type alongside archive, thesis export, and outbox
jobs. Startup recovery requeues generic records left in `running` after a
process restart.

Duplicate queued/running requests for the same project revision, content hash,
and force mode return the existing render job. A worker failure records a
bounded error code and audit event, then the existing queue retry policy decides
whether to requeue or park the job.

## Tests and verification

Verification completed locally for this phase:

- Generic project render-job API/worker round trip: `5 passed, 2 warnings` in
  `backend/tests/test_document_projects.py`.
- Full backend suite: `579 passed, 2 warnings`.
- Frontend unit suite: `24 passed` across `9` files.
- Frontend typecheck: `PASS`.
- Frontend paragraph checks: `PASS`.
- Frontend production build: `PASS`.
- Python compile check: `PASS`.
- `git diff --check`: `PASS`.
- Hosted GitHub Actions run `34676620913` for PR `#15`: `PASS`; Python 3.11,
  Python 3.12, and frontend build all passed for remote head
  `4d710de8d5c1da330f9e71e75c58fd7f15a67c93`. GitHub emitted only the
  existing Node.js 20 action deprecation annotations.
- Staging/production deployment and online verification: NOT RUN by scope and
  authorization policy.

## Risks carried forward

- SQLite workflow JSON remains suitable for the current deployment scale but
  needs an explicit object-storage/retention adapter before large artifacts or
  multiple workers are used at production scale.
- Artifact cleanup and retention policy are not yet generic; orphan recovery,
  expiry, and quota enforcement belong to the security/migration and operations
  phases.
- Generic rich-media content remains subject to the Phase 7 renderer fidelity
  boundary.

## Explicit non-goals

- no replacement of the existing worker queue;
- no migration of thesis export jobs or legacy project data;
- no public exposure of job snapshots, local paths, or storage keys;
- no production database migration, deployment, or online verification;
- no merge of the phase PR without explicit authorization.
