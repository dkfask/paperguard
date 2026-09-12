# Phase 6: Generic Validation

## Scope

This phase adds deterministic validation for a generic `DocumentProject`. The
validator consumes the project's frozen `DocumentPackage` references and the
stored `SpecVersion`; it does not call AI and it does not modify the existing
thesis `check_project` path.

```text
DocumentProject revision + frozen SpecVersion
                      |
                      v
          document_validation_engine
             /                 \
       findings             not_checked
             \                 /
                ValidationRun
```

## Execution contract

The engine currently executes these structured checks:

- section existence and non-empty content;
- character/word count for document, body, and targeted sections;
- keyword count;
- required metadata fields and declared AI usage;
- reference count, recent-reference count/ratio, and conservative reference
  syntax checks.

`chapter.page_break` is intentionally recorded as `renderer_required` because
the generic editor does not yet have a rendered DOCX to inspect. Manual and
reference-document requirements are recorded as `manual_or_reference_rule`.
Unknown checkers are recorded as `unsupported_checker`. A deferred rule is
never reported as passing.

The `ValidationRun` is immutable evidence. It captures project revision,
content hash, exact package/version references, summary counts, findings, and
deferred checks. Migration `0006_validation_runs` adds its append-only storage
and project/time index.

## API

| Endpoint | Behavior |
| --- | --- |
| `POST /api/document-projects/{id}/validate` | Validate the current project revision and persist a run |
| `GET /api/document-projects/{id}/validations` | List validation history subject to project access |
| `GET /api/document-projects/{id}/validations/{run_id}` | Read one validation run and verify project ownership |

Validation rejects a corrupted project/package reference instead of silently
using a different template or specification. Create/update/validate actions
remain owner-scoped for regular users and admin-readable; validation writes a
metadata-only audit event.

## Frontend flow

The generic editor now exposes a check action and a result panel. After a
successful debounced save it validates the saved revision automatically. The
panel distinguishes findings from rules requiring manual or rendered-document
review and keeps the frozen renderer/rule-engine versions visible beside the
content.

## Tests and verification

Verification completed for this phase:

- Targeted generic project, domain, and migration tests: `19 passed, 2
  warnings`.
- Full backend suite: `576 passed, 2 warnings`.
- Frontend unit suite: `23 passed` across `8` files.
- Frontend typecheck: `PASS`.
- Frontend paragraph checks: `PASS`.
- Frontend production build: `PASS`.
- Python compile check: `PASS`.
- `git diff --check`: `PASS`.
- Hosted CI for the Phase 6 implementation branch: `34675351269` green for
  head `6775e7d`; Python 3.11, Python 3.12, and frontend build all passed.
- Staging/production deployment and online verification: NOT RUN by scope.

## Explicit non-goals

- no generic auto-fix;
- no formatting validation against a rendered DOCX;
- no migration of legacy thesis check runs;
- no production database migration or deployment;
- no merge of the phase PR without explicit authorization.