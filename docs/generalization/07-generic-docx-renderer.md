# Phase 7: Generic DOCX Renderer

## Scope

This phase adds the first generic `DocumentProject` to DOCX rendering path.
It deliberately reuses the existing thesis DOCX exporter through an adapter;
the thesis export path and its public APIs are unchanged.

```text
DocumentProject + frozen DocumentPackage
                  |
                  v
       generic_docx_renderer adapter
                  |
                  v
  legacy PaperTemplate + WritingProject
                  |
                  v
       existing DOCX exporter + source DOCX
                  |
                  v
             DOCX artifact
```

## Adapter contract

`generic_docx_renderer` verifies that the project, package, template version,
renderer version, and rule-engine version are the exact frozen references from
project creation. It resolves the source DOCX only through
`resolve_template_source`, which accepts a safe stored filename and a controlled
template directory.

The adapter maps the generic domain to the existing export models:

- generic section kinds already understood by the legacy exporter retain their
  meaning; unknown kinds become a body section instead of being rejected as a
  thesis-only type;
- paragraphs become legacy paragraph blocks;
- image/figure blocks use the existing image exporter;
- table/grid blocks retain structured cells and optional source XML for the
  existing table exporter;
- equation/formula blocks retain OMML and use the existing equation fallback;
- template cover blocks are captured during DOCX parsing and reused for static
  cover text, metadata fields, tables, and spacing;
- missing generic styles are filled from the legacy default style set so the
  existing exporter always receives a complete style map;
- active specification page-layout overrides are applied by the existing
  exporter after the template snapshot has been adapted.

The generic endpoint creates a validation run before rendering. Hard validation
failures return `409` with the run ID and summary; `force=true` is an explicit
caller override for workflows that permit an artifact with known findings.
Successful output is returned as a DOCX download, the temporary render
directory is removed by the response background task, and the render is added
to the audit ledger.

## Frontend flow

The generic editor now exposes an `Export DOCX` action next to deterministic
validation. It disables conflicting actions while saving, validating, or
rendering and downloads the server-provided filename. The action is translated
in both supported UI languages.

## Fidelity boundary

The source DOCX remains the layout base, so styles, themes, page settings,
headers, footers, and package resources are preserved by the existing exporter.
Cover content recognized by the import parser is also re-emitted from its
structured cover definition. This phase does not yet provide arbitrary
placeholder binding or lossless preservation of every static body paragraph,
unsupported Word object, tracked change, or renderer-specific field. Such
content requires a dedicated placeholder/asset model and belongs in later
renderer and storage phases. Unknown section kinds are intentionally rendered
as body content and should be reviewed during template confirmation.

## Tests and verification

Verification completed locally for this phase:

- Generic renderer and project API tests: `5 passed, 2 warnings`.
- Full backend suite: `578 passed, 2 warnings`.
- Frontend unit suite: `24 passed` across `9` files.
- Frontend typecheck: `PASS`.
- Frontend paragraph checks: `PASS`.
- Frontend production build: `PASS`.
- `git diff --check`: `PASS`.
- Hosted GitHub Actions run `34675958927` for PR `#14`: `PASS`; frontend
  build, Python 3.11, and Python 3.12 all passed for remote head
  `eef7b75c284be00b4672635461759e1c67bc1be4`. GitHub emitted only the
  existing Node.js 20 action deprecation annotations.
- Staging/production deployment and online verification: NOT RUN by scope and
  authorization policy.

## Explicit non-goals

- no replacement of `docx_exporter.py`;
- no arbitrary Word placeholder engine or full static-body round trip;
- no generic PDF renderer;
- no durable generic render queue or artifact catalog;
- no migration of legacy thesis projects;
- no production database migration or deployment;
- no merge of the phase PR without explicit authorization.
