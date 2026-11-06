---
type: technical-specification
status: proposed — needs James's decision on the base and the version fields before any schema code
base: main 41f28d3
summary: Converge the canonical and toolkit workbook shapes into one versioned contract for workbook meaning, with the session and the preservation envelope as named companions, by moving one responsibility at a time. Costs both candidate bases, recommends one, and names the adapters, retirement conditions and evidence for each step. The version decision is the repository's approval gate.
---

# 09 — Contract convergence and migration map

## Problem

Spec 08 established that the tree holds two shapes for workbook meaning and that no
code converts between them in either language. Capability (sessions, preservation,
agent operations, the browser, the source compiler, the native bridge, the only Rust
`.xlsx` importer) accrues on the toolkit shape. Versioning discipline, provenance,
structured diagnostics, the intake carry list, the receipt harness and the strict
evidence gate accrue on the canonical shape. Specs 05 and 06 propose a session and
typed bindings on the canonical base and assume that base is the destination. Spec
08's independent refutation showed the field evidence is balanced and the edge
evidence leans the other way. This proposal makes the choice explicit, costs it, and
lays out the migration so that nothing either side has today is lost on the way.

## Outcome

One versioned contract for workbook meaning that every consumer takes: the agent
operations in both languages, the browser session, the source compiler, the native
bridge, intake, export, the receipt harness and the evidence gate. Two named
companions travel beside it: the **session** (revision, preview, atomic edit,
calculate, report) and the **preservation envelope** (source identity, imported
package baseline, feature guards, text layout, the carry inventory). Old documents of
both shapes still read through a migrator. Every step is independently testable and
leaves both engines producing the same results on the shared fixtures.

## Scope

- A decision record for the base and the version fields (section 3).
- The target contract as a field list with its owner for each field (section 4).
- The migration in six steps with the adapters each step needs, its retirement
  condition and its evidence (section 5).
- Alignment of specs 05 and 06 to the chosen base (section 6).

## Non-goals

- Writing schema code, a migrator or a new version before the decision.
- The preservation envelope's own serialization and invalidation rules (spec 10).
- The result-range (spill) contract, the source route and export planning; they
  consume this contract and are specified separately.
- Any change to the agent protocol wire format or the browser command API.

## 1. What each shape has that the other refuses

Both shapes reject unknown fields: the canonical schema with `additionalProperties:
false`, the Python toolkit normaliser with closed key sets (`python_engine.py:202,
220, 236`), the Rust toolkit with `deny_unknown_fields` (`toolkit.rs:402`).

| Only in the canonical shape | Only in the toolkit shape |
| --- | --- |
| `model_version` beside `schema_version` | `Sheet.id`, a stable identity distinct from `name` |
| `provenance{origin, source}` per workbook and per cell; `origin ∈ {authored, imported, calculated}` | `blocked_reason` per cell, a write and bind guard |
| `diagnostics[{code, classification, message, sheet, address, function}]` | the rule that a formula cell carries no authored value |
| `metadata` holding defined names and `intake.not_carried` | typed input bindings: `kind ∈ {number, text, boolean}`, `required`, `min`, `max`, 1 to 32 `choices` |
| explicit `data_type` per cell | `Style{number_format, bold, font_color, fill_color, horizontal}` and `column_widths` |
| stored `Formula.dependencies` and `Formula.result` | `cached_value` separate from `value` |
| `Sheet.dimensions`; `source_path` (redacted for agents); source fingerprint on report and views | `revision` on the document |

Arrays are refused by both shapes' serialized form today; the toolkit calculates
them in memory as `CalculatedValue::Array`, the canonical calculators report
`array results are unsupported`.

## 2. Who consumes each shape

| Consumer | Shape today | Languages |
| --- | --- | --- |
| nine agent operations and the JSON-lines runners | toolkit | Python, Rust |
| browser session, command API, event log | toolkit | Rust via WASM |
| source compiler input | toolkit | Python |
| native bridge | toolkit | Rust for Python |
| CLI `inspect`, `run`, `scenario`, `agent` | toolkit | Python |
| `.xlsx` import with preservation | toolkit | Python, Rust |
| CLI `intake`, `calculate`, views | canonical | Python |
| canonical export, receipt harness, strict evidence gate | canonical | Python |
| dual-engine calculation fixtures | canonical | Python, Rust |
| TypeScript client | neither directly; the agent protocol | TypeScript |

## 3. The decision: which base, which version fields

Two bases are possible. Either one is a schema change of comparable size and therefore
meets the approval gate in `AGENTS.md`.

### Base A: extend the canonical shape

Add the right-hand column of section 1 to the canonical schema: `Sheet.id`,
`blocked_reason`, the formula-or-value rule, typed bindings (spec 06's shape),
presentation as a separate `style` object and `column_widths`, and an array-capable
`Formula.result`. Keep `revision` out of the document; it is session state.

- Version: `schema_version` 2 (new wire fields readers must understand) and
  `model_version` 2 (new meaning: typed binding validation, the formula-or-value
  rule, array results).
- Migration cost: every consumer in section 2 that takes the toolkit shape moves:
  both agent adapters, the browser session, the source compiler input, the native
  bridge, four CLI commands and both `.xlsx` importers. The only Rust `.xlsx`
  importer is rewritten against a shape it never produced.
- What is kept for free: both version fields, provenance, diagnostics, the carry
  list, the receipt harness, the evidence gate, the dual-engine fixtures.
- Risk: preservation and session behaviour re-implemented under a new shape; the
  toolkit's 4a tests become the only guard against silent loss.

### Base B: extend the toolkit shape

Add the left-hand column of section 1 to the toolkit shape: `model_version`,
`provenance` per workbook and per cell, structured `diagnostics` with
`classification`, `metadata` with defined names and the carry inventory, explicit
`data_type`, stored `Formula.dependencies` and `Formula.result` beside `cached_value`,
`Sheet.dimensions`, and the fingerprint rule for source identity. Move `revision` out
of the document into the session.

- Version: `schema_version` 2 for the toolkit shape (new wire fields) and the first
  `model_version`, set to 2 so that the two shapes' histories do not collide and a
  reader can tell a migrated document from an unmigrated one.
- Migration cost: intake, canonical export, the receipt harness, the views and the
  evidence gate move to the extended shape; they are Python modules plus the Rust
  canonical hydrator and calculator, which become a reader for old v1 canonical
  documents through the migrator. The dual-engine fixtures are migrated once.
- What is kept for free: every consumer in section 2 that takes the toolkit shape,
  the sessions in both languages, both `.xlsx` importers with their preservation
  context, the browser, the native bridge.
- Risk: discipline retrofitted rather than inherited; the carry list and provenance
  must be produced by `import_xlsx`, which never did so.

### Recommendation

**Base B.** The cost that matters is behaviour-risking migration, and Base A moves
eight consumers including the only Rust importer while Base B moves a Python-side
pipeline and one Rust hydrator. The canonical side's strengths are rules and fields,
which are mechanical to add; the toolkit side's strengths are behaviours attached to
consumers, which are expensive to move. The documentation cost of Base B (vision,
red flags, model-versioning, specs 05 and 06 all say "canonical") is real and is paid
once in the docs; the behaviour cost of Base A is paid in every consumer. If James
prefers Base A for the sake of the documented story, section 5 applies unchanged with
the roles reversed, and the first step is identical either way.

**Version fields:** both to 2 under either base. `model-versioning.md` already says
a new required wire field is a `schema_version` change and new validation meaning is
a `model_version` change; both happen here.

**This is decision G-A.** Nothing in section 5 beyond step 0 may start before it.

## 4. The target contract

One document, `workbook-model.v2`, whichever base it grows from. Owner per field in
the right-hand column names where the authority lives after convergence.

| Field | Owner after convergence |
| --- | --- |
| `schema_version`, `model_version` | the contract; enforced on read and write in both languages |
| `metadata.source` (fingerprint, package part names, never a path) | intake and import |
| `metadata.defined_names`, `metadata.not_carried` | intake and import; the preservation envelope's inventory |
| `provenance.origin` per workbook and per cell | import sets `imported`; authoring sets `authored`; calculation sets `calculated` on `Formula.result` only |
| `sheets[].id`, `name`, `dimensions`, `column_widths` | the contract |
| `cells[].value`, `data_type`, `cached_value`, `formula.expression`, `formula.dependencies`, `formula.result` | the contract; `dependencies` and `result` written only by calculation |
| `cells[].style`, `blocked_reason` | the contract; `blocked_reason` set only by import |
| `bindings.inputs[name]{cell, kind, required, min, max, choices}`, `bindings.outputs[name]{cell}` | the contract; spec 06's constraint list is decided there |
| `diagnostics[]` | whoever produced the document state; cleared and rewritten by calculation |
| not in the document: `revision`, preview state, the event log | the session |
| not in the document: package baseline, merged ranges, date system, compatibility version, feature guards, text layouts, insertion bound | the preservation envelope |

## 5. Migration, one responsibility at a time

Each step is a separate pull request with its own gate run. A step's retirement
condition says when the thing it replaces may be deleted; until then both exist and
the tests of both pass.

| Step | What changes | Adapters it adds | Retirement condition | Evidence |
| --- | --- | --- | --- | --- |
| 0. Explicit conversion edges (no version change; may start now) | `to_toolkit(Workbook) -> (ToolkitModel, not_carried)` and `to_canonical(ToolkitModel, envelope) -> (Workbook, diagnostics)` in Python; the same pair in Rust; each declares what it drops | the two functions themselves, plus an in-memory `PreservationEnvelope` type assembled from what `import_xlsx` already holds | never retired; they become the migrator in step 6 | the operating scenario and every `fixtures/corpus` workbook round-trip through both functions in both languages with the declared losses and nothing else; `test_dual_engines` passes on the converted toolkit document |
| 1. Session over the owner model | spec 05 reframed: the toolkit `Session` machinery (apply, set_inputs, preview, calculate, revision checks) operates on the owner shape; `revision` leaves the document | a `Session` constructor that accepts either shape through step 0 | the old entry point once every caller passes the owner shape | `test_python_engine`, `test_agent_python`, `rust/tests/command_envelope.rs` pass unchanged; a new shared input-change fixture produces the same report in both engines |
| 2. Typed bindings in the owner contract | spec 06 under the chosen base; constraints as James decides there | migrator entry for untyped v1 bindings | the untyped binding reader when no v1 document remains in fixtures | `test_canonical_model` migration tests; `test_agent_contract` validation cases |
| 3. Agent operations on the owner | both adapters construct the owner shape; the wire format does not change | step 0 conversion at the boundary first, then native | the boundary conversion once both adapters are native | `test_agent_contract`, `test_agent_python`, `test_typescript_sdk` pass unchanged; the TypeScript client's contract hash is unchanged |
| 4. Browser session on the owner | `WorkbookSession` and the command session take the owner; the event log's baseline and replay unchanged | step 0 conversion inside `open` first | the conversion once `wasm.rs` is native | `rust/tests/browser_contract.rs`, `event_log.rs`; `web/forge-engine` Node tests; Playwright run recorded in the PR |
| 5. Source compiler input on the owner | `compile_workbook` starts from the owner shape; defined names become available to it | none beyond step 0 | the toolkit-only path once the equivalence matrix (spec 11) is green on the owner | `test_source_graph*`, `test_source_equivalence` |
| 6. Retire the second writer; keep a reader | the shape not chosen is no longer written; old documents read through step 0 as a migrator; `model-versioning.md` and `model-changelog.md` updated | none | when every fixture and every receipt in the tree is in the owner shape | the whole gate; a receipt check (`tools/excel_oracle.py --check-receipt`) on every committed receipt |

Step 0 needs no decision and is the first thing to build: it is the edge spec 08
found missing, and it is useful under either base and under no convergence at all.

## 6. Alignment of specs 05 and 06

- **Spec 05** stays as the session contract. Its "Scope" changes from "taking
  `model.Workbook` directly" to "taking the owner model"; its acceptance tests keep
  their names. Its report question (revision as an optional v1 field or a
  `calculation-report.v2`) is answered here: v2, because the report schema refuses
  unknown fields and because the context field spec 08 found missing (`CalcContext`)
  lands in the same change.
- **Spec 06** stays as the typed-bindings contract. Under Base B its shape is the
  toolkit's existing `{sheet, address, kind, required, min, max, choices}` plus a
  `shape` field; under Base A it is as written. Its open questions (default for
  `required`, the constraint list) are unchanged and remain James's.

## 7. Evidence that demonstrates the contract

- The same document bytes hydrate and calculate to the same values, errors and
  diagnostics in both engines before and after each step, on
  `fixtures/operating-scenario.workbook.json`, `fixtures/formula-cases.jsonl` where a
  case has a workbook form, and every corpus workbook.
- Every toolkit preservation test (`test_absent_cell_export`, `test_toolkit_xlsx`,
  `test_xlsx_features`, `rust/tests/absent_cells.rs`, `corpus.rs`,
  `xlsx_features.rs`) passes unchanged through the adapters at every step.
- The agent protocol's catalog hash and the TypeScript contract hash do not change.
- A v1 document of either shape reads through the migrator and produces the same
  calculation as before.
- Excel observations remain pinned to the documents they observed; a new
  observation is taken on the owner shape's export before the second writer is
  retired, with the export regression test from the WA3 lane standing beside it.

## 8. Dependencies

Spec 08 (the map). Spec 10, the preservation envelope, for the companion's own
rules; step 0 only needs the in-memory type. Spec 11, the source route, consumes
step 5. The result-range contract consumes steps 1 and 2.

## 9. Open questions for James

- **Decision G-A:** Base A or Base B, and both version fields to 2.
- Whether step 0 may start before the decision (recommended: yes; it decides
  nothing).
- Spec 06's constraint list and `required` default, unchanged from spec 06.
