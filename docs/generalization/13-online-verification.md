# Phase 13: Online Verification

## Scope

Phase 13 provides a guarded, non-destructive verification path for an
explicitly authorized PaperGuard environment. Login creates a short-lived
session and authenticated requests update session metadata; the verifier does
not deploy, migrate, create a project, change content, render a document, or
alter business data.

The local verifier is `ops/online-verify.sh`. The hosted wrapper is the
manual-only `.github/workflows/online-verification.yml` workflow. The workflow
runs only from protected `main`, uses the protected
`paperguard-online-verification` environment, and reads the target URL and
credentials from environment secrets at run time. No target or credential is
accepted as a caller-controlled workflow input.

## Non-Destructive Checks

The verifier checks the public frontend assets and health endpoints, then logs
in with the supplied administrator account and reads:

- `/logo.png` and `/avatar.png`;
- `/api/health/live` and `/api/health/ready`;
- `/api/auth/me`;
- `/api/document-packages`;
- `/api/audit?limit=1`.

It validates response shape, never prints the bearer token or password, and
writes only a redacted evidence JSON containing the target URL, source
revision, check names/statuses, and timestamp. HTTPS is required except for
loopback rehearsals. The legacy `/api/templates` route is intentionally not
used because its dependency can lazily seed the default template.

## Runbook

Run only after the target environment and credentials have been explicitly
approved:

```bash
PAPERGUARD_ONLINE_BASE_URL='https://authorized.example.invalid' \
PAPERGUARD_ONLINE_ADMIN_USERNAME='approved-readonly-admin' \
PAPERGUARD_ONLINE_ADMIN_PASSWORD='provided-out-of-band' \
PAPERGUARD_ONLINE_EVIDENCE_PATH="$(mktemp /tmp/paperguard-online-evidence.XXXXXX.json)" \
bash ops/online-verify.sh
```

The hosted workflow uses the same verifier and uploads the evidence as an
artifact. Configure the three `PAPERGUARD_ONLINE_*` secrets on the protected
environment, require reviewers for that environment, and restrict the
environment to `main`. A successful non-destructive check is not evidence that project autosave,
validation, DOCX rendering/download, worker processing, or rollback works in
production. Those flows require a designated test tenant or disposable
environment, explicit cleanup authorization, and a separate write-path test
plan.

## Status

- Script syntax and input fail-closed behavior: validated locally.
- Full verifier contract against a local loopback mock: **PASS**; all 8
  non-destructive checks passed and the mock service was stopped afterward.
- Hosted evidence for the final executable source revision
  `2ff07d2802031e430d448cdb16cbdf27f7a4867b`:
  - CI run `34692470703`: **success**; Python 3.11/3.12 backend tests,
    frontend checks, and loopback verifier contract passed.
  - Generic document E2E `34692442646`: **success**.
  - Disposable staging `34692442641`: **success**; probes, clean migration,
    built-frontend desktop/mobile E2E, evidence capture, and teardown passed.
  - Production-topology preflight `34692442655`: **success**; it completed
    the production Compose build/start, real migration CLI, manifest creation
    and JSON Schema validation, capture, upload, and teardown.
- This documentation-only evidence update follows the tested executable
  revision; it does not change runtime behavior.
- Actual authorized online verification: **NOT RUN**; no target URL or
  out-of-band credentials were supplied.
- Production deployment: **NOT RUN** by authorization.
- Production write-path E2E, backup restore, rollback, and online repair loop:
  **NOT RUN**.

Phase 13 tooling is **GO**. Phase 13 acceptance is **BLOCKED** pending an
authorized target, credentials, and a designated safe test scope. No online
state has been changed.

The explicit-anchor renderer, intrinsic required-field hardening, server-bound
candidate publication, recoverable candidate inbox, and structured review
editor are included in the PR #20 evidence head used by the runs above. Actual
online verification and production write-path verification remain separately
unauthorized and unexecuted.
