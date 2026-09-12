# Phase 3: Generic Specification Engine

## Scope

This phase adds a review-only specification parsing contract. It does not
persist a new generic specification table, publish a version, change the
existing thesis policy lifecycle, or execute rules.

The implementation reuses the current policy document extractors and AI
provider boundary:

```text
DOCX/PDF/TXT/MD upload
        |
        v
extract_policy_text
        |
        +--> Markdown frontmatter.rules --> deterministic normalization
        |
        +--> other documents ------------> injected AI completion
                                                |
                                                v
                                   existing policy JSON normalization
                                                |
                                                v
                                      generic SpecRule candidate
                                                |
                                                v
                                     schema/conflict validation
```

AI is a candidate producer only. `SpecRule` is the structured hand-off to a
future rule engine. Unknown checkers supplied by AI are downgraded to manual
rules by the existing normalization layer; candidates with invalid executable
rules or conflicting thresholds cannot be confirmed.

## Candidate contract

`SpecParseCandidate` contains:

- candidate and logical spec identifiers;
- name, version, description, and source-preserving Markdown content;
- normalized `SpecRule` entries with `kind`, `target`, `checker`, `params`,
  severity, enforcement level, and provenance;
- format/layout overrides carried forward from the existing importer;
- extraction mode, source length/truncation metadata, analysis counters, and
  review issues;
- `needs_confirmation` or `confirmed` state.

The current confirmation function returns an immutable-in-practice model copy
and records no publication. Persistence and package composition are reserved
for Phase 4.

## API

| Endpoint | Behavior |
| --- | --- |
| `POST /api/specs/parse` | Manager-only upload and candidate generation |
| `POST /api/specs/parse/confirm` | Manager-only deterministic validation and confirmation |

Both operations emit generic audit events. Audit details intentionally omit
source bytes and content checksums. A non-structured document fails closed
with `503` when no AI configuration is available. A Markdown document with
explicit `frontmatter.rules` does not require AI.

## Validation gates

- rule IDs must be unique;
- kind, severity, and enforcement values must be from the structured schema;
- executable rules must have a supported checker;
- section existence and page-break rules must declare a section key;
- duplicate target/checker rules with different parameters, severity, or
  enforcement are reported as blocking conflicts;
- manual/document rules cannot silently become executable because they carry
  a checker field.

## Tests

`backend/tests/test_spec_engine.py` covers deterministic Markdown parsing,
AI-injected normalization, no-AI fail-closed behavior, unknown checker
downgrade, conflict blocking, authentication, confirmation, audit, and
checksum non-disclosure. Tests use an injected completion function and never
call an external AI service.

Verification recorded for this phase:

- Backend target: `5 passed, 2 warnings`.
- Backend full suite: `571 passed, 2 warnings`.
- Frontend unit tests: `22 passed` across `7` files.
- Frontend typecheck: `PASS`.
- Frontend paragraph checks: `PASS`.
- Frontend production build: `PASS`.
- `git diff --check`: `PASS`.
- Browser E2E against a running deployment: `NOT RUN` in this phase.
- Staging/production deployment and online verification: `NOT RUN` by scope.

## Explicit non-goals

- no database migration;
- no generic package persistence or publish workflow;
- no dynamic editor or generic validation execution;
- no production or staging deployment;
- no change to `/api/policies/import` behavior.
