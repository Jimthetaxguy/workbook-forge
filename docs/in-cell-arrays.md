# In-cell arrays (Excel Beta, September 2026)

Microsoft announced lists, arrays in cells, nested arrays, four new functions
(FLATTEN, HAS, HASANY, HASALL) and Compatibility Version 3 for the Excel Beta
Channel on 24 September 2026 (Windows 2610 build 20520.20000, Mac 16.114 build
26092111):
[Put multiple values in one cell with lists and arrays in Excel](https://techcommunity.microsoft.com/blog/microsoft365insiderblog/put-multiple-values-in-one-cell-with-lists-and-arrays-in-excel/4559395).
Workbook Forge does not read or calculate them. It either refuses them with one
typed code, naming the cell, or reads and preserves them unchanged with a typed
calculation diagnostic. It never replaces an array with its fallback value.

## What each surface does

The Python toolkit, Rust and the browser engine (Rust compiled to WASM) report
the same codes and messages. The canonical lane refuses a Compatibility Version
3 workbook on open with its own sentence.

| Construct | How it is stored (see below) | Outcome |
| --- | --- | --- |
| A list or an array held in a cell (Insert > List, a pasted array value) | Cell with value metadata (`vm`) in a package that stores rich arrays | Refused on open: `in_cell_array`, naming the cell |
| A formula wrapped in extra braces (`={{1;2;3}}`, `={TEXTSPLIT(A1,",")}`) whose result stays in one cell | Same, with the formula in `<f>` | Refused on open: `in_cell_array` |
| A nested array (`={{1,2,3};{4,5,6}}`) | Same, possibly with a spill range | Refused on open: `in_cell_array` |
| The same syntax in a formula with no array value stored (or typed as an edit) | `<f>` text only | Read and preserved; calculation diagnostic `unsupported_formula`, message `in-cell array {...}: braces around a formula or an array keep an array in one cell (Excel arrays in cells), which is not calculated`. A formula edit with it is refused with the same code and message |
| FLATTEN, HAS, HASANY, HASALL (with or without `_xlfn.`) | `<f>` text | Read and preserved; diagnostic `unsupported_formula`, message `in-cell array function NAME: FLATTEN, HAS, HASANY and HASALL work on Excel arrays in cells and are not calculated`. Formula edits that use them are refused the same way |
| Compatibility Version 3 | `xlwcv:version setVersion="3"` in the workbook's extension list | Values read and edit. Formulas are preserved byte-for-byte and not calculated (each reports `unsupported_cell` with `Compatibility Version 3 formula calculation is unsupported: ...`); formula edits are refused with `compat_version_3`. The canonical path refuses the workbook with `compat_version_3`, because its fresh package would drop the setting |

The browser reports a refused open as `open_failed` and a refused export as
`export_failed`, each with the typed code in `refusal` (for example
`refusal: "in_cell_array"`). An edit refusal carries the code directly
(`compat_version_3`, `unsupported_formula`).

## No data loss

- A cell that may hold an array is never read as its fallback value: the whole
  workbook is refused before any value is returned.
- Where a workbook is read (the functions, the syntax without a stored array,
  Compatibility Version 3), the formulas and every package part other than the
  edited sheet, `xl/workbook.xml` and `[Content_Types].xml` come back
  byte-identical after a value edit and export; the formula cells themselves
  come back byte-identical inside the edited sheet. The Python toolkit rewrites
  `xl/workbook.xml` when it asks Excel to recalculate, so there the
  Compatibility Version element is kept as the same XML with a different
  namespace prefix; Rust keeps it byte-for-byte.
- Exporting caches for formulas the engine did not calculate is refused
  (`calculation_diagnostics`) unless the caller binds the cells it works with,
  the documented flow for unsupported formulas. For a Compatibility Version 3
  workbook, that unbound export refusal carries `compat_version_3` in both
  Python and Rust. Python reaches that code through the refusal phrase order:
  the phrase `compatibility version 3` is earlier in the shared table than
  `cannot export calculation diagnostics`, and the diagnostic sentence contains
  it. Rust's unbound export message includes the same diagnostic sentence, so
  the same phrase selects `compat_version_3`. A snapshot that adds or changes a
  formula is an explicit `compat_version_3` refusal in both Python and Rust.
  The browser reports the unbound export as `export_failed` with
  `refusal: "compat_version_3"`.

Tests: `python/tests/test_in_cell_arrays.py`, `rust/tests/corpus.rs`,
`web/forge-engine/test/engine.node.test.mjs`, and the corpus matrix
(`python/tests/test_corpus.py`, [corpus report](corpus-report.md)). The two
parsers share `tools/corpus/in-cell-array-cases.json`.

## How the files are stored, and what is guessed

Microsoft has not published how Excel writes these cells. The corpus files
(`hb_in_cell_list`, `hb_in_cell_array_formula`, `hb_nested_array`,
`hb_flatten_has_functions`, `hb_compat_version_3`) are **modeled, not captured
from Excel**, and their `MANIFEST.json` entries say so. Replace them with real
Excel Beta captures when one is available.

- Published: rich values and rich arrays ([MS-XLSX] Rich Value Data, Rich
  Value Structure, Rich Value Array: `xl/richData/rdarray.xml`, root
  `arrayData`, a structure with `t="_array"` and one key `array` of type `a`,
  array values typed `d i b e s r a`), and the Compatibility Version element
  (`CT_WorkbookCompatibilityVersionExt`, namespace
  `http://schemas.microsoft.com/office/spreadsheetml/2024/workbookCompatibilityVersion`).
  A third-party test of the Beta build reports the in-cell array data in
  `xl/richData/rdarray.xml` ([SpreadsheetWeb](https://spreadsheetweb.com/excel-in-cell-arrays/)).
- Guessed: the cell's fallback value (`t="e"` `#VALUE!`), how `vm` is wired,
  the formula text for extra braces and nested arrays, the `_xlfn.` prefix on
  the new functions, and `setVersion="3"`.

The refusal is deliberately broad: any cell with value metadata in a package
that stores rich arrays (a rich array part, or a rich value structure of type
`_array`) is refused, without resolving which rich value the cell points at.
A linked data type whose properties use rich arrays is refused too. Rich
values of other types (images in cells, data types without arrays) read as
before.
