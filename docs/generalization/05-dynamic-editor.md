# Phase 5: Dynamic Document Project Editor

## Scope

This phase creates the first generic user-authored project flow on top of a
published `DocumentPackage`. A project is instantiated from the package's
immutable template/spec/renderer/rule-engine references and is edited through
an editor schema derived from the pinned `TemplateDefinition`.

```text
published DocumentPackage
          |
          v
  DocumentProject + ContentBlock[]
          |
          v
  editor schema + autosave + revision guard
```

The thesis `WritingProject` and `Writer` flows are unchanged. This phase adds
parallel generic APIs and frontend routes; it does not migrate existing thesis
rows or make thesis UI depend on the new editor.

## Domain and persistence

`DocumentProject` stores:

- owner and organization context;
- the package ID and exact template/spec/renderer/rule-engine references;
- title, field values, section content, status, timestamps, and revision;
- generic `ContentBlock` records with a declared kind, text, data payload, and
  stable order.

Migration `0005_document_projects` adds a dedicated SQLite table and an owner/
updated-at index. The full project JSON is persisted together with indexed
identity and revision columns. Existing tables and legacy data are untouched.

## API contract

| Endpoint | Behavior |
| --- | --- |
| `GET /api/document-projects` | List all projects for an admin, or only the authenticated user's projects |
| `POST /api/document-projects` | Create a draft from a visible published package |
| `GET /api/document-projects/{id}` | Read a project subject to owner/admin access |
| `GET /api/document-projects/{id}/editor` | Return the project, package snapshot, and dynamic editor schema |
| `PUT /api/document-projects/{id}` | Validate and persist title/fields/sections with an optimistic revision guard |

The API rejects unknown template fields and section keys, limits a single
content save to 2 MB, limits an individual text block to 200,000 characters,
and rejects stale revisions with `409`. Required fields remain blankable while
a project is a draft; final completeness belongs to the validation phase.

Project creation requires a published, visible package. It copies the exact
package execution versions into the project, so later publication cannot
silently change an existing project's behavior. Create and update operations
emit metadata-only audit events.

## Frontend flow

Authenticated users can open `/document-projects`, choose a published package,
name a project, and enter `/document-projects/{id}`. The editor renders fields,
editable sections, content blocks, the rule list, and the frozen execution
snapshot from the API response. Changes are debounced into an update request;
the revision is sent back on the next save and conflicts are shown to the user
instead of being overwritten.

The new view is linked from student, teacher, and administrator navigation and
has Chinese/English translations. It is intentionally a focused block editor;
DOCX rendering, automatic checks, fix actions, and rich media blocks are
explicitly deferred.

## Tests and verification

Verification completed for this phase:

- `backend/tests/test_document_projects.py`: `2 passed`.
- Full backend suite: `575 passed, 2 warnings`.
- Frontend targeted component test: `1 passed`.
- Frontend unit suite: `23 passed` across `8` files.
- Frontend typecheck: `PASS`.
- Frontend paragraph checks: `PASS`.
- Frontend production build: `PASS`.
- `git diff --check`: `PASS`.
- Hosted CI for the Phase 5 branch: NOT RUN yet.
- Staging/production deployment and online verification: NOT RUN by scope.

## Explicit non-goals

- no generic validation execution or auto-fix;
- no generic DOCX renderer or artifact download;
- no migration of `WritingProject` data;
- no production database migration or deployment;
- no merge of the phase PR without explicit authorization.