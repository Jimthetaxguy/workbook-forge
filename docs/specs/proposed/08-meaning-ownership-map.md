---
type: technical-specification
status: proposed
base: main 580aacd
summary: A field- and operation-level map of which representation owns which piece of spreadsheet meaning today, which companions each route needs to preserve and edit an imported workbook, which transformation edges exist and are tested, which are missing, where authority is duplicated, and a proposed owner per concept. It decides nothing about versions; it is the input the convergence proposals need.
---

# 08 — Meaning ownership map

## Problem

The vision asks for one contract for workbook meaning that Python, Rust, source
generation, editors, agents and the Excel return paths all consume. The code holds
that meaning in several representations that grew on separate tracks. Nobody has
written down, field by field, what each one carries, what travels with it, and how
one becomes another. Without that map, "converge on the canonical model" is a slogan
that could discard preservation, bindings and sessions the toolkit already has, and
"keep the toolkit" is a slogan that keeps two models forever.

This document is the map. Every claim names a file and a line at the base revision.
It recommends owners; it does not change a schema, a version or a public contract.

## 1. The representations

| Representation | Where | Carries | Versioning |
| --- | --- | --- | --- |
| Canonical workbook model | `schemas/workbook-model.v1.schema.json`; `python/workbook_forge/model.py:96-230`; `rust/src/model.rs:48-156` | `Workbook{schema_version, model_version, metadata, sheets, source_path, provenance, bindings, diagnostics}`; `Sheet{name, cells, dimensions}`; `Cell{address, value, data_type, number_format, formula, provenance}`; `Formula{expression, dependencies, result}`; `Bindings{inputs, outputs}` as name → `CellId{sheet, address}`; `Diagnostic{code, classification, message, sheet, address, function}`; `Provenance{origin, source}` | `schema_version` 1 and `model_version` 1, both required, unknown values refused (`model.py:967-968`; `model.rs:168-175`) |
| Toolkit workbook and session | Python: dict normalised in `python/workbook_forge/python_engine.py:202-300`, wrapped by `toolkit.WorkbookModel` (`toolkit.py:48-103`); Rust: `rust/src/toolkit.rs:154-427` | `{schema_version, revision, sheets[{id, name, cells{address: {value, formula, cached_value, style, blocked_reason}}, column_widths}], inputs{name: {sheet, address, kind, required, min, max, choices}}, outputs{name: {sheet, address}}}`; `Style{number_format, bold, font_color, fill_color, horizontal}`; `CalculatedValue::{Scalar, Array}` | `schema_version` 1 only; `revision` counter; no `model_version` |
| Toolkit companions (not in the snapshot) | Python `toolkit.py:98-109`: `_names`, `_source_baseline`, `_import_metadata`, `_feature_guard`, `_text_cells`, `_text_layouts`, `_imported_addresses`; `xlsx.py:92-100` `_Baseline{source, parts, infos, comment, document_json, uses_1904_date_system, merged, compatibility_version}`; Rust `rust/src/xlsx.rs:1416-1431` `ImportedWorkbook{source, baseline, original, session, workbook_part, sheet_parts, merged, insertion_bound, date1904, compat3, capabilities, guard, text_cells, text_layouts}`; `xlsx_features.FeatureGuard` (`python/workbook_forge/xlsx_features.py:647`; `rust/src/xlsx_features.rs:949`) | the imported package baseline and patch context, defined-name table, validation and pivot and chart guards, text-format layout, the insertion bound | none; held in process memory beside the session |
| Source compiler program | `python/workbook_forge/source_graph.py:48-60` | `Program{sheet, defaults, formulas, sheets, version}`; `Node{kind, value, args}`; rendered into a versioned scaffold (`source_cli.py:33`) with embedded runtimes `source_runtime_v2..v6.{py,rs}` | profile `version` 2 to 6; scaffold must regenerate byte-identically (`source_graph.py:497-503`) |
| Primitive expression | `python/workbook_forge/primitives.py:289-300`; `rust/src/primitives.rs:372`; `catalog/primitive-expression.schema.json` | `Expression` over a node tree (input, literal, call, binary, range) with native named inputs; compiles to the evaluator AST (`primitives.py:1-4`) | `schema_version` on the expression document |
| Browser session projection | `web/forge-engine/src/protocol.ts`; `rust/src/browser_contract.rs:265-333`; `rust/src/event_log.rs:46-110` | `Snapshot{revision, sheets[{name, cells{value, formula, text}, textFormat}]}`; `Calculation{revision, values, stale, diagnostics, calc}`; `CommandEnvelope{v, command_id, client_id, client_seq, idempotency_key, actor, base_revision, mode, preconditions, op}`; `EventLog{events, session, baseline, undo, redo}`; `Baseline{kind, name, sha256, bytes, sheet}` | envelope `v`; `Revision` as a decimal string; event log schema v1 |
| Agent protocol | `catalog/agent-operations.json` (byte-identical copy at `rust/src/agent_operations.json`) | nine operations with `input_schema` and `output_schema`; envelope `{schema_version: 1, id, operation, ok, revision, result | error{code, message}}` | `schema_version` 1, profile `agent-workbook-v1` |
| Calculation report | `schemas/calculation-report.v1.schema.json`; `model.py:468`; `model.rs:1615` | `{report_version, stage, engine, behavior_profile, schema_version, model_version, source_fingerprint, inputs[{name, sheet, address, origin, value}], outputs[{name, sheet, address, origin, result}], diagnostics, calculated_formulas, unresolved_formulas}` | `report_version` plus the model's two versions; `additionalProperties: false` |

## 2. Concept by representation

Present (✓), absent (—), present under another name or with different semantics (≈, explained).

| Concept | Canonical | Toolkit snapshot | Toolkit companions | Source program | Primitive expression | Browser projection | Agent protocol |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sheet identity | `name`, optional `dimensions` | `id` and `name`, `column_widths` | — | `sheet` plus ordered `sheets` identities from profile 3 (`source_graph.py:508-512`) | — (no sheets) | `name` | `describe` returns `{id, name, populated_cells}` |
| Cell address | A1 within a sheet; `CellId{sheet, address}` | A1 keys; `CellAddress{sheet, address}` | `insertion_bound` (Rust) and `_imported_addresses` (Python) | `Sheet!A1` string keys (`source_graph.py:104-127`) | none; named inputs replace addresses | `Address{sheet, address}` | `{sheet, address}` |
| Value | `value` plus `data_type`; Rust `Scalar::{Blank, Number, Text, Boolean, Error}` | `CellValue::{Number, Text, Boolean, Error, Blank}` plus `cached_value` | — | `Node` literal | `PrimitiveValue::{Scalar, Range, Array, Omitted}` | `Scalar` or `{rows}` | JSON scalar |
| Array result | ≈ Python `Formula.result: Any`; **Rust `Formula.result: Scalar` cannot hold an array** (`model.rs:124-128`) | `CalculatedValue::Array` | — | — | `Array` | `{rows}`; projected only when 1×1 (PR #47) | `arrays: false` in the TypeScript client |
| Formula | `Formula{expression, dependencies, result}`; dependencies stored | `formula: String`; dependencies derived by the graph (`python_engine.py:352-360`) | — | `Node` tree (`kind, value, args`); five functions in profile 6 (`source_graph.py:31-38`) | node tree over the evaluator AST | `formula: string` | `formula: string` in `read`/`edit` |
| Imported cache versus calculated | `provenance.origin` on the cell; report `origin ∈ {calculated, authored, blank, unavailable}` | `cached_value` separate from `value`; `blocked_reason` | — | `defaults` (literal values) | — | `value` is the engine's result | `explain` returns `value_origin` |
| Presentation | `number_format` only | `Style{number_format, bold, font_color, fill_color, horizontal}`, `column_widths` | `_text_cells`, `_text_layouts` (`TextLayout` `xlsx.py:228`; `xlsx.rs:832`) | refused beyond `s`/`t` attributes (`source_package.py:8-22`) | — | `text`, `textFormat{columns, rows, generalRows}` | `style` on `edit` |
| Application bindings | `Bindings{inputs, outputs}`: name → `CellId`, **untyped** | `inputs`: name → `{sheet, address, kind, required, min, max, choices}` (`python_engine.py:267-299`; `toolkit.rs:364`); `outputs`: name → address | — | `defaults` per cell; `--inputs` overrides at emit (`source_cli.py:227`) | named inputs as native values | — (no bindings) | `describe` inputs/outputs; `set_inputs`, `preview_inputs` |
| Defined names | `metadata.defined_names` carried by intake; `workbook_defined_names()` builds a table (`model.py:707`) | — in the snapshot | `_names` JSON table passed to `Session` (`toolkit.py:98-103`); Rust `OwnedWorkbook::names()` (`agent.rs:34`) | untested (no reference in `source_graph.py`) | — | indirect: imported workbooks only (`wasm.rs:12-15`) | indirect through the owning workbook |
| Source identity and provenance | `provenance{origin, source}`, `source_path` (redacted for agents), `source_fingerprint` on report and views | — | `_Baseline.source`; `ImportedWorkbook.source` | `bounded_package` fidelity check (`source_graph.py:546`) | — | `Baseline{kind, name, sha256, bytes}` in the event log | `describe` `source.kind ∈ {authored, xlsx}` |
| Preservation envelope | — (fresh package on export); `metadata.intake.not_carried` lists what was dropped (`intake.py:118-199`) | — | `_Baseline.parts`, `infos`, `comment`, `document_json`, `merged`, `compatibility_version`; Rust `baseline: Package`, `workbook_part`, `sheet_parts`, `merged`, `date1904`, `compat3` | refused: `check_fidelity` | — | baseline bytes kept in the worker for export | through the owning workbook |
| Feature guards | — (`not_carried`) | — | `FeatureGuard{rules, pivots, charted}` | — | — | enforced inside Rust on import; surfaces as error codes | refusals rewritten to `unsupported_operation` (`docs/agent-protocol.md:123-125`) |
| Revision and session | — (`Workbook` is immutable; `with_input` returns a new one, `model.py:534`) | `revision: u64`; `Session.apply/set_inputs/calculate` (`python_engine.py:623`; `toolkit.rs:1735`) | — | — | — | `Revision` string; `base_revision`; `revision_conflict` | `revision`, `expected_revision` |
| History | — | — | — | — | — | `EventLog` with undo and redo intents, replay (`event_log.rs:102-110`) | — |
| Calculation context | `calculate(workbook, context=CalcContext)` (`model.py:364`); **the v1 report has no context field** | `CalculationReport.calc: Option<CalcContext>` (`toolkit.rs:1203-1210`) | — | — | — | `CalcContext{now_ms, utc_offset_minutes, seed}` | not exposed |
| Diagnostics and refusals | `Diagnostic{code, classification, message, sheet, address, function}` | `Diagnostic{code, message, sheet, address}`; `ToolkitError{code, message}`; codes from `catalog/refusal-codes.json` | — | `SourceDiagnostic(code, message)`: `unsupported_source`, `invalid_source`, `unsupported_fidelity`, `resource_limit`, `invalid_reference` | `PrimitiveError{code, message}` | `EngineError{code, message, expected_revision, current_revision, refusal, …}` | eight codes (`docs/agent-protocol.md:27-33`) |
| Limits | `MAX_ROW`, `MAX_COLUMN`, `MAX_RANGE_CELLS` (`model.py:35-37`; `model.rs:17-19`) | `MAX_WORKBOOK_CELLS`, `MAX_DEPENDENCIES`, `MAX_EDIT_BATCH`, `MAX_WORKERS`, `MAX_RESULT_CELLS` (`python_engine.py:33-38`; `toolkit.rs:17-20`) | — | `MAX_SOURCE_BYTES`, `MAX_ARTIFACT_BYTES` | payload budget (`primitive_catalog.json`) | `MAX_ID_BYTES` (`browser_contract.rs:261`) | `limits` block in the catalog; `agent.rs:17-19` |

Evaluator limits (`MAX_FORMULA_LENGTH_UNITS`, `MAX_FUNCTION_NESTING`, `MAX_EXPRESSION_NESTING`, `MAX_WILDCARD_WORK`, `MAX_TEXT_LENGTH_UNITS`) sit in the two evaluators (`__init__.py:22-25,168`; `lib.rs:38-42`) and apply to every representation that evaluates; the canonical hydrator does not apply the text cap (proposed spec 03).

## 3. Public operations: what each consumes, needs, and leaves behind

| Operation | Consumes | Companions it needs | Revision | Produces | What survives |
| --- | --- | --- | --- | --- | --- |
| `workbook-forge intake` (`cli.py:109`) | `.xlsx` | none | none | canonical JSON; `--summary`, `--markdown`, `--map`, `--paired` views (`views.py:19,54,105`) | cells, formulas, number formats, defined names, dimension; everything else in `not_carried` |
| `workbook-forge calculate` (`cli.py:116`) | canonical JSON, or `.xlsx` through intake | optional bindings document | none | calculation report; `--model` canonical JSON; `--xlsx` fresh package via `export_canonical` | as intake; refuses array spill (`canonical_xlsx.py:105`) and, in strict mode, cells without Excel evidence (`:455`) |
| `workbook-forge inspect`, `run`, `scenario` (`cli.py:106,131,133`) | toolkit model: `.xlsx` via `import_xlsx` or a toolkit document (`cli.py:44`) | baseline, names, guards, text layout (attached by `import_xlsx`) | toolkit `revision` | inspection dict; calculated closure; scenario `.xlsx` via `export_xlsx` | the imported package minus supported patches |
| `workbook-forge agent` and the nine operations (`cli.py:136`; `agent.py:179`; `agent.rs:66`) | toolkit model with companions (Python wrapper; Rust `OwnedWorkbook::{Authored, Imported}`) | same as above | `expected_revision` on `edit`, `set_inputs`, `export`; `preview_inputs` detached | JSON responses; one new `.xlsx` in the host directory | patch export preserves the baseline; source overwrite refused (`xlsx.py:1086`; `xlsx.rs:1952`) |
| `workbook-forge extract` (`cli.py:125`) | `.xlsx` parts | none | none | extraction records (`catalog/extraction-report.schema.json`) | read-only |
| `workbook-forge capabilities` | catalogs | none | none | text | — |
| `workbook-forge-compile compile` (`source_cli.py:211`; `source_graph.py:561`) | `.xlsx` through **toolkit** `import_xlsx` (`source_graph.py:28`) | `check_fidelity` must pass (`source_package.py:8`) | none | a Python or Rust program in the versioned scaffold | five functions, operators, references, multi-sheet; everything else refused |
| `workbook-forge-compile translate` (`source_cli.py:216`) | a generated program | scaffold unchanged | none | the other language's program | expressions and defaults inside the scaffold |
| `workbook-forge-compile emit` (`source_cli.py:221`) | a generated program plus inputs | the system `rustc` for Rust (`source_process.py`) | none | values, or an `.xlsx` through `verified_export` (`source_graph.py:751`) | as compile |
| Browser `open`, `newWorkbook`, `snapshot`, `applyEdits`, `calculate`, `applyAndCalculate`, `export`, `log`, `subscribe`, `replay` (`protocol.ts` `Commands`) | toolkit `WorkbookSession::{Imported, Authored}` (`wasm.rs:12-15`) | baseline bytes in the worker; guards inside Rust | `Revision` string; `base_revision` in the envelope | snapshots, calculations, patches, bytes, log pages | patch export; undo and replay only here |
| Primitive contract (`rust/examples/primitive_contract.rs`; `primitives.py`) | an `Expression` document and native inputs | none | none | a value | no workbook, no addresses |
| Canonical library (`model.hydrate`, `calculate`, `with_input`, `calculation_report`; `canonical_xlsx.export_canonical`, `import_canonical`) | canonical JSON | optional `CalcContext` | none | new immutable `Workbook`; report; fresh package | as intake |

## 4. Transformation edges

### 4a. Edges that exist, and the tests that exercise them

| Edge | Code | Tests |
| --- | --- | --- |
| `.xlsx` → toolkit model with companions | `xlsx.py` `import_xlsx`; `rust/src/xlsx.rs` | `python/tests/test_toolkit_xlsx.py` (13), `test_workbook.py` (46), `test_xlsx_features.py` (14), `test_rust_xlsx_interop.py` (6); `rust/tests/xlsx_features.rs` (12), `corpus.rs` (12), `absent_cells.rs` (10) |
| `.xlsx` → canonical model (Python only) | `intake.py` | `test_intake.py` (45) |
| canonical JSON → both engines hydrate and calculate | `model.py:340,364`; `model.rs` | `test_dual_engines.py` (21), `test_canonical_model.py` (14), `test_canonical_model_diagnostics.py` (4), `rust/examples/canonical_calc.rs` |
| canonical → fresh package → canonical | `canonical_xlsx.py` `export_canonical`, `import_canonical`; `canonical_roundtrip.py` | `test_canonical_export.py` (12), `test_canonical_receipt_safety.py` (2) |
| toolkit → patched package → toolkit | `xlsx.py` `export_xlsx`; `xlsx.rs` | `test_absent_cell_export.py` (12), `test_toolkit_xlsx.py`, `test_corpus.py` (10); `rust/tests/absent_cells.rs`, `corpus.rs` |
| toolkit → source program → program text → program → other language → `.xlsx` | `source_graph.py:561,407,497,542,751` | `test_source_graph*.py` (48 across v2 to v6), `test_source_compiler.py` (6), `test_compiler_seeded.py` (5), `test_source_process.py` (5), `test_source_export_verification.py` (1) |
| primitive expression → evaluator AST | `primitives.py`; `primitives.rs` | `test_primitives_contract.py` (28), `test_primitives_python.py` (15) |
| toolkit → browser snapshot, calculation, patches, event log, replay | `wasm.rs`; `browser_contract.rs`; `event_log.rs`; `web/forge-engine/src/engine.ts` | `rust/tests/browser_contract.rs` (5), `command_envelope.rs` (8), `event_log.rs` (8); `web/forge-engine/test` (15 Node); Playwright (13, outside the gate) |
| toolkit → agent protocol (both languages) → TypeScript client | `agent.py`; `agent.rs`; `typescript/src/client.ts` | `test_agent_contract.py` (20), `test_agent_python.py` (22), `test_agent_cli.py` (4), `test_typescript_sdk.py` (4) |
| delimited text → toolkit model | `delimited.py`; `delimited.rs` | `test_delimited.py` (8); `rust/tests/delimited.rs` (4) |

### 4b. Edges the vision needs that do not exist

Checked by searching each side for the other's types and modules at the base revision.

| Missing edge | Evidence | Consequence |
| --- | --- | --- |
| canonical model ↔ toolkit model, either direction, either language | `model.py`, `canonical_xlsx.py`, `intake.py`, `views.py` never import `toolkit` or `python_engine`; `toolkit.py`, `python_engine.py`, `agent.py`, `xlsx.py` never import `model`; in Rust the only `crate::model` uses outside `model.rs` are `screen_json_text` in `agent.rs:783` and `browser_contract.rs:118-657` | an Excel observation of the canonical export says nothing about the package an agent or the browser exports |
| canonical model → session with revisions | `Workbook` is immutable; no `Session` over it | agents cannot edit the model the receipt harness proves |
| source program ↔ canonical model | `source_graph.py` imports only `toolkit` and `xlsx` | source generation cannot start from, or return to, the model intake produces |
| primitive expression ↔ workbook formula text | `primitives.py` has no formula renderer or parser into cells; README.md:98-99 calls them separate APIs | a composition cannot be placed into cells, and a dependency closure cannot be extracted into a composition |
| browser command API ↔ agent protocol | different envelopes, revision types and operation sets (`protocol.ts` versus `agent-operations.json`) | a host must choose one and cannot move a session between them |
| canonical report ← calculation context | `calculate()` accepts `CalcContext`; the v1 report schema has no field for it | a report of a workbook with `NOW()` or `RAND()` does not say which clock or seed produced it |

## 5. Duplicated authority

Two independent engines are intentional and are not listed. Listed are places where one language, or one shared contract, holds the same authority twice.

| Authority | Copies | Note |
| --- | --- | --- |
| Which functions exist | `catalog/formulas.json`; Rust `SUPPORTED_FUNCTIONS` (`toolkit.rs:26-150`, equality asserted at `:2763`); Python reads the catalog at runtime (`python_engine.py:326-328`) but its dispatcher is a separate list (`__init__.py:3811-3925` plus eleven lazy functions); source `FUNCTIONS_BY_VERSION`; function bodies again in `source_runtime_v2..v6` | the generated runtimes are the only copies that reimplement behaviour rather than list names |
| Structural validation of a workbook | canonical `_workbook/_sheet/_cell/_formula` (`model.py:965-1068`; `model.rs`) and toolkit normalisers (`python_engine.py:202-300`; `toolkit.rs:395-418`) | two rule sets for what a sheet, cell and address may contain |
| Cell address parsing and normalisation | `python_engine._canonical` (`:166`); `model._parts/_encode` (`:947,957`); `toolkit.rs CellReference` (`:466`); `source_graph.split_reference/key` (`:104-127`); `workbook._cell_position/_normal_address` used by `agent.py:20` | the 2026-09-29 review's decision brief 9 ("one routine for cell addresses") is still open |
| Limits | `MAX_RANGE_CELLS` in both canonical modules and `MAX_ARRAY_CELLS` in `lib.rs:37`; populated-cell and dependency caps only on the toolkit side; the text cap only in the evaluators | the same workbook can be accepted by one path and refused by another |
| Refusal vocabularies | shared `refusal-codes.json` (good); agent's eight codes; `SourceDiagnostic`'s five; browser `EngineError`; canonical `Diagnostic.classification` | four vocabularies for "this was refused" |
| Defined-name table within Python | `workbook_defined_names()` builds from canonical `metadata` (`model.py:707`); the toolkit passes a separate JSON table into `Session` (`toolkit.py:98-103`) | one language, two carriers for one table |
| Export writers | Python: `canonical_xlsx` fresh writer and `xlsx.py` patch writer, with different refusal sets (`docs/capabilities-xlsx.md`, "Refused"); Rust: one writer in `xlsx.rs` | the canonical writer renumbers `localSheetId` for defined names (`canonical_xlsx.py:303`); the patch writer copies `xl/workbook.xml` byte for byte |

## 6. Companions a route needs

A **semantic-only artifact** is enough to calculate and to write a fresh package. A **preservation-capable artifact** is needed to patch an imported workbook, enforce its validations, keep its text formatting, and later update the original file.

| Route | Semantic-only artifact today | Preservation-capable companions today | Where they are lost |
| --- | --- | --- | --- |
| Python canonical | canonical JSON | none exist | n/a: the route never preserves |
| Python toolkit | `WorkbookModel.to_dict()` | `_source_baseline`, `_names`, `_feature_guard`, `_text_cells`, `_text_layouts`, `_imported_addresses`, `_import_metadata` | `to_dict()` drops all seven; a new `WorkbookModel(document=…)` has none (`toolkit.py:69-109`) |
| Rust toolkit | `WorkbookModel` JSON | `ImportedWorkbook` fields other than `session` | serialising the session's model alone drops them |
| Browser | `Snapshot` | baseline bytes and guard inside the worker's `WorkbookSession` | `export` without `includeLog` returns bytes only; replay needs the original bytes (`event_log.rs:45-65`) |
| Source compiler | program text | none; `check_fidelity` refuses what it cannot carry | by design |
| Agent protocol | responses | the owning process's workbook object | nothing crosses the wire except the exported file |

## 7. Proposed owners (to be refuted, then carried into the convergence proposal)

1. **Workbook meaning:** one versioned contract, built by extending the canonical model with the toolkit's meaning-bearing fields rather than by replacing either: typed bindings (`kind`, `required`, `min`, `max`, `choices`; proposed spec 06), presentation as a separate layer (`Style`, `column_widths`, text format), and an array-capable `Formula.result` in Rust. The canonical model is the better base because it already carries both versions, provenance per cell, structured diagnostics, source fingerprints and the intake carry list, and both engines hydrate it; the toolkit's extras are session and preservation companions, not meaning. This is the one recommendation that may need a `model_version` or `schema_version` change and therefore the repository's approval gate; the convergence proposal must present it with and without a version bump.
2. **Sessions and revisions:** the toolkit `Session` machinery in both languages (apply, set_inputs, preview, calculate, revision checks) is the asset; it should operate over the owner model rather than over a second model shape. Proposed spec 05 is the vehicle.
3. **Preservation envelope:** a named companion object owns what `_Baseline`, `ImportedWorkbook`'s package fields, `FeatureGuard` and `TextLayout` hold today, travels beside the owner model, and makes `not_carried` its inventory instead of a disclosure list. Contracts C1 and C4 in the architecture commentary.
4. **Expressions:** the evaluator AST in each language already serves primitives; the source compiler's `Node` should lower from and to it instead of parsing formula text a second time. Whether generated runtimes keep their own function bodies is decided by the equivalence data (WA2), not here.
5. **Transports:** agent protocol and browser command API remain separate encodings over one session owner; their revision types differ in representation only.
6. **Retire or merge:** the duplicate address routines within each language; the second structural validator once one owner exists; the double carrier of the defined-name table in Python; the fourth refusal vocabulary once the shared codes cover source and agent errors.

## 8. What this specification does not decide

- Whether the owner contract is version 1 with additive fields or version 2. That is the repository's approval gate and belongs to the convergence proposal.
- Which companions are mandatory for which route. Section 6 describes today; the preservation proposal decides tomorrow.
- How generated runtimes reuse engine semantics. That waits for the equivalence matrix.
- Anything about publishing, MCP, Excel runs or the source-overwrite refusals.

## 9. Acceptance for this document

- Every public operation in section 3 exists at the base revision at the line cited.
- Every "missing edge" in section 4b is reproducible by the search described there; an independent refuter attempts to find a conversion the map says does not exist and either fails or the map is corrected.
- Every duplicated authority in section 5 cites both copies.
- The ownership recommendations in section 7 have been attacked by a reviewer who did not write them, under the review protocol's "canonical-model spec" and "duplicated logic" lenses, and the objections are recorded in the pull request.
- Docs-only change: `python -m ruff check python tools examples setup.py` and `python -m pytest -q python/tests/test_corpus.py::test_capabilities_document_is_current python/tests/test_project_policy.py` pass.
