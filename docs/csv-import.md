# CSV and TSV import

`workbook_forge.delimited.import_csv` (Python) and `workbook_forge::delimited::import_delimited`
(Rust) read delimited text into a new one-sheet workbook model. Export it like any other
model (`workbook_forge.xlsx.export_xlsx`, `xlsx::export_xlsx` / `export_xlsx_bytes`).
Both engines follow the rules below and run the same case table,
`tools/corpus/csv-cases.json` (`python/tests/test_delimited.py`, `rust/tests/delimited.rs`).

```python
from workbook_forge.delimited import import_csv
from workbook_forge.xlsx import export_xlsx

model = import_csv("parts.tsv")                 # .tsv/.tab -> tab, otherwise comma
model = import_csv(data, delimiter=";", encoding="latin-1", sheet_name="Data")
export_xlsx(model, "parts.xlsx")
```

## Decoding

- With no `encoding`, a UTF-8 or UTF-16 byte-order mark picks the encoding and is dropped.
  Without a mark the bytes must be UTF-8.
- An explicit `encoding` is one of `utf-8`, `utf-16` (needs a mark), `utf-16-le`, `utf-16-be`
  or `latin-1` (`iso-8859-1`). Case and `_`/`-` do not matter.
- Bytes that do not decode, an odd UTF-16 byte count, an unpaired surrogate or an
  unsupported encoding name refuse with **`csv_encoding`**. Nothing falls back to another
  encoding silently, so a Windows-1252 file must be named as `latin-1` or converted first.

## Syntax

RFC 4180, the way Excel reads it:

- The delimiter is one of `,` (default), tab, `;` or `|`.
- A field that starts with `"` is quoted. `""` inside it is a literal quote, and it may hold
  delimiters and line breaks, which are kept exactly (including CRLF).
- A quote inside an unquoted field is literal (`5" pipe`).
- Records end at CRLF, LF or CR. A final line break does not add a row. A blank line in the
  middle is an empty row, so later rows keep their line numbers.
- Records may have different lengths.
- An unterminated quoted field, text after a closing quote (`"ab"c`), or a control character
  a workbook cannot store (NUL and the other C0 controls except tab, LF and CR) refuse with
  **`csv_malformed`**. The message names the line.
- More than 1,048,576 rows or 16,384 fields in a record refuse with `out_of_bounds`; input
  over 128 MiB with `package_limit`; a field over 32,767 UTF-16 units with the model's usual
  text-length refusal.

## Values

- An empty field is an absent cell.
- A field that is exactly a plain decimal number (`-12`, `3.5`, `1E+20`, `2.5e-3`) with at
  most 15 significant digits becomes a number. `-0` is 0.
- Everything else stays text exactly as written: `00123`, `007.0`, `1,234`, `+5`, `.5`,
  ` 12`, `1234567890123456`, `1E999`, `TRUE`, `#N/A`, `=SUM(A1:A2)`. A field that looks
  like a formula is never imported as one, so a CSV cannot inject formulas.
- `as_text=True` keeps every non-empty field text.

Text-looking numbers stay text where Excel would convert them and lose digits (leading
zeros, long IDs). Imported cells carry no number format; see [text cells](text-cells.md) for
how `@`-formatted cells keep later edits as text.

## Not covered

- Other encodings (Windows-1252, Shift-JIS and so on), delimiter sniffing, header detection,
  locale-specific numbers (`1.234,5`) and date parsing. Dates stay text.
- The browser view (`web/`) does not import CSV yet.
