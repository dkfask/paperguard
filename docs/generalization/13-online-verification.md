# Phase 13: Online Verification

## Scope

Phase 13 provides a guarded, read-only verification path for an explicitly
authorized PaperGuard environment. It does not deploy, migrate, create a
project, change content, render a document, or alter production data.

The local verifier is `ops/online-verify.sh`. The hosted wrapper is the
manual-only `.github/workflows/online-verification.yml` workflow. The workflow
requires a target URL and GitHub secrets at run time; neither is stored in the
repository.

## Read-Only Checks

The verifier checks the public frontend assets and health endpoints, then logs
in with the supplied administrator account and reads:

- `/logo.png` and `/avatar.png`;
- `/api/health/live` and `/api/health/ready`;
- `/api/auth/me`;
- `/api/templates`;
- `/api/document-packages`;
- `/api/audit?limit=1`.

It validates response shape, never prints the bearer token or password, and
writes only a redacted evidence JSON containing the target URL, source
revision, check names/statuses, and timestamp. HTTPS is required except for
loopback rehearsals.

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
artifact. A successful read-only check is not evidence that project autosave,
validation, DOCX rendering/download, worker processing, or rollback works in
production. Those flows require a designated test tenant or disposable
environment, explicit cleanup authorization, and a separate write-path test
plan.

## Status

- Script syntax and input fail-closed behavior: validated locally.
- Full verifier contract against a local loopback mock: **PASS**; all 9
  read-only checks passed and the mock service was stopped afterward.
- Actual authorized online verification: **NOT RUN**; no target URL or
  out-of-band credentials were supplied.
- Production deployment: **NOT RUN** by authorization.
- Production write-path E2E, backup restore, rollback, and online repair loop:
  **NOT RUN**.

Phase 13 tooling is **GO**. Phase 13 acceptance is **BLOCKED** pending an
authorized target, credentials, and a designated safe test scope. No online
state has been changed.
