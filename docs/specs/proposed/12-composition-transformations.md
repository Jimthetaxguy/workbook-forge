---
type: technical-specification
status: proposed
base: main 41f28d3
summary: Two explicit transformations between the grid and the workbook-free composition API. Extraction turns one output cell's dependency closure into a composition whose named inputs stand for chosen cells, with a provenance record keeping every original address, anchor and the model revision, and refuses with a complete blocker list when the closure cannot be expressed in the nine-function catalog. Placement renders a composition into worksheet formulas under explicit cell bindings whose anchors decide how one pattern copies across a range. Round-trip invariants are worked on the operating scenario with independent arithmetic. No schema or version change; the agent wire format is untouched.
---

# 12 — Composition transformations

## Problem

The composition API and the workbook formula API share an evaluator but have no public
conversion: `README.md:98-100` calls them separate APIs and `docs/primitives.md:166-171`
warns that matching scenario outputs do not make a composition exportable. The one
renderer, `expressions._render` (`python/workbook_forge/expressions.py:174`), raises on the
`input` kind compositions emit (`expressions.py:225-226`), on the `native_value` kind
(`python/workbook_forge/primitives.py:213-214,308`) and on numeric `literal` nodes
(`expressions.py:198-201`); Rust refuses `Expr::Native` likewise (`rust/src/toolkit.rs:741-746`).
Spec 08 records the missing edge (`docs/specs/proposed/08-meaning-ownership-map.md:106`),
"named inputs replace addresses" (`:44`), untyped versus typed bindings (`:50`) and the
renderer gap (`:147`). `docs/toolkit-delivery.md:164-166` defers "recovering native
compositions from imported formulas" to a later transformation with its own acceptance.

The constraint on any fix is the commentary's formula-level insight
(`vision-and-architecture-commentary.md`, section 3): `=A2*B2` and `=A3*B3` share a
pattern but not a reference context, and named inputs may replace coordinates only
through an explicit binding transformation. Contract C3 (section 6) and question 5
(section 12) ask for extraction, named parameters, placement and reference updates.

## Outcome

- A developer names an output cell and the cells that become parameters and receives
  either a composition (the unchanged `to_dict()` envelope, `primitives.py:352-356`) with
  an extraction record, or a refusal listing every blocker with its cell.
- The same developer places a composition into a cell or across a range with explicit
  bindings and receives formula text per cell, relative axes shifted and absolute axes
  fixed, ready for `WorkbookModel.apply` or the agent `edit` operation.
- Both languages pass one fixture: extraction then placement into the original cells
  reproduces the formulas under a defined normalisation, and the composition evaluated
  with the cells' values equals the grid's calculated output.

## Scope

- New `python/workbook_forge/compositions.py` and `rust/src/compositions.rs`, each using
  only its own evaluator, parser and renderer; functions `extract`, `place`, `render`.
  Input is the toolkit snapshot, where the dependency graph lives
  (`python/workbook_forge/python_engine.py:352-422`); after spec 09 step 0 the owner model
  enters through `to_toolkit`.
- One new companion schema `schemas/composition-extraction.v1.schema.json` for the
  record and the binding map; a new document, not a version bump.
- Rust visibility only: `CellReference::shifted` (`toolkit.rs:526`) and `render_expr`
  (`:734`) are private, so placement lives in `toolkit.rs` or both become `pub(crate)`.
- Shared fixture `fixtures/composition-cases.jsonl` with hand-derived expected values.

## Non-goals

- Algebraic recognition: extraction is structural, so the hand-written
  `SUM(volumes) * unit_price` of `docs/primitives.md:54-59` is not what extracting
  `Forecast!F2` yields (D5), though both evaluate to the same number.
- Array constants, spill, structured table references, external links, defined names as
  placement targets, new catalog functions, the source route (spec 11), the agent wire
  format, and any change to `schema_version` or `model_version`.

## Design

### D1. Vocabulary

*Composition* and *application binding* per `CONTEXT.md:43,64`. *Closure*: what
`_Graph.closure` returns (`python_engine.py:414-422`) and agent `read`/`explain` page
through (`python/workbook_forge/agent.py:452-457`; `docs/agent-protocol.md:67-70,89-94`).
*Anchor*: the per-axis absolute flags (`expressions.py:30-36`; `toolkit.rs:466-472`).
*Reference context*: the cell a formula is written at plus its anchors.

### D2. Extraction

`extract(snapshot, output, inputs, form)`: `output` is a `Sheet!A1` or a bound output
name; `inputs` maps parameter names to `Sheet!A1` cells or `Sheet!A1:B9` rectangles;
`form` is `set` or `inline`.

1. Build the graph (`python_engine.py:365-412`), take the output's closure (`:414-422`).
2. A chosen input becomes an `input` node; a formula cell is parsed with
   `parse_expression` under its sheet's defined-name scope (`expressions.py:137-152`) and
   lowered; any other closure cell is a blocker.
3. Lowering to the composition schema (`catalog/primitive-expression.schema.json:110-227`):

   | Evaluator node | Composition node | Otherwise |
   | --- | --- | --- |
   | `cell` (`__init__.py:779,817,825`) to a chosen cell | `input` | formula cell: its lowered tree (inline) or a named intermediate (set); literal, blank or `blocked_reason` cell: `invalid_input` / `unbound_reference` naming the cell |
   | `range` equal to a chosen rectangle | range-shaped `input` | flattening rule below |
   | `number` (token kept, `expressions.py:96-100`) | finite `literal` (`primitives.py:64-75`) | `invalid_expression` |
   | `literal` bool, text, error in `_ERRORS` (`primitives.py:26`) | `literal` | `#NULL!`, `#SPILL!` parse (`expressions.py:26`) but refuse: `unsupported_operation` |
   | `missing` | `omitted` | — |
   | `call` in `_FUNCTIONS` (`primitives.py:27`), arity checked (`:501-507`) | `call` | other function: `unsupported_operation` with cell and name; volatile (`volatile.py:29`) adds a second blocker |
   | `binary` in `_OPERATORS` (`primitives.py:28-32`) | `binary` | `^`, `&` (`expressions.py:25`): `unsupported_operation` |
   | `unary` `-x` | `binary("-", literal 0, x)`, listed under `rewrites` | `+x`, postfix `%`: `unsupported_operation` |

   **Flattening.** A `range` over formula cells is flattened into separate arguments
   only when the enclosing call is SUM, AVERAGE, MIN, MAX or COUNT (arity 1 to 255,
   `catalog/primitive-catalog.json`) and every cell in the range lowers to a tree rooted
   in an arithmetic `binary`, ROUND, ABS or one of those five. Such a tree yields a number
   or an error in Excel and in both evaluators, and the documented direct-argument versus
   reference difference concerns logicals and numeric text only (`docs/primitives.md:121-126`),
   so the value is unchanged. Any other range is `invalid_input` / `range_requires_input`.
   The record lists each flattened range so placement can fold it back.
4. **Inline form** substitutes each intermediate's tree at every reference. The schema has
   no sharing node, so a twice-referenced intermediate appears twice; the 1,024-node and
   depth-64 limits (`primitives.py:19-20`; `rust/src/primitives.rs:13-14`) are checked on the
   result and reported as `resource_limit` with counts. Inlining into an untaken IF branch
   preserves value: that cell never reaches the output either, and the composition does
   not evaluate the branch (`docs/primitives.md:117`). **Set form** keeps one composition
   per formula cell in the closure, each taking its direct references as inputs. The set
   serves an editor or planner; the inline form is one callable for a form or agent tool.
5. Input names default to the workbook binding name when the chosen cell is a bound input
   (`snapshot["inputs"]`), else the caller's; all match `_NAME` (`primitives.py:25`), at most 256 (`:21`).
6. **Extraction record** (companion, never inside the envelope): `source {kind,
   fingerprint, revision}` using `content_fingerprint` as the CLI does
   (`python/workbook_forge/cli.py:153-157`) plus the snapshot `revision`; `output {name?,
   cell}`; `inputs {name: {cell | range, anchors_as_written: [per occurrence], binding?}}`;
   `intermediates [{cell, formula, inlined_count}]`; `range_flattened`; `rewrites`. The
   `binding` object is the workbook's own binding for that cell
   (`{kind, required, min, max, choices}`, `python_engine.py:267-299`), copied unchanged.
7. **Refusal is complete, not first-fault**: `{blockers: [{code, cell, detail}]}` with no
   partial composition. Codes reuse `PrimitiveError` (`primitives.py:35-41`) and the
   graph's `ToolkitError` codes `unsupported_cell`, `parse_error`, `unsupported_formula`
   (`python_engine.py:331-348,372`); finer reasons (`unbound_reference`,
   `range_requires_input`, `volatile`) are `detail` values, not new codes.

Also not extractable: structured and external references (the parser's bracket detector,
`__init__.py:338-348`) and imported array, shared or data-table formulas (`blocked_reason`,
`python/workbook_forge/xlsx.py:379-387`, surfacing as `unsupported_cell`, `python_engine.py:371-373`).

### D3. Placement

`place(composition, bindings, result)`: `bindings` maps every input name to an A1 string
written with its anchors (`Assumptions!$B$1`, `B2`, `Data!$A$2:$A$10`); `result` is one
cell or a rectangle. It returns edit records `[{sheet, address, formula}]` and writes
nothing; the session's `apply` and the agent `edit` enforce cell guards
(`docs/agent-protocol.md:106-131`).

- Anchors are the developer's declaration of reference context; a missing `$` means
  relative as in Excel, nothing is defaulted or inferred.
- Shape first: a scalar input bound to a rectangle, a range input bound to a cell, or
  endpoints on different sheets (`expressions.py:323-324`) is `invalid_input` before any text.
- Single cell: substitute (D4) and render. Rectangle: the first cell is the anchor cell;
  the formula at offset `(dr, dc)` is the anchor formula with every bound reference
  `shifted(dr, dc)` (`expressions.py:74-83`; `toolkit.rs:526-549`), the rule `copy_formula`
  already applies (`expressions.py:230-232`; `toolkit.rs:833-841`;
  `python/workbook_forge/toolkit.py:254-272`); leaving the grid refuses
  (`expressions.py:42-43`; `toolkit.rs:540-545`).
- Folding: a range the record marks as flattened is emitted as a range again when the
  bound cells form one contiguous rectangle in argument order, else as the argument list.

### D4. Renderer

`_render` is unchanged. A substitution pass first builds an evaluator tree: `input` to a
`cell` or `range` node in the parser's tuple shape (`__init__.py:779,817,825`); scalar
`native_value` to a `number` node with the token `Expression.literal` would use
(`expressions.py:302-309`) or to `literal` for bool, text and error; `missing` to
`missing`; `call` and `binary` unchanged. A `native_value` holding `_Range` or `ArrayValue`
refuses `unsupported_operation`: there is no array-constant rendering, matching Rust's
`Expr::Native` refusal (`toolkit.rs:741-746`). Then `_render` runs with its 8,192
UTF-16-unit limit (`expressions.py:184-186`; `toolkit.rs:691-696`). Rust builds `Expr::Ref`,
`Expr::Range`, `Expr::Number(f64, token)` (`rust/src/lib.rs:239-251`) and calls `render_expr`.

An unbound `input` must still refuse: the pass raises `invalid_input` naming it, and
`_render`'s own refusal (`expressions.py:225-226`) remains the backstop. A bare name in a
worksheet is a defined-name lookup; absent such a name Excel gives `#NAME?` and the
evaluator's parser refuses it (`__init__.py:801-803`), a silent change of meaning. The rule
mirrors evaluation: every declared input bound, no more and no fewer
(`primitives.py:332-333`; `primitives.rs:500-505`).

### D5. Round-trip invariants, worked on the operating scenario

**Normalisation N**: `_render(parse_expression(f))` in Python, `Expression::parse(f)?.render()`
in Rust. Every binary is parenthesised (`expressions.py:216-217`), every sheet quoted
(`expressions.py:71`; `toolkit.rs:551-553`), functions uppercased, numeric tokens kept.
Equality under N is parse-tree equality.

- **I1 (text).** Set-form extraction, then placement of each composition into its own
  cell with the recorded anchors, equals the originals under N, flattened ranges folded.
- **I2 (value).** The inline composition evaluated with the chosen cells' values equals
  the grid's calculated output for the same snapshot, errors included.
- **I3 (forms agree).** The set form evaluated in dependency order equals the inline form.

Fixture `fixtures/operating-scenario.workbook.json`: `Forecast!B2:B4` = 100, 120, 150
(`:160-163,208-211,256-259`); `Assumptions!B1` = 20, `B2` = 8, `B3` = 1000 (`:71,91,102`).
Chosen inputs `q1,q2,q3`, `unit_price`, `unit_cost`, `fixed_cost` (names from the
bindings, `:13-26`).

*Revenue* `Forecast!F2` `=SUM(C2:C4)` (`:304`); `C2..C4` `=B2*Assumptions!$B$1` etc.
(`:170,218,266`). Set form: `c2 = q1*unit_price`, `c3`, `c4` likewise, `revenue = SUM(c2,
c3, c4)`, `C2:C4` flattened over three arithmetic intermediates. Inline:
`SUM(q1*unit_price, q2*unit_price, q3*unit_price)`. By hand: 100·20 = 2000, 120·20 = 2400,
150·20 = 3000, total **7400**; at unit_price 25: 2500 + 3000 + 3750 = **9250**.

*Profit* `Forecast!F3` `=SUM(E2:E4)` (`:315`); `E2` `=C2-D2-Assumptions!$B$3` (`:192`),
`D2` `=B2*Assumptions!$B$2` (`:181`), rows 3 and 4 alike; `C2-D2-$B$3` parses
left-associative. Inline: `SUM((q1*unit_price - q1*unit_cost) - fixed_cost, …)`, 25 nodes.
By hand: variable costs 800, 960, 1200; 2000−800−1000 = 200, 2400−960−1000 = 440,
3000−1200−1000 = 800; total **1440**; at 25: 700 + 1040 + 1550 = **3290**.

*Break-even* `Forecast!F4` `=IF(Assumptions!B1>Assumptions!B2,Assumptions!B3/(Assumptions!B1-Assumptions!B2),NA())`
(`:326`) is refused: `unsupported_operation {cell: Forecast!F4, function: NA}`; `NA` is in
the evaluator (`__init__.py:3904`) but not the catalog (`primitives.py:27`). The record
would show `anchors_as_written` for `unit_price` as `$B$1` from `C2` and `B1` from `F4`:
one input, two reference contexts. Were the function available, 1000/(20−8) = **83.333…**
and 1000/17 ≈ **58.82** at 25. `F5` `=1/0` (`:338`) extracts as a zero-input composition
evaluating to `#DIV/0!`; `J1` `=NOW()` (`:347`) reports two blockers; `J2` `=1+` (`:357`)
reports `parse_error`.

*Placement across a range.* `line_revenue = quantity*unit_price` at `Forecast!C2:C4` with
`quantity → B2`, `unit_price → Assumptions!$B$1` renders `=B2*'Assumptions'!$B$1`,
`=B3*'Assumptions'!$B$1`, `=B4*'Assumptions'!$B$1`, equal under N to the fixture's `C2..C4`.
Bound as `Assumptions!B1`, `C3` would read `'Assumptions'!B2`, the unit cost: why anchors
are never defaulted.

### D6. Typed bindings and the agent protocol

- **Spec 06.** The record's `binding` is a copy of the workbook's binding for the chosen
  cell, today the toolkit `{kind, required, min, max, choices}` (spec 08 `:50`); once
  specs 06 and 09 fix the owner's binding shape the record `$ref`s it. No third binding
  schema (spec 06 non-goal `:41-42`). The envelope cannot carry constraints: the schema is
  closed at every level (`primitive-expression.schema.json:20,143`).
- **Agent protocol.** Placement needs no operation: `edit {edits}` already takes formula
  strings (`docs/agent-protocol.md:106-109`); a host calls `place` and submits. Extraction
  would be a tenth read-only operation `extract {output, inputs, form}` returning
  `{composition, extraction}` or the blockers, over the closure `read`/`explain` already
  return (`agent.py:452-457`); that is a catalog change (`catalog/agent-operations.json` and
  its embedded Rust copy, `docs/agent-protocol.md:58-61`) for a later spec. The nine
  operations' wire format does not change here; the cross-language envelope is the
  existing one (Python `to_dict()` loads in Rust `from_dict`, `primitives.rs:421`).

## Schema / model-version impact

None to `schemas/workbook-model.v1.schema.json`, to `catalog/primitive-expression.schema.json`
(`schema_version` stays `const 1`, `:8-11`) or to `catalog/agent-operations.json`. The
record and binding map are one new document, `schemas/composition-extraction.v1.schema.json`;
by `docs/specs/model-versioning.md:7-8` a new file is neither a wire nor a meaning change
to an existing one. Adding `NA` would extend the function enum
(`primitive-expression.schema.json:152-162`), a wire change for that schema and James's call.

## Acceptance tests

```python
# python/tests/test_compositions.py
def test_extract_revenue_set_form_reproduces_closure_formulas_under_normalisation(): ...
def test_extract_revenue_inline_evaluates_7400_then_9250_at_price_25(): ...
def test_extract_profit_inline_evaluates_1440_then_3290_with_25_nodes(): ...
def test_extract_break_even_refuses_na_naming_cell_and_function(): ...
def test_extract_lists_every_blocker_not_the_first(): ...   # J1 volatile+unsupported, J2 parse_error
def test_extract_refuses_reference_outside_chosen_inputs_naming_cells(): ...
def test_extract_flattens_ranges_only_under_aggregates_over_arithmetic_cells(): ...
def test_extract_record_keeps_anchors_per_occurrence_and_copies_binding(): ...
def test_extract_inline_refuses_over_1024_nodes_with_counts(): ...
def test_place_pattern_across_range_shifts_relative_axes_only(): ...
def test_place_refuses_unbound_input_shape_mismatch_and_array_literals(): ...
def test_round_trip_into_same_cells_equals_original_under_normalisation(): ...
```

```rust
// rust/tests/compositions.rs — same names, snake_case, minus the test_ prefix, e.g.
fn extract_profit_inline_evaluates_1440_then_3290_with_25_nodes()
fn extract_break_even_refuses_na_naming_cell_and_function()
fn place_pattern_across_range_shifts_relative_axes_only()
fn round_trip_into_same_cells_equals_original_under_normalisation()
```

Both read `fixtures/composition-cases.jsonl`: workbook, output, inputs, form, and either
the expected composition with hand-derived values (above) or the expected blockers.
`docs/agent-protocol.md:181-183` (9250, 3290) agrees with these numbers but is not their source.

## Dependencies

Spec 08 (the edge `:106`, owners `:147`). Spec 09: this starts on the toolkit snapshot
now, with step 0's "decides nothing" property, and takes the owner model through
`to_toolkit` after step 0. Spec 06 only for the record's `binding` reference; until then
the toolkit binding is copied verbatim. Spec 11 consumes compositions and is not a
dependency. The result-range (spill) contract is untouched; arrays are refused here.

## Open questions for James

None block the core; three touch public vocabulary:

- May a placement binding target a defined name, rendering `=quantity*unit_price` with a
  names-table entry? Names are read-only today (`README.md:293-294`).
- Should `NA` join the catalog so break-even extracts? The scenario is the first concrete
  need (`docs/primitives.md:173-175`); it changes the primitive schema's enum.
- Is the unary-minus rewrite (`-x` as `0 - x`, recorded) acceptable, or a blocker like `%`?
