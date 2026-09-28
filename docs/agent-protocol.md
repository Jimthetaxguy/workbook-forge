---
author: Codex
created: 2026-09-28
agent: codex/Codex
date: 2026-09-28
type: interface-contract
status: implemented
summary: Agent operation contract over independently implemented workbook SDKs.
---
# Agent workbook operations, version 1

The adapter owns one transient workbook session and one host-selected export
directory. It exposes deterministic SDK operations; it does not run an LLM or
execute workbook text as instructions. Python and Rust implement the adapter
independently. This JSON-lines transport is framework-neutral, not an MCP server.

These nine operations act on a cell-based workbook model. Workbook-free native
compositions use the separate [primitive API](primitives.md). See the
[README quick start](../README.md#agent-sdk-quick-start) for an executable host example.

## Envelope and limits

Request: `{"id":"call-1","operation":"describe","arguments":{}}`.
`id` is optional text (at most 128 UTF-8 bytes), echoed as text or null.
Only these three fields are accepted; arguments defaults to an empty object.

Response: `{"schema_version":1,"id":"call-1","operation":"describe","ok":true,"revision":0,"result":{...}}`.
Failure replaces result with `"error":{"code":"invalid_request","message":"..."}`
and sets ok false. Error wording may differ between languages; codes must agree.
Use invalid_request, unknown_operation, revision_conflict, invalid_reference,
invalid_input, unsupported_operation, resource_limit, export_error, or the
underlying engine's structural error code. Unknown input properties are rejected.
Schema/argument-shape failures, including empty/duplicate collections and invalid
filename syntax, use invalid_request. Unknown bindings and input constraint
failures use invalid_input. Filesystem or XLSX export refusals use export_error.
Independently valid IDs and nonempty operation names are echoed even when other
envelope fields are invalid. Missing/invalid fields become null individually.
Numeric control fields require exact JSON integers; booleans are rejected.
Revisions and offsets are unsigned 64-bit integers.

Requests are at most 1 MiB UTF-8; responses at most 256 KiB UTF-8. Requests are
processed sequentially by the JSON-lines runner. Responses contain no logging.
Limit violations fail explicitly. Mutations must not commit and then fail merely
because their response is too large. No durable session or audit storage is promised.

Read/explain pagination uses offset (default 0), limit (default 40, maximum 100),
and optional expected_revision. An offset greater than zero requires
expected_revision. A conflicting revision fails instead of mixing pages from
different snapshots. Page fields are cells, offset, limit, total_cells,
next_offset (integer or null), and truncated (whether more cells remain).
Order is worksheet order, then row, then column. Return at most 100 direct
dependencies per cell with dependency_count and dependencies_truncated. Report
diagnostic_count and diagnostics_truncated when clipping diagnostics to 100.
Large individual values can still exceed the response budget and fail explicitly.

## Operations

`discover {}` returns the catalog in catalog/agent-operations.json, containing
input/output schemas, descriptions and side-effect classifications. The Rust
crate embeds an identical copy in src/agent_operations.json for standalone
packaging; tests check the copies match.

`describe {}` returns sheets (id/name/populated_cells), inputs, outputs, limits,
and source (`kind`: authored or xlsx). It exposes explicit bindings, not guessed
business meaning. It must not dump the full dependency graph or cell contents.

`read {sheet?, range?, output?, offset?, limit?, expected_revision?}` selects
either a sheet (optionally a rectangular A1 range) or one named output's full
dependency closure, including referenced blank cells. Exactly one of sheet and
output is required; range requires sheet. Sheet/range views list populated cells.
Cell records contain sheet_id, sheet, address, content (the original normalized
cell record or {value:null} for an absent cell), dependencies (sheet!A1 strings),
dependency_count and dependencies_truncated. Keep original formulas and imported
cached_value distinct. Include bounded relevant diagnostics in the page.
Sheet IDs are opaque within a loaded model and need not match across imports or
languages. Use sheet names and addresses when comparing source locations across
implementations. An omitted cached_value and cached_value:null both indicate no
available cache. Evaluated cell counts measure implementation work and may differ
while calculated values agree.

`calculate {outputs?, workers?}` calculates an immutable snapshot. Outputs is
an optional nonempty unique list of at most 100 explicit output names; omission
uses all named outputs, requiring at most 100. Workers defaults to 1, maximum 8.
No bindings means invalid_input: agents must bind business outputs explicitly.
Return revision, outputs, diagnostics, diagnostic_count, diagnostics_truncated,
evaluated_cell_count and stale. Do not return the full values map. A selected
output calculation must not be blocked by unrelated unsupported outputs.

`explain {output, offset?, limit?, expected_revision?}` returns the same paged
dependency closure as read, plus output, binding, value, and bounded calculation
diagnostics. Each cell additionally has calculated_value and value_origin
(calculated, authored, blank or unavailable). Unavailable results are null and
must not be substituted with cached values. Trace formulas and references rather
than generating an ungrounded natural-language rationale.

`preview_inputs {values, expected_revision, outputs?}` validates a nonempty map
of at most 100 named inputs against a detached snapshot and calculates the
selected outputs before and after. It returns applied:false, base_revision,
proposed_revision, changes (sheet/address/before/after values), before and after
compact calculation reports. It never mutates the owned session or writes files.

`set_inputs {values, expected_revision}` applies the same bounded input map
atomically. Return previous_revision, revision and changed_cells
(sheet/address/fields, with fields=["value"]). No-op entries may be reported.

`edit {edits, expected_revision}` applies 1–100 engine Edit records atomically.
Each record has sheet/address and supported value/formula/style fields; unknown
fields are invalid. At least one of value/formula/style is required; value and
formula cannot appear together. The discovery schema requires formula to be a
string and style to be an object: null-only formula/style edits are not supported.
Use value:null to clear cell content; an empty style object resets an authored
cell's supported style. Return previous_revision, revision and changed_cells
with the submitted fields. The common agent import profile permits only existing
original-cell value/formula changes. Imported style edits, new
cells, grouped/table results and 1904 formulas are rejected before mutation.
These restrictions also apply to set_inputs and preview_inputs. The lower-level
Python adapter may support additional edits outside this shared agent profile.
The response is a compact receipt; read or preview to retrieve values.

`export {filename, expected_revision}` calculates and exports through the owning
XLSX adapter, preserving imported baselines. Filename matches
`[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.xlsx` and names one new file in the configured
export directory. Absolute paths, path traversal and overwrites are rejected.
Return filename, revision and bytes. Unsupported required calculations refuse
export. Export does not mutate workbook revision and never alters its source.

Failed selected outputs produce successful calculate/explain/preview envelopes
with diagnostics, omitted failed output entries and null explanation values.
Export refuses these failed calculations. Formula results use value_origin
calculated; literals use authored, absent/authored blanks use blank, and blocked
or failed cells use unavailable. Preview enforces the same imported-content
edit restrictions as the corresponding mutation.

## Library and process surfaces

Python: `AgentWorkbook(model, output_dir=...)`, `.call(operation, arguments=None,
request_id=None)` and `.handle(request)` return response dictionaries;
`operation_catalog()` is available independently of a workbook. Optional native
backend selection remains explicit on the supplied model.

Rust: `agent::AgentWorkbook::new(model, output_dir)` and
`::from_imported(imported, output_dir)`; `.handle(serde_json::Value)` returns a
JSON response; `agent::operation_catalog()` returns the catalog. The adapter
owns the actual Rust Session or ImportedWorkbook.

Both runners load one host-selected source or the synthetic scenario at startup.
Agents cannot select arbitrary input files through operations. Python command:
`workbook-forge agent SOURCE --bindings BINDINGS --output-dir DIRECTORY`, or
`workbook-forge agent --scenario --output-dir DIRECTORY`.
From the repository root, the corresponding Rust command is:

```sh
cargo run --manifest-path rust/Cargo.toml --example agent_workbook -- --scenario --output-dir exports
```

Replace `--scenario` with `SOURCE --bindings BINDINGS` to load an imported model.
Each line is one request; EOF closes the transient session. Request errors return
structured responses and leave the process usable for the next request.

The bindings file is a JSON object with `inputs` and `outputs` maps. It uses the
same explicit cell bindings as `WorkbookModel`; the scenario example exposes
its bindings through `model.to_dict()`. Importing XLSX alone does not recover
application input names or constraints automatically.

## Acceptance

Discover operations and bindings, read the profit closure, preview unit_price=25
without changing revision, apply at the observed revision, calculate revenue
9,250, profit 3,290 and break-even units 1000/17; explain profit through source cells,
export, and reimport. Reject a stale edit, invalid input, unknown property,
oversized request, unsafe export path and unsupported output without partial
changes. Run against both independent implementations and installed packages.

These acceptance checks establish the shared SDK contract. Full Excel Desktop
open/edit/recalculate/save/reimport acceptance remains separate; see
[Excel observations](excel-observations.md).
