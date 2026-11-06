# Robustness corpus

`fixtures/corpus/` holds small synthetic workbooks of every common shape, written
by our own scripts so there is no third-party licensing exposure:

- `op_*` files come from openpyxl, `xw_*` from XlsxWriter (generator-only
  dependencies; Workbook Forge does not depend on them).
- `hb_*` files are hand-built OOXML (`handbuilt.py`, standard library only). They
  cover what the libraries never write: missing optional parts, absolute part
  targets, prefixed SpreadsheetML, Strict OOXML, pivot caches, external links,
  threaded comments, BOM-prefixed parts, cells without `r` attributes, and the
  quirks of Google Sheets, Numbers and LibreOffice exports.
  `shapes.py` adds later synthetic shapes on the same helpers: a sheet out to
  column XFD, 210 sheets, 30,000 shared strings, mixed inline and shared strings,
  4,000 cell formats with a theme, outline, print and protection settings, and
  synthetic feature parts shaped after the published OOXML schema that the
  libraries do not write (form controls, slicers,
  timelines, sparklines, connections with a Power Query item stored as UTF-16,
  customXml, embedded OLE objects, a pivot calculated field, a table totals row
  and calculated column) plus formula shapes (3D references, names that look
  like references, a formula near the 8,192-character limit).
  `in_cell_arrays.py` adds Excel's lists, arrays in cells, nested arrays, the
  FLATTEN/HAS/HASANY/HASALL functions and Compatibility Version 3 (Excel Beta,
  September 2026). These five files are **modeled, not captured from Excel**:
  Microsoft has not published the cell markup, so the module documents what is
  published and what is guessed, and their `MANIFEST.json` entries carry
  `"provenance": "modeled, not captured from Excel"` with a note to replace
  them with real Excel Beta captures. See [in-cell arrays](../../docs/in-cell-arrays.md).
- `lo_*` files come from LibreOffice headless when `soffice` is on PATH and
  `--writers` includes `libreoffice`. That writer is not in the default list.

Regenerate with `python tools/corpus/generate_corpus.py` (openpyxl and XlsxWriter
installed). The default writers are openpyxl, XlsxWriter, and hand-built OOXML.
`fixtures/corpus/MANIFEST.json` lists each file's writer and
categories. openpyxl stamps the save time into `docProps/core.xml`, so its files
are not byte-stable across regenerations; the others are.

`python tools/corpus_check.py --report docs/corpus-report.md` runs every file
through every engine and writes the matrix. `expectations.json` lists the
expected non-pass outcomes; `python/tests/test_corpus.py` holds the matrix to it
and never accepts a crash, a corrupt package or a lost value. The report's
`Run:` line names the day and the commit the engines ran from;
`test_committed_corpus_report_is_fresh` regenerates the report and compares
everything but that line, so a stale committed report fails the suite.

`python tools/corpus/capabilities.py` turns the committed report into
[docs/capabilities-xlsx.md](../../docs/capabilities-xlsx.md): each category as
supported, preserved only, or refused with its code. Rerun it after the report
changes; `test_capabilities_document_is_current` fails until you do.
