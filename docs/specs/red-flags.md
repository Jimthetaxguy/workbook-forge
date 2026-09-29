# Specs from call red flags

Standing specs that close the red flags raised while locking the Workbook Forge
North Star. These are build contracts, not backlog wishes. Implementation
slices must satisfy them or name an explicit waiver in the PR.

Related: [North Star vision](../vision.md).

---

## Spec 1 — Canonical intermediate form

### Problem
Python and Rust must agree on the workbook model without sharing live objects
across the language boundary. Ad-hoc dicts and dual native types drift.

### Contract
1. **Canonical bytes are SoT.** Define a versioned JSON document validated by a
   checked-in JSON Schema. JSON is the canonical representation. Protobuf may
   be added only as a binary twin with the same logical fields and a Python and
   Rust reader; a Rust-only or Protobuf-only workbook path is not allowed.
   The schema covers at least: `Cell`, `Formula`, `Sheet`, and `Workbook`, plus
   document metadata, explicit named input/output bindings, and the model
   version field from Spec 4.
2. **Hydration only.** Python and Rust each deserialize the canonical bytes into
   their native types. No shared heap, no FFI object graph passed as SoT, no
   “Python model is truth / Rust mirrors it” shortcut. Engines, calculation
   sessions, exporters, and intake must use those hydrated native model types
   directly; a second workbook DTO inserted between schema and engine is not
   allowed.
3. **Field set (v1 minimum).**
   - `Workbook`: `schema_version`, `model_version`, `source_path` (optional),
     `metadata` (object), explicit named input/output bindings, and `sheets`
     (array of Sheet). Each binding identifies a direction, name, explicit
     sheet and cell, value type, required status, and any declared constraints.
     The schema owner chooses the serialized property names; slices must not
     invent another binding schema.
   - `Sheet`: `name`, `dimensions` (optional `[min_row, max_row, min_col, max_col]`),
     `cells` (map of A1 address → Cell).
   - `Cell`: `address`, authored `value` for literal/input cells (JSON
     null/number/string/bool or error object), `data_type`, `number_format`
     (optional string), and `formula` (optional Formula).
   - `Formula`: `expression`, `dependencies` (array of `Sheet!A1` strings),
     and an optional cached value read from OOXML. A formula cache is an
     imported observation, distinct from an authored cell value and from a
     result freshly calculated by Workbook Forge.
4. **Versioning.** `schema_version` is an integer on every serialized document.
   Bump it when the schema changes. Each reader accepts every supported older
   version through an explicit compatibility path or rejects it with an
   unsupported-version error. Silent field drops are forbidden.
5. **Migration rule.** Old → new: a migrator upgrades bytes to the latest
   written version before hydrate when possible. New → old writers are optional;
   if absent, tools must refuse and say which version they need.
6. **One fixture set.** Store schemas under `schemas/` and shared canonical
   workbook and golden result fixtures under `tests/fixtures/canonical/`.
   Python and Rust suites load the same files and bytes; neither suite keeps a
   language-local copy of the canonical workbook or expected result.
7. **Cache is not a calculation.** Intake preserves an existing formula cache
   separately from formula text and literal cell values. A calculation report
   records fresh results with backend and model provenance; an imported cache
   can never satisfy a calculation acceptance check. Export may write a
   calculated result as an OOXML cache, but must retain the calculation receipt
   that produced it.

### Done when
- A checked-in schema file exists under `schemas/`; Python and Rust independently
  deserialize the same canonical fixture bytes into their native types, then
  serialize them back with stable workbook meaning and bindings.
- A CI matrix runs both language suites against the same schema and
  `tests/fixtures/canonical/` files.
- Both calculation sessions accept the canonical native workbook type directly;
  no competing model or schema-to-DTO hop is needed.

---

## Spec 2 — Behavioral parity for export

### Problem
Matching stored cached values after export is not enough. Excel may recalc
differently for volatiles, iteration, arrays, and version quirks.

### Contract
1. **Recalc is part of the round-trip gate.** Export tests must open the
   exported workbook in Excel or a documented compatible engine, trigger a
   **full recalculation**, then diff results against the original’s cached
   values and formula results for the covered cells.
2. **Required coverage classes** (each needs at least one fixture):
   - Volatile functions (`NOW`, `RAND`, and peers the engines claim to support).
   - Iterative calculation settings (when the source workbook enables them).
   - Array / dynamic-array formulas in the supported set.
   - Cross-version Excel quirks called out in `docs/excel-observations.md` that
     affect recalculation of exported files.
3. **Harness.** A named test harness (script or pytest/cargo target) that:
   - loads original + exported paths,
   - runs full recalc on the export,
   - produces a structured diff (address, expected, actual, class),
   - **fails the test** on any mismatch outside an explicit allowlist (e.g.
     documented volatile tolerance windows).
   The Python harness may drive `export_xlsx` for either backend. Pytest marks
   distinguish `oracle_optional` local runs, which report an explicit skip
   when the oracle is unavailable, from `oracle_required` acceptance runs,
   where a missing oracle fails the gate.
4. **Volatiles.** Do not assert bit-identical `NOW`/`RAND` across runs. Assert
   type/shape and, where applicable, that both sides recalculate. Non-volatile
   cells remain strict.
5. **No silent “values only” green.** A suite that only compares stored XML
   caches without recalc does not satisfy this spec.

### Done when
- The harness is runnable in CI or a documented Excel-oracle job.
- At least one failing test was proven to catch a recalc mismatch before the
  suite goes green on a known-good export.

---

## Spec 3 — Intake detector isolation

### Problem
Semantic detectors intertwined with core intake become untestable, untunable,
and sticky to one workbook’s quirks.

### Contract
1. **Independently testable.** Each detector (including repeated-formula
   collapse, outlier detection, header inference, semantic tagging) has its own
   unit/fixture tests. Core intake tests do not require detectors to be on.
2. **Independently disableable.** Config (file or API flags) can turn each
   detector off without code edits. Default may enable a small safe set; all
   must be switchable.
3. **Diverse fixtures.** No detector may be tuned only to a single workbook.
   Each ships with a **diverse fixture set** (multiple shapes, sheets, and
   edge cases). A detector that only passes on one golden file is not ready.
4. **Removable without pipeline changes.** A failing detector can be removed or
   disabled via config/plugin registry. Core intake → model path does not
   hard-import detector modules for success.
5. **Failure isolation.** Detector exceptions become diagnostics; they do not
   abort the canonical model (aligns with vision resilience).
6. **Small explicit configuration.** Use a simple plugin registry (entry points
   or explicit registration) and small dict/TOML flags. Each detector is
   independently importable and can be disabled without code edits.

### Done when
- Plugin/registry interface exists; disabling all detectors still yields a valid
  `Workbook` model from intake.
- Each detector directory (or module) lists its fixtures and a one-line purpose.

---

## Spec 4 — Model versioning from day one

### Problem
“We’ll add versioning later” leaves early extracted models unreadable when the
schema moves. The extracted model is already a product artifact.

### Contract
1. **`model_version` field.** Every serialized workbook model carries
   `model_version` (semver string or integer sequence — pick one in the schema
   and stick to it; recommend integer `model_version` paired with
   `schema_version`). `schema_version` tracks the byte contract; `model_version`
   identifies the workbook model artifact. Both are validated on read/write.
2. **Changelog.** `docs/specs/model-changelog.md` (create with the first bump)
   records each model_version: date, summary, breaking or not, migration notes.
3. **Migration path.** When v2 ships, a reader for v1 remains available. Prefer
   auto-migrate v1 → v2 on load; if not, fail with a clear “need migrator X”.
4. **No later.** Intake and export land with `model_version` set on day one of
   the typed model (v1). Blank or missing version is a validation error for
   newly written files; readers may default missing → v1 only during a single
   compatibility window documented in the changelog, then reject.
5. **Agents and CLI** print `model_version` in intake summaries so humans can
   see which artifact generation they hold. Python's `intake_workbook` emits
   serialized canonical JSON as well as its native model result; it is not
   limited to an in-memory dataclass.

### Done when
- Schema + Python/Rust hydrate enforce `model_version`.
- Changelog file exists once the first bump is planned; v1 row is present when
  intake SoT ships.

---

## Language implementation contract

### Python

- `workbook_forge.model.Workbook` is the native hydration target. Keep its
  fields in sync with `schemas/` (generated types are fine if they remain
  readable and reviewable).
- `intake_workbook` emits the canonical JSON artifact, including
  `model_version`, as an explicit output; it may also return the native model
  object for callers that need it. The headless intake CLI exposes those same
  bytes and prints the model version in its summary.
- The pure Python backend is the reference for expected results in shared
  fixtures. Calculation binds named I/O on the canonical workbook types and
  passes that object directly to the session.
- The Python export harness drives `export_xlsx` and the Excel oracle. Local
  optional oracle tests may skip clearly; the required acceptance job may not.
- `python -m workbook_forge...` remains the thin, headless agent surface.

### Rust

- The native Rust crate mirrors `Cell`, `Formula`, `Sheet`, and `Workbook` from
  the same schema and uses Serde to read canonical JSON. It does not call
  Python to parse, calculate, or export.
- Calculation sessions take the hydrated Rust `Workbook` directly. Existing
  `WorkbookModel` session code may be retargeted or refactored, but must not
  become a second serialized workbook meaning or an intermediate DTO.
- Where Rust OOXML read/write already exists, it remains an independent Rust
  path. Compare shared serialized meaning and observable results, not private
  in-memory tree layouts.

### Cross-language rules

- Both suites load the exact same schema and files under
  `tests/fixtures/canonical/`; CI runs both as a matrix against those files.
- No live Python or Rust object crosses FFI as the contract. A future FFI may
  speed calls, but it cannot replace the JSON schema.
- When engines disagree, fix the failing engine or the shared fixture. Do not
  hide a semantic mismatch behind an adapter.

### Build order

1. Check in the schema, then prove Python hydration/serialization and Rust
   Serde round-trip against the same fixture bytes.
2. Run one explicitly bound calculation from the same canonical JSON document
   through the Python and Rust sessions and compare outputs.
3. Export from either backend and run the full-recalculation Excel harness.
4. Wrap the proven path in the Python headless CLI first. Expose Rust to agents
   after its native API offers the same operations.
5. Expand intake only from gaps exposed by round-trip diffs.

---

## Waiver rule

Temporary waivers need: owner, expiry date, which spec number, and what green
looks like when the waiver ends. Waivers live in the PR description and a line
in `docs/specs/red-flags.md` under **Active waivers** (none yet).

## Active waivers

_None._
