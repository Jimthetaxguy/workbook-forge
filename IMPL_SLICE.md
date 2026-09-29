# v1 intake — Excel → structured model

- **Branch:** `impl/v1-intake`
- **Base:** `5698f5b0c365f0bfbbf81ac56c13b77c2c05981a` (`origin/main`, vision and red-flag specs)
- **Checkout:** local managed worktree for this branch

## Scope
First vertical slice: read a workbook and emit a structured model (cells, formulas, cached values, layout, links) plus a compact overview. Readable Markdown preview is a scan layer; the cell map is source of record. Surface unsupported or incomplete extraction.

## Canonical schema and versioning
The serialized source of truth is `schemas/workbook-model-v1.schema.json`; the
shared fixture lives at `tests/fixtures/canonical/workbook-v1.json`. Python
hydrates `workbook_forge.model.Workbook`; Rust hydrates
`workbook_forge::model::Workbook`. Both expose byte hydration and serialization,
require `schema_version` and `model_version`, and reject unsupported versions or
unknown fields. Intake summaries print the model version.

## Calc-binding boundary
Calc-binding must hydrate the same canonical bytes and operate on the native
Python or Rust canonical `Workbook` types directly. It must not serialize that
model into a second workbook DTO before calculation. The current toolkit
`WorkbookModel` is a separate legacy session shape; calc-binding must retarget or
refactor the session API so canonical model types are the direct calculation
input. Preserve existing callers deliberately, but do not make a conversion DTO
the acceptance path.

## First concrete task
Completed for this slice: define the canonical, versioned workbook model and
hydrate it from serialized bytes in both native implementations. Intake remains
an overview-plus-cell-map path and does not mutate its source workbook.

## Do not
- Do not rewrite history on `main` or force-push.
- Do not modify Codex session files under `~/.codex`.
- Keep shelf split: Forge owns compute + OOXML; cell-store owns the sealed event log (join later via FORGE_EDGE).

## Status (2026-09-28)
Landed the typed intake model and canonical schema/versioning contract:

- `python/workbook_forge/model.py` — SoT types: `Cell`, `Formula`, `Sheet`, `Workbook`
- `python/workbook_forge/intake.py` — `intake_workbook(path)` emits canonical JSON bytes via bounded OOXML/`import_xlsx`; `intake_workbook_model(path)` returns the native Python model. The CLI emits canonical JSON by default; `--summary` prints the versioned overview.
- `schemas/workbook-model-v1.schema.json` — required v1 interchange form
- `rust/src/model.rs` — Rust types hydrate the same bytes without an FFI object graph
- `tests/fixtures/canonical/workbook-v1.json` — shared Python/Rust round-trip fixture
- `docs/specs/model-changelog.md` — initial `model_version: 1` row
- Intake CLI summaries include both schema and model versions

The previous toolkit calculation session still uses its own `WorkbookModel`
shape. Calc-binding must refactor or retarget that session so the acceptance path
calculates directly against the canonical native model types.

## Vision
Follow [docs/vision.md](docs/vision.md). Shared types and build rules there bind this slice to the others.

## Red-flag specs
Build contracts: [docs/specs/red-flags.md](docs/specs/red-flags.md).
