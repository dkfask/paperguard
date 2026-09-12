# Phase 10: Automated E2E

## Scope

This phase adds an isolated browser regression for the first generic document
workflow. It does not change the thesis workflow or introduce a second document
renderer.

The covered path is:

1. An isolated admin session parses a repository DOCX fixture.
2. A deterministic Markdown `frontmatter.rules` fixture is parsed without AI.
3. Both candidates are confirmed and composed into a published document package.
4. An isolated student signs in through the real UI.
5. The student selects the published package, creates a project, and receives a
   dynamically generated editor from the parsed section schema.
6. Content is autosaved, reloaded, deterministically validated, and exported as
   a DOCX download.

The test bootstrap uses API calls only for isolated account and package setup.
The system under test remains the real FastAPI server, Vite proxy, Vue routes,
browser session storage, editor, validation endpoint, and DOCX download. The
template and specification are unique per test run so stale local data cannot
make a package selector ambiguous.

## Test Design

The browser interactions are kept in:

- `frontend/e2e/pages/document-projects.page.ts`
- `frontend/e2e/pages/document-project-editor.page.ts`
- existing `frontend/e2e/pages/paperguard.page.ts` for login

The API fixture is in `frontend/e2e/document-platform.api.ts`. It reads the
DOCX path from `E2E_TEMPLATE_PATH` when supplied and otherwise uses the tracked
minimal fixture. `E2E_ADMIN_PASSWORD` is required; no password is stored in
the test source. The Playwright config starts a temporary backend database,
template directory, and artifact directory, with optional
`E2E_BACKEND_PORT`, `E2E_FRONTEND_PORT`, `E2E_API_BASE_URL`, and
`E2E_PYTHON_BIN` overrides for local execution.

The suite contains four independent points:

- parsed package schema opens a dynamic editor;
- content survives autosave and reload;
- empty content produces the deterministic rule finding;
- valid content produces a non-empty DOCX download.

The same four points run in desktop Chromium and mobile Chromium. Existing
thesis lifecycle coverage remains separate and is not altered.

## Fix Loop

The first isolated run found that the UI Vite process was proxying to an
already-running port instead of the isolated backend. The config now passes the
selected backend URL to the Vite web server and accepts alternate ports.

The next run found a product defect: the generic editor rendered the snapshot
labels but did not copy `template_version_id`, `spec_version_id`,
`renderer_version`, or `rule_engine_version` from the project response. The
editor now binds those immutable project values before rendering the snapshot
panel. The package selector was also narrowed by the unique specification name
to make duplicate local data diagnosable rather than silently selecting an
arbitrary package.

## Local Evidence

Executed from the Phase 10 worktree on 2026-09-12:

| Check | Result |
|---|---|
| Generic E2E, desktop Chromium | 4 passed in 15.7s |
| Generic E2E, mobile Chromium | 4 passed in 16.9s |
| Existing frontend smoke, desktop/mobile Chromium | 6 passed in 10.9s |
| Frontend unit tests | 24 passed, 9 files |
| Frontend typecheck | PASS |
| Frontend production build | PASS |
| Paragraph splitter checks | PASS |
| Related backend document-project/renderer tests | 6 passed, 2 warnings |
| `git diff --check` | PASS |

The backend warning output is from the installed FastAPI/Starlette TestClient
deprecation path; it is not a test failure.

The following were not part of this phase's local execution:

- Full backend matrix: **NOT RUN** in this phase; the related API and renderer
  tests were run locally and the complete matrix is delegated to hosted CI.
- Existing thesis `full-lifecycle.spec.ts`: **NOT RUN**; it requires private
  user-provided thesis files and `E2E_ADMIN_PASSWORD` and is intentionally not
  mixed into the generic document fixture.
- Staging, production deployment, and online verification: **NOT RUN**.

## Hosted Workflow

`.github/workflows/e2e.yml` runs on pull requests and manual dispatch. It:

- installs the backend dependencies and Node dependencies;
- generates an ephemeral admin password in the runner environment;
- installs Chromium with its system dependencies;
- runs the four generic tests in desktop and mobile projects;
- uploads Playwright reports when the job finishes.

The workflow is intentionally separate from the existing backend/build CI so
the browser gate can be dispatched against a phase branch without changing the
legacy CI contract. No deployment is performed by this phase.

Hosted evidence for the final Phase 10 head
`36a814748db3c4baee64d4185c2cbda139dd59f0`:

- Existing CI run `34678310503`: success; frontend build and Python 3.11/3.12
  jobs all passed.
- Generic E2E run `34678310659`: success; 8 tests passed across the
  desktop and mobile projects.
- The workflow masks its generated admin password before exporting it to the
  runner environment; hosted logs show the value as `***`.

## Phase Gate

Local browser and regression checks are green. Phase 10 is **GO pending hosted
CI** for the exact PR head. Phase 11 must not start until the hosted workflow
and the existing CI matrix are green on that head.
