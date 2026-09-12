# Phase 4: DocumentPackage Persistence

## Scope

This phase composes the confirmed Phase 2 template candidate and Phase 3
specification candidate into a published generic document package. The
existing thesis `templates` and `requirement_sets` stores remain unchanged.

```text
confirmed template candidate + confirmed spec candidate
                         |
                         v
                 package_engine
                         |
       DocumentTemplate + TemplateVersion
       SpecSet + SpecVersion
                         |
                         v
                 DocumentPackage
                         |
                         v
           SQLite immutable aggregate snapshot
```

## Persistence contract

The new `document_packages` table stores the package and the complete
template/spec aggregate snapshot in one JSON payload. The row has explicit
version reference columns and a unique composition constraint over template
version, spec version, renderer version, and rule-engine version.

Writes are insert-only. A duplicate composition is rejected rather than
overwritten. The schema ledger adds `0004_document_packages`; existing data is
not transformed and existing legacy stores remain the source of truth for
thesis APIs.

## API

| Endpoint | Behavior |
| --- | --- |
| `POST /api/document-packages` | Manager-only publish of two confirmed candidates |
| `GET /api/document-packages` | Authenticated list of published packages visible to the user |
| `GET /api/document-packages/{id}` | Authenticated read of the complete package snapshot |

Publication revalidates both candidates, requires `confirmed` status, requires
the template source asset to be security-validated and present under the
controlled template source directory, and emits a metadata-only audit event.
Parse candidates are persisted as server-owned workflow records. The JSON sent
back to the browser is only an editable review projection: confirmation binds
the submitted review to the stored candidate identity and source asset, while
publication reloads both confirmed candidates from the server. Template and
specification candidates must also carry the same organization context; the
published package inherits that context rather than the caller's request body.
The returned package pins `generic-docx-v1` and `generic-check-v1` by default;
both can be changed only at publication time and are then immutable.

## Tests

`backend/tests/test_document_packages.py` covers SQLite round-trip,
duplicate-composition rejection, the complete parse/confirm/publish/read flow,
package listing, and audit behavior. The existing migration CLI test now
asserts the new schema ledger entry.

Verification recorded for this phase:

- Phase 4 targeted regression: `16 passed, 2 warnings`.
- Full backend suite after final Phase 4 changes: `573 passed, 2 warnings`.
- Frontend unit tests: `22 passed` across `7` files.
- Frontend typecheck: `PASS`.
- Frontend paragraph checks: `PASS`.
- Frontend production build: `PASS`.
- `git diff --check` after final Phase 4 changes: `PASS`.
- Hosted CI for the Phase 4 branch: `34674196409` **success** on head
  `81fe84570c9827ad63a42485d0394f17b738ce84` (Python 3.11/3.12 and frontend
  build).
- Staging/production deployment and online verification: NOT RUN by scope.

## Explicit non-goals

- no dynamic editor or project creation from a package;
- no generic validation execution;
- no generic DOCX rendering;
- no production database migration or deployment;
- no merge of the phase PR without explicit authorization.
