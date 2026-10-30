# Refusal codes

Every workbook refusal carries a typed code. Python raises
`workbook_forge.workbook.WorkbookError` (or its subclass `UnsupportedWorkbook`) with a
`code` attribute. Rust returns `workbook_forge::xlsx::XlsxError`, whose `code()` method
reports a code for that message. Canonical-model errors (`ModelError`,
`CanonicalPackageError`) and engine errors (`ToolkitError`) already carried codes and
keep them.

The code is chosen in this order:

1. An explicit code. Python passes `code=` on the exception. Rust starts the message
   with a `token: ` prefix, where `token` is any non-empty `[a-z0-9_]+` sequence.
   `ToolkitError` displays as `code: message`, so a conversion into `XlsxError` keeps
   that code, including toolkit codes such as `unsupported_operation`,
   `invalid_request`, `export_error` and `unsupported_cell`.
2. In Python, the same `token: ` prefix when `token` is registered in
   [`catalog/refusal-codes.json`](../catalog/refusal-codes.json), under `codes` or
   `inherited`. Any other leading token is left for the phrase table.
3. The shared phrase table in that catalog. Rust embeds the same bytes as
   `rust/src/refusal_codes.json` (a test keeps the two copies identical). The first
   rule whose phrase occurs in the message, compared case-insensitively, names the code.
4. A fallback when nothing matches: `workbook_error` for `WorkbookError`,
   `unsupported_package` for `UnsupportedWorkbook`, and `xlsx_error` for `XlsxError`.
   The corpus report counts those fallbacks as `untyped`, a gap to close rather than
   an accepted outcome.

`tools/corpus/expectations.json` records two splits that remain:

| Corpus file | Python | Rust |
| --- | --- | --- |
| `hb_no_cell_references.xlsx` | `unsupported_package` | `package_damaged` |
| `xw_macro.xlsm` | `not_xlsx` | `macro_content` |

Callers should branch on the code, not on the message text, which may change.

| Code | Meaning |
| --- | --- |
| `macro_content` | The package holds VBA or macro-enabled content. Workbook Forge refuses it rather than preserve code it cannot inspect. |
| `strict_ooxml` | The package uses the Strict OOXML namespaces. Only Transitional SpreadsheetML is read. |
| `not_xlsx` | The file or output name is not an .xlsx workbook (for example .xlsm, .xls or .csv given to the XLSX reader). |
| `package_limit` | A size, entry-count, cell-count or work limit was exceeded. |
| `date_1904_unsupported` | Formula calculation, formula edits and formula caches are not supported in a 1904 date-system workbook. Values still read and edit. |
| `array_spill_unsupported` | The operation would need worksheet array or spill placement, which is not modeled. |
| `calculation_diagnostics` | Export was asked to write formula caches for cells the engine could not calculate. Bind outputs to the cells you work with so unrelated formulas are preserved unchanged. |
| `preserved_only_edit` | The edit targets content that is preserved byte-for-byte but not editable (styles, layout, validation rules, grouped or table formula regions). |
| `pivot_output_protected` | The edit writes into a pivot table's output range. Pivot tables are preserved but not refreshed. |
| `chart_reference_protected` | The change would break a chart's series references (for example renaming or removing a sheet a chart reads). |
| `validation_failed` | The value is outside an enforced data-validation rule: `whole`, `decimal`, `date`, `time`, `textLength` or `list`. |
| `merged_cell` | The edit targets a merged range's non-anchor cell. |
| `out_of_bounds` | The address is outside the Excel grid or the session's grid bound. |
| `output_exists` | The output path exists or is the source workbook. |
| `revision_conflict` | The model changed between the snapshot and the operation. |
| `formula_unsupported` | A formula uses syntax, a function or a reference the engine does not calculate. |
| `unsupported_sheet_type` | A dialog sheet, macro sheet or other non-worksheet sheet type blocked the operation. Chartsheets are read and preserved byte-for-byte; they are listed and are not modeled as worksheets. |
| `unsupported_error_value` | A cell stores an error value outside the v1 error set (`#NULL!`, `#DIV/0!`, `#VALUE!`, `#REF!`, `#NAME?`, `#NUM!`, `#N/A`, `#SPILL!`, `#CALC!`), for example `#GETTING_DATA`, `#FIELD!` or `#BLOCKED!`. The model's error enum is fixed for v1; the workbook is refused rather than the value rewritten. |
| `csv_encoding` | A delimited text file could not be decoded with the requested or detected encoding. |
| `csv_malformed` | A delimited text file has an unterminated quoted field or another syntax error. |
| `package_damaged` | The ZIP container, a package relationship, or a part's XML is malformed or inconsistent. |
| `unsupported_package` | The package is well formed but uses a feature outside the supported profile. |

## Canonical-model codes

The canonical intake and export path (`workbook_forge.intake`, `canonical_xlsx`) reports
its own codes through `CanonicalPackageError` and `ModelError`, for example
`unsupported_formula_group` (shared, array or data-table formula groups),
`array_spill_refused`, `unsupported_package` (such as a 1904 date system),
`invalid_output` and `evidence_insufficient`. The table's `inherited` list names them and the engine
(`ToolkitError`) codes such as `invalid_value` and `parse_error`. They are defined at their raise
sites in `python/workbook_forge/canonical_xlsx.py` and `model.py`.
