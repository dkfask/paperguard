# Phase 10: Automated E2E

## Scope

This phase adds an isolated browser regression for the first generic document
workflow. It does not change the thesis workflow or introduce a second document
renderer.

The covered path is:

1. An isolated manager session uploads an unseen tracked DOCX fixture and parses
   it into a reviewable candidate.
2. The manager edits the candidate name, changes a field to a choice field,
   configures its options, and confirms the template candidate.
3. A deterministic Markdown `frontmatter.rules` fixture is parsed, confirmed,
   and composed with the template into a published document package.
4. An isolated student signs in through the real UI, selects the published
   package, creates a project, and receives a dynamically generated editor from
   the confirmed schema.
5. The student enters field and section content, reloads the project, runs the
   deterministic validator, and downloads a DOCX whose anchored dynamic content,
   static body content, header, and footer are checked from the ZIP parts.
6. The browser flow reopens the saved project through the API, verifies another
   student cannot read it, and verifies the project audit actions are queryable
   by resource ID.

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
`generic-anchored.docx` fixture. `E2E_ADMIN_PASSWORD` is required; no password
is stored in the test source. The Playwright config starts a temporary backend
database, template directory, and artifact directory, with optional
`E2E_BACKEND_PORT`, `E2E_FRONTEND_PORT`, `E2E_API_BASE_URL`, and
`E2E_PYTHON_BIN` overrides for local execution.

The suite contains six scenarios: one full browser acceptance chain covering
parse, review, publish, project creation, persistence, validation, export,
isolation, and audit; plus five focused checks for parsed schema rendering,
dynamic editor blocks, autosave/reload, deterministic findings, and anchored
DOCX export. All six scenarios run in desktop Chromium and mobile Chromium.
Existing thesis lifecycle coverage remains separate and is not altered.

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

The current acceptance run then found two gaps in the test/product boundary:
the browser flow did not exercise the human-editable template name, and the
publisher did not expose choice field types or their options. The publisher now
supports `select`/`enum` review fields with comma-separated options, and the
browser path edits those values before confirmation. The export assertion also
checks the actual anchored XML rather than only the DOCX ZIP signature.

## Local Evidence

Executed from the Phase 13 worktree on 2026-09-12:

| Check | Result |
|---|---|
| Generic document E2E, desktop + mobile Chromium | 12 passed in 45.4s |
| Full local browser regression, including thesis lifecycle and smoke checks | 20 passed, 2 skipped in 2.2m |
| Full backend pytest matrix | 605 passed, 2 warnings |
| Frontend unit tests | 28 passed, 10 files |
| Frontend typecheck | PASS |
| Frontend production build | PASS |
| Paragraph splitter checks | PASS |
| Anchored renderer, parser, and audit focused tests | 9 passed, 2 warnings |
| `git diff --check` | PASS |

The backend warning output is from the installed FastAPI/Starlette TestClient
deprecation path; it is not a test failure.

The latest local browser execution included the existing thesis
`full-lifecycle.spec.ts`; its mobile project remains intentionally skipped
because that scenario is desktop-only. Staging, production deployment, and
online verification remain **NOT RUN**.

## Hosted Workflow

`.github/workflows/e2e.yml` runs on pull requests and manual dispatch. It:

- installs the backend dependencies and Node dependencies;
- generates an ephemeral admin password in the runner environment;
- installs Chromium with its system dependencies;
- runs the six generic scenarios in desktop and mobile projects;
- uploads Playwright reports when the job finishes.

The workflow is intentionally separate from the existing backend/build CI so
the browser gate can be dispatched against a phase branch without changing the
legacy CI contract. No deployment is performed by this phase.

Historical hosted evidence for the Phase 10 gate was collected at
`36a814748db3c4baee64d4185c2cbda139dd59f0`:

- Existing CI run `34678310503`: success; frontend build and Python 3.11/3.12
  jobs all passed.
- Generic E2E run `34678310659`: success; 8 tests passed across the
  desktop and mobile projects before the current full-chain expansion.
- The workflow masks its generated admin password before exporting it to the
  runner environment; hosted logs show the value as `***`.

Current hosted regression for the Phase 13 executable source
`e0c889d605e0c77090756130154c2218207f8f08`:

- CI run `34690485516`: success; frontend build, Python 3.11/3.12 backend
  tests, and loopback verifier contract passed.
- Generic E2E run `34690470830`: success.
- Disposable staging run `34690470772`: success; built-frontend desktop/mobile
  generic E2E, migration, probes, evidence capture, and teardown passed.
- Production-topology preflight run `34690470963`: success; Compose build,
  real migration command, manifest schema validation, evidence capture, and
  teardown passed.

## Phase Gate

The local browser and regression checks are green. The historical hosted CI and
E2E runs above remain valid for the Phase 10 gate, and the current executable
source is additionally covered by the hosted regression runs listed above.
