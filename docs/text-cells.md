# Text cells

Text in an imported workbook round-trips exactly in both engines: shared and inline
strings, rich-text runs (read as their plain text; the runs stay in the preserved
shared-string table), leading and trailing spaces, line breaks, escaped control
characters (`_x000D_`), astral-plane and combining characters, right-to-left text,
and strings up to Excel's 32,767-character cell limit. Numeric-looking text such as
`00123`, `1E5` or `TRUE` stays text, and a quote-prefixed (`'`) entry such as
`=SUM(1,2)` stays text rather than becoming a formula. `fixtures/corpus/hb_text_edge_cases.xlsx`,
`op_text_cells.xlsx` and `xw_text_formats.xlsx` cover these cases
(`python/tests/test_text_cells.py`, `rust/tests/xlsx_features.rs`).

## Writing into a Text-formatted (`@`) cell

Excel stores whatever is typed into a Text-formatted cell as text. Both engines do
the same for imported workbooks: a number written into an existing `@` cell is stored
as text, written with up to 15 significant digits, switching to an `E` exponent below
`0.0001` or from `1E+15` up (`123` -> `"123"`, `1.5` -> `"1.5"`, `1e20` -> `"1E+20"`). Text
written there is kept as given, so `"00456"` keeps its zeros. Booleans and formulas
are not converted. `tools/corpus/feature-cases.json` (`text_writes`) holds the cases.

Only cells the workbook already holds with the `@` format are covered. A cell
inserted where none existed does not inherit a column's Text format.

## Browser view

The Univer view (`web/univer-spike/src/adapter.ts`) learns which cells are Text
formatted from the engine snapshot (`SnapshotCell.text`). Typing into such a cell
sends the typed characters as text, so `00123` is not read as the number 123 and `=A1`
is not read as a formula. A leading apostrophe marks a force-text entry in any cell and
is not part of the stored value.

## Not covered

CSV and TSV import are not implemented. When they are, decoding failures should refuse
with `csv_encoding` and syntax errors (an unterminated quoted field) with
`csv_malformed`; both codes are registered in `catalog/refusal-codes.json`.
