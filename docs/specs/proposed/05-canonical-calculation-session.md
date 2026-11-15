---
type: technical-specification
status: proposed — reframed 2026-11-09 to the owner model under decision G-A (spec 09 section 3)
base: main c27ac63
summary: Add a revisioned calculation session over the owner workbook model in both engines, so set-input/preview/calculate carry model revisions like the agent session already does. Reframed under decision G-A (2026-11-09) from the canonical Workbook to the owner model, which grows from the toolkit shape. The session contract that spec 09 step 1 builds, with the nine requirements of spec 09 section 10, is the last section.
---

# 05 — Revisioned calculation session on the owner model

Reframed on 2026-11-09: decision G-A chose the toolkit shape as the base of the owner
contract (spec 09 section 3), so this session operates on the owner model through spec
09 step 1, starting from the toolkit `Session` machinery rather than a second session
on the canonical path. The problem and the operations below are unchanged; where the
text says "canonical" it names the v1 documents the session must still accept through
spec 09 step 0.

## Problem

The JSON-lines agent ([agent-protocol.md](../../agent-protocol.md)) has
revisions, `expected_revision`, `revision_conflict`, `preview_inputs` and
`set_inputs`, but it runs on the older toolkit workbook form. The canonical
path calculates once and writes a report with no revision history.
CONTEXT.md, as it read before spec 09 step 1: "Typed binding constraints and
the calculation session with revisions are not [ported]". The glossary already defines a **calculation
snapshot** (immutable model revision; stale work cannot replace current
results).

## Outcome

An agent can hydrate a canonical workbook, preview an input change, apply it
at an observed revision, calculate, and get a report that names the revision
it came from. A stale apply is refused. This is the session the joined
headless command (07) and future agent operations run on.

## Scope

- Python: a session in `python/workbook_forge/session.py` taking the owner model
  directly (Spec 1: no DTO in between), built from the toolkit `Session` machinery
  (spec 09 step 1); documents of either v1 shape enter through spec 09 step 0.
- Rust: the same session in `rust/src/session.rs`, taking the owner model.
- Operations: `revision`, `preview_inputs(values, expected_revision)`,
  `set_inputs(values, expected_revision)`, `calculate() -> report`.
- Inputs are addressed by `bindings.inputs` names only.

## Non-goals

- Moving the JSON-lines agent onto the canonical form (later, behind the same
  session).
- Durable sessions, audit storage, undo history.
- Typed binding validation (spec 06).

## Design

- Revision is an integer starting at 0, incremented by each successful
  `set_inputs`. `preview_inputs` never mutates and never writes files.
- Calculation runs on one committed snapshot. A failure publishes nothing.
- Error codes reuse the agent protocol's: `revision_conflict`,
  `invalid_input`, `invalid_reference`, `resource_limit`.
- Applied inputs become cell values with provenance `authored`; calculated
  formula results set `Formula.result` and the report marks them `calculated`.
  An imported cache is never promoted.

## Schema / model-version impact

- Workbook model: `revision` leaves the document and becomes session state (spec 09
  section 4; the change set is in spec 09 step 1).
- Calculation report: one `calculation-report.v2` merging the toolkit
  `CalculationReport` and the canonical v1 report, carrying `revision`,
  `base_revision` and the calculation context (spec 09 section 4). Decided with G-A
  on 2026-11-09; the v1 report schema stays readable until spec 09 step 6.

## Acceptance tests

```python
# python/tests/test_canonical_session.py
def test_new_session_starts_at_revision_zero(): ...
def test_preview_does_not_change_revision_or_values(): ...
def test_set_inputs_at_current_revision_increments_and_recalculates(): ...
def test_set_inputs_at_stale_revision_is_revision_conflict(): ...
def test_unknown_binding_name_is_invalid_input(): ...
def test_failed_calculation_publishes_no_partial_result(): ...
def test_report_carries_revision_and_imported_cache_not_promoted(): ...
```

```rust
// rust/tests/canonical_session.rs
#[test] fn preview_is_detached() {}
#[test] fn stale_revision_is_revision_conflict() {}
#[test] fn report_matches_python_on_operating_scenario_after_set_inputs() {}
```

The cross-engine comparison uses `fixtures/operating-scenario.workbook.json`
and a new shared input-change fixture.

## Dependencies

None to start. Spec 06 builds typed validation on top of it; spec 07 uses it.

## Open questions for James

Both answered by decision G-A (spec 09 section 3, 2026-11-09): the session is the
toolkit session over the owner model, not a second session on the canonical path; the
report is `calculation-report.v2`.

Spec 09 step 1 was built with the defaults below. None moves a version field, and each
can be changed without touching the others. They stand unless James decides otherwise.

1. **The legacy `revision` key.** No writer emits it. An engine session built from
   document text still starts at the key's value, so a v1 document that answered wire
   revision 3 through an agent runner still does. `CalculationSession` does not use the
   key: it starts at 0 and `open` lists the key as dropped. The alternative is to let
   `open` seed from the key as well, which would contradict "starting at 0" in Design.
2. **`model_version` is null for a toolkit-shape session.** The toolkit shape has no
   such field in v1, so the report says so rather than claiming 1.
3. **`calc` is always present** in the v2 report, including on a pass with no volatile
   cell. The legacy report carries it only when a volatile cell was evaluated.
4. **`origin` on inputs is tracked by the session**, not read from the document. It
   says `authored` only for cells this session (or the session it was forked from)
   wrote; a canonical document's cell provenance is not consulted.
5. **The two formula counts range over the pass's closure**, not the whole workbook,
   so a formula no bound output depends on is not reported as unresolved.
6. **A pass that ends early is not published**, on the legacy report path as well as
   the v2 path, because both share one publish step. Before this change an early-ended
   pass at the current revision replaced the published report.

## Session contract (spec 09 step 1 and section 10)

This section is the session contract spec 09 step 1 asks for. It describes what the
change builds in `python/workbook_forge/session.py` and `rust/src/session.rs` and on
the engine sessions beneath them, and it states the nine requirements of spec 09
section 10 with what step 1 does about each. Line citations are to `main c27ac63`, the
base of the change; files the change adds are cited by path only.

### 1. Terms

- **Owner model.** The workbook contract decision G-A chose, grown from the toolkit
  shape. In step 1 it is the v1 toolkit document without `revision`:
  `{schema_version: 1, sheets, inputs, outputs}`. It gains its remaining fields at
  steps 2 and 6.
- **Engine session.** The existing toolkit session, `python_engine.Session` and
  `toolkit::Session` (and the native bridge's `Session` over the latter). It holds a
  model, a revision, the published report and the set of cells edited since that
  report. Every signature it had is kept.
- **Calculation session.** `CalculationSession`, the contract layer this spec names. It
  wraps one engine session and adds the either-shape entry, the report identity fields
  and the v2 report.
- **Revision.** An unsigned 64-bit counter on the session. It adds 1 for each committed
  non-empty edit batch and is refused at 18446744073709551615 with `resource_limit`,
  "revision counter exhausted".
- **Pass.** One calculation: a read of the model at one revision under one
  `CalcContext`, producing one report.
- **Base.** The published report a pass reuses values from. A pass with no base is a
  full pass.
- **Fork.** A detached copy of a session: the same model, defined names, clock offset,
  revision and authored-cell set, with no published report and an empty edited-cell
  set, sharing nothing afterwards. Its first pass is therefore a full pass. Previews
  run on forks.

### 2. Document revision rule

`revision` is session state. It is not a field of the owner model, and nothing writes
it into a document. A v1 toolkit document written before this change may still carry
the key at its root, so every reader keeps accepting it. `schema_version` stays 1
because every document that read before still reads, and a document written now reads
in an older build, whose readers default the key to 0.

A reader that finds the key requires an integer from 0 to 18446744073709551615. The
values `-1`, `true`, `false`, `null`, `1.5`, `"1"` and 18446744073709551616 are refused
by both readers: Python with `invalid_model`, "revision must be an unsigned 64-bit
integer" (`python/workbook_forge/python_engine.py:210-211`), Rust when the document is
deserialised, with the reader's own message and no code of its own; `open` reports
either refusal as `invalid_model`. What happens to a valid value depends on the entry
point.

| Entry point | Reads the key | Writes the key |
| --- | --- | --- |
| Engine session built from document text or bytes: `python_engine.Session(model_json)`, the native `Session(model_json)`, `WorkbookModel(document=...)` in `python/workbook_forge/toolkit.py`, and the Rust paired reader `toolkit::read_document`, which returns the model with its legacy revision (0 when absent) for the caller to pass to `Session::with_names_at` | A valid key seeds the counter. An explicit `revision` seed argument wins over the key. Without either the counter starts at 0 | Never. `snapshot()` and `to_dict()` have no `revision` |
| Engine session built from a Rust `WorkbookModel` value: `Session::new`, `Session::with_names` | The struct has no revision field; the counter starts at 0. The seeded constructor `Session::with_names_at` takes the value explicitly and is used for forks and by callers of the paired reader | Never |
| Plain deserialisation into the Rust `WorkbookModel` | The key is checked and dropped | Never; the struct cannot hold it |
| Stateless `calculate(model_json)` and `inspect(model_json)`, both engines | The report's `revision` is the key's value when present, 0 when absent, as before | Not applicable |
| `to_toolkit` (`python/workbook_forge/convert.py`, `rust/src/convert.rs`) | Not applicable; a canonical document has no such key | No longer adds `revision` to the toolkit document or to the `added` list |
| `to_canonical` | The key is checked and dropped by the toolkit reader before conversion | No longer lists `revision` under `dropped`; the model it converts has none |
| `CalculationSession` constructor | The owner model has no revision. The session starts at 0. A mapping that still carries the key is checked like any document and the key does not seed | Never |
| `CalculationSession.open` | A toolkit-shape document that carries the key opens at 0, and `conversion.dropped` is `["revision"]` so the discard is visible | Never |

The engine session seeds from the key so that a v1 document keeps its behaviour through
the JSON-lines agent runners: a toolkit document with `"revision": 3` loaded by
`python/workbook_forge/cli.py:42-55` or `rust/examples/agent_workbook.rs:66-72` still
answers wire revision 3 and accepts `expected_revision: 3`. The calculation session
does not, because this spec's Design starts it at 0 and nothing depended on it before.

Explicit seeds have two uses besides the legacy key: the agent adapters build a
detached session from a snapshot and seed it with the revision the snapshot was read
at, so a preview or export on that copy reports the same wire revision as the session
it came from, and the counter-limit tests start near the last value. A `fork()`
carries its parent's revision without a seed argument. A seed follows the rule for the
key: both Python engine sessions refuse anything but an integer from 0 to
18446744073709551615, a boolean included, with `invalid_request`. The Rust constructor
takes a `u64`.

A document reloaded from a snapshot starts at 0, because the snapshot has no key. That
includes a document that an older build wrote with a non-zero `revision` and that was
since snapshotted by this one. Sessions are transient by contract
(`docs/agent-protocol.md:44`).

**Two reader differences.** The Python and Rust JSON readers disagree on two spellings
of the key. Neither is revision logic and neither parser is changed; the shared fixture
lists them apart from the table both readers agree on.

| Document | Python | Rust | Reason |
| --- | --- | --- | --- |
| `"revision": -0` | accepted as 0 | refused | Python's JSON reader yields the integer 0; Rust's number parser treats `-0` as a float, which is not an unsigned integer |
| the `revision` key written twice | accepted, the last value wins | refused as a duplicate field | Python's JSON object reader keeps the last of repeated keys; the Rust struct reader refuses a repeated field |

### 3. Entry

There are two ways in.

**The constructor** takes the owner model directly, with optional defined names and, in
Python, the backend (`"python"` or `"rust"` through the native bridge). The session
starts at revision 0. `conversion`, `model_version` and `source_fingerprint` are null,
because nothing was converted and no document bytes were seen.

**`open(document, input_kinds, names)`** takes the bytes of a v1 document of either
shape and decides which reader to use with one test: a root object that has a
`model_version` key is canonical, anything else is toolkit. The two v1 readers are
disjoint on that key: the canonical reader requires it
(`python/workbook_forge/model.py:966`) and the toolkit reader refuses it
(`python/workbook_forge/python_engine.py:202`). A malformed canonical document that
lacks `model_version` therefore takes the toolkit reader and is refused there as
`invalid_model`, for the canonical keys that reader does not know. The test lives in
one function per language because it stops working
at step 6, when the owner shape gains `model_version`; the migrator replaces it there
with a test on other fields (spec 09 section 3, version fields).

Before either reader is chosen, `open` itself refuses two things as `invalid_model`, in
both languages: bytes that are not JSON, and a JSON root that is not an object. "JSON"
is read strictly, as the Rust reader reads it: UTF-8 with no byte order mark, no `NaN`
or `Infinity` constant, and no number outside the binary64 range. The root rule is
there because the Rust struct reader would otherwise take a root array as positional
fields, second element the legacy revision, and drop that revision without reporting
it. These refusals come before the `input_kinds` check below, so a document that is
not a JSON object is `invalid_model` whether or not `input_kinds` was given.

| | Canonical v1 document | Toolkit v1 document |
| --- | --- | --- |
| Path | `hydrate`, then `to_toolkit` (spec 09 step 0) | the toolkit reader |
| Defined names | `metadata.defined_names`, unless the caller passed names | the caller's names, if any; the document holds none |
| `input_kinds` | maps an input binding's name to `number`, `text` or `boolean`. A name that is not a bound input is `unknown_input` (`python/workbook_forge/convert.py:107`; `rust/src/convert.rs:102-105`) | refused with `invalid_request`; the bindings already carry their kinds |
| `conversion` | the `Conversion` that `to_toolkit` returned: every dropped and added field path | a `Conversion` whose `dropped` is `["revision"]` when the document carried the key; null otherwise, because nothing was converted |
| `model_version` | 1 | null |
| `source_fingerprint` | the content fingerprint of the exact bytes given to `open` (a string is fingerprinted as its UTF-8 bytes) | the same |
| Starting revision | 0 | 0 |

A canonical input binding has no kind and the toolkit shape requires one, so step 0
carries a binding only when the caller supplies its kind. Until step 2 makes `kind`
optional, a canonical document opened without `input_kinds` has no inputs, and
`conversion.dropped` lists each one as `bindings.inputs.<name>`
(`python/workbook_forge/convert.py:151-153`). Outputs need no kind and are carried.

Python's `open(..., backend="rust")` converts a canonical document with Python's
`to_toolkit` and hands the result to the Rust engine. Rust's own `open` is exercised by
the Rust tests on the same fixture.

### 4. Operations and error codes

| Operation | What it does | Changes the session |
| --- | --- | --- |
| `revision` | The current counter | No |
| `snapshot()` | The owner model at the current revision, without `revision` | No |
| `preview_inputs(values, expected_revision)` | Checks the revision, forks, calculates the fork, applies the values to the fork and calculates again. Returns `{applied: false, base_revision, proposed_revision, changes: [{sheet, address, before, after}], before, after}`, where `before` and `after` are v2 reports and `proposed_revision` is the revision the same call to `set_inputs` would return (`base_revision + 1` for a non-empty batch). `before` is a full pass on the fork, so its `base_revision` is null; `after` names `before` as its base | No: not the model, the revision, the published report or the edited-cell set, and no file is written. A live pass after a preview reuses the same base it would have without it |
| `set_inputs(values, expected_revision)` | Checks the shape of the arguments first (below). Then, under one lock: compares the revision, validates every value against its binding, validates the whole candidate model, then swaps it in and adds 1. Returns the new revision. An empty map commits nothing and returns the current revision | Yes, all values or none. One unknown name or one refused value refuses the batch and leaves the revision where it was |
| `calculate()` | Reads the model, the published report and the edited-cell set under the lock, calculates outside it, then publishes under the lock. Returns a `calculation-report.v2` | Only by publishing, under the rule below |

Inputs are addressed by binding name. `expected_revision` is required on both
`preview_inputs` and `set_inputs`; the engine session beneath keeps it optional for its
existing callers.

**Argument checks come first.** `preview_inputs` and `set_inputs` check their arguments
before the revision is compared and before either engine sees them, so a malformed call
gets the same code on both Python backends whatever the session's revision:
`expected_revision` must be an unsigned 64-bit integer and `values` a map from input
names, which are text, to values (`invalid_request` otherwise; a key such as the number
1 is not read as the name `"1"`), and every value must be a cell value: blank, a
boolean, a finite number, text, or an `{error, message?}` object (`invalid_input`
otherwise, which covers a list, any other object, `NaN`, an infinity, an integer too
large for binary64 and text that is not valid Unicode). Only then is the revision
compared, and only then does a binding judge a well-formed value. The Rust session
takes a typed map, which leaves one value that is not a cell value, a number that is
not finite; it is `invalid_input` there too, before the revision is compared.
`calculate` checks `workers` the same way: a non-negative integer (a boolean is not
one), else `invalid_workers`; a number above the engines' cap of 8 is clamped, and 0
means 1. A pinned context that is not a `CalcContext` is `invalid_request`.

**Publish rule.** A pass replaces the published report and clears the edited-cell set
only when both of these hold:

1. The session is still at the revision the pass read. Otherwise the report is returned
   with `stale: true`.
2. The pass completed. A pass that ends early is returned to the caller and not
   published; the published report and the edited-cell set are left exactly as they
   were.

A pass ends early when it returns before every cell of its closure was evaluated,
reused from the base, or given its own diagnostic. That is a refusal of the whole graph
before any cell runs (`python/workbook_forge/python_engine.py:517-522`;
`rust/src/toolkit.rs:1594-1597`), a result-budget `resource_limit` part of the way
through (`python_engine.py:546-552`; `toolkit.rs:1636-1642`), or a worker failure. A
pass that completes with diagnostics on individual cells is a completed pass and is
published. This is the Design line "A failure publishes nothing". The rule sits in the
publish step both report shapes share, so the legacy report follows it too. Whether a
pass ended early is carried on the internal pass result; neither report shape has a key
for it. The effect shows on the next pass: its `base_revision` names the last completed
pass, or is null when there was none, and it reuses nothing from the pass that failed.

**Error codes.** Codes are the contract and wording may differ between languages
(`docs/agent-protocol.md:29`).

| Code | When |
| --- | --- |
| `revision_conflict` | `expected_revision` is a valid revision and not the session's |
| `invalid_input` | a value in `values` is not a cell value (checked before the revision); a name in `values` is not a bound input; or the binding refuses the value (kind, `required`, `min`, `max`, `choices`). The engine's own code for an unknown name is `unknown_input` (`python_engine.py:310-311`; `rust/src/toolkit.rs:1133-1135`); the session maps it as the agent adapters do (`rust/src/agent.rs:344-348`) |
| `invalid_reference` | a binding names a sheet or address the model does not have |
| `resource_limit` | the revision counter is at its last value, or an engine limit was reached |
| `invalid_request` | `expected_revision` is not an unsigned 64-bit integer; `values` is not a map from text names to values; `input_kinds` was given with a toolkit-shape document; a pinned calculation context is malformed (not a `CalcContext`, or, on an engine session's `calculate_report`, JSON that is not `{now_ms, utc_offset_minutes, seed}` or carries a UTC offset outside -840 to 840 minutes); an engine session's `revision` seed is not an unsigned 64-bit integer |
| `invalid_workers` | `workers` is not a non-negative integer |
| `invalid_model` | from `open`: the bytes are not JSON, or the root is not an object. From the constructor: the model is not a mapping of finite JSON data. Also a reader code, below |
| `unknown_input` | only from `open`, when `input_kinds` names an input the canonical document does not bind |
| reader codes | a refusal from either v1 reader passes through with that reader's code, for example `invalid_model` or `schema_version`; see below |

The engine session reports a malformed `expected_revision` as `revision_conflict`
(`python_engine.py:660-661, 705-706`); the calculation session checks the argument first and
reports `invalid_request`, the agent protocol's code for an argument of the wrong
shape. Through the native bridge every engine session method the calculation session
calls reports a code, not only `open`: the bridge prefixes its message with the code,
and where the failure is inside the bridge it supplies one itself (`invalid_value` for
an input batch its cell-value decoder refuses, `invalid_request` for a pinned context
or a seed it refuses). With the argument checks above, every code in this table except
the reader codes is the same on both Python backends for the same call.

**Reader codes are per engine.** A JSON object that is not canonical goes to the
toolkit reader, and the two readers do not share every code. The Python reader works
on the parsed JSON and reports the code of the first rule it finds broken:
`schema_version` for a missing, non-integer or unsupported version, `invalid_value`
for a cell value that is not one, `invalid_model` for an unknown key or a wrong
container. The Rust reader first deserialises the document into typed structs. A
document it cannot deserialise has no code of its own, and `open` reports it as
`invalid_model`, in Rust and through `backend="rust"`; a document it can deserialise
is then validated with coded rules like Python's. So the two agree wherever the
document deserialises (a missing or unsupported `schema_version` is `schema_version`
in both) and can differ where it does not:

| Document | Python | Rust | Reason |
| --- | --- | --- | --- |
| `{}` | `schema_version` | `invalid_model` | Python checks the version first; the Rust struct reader cannot build a document without `sheets` |
| `"schema_version": 1.0` | `schema_version` | `invalid_model` | the Rust struct reader cannot read 1.0 as an integer |
| a cell `"value": [1]` | `invalid_value` | `invalid_model` | the Rust struct reader cannot read a list as a cell value |

These three stand for the class; neither reader is changed, and step 1 does not
converge their codes. Both always refuse such a document, and a caller that must
behave the same on both engines should treat any reader code as "the document was
refused". The shared fixture pins each engine's code for these and for the refusals
`open` makes itself (`open_refusals`), and both test suites check that the listed
differences are the only ones in that list.

The Rust `CalculationSession` takes typed arguments, so most of the argument checks
have nothing to refuse there. `calculate_with` takes a `CalcContext` value as given:
its UTC offset is checked where a context is parsed from JSON or set on an engine
session, not on the typed struct.

### 5. Report

`schemas/calculation-report.v2.schema.json` is a closed object with 18 required keys.
Each language has one builder, fed by one pass, and the legacy report is still produced
from the same pass for its existing callers.

| Field | Type | Value in step 1 |
| --- | --- | --- |
| `report_version` | 2 | constant |
| `stage` | `"calculate"` | constant |
| `engine` | `"python"` or `"rust"` | the engine that ran the pass; `"rust"` through the native bridge. The only field allowed to differ between engines |
| `behavior_profile` | `"workbook-forge"` | constant |
| `schema_version` | 1 | the owner document's |
| `model_version` | 1 or null | 1 when the session was opened from a canonical v1 document, otherwise null |
| `source_fingerprint` | `sha256:` and 64 hex digits, or null | the fingerprint of the bytes given to `open`; null for a session built by the constructor |
| `revision` | integer, 0 to 2^64 - 1 | the session revision the pass read |
| `base_revision` | integer or null | the revision of the published report the pass reused as its base; null on a full pass |
| `calc` | `{now_ms, utc_offset_minutes, seed}`; `seed` is a decimal string | the pass's `CalcContext`, always present |
| `inputs` | array of `{name, sheet, address, origin, value}`, ordered by name | `value` is the cell's value in the model the pass read. `origin` is `"authored"` once this session, or the session it was forked from, has written the cell, otherwise null |
| `outputs` | array of `{name, sheet, address, origin, result}`, ordered by name | a formula cell whose value this pass copied to its outputs: `"calculated"` and the value. A formula cell with no value in this pass, or whose value the output result budget did not admit (below): `"not_calculated"` and null. A cell that is not a formula: `origin` as for inputs, `result` the cell's value |
| `values` | object from `Sheet!A1` to a value | the pass's values, as the legacy report has them |
| `evaluated_cells` | sorted, unique `Sheet!A1` | the cells this pass executed (section 6, item 4); sorted on every pass, including one that ended early |
| `stale` | boolean | true when the session moved on before the pass could publish |
| `diagnostics` | array of `{code, message, sheet?, address?}` | the pass's diagnostics ordered by sheet, address, code and message, on every pass including one that ended early, in the toolkit code vocabulary; no `classification` and no `function` |
| `calculated_formulas` | integer, 0 or more | formula cells in the pass's closure that have a key in `values` |
| `unresolved_formulas` | integer, 0 or more | formula cells in the pass's closure that do not |

A value is null, a number, a string, a boolean, `{error, message?}` or `{rows: [[...]]}`.

**The closure** is the set of cells the pass was asked to cover: every cell when the
model binds no outputs, otherwise the cells the bound outputs depend on. Both formula
counts range over the formula cells in the closure and nowhere else, because `values`
holds only the closure; a formula that no bound output depends on is in neither count,
is not in `evaluated_cells`, and gets no diagnostic even when it does not parse.
The two counts always add up to the number of formula cells in the closure, and the
same definition applies to a pass that ended early. One case has no closure: a pass
refused before its graph was built. It can vouch for no formula, so both engines count
every formula cell in the document as unresolved and none as calculated.

**The output result budget.** A pass may materialise at most 100000 result cells in
`values`, and the engines apply a second budget of the same size to the values copied
to bound outputs, where an array counts its cells and anything else counts 1. Outputs
are copied in name order. The first one that would take the total past 100000 is not
copied and puts a `resource_limit` diagnostic on its cell, and the copying stops
there, so no later output is copied either. The v2 report follows the legacy report in
this: a formula output that was not copied is `"not_calculated"` with a null result
even though its cell may have a value in `values`, and an output on a plain cell falls
back to the cell's own value. A report therefore never holds more than the two
budgets, however many outputs are bound to one array. Running out of the output
budget does not end the pass early: every cell of the closure was covered, so the pass
is published.

**What is null in step 1, and why.**

- `model_version` for a session built by the constructor or opened from a toolkit
  document: the toolkit shape has no such field until step 6
  (`docs/specs/model-versioning.md:10`).
- `source_fingerprint` for a session built by the constructor: there were no bytes.
- `base_revision` on a full pass: the first pass, the `before` pass of a preview, and
  any pass after one that ended early with nothing published before it.
- `inputs[].origin` for a cell this session has not written. It reflects only writes
  made through this session or the session it was forked from. It does not read a
  canonical document's cell provenance, which the toolkit shape cannot hold until
  step 6.
- `outputs[].result` with `origin: "not_calculated"`: the formula has no value in this
  pass, or the output result budget did not admit it. A `cached_value` on the cell is
  never read, so an imported cache is not
  promoted: `=1+1` carrying `cached_value: 999` reports 2, and `=1+` carrying
  `cached_value: 5` reports null with one `parse_error`.

Two fields differ from the legacy report on purpose: `calc` is present on every pass,
not only one that evaluated a volatile cell, and `outputs` is a list of bound outputs
with their origin, not a map of values.

### 6. Section 10 requirements

Each item gives the contract and what step 1 does about it.

1. **Pass identity.**
   Contract: a pass is identified by its revision and its `CalcContext` together;
   revision alone does not tell apart two passes with different volatile contexts.
   Step 1: the v2 report carries `revision` and `calc` on every pass. A monotonic pass
   counter is the alternative spec 09 allows; it is not added.

2. **Publish rule.**
   Contract: publication is a compare-and-swap on the revision and the base pass, and a
   pass that ends early publishes nothing.
   Step 1: the revision half exists (`python/workbook_forge/python_engine.py:721-726`;
   `rust/src/toolkit.rs:1982-1991`) and now compares against session state instead of a
   document field. The early-end half is built in the same publish step (section 4).
   The base half is not: two passes that read the same revision both publish and the
   last one wins. `base_revision` is reported and not compared.

3. **Volatile cells.**
   Contract: a volatile cell is dirty on every pass, and the report carries the context
   the pass used.
   Step 1: already built (`python_engine.py:525-529`; `rust/src/toolkit.rs:1599-1608`).
   The fixture's canonical document pins the `NOW` cell in `evaluated_cells` on every
   pass, under a pinned context.

4. **One `evaluated_cells` definition.**
   Contract: the list holds the formula cells in the closure, free of a structural
   error, whose expression this pass executed. A cell executes when the pass has no
   base, when the cell is invalidated (edited, volatile, or downstream of either), or
   when the base holds no value for it. The list is sorted. A literal cell and a cell
   refused for a structural error are never listed.
   Step 1: this is what both engines do today (`python_engine.py:544-553, 586`;
   `rust/src/toolkit.rs:1633-1649`), including the re-run on every pass of a clean cell
   whose last pass gave a diagnostic and no value. The spike's variant, which treats
   such a cell as covered and omits it, is a later change made to both engines
   together. The fixture pins the list at every step; it does not exercise the
   diagnostic re-run clause. Both engines sort the list on every exit of a pass, an
   early end included.

5. **Validation rule scopes.**
   Contract: each structural validation rule declares what an edit can change, so
   validating only what an edit touched is a property of the rule set.
   Step 1: stated only. Every edit validates the whole candidate model
   (`python_engine.py:693-695`; `rust/src/toolkit.rs:1934-1936`).

6. **In-place edits.**
   Contract: an edit is applied in place only for a batch of cells, with a rollback
   list and a statement of how a panic part of the way through leaves the session.
   Step 1: stated only. Edits stay copy, validate, swap. A panic under the lock leaves
   the previous model, published report, edited-cell set and revision intact, because
   every fallible step works on a private copy (`rust/src/toolkit.rs:1788-1794`), and
   the revision is moved in the same infallible step as the model.

7. **Delta since a named base.**
   Contract: `changes_since(base_pass) -> Changes{base, new} | Superseded`, with the
   caller verifying `base` and `new`, and result statuses `published`,
   `superseded: newer revision` and `superseded: base moved`.
   Step 1: stated only. The report keeps `stale: bool`, because spec 09 section 4 lists
   it in the v2 report and the agent wire requires it
   (`python/workbook_forge/agent.py:86-87`).

8. **One funnel for logged mutations.**
   Contract: while a command session is attached, every state change either enters its
   log as an intent or is refused.
   Step 1: stated only. Step 4 builds it, when the browser command session moves onto
   the owner model.

9. **Interleaving test.**
   Contract: with writers using compare-and-swap and calculators on one session, and a
   deterministic yield at the lock boundaries, every stored report equals the full
   calculation of its named pass, and every returned delta equals the difference of the
   full calculations of its two named states.
   Step 1: the invariants are stated; the test is not written. The first invariant
   needs item 2's base half and the second needs item 7.

### 7. Deferred

| Step | What it adds to the session |
| --- | --- |
| 2 | optional `kind` and the `required: false` rule, so a canonical document opened without `input_kinds` keeps its inputs |
| 3 | both agent adapters construct the owner shape natively |
| 4 | `WorkbookSession` and the command session on the owner model; items 7 and 8 above |
| 5 | the source compiler starts from the owner shape |
| 6 | both version fields to 2 with their documentation entries; the migrator, which replaces the `model_version` test in `open`; retiring the legacy report, the v1 reader's acceptance of the `revision` key and the v1 canonical report |

Not scheduled in any step: a pass counter and the base half of the compare-and-swap; a
changed `evaluated_cells` definition; scoped validation; in-place edits; the
interleaving test; cell provenance in the owner document; diagnostic classification;
one reconciled shape for error and array values; an `open` on the native bridge;
durable sessions.

### 8. Corrections to spec 09's step 1 change set

Spec 09 is not edited by this change. Its step 1 row was written against an earlier
tree; these are the differences found at `main c27ac63`.

| Spec 09 says | At the base |
| --- | --- |
| snapshot writers at `toolkit.py:28` and `wasm.rs:18, 94` | `python/workbook_forge/toolkit.py:75` and `rust/src/wasm.rs:93-96` |
| the toolkit report at `toolkit.rs:1203-1213` | `rust/src/toolkit.rs:1250-1263` |
| the `evaluated_cells` behaviour at `toolkit.rs:1585-1603` | `rust/src/toolkit.rs:1633-1649` |
| `test_toolkit_interfaces.py` and `test_agent_cli.py` build or read a document `revision` | neither holds one. The first gains one assertion that `to_dict()` has no `revision`; the second holds only wire revisions and is unchanged |
| seven test files change | further files change: `fixtures/shape-conversion-cases.v1.json` with `python/tests/test_shape_conversion.py` and `rust/tests/shape_conversion.rs`; `rust/tests/defined_names.rs`; the test in `rust/src/wasm.rs`; in-crate Rust tests; `examples/ts_client_fixture.py`; `tools/compiler_workloads.py`; and `python/tests/test_dual_engines.py` lines 215-216 and 512-513, which compared a session report with a stateless one including its revision |
| "`revision` leaves the document" | it leaves every writer. Readers still accept it, and an engine session built from document text is seeded from it (section 2), so that a v1 document behaves through the agent runners as it did |

`docs/specs/red-flags.md` lines 110 and 256-258 still describe calculation sessions
that take the canonical `Workbook` directly. Decision G-A put the session on the owner
model instead. That file is a spec in force and waits on the owner's edit; this change
does not touch it.
