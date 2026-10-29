# Refusal codes

Every workbook refusal carries a typed code. Python raises
`workbook_forge.workbook.WorkbookError` (or its subclass `UnsupportedWorkbook`) with a
`code` attribute; Rust returns `workbook_forge::xlsx::XlsxError`, whose `code()` method
returns the same code. Canonical-model errors (`ModelError`, `CanonicalPackageError`) and
engine errors (`ToolkitError`) already carried codes and keep them.

A raise site can name its code explicitly (Python `code=`, Rust a `code: ` message prefix).
Otherwise the message is classified by the shared table in
[`catalog/refusal-codes.json`](../catalog/refusal-codes.json), which Rust embeds as
`rust/src/refusal_codes.json` (a test keeps the two identical): the first rule whose phrase
occurs in the message names the code. A message no rule matches keeps the untyped fallback
(`workbook_error` in Python, `xlsx_error` in Rust); the corpus report counts those as
`untyped`, a gap to close rather than an accepted outcome.

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
| `validation_failed` | The value is not allowed by the target cell's data-validation rule (a drop-down list). |
| `merged_cell` | The edit targets a merged range's non-anchor cell. |
| `out_of_bounds` | The address is outside the Excel grid or the session's grid bound. |
| `output_exists` | The output path exists or is the source workbook. |
| `revision_conflict` | The model changed between the snapshot and the operation. |
| `formula_unsupported` | A formula uses syntax, a function or a reference the engine does not calculate. |
| `unsupported_sheet_type` | A sheet type other than a worksheet blocked the operation. |
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
