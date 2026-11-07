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
| Typed formula expression | `python/workbook_forge/expressions.py:95,137,264` | `_ExpressionParser` subclasses the evaluator's own parser; `parse_expression` returns the evaluator's parse tree; `Expression` is a text-backed object over it, used by `analyze_formula` and the source compiler | none; follows the evaluator |
| Calculation report | `schemas/calculation-report.v1.schema.json`; `model.py:468`; `model.rs:1523` | `{report_version, stage, engine, behavior_profile, schema_version, model_version, source_fingerprint, inputs[{name, sheet, address, origin, value}], outputs[{name, sheet, address, origin, result}], diagnostics, calculated_formulas, unresolved_formulas}` | `report_version` plus the model's two versions; `additionalProperties: false` |

## 2. Concept by representation

Present (✓), absent (—), present under another name or with different semantics (≈, explained).

| Concept | Canonical | Toolkit snapshot | Toolkit companions | Source program | Primitive expression | Browser projection | Agent protocol |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sheet identity | `name`, optional `dimensions` | `id` and `name`, `column_widths` | — | `sheet` plus ordered `sheets` identities from profile 3 (`source_graph.py:508-512`) | — (no sheets) | `name` | `describe` returns `{id, name, populated_cells}` |
| Cell address | A1 within a sheet; `CellId{sheet, address}` | A1 keys; `CellAddress{sheet, address}` | `insertion_bound` (Rust) and `_imported_addresses` (Python) | `Sheet!A1` string keys (`source_graph.py:104-127`) | none; named inputs replace addresses | `Address{sheet, address}` | `{sheet, address}` |
| Value | `value` plus `data_type`; Rust `Scalar::{Blank, Number, Text, Boolean, Error}` | `CellValue::{Number, Text, Boolean, Error, Blank}` plus `cached_value` | — | `Node` literal | `PrimitiveValue::{Scalar, Range, Array, Omitted}` | `Scalar` or `{rows}` | JSON scalar |
| Array result | **— by contract in both languages**: the schema's `$defs/value` has no array alternative, the Python hydrator refuses lists (`model.py:1068-1087`), Rust `Formula.result: Scalar` has no array variant (`model.rs:112-128`), and both calculators report "array results are unsupported" (`model.py:883`; `model.rs:685`) | `CalculatedValue::Array` | — | — | `Array` | `{rows}`; projected only when 1×1 (PR #47) | `arrays: false` in the TypeScript client |
| Formula | `Formula{expression, dependencies, result}`; dependencies stored | `formula: String`; dependencies derived by the graph (`python_engine.py:352-360`) | — | `Node` tree (`kind, value, args`) lowered from the evaluator's own parse tree (`expressions.py:95`; `source_graph.py:307,602`); five functions in profile 6 (`source_graph.py:31-38`) | node tree over the evaluator AST | `formula: string` | `formula: string` in `read`/`edit` |
| Imported cache versus calculated | `provenance.origin` on the cell; report `origin ∈ {calculated, authored, blank, unavailable}` | `cached_value` separate from `value`; `blocked_reason` | — | `defaults` (literal values) | — | `value` is the engine's result | `explain` returns `value_origin` |
| Presentation | `number_format` only | `Style{number_format, bold, font_color, fill_color, horizontal}`, `column_widths` | `_text_cells`, `_text_layouts` (`TextLayout` `xlsx.py:228`; `xlsx.rs:832`) | refused beyond `s`/`t` attributes (`source_package.py:8-22`) | — | `text`, `textFormat{columns, rows, generalRows}` | `style` on `edit` |
| Application bindings | `Bindings{inputs, outputs}`: name → `CellId`, **untyped** | `inputs`: name → `{sheet, address, kind, required, min, max, choices}` (`python_engine.py:267-299`; `toolkit.rs:364`); `outputs`: name → address | — | `defaults` per cell; `--inputs` overrides at emit (`source_cli.py:227`) | named inputs as native values | — (no bindings) | `describe` inputs/outputs; `set_inputs`, `preview_inputs` |
| Defined names | `metadata.defined_names` carried by intake; `workbook_defined_names()` builds a table (`model.py:707`) | — in the snapshot | `_names` JSON table passed to `Session` (`toolkit.py:98-103`); Rust `OwnedWorkbook::names()` (`agent.rs:34`) | refused: `compile_workbook` parses each formula with no name scope (`source_graph.py:602`), so a formula using a defined name fails | — | indirect: imported workbooks only (`wasm.rs:12-15`) | indirect through the owning workbook |
| Source identity and provenance | `provenance{origin, source}`, `source_path` (redacted for agents), `source_fingerprint` on report and views | — | `_Baseline.source`; `ImportedWorkbook.source` | `bounded_package` fidelity check (`source_graph.py:546`) | — | `Baseline{kind, name, sha256, bytes}` in the event log | `describe` `source.kind ∈ {authored, xlsx}` |
| Preservation envelope | — (fresh package on export); `metadata.intake.not_carried` lists what was dropped (`intake.py:118-199`) | — | `_Baseline.parts`, `infos`, `comment`, `document_json`, `merged`, `compatibility_version`; Rust `baseline: Package`, `workbook_part`, `sheet_parts`, `merged`, `date1904`, `compat3` | refused: `check_fidelity` | — | baseline bytes kept in the worker for export | through the owning workbook |
| Feature guards | — (`not_carried`) | — | `FeatureGuard{rules, pivots, charted}` | — | — | enforced inside Rust on import; surfaces as error codes | refusals rewritten to `unsupported_operation` (`docs/agent-protocol.md:123-125`) |
| Revision and session | — (`Workbook` is immutable; `with_input` returns a new one, `model.py:534`) | `revision: u64`; `Session.apply/set_inputs/calculate` (`python_engine.py:623`; `toolkit.rs:1735`) | — | — | — | `Revision` string; `base_revision`; `revision_conflict` | `revision`, `expected_revision` |
| History | — | — | — | — | — | `EventLog` with undo and redo intents, replay (`event_log.rs:102-110`) | — |
| Calculation context | `calculate(workbook, context=CalcContext)` (`model.py:364`); **the v1 report has no context field** | both toolkit reports carry it: Python `report["calc"]` (`python_engine.py:528`), Rust `CalculationReport.calc` (`toolkit.rs:1203-1210`) | — | — | — | `CalcContext{now_ms, utc_offset_minutes, seed}` | not exposed |
| Diagnostics and refusals | `Diagnostic{code, classification, message, sheet, address, function}` | `Diagnostic{code, message, sheet, address}`; `ToolkitError{code, message}`; codes from `catalog/refusal-codes.json` | — | `SourceDiagnostic(code, message)`: `unsupported_source`, `invalid_source`, `unsupported_fidelity`, `resource_limit`, `invalid_reference` | `PrimitiveError{code, message}` | `EngineError{code, message, expected_revision, current_revision, refusal, …}` | eight named codes plus the engine's structural code (`docs/agent-protocol.md:27-33`) |
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
| canonical JSON → both engines hydrate and calculate | `model.py:340,364`; `model.rs` | `test_canonical_model.py` (14), `test_canonical_model_diagnostics.py` (4), `test_volatile.py` (canonical cases); the Rust side runs by subprocess through `rust/examples/canonical_calc.rs` |
| toolkit document → both engines calculate (Python `Session` and the native bridge) | `python_engine.py:623`; `toolkit.rs:1735`; `native/src/lib.rs` | `test_dual_engines.py` (21, builds toolkit-shape documents), `test_python_engine.py` (26), `test_defined_names.py` (9), `test_volatile.py` (toolkit cases) |
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
| canonical model ↔ toolkit model, either direction, either language | `model.py`, `canonical_xlsx.py`, `intake.py`, `views.py` import no model type from `toolkit` or `python_engine`; they reuse the toolkit side's OOXML package helpers and one address routine (`canonical_xlsx.py:35-40`; `intake.py:33-34`). `toolkit.py`, `python_engine.py`, `agent.py`, `xlsx.py` never import `model`. In Rust the `crate::model` uses outside `model.rs` are `screen_json_text` (`agent.rs:783`; `browser_contract.rs:118-657`) and the hash helpers `sha256` and `content_fingerprint` (`event_log.rs:965,991`). The one tool that exercises both, `tools/corpus_check.py`, runs them as separate lanes (`:230`, `:325`) | an Excel observation of the canonical export says nothing about the package an agent or the browser exports, and the reverse: observations of the toolkit export (`tools/excel_oracle.py:526`) do not grade the canonical writer, which is the only writer behind the strict evidence gate (`canonical_xlsx.py:455`) |
| canonical model → session with revisions | `Workbook` is immutable; no `Session` over it | agents cannot edit the model the receipt harness proves |
| source program ↔ canonical model | `source_graph.py` imports only `toolkit` and `xlsx` | source generation cannot start from, or return to, the model intake produces |
| primitive expression ↔ workbook formula text | `primitives.py` has no formula renderer or parser into cells; `expressions._render` renders evaluator nodes to text but raises on the `input` kind primitives emit (`expressions.py:174,224`); README.md:98-99 calls them separate APIs | a composition cannot be placed into cells, and a dependency closure cannot be extracted into a composition |
| source program → evaluator parse tree (reverse lowering) | `from_excel` lowers the evaluator's tree into `Node` (`source_graph.py:307`); the reverse, `formula_text`, renders `Node` to text that is parsed again (`source_graph.py:625,768`); no `Node` → parse-tree function exists; there is no Rust source compiler at all (the Rust half is generated source compiled by `rustc`, `source_process.py`) | source edits return to a workbook only through text, and only in Python |
| browser command API ↔ agent protocol | different envelopes, revision types and operation sets (`protocol.ts` versus `agent-operations.json`); the only shared item is the request-size constant (`browser_contract.rs:111,348`) | a host must choose one and cannot move a session between them |
| canonical report ← calculation context | `calculate()` accepts `CalcContext`; `calculation_report()` takes none (`model.py:468`; `model.rs:1523`) and the v1 report schema has no field for it | a report of a workbook with `NOW()` or `RAND()` does not say which clock or seed produced it |

## 5. Duplicated authority

Two independent engines are intentional and are not listed. Listed are places where one language, or one shared contract, holds the same authority twice.

| Authority | Copies | Note |
| --- | --- | --- |
| Which functions exist | `catalog/formulas.json`; Rust `SUPPORTED_FUNCTIONS` (`toolkit.rs:26-150`, equality asserted at `:2763`); Python reads the catalog at runtime (`python_engine.py:326-328`) but its dispatcher is a separate list split between `_function`'s table (`__init__.py:3811-3925`) and `_eval`'s call branch (`__init__.py:2758` onward: NOW, TODAY, the RAND family, SEQUENCE, FILTER, SORT, UNIQUE, IF, IFERROR, IFNA, IFS and others); source `FUNCTIONS_BY_VERSION`; function bodies again in `source_runtime_v2..v6` | the generated runtimes are the only copies that reimplement behaviour rather than list names |
| Structural validation of a workbook | canonical `_workbook/_sheet/_cell/_formula` (`model.py:965-1068`; `model.rs`) and toolkit normalisation (`python_engine.py:201-300` `_normalize_model`; `toolkit.rs:403-418` serde shape with `deny_unknown_fields` plus `validate`) | two rule sets for what a sheet, cell and address may contain; each refuses the other's fields |
| Cell address parsing and normalisation | `python_engine._canonical` (`:166`); `model._parts/_encode` (`:947,957`); `toolkit.rs CellReference` (`:466`); `source_graph.split_reference/key` (`:104-127`); `workbook._cell_position/_normal_address` used by `agent.py:20` **and by the canonical writer** (`canonical_xlsx.py:37` → `workbook.py:561`) | the 2026-09-29 review's decision brief 9 ("one routine for cell addresses") is still open; the canonical path itself uses two routines |
| Limits | `MAX_RANGE_CELLS` in both canonical modules and `MAX_ARRAY_CELLS` in `lib.rs:37`; populated-cell and dependency caps only on the toolkit side; the text cap only in the evaluators | the same workbook can be accepted by one path and refused by another |
| Refusal vocabularies | shared `refusal-codes.json` (good); agent's eight codes; `SourceDiagnostic`'s five; browser `EngineError`; canonical `Diagnostic.classification` | four vocabularies for "this was refused" |
| Defined-name table within Python | `workbook_defined_names()` builds from canonical `metadata` (`model.py:707`); the toolkit passes a separate JSON table into `Session` (`toolkit.py:98-103`) | one language, two carriers for one table |
| Export writers | Python: `canonical_xlsx` fresh writer and `xlsx.py` patch writer, with different refusal sets (`docs/capabilities-xlsx.md`, "Refused"); Rust: one writer in `xlsx.rs` | half delegation: the canonical writer borrows package scaffolding from the patch writer (`_fresh_parts`, `_styles`, `_xml`: `canonical_xlsx.py:115,219,328` → `xlsx.py:734,162,573`), so the duplicated authority is cell and sheet serialisation and the refusal sets, not package construction. The canonical writer renumbers `localSheetId` for defined names (`canonical_xlsx.py:303`); the patch writer copies `xl/workbook.xml` byte for byte |

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

## 7. Proposed owners (refuted once; objections applied and recorded in section 10; carried into the convergence proposal)

1. **Workbook meaning: one versioned contract, built from one of the two existing shapes.** The map does not choose the base; the convergence proposal must cost both, each with and without a version bump, because either choice is a schema change of comparable size and so meets the repository's approval gate.
   - *Fields only the canonical shape has:* explicit `data_type`; `provenance{origin, source}` per cell and per workbook; stored `Formula.dependencies`; `Formula.result` on the cell; `metadata` (defined names, `intake.not_carried`); `diagnostics[{code, classification, message, sheet, address, function}]`; `source_path`; `model_version`; `Sheet.dimensions`. The toolkit refuses all of them (`python_engine.py:235`; `toolkit.rs:403` `deny_unknown_fields`).
   - *Fields and rules only the toolkit shape has:* `Sheet.id` as a stable identity distinct from `name` (`python_engine.py:220-224`; `toolkit.rs:340-346`); `blocked_reason` per cell as a write and bind guard (`python_engine.py:251,276`); the rule that a formula cell carries no authored value (`python_engine.py:240`); input `kind` closed to `{number, text, boolean}` with `required`, `min`, `max` and 1 to 32 `choices` (`python_engine.py:270-299`); `revision`; `Style` and `column_widths`. The canonical hydrator refuses all of them (`model.py:965-1068`; schema `additionalProperties: false`).
   - *Edge evidence:* both engines hydrate both shapes; every edge in section 4a except canonical intake and canonical export consumes the toolkit shape; the toolkit is the only shape with an `.xlsx` importer in both languages (canonical intake is Python-only); the native bridge takes the toolkit shape (`native/src/lib.rs:3-4`). Choosing the canonical base therefore costs a migration of every consumer; choosing the toolkit base costs adding versions, provenance, diagnostics and the carry list to the shape every consumer already takes.
   - *What favours the canonical base* is discipline rather than fields: two version numbers enforced on read and write, provenance per cell, the receipt harness and the strict evidence gate, and the intake carry list. *What favours the toolkit base* is consumer reach and the companions that already exist around it. Arrays are refused by contract in both shapes' canonical form (section 2), so an array-capable result is a contract change on either path, not a Rust detail.
2. **Sessions and revisions:** the toolkit `Session` machinery in both languages (apply, set_inputs, preview, calculate, revision checks) is the asset; it should operate over the owner model, whichever base is chosen, rather than over a second model shape. Proposed spec 05 is the vehicle.
3. **Preservation envelope:** a named companion object owns what `_Baseline`, `ImportedWorkbook`'s package fields, `FeatureGuard` and `TextLayout` hold today, travels beside the owner model, and makes `not_carried` its inventory instead of a disclosure list. Contracts C1 and C4 in the architecture commentary.
4. **Expressions:** the evaluator's parse tree already serves primitives and the source compiler's forward lowering (`expressions.py:95`; `source_graph.py:307,602`). The gaps are the reverse lowering from a source `Node` back to that tree (today `formula_text` renders to text that is parsed again, `source_graph.py:625,768`), a renderer from a composition to a worksheet formula (`expressions._render` raises on `input` nodes, `expressions.py:224`), and a Rust source compiler, which does not exist. Whether generated runtimes keep their own function bodies is decided by the equivalence data (WA2), not here.
5. **Transports:** agent protocol and browser command API remain separate encodings over one session owner; their revision types differ in representation only.
6. **Retire or merge:** the duplicate address routines within each language (the canonical writer itself uses two); the second structural validator once one owner exists; the double carrier of the defined-name table in Python; the fourth refusal vocabulary once the shared codes cover source and agent errors.

## 8. What this specification does not decide

- Whether the owner contract is version 1 with additive fields or version 2. That is the repository's approval gate and belongs to the convergence proposal.
- Which companions are mandatory for which route. Section 6 describes today; the preservation proposal decides tomorrow.
- How generated runtimes reuse engine semantics. That waits for the equivalence matrix.
- Anything about publishing, MCP, Excel runs or the source-overwrite refusals.

## 9. Acceptance for this document

- Every public operation in section 3 exists at the base revision at the line cited.
- Every "missing edge" in section 4b is reproducible by the search described there; an independent refuter attempts to find a conversion the map says does not exist and either fails or the map is corrected.
- Every duplicated authority in section 5 cites both copies.
- The ownership recommendations in section 7 have been attacked by a reviewer who did not write them, under the `contract` and `simplification` lenses in `tools/review/lenses.json`, and the objections are recorded in section 10 and in the pull request.
- Docs-only change: `python -m ruff check python tools examples setup.py` and `python -m pytest -q python/tests/test_corpus.py::test_capabilities_document_is_current python/tests/test_project_policy.py` pass.

## 10. Refutation record

One independent refuter read the first draft against the code at the base revision. Counts: 3 refuted, 9 weakened, 12 confirmed, 6 citation errors. All were applied above.

| Finding | Effect on the map |
| --- | --- |
| Refuted: the source compiler "parses formula text a second time". `expressions._ExpressionParser` subclasses the evaluator's parser and `from_excel` lowers its tree | Recommendation 4 rewritten around the reverse lowering and the missing Rust compiler; section 2 "Formula" and section 4b corrected |
| Refuted: arrays are a Rust-only gap in the canonical model. The schema, both hydrators and both calculators refuse them | Section 2 "Array result" and recommendation 1 corrected |
| Refuted: "the only `crate::model` uses" outside `model.rs`. `event_log.rs:965,991` use its hash helpers | Section 4b evidence corrected; conclusion unchanged |
| Weakened: the canonical route does import toolkit-side OOXML helpers and one address routine; the canonical writer delegates package scaffolding to the patch writer | Sections 4b and 5 corrected |
| Weakened: recommendation 1 was one-sided; the field evidence is balanced and the edge evidence leans toolkit | Recommendation 1 rewritten as two costed bases without a choice |
| Weakened: Python's toolkit report also carries the calculation context; the agent error vocabulary is open-ended; a defined name in the source compiler is refused, not untested; "eleven lazy functions" had no anchor | Section 2 and section 5 corrected |
| Citation errors: `model.rs:1523` not `:1615`; `toolkit.rs:403-418` is the struct, the validator is `validate`; lens names are `contract` and `simplification` | Corrected |
| Confirmed: no conversion between the two models in any of nine source roots; no session over the canonical model; no bridge between the two protocols; no context in the v1 report; all test counts and the sampled citations | Unchanged |

A second correction, found by the refuter of spec 09 on 2026-11-06: section 4a had listed `test_dual_engines.py` under the canonical edge; that suite builds toolkit-shape documents and drives both engines through `python_engine` and the native bridge, so it now appears on its own toolkit row, and the canonical Rust calculator is reached by subprocess. Two citations in section 7.1 moved by one line (`python_engine.py:235`, `toolkit.rs:403`).

What the refuter added to the central conclusion: capability does accrue on the toolkit side, but Excel evidence is not canonical-only. Observations have been made of the toolkit export (`tools/excel_oracle.py:526`); the canonical side holds the strict evidence gate and the receipt harness. Neither route's observations grade the other.
