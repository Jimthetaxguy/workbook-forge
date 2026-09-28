---
author: codex/Codex
created: 2026-09-28
agent: codex/Codex
date: '2026-09-28T15:51:28-04:00'
type: public-api-guide
task: Explain workbook-free spreadsheet primitives
status: verified-review-candidate
summary: Independent Python and Rust calls and inspectable expressions share tested spreadsheet contracts, installed metadata and explicit compatibility limits.
next_steps: [Define explicit composition-to-workbook bindings, select further primitives through concrete application cases and independent behavior evidence]
remaining: [Broader primitive coverage, independent Excel behavior observations, later application interfaces]
open_questions: []
---
# Spreadsheet primitives

Workbook Forge connects four uses: extract supported meaning from Excel, build
editable Excel workbooks, compose ordinary programs, and eventually power an
Excel-like interface. A primitive is a reusable operation with a stable identity,
typed arguments, executable behavior and an explicit compatibility profile.
The interface does not require a workbook, cell addresses or formula strings.

Python and Rust implement this layer independently, each reusing its own
handwritten evaluator. They do not call each other. The shared catalog describes
meaning and public entry points; it does not replace executable implementations.

## Direct calls and composition

Direct calls return values:

```python
from workbook_forge import primitives as xl

volumes = xl.range_values([[100], [120], [150]])
assert xl.sum(volumes) == 370
assert xl.round(2.5, 0) == 3
```

Compositions retain the operation identities and named dependencies that an
agent or future interface can inspect:

```python
quantity = xl.call("SUM", xl.input("volumes"))
revenue = quantity * xl.input("unit_price")
profit = quantity * (xl.input("unit_price") - xl.input("unit_cost")) - 3 * xl.input("fixed_cost")
inputs = {"volumes": volumes, "unit_price": 25, "unit_cost": 8, "fixed_cost": 1000}
assert profit.evaluate(inputs) == 3290
assert revenue.evaluate({"volumes": volumes, "unit_price": 25}) == 9250
assert "excel.SUM" in profit.inspect()["operations"]
restored = xl.Expression.from_dict(profit.to_dict())
assert restored.evaluate(inputs) == 3290
```

The first function set is SUM, AVERAGE, MIN, MAX, COUNT, IF, IFERROR, ROUND and
ABS. Python exposes `sum`, `average`, `min`, `max`, `count`, `if_`, `iferror`,
`round` and `abs` in the `primitives` module. Rust exposes the corresponding
functions in `workbook_forge::primitives`, taking slices of `PrimitiveValue`.
Both expression APIs support arithmetic and comparison operators. Python
comparisons use `Expression.binary("<=", left, right)` rather than overloaded
comparison syntax.

Rust's `Expression` constructors return checked results. `PrimitiveValue`
distinguishes a scalar, range, array and omitted argument; evaluation accepts a
map of named native values. The standalone `primitives_scenario` example builds
and runs the same revenue/profit calculation without Python or a workbook.

## Meaning, values and errors

- `None` / `Value::Blank` is a blank. `MISSING` / `PrimitiveValue::Omitted` is an
  omitted call argument. Omission is valid only directly inside a function call.
- `RangeValues` / `PrimitiveValue::Range` retains range identity. A calculated
  array is a different value kind; neither needs a worksheet or spill cells.
  Evaluating a range at the top level returns an array, while the expression
  and its interchange document retain the original range identity.
- Numbers use finite binary64 values. Calculated overflow becomes `#NUM!` at
  this new boundary. Nonfinite inputs are rejected. Legacy APIs are unchanged.
- Spreadsheet errors are returned as values. Invalid API input, unsupported
  operations and resource limits produce `PrimitiveError` with a stable code.
- Named inputs are case-sensitive ASCII identifiers. Every declared input is
  required; extra inputs are refused. This includes inputs in an untaken branch.
- Composed IF and IFERROR retain lazy branch evaluation. Python and Rust still
  evaluate ordinary arguments before calling a direct function; use an expression
  to defer a calculation that belongs in a conditional branch.

Operation identity does not establish full Excel equivalence. The current
aggregate profile ignores Boolean and text values in scalar arguments and in
ranges. Microsoft documents a distinction for SUM: directly supplied logical
values and numeric text are counted, while these values inside arrays or
references are ignored. This is a documented difference, not a new live Excel
observation. [Microsoft SUM reference](https://learn.microsoft.com/en-us/office/vba/api/excel.worksheetfunction.sum)

The catalog separates inherited reference documentation from the native
primitive's actual accepted error types and limits. A future strict numeric
application profile must have its own explicit contract; changing a mode must
never silently change existing calculations.

## Discovery and interchange

`primitive_catalog()` returns detached metadata, including stable operation IDs,
Excel correspondence, public Python/Rust paths, argument counts, limits and
known differences. It is derived from the formula support catalog through
`tools/sync_primitive_catalog.py`; the Rust crate embeds an identical copy.
`primitive_expression_schema()` returns the canonical expression schema in both
languages. Both resources are included in installed packages; no source checkout
or schema download is needed.

`Expression.to_dict()` produces a versioned tree of literals, named inputs,
calls and operators. `inspect()` also lists declared inputs and operation IDs.
It describes the expression, not a trace claiming every branch was executed.
`from_dict()` validates the tree before it can execute; it never runs host code.
See the [expression schema](../catalog/primitive-expression.schema.json) and
[primitive catalog](../catalog/primitive-catalog.json).

Limits are 1,024 expression nodes, depth 64, 256 named inputs, 32,767 UTF-16 units
per text value, and 100,000 cumulative value elements. Repeated uses of a bound
collection count repeatedly toward the element limit. Both the expression and
the combined expression/input payload must fit within 1 MiB of compact UTF-8
JSON. Matrices must be nonempty, rectangular and contain only supported scalars.

## Boundaries and next uses

Existing formula authoring, workbook calculation, XML extraction and XLSX
production remain supported through their current APIs. This layer adds
workbook-free execution; it does not yet translate arbitrary Python/Rust source,
automatically bind business inputs to cells, or render an application interface.
An agent can reliably identify declared primitive calls and inspect expression
trees. Recognizing equivalent behavior in unrelated scripts requires separate
analysis and evidence.

Native compositions and cell-based workbook expressions currently use separate
authoring interfaces. Matching scenario outputs do not mean a native expression
can already be exported as workbook formulas. The proposed
[next integration milestone](toolkit-delivery.md#next-integration-milestone-one-authored-calculation)
adds explicit input/output bindings and independent transformations, with refusal
when the declared behavior cannot be preserved in Excel.

Further function coverage should follow a concrete composition that the first
nine functions cannot express, then add its typed contract, native implementations
and independent expected results. A spreadsheet-like UI is a later consumer of
these operations and explanations, rather than a new source of calculation rules.
