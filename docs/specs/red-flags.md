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
1. **Serialized schema is SoT.** Define a versioned serialized form (JSON Schema
   first; Protobuf allowed as a binary twin with the same logical fields). The
   schema covers at least: `Cell`, `Formula`, `Sheet`, `Workbook`, plus document
   metadata and the model version field from Spec 4.
2. **Hydration only.** Python and Rust each deserialize the canonical bytes into
   their native types. No shared heap, no FFI object graph passed as SoT, no
   “Python model is truth / Rust mirrors it” shortcut.
3. **Field set (v1 minimum).**
   - `Workbook`: `schema_version`, `model_version`, `source_path` (optional),
     `metadata` (object), `sheets` (array of Sheet).
   - `Sheet`: `name`, `dimensions` (optional `[min_row, max_row, min_col, max_col]`),
     `cells` (map of A1 address → Cell).
   - `Cell`: `address`, `value` (JSON null/number/string/bool or error object),
     `data_type`, `number_format` (optional string), `formula` (optional Formula).
   - `Formula`: `expression`, `dependencies` (array of `Sheet!A1` strings),
     `result` (same value union as Cell.value).
4. **Versioning.** `schema_version` is an integer on every serialized document.
   Any breaking or additive schema change that readers must understand requires
   a **version bump**. Ship a backward-compatible reader that accepts all
   supported prior versions (or rejects with an explicit unsupported-version
   error). Silent field drops are forbidden.
5. **Migration rule.** Old → new: a migrator upgrades bytes to the latest
   written version before hydrate when possible. New → old writers are optional;
   if absent, tools must refuse and say which version they need.

### Done when
- A checked-in schema file exists under `docs/specs/` or `schemas/` and both
  language test suites round-trip a fixture through serialize → hydrate →
  serialize with stable semantics.
- CI fails if either language invents a parallel DTO that is not produced from
  the schema (enforced by review checklist until automated).

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
4. **Volatiles.** Do not assert bit-identical `NOW`/`RAND` across runs. Assert
   type/shape and, where applicable, that both sides recalculate. Non-volatile
   cells remain strict.
5. **Spilling arrays.** When a fixture returns multiple values into worksheet
   cells, compare every declared cell in the spill range after Excel saves and
   the SDK reimports the workbook. A formula that reduces an array to one value
   does not prove spill placement.
6. **No silent “values only” green.** A suite that only compares stored XML
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
   `schema_version`).
2. **Changelog.** `docs/specs/model-changelog.md` (create with the first bump)
   records each model_version: date, summary, breaking or not, migration notes.
3. **Migration path.** When v2 ships, a reader for v1 remains available. Prefer
   auto-migrate v1 → v2 on load; if not, fail with a clear “need migrator X”.
4. **No later.** Intake and export land with `model_version` set on day one of
   the typed model (v1). Blank or missing version is a validation error for
   newly written files; readers may default missing → v1 only during a single
   compatibility window documented in the changelog, then reject.
5. **Agents and CLI** print `model_version` in intake summaries so humans can
   see which artifact generation they hold.

### Done when
- Schema + Python/Rust hydrate enforce `model_version`.
- Changelog file exists once the first bump is planned; v1 row is present when
  intake SoT ships.

---

## Waiver rule

Temporary waivers need: owner, expiry date, which spec number, and what green
looks like when the waiver ends. Waivers live in the PR description and a line
in `docs/specs/red-flags.md` under **Active waivers** (none yet).

## Active waivers

_None._
