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
- `lo_*` files come from LibreOffice headless when `soffice` is on PATH.

Regenerate with `python tools/corpus/generate_corpus.py` (openpyxl and XlsxWriter
installed). `fixtures/corpus/MANIFEST.json` lists each file's writer and
categories. openpyxl stamps the save time into `docProps/core.xml`, so its files
are not byte-stable across regenerations; the others are.

`python tools/corpus_check.py --report docs/corpus-report.md` runs every file
through every engine and writes the matrix. `expectations.json` lists the
expected non-pass outcomes; `python/tests/test_corpus.py` holds the matrix to it
and never accepts a crash, a corrupt package or a lost value.
