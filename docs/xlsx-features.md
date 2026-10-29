# Drop-downs, pivot tables and charts in imported workbooks

Imported workbooks keep data validation, pivot tables, pivot caches, charts,
drawings and chartsheets byte-for-byte. Workbook Forge reads them for `inspect`
and refuses the cell writes they forbid. Python (`workbook_forge.xlsx_features`)
and Rust (`rust/src/xlsx_features.rs`) implement the same rules;
`tools/corpus/feature-cases.json` holds the cases both test suites run
(`python/tests/test_xlsx_features.py`, `rust/tests/xlsx_features.rs`).

Where to find the metadata:

- Python: `import_xlsx(path).inspect()["xlsx"]` has `data_validations`, `pivots`,
  `charts` and `chartsheets`.
- Rust: `ImportedWorkbook::capabilities()` has the same keys with the same shapes.
  The older raw `validation` list stays for compatibility.

## Data validation (drop-downs)

Each `dataValidation` becomes one record:

| Field | Meaning |
| --- | --- |
| `sheet`, `sqref` | Where the rule applies (`sqref` is a list of ranges). |
| `type` | `list`, `whole`, `decimal`, `date`, `time`, `textLength`, `custom` or `none`. |
| `operator`, `formula1`, `formula2` | The comparison for number, date and text-length rules. |
| `source` | For lists: `{"kind": "inline", "values": [...]}`, `{"kind": "range", "sheet", "range"}`, `{"kind": "name", "name", "resolves_to", ...}` (a defined name, resolved sheet-scoped first, then workbook-scoped), or `{"kind": "formula"}`. |
| `allowed_values` | The list items: inline values, or the source range's values as imported. `null` when the list is built by a formula. |
| `allow_blank`, `show_error`, `error_style`, `in_cell_dropdown`, `error_title`, `error` | The rule's flags. `in_cell_dropdown` is false when OOXML `showDropDown="1"` hides the arrow. |
| `enforced`, `unenforced_reason` | Whether writes are checked, and why not when they are not. |

A rule is **enforced** when Excel would stop the entry: `showErrorMessage` is on and
`errorStyle` is `stop` (the default). Warning and information alerts let the entry
through in Excel, and a rule with no error alert accepts anything, so those rules are
reported but not enforced. Rules this engine cannot evaluate are also reported with
`enforced: false`: custom formulas, lists built by a formula (for example `OFFSET`),
and number rules whose bounds are formulas.

An enforced rule refuses an out-of-rule value with `validation_failed`, at edit time
and again at export (so a transferred snapshot meets the same check):

- Lists compare text without regard to case (Excel's list check is not
  case-sensitive). A number matches a list item that is the same number, including an
  inline item such as `"1,2,3"`. Text never matches a number in a source range. A
  range-sourced list reads the range's current values when the write happens.
- `whole` needs an integer, `decimal`, `date` and `time` need a number, each compared
  with the operator and bounds. `textLength` compares the length of the text.
- Clearing a cell (an empty value) is never validated, as in Excel.
- Formula entries are not checked. Excel checks a formula's result once at entry and
  never again on recalculation; Workbook Forge does not check it at all (a known gap).

Validation rules themselves cannot be edited; they are preserved.

## Pivot tables

Each pivot table on a worksheet is reported with its `sheet`, `name`, `location` (the
output range), `source` (cache source type, `sheet` and `ref` for a worksheet source,
the cache part and `refresh_on_load`), the cache `fields`, `row_fields`,
`column_fields` (`Values` stands for the data-field column), `page_fields`, and
`data_fields` with their source field and summary function.

**Refresh is not supported.** The cached output is preserved as stored. Editing the
source range is allowed and leaves the pivot output stale until Excel refreshes it
(a cache with `refreshOnLoad` refreshes when Excel opens the file). A write into a
pivot's output range refuses with `pivot_output_protected`.

## Charts

Each chart on a worksheet drawing or a chartsheet is reported with its `sheet`, `host`
(`worksheet` or `chartsheet`), `anchor` (the cell range it covers, the top-left cell
for a one-cell anchor, or `whole sheet`), `types` (for example `barChart`,
`lineChart`, `scatterChart`) and `series`, each with its chart type and the reference
formulas it reads (`tx`, `cat`, `val`, `xVal`, `yVal`, `bubbleSize`).

Value edits keep chart references valid: the chart reads the same cells and Excel
redraws it from their new values. Structural changes to a sheet a chart reads
(renaming, removing or moving it in a transferred snapshot) refuse with
`chart_reference_protected`. Row and column insertion and deletion are not offered by
either engine, so they cannot shift a series range.
