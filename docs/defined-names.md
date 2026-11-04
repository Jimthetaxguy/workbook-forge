# Defined names

Workbook Forge reads Excel defined names (`definedName` in `xl/workbook.xml`)
and calculates formulas that use them, in every engine: the Python toolkit
(`python_engine`), the Python canonical model (`model.calculate`), the Rust
toolkit (`toolkit::Session`) and the Rust canonical model (`model::calculate`).
Names are **read-only**. No edit, agent operation or browser command adds,
changes or removes a name, and the agent and browser protocols carry no name
operations. A formula you write may use a name the workbook already has.

The rules below are Workbook Forge's. Expected values in the shared fixture are
independent arithmetic over its cells, not an Excel observation.

## How a name calculates

Each parser resolves a name where it reads it: the name is parsed into the tree
of what it refers to, as if that text stood there in parentheses. So:

- a cell that uses a name depends on every cell the name refers to, through any
  chain of names, and editing one of those cells recalculates the cell;
- functions inside a name count as the cell's functions (an unsupported
  function inside a name refuses the cell, a volatile one makes it volatile);
- the stored formula text never changes, and export writes the formula as it
  was read.

## Lookup

- Names are case-insensitive (`rate`, `Rate` and `RATE` are one name).
- `localSheetId` scopes a name to the sheet at that position in the workbook's
  `sheets` list (chartsheets count). On its own sheet a sheet-scoped name wins
  over a workbook name of the same spelling; elsewhere the workbook name applies.
  A name whose `localSheetId` names no sheet is not read.
- A name inside a name resolves on the inner name's scope sheet when it has
  one, otherwise on the sheet of the formula being calculated.
- A token shaped like a cell is a cell, as before: `XFE1` (past column XFD)
  stays `invalid_reference` even when a name of that spelling exists. A token of
  four or more letters and digits (`RATE2`, `Tax2026`) cannot be a cell and is a
  name; Rust now reads that shape the way Python does.
- `Sheet!Name` (a sheet-qualified name) is not read; it stays a parse error.

## What a name may refer to

| Target | Result |
| --- | --- |
| a cell or range, sheet-qualified and absolute (`Inputs!$A$1`, `Inputs!$B$1:$B$3`) | calculated |
| a constant (`0.25`, `"text"`) | calculated |
| a formula (`=Rate*Principal`; a leading `=` is optional) | calculated |
| another name, to any depth | calculated |
| `#REF!`, or `Sheet!#REF!` left after deleting the cells | `#REF!` |
| a name the workbook does not define | `#NAME?` |
| a cycle (`A` -> `B` -> `A`, or a name naming itself) | `name_cycle`: `defined names form a cycle: A -> B -> A` |
| a hidden name, or a built-in `_xlnm.` name (`Print_Area`, `_FilterDatabase`, `Print_Titles`) | `unsupported_formula`: `defined name X is hidden or built-in and is not calculated` |
| another workbook (`[1]Rates!$B$2`, `'[1]My Sheet'!$A$1`) or a structured reference (any `[` outside a string) | `unsupported_formula`: `defined name X refers to another workbook or a table, which is not calculated` |
| `OFFSET` or `INDIRECT` (dynamic names) | `unsupported_formula`: `unsupported function OFFSET` |
| a relative reference (`Inputs!A1`, `Inputs!$A1`) | `unsupported_formula`: `defined name X uses a relative reference, which is not calculated` |
| a cell without a sheet (`$A$1`) | `unsupported_formula`: `defined name X refers to a cell without a sheet, which is not calculated` |
| a 3D span (`Jan:Mar!$B$2`) | `unsupported_formula`, the shared 3D message |
| an array constant, an empty or malformed target | `parse_error`: `defined name X could not be parsed` |

Excel resolves a relative reference inside a name against the cell that uses
the name. Workbook Forge refuses those names with a typed code rather than
guess the anchor; they are uncommon in workbooks and easy to misread.

A name cannot be a range endpoint here (`SUM(Rate:Principal)`, `A1:Rate`): both
parsers report `parse_error`. One formula may expand at most 256 names,
counting repeats and nesting (`defined names expand past 256 uses in one
formula`, `resource_limit`).

The `#NAME?` rule needs a name table. Every workbook read from `.xlsx` has one
(possibly empty). A document built without one (a JSON document, a blank
browser workbook, the plain `evaluate` API) keeps the old refusal,
`unsupported name 'X'`, because nothing says the name is undefined.

## Codes by engine

The toolkit engines (Python and Rust) report the code in the table. The
canonical model (Python and Rust) maps it onto its diagnostic set with the same
message: `name_cycle` becomes `cycle`/`cycle`, `resource_limit` stays
`resource_limit`, `parse_error` stays `parse_error`, `unsupported_formula` is
`unsupported_formula`/`unsupported`.

## Where the table lives

- Toolkit sessions take the table beside the document, never inside it:
  `python_engine.Session(model_json, names_json)`, the native
  `Session(model_json, names_json)` and `toolkit::Session::with_names`. The
  table is a JSON array of `{name, sheet, formula, hidden}`. `import_xlsx` in
  both languages builds it from the package; the browser's `openXlsx` gets it
  through the Rust importer. The workbook document schema is unchanged.
- The canonical model keeps it in `metadata.defined_names`: each entry has
  `name`, `sheet`, `formula`, `hidden` and the element's `attributes`. Intake
  fills it, so `definedNames` is no longer listed as not carried (a name scoped
  to a sheet the model does not carry is left out and still listed).

## Export

The toolkit writers patch the source package, so `xl/workbook.xml` and every
`definedName` come back byte-for-byte. The canonical writer builds a fresh
package and writes each name back with its attributes and text;
`localSheetId` is renumbered to the fresh sheet order, and a name scoped to a
sheet the workbook no longer has refuses the export (`unsupported_package`).

## Tests

- `fixtures/defined-names.v1.json`: the shared cases (scopes, constants,
  formulas, chains, cycles, `#REF!`, unknown names, hidden and built-in names,
  external, dynamic, relative, 3D, array constants, range endpoints, the use
  limit) and three recalculations after editing a target. Python runs it in
  `python/tests/test_defined_names.py` (toolkit, native toolkit when built,
  canonical); Rust in `rust/tests/defined_names.rs` (toolkit and canonical).
- `fixtures/corpus/hb_defined_names.xlsx`: scoped names, a constant, formula
  and chained names, a range name, a cycle, a `#REF!` name, an unknown name,
  and hidden and built-in names, through the corpus harness in every engine.
