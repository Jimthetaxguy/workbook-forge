---
type: technical-specification
status: proposed
base: main 41f28d3
summary: Two explicit transformations between the grid and the workbook-free composition API. Extraction turns one output cell's input-bounded dependency closure into a composition whose named inputs stand for chosen cells, with a provenance record keeping every original address, anchor and qualification as written and the model revision, and refuses with a complete blocker list when the closure cannot be expressed in the nine-function catalog or would change value under the composition path's documented profile. Placement renders a composition into worksheet formulas under explicit per-occurrence cell bindings whose anchors decide how one pattern copies across a range, folding recorded rewrites back. Round-trip invariants are stated under a normalisation that canonicalises number tokens and worked on the operating scenario with independent arithmetic. No schema or version change; the agent wire format is untouched.
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

The constraint on any fix, taken as this spec's premise: `=A2*B2` and `=A3*B3` share a
pattern but not a reference context, and named inputs may replace coordinates only
through an explicit binding transformation. A second constraint the first draft missed:
the composition path and the worksheet path evaluate under **documented different
profiles** in both engines. Aggregates ignore boolean and text values given as direct
scalar arguments on the composition path (`docs/primitives.md:121-123`;
`python/workbook_forge/__init__.py:2940-2960` wraps every scalar argument as a 1×1 range
when `cells` is `None`; Rust `primitive_value_profile`, `rust/src/lib.rs:3655-3660`), and
comparisons use the primitive profile (`CONTEXT.md:82`; Rust `lib.rs:1096`) rather than the
worksheet's typed-rank profile (`__init__.py:985-986,1107-1145`; `lib.rs:2105,2143`). Both
engines agree with each other on every such case, so a dual-engine check cannot see it;
the transformation itself has to.

## Outcome

- A developer names an output cell and the cells that become parameters and receives
  either a composition (the unchanged `to_dict()` envelope, `primitives.py:352-356`) with
  an extraction record, or a refusal listing every blocker with its cell.
- The same developer places a composition into a cell or across a range with explicit
  bindings and receives formula text per cell, relative axes shifted and absolute axes
  fixed, ready for `WorkbookModel.apply` or the agent `edit` operation.
- Both languages pass one fixture: extraction then placement into the original cells
  reproduces the formulas under a defined normalisation, and the composition evaluated
  with the cells' values equals the grid's calculated output, for every formula the
  extraction accepts.

## Scope

- New `python/workbook_forge/compositions.py` and `rust/src/compositions.rs`, each using
  only its own evaluator, parser and renderer; functions `extract`, `place`, `render`.
  Input is the toolkit snapshot (`python/workbook_forge/python_engine.py:202`); the
  dependency graph is built on demand from it (`_Graph.build`, `:365-412`) and is not
  part of the snapshot. After spec 09 step 0 the owner model enters through `to_toolkit`.
- A new **single-source, input-bounded closure**: `_Graph.closure` (`:414-422`) takes no
  output and seeds from every bound output, and the agent's per-output loop
  (`python/workbook_forge/agent.py:452-457`) walks `inspect()["dependencies"]` without
  stopping at chosen inputs; neither can be reused as is.
- One new companion schema `schemas/composition-extraction.v1.schema.json` for the
  record and the binding map; a new document for a new artifact. The workbook model's
  fields and versions do not move.
- Rust visibility only: `CellReference::shifted` (`toolkit.rs:526`) and `render_expr`
  (`:734`) are private, so placement lives in `toolkit.rs` or both become `pub(crate)`.
- Shared fixture `fixtures/composition-cases.jsonl` with hand-derived expected values,
  including cases that exercise the profile differences above.

## Non-goals

- Algebraic recognition: extraction is structural, so the hand-written
  `SUM(volumes) * unit_price` of `docs/primitives.md:54-59` is not what extracting
  `Forecast!F2` yields (D5), though both evaluate to the same number.
- Array constants, spill, structured table references, external links, defined names in
  extracted formulas or as placement targets, new catalog functions, the source route
  (spec 11), the agent wire format, and any change to `schema_version` or `model_version`.

## Design

### D1. Vocabulary

*Composition* and *application binding* per `CONTEXT.md:43,64`. *Closure*: the set of
cells reachable from one output through formula references, stopping at chosen inputs
(new; see Scope). *Anchor*: the per-axis absolute flags (`expressions.py:30-36`;
`toolkit.rs:466-472`). *Qualification*: whether a reference was written with a sheet
prefix. *Reference context*: the cell a formula is written at plus its anchors and
qualification.

### D2. Extraction

`extract(snapshot, output, inputs, form)`: `output` is a `Sheet!A1` or a bound output
name; `inputs` maps parameter names to `Sheet!A1` cells or `Sheet!A1:B9` rectangles;
`form` is `set` or `inline`.

1. Build the graph (`python_engine.py:365-412`), then walk from the one output through
   each formula's references, stopping at any cell or rectangle in `inputs`. A chosen
   input that is itself a formula cell is still a leaf. This is the input-bounded
   closure; it is not `_Graph.closure`, which unions every bound output and would refuse
   every extraction on the operating scenario through its `F4`, `J1` and `J2` outputs.
2. A chosen input becomes an `input` node; a formula cell is parsed with
   `parse_expression` (`expressions.py:137-152`) and lowered; a formula that uses a defined
   name is a blocker (`unsupported_operation`, detail `defined_name`), because the
   normalisation in D5 has no name scope and names are read-only today; any other
   closure cell is a blocker.
3. Lowering to the composition schema (`catalog/primitive-expression.schema.json:110-227`):

   | Evaluator node | Composition node | Otherwise |
   | --- | --- | --- |
   | `cell` (`__init__.py:779,817,825`) to a chosen cell | `input` | formula cell: its lowered tree (inline) or a named intermediate (set); literal, blank or `blocked_reason` cell: `invalid_input` / `unbound_reference` naming the cell |
   | `range` equal to a chosen rectangle | range-shaped `input` | flattening rule below |
   | `number` (token kept by the parser, `expressions.py:96-100`) | finite `literal`; the composition holds a float (`primitives.py:64-70`; Rust `Expr::Native`, `primitives.rs:316`), so the token is lost and D5's normalisation compares numbers by value | `invalid_expression` |
   | `literal` bool or text **as a direct argument of SUM, AVERAGE or COUNT** | blocker: `unsupported_operation`, detail `profile_difference` (the composition path ignores it, the grid counts it) | elsewhere: `literal` |
   | `literal` error in `_ERRORS` (`primitives.py:26`) | `literal` | `#NULL!`, `#SPILL!` parse (`expressions.py:26`) but refuse: `unsupported_operation` |
   | `missing` | `omitted` | — |
   | `call` in `_FUNCTIONS` (`primitives.py:27`), arity checked (`:501-507`) | `call` | other function: `unsupported_operation` with cell and name; volatile (`volatile.py:29`) adds a second blocker |
   | `binary` arithmetic in `_OPERATORS` (`primitives.py:28-32`) | `binary` | `^`, `&` (`expressions.py:25`): `unsupported_operation` |
   | `binary` comparison | `binary` **only when both operands are provably numeric**: number literals, arithmetic results, or inputs whose workbook binding `kind` is `number`; otherwise blocker `unsupported_operation`, detail `profile_difference` (worksheet typed-rank comparison versus the primitive profile) | — |
   | `unary` `-x` | `binary("-", literal 0, x)`, listed under `rewrites` with the node path; placement folds it back (D3) | `+x`, postfix `%`: `unsupported_operation` |

   **Flattening.** A `range` over formula cells is flattened into separate arguments
   only when the enclosing call is SUM, AVERAGE, MIN, MAX or COUNT (arity 1 to 255,
   `catalog/primitive-catalog.json`), every cell in the range lowers to a tree rooted in
   an arithmetic `binary`, ROUND, ABS or one of those five, **and no range-shaped input
   appears beneath that tree** (a range-shaped input under `*` yields an array,
   `__init__.py:953-960`, `primitives.py:266-269`, and `SUM(array)` sums every element
   while the grid cell holds one value). Such a scalar tree yields a number or an error,
   and both engines treat a number or an error identically in range and direct position
   under those five functions (`__init__.py:4052-4106,4128-4134`; `lib.rs:3650-3700,
   3870-3890`). A range of more than 255 cells cannot be flattened and is
   `resource_limit` with the count. Any other range is `invalid_input` /
   `range_requires_input`. The record lists each flattened range so placement can fold it back.
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
   cell}`; `inputs {name: {cell | range, occurrences: [{at, anchors, qualified}],
   binding?}}` recording, per occurrence, the anchors and whether the reference was
   sheet-qualified as written; `intermediates [{cell, formula, inlined_count}]`;
   `range_flattened`; `rewrites [{path, from: "unary-", to: "0-"}]`. The `binding` object
   is the workbook's own binding for that cell (`{kind, required, min, max, choices}`,
   `python_engine.py:267-299`), copied unchanged when present; a workbook that entered
   through spec 09's step 0 without caller-supplied kinds has no bindings, and the record
   says so rather than inventing one.
7. **Refusal is complete, not first-fault**: `{blockers: [{code, cell, detail}]}` with no
   partial composition. Codes reuse `PrimitiveError` (`primitives.py:35-41`) and the
   graph's `ToolkitError` codes `unsupported_cell`, `parse_error`, `unsupported_formula`,
   `invalid_reference`, `resource_limit`, `name_cycle` (`python_engine.py:330-348,372`);
   finer reasons (`unbound_reference`, `range_requires_input`, `volatile`,
   `profile_difference`, `defined_name`) are `detail` values, not new codes. The
   `PrimitiveError` codes and `unsupported_cell` are not in
   `catalog/refusal-codes.json`'s inherited list today; adding them there is a one-line
   change this spec asks for.

Also not extractable: structured and external references (the parser's bracket detector,
`__init__.py:338-348`) and imported array, shared or data-table formulas (`blocked_reason`,
`python/workbook_forge/xlsx.py:379-387`, surfacing as `unsupported_cell`, `python_engine.py:371-373`).

### D3. Placement

`place(composition, bindings, result)`: `bindings` maps every input name to either one
A1 string written with its anchors (`Assumptions!$B$1`, `B2`, `Data!$A$2:$A$10`) for a new
placement, or, when round-tripping from a record, the record's per-occurrence
`occurrences` list so that one input written as `$B$1` in one cell and `B1` in another
returns to both forms; `result` is one cell or a rectangle. It returns edit records
`[{sheet, address, formula}]` and writes nothing; the session's `apply` and the agent
`edit` enforce cell guards (`docs/agent-protocol.md:106-131`).

- Anchors and qualification are the developer's declaration of reference context; a
  missing `$` means relative as in Excel, nothing is defaulted or inferred. A same-sheet
  reference is rendered unqualified when the record or binding says it was written so;
  `_render` otherwise qualifies every reference that carries a sheet (`expressions.py:70-71`;
  `toolkit.rs:520-524`), which would change the parse tree under N.
- A binding string that is a 3D reference, a whole-column or whole-row range, or an
  external reference is `invalid_input` before any text; the parser refuses those forms
  (`__init__.py:205-213,719-748`) and placement does not widen them.
- Shape first: a scalar input bound to a rectangle, a range input bound to a cell, or
  endpoints on different sheets (`expressions.py:323-324`) is `invalid_input` before any text.
- Single cell: fold recorded `rewrites` back (`0 - x` to `-x`), substitute (D4) and render.
  Rectangle: the first cell is the anchor cell; the formula at offset `(dr, dc)` is the
  anchor formula with every bound reference `shifted(dr, dc)` (`expressions.py:74-83`;
  `toolkit.rs:526-549`), the rule `copy_formula` already applies (`expressions.py:230-232`;
  `toolkit.rs:833-841`; `python/workbook_forge/toolkit.py:254-272`); leaving the grid refuses
  (`expressions.py:42-43`; `toolkit.rs:540-545`).
- Folding: a range the record marks as flattened is emitted as a range again when the
  bound cells form one contiguous rectangle in argument order, else as the argument list.

### D4. Renderer

`_render` is unchanged. A substitution pass first builds an evaluator tree: `input` to a
`cell` or `range` node in the parser's tuple shape (`__init__.py:779,817,825`) with the
occurrence's anchors and qualification; scalar `native_value` to a `number` node with the
token `str(value)` produces (`expressions.py:302-309`) or to `literal` for bool, text and
error; `missing` to `missing`; `call` and `binary` unchanged; a recorded unary rewrite
back to `unary`. A `native_value` holding `_Range` or `ArrayValue` refuses
`unsupported_operation`: there is no array-constant rendering, matching Rust's
`Expr::Native` refusal (`toolkit.rs:741-746`). Then `_render` runs with its 8,192
UTF-16-unit limit (`expressions.py:184-186`; `toolkit.rs:691-696`). Rust builds `Expr::Ref`,
`Expr::Range`, `Expr::Number(f64, token)` (`rust/src/lib.rs:239-251`) and calls `render_expr`;
Rust compositions compile literals to `Expr::Native` (`primitives.rs:316-318`), so the
substitution pass is required on the Rust side too.

An unbound `input` must still refuse: the pass raises `invalid_input` naming it, and
`_render`'s own refusal (`expressions.py:225-226`) remains the backstop. A bare name in a
worksheet is a defined-name lookup; absent such a name Excel gives `#NAME?` and the
evaluator's parser resolves or refuses it (`__init__.py:801-803`), a silent change of
meaning. The rule mirrors evaluation: every declared input bound, no more and no fewer
(`primitives.py:332-333`; `primitives.rs:500-505`).

### D5. Round-trip invariants, worked on the operating scenario

**Normalisation N**: `_render(parse_expression(f))` in Python, `Expression::parse(f)?.render()`
in Rust, then parse-tree equality **with number nodes compared by value, not token**
(the composition keeps floats, so `1` and `1.0` are one number) and with recorded
`rewrites` folded back before comparison. Every binary is parenthesised
(`expressions.py:216-217`), every qualified sheet quoted (`expressions.py:71`;
`toolkit.rs:551-553`), functions uppercased.

- **I1 (text).** Set-form extraction, then placement of each composition into its own
  cell with the recorded occurrences, equals the originals under N, flattened ranges
  folded, for every formula extraction accepts.
- **I2 (value).** The inline composition evaluated with the chosen cells' values equals
  the grid's calculated output for the same snapshot under `==`, errors included.
  Signed zero is the one residue: the grid's `-x` at `x = 0` is `-0.0` by contract
  (`__init__.py:2731-2735`; `lib.rs:1070,8912-8914`; `test_source_stage6_review.py:152-170`)
  and `0 - 0` is `+0.0`; they are equal under `==` and differ bit-wise. Extraction records
  the rewrite and placement folds it back, so the grid never sees the difference; the
  composition's own evaluation may.
- **I3 (forms agree).** The set form evaluated in dependency order equals the inline form.

The blockers in D2 are what make I1 and I2 statements about the transformation rather
than about this fixture: direct boolean or text literals under aggregates and comparisons
on possibly mixed operands are refused precisely because both invariants would fail for
them under the composition path's profile, in both engines alike.

Fixture `fixtures/operating-scenario.workbook.json`: `Forecast!B2:B4` = 100, 120, 150
(`:160-163,208-211,256-259`); `Assumptions!B1` = 20, `B2` = 8, `B3` = 1000 (`:71,91,102`).
Chosen inputs `q1, q2, q3` (caller names for `Forecast!B2:B4`) and `unit_price`,
`unit_cost`, `fixed_cost` (the workbook's binding names, `:13-26`).

*Revenue* `Forecast!F2` `=SUM(C2:C4)` (`:304`); `C2..C4` `=B2*Assumptions!$B$1` etc.
(`:170,218,266`). Set form: `c2 = q1*unit_price`, `c3`, `c4` likewise, `revenue = SUM(c2,
c3, c4)`, `C2:C4` flattened over three arithmetic intermediates. Inline:
`SUM(q1*unit_price, q2*unit_price, q3*unit_price)`, 10 nodes. By hand: 100·20 = 2000,
120·20 = 2400, 150·20 = 3000, total **7400**; at unit_price 25: 2500 + 3000 + 3750 = **9250**.

*Profit* `Forecast!F3` `=SUM(E2:E4)` (`:315`); `E2` `=C2-D2-Assumptions!$B$3` (`:192`),
`D2` `=B2*Assumptions!$B$2` (`:181`), rows 3 and 4 alike; `C2-D2-$B$3` parses
left-associative. Inline: `SUM((q1*unit_price - q1*unit_cost) - fixed_cost, …)`, **28
nodes** under the evaluator's count (`primitives.py:226-235` counts every node including
each input occurrence: 4 binaries and 5 leaves per line, three lines, plus SUM). By hand:
variable costs 800, 960, 1200; 2000−800−1000 = 200, 2400−960−1000 = 440,
3000−1200−1000 = 800; total **1440**; at 25: 700 + 1040 + 1550 = **3290**.

*Break-even* `Forecast!F4` `=IF(Assumptions!B1>Assumptions!B2,Assumptions!B3/(Assumptions!B1-Assumptions!B2),NA())`
(`:326`) is refused with two blockers: `unsupported_operation {cell: Forecast!F4,
function: NA}` (`NA` is in the evaluator, `__init__.py:3904`, but not the catalog,
`primitives.py:27`) and the comparison `B1>B2` whose operands are inputs without a
`number` binding kind unless the caller supplies them (`profile_difference`). The record
would show `unit_price` occurring as `$B$1` from `C2` and as `B1` from `F4`: one input, two
reference contexts, which is why occurrences are recorded per cell. Were the function
available and the kinds bound, 1000/(20−8) = **83.333…** and 1000/17 ≈ **58.82** at 25.
`F5` `=1/0` (`:337`) extracts as a zero-input composition evaluating to `#DIV/0!` and
places back as `=(1.0/0.0)`, equal to `=1/0` under N; `J1` `=NOW()` (`:347`) reports two
blockers; `J2` `=1+` (`:357`) reports `parse_error`.

*Placement across a range.* `line_revenue = quantity*unit_price` at `Forecast!C2:C4` with
`quantity → B2` (unqualified), `unit_price → Assumptions!$B$1` renders
`=(B2*'Assumptions'!$B$1)`, `=(B3*'Assumptions'!$B$1)`, `=(B4*'Assumptions'!$B$1)`, equal
under N to the fixture's `C2..C4`. Bound as `Assumptions!B1`, `C3` would read
`'Assumptions'!B2`, the unit cost: why anchors are never defaulted.

### D6. Typed bindings and the agent protocol

- **Spec 06.** The record's `binding` is a copy of the workbook's binding for the chosen
  cell, today the toolkit `{kind, required, min, max, choices}` (spec 08 `:50`); once
  specs 06 and 09 fix the owner's binding shape the record `$ref`s it. No third binding
  schema (spec 06 non-goal `:41-42`). The envelope cannot carry constraints: the schema is
  closed at every level (`primitive-expression.schema.json:20,143`). Binding kinds also
  feed D2's comparison rule: an input of kind `number` makes a comparison on it extractable.
- **Agent protocol.** Placement needs no operation: `edit {edits}` already takes formula
  strings (`docs/agent-protocol.md:106-109`); a host calls `place` and submits. Extraction
  would be a tenth read-only operation `extract {output, inputs, form}` returning
  `{composition, extraction}` or the blockers; that is a catalog change
  (`catalog/agent-operations.json`, its embedded Rust copy, `docs/agent-protocol.md:58-61`)
  **and** a change to the TypeScript contract, whose `CATALOG_SHA256` and `OPERATIONS` are
  generated from the catalog bytes (`tools/generate_ts_contract.py:15-26`;
  `python/tests/test_typescript_sdk.py:87`), for a later spec. The nine operations' wire
  format does not change here; the cross-language envelope is the existing one (Python
  `to_dict()` loads in Rust `from_dict`, `primitives.rs:421`).

## Schema / model-version impact

None to `schemas/workbook-model.v1.schema.json`, to `catalog/primitive-expression.schema.json`
(`schema_version` stays `const 1`, `:8-11`) or to `catalog/agent-operations.json`. The
record and binding map are one new document, `schemas/composition-extraction.v1.schema.json`,
for a new artifact; the workbook model's two version fields do not move for it. Adding
`NA` would extend the function enum (`primitive-expression.schema.json:152-162`), a wire
change for that schema and James's call.

## Acceptance tests

```python
# python/tests/test_compositions.py
def test_extract_uses_an_input_bounded_single_output_closure(): ...   # F4/J1/J2 do not block revenue
def test_extract_revenue_set_form_reproduces_closure_formulas_under_normalisation(): ...
def test_extract_revenue_inline_evaluates_7400_then_9250_at_price_25(): ...
def test_extract_profit_inline_evaluates_1440_then_3290_with_28_nodes(): ...
def test_extract_break_even_refuses_na_and_untyped_comparison_naming_both(): ...
def test_extract_lists_every_blocker_not_the_first(): ...   # J1 volatile+unsupported, J2 parse_error
def test_extract_refuses_reference_outside_chosen_inputs_naming_cells(): ...
def test_extract_refuses_direct_bool_or_text_literal_under_aggregates(): ...   # profile_difference
def test_extract_refuses_comparison_on_non_numeric_operands(): ...             # profile_difference
def test_extract_allows_comparison_on_number_kind_inputs(): ...
def test_extract_refuses_formula_using_a_defined_name(): ...
def test_extract_flattens_ranges_only_under_aggregates_over_scalar_arithmetic_cells(): ...
def test_extract_refuses_flattening_over_a_range_shaped_input(): ...
def test_extract_record_keeps_anchors_and_qualification_per_occurrence_and_copies_binding(): ...
def test_extract_inline_refuses_over_1024_nodes_with_counts(): ...
def test_place_pattern_across_range_shifts_relative_axes_only(): ...
def test_place_same_sheet_reference_renders_unqualified_as_written(): ...
def test_place_folds_recorded_unary_rewrite_back(): ...
def test_place_refuses_unbound_input_shape_mismatch_3d_and_array_literals(): ...
def test_round_trip_into_same_cells_equals_original_under_normalisation(): ...   # number tokens by value
def test_division_literal_round_trips_by_value(): ...   # =1/0 places as =(1.0/0.0)
```

```rust
// rust/tests/compositions.rs — same names, snake_case, minus the test_ prefix, e.g.
fn extract_profit_inline_evaluates_1440_then_3290_with_28_nodes()
fn extract_refuses_direct_bool_or_text_literal_under_aggregates()
fn extract_refuses_comparison_on_non_numeric_operands()
fn place_pattern_across_range_shifts_relative_axes_only()
fn round_trip_into_same_cells_equals_original_under_normalisation()
```

Both read `fixtures/composition-cases.jsonl`: workbook, output, inputs, form, and either
the expected composition with hand-derived values (above) or the expected blockers; the
fixture includes mixed-type comparison and direct-literal aggregate cases so the profile
blockers are exercised. `docs/agent-protocol.md:181-183` (9250, 3290) agrees with these
numbers but is not their source.

## Dependencies

Spec 08 (the edge `:106`, owners `:147`). Spec 09: this starts on the toolkit snapshot
now and takes the owner model through `to_toolkit` after step 0; a workbook converted
without caller-supplied binding kinds has no bindings, so its comparisons are not
extractable until spec 06 or the caller supplies kinds. Spec 06 for the record's
`binding` reference and for the comparison rule; until then the toolkit binding is copied
verbatim. Spec 11 consumes compositions and is not a dependency. The result-range (spill)
contract is untouched; arrays are refused here.

## Open questions for James

None block the core; three touch public vocabulary:

- May a placement binding target a defined name, rendering `=quantity*unit_price` with a
  names-table entry? Names are read-only today (`README.md:293-294`).
- Should `NA` join the catalog so break-even extracts? The scenario is the first concrete
  need (`docs/primitives.md:173-175`); it changes the primitive schema's enum.
- Should the profile differences (direct literals under aggregates, mixed-type
  comparisons) remain blockers, or be accepted deviations recorded in the extraction
  record? Blockers are recommended: both engines agree with each other on them, so an
  accepted deviation would be invisible to every existing test.

## Refutation record

An independent refuter read the first draft under the `contract` and `parity` lenses:
5 refuted, 7 weakened, 8 confirmed, 8 citation errors. All applied above.

| Finding | Effect |
| --- | --- |
| Refuted: extraction reuses `_Graph.closure` (it takes no output, unions every bound output, and would refuse every extraction on the fixture; neither it nor the agent's loop stops at chosen inputs) | Scope and D2 define a new single-source, input-bounded closure |
| Refuted: profit inline is 25 nodes (28 under the evaluator's count; revenue 10) | Corrected |
| Refuted: I1 holds with number tokens kept (compositions hold floats; the unary rewrite leaves a different tree) | N compares numbers by value; placement folds recorded rewrites back |
| Refuted: I2 holds for direct boolean or text literals under aggregates (the composition path ignores them in both engines) | Such literals are blockers (`profile_difference`) |
| Refuted: I2 holds for comparisons (the composition path uses the primitive profile, the grid the typed-rank profile, in both engines) | Comparisons extract only on provably numeric operands |
| Weakened: flattening needs "no range-shaped input beneath"; unary minus differs at signed zero bit-wise; anchors and qualification must be per occurrence; 3D, whole-column and external binding strings need a stated refusal; defined names make N undefined; the tenth operation also changes the TypeScript contract hash; step 0 drops bindings without caller kinds; the graph is derived, not in the snapshot; the graph's other codes and the catalog gap for `PrimitiveError` codes | All corrected in Scope, D2, D3, D4, D5, D6 and Dependencies |
| Citation errors: fixture `:337`; `q1..q3` are caller names; an architecture document outside the repository (removed; the constraint is stated as this spec's premise); rendered text is parenthesised; the versioning citation did not say what was claimed; the node count; the graph's code list | Corrected |
| Confirmed: the arithmetic; the renderer's refusal and the parser's name handling; no wire change in this spec; the break-even, `J1` and `J2` refusals; flattening for scalar number and error arguments; `copy_formula` and `shifted`; Rust's `Expr::Native` refusal; the exact input-set rule | Unchanged |

The refuter's verdict: the design holds in outline (substitution plus `_render` with
`shifted` semantics, structural extraction with complete refusal); as first written, the
closure was the wrong function and both invariants were statements about the fixture.
This version makes them statements about the transformation by refusing what the
composition profile would change.
