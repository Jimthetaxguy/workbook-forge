# v1 calc-binding — one canonical workbook through both engines

| Branch | `impl/v1-calc-binding` |
| --- | --- |
| Base | `origin/main` (synced locally at `5698f5b`) |
| Checkout | `/Users/jamespustorino/code/_worktrees/workbook-forge-v1-calc-binding` |
| Status | Canonical schema prerequisite merged into this branch; direct Python/Rust sessions and shared operating-scenario cases are implemented and locally verified. The Excel Desktop round-trip remains a separate export gate. |

## Job

Prove that a workbook definition is usable software: load one canonical JSON
document into Python and Rust, bind the same named inputs and outputs to the
same cells, calculate through both native engines, and compare both results to
one shared golden fixture. Do not construct the operating scenario twice in
language-specific code.

The canonical serialized bytes are the source of truth. Python hydrates those
bytes into `workbook_forge.model.Workbook`; Rust independently hydrates the
same bytes into its native `model::Workbook`. Each engine works against its
native canonical type and serializes its result. No live objects cross FFI and
no intermediate workbook DTO is introduced.

## Prerequisites

This slice includes the canonical schema/versioning work first authored on
`impl/v1-intake`:

- `schemas/` contains the JSON Schema for `Workbook`, `Sheet`, `Cell`, and
  `Formula`, including `schema_version` and `model_version`.
- The single shared workbook fixture lives under
  `tests/fixtures/canonical/`; Python and Rust tests load the same file bytes.
- The schema-hydrated native types are the calculation-session input. The
  existing toolkit `WorkbookModel` is currently a separate engine/session
  shape; retarget or refactor the session API so it calculates directly from
  the canonical native `Workbook`. Do not translate through a parallel DTO.
- The fixture carries the explicit input/output cell bindings in the canonical
  model, using the schema owner's field contract. Do not create another
  binding manifest schema or guess field names in this slice.
- Literal `Cell.value` data and an imported formula cache are distinct. A
  formula's optional cached value is a source observation, never evidence that
  this session calculated the cell. Fresh calculations live in a result report
  tied to the backend and model version; sessions calculate from formula text
  and bound inputs.

## First calculation: operating scenario

The existing operating scenario is the selected first case. It already covers
cross-sheet formulas, aggregation, and a meaningful break-even boundary. The
current Python and Rust constructors build separate legacy models, so existing
matching results are useful precedent but are not proof of this acceptance
case.

Named numeric inputs and required locations:

| Name | Cell | Constraint |
| --- | --- | --- |
| `unit_price` | `Assumptions!B1` | Required number, minimum 0 |
| `unit_cost` | `Assumptions!B2` | Required number, minimum 0 |
| `fixed_cost` | `Assumptions!B3` | Required number, minimum 0 |

Named outputs and required locations:

| Name | Cell | Workbook meaning |
| --- | --- | --- |
| `revenue` | `Forecast!F2` | `=SUM(C2:C4)` |
| `profit` | `Forecast!F3` | `=SUM(E2:E4)` |
| `break_even_units` | `Forecast!F4` | Contribution-margin break-even; `#N/A` when price is not above unit cost |

The detailed period rows are `Forecast!A2:B4` (period and quantity),
`C2:C4` (revenue), `D2:D4` (variable cost), and `E2:E4` (profit). Revenue is
quantity × unit price; variable cost is quantity × unit cost; each period's
profit subtracts its variable cost and one period of fixed cost. The total
outputs aggregate the three period rows.

## One fixture set, two language suites

Store canonical input bytes and expected cases once, under
`tests/fixtures/canonical/`. Both language suites must open those files rather
than copy expected values into separate test code. At minimum, the shared
cases cover:

| Inputs (`unit_price`, `unit_cost`, `fixed_cost`) | Revenue | Profit | Break-even units per period |
| --- | ---: | ---: | ---: |
| `(20, 8, 1000)` | `7400` | `1440` | `1000 / 12` |
| `(25, 8, 1000)` | `9250` | `3290` | `1000 / 17` |
| `(8, 8, 1000)` | Scenario formula result | Scenario formula result | Excel `#N/A` error value |

Python's pure `backend="python"` path is the reference implementation for
fixture expectations. Rust must independently calculate the same canonical
document; it must not call Python. Numeric comparisons use the declared
fixture tolerance. Error results compare by typed Excel error identity, not by
stringifying them into a successful number or rejecting the input.

## Acceptance checks

1. Validate the canonical fixture against the checked-in schema.
2. Hydrate the exact same fixture bytes into the Python and Rust native
   `Workbook` types and serialize each back. Compare canonical meaning and
   binding locations, not language-internal object trees.
3. Create one calculation session per backend directly from the hydrated
   canonical workbook. Apply each shared input case using the same binding
   names, then calculate the declared outputs.
4. Compare both backends to the shared golden results, including the `#N/A`
   boundary result, and compare their serialized result meaning.
5. Preserve existing input and calculation refusals: unknown input names,
   invalid type/blank/below-minimum values, unsupported formula paths, and
   dependency cycles remain explicit; a rejected input batch changes nothing.
6. Emit a deterministic receipt with fixture digest, schema/model versions,
   backend identity, input case, bound cells, output values/errors, tolerance,
   and pass/fail differences. Do not call imported cached values evidence that
   either engine calculated the outputs.

## Boundaries

- This proves one calculation through both native engines; it does not claim
  broad formula compatibility or Excel compatibility by itself.
- Excel export, open/edit/full-recalc/save/reimport evidence belongs to
  `impl/v1-export` and is the next trust gate.
- Do not add formula families before the shared-model case passes.
- Do not merge this implementation branch into `main` before its acceptance
  evidence is recorded.
- Keep the shelf split: Workbook Forge owns compute and conservative OOXML;
  cell-store owns the sealed cell event log; connect later through
  `FORGE_EDGE`. No fourth tree.

## Current source entry points

- Python direct canonical session: `python/workbook_forge/canonical_calc.py`
- Rust direct canonical session: `rust/src/canonical_calc.rs`
- Shared bound workbook and golden cases: `tests/fixtures/canonical/operating-scenario-v1.json` and `tests/fixtures/canonical/operating-scenario-cases.json`
- Cross-backend deterministic receipt: `tools/verify_canonical_calc.py`
- Canonical native model types: `python/workbook_forge/model.py` and `rust/src/model.rs`
- Legacy operating scenario constructors retained for comparison: `python/workbook_forge/toolkit.py` and `rust/src/toolkit.rs`
- Export oracle and round-trip work: `tools/excel_oracle.py` and
  `impl/v1-export`

## Local acceptance evidence — 2026-09-28

- `PYTHONPATH=python uv run --extra test python -m pytest -q --tb=short` — 885 passed, 56 skipped.
- `CARGO_TARGET_DIR="$PWD/rust/target" cargo check --manifest-path rust/Cargo.toml` — passed.
- `CARGO_TARGET_DIR="$PWD/rust/target" cargo test --manifest-path rust/Cargo.toml` — 99 passed.
- `cargo fmt --manifest-path rust/Cargo.toml --check`, Python compilation, and `git diff --check` — passed.
- `python tools/verify_canonical_calc.py` — pass, no differences. Fixture SHA-256: `a6ff2b1cc79f11a69e94d1ffabc1dd3ecb1980bb2dcff1d0fae2522f310bab66`; schema/model versions: `1`/`1`; tolerance: `1e-12`.
- The receipt compares canonical workbook meaning, bindings, named outputs, every calculated cell, diagnostics, and the `#N/A` result across both engines.
- This is a native-engine parity check. It does not satisfy Excel Desktop open/edit/recalculate/save/reimport acceptance; that remains in `impl/v1-export`.
