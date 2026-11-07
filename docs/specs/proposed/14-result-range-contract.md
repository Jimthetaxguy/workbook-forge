---
type: technical-specification
status: proposed — needs G-A if a model field is added
base: main 41f28d3
summary: Declare the result-range (spill placement) contract as a capability chain, from array semantics through anchor ownership, collisions, dependencies, atomic publication, projection and OOXML emission, to the Excel evidence the oracle must collect, so that red-flag Spec 2 and proposed spec 01 Phase B can close or be deliberately left open. The chain is a decomposition today, not yet a contract: link 1 carried engine-parity divergences until PR #111, no reader in either language distinguishes a dynamic-array anchor from a legacy CSE array, the repository's only dynamic-array encodings are hand-authored or XlsxWriter output, and the oracle's contract schema cannot carry per-cell expected values. A saved Excel capture is the precondition of links 2, 6 and 7.
---

# 14 — Result-range (spill placement) contract

## Problem

Both engines calculate bounded arrays in memory and stop there. `ArrayValue` is a frozen row-major rectangle capped at 100,000 cells (`python/workbook_forge/__init__.py:40-58`; `rust/src/lib.rs:37,105-138`), `evaluate_result` says "worksheet spill placement is not modeled" (`__init__.py:5617`; `lib.rs:152`), and the toolkits carry the result as `CalculatedValue::Array { rows }` / `{"rows": [...]}` for the anchor cell only (`rust/src/toolkit.rs:234-252`; `python/workbook_forge/python_engine.py:486-487`). Nothing writes an array into neighbouring cells, and every serialized shape refuses one: canonical export and import raise `array_spill_refused` (`python/workbook_forge/canonical_xlsx.py:100-108,142-152`), the canonical calculators report `array results are unsupported` (`python/workbook_forge/model.py:879-885`), the Python toolkit writer refuses `worksheet array spill caches are unsupported` (`python/workbook_forge/xlsx.py:1145`; the refusal `docs/excel-observations.md:100-101` and `python/tests/test_excel_oracle.py:374-381` name), the Rust writer refuses an array cache (`rust/src/xlsx.rs:2030-2042`), the toolkit importers block the whole `ref` region of any `t="array"` formula (`xlsx.py:379-409,211-225`; `xlsx.rs:1106-1129,1155-1161,530-546`), the oracle marks the `array_spill` class `blocked` / `unsupported_capability` before Excel opens (`tools/excel_oracle.py:47-56,1275-1291,1446-1457`), the browser paints a `{rows}` value only when it is 1×1 (`web/univer-spike/src/adapter.ts:105-123,383-390`), and the TypeScript client declares `arrays: false` (`typescript/src/client.ts:31`). Spec 2 says this leaves the export gate open (`docs/specs/red-flags.md:129-134,150-153`); spec 01 lists the writer-side contract as a non-goal and the missing contract as its open question; `docs/specs/proposed/README.md` records it as the missing spec. Spec 09 section 1 notes that arrays are refused by both serialized forms, so an array-capable result is a contract change on either base (decision G-A).

The repository holds no Excel-written dynamic array. The dynamic-array markup it knows (`cm="1"` on the anchor, `<f t="array" ref=…>`, `xl/metadata.xml` with `XLDAPR` and `fDynamic="1"`, the `sheetMetadata` relationship and content type) is written by `tools/corpus/handbuilt.py:573-588` into `fixtures/corpus/hb_dynamic_array_cached.xlsx`, whose manifest entry carries no provenance, and by XlsxWriter into `xw_dynamic_arrays.xlsx`. Both are models of what Excel writes, not captures of it. Every statement below about how Excel stores or places a spill is marked **[unobserved]** until a capture exists.

## Outcome

- One declared chain, each link with an owner and the evidence that proves it, so the `array_spill` class can move from `blocked` to `observed` without a scalar reduction standing in for placement (`docs/excel-observations.md:97-105`).
- A document shape in which a spilled cell is derivable from its anchor, never a cell with a formula of its own, and in which a v1 reader fails closed on a v2 array.
- An honest split between what the calculation decides (collision, growth, `#SPILL!`), what the session supplies and guarantees (the blocked-destination set, all-or-nothing publication), and what only Excel can confirm.

## Scope

Links 1 to 8 below, the refusal codes, and the acceptance tests by name. The proposal covers dynamic-array anchors produced by FILTER, SORT, UNIQUE, SEQUENCE, RANDARRAY and elementwise lifting (`__init__.py:1186-1260,1297-1541,3030-3055`; Rust lifting `lib.rs:1428-1466`, `sort_call` `:1246`, `unique_call` `:1346`, `filter_call` `:1469`, SEQUENCE `:1533-1610`, `randarray_call` `:1653`), across Python, Rust, the canonical and toolkit document shapes, the browser, the TypeScript client, and the oracle. `catalog/formulas.json` has `semantic_specs` entries for FILTER, SORT, UNIQUE and SEQUENCE and none for RANDARRAY.

## Non-goals

- Legacy CSE arrays (`t="array"` with `ref`, no dynamic-array metadata) stay refused as today: blocked regions on both toolkit readers (`xlsx.py:387-409`; `xlsx.rs:1116-1129`) and `array_spill_refused` on the canonical reader, which sends every `t="array"` cell through `_spill_marker` (`canonical_xlsx.py:142-152,540-547`; `python/tests/test_intake.py:656-658`). `unsupported_formula_group` is for `shared` and `dataTable` groups only (`canonical_xlsx.py:153-161,556-560`; `test_intake.py:584-597`). Reading CSE arrays as fixed-shape arrays is a later item.
- The spill-range operator (`B7#`) and implicit intersection (`@`). Both lexers treat `#` as the start of an error literal only (`__init__.py:176`; `lib.rs:376-395`). Today the engines disagree on the refusal: Python's lexer raises `unsupported character at offset …` (`__init__.py:596-598`), which the toolkit maps to `parse_error` (`python_engine.py:338-347`); Rust returns `FormulaError::Unsupported("unknown or unsupported error literal")` (`lib.rs:393-395`), mapped to `unsupported_formula` (`toolkit.rs:447-450`). Link 1 asks both to refuse `B7#` as `unsupported_formula` with the reason "spill range operator"; neither emits `formula_unsupported`, a message-table code this proposal's first draft named by mistake.
- In-cell arrays, lists and nested arrays stored as rich values (`vm`), which are a different construct and stay refused with `in_cell_array` (`docs/in-cell-arrays.md`).
- Any `schema_version` or `model_version` bump before James's G-A decision (`CONTEXT.md:31`).

## Design

The contract is a chain. A link is "held" only when its owner's tests pass and its evidence row is filled; a later link cannot be claimed while an earlier one is open.

| Link | Owner | Today | Evidence that holds it |
| --- | --- | --- | --- |
| 1 Array semantics | both engines | held for shape and size (`__init__.py:40-58`; `lib.rs:105-145`); parity on `B7#`, lifted error elements and the oversize-result code is restored by PR #111, with a pin that `=#N/A!` is a parse error in both engines | the two existing tests plus the four parity tests named in link 1; catalog `semantic_specs` for FILTER, SORT, UNIQUE, SEQUENCE |
| 2 Anchor and ownership | the contract (spec 09 §4) | refused on every serialized path; no reader classifies | a saved Excel capture (precondition); classification tests in both languages; schema change under G-A; v1 fail-closed test |
| 3 Collisions and growth | the calculation pass, given a blocked-destination set the session derives from the envelope | not modeled; the engines never receive `merged` or `tables` | `#SPILL!` and growth tests in both languages; per-case Excel observation |
| 4 Dependency effects | the toolkit graphs | array dependency refused (`toolkit.rs:1452-1458`; `python_engine.py:479-480`) | element-read and `spill_cell_owned` tests |
| 5 Atomic publication | the session (spec 09 §10) | strict compare-and-swap exists (`browser_contract.rs:235-248`); `changed` is keyed by cell | interleaving test with the range invariant; replay equality; patch-expansion test |
| 6 Projection and emission | browser, writers, envelope (spec 10) | 1×1 only (`adapter.ts:105-123`); writers refuse (`xlsx.rs:2030-2042`; `canonical_xlsx.py:100-108`; `xlsx.py:1145`) | browser tests; writer and reimport tests against the capture |
| 7 Excel evidence | the oracle | class `blocked` (`excel_oracle.py:1446-1457`); the contract schema carries shape only; one cycle | a two-cycle harness; per-cell expected values in the contract; an `observed` receipt |

### 1. Array semantics — owner: the two engines

- **Shape.** A result is a scalar or a non-empty rectangle with explicit rows × columns; ragged rows are invalid (`__init__.py:47-51`; `lib.rs:113-119`; Python raises `ValueError`, Rust `#VALUE!`/`#NUM!`, both reach `#VALUE!` at the evaluate boundary). `CONTEXT.md:80` defines `ArrayValue`; `:83` the shared `FormulaResult` contract.
- **Element types.** Each element is a v1 scalar: blank, number, text, boolean, or one of the nine errors including `#SPILL!` and `#CALC!` (`schemas/workbook-model.v1.schema.json` `$defs.value`, `$defs.error`; `lib.rs:79,96`; `__init__.py:176`). One divergence broke this until PR #111: Rust's elementwise lifting stored any `FormulaError`, including `Unsupported` and `Parse`, as an element (`lib.rs:1460`, `unwrap_or_else(Value::Error)`), which `CellValue::from_engine` would serialise as `error: "unsupported formula: …"` (`toolkit.rs:226-229`), outside the enum; it now raises the formula-level refusal a scalar formula gets. A second suspected divergence did not exist: Python's lexer regex listed `#N/A!` as an alternative after `#N/A` (`__init__.py:176`), but a regex alternation takes the leftmost match, so `=#N/A!` lexed as `#N/A` then `!` and was a parse error in both engines; the dead alternative is removed and a parity test pins the agreement. `#N/A!` stays an accepted spelling for an authored cell value in both engines, normalised to `#N/A` before evaluation (`python_engine.py:445`; `toolkit.rs:212`), which is why `IFNA` and `ISNA` still compare against both codes (`:2821,3963`). The rule: elements are exactly the nine errors; an unsupported or unparsable sub-expression during lifting is a formula-level refusal, never an element.
- **Blank rule.** Blanks inside an array stay blanks and are not coerced to zero. A scalar `Blank` result is written as numeric zero in both toolkits (`toolkit.rs:1473`; `python_engine.py:490`), whatever produced it; the code comment's claim that Excel stores such a result as numeric zero has no row in `docs/excel-observations.md` **[unobserved]**.
- **FILTER mask rule.** `include` must be one column of source height or one row of source width; no match with no `if_empty` is `#CALC!` (`__init__.py:1504-1529`; `CONTEXT.md:81`). The `#VALUE!` for a two-dimensional mask is the catalog's `shape_profile`, a Workbook Forge profile: the Microsoft source `ms-filter` is recorded as saying only that the call produces an error (`catalog/formulas.json` `semantic_specs.FILTER`), and the coercion of blanks and text to Boolean is likewise `include_coercion_profile`.
- **Size limits.** 100,000 elements per array (`__init__.py:52-53`; `lib.rs:37,121-123`); 100,000 materialized result cells per calculation across all anchors, with a separate equal budget for output aliases, so retained elements are bounded by twice that (`python_engine.py:38,498-504`; `toolkit.rs:22-24,1486-1507`, `resource_limit`); `MAX_WORKBOOK_CELLS`, 100,000 populated cells (`python_engine.py:34`; `xlsx.rs:533`), which a reimport that places spilled caches into cells counts against, as `block_range` already counts region cells; and the oracle's 10,000-cell cap on a declared spill check (`excel_oracle.py:698`; the contract schema's `spill_cells.maxItems`). The grid bound (1,048,576 × 16,384) is enforced by the oracle's rectangle check today (`tools/excel_oracle.py:94-100`) and becomes a calculation rule in link 3. Spec 03's per-cell memory bound applies unchanged. Oversize results carry different codes today: Python's SORT and UNIQUE raise `ValueError` out of `_formula_array_result` (`__init__.py:1278-1294`) and surface as `#VALUE!` through the generic catch (`:5678-5679`), FILTER catches the same as `#NUM!` (`:1541-1542`), Rust returns `#NUM!` for all (`lib.rs:121-122`); range sources are capped at 100,000 (`:914`) so the case is nearly unreachable, and it still needs one agreed code and a parity test.
- **Today.** Shape and size are held by `test_array_results_are_explicit_and_array_dependencies_refused` (`python/tests/test_python_engine.py:80`) and `array_results_have_shape_without_implicit_spill_projection` (`toolkit.rs:2451`). PR #111 adds four parity tests in both languages: `B7#` is `unsupported_formula` naming the spill range operator; `=#N/A!` is a parse error in both engines while the authored-value alias normalises to `#N/A`; lifting never yields a non-Excel error element; an oversize result is `#NUM!` in both. The link is held once #111 is merged; none of it waited on an Excel capture.

### 2. Anchor and ownership — owner: the contract (spec 09 section 4)

- **Anchor.** The formula cell is the anchor. Its **result range** is the rectangle whose top-left is the anchor and whose extent is the result's shape. A 1×1 array result has a result range equal to the anchor and no spilled cells; it stays an array, not a scalar. Today a 1×1 array is an array everywhere (`python/tests/test_compatibility_prefixes.py:20-22`; the wire `{"rows": [[…]]}`; `model.py:879`; `xlsx.rs:2037-2039`; `_spill_marker` refuses a single-cell `ref`, `canonical_xlsx.py:545-546`, `test_intake.py:656`), and how Excel encodes a 1×1 dynamic result is **[unobserved]** (`op_array_formulas.xlsx` `ref="D1"` is openpyxl's CSE, not Excel's). Collapsing 1×1 to a scalar would change five places on an unobserved premise; it is open question 4, with "keep it an array" recommended.
- **Spilled cells** are the cells of the range other than the anchor. They are owned by the anchor, hold no formula and no authored value, and are never bound as inputs (the toolkit already refuses binding a formula or blocked cell, `python_engine.py:276-277`).
- **Representation.** Two candidates:
  - *(a) Anchor-owned result, derived index.* The anchor's calculated result widens from scalar to scalar-or-`{rows}`: canonical `formula.result` (`$defs.formula.result`, scalar today) and the toolkit's wire `{"rows": ...}` that already exists (`toolkit.rs:236-238`; `python_engine.py:487`). Spilled cells are **not serialized**; a validator derives an ownership index (spilled address → anchor) and refuses overlaps. The index serves every reader of a cell's value, not only the graph and the browser: `get_cell`, which returns none for a spilled address today (`toolkit.rs:1439-1440`), `read_cells`, paged range reads (`typescript/src/client.ts:31` `paged_range_reads`), delimited export, and canonical `Cell.value`. Reimport places the spilled `<v>` caches into the anchor's imported cache, which on Base B means `cached_value: Option<CellValue>` (`toolkit.rs:318`) also widens to carry an array; on Base A it is `Cell.value` with `provenance.origin: imported` at the anchor, and the anchor's provenance covers its range since a spilled cell has no cell of its own to carry one. So (a) widens one field on Base A and two on Base B.
  - *(b) Per-cell marker.* Each spilled cell is serialized with `spilled_from: "B7"` and its cached value. Readers see values without consulting the anchor, at the cost of a second source of truth that can disagree with the anchor's shape.
  - **Recommendation: (a).** No duplicated values, and `additionalProperties: false` plus the `oneOf` on `$defs.value` make a v1 reader refuse a v2 array document rather than misread it (`schemas/workbook-model.v1.schema.json`; `python_engine.py:235`; `toolkit.rs:312`, `deny_unknown_fields`). A workbook-level map from anchor to range adds nothing (a) does not derive.
- **Dependencies field.** `Formula.dependencies` is a list of addresses the schema requires. A formula whose text reads `B8` keeps `B8` in `dependencies`, as written; the graph resolves `B8` to its anchor through the index when it is built (link 4). The document stays faithful to the formula text and the graph owns the resolution (open question 2).
- **Schema consequence.** Either candidate changes a serialized shape, so under spec 09 it is part of **G-A**: both version fields to 2, and the field lands on whichever base James picks. Until then the array result stays memory-only and every serialized path keeps today's refusal.
- **Reimport classification.** Today no reader in either language distinguishes a dynamic-array anchor from a legacy CSE array. Python's `_spill_marker` uses `t="array"`, a multi-cell `ref` and `cm` as alternative triggers for one refusal and returns a marker string, never a class, marking a `t="array"` cell identically with or without `cm` (`canonical_xlsx.py:540-552`); Rust never reads `cm` (no `"cm"` in `rust/src/xlsx.rs`). The proposed rule, dynamic-array anchor = `t="array"` with `ref` **and** cell metadata `cm` pointing at an `xl/metadata.xml` entry with `XLDAPR` and `fDynamic="1"`, legacy CSE = `t="array"` with `ref` and no `cm`, plain cell = no `<f>`, is supported in the tree only by the hand-authored and XlsxWriter encodings named in Problem **[unobserved]**. A saved Excel capture of a dynamic anchor, a `#SPILL!` anchor, a grown spill and a legacy CSE array with identical text is the precondition of classification code in either language; the manifest entry for the capture records its provenance, and the classification tests in both languages run against it.

### 3. Collisions and growth — owner: the calculation pass, with the blocked set supplied by the session

- **Owner.** Collision needs inputs the toolkit documents do not carry: merged ranges and table regions live in the preservation envelope (spec 10 `merged`, `tables`; spec 09 §4 "not in the document"), and neither engine reads them (`merged` appears nowhere in `rust/src/toolkit.rs` or `python/workbook_forge/python_engine.py`). Deciding collision after the pass is wrong, because dependents would already have evaluated against the placed array. So the session derives a **blocked-destination set** from the envelope (`merged` non-anchor cells, `tables` result regions) and passes it into the calculation call as a new argument; the pass adds the document's own blockers (authored values, formulas, other anchors' ranges, the grid) and decides placement. The argument is part of the calculation contract, not the document (open question 3).
- **Blocked destinations.** A spilled cell's destination is blocked when it holds an authored value or a formula, lies in another anchor's result range, is in the supplied blocked set, or lies outside the grid. A blocked range produces **`#SPILL!` at the anchor** and places nothing; `#SPILL!` is already in the error set (`$defs.error`; `lib.rs:79`). Microsoft's behaviour page is the catalog source id `ms-dynamic-array-spill`; its text is not quoted in the repository, so that an occupied destination yields `#SPILL!` with nothing placed, and which of merged cells, table regions and the worksheet edge Excel reports as `#SPILL!` rather than handling otherwise, are **[unobserved]** and confirmed per case.
- **Refused versus `#SPILL!`.** A collision found during calculation is a *value* (`#SPILL!`), never a refusal, so the pass completes and the document remains exportable. A refusal is reserved for *operations*: an edit targeting a spilled cell, a document whose declared ranges overlap, or a declared `ref` that holds a formula (link 8).
- **Growth and shrinkage** **[unobserved]**. The result range is recomputed every pass. Cells that leave the range are published as blank with no formula; cells that join must be free by the rule above, else the whole anchor is `#SPILL!` and its previous range is cleared. Within one pass anchors are placed in dependency order, ties by sheet order then row-major address; when two anchors contend, the later-placed one is `#SPILL!`. This tie rule is a Workbook Forge profile until an observation says what Excel does.
- **Grid** **[unobserved as Excel's rule]**. `anchor_row + rows − 1 > 1,048,576` or `anchor_col + cols − 1 > 16,384` is `#SPILL!`; the oracle's `_spill_rectangle` encodes the bound as its own `ValueError` today (`tools/excel_oracle.py:94-100`).

### 4. Dependency effects — owner: the toolkit graphs

- A formula that references a spilled cell depends on the **anchor**; the graph records the edge to the anchor, resolved through the index from the textual reference the document keeps (link 2), and evaluation reads element (row offset, column offset) of the anchor's array. Today a spilled address resolves to a blank because no cell exists (`toolkit.rs:1439-1440`), and an array-valued dependency is refused with `unsupported_array_reference` (`toolkit.rs:1452-1458`; `python_engine.py:479-480`); that refusal is replaced by element reads once the index exists.
- A range reference overlapping a spill resolves each covered cell through the index; a reference to the anchor itself yields the whole array (today's behaviour for a referenced array value).
- Ownership is a derived index on the calculation snapshot (`CONTEXT.md:65`), rebuilt from the published results of the previous pass, so an edit to a spilled cell can be refused before calculation (`spill_cell_owned`, naming the anchor). The alternative, redirecting the edit to the anchor, is rejected: it would silently change a formula.
- `B7#` is refused `unsupported_formula` ("spill range operator") in both engines in this release (link 1's parity item); adding it later is a lexer change in both engines and a dependency on the anchor's shape, not on the range cells.

### 5. Atomic revision publication — owner: the session (spec 09 section 10)

- A pass is identified by revision plus `CalcContext` or a pass counter (section 10 item 1); publication is compare-and-swap on (revision, base pass) (item 2), already the strict mode of the command envelope (`rust/src/browser_contract.rs:235-248`).
- The published set for an anchor is the anchor value **and** every cell of its new range **and** every cell of its previous range that left; all are stored in the same swap or none are. A pass that is superseded publishes nothing (item 7's `superseded` statuses).
- **Patches.** `changed` is built today from the `report.values` keys whose value differs from the baseline plus the edited inputs (`browser_contract.rs:856-880`); a spilled cell is in neither set, because `values` is anchor-keyed and spilled cells are not serialized. The session therefore **expands** each anchor whose array changed, through the index, into one `ChangedCell { sheet, address, value, formula: None }` per cell of its new range plus one per cell that left (value blank), and the anchor itself with its formula (`browser_contract.rs:311-316`); `values` stays anchor-keyed. A client that paints `changed` paints the whole range with no spill knowledge.
- **Event log.** `deltas` records the **anchor's** change only, `[key, before, after]` with `after` the new `{rows}` or `#SPILL!` (`rust/src/event_log.rs:17-19,227-256`); spilled cells and cells that left are derived on replay from the before and after shapes, so a 100,000-cell spill is one triple, not 100,000, and the unimplemented compaction (`event_log.rs:28-29`) is not made worse. The docstring "formula cells whose value changed" (`event_log.rs:19`; `browser_contract.rs:815`) keeps its meaning. `intent` events are unchanged because spilled cells are never inputs; an intent targeting one is logged `rejected` with `spill_cell_owned` (`event_log.rs:14-16`). Replay re-derives placement, so the tie rule in link 3 must be deterministic.
- The interleaving test of section 10 item 9 gains an invariant: every published range equals the full calculation's range for its named pass, and the expanded `changed` equals the derived range.

### 6. Projection and emission — owner: browser and writers; keep rules from spec 10

- **Browser.** Replace the 1×1 projection (`adapter.ts:105-123`) with per-cell projection from `changed`; show the anchor's formula at the anchor only; a spilled cell shows its value, is not editable, and the notice names the anchor instead of "multi-cell array result is not shown" (`adapter.ts:385-387`). `web/forge-engine/src/protocol.ts:23-24` already types `{ rows }`. The client capability flips `arrays: false` to `true` only after this link's tests pass (`typescript/src/client.ts:31`).
- **OOXML emission** **[unobserved]**. Anchor: `<f t="array" ref="B7:B9">` plus the dynamic-array cell metadata and its metadata part; every spilled cell: a `<c>` with cached `<v>` and no `<f>`; `[Content_Types].xml` and the workbook relationships gain the metadata part. The names in the tree are the hand-authored ones (`handbuilt.py:573-588`); the capture of link 2 confirms or corrects them before writer code. The preservation envelope must keep an imported `xl/metadata.xml` and merge rather than replace it (spec 10 `baseline.parts`, `inventory`); the writer must not emit into the supplied blocked set, which calculation already turned into `#SPILL!`.
- **Reimport** reads the anchor by classification (link 2), takes the spilled `<v>` values as the anchor's imported cache, and refuses a `<f>` inside the declared `ref` (`spill_range_conflict`). The canonical path's `array_spill_refused` and both toolkit writers' refusals are retired for v2 documents only; v1 documents keep them.

### 7. Excel evidence — owner: the oracle (`tools/excel_oracle.py`)

Today the class cannot be observed as placement even if it were unblocked. `$defs/arrayResult` in `schemas/excel-roundtrip-contract.schema.json` is `{type, shape}` with `additionalProperties: false`, so the contract has no field for expected per-cell values; `roundtrip_result_matches` compares `{"type": "array", "shape"}` only (`excel_oracle.py:1076-1077`); `spill_cells` is read for contract validation and blocker reporting (`:701-703,728-729,1286`), never in a comparison; the `actual` shape comes from the SDK's reimport of the Excel-saved file, an engine path; and the harness runs one cycle, applying `edits` inside the AppleScript before recalculation (`:1113-1116`), so there is no Excel-observed pre-edit state. The fixture's anchor is `=SEQUENCE(3)` with a constant argument (`fixtures/excel-roundtrip-red-flags.contract.json`, check `array-spill-placement`, `Red Flags!B7`, `B7:B9`), so the contract's edit (`Assumptions!B1` to 25) cannot change its shape.

The class therefore needs, before its first receipt: a contract field `expected_cells: {address: value}` with values from independent arithmetic (for `=SEQUENCE(3)`: 1, 2, 3); a harness comparison that reads each declared cell's cached `<v>` from the Excel-saved package directly with the package reader, never through an engine; a `#SPILL!`-at-anchor expectation and a blocked fixture with an occupied cell in range; a growth fixture whose shape depends on an edited input (`=SEQUENCE(Assumptions!B1/5)`: four rows at 20, five at 25, both independent arithmetic) with a `cycles: 2` harness option that saves and reads once before the edit and once after, so cells that joined and cells that left are both Excel-observed; and the 10,000-cell cap kept. The scalar reduction `=SUM(SEQUENCE(3))` stays a separate class and proves nothing about placement (`docs/excel-observations.md:97-105`). Unknown without observation: how Excel stores the dynamic-array metadata; whether Excel rewrites `ref` when a spill grows; how a `#SPILL!` anchor is stored (single-cell `ref`?); merged-cell, table-region and grid-edge behaviour; whether a legacy CSE array with identical text differs only by the metadata; how a 1×1 dynamic result is encoded. Expected values come from Microsoft examples or independent arithmetic, never from either engine; with the current schema that rule cannot be met for this class, which is why the schema change comes first.

### 8. Refusal codes

Reused: `array_spill_unsupported` for surfaces that have not adopted the contract (`catalog/refusal-codes.json`, phrases "array spill", "spill caches"); `array_spill_refused` on canonical v1 (`inherited`); `unsupported_formula_group` for `shared` and `dataTable` groups; `unsupported_formula` for `B7#` in both engines; `resource_limit` for the 100,000-cell budgets; `merged_cell`, `out_of_bounds` and `revision_conflict` unchanged; `preserved_only_edit` keeps its phrase rule (`refusal-codes.json:160`), though nothing in `rust/src` or `python/workbook_forge` produces its message today. Catalogued now, because it is today's refusal and is in neither `codes` nor `inherited`: `unsupported_array_reference` (`toolkit.rs:1453`; `python_engine.py:480`) joins `inherited`. New, both engines and the browser, with the same message: `spill_cell_owned` (an edit targets a cell inside `{anchor}`'s result range) and `spill_range_conflict` (a document or package declares overlapping ranges, or a formula inside a declared `ref`). Because the browser surfaces them by message, they go in `codes` and `rules` with the identifier itself as a phrase, as `merged_cell`, `preserved_only_edit` and `revision_conflict` do, not in `inherited`, whose definition is codes the message table does not produce; `docs/refusal-codes.md` gains their rows.

## Schema / model-version impact

Link 2 widens the anchor's result field from scalar to scalar-or-`{rows}`: one field on Base A (`formula.result`), two on Base B (the result field and `cached_value`), and adds nothing else to the document; that is a G-A change and both version fields move to 2 together with spec 09. No change to `$defs.error` (`#SPILL!` exists; the dead `#N/A!` alternative leaves Python's lexer, nothing is added to the schema) or `$defs.diagnostic` (`#SPILL!` is a value). The calculation report's `values` already carries `{rows}` for anchors (`toolkit.rs:1205-1206`); the calculation call gains the blocked-destination argument (link 3). The round-trip contract schema gains `expected_cells`, a `#SPILL!`-at-anchor expectation, a growth check type and `cycles`; `ROUNDTRIP_RESULT_TYPES` already includes `error` and `array` (`tools/excel_oracle.py:45`).

## Acceptance tests

```python
# python/tests/test_result_range.py
def test_spill_range_operator_is_unsupported_formula_in_both_engines(): ...        # link 1 parity
def test_na_bang_is_not_a_formula_literal_in_either_engine(): ...                   # link 1 parity
def test_lifting_never_yields_a_non_excel_error_element(): ...                      # link 1 parity
def test_oversize_array_result_has_one_agreed_code(): ...                           # link 1 parity
def test_result_range_is_anchor_plus_shape_and_one_by_one_has_no_spilled_cells(): ...
def test_spilled_cells_hold_no_formula_and_cannot_be_bound(): ...
def test_occupied_destination_yields_spill_error_at_anchor_and_places_nothing(): ...
def test_supplied_blocked_set_yields_spill_error_at_anchor(): ...                   # merged / table cells from the envelope
def test_growth_clears_cells_that_leave_and_requires_cells_that_join_free(): ...
def test_two_anchors_contending_follow_the_declared_order_rule(): ...
def test_reference_to_spilled_cell_depends_on_anchor_and_reads_its_element(): ...
def test_dependencies_field_keeps_the_textual_reference(): ...
def test_edit_into_spilled_cell_is_refused_spill_cell_owned_naming_anchor(): ...
def test_v1_reader_fails_closed_on_v2_array_result(): ...
def test_classification_fixture_is_an_excel_capture_with_provenance(): ...          # manifest, not handbuilt or XlsxWriter
def test_canonical_reimport_classifies_dynamic_anchor_legacy_cse_and_plain_cell(): ...
def test_reimport_refuses_formula_inside_declared_ref_spill_range_conflict(): ...
def test_contract_accepts_expected_cells_spill_anchor_growth_and_cycles(): ...      # python/tests/test_excel_oracle.py
@pytest.mark.oracle_optional
def test_array_spill_class_checks_every_declared_cell_from_the_saved_package(): ...
@pytest.mark.oracle_optional
def test_blocked_spill_fixture_reads_spill_error_at_anchor_in_excel(): ...
@pytest.mark.oracle_optional
def test_two_cycle_growth_matches_joined_and_left_cells(): ...
```

```rust
// rust/tests/result_range.rs
fn spill_range_operator_is_unsupported_formula() {}
fn lifting_never_yields_a_non_excel_error_element() {}
fn oversize_array_result_has_the_agreed_code() {}
fn result_range_is_anchor_plus_shape_and_one_by_one_has_no_spilled_cells() {}
fn occupied_destination_yields_spill_error_at_anchor_and_places_nothing() {}
fn supplied_blocked_set_yields_spill_error_at_anchor() {}
fn growth_clears_left_cells_and_requires_joined_cells_free() {}
fn reference_to_spilled_cell_depends_on_anchor() {}
fn edit_into_spilled_cell_is_refused_spill_cell_owned() {}
fn publication_is_all_cells_or_none_under_compare_and_swap() {}   // section 10 item 9 interleaving
fn changed_expands_anchor_to_its_range_and_left_cells() {}
fn calc_event_deltas_record_the_anchor_only_and_replay_derives_the_range() {}
fn xlsx_reader_classifies_dynamic_anchor_by_metadata_and_blocks_legacy_cse() {}   // against the capture
fn xlsx_writer_emits_anchor_metadata_and_cached_values_in_every_spilled_cell() {}
```

Browser (`web/forge-engine/test/engine.node.test.mjs`, `web/univer-spike/tests/acceptance.spec.ts`): `changed carries every spilled cell with formula null`; `grid paints every spilled cell and shows the formula at the anchor only`; `typing into a spilled cell is refused with a notice naming the anchor`; `CLIENT_CAPABILITIES.arrays flips only with the projection tests`.

For this document alone: `python -m ruff check python tools examples setup.py` and `python -m pytest -q python/tests/test_project_policy.py python/tests/test_corpus.py::test_capabilities_document_is_current`.

## Dependencies

- A saved Excel capture of a dynamic anchor, a `#SPILL!` anchor, a grown spill and a legacy CSE array, with provenance in the corpus manifest: precondition of links 2, 6 and 7. No Excel runs for this proposal; the capture is a spec 01 harness item.
- Spec 09 section 4 (the owner of `formula.result` / `cached_value`; G-A) and section 10 (pass identity, compare-and-swap publication, delta since a named base, one funnel, the interleaving test). Links 2 and 5 cannot be coded before the base is chosen.
- Spec 10 (`merged`, `tables`, `insertion_bound`, `baseline.parts` for the metadata part; the move of merged-cell refusal to `apply`), which supplies the blocked-destination set of link 3.
- Spec 01 Phase B (the `oracle_optional` / `oracle_required` marks and the `array_spill` fixture; this spec is what lets that class leave `blocked`), plus the contract-schema change and two-cycle harness of link 7.
- Spec 03 (the per-cell memory bound the 100,000-cell budgets sit under).
- Ordering: link 1 parity fixes (PR #111, including the oversize-result code) → capture → classification tests in both languages → G-A (base, fields, `dependencies`, collision owner, 1×1) → links 2, 3, 4 → link 5 → link 6 → contract schema and two-cycle harness → link 7 → Spec 2 close. The parity fixes never waited on the capture; the capture gates only what a reader must recognise and a writer must emit.

## Open questions for James

1. **G-A, base and fields:** which base carries the widened result field (one field on Base A, two on Base B), and whether both version fields move with spec 09 or on their own.
2. **G-A, `dependencies`:** keep the textual reference in the document and resolve to the anchor in the graph (recommended), or store the anchor.
3. **G-A, collision owner:** the calculation pass with a session-supplied blocked-destination set (recommended), or the session after the pass, which would evaluate dependents against a placement later withdrawn.
4. **G-A, 1×1 results:** keep a 1×1 array an array with a result range of the anchor alone (recommended, no observation needed), or collapse it to a scalar once Excel's encoding is observed.
5. **Scope of the first release:** does the toolkit's first release include spill at all? The vision treats it as a capability rather than a gate; if it ships refused, Spec 2's spill class stays open by decision and spec 01's question (can 07 ship with spill open) is answered yes.
6. **Spill-range operator:** in or out; this proposal says out, refused `unsupported_formula` in both engines.
7. **Legacy CSE arrays:** keep refused, or read as fixed-shape arrays once the metadata distinction is observed.
8. **Tie rule** for two anchors contending (link 3): accept the dependency-order profile, or wait for an observation of Excel's order.

## Refutation record

An independent refuter read the first draft against the code under the `contract`, `parity` and `oracle` lenses: 7 refuted, 10 weakened, 20 confirmed, 8 citation errors, 17 Excel claims without an observation. All applied above.

| Finding | Effect |
| --- | --- |
| Refuted: legacy CSE is `unsupported_formula_group` (the canonical reader refuses every `t="array"` as `array_spill_refused`; the group code is for `shared` and `dataTable`) | Non-goals and link 8 corrected |
| Refuted: the canonical reader "keys on all three signals in order" (`_spill_marker` uses them as alternative triggers for one refusal; no reader classifies; Rust never reads `cm`) | Link 2 classification rewritten as a rule the tree does not yet support |
| Refuted: the metadata names are "not cited anywhere" (`handbuilt.py:573-588` and two fixtures carry them, hand-authored or XlsxWriter) | Problem and link 2: the names exist as models, not captures; capture made the precondition |
| Refuted: the contract's edit drives spill growth (the anchor is `=SEQUENCE(3)`, constant) | Link 7 proposes a growth fixture whose shape depends on the edited input |
| Refuted: `B7#` is a parse error and stays `formula_unsupported` (Python `parse_error`, Rust `unsupported_formula`; neither emits `formula_unsupported`) | Non-goals, links 1, 4 and 8: parity fix to `unsupported_formula` |
| Refuted: every element is one of the nine errors (Rust lifting stored unsupported and parse errors as elements; the refuter's second half, that Python's lexer accepts `#N/A!`, did not hold: the alternative never matched) | Link 1 element rule; the lifting fix and a `#N/A!` parity pin in PR #111 |
| Refuted: expected spill values never come from an engine (the contract schema has no per-cell field; the harness compares shape from SDK reimport) | Link 7 rewritten: `expected_cells`, package-reader comparison, schema change first |
| Weakened: link 1 "held" (oversize-result codes differ; no parity test) | Link 1 size limits and a fourth parity test |
| Weakened: a 1×1 result "is a scalar" (an array in five places today; Excel's encoding unobserved) | Link 2 keeps 1×1 an array; open question 4 |
| Weakened: the engines own collision but never receive `merged` or `tables` | Link 3 owner: the pass with a session-supplied blocked set; open question 3 |
| Weakened: "no third limit" (output budget doubles retained elements; the oracle's 10,000-cell cap; `MAX_WORKBOOK_CELLS`) | Link 1 size limits |
| Weakened: link 5 does not say how spilled cells reach `changed` and `deltas` | Link 5: the session expands `changed` through the index; `deltas` records the anchor only |
| Weakened: representation (a) left value readers, `dependencies`, Base B's `cached_value` and provenance unanswered | Link 2 representation rewritten; "one field" is Base-A-shaped |
| Weakened: new codes in `inherited`; `unsupported_array_reference` uncatalogued; `preserved_only_edit` has no producer | Link 8 corrected |
| Weakened: the single-cycle harness cannot observe growth | Link 7: `cycles: 2` |
| Weakened: the Python toolkit writer's refusal omitted; the blank rule misstated and its Excel claim unobserved | Problem and link 1 corrected |
| Citation errors: `canonical_xlsx.py:153-161`; `protocol.ts` path; the Rust function ranges for FILTER/SORT/UNIQUE/RANDARRAY; `excel_oracle.py:693-703` as a value check; "not cited anywhere"; `formula_unsupported`; `toolkit.rs:24` as a single budget; RANDARRAY's `semantic_specs` entry | Corrected in place |
| Excel claims without an observation (17) | Each marked `[unobserved]` where it is stated; the capture listed as a dependency |
