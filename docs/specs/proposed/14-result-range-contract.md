---
type: technical-specification
status: proposed — needs G-A if a model field is added
base: main 41f28d3
summary: Declare the result-range (spill placement) contract as a capability chain, from array semantics through anchor ownership, collisions, dependencies, atomic publication, projection and OOXML emission, to the Excel evidence the oracle must collect, so that red-flag Spec 2 and proposed spec 01 Phase B can close or be deliberately left open.
---

# 14 — Result-range (spill placement) contract

## Problem

Both engines calculate bounded arrays in memory and stop there. `ArrayValue` is a frozen row-major rectangle capped at 100,000 cells (`python/workbook_forge/__init__.py:40-58`; `rust/src/lib.rs:37,105-138`), `evaluate_result` says "worksheet spill placement is not modeled" (`__init__.py:5617`; `lib.rs:152`), and the toolkits carry the result as `CalculatedValue::Array { rows }` / `{"rows": [...]}` for the anchor cell only (`rust/src/toolkit.rs:234-252`; `python/workbook_forge/python_engine.py:486-487`). Nothing writes an array into neighbouring cells, and every serialized shape refuses one: canonical export and import raise `array_spill_refused` (`python/workbook_forge/canonical_xlsx.py:100-108,142-152`), the canonical calculators report `array results are unsupported` (`python/workbook_forge/model.py:879-885`), the Rust writer refuses an array cache (`rust/src/xlsx.rs:2030-2042`), the toolkit importers block the whole `ref` region of any `t="array"` formula (`python/workbook_forge/xlsx.py:379-409,211-225`; `rust/src/xlsx.rs:1106-1129,1155-1161,530-546`), the oracle marks the `array_spill` class `blocked` / `unsupported_capability` before Excel opens (`tools/excel_oracle.py:47-56,1275-1291,1446-1457`), the browser paints a `{rows}` value only when it is 1×1 (`web/univer-spike/src/adapter.ts:105-123,383-390`), and the TypeScript client declares `arrays: false` (`typescript/src/client.ts:31`). Spec 2 says this leaves the export gate open (`docs/specs/red-flags.md:129-134,150-153`); spec 01 lists the writer-side contract as a non-goal and the missing contract as its open question; `docs/specs/proposed/README.md` records it as the missing spec. Spec 09 section 1 notes that arrays are refused by both serialized forms, so an array-capable result is a contract change on either base (decision G-A).

## Outcome

- One declared chain, each link with an owner and the evidence that proves it, so the `array_spill` class can move from `blocked` to `observed` without a scalar reduction standing in for placement (`docs/excel-observations.md:97-105`).
- A document shape in which a spilled cell is derivable from its anchor, never a cell with a formula of its own, and in which a v1 reader fails closed on a v2 array.
- An honest split between what the engines decide (collision, growth, `#SPILL!`), what the session guarantees (all-or-nothing publication), and what only Excel can confirm.

## Scope

Links 1 to 8 below, the refusal codes, and the acceptance tests by name. The proposal covers dynamic-array anchors produced by FILTER, SORT, UNIQUE, SEQUENCE, RANDARRAY and elementwise lifting (`__init__.py:1186-1260,1297-1541,3030-3055`; `lib.rs:1428-1466,1533-1610`), across Python, Rust, the canonical and toolkit document shapes, the browser, the TypeScript client, and the oracle.

## Non-goals

- Legacy CSE arrays (`t="array"` with `ref`, no dynamic-array metadata) remain blocked regions / `unsupported_formula_group` as today (`xlsx.py:387-409`; `rust/src/xlsx.rs:1116-1129`; `canonical_xlsx.py:153-161`). Reading them as fixed-shape arrays is a later item.
- The spill-range operator (`B7#`) and implicit intersection (`@`). Both lexers treat `#` as the start of an error literal only (`__init__.py:176`; `lib.rs:376-395`), so `B7#` is a parse error today and stays refused (link 4).
- In-cell arrays, lists and nested arrays stored as rich values (`vm`), which are a different construct and stay refused with `in_cell_array` (`docs/in-cell-arrays.md`).
- Any `schema_version` or `model_version` bump before James's G-A decision (`CONTEXT.md:31`).

## Design

The contract is a chain. A link is "held" only when its owner's tests pass and its evidence row is filled; a later link cannot be claimed while an earlier one is open.

| Link | Owner | Today | Evidence that holds it |
| --- | --- | --- | --- |
| 1 Array semantics | both engines | held (`__init__.py:40-58`; `lib.rs:105-145`) | the two named tests in link 1; catalog `semantic_specs` for FILTER, SORT, UNIQUE, SEQUENCE |
| 2 Anchor and ownership | the contract (spec 09 §4) | refused on every serialized path | schema change under G-A; v1 fail-closed test; reimport classification tests in both languages |
| 3 Collisions and growth | the engines | not modeled | `#SPILL!` and growth tests in both languages; per-case Excel observation |
| 4 Dependency effects | the toolkit graphs | array dependency refused (`toolkit.rs:1452-1458`; `python_engine.py:479-480`) | element-read and `spill_cell_owned` tests |
| 5 Atomic publication | the session (spec 09 §10) | strict compare-and-swap exists (`browser_contract.rs:235-248`) | interleaving test with the range invariant; replay equality |
| 6 Projection and emission | browser, writers, envelope (spec 10) | 1×1 only (`adapter.ts:105-123`); writers refuse (`xlsx.rs:2030-2042`; `canonical_xlsx.py:100-108`) | browser tests; writer and reimport tests; metadata part observed in a capture |
| 7 Excel evidence | the oracle | class `blocked` (`excel_oracle.py:1446-1457`) | an `observed` receipt for the spill class with every declared cell compared |

### 1. Array semantics — owner: the two engines

- **Shape.** A result is a scalar or a non-empty rectangle with explicit rows × columns; ragged rows are invalid (`__init__.py:47-51`; `lib.rs:113-119`). `CONTEXT.md:80` defines `ArrayValue`; `:83` the shared `FormulaResult` contract.
- **Element types.** Each element is a v1 scalar: blank, number, text, boolean, or one of the nine errors including `#SPILL!` and `#CALC!` (`schemas/workbook-model.v1.schema.json` `$defs.value`, `$defs.error`; `lib.rs:79,96`; `__init__.py:176`). Blanks inside an array stay blanks and are not coerced to zero; only a scalar formula referring to an empty cell is written as numeric zero (`toolkit.rs:1470-1475`).
- **FILTER mask rule.** `include` must be one column of source height or one row of source width; two-dimensional masks are `#VALUE!`; no match with no `if_empty` is `#CALC!` (`__init__.py:1504-1529`; `CONTEXT.md:81`; catalog `semantic_specs.FILTER.shape_profile`). Microsoft's page is the catalog source `ms-filter`; the coercion of blanks and text to Boolean is a Workbook Forge profile (`catalog/formulas.json` `semantic_specs.FILTER.include_coercion_profile`).
- **Size limits.** 100,000 elements per array (`__init__.py:52-53`; `lib.rs:37,121-123`) and 100,000 materialized result cells per calculation across all anchors (`python_engine.py:38,498-504`; `toolkit.rs:24,1486-1507`, `resource_limit`). Spill placement adds no third limit; the grid bound (1,048,576 × 16,384) is enforced by the oracle's rectangle check today (`tools/excel_oracle.py:94-100`) and becomes an engine rule in link 3. Spec 03's per-cell memory bound applies unchanged.
- **Today.** Both engines hold this link; the shared tests are `test_array_results_are_explicit_and_array_dependencies_refused` (`python/tests/test_python_engine.py:80`) and `array_results_have_shape_without_implicit_spill_projection` (`toolkit.rs:2451`).

### 2. Anchor and ownership — owner: the contract (spec 09 section 4)

- **Anchor.** The formula cell is the anchor. Its **result range** is the rectangle whose top-left is the anchor and whose extent is the result's shape. A 1×1 result has a result range equal to the anchor and is a scalar for every later link.
- **Spilled cells** are the cells of the range other than the anchor. They are owned by the anchor, hold no formula and no authored value, and are never bound as inputs (the toolkit already refuses binding a formula or blocked cell, `python_engine.py:276-277`).
- **Representation.** Two candidates:
  - *(a) Anchor-owned result, derived index.* The anchor's calculated result widens from scalar to scalar-or-`{rows}`: canonical `formula.result` (`$defs.formula.result`, scalar today) and the toolkit's wire `{"rows": ...}` that already exists (`toolkit.rs:236-238`; `python_engine.py:487`). Spilled cells are **not serialized**; a validator derives an ownership index (spilled address → anchor) and refuses overlaps. Reimport places the spilled `<v>` caches into the anchor's imported cache (toolkit `cached_value`, canonical `Cell.value` with `provenance.origin: imported`; spec 09 section 4 reconciles these two into one field).
  - *(b) Per-cell marker.* Each spilled cell is serialized with `spilled_from: "B7"` and its cached value. Readers see values without consulting the anchor, at the cost of a second source of truth that can disagree with the anchor's shape.
  - **Recommendation: (a).** One widened field, no duplicated values, and `additionalProperties: false` plus the `oneOf` on `$defs.value` make a v1 reader refuse a v2 array document rather than misread it (`schemas/workbook-model.v1.schema.json`; `python_engine.py:235`; `toolkit.rs:312`, `deny_unknown_fields`). A workbook-level map from anchor to range adds nothing (a) does not derive.
- **Schema consequence.** Either candidate changes a serialized shape, so under spec 09 it is part of **G-A**: both version fields to 2, and the field lands on whichever base James picks. Until then the array result stays memory-only and every serialized path keeps today's refusal.
- **Reimport classification** (per formula cell): dynamic-array anchor = `t="array"` with `ref` **and** the dynamic-array cell metadata (`cm`); legacy CSE = `t="array"` with `ref` and no `cm` (stays blocked, Non-goals); plain cell = no `<f>`. Python's canonical reader already keys on all three signals in that order (`canonical_xlsx.py:540-552`); the Rust reader keys on `t` and `ref` only and never inspects `cm` (`rust/src/xlsx.rs:1116-1129`, no `cm` match in the file), so Rust must add the `cm` branch before classification is symmetric. That Excel marks a dynamic anchor with `cm` pointing at a metadata part is a format claim: [MS-XLSX] is the repository's format source (`catalog/formulas.json` `ms-xlsx-formula-spec`), but the exact part and attribute names are not cited anywhere in the repository yet — **needs an Excel observation** (a saved capture) before any reader or writer code.

### 3. Collisions and growth — owner: the engines, decided per pass

- **Blocked destinations.** A spilled cell's destination is blocked when it holds an authored value or a formula, lies in another anchor's result range, is a non-anchor cell of a merged range (spec 10 `merged`), lies in a table formula result range (`CONTEXT.md:76`; spec 10 `tables`), or lies outside the grid. A blocked range produces **`#SPILL!` at the anchor** and places nothing; `#SPILL!` is already in the error set (`$defs.error`; `lib.rs:79`). Microsoft's behaviour page is the catalog source `ms-dynamic-array-spill`; which of these cases Excel reports as `#SPILL!` versus an edge case it handles differently (merged cells, table regions, the worksheet edge) must be confirmed per case — **needs an Excel observation**.
- **Refused versus `#SPILL!`.** A collision found during calculation is a *value* (`#SPILL!`), never a refusal, so the pass completes and the document remains exportable. A refusal is reserved for *operations*: an edit targeting a spilled cell, a document whose declared ranges overlap, or a declared `ref` that holds a formula (link 8).
- **Growth and shrinkage.** The result range is recomputed every pass. Cells that leave the range are published as blank with no formula; cells that join must be free by the rule above, else the whole anchor is `#SPILL!` and its previous range is cleared. Within one pass anchors are placed in dependency order, ties by sheet order then row-major address; when two anchors contend, the later-placed one is `#SPILL!`. This tie rule is a Workbook Forge profile until an observation says what Excel does.
- **Grid.** `anchor_row + rows − 1 > 1,048,576` or `anchor_col + cols − 1 > 16,384` is `#SPILL!`; the oracle's `_spill_rectangle` already encodes the bound (`tools/excel_oracle.py:94-100`).

### 4. Dependency effects — owner: the toolkit graphs

- A formula that references a spilled cell depends on the **anchor**; the graph records the edge to the anchor and evaluation reads element (row offset, column offset) of the anchor's array. Today a spilled address resolves to a blank because no cell exists (`toolkit.rs:1439-1440`), and an array-valued dependency is refused with `unsupported_array_reference` (`toolkit.rs:1452-1458`; `python_engine.py:479-480`); that refusal is replaced by element reads once the index exists.
- A range reference overlapping a spill resolves each covered cell through the index; a reference to the anchor itself yields the whole array (today's behaviour for a referenced array value).
- Ownership is a derived index on the calculation snapshot (`CONTEXT.md:65`), rebuilt from the published results of the previous pass, so an edit to a spilled cell can be refused before calculation (`spill_cell_owned`, naming the anchor). The alternative, redirecting the edit to the anchor, is rejected: it would silently change a formula.
- `B7#` stays `formula_unsupported` ("spill range operator") in this release; adding it later is a lexer change in both engines and a dependency on the anchor's shape, not on the range cells.

### 5. Atomic revision publication — owner: the session (spec 09 section 10)

- A pass is identified by revision plus `CalcContext` or a pass counter (section 10 item 1); publication is compare-and-swap on (revision, base pass) (item 2), already the strict mode of the command envelope (`rust/src/browser_contract.rs:235-248`).
- The published set for an anchor is the anchor value **and** every cell of its new range **and** every cell of its previous range that left; all are stored in the same swap or none are. A pass that is superseded publishes nothing (item 7's `superseded` statuses).
- **Patches.** `changed` carries one `ChangedCell { sheet, address, value, formula }` per spilled cell with `formula: None` (`browser_contract.rs:311-316`), plus the anchor with its formula; a client that paints `changed` paints the whole range with no spill knowledge.
- **Event log.** `calc` events gain spilled keys in `deltas` as `[key, before, after]` triples, with `after: null` for cells that left (`rust/src/event_log.rs:17-19,227-256`); `intent` events are unchanged because spilled cells are never inputs; an intent targeting one is logged `rejected` with `spill_cell_owned` (`event_log.rs:14-16`). Replay re-derives placement, so the tie rule in link 3 must be deterministic.
- The interleaving test of section 10 item 9 gains an invariant: every published range equals the full calculation's range for its named pass.

### 6. Projection and emission — owner: browser and writers; keep rules from spec 10

- **Browser.** Replace the 1×1 projection (`adapter.ts:105-123`) with per-cell projection from `changed`; show the anchor's formula at the anchor only; a spilled cell shows its value, is not editable, and the notice names the anchor instead of "multi-cell array result is not shown" (`adapter.ts:385-387`). `protocol.ts:23-24` already types `{ rows }`. The client capability flips `arrays: false` to `true` only after this link's tests pass (`typescript/src/client.ts:31`).
- **OOXML emission.** Anchor: `<f t="array" ref="B7:B9">` plus the dynamic-array cell metadata and its metadata part; every spilled cell: a `<c>` with cached `<v>` and no `<f>`; `[Content_Types].xml` and the workbook relationships gain the metadata part. Exact names: **needs an Excel observation** (link 2). The preservation envelope must keep an imported `xl/metadata.xml` and merge rather than replace it (spec 10 `baseline.parts`, `inventory`); the writer must not emit into `merged` or `tables` ranges, which calculation already turned into `#SPILL!`.
- **Reimport** reads the anchor by classification (link 2), takes the spilled `<v>` values as the anchor's imported cache, and refuses a `<f>` inside the declared `ref` (`spill_range_conflict`). The canonical path's `array_spill_refused` and the Rust writer's refusal are retired for v2 documents only; v1 documents keep them.

### 7. Excel evidence — owner: the oracle (`tools/excel_oracle.py`)

The `array_spill` class must check, after full recalculation, save and reimport: the anchor formula text; the value of **every** declared `spill_cells` entry (`tools/excel_oracle.py:693-703`; `fixtures/excel-roundtrip-red-flags.contract.json` check `array-spill-placement`, `Red Flags!B7`, `=SEQUENCE(3)`, `B7:B9`); a blocked fixture whose anchor reads `#SPILL!` with an occupied cell in range; and growth after the contract's edit (`Assumptions!B1` to 25) drives a shape change, comparing cells that joined and cells that left. The scalar reduction `=SUM(SEQUENCE(3))` stays a separate class and proves nothing about placement (`docs/excel-observations.md:97-105`). Unknown without observation: how Excel stores the dynamic-array metadata; whether Excel rewrites `ref` when a spill grows; how a `#SPILL!` anchor is stored (single-cell `ref`?); merged-cell and table-region behaviour; whether a legacy CSE array with identical text differs only by the metadata. Expected values come from Microsoft examples or independent arithmetic, never from either engine.

### 8. Refusal codes

Reused: `array_spill_unsupported` for surfaces that have not adopted the contract (`catalog/refusal-codes.json`, phrases "array spill", "spill caches"); `array_spill_refused` on canonical v1 (`inherited`); `unsupported_formula_group` for legacy CSE; `formula_unsupported` for `B7#`; `resource_limit` for the 100,000-cell budget; `merged_cell`, `out_of_bounds`, `preserved_only_edit` unchanged for direct edits; `revision_conflict` for a stale publish. New, both engines and the browser, with the same message: `spill_cell_owned` (an edit targets a cell inside `{anchor}`'s result range) and `spill_range_conflict` (a document or package declares overlapping ranges, or a formula inside a declared `ref`). Add both to `catalog/refusal-codes.json` `inherited` and `docs/refusal-codes.md`.

## Schema / model-version impact

Link 2 widens one field (`formula.result` or its Base B counterpart) from scalar to scalar-or-`{rows}` and adds nothing else to the document; that is a G-A change and both version fields move to 2 together with spec 09. No change to `$defs.error` (`#SPILL!` exists) or `$defs.diagnostic` (`#SPILL!` is a value). The calculation report's `values` already carries `{rows}` for anchors (`toolkit.rs:1205-1206`). The round-trip contract schema needs a `#SPILL!`-at-anchor expectation and a growth check type; `ROUNDTRIP_RESULT_TYPES` already includes `error` and `array` (`tools/excel_oracle.py:45`).

## Acceptance tests

```python
# python/tests/test_result_range.py
def test_result_range_is_anchor_plus_shape_and_one_by_one_is_scalar(): ...
def test_spilled_cells_hold_no_formula_and_cannot_be_bound(): ...
def test_occupied_destination_yields_spill_error_at_anchor_and_places_nothing(): ...
def test_growth_clears_cells_that_leave_and_requires_cells_that_join_free(): ...
def test_two_anchors_contending_follow_the_declared_order_rule(): ...
def test_reference_to_spilled_cell_depends_on_anchor_and_reads_its_element(): ...
def test_edit_into_spilled_cell_is_refused_spill_cell_owned_naming_anchor(): ...
def test_spill_range_operator_is_refused_formula_unsupported(): ...
def test_v1_reader_fails_closed_on_v2_array_result(): ...
def test_canonical_reimport_classifies_dynamic_anchor_legacy_cse_and_plain_cell(): ...
def test_reimport_refuses_formula_inside_declared_ref_spill_range_conflict(): ...
@pytest.mark.oracle_optional
def test_array_spill_class_checks_every_declared_cell_after_excel_recalc(): ...
@pytest.mark.oracle_optional
def test_blocked_spill_fixture_reads_spill_error_at_anchor_in_excel(): ...
@pytest.mark.oracle_optional
def test_spill_growth_after_input_edit_matches_joined_and_left_cells(): ...
```

```rust
// rust/tests/result_range.rs
fn result_range_is_anchor_plus_shape_and_one_by_one_is_scalar() {}
fn occupied_destination_yields_spill_error_at_anchor_and_places_nothing() {}
fn growth_clears_left_cells_and_requires_joined_cells_free() {}
fn reference_to_spilled_cell_depends_on_anchor() {}
fn edit_into_spilled_cell_is_refused_spill_cell_owned() {}
fn publication_is_all_cells_or_none_under_compare_and_swap() {}   // section 10 item 9 interleaving
fn calc_event_deltas_include_spilled_and_left_cells_and_replay_matches() {}
fn xlsx_reader_classifies_dynamic_anchor_by_metadata_and_blocks_legacy_cse() {}
fn xlsx_writer_emits_anchor_metadata_and_cached_values_in_every_spilled_cell() {}
```

Browser (`web/forge-engine/test/engine.node.test.mjs`, `web/univer-spike/tests/acceptance.spec.ts`): `changed carries every spilled cell with formula null`; `grid paints every spilled cell and shows the formula at the anchor only`; `typing into a spilled cell is refused with a notice naming the anchor`; `CLIENT_CAPABILITIES.arrays flips only with the projection tests`. Harness (`python/tests/test_excel_oracle.py`): `array_spill contract accepts a #SPILL! anchor expectation and a growth check`.

## Dependencies

- Spec 09 section 4 (the owner of `formula.result` / `cached_value`; G-A) and section 10 (pass identity, compare-and-swap publication, delta since a named base, one funnel, the interleaving test). Links 2 and 5 cannot be coded before the base is chosen.
- Spec 10 (`merged`, `tables`, `insertion_bound`, `baseline.parts` for the metadata part; the move of merged-cell refusal to `apply`).
- Spec 01 Phase B (the `oracle_optional` / `oracle_required` marks and the `array_spill` fixture; this spec is what lets that class leave `blocked`).
- Spec 03 (the per-cell memory bound the 100,000-cell budget sits under).
- Ordering: link 1 is held today → G-A → links 2, 4 (engines, documents) → link 5 (session) → link 6 (browser, writers) → link 7 (Excel) → Spec 2 close. Link 3 ships with link 2 since a reader must already know what `#SPILL!` at an anchor means.

## Open questions for James

- **G-A:** which base carries the widened result field, and whether both version fields move with spec 09 or on their own.
- **Scope of the first release:** does the toolkit's first release include spill at all? The vision treats it as a capability rather than a gate; if it ships refused, Spec 2's spill class stays open by decision and spec 01's question (can 07 ship with spill open) is answered yes.
- **Spill-range operator:** in or out; this proposal says out.
- **Legacy CSE arrays:** keep blocked, or read as fixed-shape arrays once the metadata distinction is observed.
- **Tie rule** for two anchors contending (link 3): accept the dependency-order profile, or wait for an observation of Excel's order.
