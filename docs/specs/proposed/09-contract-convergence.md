---
type: technical-specification
status: proposed — needs James's decision on the base, the binding shape and the version fields before any schema code
base: main 41f28d3
summary: Converge the canonical and toolkit workbook shapes into one versioned contract for workbook meaning, with the session and the preservation envelope as named companions, by moving one responsibility at a time. Costs both candidate bases from the import map, leans toward extending the toolkit shape, states what that lean rests on and what would reverse it, and names the adapters, retirement conditions and evidence for each step. The base, the binding shape and the version fields are one decision, the repository's approval gate.
---

# 09 — Contract convergence and migration map

## Problem

Spec 08 established that the tree holds two shapes for workbook meaning and that no
code converts between them in either language. Capability (sessions, preservation,
agent operations, the browser, the source compiler, the native bridge, the only Rust
`.xlsx` importer, the cross-engine fixture suite) accrues on the toolkit shape.
Versioning discipline on read and write, provenance, structured diagnostics, the intake
carry list, the receipt fingerprints and the strict evidence gate accrue on the
canonical shape. Specs 05 and 06 propose a session and typed bindings on the canonical
base and assume that base is the destination. Spec 08's refutation showed the field
evidence is balanced and the edge evidence leans the other way. This proposal makes the
choice explicit, costs it from the import map, and lays out the migration so that
nothing either side has today is lost on the way.

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

- A decision record for the base, the binding shape and the version fields (section 3).
- The target contract as a field list with its owner for each field, including one
  report shape (section 4).
- The migration in six steps with the adapters each step needs, its change set, its
  retirement condition and its evidence (section 5).
- Alignment of specs 05 and 06 to the chosen base (section 6).
- Session requirements the persistent-graph spike exposed (section 10).

## Non-goals

- Writing schema code, a migrator or a new version before the decision.
- The preservation envelope's own serialization and invalidation rules (a separate
  proposal, in preparation).
- The result-range (spill) contract, the source route and export planning; they
  consume this contract and are specified separately.
- Any change to the agent protocol wire format or the browser command API.

## 1. What each shape has that the other refuses

The canonical schema sets `additionalProperties: false` at the root and in every
`$defs` object except `metadata`, which is an open object (`schemas/workbook-model.v1.schema.json`;
`python/workbook_forge/model.py:969`; `rust/src/model.rs:811`). The Python toolkit
normaliser uses closed key sets (`python/workbook_forge/python_engine.py:202, 220, 235, 267, 301`);
the Rust toolkit sets `deny_unknown_fields` on every document type (`rust/src/toolkit.rs:255, 269, 312, 339, 363, 403`).

| Only in the canonical shape | Only in the toolkit shape |
| --- | --- |
| `model_version` beside `schema_version` | `Sheet.id`, a stable identity distinct from `name` (`python_engine.py:220-224`; `toolkit.rs:340-346`) |
| `provenance{origin, source}` per workbook and per cell; `origin ∈ {authored, imported, calculated}` | `blocked_reason` per cell, a write and bind guard (`python_engine.py:251, 276`) |
| `diagnostics[{code, classification, message, sheet, address, function}]` | the rule that a formula cell carries no authored value (`python_engine.py:239-240`); the canonical hydrator accepts both (`model.py:1040-1048`) |
| `metadata` as an open object holding defined names and `intake.not_carried` | typed input bindings: `kind ∈ {number, text, boolean}`, `required` defaulting to true (`python_engine.py:278`; `toolkit.rs:368`), `min`, `max`, 1 to 32 `choices` |
| explicit `data_type` per cell | `Style` beyond number format: `bold`, `font_color`, `fill_color`, `horizontal`; `column_widths` |
| stored `Formula.dependencies` and `Formula.result`, both required by the schema | `revision` on the document |
| `Sheet.dimensions` | — |

Shared or equivalent: `number_format` is on both (`Cell.number_format`; `Style.number_format`).
The imported cache is one concept carried two ways: toolkit `cached_value` beside `value`;
canonical `Cell.value` with `provenance.origin: imported` (`docs/specs/model-versioning.md`).
`source_path` is accepted and dropped by the canonical reader and never written
(`model.py:971-974`; `model-changelog.md`, 2026-10-14); it is not a field either base adds.
Arrays are refused by both serialized forms; the toolkit calculates them in memory as
`CalculatedValue::Array`, the canonical calculators report `array results are unsupported`.

## 2. Who consumes each shape

Counts are files that import the shape's module, from the import map taken at the base
revision (`grep -rln` over `python/workbook_forge`, `tools`, `examples`, `rust/src`,
`rust/examples`, `rust/tests`, `native/src`, `python/tests`).

| Shape | Package modules | Tools and examples | Tests | Named consumers |
| --- | --- | --- | --- | --- |
| Toolkit | 28 Python files import `toolkit`, `python_engine`, `xlsx`, `source_graph` or `delimited`; 29 Rust files | eight `tools/*.py` (corpus harness both lanes, TypeScript receipt, installed-wheel check, compiler and source tools), four of five `examples/*.py`, ten of fourteen `rust/examples/*.rs` | `test_dual_engines.py` (21, both engines through `python_engine` and the native bridge), `test_python_engine.py`, `test_agent_*`, `test_toolkit_*`, `test_absent_cell_export.py`, `test_xlsx_features.py`, `test_source_*`, `test_defined_names.py`, `test_volatile.py`, `test_limit_boundaries.py`; `rust/tests/*` | nine agent operations and both JSON-lines runners; browser session, command API, event log; source compiler input; native bridge; CLI `inspect`, `run`, `scenario`, `agent`; `.xlsx` import with preservation in both languages; CSV import; the Excel oracle (`tools/excel_oracle.py:526, 927`) |
| Canonical | six: `model`, `canonical_xlsx`, `canonical_roundtrip`, `intake`, `views`, `cli`; `model.rs` | four `tools/*.py` (`canonical_excel_receipt`, `sanitize_receipt` for `content_fingerprint`, two others), `rust/examples/canonical_calc.rs`, `benchmark_canonical.rs` | 14 Python test files (`test_canonical_model.py`, `test_canonical_model_diagnostics.py`, `test_canonical_export.py`, `test_intake.py`, `test_export_regression.py`, `test_canonical_receipt_safety.py`, …); canonical Rust is exercised by subprocess from `test_canonical_model.py` and `test_volatile.py` through `canonical_calc` | CLI `intake`, `calculate`, views; canonical export; the canonical receipt and the strict evidence gate |
| Neither directly | the TypeScript client speaks the agent protocol | | | |

The ratio by importing files is roughly three to one in favour of the toolkit shape in
both languages. The cross-engine fixture suite (`test_dual_engines.py`) is toolkit-shape;
the canonical Rust calculator is reached only through the `canonical_calc` example.

## 3. The decision: which base, which binding shape, which version fields

Two bases are possible. Either is a schema change meeting the approval gate in
`AGENTS.md`. The bills below are complete to the import map; the earlier draft of this
proposal padded one side and omitted the other's engine work, and an independent
refutation (section 11) corrected it.

### Base A: extend the canonical shape

Add the right-hand column of section 1 to the canonical schema: `Sheet.id`,
`blocked_reason`, the formula-or-value rule, typed bindings in spec 06's shape, `style`
beyond number format and `column_widths`, and an array-capable result. Keep `revision`
out of the document as session state.

- **Bill.** Move every toolkit consumer: both agent adapters, the browser session and
  command API, the source compiler input, the native bridge, four CLI commands, both
  `.xlsx` importers with their preservation context, both toolkit engines' normalisers
  and calculators, `delimited.{py,rs}`, `xlsx_features.rs`, `extraction.rs`, eight tools,
  four Python examples, ten Rust examples, and the toolkit-shape test suites including
  the cross-engine `test_dual_engines.py`. The only Rust `.xlsx` importer is rewritten
  against a shape it never produced.
- **Kept.** Both version fields enforced on read and write in both languages,
  provenance, diagnostics with classification, the carry list, the receipt
  fingerprints, the strict evidence gate, the changelog's version 1.
- **Risk.** Preservation and session behaviour re-implemented under a new shape; the
  toolkit test suites are the only guard against silent loss.

### Base B: extend the toolkit shape

Add the left-hand column of section 1 to the toolkit shape: `model_version`,
`provenance` per workbook and per cell, `diagnostics` with `classification` and
`function`, `metadata` with defined names and the carry inventory, explicit `data_type`,
stored `Formula.dependencies` and `Formula.result` beside `cached_value`,
`Sheet.dimensions`, and the fingerprint rule for source identity. Move `revision` out of
the document into the session.

- **Bill.** The two toolkit shape owners gain about ten fields with validation
  (`python_engine.py:201-300`; `toolkit.rs:403-418` and `validate`). Both toolkit
  calculators change, because their `Diagnostic` is `{code, message, sheet, address}`
  with no `classification` or `function` (`toolkit.rs:1183-1190`; `python_engine.py` has
  no `classification`). The Python toolkit writers gain version enforcement on write,
  which they lack today (`toolkit.py:119`; `python_engine.py:650`); red flag Spec 4
  credits only the Rust toolkit exporter. `import_xlsx` in both languages starts
  producing provenance and the carry inventory, which it never did. Intake, canonical
  export, the receipt harness, the views and the evidence gate move to the extended
  shape. `model.rs` (hydrator, calculator, report, defined-name table; about 1,500
  lines) becomes a reader for old v1 canonical documents through the migrator. The
  receipt fingerprints are re-pinned and the Excel observation re-taken on the new
  export (section 7).
- **Kept.** Every toolkit consumer, the sessions in both languages, both `.xlsx`
  importers with their preservation context, the browser, the native bridge, the
  cross-engine fixture suite.
- **Risk.** Discipline retrofitted rather than inherited: version enforcement,
  provenance and the carry list have to be built into writers that never had them, and
  the changelog's only entry describes the other shape (section 3, version fields).

### Where the lean comes from, and what would reverse it

By importing files the toolkit shape has roughly three times the consumers in both
languages, carries the only Rust `.xlsx` importer and the native bridge, and canonical
intake is Python-only. On that evidence this proposal **leans to Base B**. The lean is
not yet a recommendation James can act on, for three reasons the refutation surfaced:

1. **Binding shape.** Base B's bindings are the toolkit's `{sheet, address, kind,
   required, min, max, choices}`: `required` defaults to true, kinds exclude `date`,
   constraints exclude spec 06's `enum` and `max_length`, and spec 06's non-goals forbid
   porting the toolkit's binding schema verbatim. Choosing Base B therefore also chooses
   the binding shape, or amends spec 06. James decides which; see section 6.
2. **The canonical side's assets are not prose.** The receipt fingerprints, the strict
   evidence gate on `export_canonical`, the changelog's version 1 and both engines'
   read-and-write version enforcement are things Base B must re-create and Base A keeps.
   The bill above counts them.
3. **What would reverse the lean:** an engine-side change count for Base B that comes
   out larger than Base A's consumer moves (on this inspection it does not), or a
   decision that typed bindings keep spec 06's shape, which removes Base B's "kept for
   free" claim for bindings and narrows the gap.

### Version fields

`docs/specs/model-versioning.md` says `schema_version` changes on "a serialization
change readers must understand" and `model_version` on "meaning, normalization,
dependency, diagnostic, or provenance behavior changes". Both happen here under either
base, so this proposal asks for both fields to go to 2. That is a choice, not something
the versioning document decides: the changelog's 2026-09-28 entry added dimension maxima
"without a version bump (additive validation only)", so additive changes have landed
unbumped before. Two further facts belong to the decision:

- A reader cannot tell a v1 canonical document from a v1 toolkit document by version;
  both say `schema_version: 1`. Under either base the migrator in step 6 sniffs by field
  presence (`model_version` and `metadata` versus `revision` and `id`).
- Under Base B, version 2 would descend from a shape the changelog never recorded. The
  v2 changelog entry must carry two lineage lines, one for each v1 shape it absorbs.

**This is decision G-A: the base, the binding shape and the version fields together.**
Nothing in section 5 may start before it except the parts of step 0 that section 5
marks as mechanical.

## 4. The target contract

One document, `workbook-model.v2`, whichever base it grows from, and one report shape.
The owner column names where authority lives after convergence.

| Field | Owner after convergence |
| --- | --- |
| `schema_version`, `model_version` | the contract; enforced on read and write in both languages |
| `metadata.source` (fingerprint, package part names, never a path) | intake and import |
| `metadata.defined_names`, `metadata.not_carried` | intake and import; the preservation envelope's inventory |
| `provenance.origin` per workbook and per cell | import sets `imported`; authoring sets `authored`; calculation sets `calculated` on `Formula.result` only |
| `sheets[].id`, `name`, `dimensions`, `column_widths` | the contract |
| `cells[].value`, `data_type`, `cached_value`, `formula.expression`, `formula.dependencies`, `formula.result` | the contract; `dependencies` and `result` written only by calculation; `cached_value` and `provenance.origin: imported` are reconciled into one representation here |
| `cells[].style`, `blocked_reason` | the contract; `blocked_reason` set only by import |
| `bindings.inputs[name]`, `bindings.outputs[name]` | the contract; the shape is part of decision G-A |
| `diagnostics[]` | whoever produced the document state; cleared and rewritten by calculation |
| **report** `{report_version, stage, engine, behavior_profile, schema_version, model_version, source_fingerprint, revision, base_revision, calc{now_ms, utc_offset_minutes, seed}, inputs[], outputs[], values, evaluated_cells, stale, diagnostics[], calculated_formulas, unresolved_formulas}` | one `calculation-report.v2` merging the toolkit `CalculationReport` (`toolkit.rs:1203-1213`; `python_engine.py:516`) and the canonical v1 report; the session produces it |
| not in the document: `revision`, preview state, the event log | the session |
| not in the document: package baseline, merged ranges, date system, compatibility version, feature guards, text layouts, insertion bound | the preservation envelope |

## 5. Migration, one responsibility at a time

Each step is a separate pull request with its own gate run. A step's retirement
condition says when the thing it replaces may be deleted; until then both exist and the
tests of both pass.

| Step | What changes | Change set and adapters | Retirement condition | Evidence |
| --- | --- | --- | --- | --- |
| 0. Explicit conversion edges | `to_toolkit(Workbook) -> (ToolkitModel, Conversion)` and `to_canonical(ToolkitModel, envelope) -> (Workbook, Conversion)` in Python and Rust, where `Conversion` lists what was dropped **and what was added**. Mechanical additions: `Sheet.id` as `sheet-N`; a formula cell's canonical `value` moved to `cached_value`; `data_type` derived from the value; `formula.dependencies` derived by the engine's parser; `formula.result` from `cached_value` or blank with its origin. **Not mechanical:** binding `kind` and `required`, which the toolkit requires and the canonical shape lacks. Step 0 carries a binding only when the caller supplies its kind; otherwise it drops the binding and lists it. The default typing rule is part of G-A | the two functions, an in-memory `PreservationEnvelope` assembled from what `import_xlsx` already holds, and a new `python/tests/test_shape_conversion.py` plus `rust/tests/shape_conversion.rs` (the existing `test_dual_engines.py` builds its own documents and has no hook for an external one) | never retired; they become the migrator in step 6 | the operating scenario, every `fixtures/corpus` workbook and the toolkit documents the cross-engine suite builds round-trip through both functions in both languages with exactly the listed drops and additions |
| 1. Session over the owner model | spec 05 reframed: the toolkit `Session` machinery operates on the owner shape; `revision` leaves the document | a `Session` constructor accepting either shape through step 0. Change set for `revision`: three snapshot writers (`toolkit.py:28`; `python_engine.py:217, 650`; `wasm.rs:18, 94`) and seven test files that build or read a document `revision` (`test_dual_engines.py:45`, `test_defined_names.py:52`, `test_volatile.py:58`, `test_limit_boundaries.py:179, 198`, `test_compiler_seeded.py:64`, `test_toolkit_interfaces.py`, `test_agent_cli.py`); `test_limit_boundaries.py:198` asserts the document revision and changes | the old entry point once every caller passes the owner shape | `test_python_engine`, `test_agent_python`, `rust/tests/command_envelope.rs` pass unchanged; the seven files above are updated in the same change; a new shared input-change fixture produces the same report in both engines; the nine requirements of section 10 are stated in the session contract |
| 2. Typed bindings in the owner contract | spec 06 under the chosen binding shape | migrator entry for untyped v1 bindings | the untyped binding reader when no v1 document remains in fixtures | spec 06's named tests, which do not exist yet and are written in this step |
| 3. Agent operations on the owner | both adapters construct the owner shape; the wire format does not change | step 0 conversion at the boundary first, then native | the boundary conversion once both adapters are native | `test_agent_contract`, `test_agent_python`, `test_typescript_sdk` pass unchanged; `tools/generate_ts_contract.py`'s catalog hash is unchanged |
| 4. Browser session on the owner | `WorkbookSession` and the command session take the owner; baseline and replay unchanged | step 0 conversion inside `open` first | the conversion once `wasm.rs` is native | `rust/tests/browser_contract.rs`, `event_log.rs`; `web/forge-engine` Node tests; the Playwright run recorded in the pull request |
| 5. Source compiler input on the owner | `compile_workbook` starts from the owner shape; defined names become available to it | none beyond step 0 | the toolkit-only path once `python/tests/test_source_equivalence.py` (merged) is green on the owner shape | `test_source_graph*`, `test_source_equivalence` |
| 6. Retire the second writer; keep a reader | the shape not chosen is no longer written; old documents of both shapes read through step 0 as a migrator that sniffs by field presence; `model-versioning.md` and `model-changelog.md` updated with two lineage lines | none | when every fixture and every receipt in the tree is in the owner shape | the whole gate; `tools/excel_oracle.py --check-receipt` on every committed receipt that carries an `excel_observation` block (today none does, so this check is vacuous until an observation is committed) |

Step 0's mechanical parts may start before G-A. Its binding handling and everything
from step 1 on wait for the decision.

## 6. Alignment of specs 05 and 06

- **Spec 05** stays as the session contract. Its "Scope" changes from "taking
  `model.Workbook` directly" to "taking the owner model"; its acceptance tests keep their
  names. Its report question is answered in section 4: one `calculation-report.v2`
  merging both report shapes, carrying `revision`, `base_revision` and the calculation
  context the v1 report lacks.
- **Spec 06** stays as the typed-bindings contract, with one change forced by this
  proposal: its binding shape is part of G-A. Under Base A it is as spec 06 wrote it.
  Under Base B the toolkit's existing shape applies unless James amends it, which
  settles spec 06's open questions by default: `required` true, kinds without `date`,
  constraints `min`, `max`, `choices` only. Spec 06's non-goal against porting the
  toolkit schema verbatim conflicts with Base B as written; James resolves that
  conflict in G-A.

## 7. Evidence that demonstrates the contract

- The same document bytes hydrate and calculate to the same values, errors and
  diagnostics in both engines before and after each step, on
  `fixtures/operating-scenario.workbook.json`, the toolkit documents
  `test_dual_engines.py` builds, and every `fixtures/corpus` workbook. (The 1,691 cases
  in `fixtures/formula-cases.jsonl` have no workbook form and are not part of this
  evidence.)
- Every toolkit preservation test (`test_absent_cell_export`, `test_toolkit_xlsx`,
  `test_xlsx_features`, `rust/tests/absent_cells.rs`, `corpus.rs`, `xlsx_features.rs`)
  passes unchanged through the adapters at every step.
- The agent protocol's catalog hash and the TypeScript contract hash do not change.
- A v1 document of either shape reads through the migrator and produces the same
  calculation as before.
- Excel observations remain pinned to the documents they observed; a new observation
  is taken on the owner shape's export before the second writer is retired, with
  `python/tests/test_export_regression.py` (merged) standing beside it.

## 8. Dependencies

Spec 08 (the map). The preservation envelope proposal, in preparation as spec 10,
for the companion's own rules; step 0 only needs the in-memory type. The source route
proposal, in preparation as spec 11, consumes step 5. The result-range contract
consumes steps 1 and 2 and section 10.

## 9. Open questions for James

- **Decision G-A:** Base A or Base B; the binding shape (spec 06's or the toolkit's,
  or an amended spec 06); both version fields to 2.
- Whether step 0's mechanical parts may start before the decision (recommended: yes).

## 10. Session requirements carried from the persistent-graph spike

The spike `spike/persistent-graph` (head `9c913f9`; report `docs/specs/persistent-graph-spike.md`
on that branch) kept the dependency graph between edits and cut a one-row edit on a
50K-cell grid from 211 ms to about 20 µs through the browser command path. Read
against a converged session, its decomposition is sound but its command-path delta
assumes a single writer, which the convergence target makes false once the agent
protocol and the browser command API sit on one session (spec 08, section 7, item 5).
The session contract in step 1 must state the following before any of the spike's
production steps are taken. A test-only port of the spike's randomised differential
test (incremental versus full calculation after random edit batches) may land first
and needs nothing from this proposal.

1. **Pass identity.** A calculation is identified by its revision plus its
   `CalcContext`, or by a monotonic pass counter; revision alone does not distinguish two
   passes with different volatile contexts. The logged `calc_context` and
   `base_calc_context` (`rust/src/event_log.rs:227-251`) are the seed.
2. **Publish rule.** Publication is a compare-and-swap on (revision, base pass). The
   spike stores a result only when the model revision equals the update's revision and
   the stored base is the one the run started from; the rule keeps that and adds the
   base identity.
3. **Volatile cells** are dirty every pass and the update carries the context.
4. **`evaluated_cells` has one definition** shared by both engines. Today a clean
   formula cell whose last evaluation produced a diagnostic and no value is
   re-evaluated every pass and listed (`rust/src/toolkit.rs:1585-1603`;
   `python/workbook_forge/python_engine.py:544-545, 586`); the spike treats such cells
   as covered and omits them. Define it once, then change both engines together.
5. **Validation rule scopes.** Each structural validation rule declares what an edit can
   change, so scoped validation after an edit is a property of the rule set.
6. **In-place edits** apply only to cell batches, with the rollback list and an explicit
   statement of how a panic mid-edit leaves the session.
7. **Delta since a named base** is part of the session interface:
   `changes_since(base_pass) -> Changes{base, new} | Superseded`, with the caller
   verifying `base` and `new`; result statuses `published`, `superseded: newer revision`,
   `superseded: base moved` replace today's single `stale: bool`.
8. **One funnel for logged mutations.** While a command session is attached, every
   state change either enters its log as an intent or is refused.
9. **An interleaving test.** Writers using compare-and-swap plus calculators on one
   session with a deterministic yield at the lock boundaries; invariants: every stored
   report equals the full calculation of its named pass, and every returned delta
   equals the difference of the full calculations of its two named states.

## 11. Refutation record

An independent refuter read the first draft against the code under the `contract` and
`simplification` lenses: 6 refuted, 11 weakened, 14 confirmed, 9 citation errors. All
applied above.

| Finding | Effect |
| --- | --- |
| Refuted: `Style{number_format…}` toolkit-only (canonical has `Cell.number_format`); `source_path` a canonical field (accepted and dropped, never written) | Section 1 corrected |
| Refuted: the dual-engine fixture suite is canonical (it is toolkit-shape; canonical Rust is reached by subprocess) | Section 2 corrected; both bases' "kept" and "moved" lists corrected; spec 08 section 4a corrected in the same pull request |
| Refuted: step 0 is decision-free (it must invent binding `kind` and `required`, which spec 06 reserves to James) | Step 0 split into mechanical additions and the binding question, which moves under G-A |
| Refuted: spec 06's open questions stay open under Base B (the toolkit shape fixes them and conflicts with spec 06's non-goal) | Sections 3 and 6 corrected; the binding shape is part of G-A |
| Refuted: dependencies on specs 10 and 11 that do not exist | Section 8 marks them in preparation; step 5 cites the merged equivalence test instead |
| Weakened: lopsided cost bills (Base B omitted the shape owners, both calculators' diagnostics, Python write-side version enforcement, the re-observation; Base A padded) | Section 3 bills completed from the import map; ratio stated as about three to one |
| Weakened: canonical `metadata` is an open object; `cached_value` is a representation difference, not a concept gap; `revision` removal had no change set; the step-6 receipt check is vacuous today; two report shapes survived; the versioning paraphrase was spec 06's words; the v2 lineage question; section 10 rested on a private record | All corrected in sections 1, 3, 4, 5 and 10 |
| Citation errors: `python_engine.py:235` not `:236`; `toolkit.rs:403` not `:402`; "eight consumers" versus the list; `formula-cases.jsonl` has no workbook form; an unexplained "4a" | Corrected |
| Confirmed: the closed key sets; the toolkit-only and canonical-only field lists; arrays refused by both; the only Rust importer; Python-only intake; `import_xlsx` produces neither provenance nor the carry list; every named test file exists except spec 06's | Unchanged |

The refuter's verdict: the Base B direction survives on the import map, but the
recommendation was overstated and is not decidable until the binding shape is part of
the decision and both bills are complete. This version states the lean as a lean.
