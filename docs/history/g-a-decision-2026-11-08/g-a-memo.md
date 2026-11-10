---
creator: claude-code/Claude Fable 5.1
created: 2026-11-08T20:15:00-05:00
updated: 2026-11-08T21:40:00-05:00 (corrected after the independent refutation, g-a-memo-refutation.md: first pass 4 refuted, 15 weakened, 5 citation errors, all applied; second pass confirmed the 24 corrections and added four points on the binding rule's export effect, the step 0 / step 2 relation, the reason against reserving the spill field, and the cost of a later version, all applied)
type: decision-memo
project: workbook-forge
decision: G-A (spec 09 section 3; proposed README; CONTEXT.md open_questions)
inputs: PR #119 (spec 09 step 0, branch claude/spec09-step0-shape-conversion, head a30e956); specs 06, 09, 10, 14; fixtures/shape-conversion-cases.v1.json
reproduce: g-a-memo-measure.py, g-a-memo-diagnostic-classes.py, g-a-memo-unions.py against the #119 checkout (commands at the end); raw output g-a-memo-measure.json
summary: Step 0's measured drop and add lists confirm spec 09's lean to extending the toolkit shape, and neither of spec 09's two reversal conditions fires. Recommend Base B; the toolkit's binding shape with kind made optional and spec 06's required-false rule kept for migrated bindings; both version fields to 2 with two lineage lines; v2 not held for the spill field. Spec 14's three other sub-answers stand as recommended.
---

# G-A memo: base, binding shape, version fields

## The decision, as a statement James can adopt

> **G-A.** The owner contract is `workbook-model.v2` grown from the **toolkit shape (Base B)**. Bindings keep the toolkit's shape `{sheet, address, kind, required, min, max, choices}`, amended so that `kind` may be absent (an untyped binding accepts any scalar; the formula-cell and blocked-cell refusals still apply). `required` defaults to true for a binding written under v2; the v1 migrator writes `required: false` on every migrated binding, so no v1 document gains a refusal it did not have. `date`, `shape`, `enum` and `max_length` are not added. **Both `schema_version` and `model_version` go to 2**, and the changelog's v2 entry carries two lineage lines, one for each v1 shape it absorbs. The widened spill result field and range-shaped bindings wait for the Excel capture and take the version after v2; convergence does not wait on Excel. `formula.dependencies` stay textual `Sheet!A1`, written only by calculation; the collision owner is the calculation pass with a session-supplied blocked set; a 1×1 array result stays an array.

Everything below is the evidence and the reasoning. Spec 09 section 3 could only estimate the cost of each base from the import map; step 0's `Conversion` records measure what each shape actually holds that the other refuses, on every document in the tree. A first draft of this memo was refuted independently (`g-a-memo-refutation.md`); every refuted and weakened sentence is corrected here and the recommendation did not change.

## What step 0 measured

Both conversion edges ran over the 80 corpus workbooks that convert: 90 files; 82 imported by both importers; 2 refused by the canonical reader because a cell of a grouped formula region (a shared-formula follower in `hb_shared_formulas.xlsx`, the first cell of a data-table group in `hb_data_table_formula.xlsx`) is an empty formula with a `blocked_reason`, which the canonical schema refuses (`formula.expression` `minLength: 1`). Together: 315 sheets, 91,915 cells, 10,124 formula cells. Counts are field paths; a workbook count says how many of the 80 carry at least one. One workbook, `op_empty_sheets.xlsx`, has two sheets and no cells, which is why cell-level rows say 79 and `dimensions` says 313.

**Toolkit-only content, dropped on the way to the canonical shape**

| Path kind | Paths | Workbooks | What it is |
| --- | --- | --- | --- |
| `sheets[i].id` | 315 | 80 | identity the toolkit assigns (`sheet-N`); mechanical |
| `revision` | 80 | 80 | session state; leaves the document under either base (spec 09 §4) |
| `style.bold`, `font_color`, `fill_color`, `horizontal` | 246 | 2 | **authored presentation** |
| `style.number_format` = `General` | 46 | 7 | the default; lossless, and never "not carried" by intake either |
| `blocked_reason` | 30 | 9 | **import guard**; recoverable only from the package or the envelope's guards |
| `column_widths` | 12 | 4 | **authored presentation** |
| `cached_value` on a cell without a formula | 10 | 2 | **the cached `<v>` of spilled cells** in grouped array regions (`hb_dynamic_array_cached.xlsx` `Ranked!C2:C4`; `xw_dynamic_arrays.xlsx` `Scores!C2:C5`, `D2:D4`): the toolkit holds them; the canonical xlsx importer refuses such a package (`array_spill_refused`) and the shape conversion drops and lists them |

**Canonical-only content, added on the way to the canonical shape and dropped on the way back**

| Path kind | Paths | Workbooks | Where step 0 derives it |
| --- | --- | --- | --- |
| `cells.*.data_type` | 91,915 | 79 | from the value |
| `cells.*.provenance` | 91,915 | 79 | from the envelope's baseline (equal cell ⇒ `imported`, else `authored`) |
| `formula.dependencies` | 10,124 | 47 | the canonical analyzer |
| `formula.result` | 10,124 | 47 | blank; written by calculation |
| `sheets[i].dimensions` | 313 | 79 | from the cell addresses |
| `metadata.source` | 80 | 80 | the envelope: fingerprint and part names |
| `model_version`, workbook `provenance` | 80 each | 80 | constant; the envelope's workbook part |
| `diagnostics[i]` | 48 | 15 | analyzer refusals (classified below) |

Two readings of the two tables decide most of G-A:

1. **For an imported document, every canonical-only path is derived**: 204,679 paths, all computed by step 0 from the document, the envelope or the analyzer. The derivation exists in both languages on #119 (`convert.py` 385 lines, `convert.rs` 547, `envelope.py` 140, `envelope.rs` 164), on conversion; the importers still write the toolkit document without these fields, so under Base B the v2 writers would call the derivation step 0 already has. This reading holds for the corpus direction only. An **authored** canonical document carries content the toolkit shape loses and nothing re-derives: open `metadata` keys (the operating scenario's `name` and `description`), authored workbook and cell provenance (after the scenario's round trip no cell and not the workbook carries provenance), and `metadata.defined_names`, which canonical intake fills from the package and the analyzer reads, and which step 0 does not pass (next paragraph). Those three are Base B's bill for authored documents: `metadata` as an open object, provenance in the document, and the name table in the document.
2. **The toolkit-only paths that are neither identity, session state nor the lossless default are authored content or import guards**: 298 paths in 14 of 80 workbooks (styles, widths, `blocked_reason`, spilled caches), none of which the canonical schema can hold and none of which can be re-derived from a canonical document. Base A's bill is to add those fields to the canonical schema *and* to move every consumer that writes them today, which is the three-to-one import-map ratio spec 09 §2 counted.

The 48 diagnostics say where the toolkit *document's* missing context is:

| Cause | Diagnostics | Workbooks | What it measures |
| --- | --- | --- | --- |
| defined name absent from the document (`unsupported name 'Rate'`) | 17 | 5 | The toolkit document has no place for a name table (closed key sets, `python_engine.py:202`; `toolkit.rs:403` `deny_unknown_fields`); every importer holds one beside the document (`_names` on all 82 imported models) and passes it to the calculator as a side input (`toolkit.py:91-93`; `toolkit.rs:561, 613, 1224`). Step 0's `to_canonical(document, envelope)` takes no names, so these 17 measure that signature as much as the shape gap. Under Base B `metadata.defined_names` enters the v2 document (spec 09 §4 lists it); it is required, not optional |
| structured-reference parse failures (`[@Qty]`) in the three table workbooks | 15 | 3 | table regions live in the envelope; a table-aware parser is an engine item under either base (the classifier's rule is "parse error in a workbook with tables", exact on this corpus) |
| engine limits: 3D references 5, in-cell array functions 4, unsupported functions 4, `invalid_reference` 1 | 14 | 7 | unchanged by the base |
| `parse_error`, other: an external link; the `hb_many_sheets.xlsx` analyzer divergence recorded for N3 | 2 | 2 | N3 |

The canonical fixture (`operating-scenario.workbook.json`: 38 cells, 15 formulas, 3 inputs, 6 outputs) measures the binding gap: converted without caller-supplied kinds it loses all three input bindings whole; with kinds, each binding gains `required: true` from the toolkit's default.

## Spec 09 §3, checked against the measurement

Spec 09 §3 gave three reasons the lean to Base B was "not yet a recommendation", and named two conditions that would reverse it.

*The three reasons.* (1) Choosing Base B chooses the binding shape or amends spec 06: answered below. (2) The canonical side's assets are not prose: the receipt fingerprints, the strict evidence gate on `export_canonical`, the changelog's version 1 and both engines' read-and-write version enforcement. Step 0 re-created none of these four; they stay on Base B's bill (write-side version enforcement in the Python toolkit writers; the gate and the receipt re-pinned at step 6). Two *other* Base B bill items did advance on #119: the fingerprint rule for source identity (`source.fingerprint`, sha256 of the decoded bytes, in both languages; the cross-language test pins a floor of 40 agreeing workbooks and the recorded run agreed on all 82) and provenance derived from a baseline. (3) The two reversal conditions, next.

*The two reversal conditions.* An engine-side change count for Base B larger than Base A's consumer moves: not re-measured here (the scripts count field paths, not code changes); spec 09's own inspection found it smaller and stands, and the derived-field part of that count now exists on #119. A decision that typed bindings keep spec 06's shape: recommended against below. Neither condition fires; the lean is now a recommendation.

## Sub-answer 1: Base B

What Base B's bill looks like after step 0:

| Spec 09 §3 bill item for Base B | Status after #119 |
| --- | --- |
| `import_xlsx` produces provenance and the carry inventory, "which it never did" | provenance: the derivation exists (`to_canonical` from `envelope.baseline`, both languages); the importers' writers still omit it. Inventory: defined relative to the owner (spec 10), so this decision defines it and spec 10's PR builds it |
| the fingerprint rule for source identity; `metadata.source` | derivation exists in both languages with a cross-language test; the writer is v2's |
| `data_type`, `dimensions`, `dependencies`, `result` derived and written | derivation exists in the conversion; v2 writers call it |
| the toolkit shape owners gain about ten fields with validation | remaining: the two normalisers (`python_engine.py:201-300`; `toolkit.rs:403-418` and `WorkbookModel::validate`, `:859`) |
| both toolkit calculators' `Diagnostic` gains `classification` and `function` | remaining; the canonical analyzer's classification table is the model |
| Python toolkit writers gain version enforcement on write | remaining |
| `metadata` as an open object, with `defined_names` | remaining, and measured as needed: 17 diagnostics in 5 workbooks and the scenario's two metadata keys |
| `model.rs` becomes a reader for old v1 canonical documents through the migrator | step 6. Step 0 supplies the migrator's two halves; neither judges a document (each hands it to the target shape's reader), so the field-presence sniffer that tells the two v1 shapes apart is still step 6's work |
| receipt fingerprints re-pinned; Excel observation re-taken on the new export | step 6 only, which is the single place Base B meets Excel; steps 1 to 5 do not |

Two facts the measurement adds in Base B's favour that spec 09 did not have: the toolkit represents cells the canonical schema refuses, the empty-formula cells of grouped regions (2 of 82 corpus workbooks) and the cached values of spilled cells (10 cells in 2 workbooks), and the second of these is exactly the content spec 14 link 2 must carry on reimport, so Base A would have to widen the canonical cell or change both importers before the spill contract; and under Base B the envelope's inventory narrows (spec 10 §1: `Style` and `column_widths` leave it). Measured against today's intake inventory over the 73 workbooks canonical intake accepts: the `cols` entry (column widths) leaves on 4 workbooks, and "cell styles other than number formats", tallied once per styles part on 68 workbooks, narrows to what the five toolkit style keys (`number_format`, `bold`, `font_color`, `fill_color`, `horizontal`) do not cover: borders, italics, fonts and the rest of `styles.xml` stay not carried under either base.

## Sub-answer 2: the binding shape

Both shapes already address a binding the same way, `{sheet, address}` (`$defs.cellId`; `InputBinding`); the gap is typing. Recommend the toolkit's shape with one amendment and one migration rule, which is an amended spec 06 in spec 09 §6's terms:

| Field | v2 rule | Why |
| --- | --- | --- |
| `kind` | `number`, `text`, `boolean`, **or absent**. Absent means untyped: any scalar is accepted; the formula-cell and blocked-cell refusals still apply | Today `kind` is mandatory (`python_engine.py:268-270`; `toolkit.rs:367` `kind: String`, `:960`), so no v1 canonical binding can enter the shape without a caller inventing a type, which is why step 0 drops them (3 of 3 in the operating scenario). Inferring a kind from the bound cell's current value is a guess of the sort spec 06's intake rule forbids. Spec 06's own migrator entry was `type: null`; this is the same rule in the toolkit's vocabulary. **Change set:** both normalisers; both value validators, which index on kind (`python_engine.py:192`; `toolkit.rs:1043-1047`); the `min`/`max`-needs-`number` rule (`python_engine.py:285`); the `describe` payload, which returns the normalised bindings (`agent.py:318`; `rust/src/agent.rs:157`; the TypeScript client types it loosely, no break); spec 12's extraction rule, which reads binding `kind` (`12-*.md:117`); and both toolkit xlsx writers, whose data-validation formula is built clause by clause from `kind` (`xlsx.py` `_validation`; `xlsx.rs` `validation()`), so a kind-less binding needs a rule there (emit no validation). The agent catalog hash is unaffected (`generate_ts_contract.py` hashes the operations catalog only) |
| `required` | default **true** for a binding written under v2; the migrator writes **`required: false`** on every migrated v1 binding | The toolkit's default (`python_engine.py:278`; `toolkit.rs` `default_true`) matches the primitives layer, where every declared input is required (`docs/primitives.md:115-116`, for named expression inputs; spec 06 draws the same analogy). But both engines validate **every** binding against the bound cell's current value on every `set_inputs` and `preview_inputs`, not only the inputs being set (`python_engine.py:307-321`; `toolkit.rs:1079-1105`), so a migrated binding with a blank bound cell would refuse every input operation with `required input is blank` until filled. Spec 06 chose `required: false` for migrated bindings to avoid inventing that constraint; keep it, as a constant rule, not "true unless the bound cell is blank at migration", which would make two identical v1 documents migrate differently and would refuse blanking a filled input later. Two consequences to state: an author who wants the primitives-layer rule on a migrated document sets `required: true` explicitly, one edit per binding; and both toolkit xlsx writers serialize `required` into the exported validation (`allowBlank`, and `OR(ISBLANK(...), ...)` around the formula; `xlsx.py:733-734, 894`; `xlsx.rs:2104-2106, 2472`), so a migrated binding exports as a blank-allowing validation and a v2-authored one does not; nothing is lost, since canonical v1 export writes no validations. **Where the rule lives:** spec 09 says step 0's edges become the step 6 migrator, so the rule lands in `to_toolkit` itself at step 2: a binding without a caller-supplied kind is carried untyped with `required: false` instead of dropped (the fixture row `bindings.inputs.label` in `canonical_cases/authored-two-sheets` moves from dropped to carried), while a caller-supplied kind keeps `required: true`, because supplying a kind is authoring, not migration |
| `min`, `max`, `choices` (1 to 32) | as today | implemented and tested in both engines; `choices` is spec 06's `enum` |
| `date` kind | not added | neither engine has a date value type (`toolkit.rs` `CellValue`; `python_engine.py` `_scalar`); a date is a number with a format, so a `date` kind would be a field without a validator |
| `shape` (`scalar`, `row`, `column`, `range`) | not added in v2; **deferred**, not dropped | product-specifications.md:195-197 lists range locations, types, shapes and constraints under *Required behavior*, and :247 asks how the format represents scalar, row, column and rectangular. Neither engine validates a range binding today, and the result-range contract (spec 14) is what gives a range binding its meaning. Deferring `shape` defers a required behaviour to the version that carries spec 14 |
| `max_length` | not added | no consumer, no test |
| outputs | `{sheet, address}` as today; no type | an output's type is observable in the report |

Spec 06's non-goal ("do not port the toolkit's binding schema verbatim") exists for red-flag Spec 1's rule that there be one binding schema and no invented second one. Base B satisfies Spec 1 by adoption; the non-goal is retired by this decision, as spec 09 §6 said James would have to do.

## Sub-answer 3: version fields, and the sequencing question

Both fields to 2. Under either base the wire shape and the meaning change, which is both rows of `docs/specs/model-versioning.md`. The changelog's v2 entry carries two lineage lines because a reader cannot tell the two v1 shapes apart by version (both say `schema_version: 1`); the migrator sniffs by field presence (`model_version` and `metadata` against `revision` and `id`) and is step 6's work.

The sequencing question is spec 14's open question 1: whether both version fields "move with spec 09 or on their own", given that its widened result field (two fields under Base B, `formula.result` and `cached_value`, spec 14 link 2 representation (a)) is a G-A change but needs the saved Excel capture (links 2, 6, 7), which needs James's sitting (lane N8). Three answers are possible:

- **Hold v2 for the capture.** Puts the Excel sitting on the critical path of every Wave 2 step; spec 09 and the program plan kept it off.
- **Reserve the widened result shape in v2 now** (scalar-or-`{rows}` on the anchor, as the report wire already carries) with writers refusing to emit arrays until the capture. The reader half would be testable by hand-authored fixture, as this repository tests shapes. Three reasons not to: spec 14's own ordering puts the capture before G-A decides the fields ("capture → classification tests in both languages → G-A (base, fields, …) → links 2, 3, 4", spec 14 "Ordering"); on Base B the widening is two fields, and the second, `cached_value`, takes its widened meaning from reimport, which depends on the capture-gated classification, so half of Base B's widening cannot be specified yet; and, decisively, `shape` on bindings is deferred to the version after v2 regardless, so reserving the result field alone avoids nothing.
- **Take the next version when the capture exists.** The cost, stated in full: a third schema file; a v2→v3 migrator in both engines; a second changelog entry; the hardcoded `schema_version` in the snapshot writers (`toolkit.py:74`; `WorkbookModel::default`; the `wasm.rs` writers spec 09 step 1 lists) and both toolkit readers' version check move again, with readers accepting one more version while writers refuse all but the current (`model-versioning.md`); `shape` on bindings makes it a second bindings-schema change, so spec 06's tests and migrator entry are written twice and spec 12's binding record adjusts; every v2 fixture committed in between either migrates or stays as a v2 test input, so step 2's retirement condition recurs for v2; receipts re-pinned a second time if step 6's export changes again; spec 14's fail-closed test rewritten for v2. Not a cost: the agent protocol's `schema_version: 1` is the envelope's own constant, independent of the model's, so the catalog hash and the TypeScript client do not move with v2 or v3.

**Recommend the third.** Convergence (steps 1 to 6) then needs nothing from Excel until step 6's re-observation, and the spill field, `shape` on bindings and the result widening land together in one later version with the capture behind them. The cost above is the price of keeping Excel off the critical path; James may weigh it the other way.

## Spec 14's other three sub-questions

- **`dependencies`: keep textual `Sheet!A1`, written only by calculation (recommended by spec 14; confirmed).** New fact: dependencies are analyzer output and step 0 re-derives all 10,124 of them in both directions; the two analyzer divergences found on the corpus show a stored list can differ by engine, so the document must never be the authority for the graph. Textual and re-derivable keeps that true.
- **Collision owner: the calculation pass with a session-supplied blocked-destination set.** No new evidence; the measured tables agree that merged ranges and table regions are envelope content the document never carries (the 15 structured-reference failures above are the same fact seen from the parser).
- **1×1 results stay an array.** No new evidence; unchanged.

## Representation rules step 0 pinned, which v2 inherits

These are not James's decisions; they are listed so none is silent. Four are stated in the `note` of `fixtures/shape-conversion-cases.v1.json` and tested in both engines: `#N/A!` is spelled `#N/A`; an error value is `{error}` with `message` null when absent; an integer-valued number within 2^53 is written as an integer, negative zero as a float; `style.number_format` `General` is the default and not written. A fifth lives in the conversion code and the run history, not the fixture: a formula's text gains the canonical leading `=` (both importers store raw OOXML text without it; `convert.py` `_canonical_formula_text`; `docs/run-history.md`, 2026-11-07 entry). One is still open for the writers, not the contract: the two canonical JSON writers spell floats differently (`1e-07` against `1e-7`), recorded for N3; v2's byte-identity test across languages depends on it.

## What the decision unblocks, and where it is recorded

On James's word: spec 09 §3 gets a decision line and §6's alignment applies (spec 05 reframed to the owner model; spec 06 marked amended as above); the proposed README's decision column and `CONTEXT.md` `open_questions` drop G-A; `model-versioning.md` and `model-changelog.md` get the v2 entry when step 6 lands, not before. Unblocked: Wave 2 step 1 (session over the owner model, with spec 09 §10's nine requirements), step 2 (typed bindings under the shape above), steps 3 to 6; spec 10's inventory definition; spec 12's binding record; spec 13's `envelope_mismatch`, which compares `metadata.source.fingerprint` once v2 writes it.

## Reproduction

From the root of a checkout of PR #119, with the repository's Python environment, where `BUNDLE` is the directory holding this memo:

```
PYTHONPATH=python python "$BUNDLE/g-a-memo-measure.py" .
PYTHONPATH=python python "$BUNDLE/g-a-memo-diagnostic-classes.py" .
PYTHONPATH=python python "$BUNDLE/g-a-memo-unions.py" .
```

The first imports every `fixtures/corpus/*.xlsx` with `import_xlsx`, runs `to_canonical` with the envelope and `to_toolkit` back, classifies every listed path, and prints the counts above as JSON (`g-a-memo-measure.json` is the output this memo quotes); it also runs the operating scenario in both directions and tallies the fixture cases. The second classifies the 48 diagnostics by cause; the third gives the workbook unions. The refuter reran all three against a30e956 and found the output byte-identical.
