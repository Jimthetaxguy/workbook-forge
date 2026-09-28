---
author: Codex
created: 2026-09-28
agent: codex/Codex
date: '2026-09-28'
type: verification-guide
status: partial
task: Independently observe bounded workbook behavior in Microsoft Excel
summary: Synthetic observation harness with separate expected and observed evidence; three live formula checks succeeded while file roundtrip remains unverified.
---
# Excel observations

`tools/excel_oracle.py` generates a new public synthetic workbook and an evidence
receipt. Its default mode does not start Excel:

```sh
python tools/excel_oracle.py
python tools/excel_oracle.py --run-excel
python tools/excel_oracle.py --run-excel --live-scenario
```

The first two commands need only Python's standard library. The live-scenario
mode also needs the installed toolkit/native extension: it creates the actual
`operating_scenario()` cells and formulas in Excel, reads baseline outputs, edits
the unit price from 20 to 25, and reads the outputs again. It does not save files,
and cannot validate generated XLSX, formatting, or validation rules.

Every run has a unique directory under the system temporary directory; use
`--output-dir` to select another local evidence root. The default file-mode
corpus is `fixtures/excel-observation-cases.json`. It covers arithmetic, errors,
absent cells versus empty formula strings, mixed references copied by Excel,
and SUM/IF scenario calculations. Formula caches are absent from the generated
input, so prepopulated expected values cannot masquerade as Excel calculation.
A small reviewed function allowlist rejects external or unreviewed functions.

The file mode uses the installed Excel AppleScript interface. It checks the
new workbook's full path, calculates only its worksheet ranges, saves that
workbook, and closes that workbook after successful commands. It does not alter
global calculation or security settings. The live mode retains the newly created
workbook reference and confirms an inserted synthetic marker before closing it.
Neither mode closes other workbooks or sends cleanup commands after an uncertain
failure. A failed run can leave its scratch workbook open; the receipt reports
whether closing was confirmed. No process-wide Excel termination is attempted.

## Evidence meanings

- `documented` expectation: a proposed result supported by an explicit source.
- `independently_derived` expectation: a stated mathematical derivation or
  manually reasoned candidate behavior. It is not an Excel observation.
- `forge_profile` expectation: a local behavior choice, not Microsoft evidence.
- `excel_observed`: a typed cache read from the new workbook after the Excel
  commands completed. The receipt separately records comparison results.
- `excel_live_observed`: a live value returned by Excel before saving or closing.
  Live-scenario values are emitted before cleanup and survive a later failure.

`generated` means no Excel observation was attempted. `observed` requires all
file-mode cases to match. `observed_unsaved` means live scenario values match,
without a saved-workbook claim. `observed_partial` retains successful reads when
later commands fail. `mismatch`, `failed`, and `blocked` do not count as conformance.
A live run returns a nonzero exit status unless every expected observation
matches and its owned workbook was confirmed closed.

Receipts retain case/model hashes, timestamps, Excel version and calculation
settings when available, typed results, expected results and their basis, and
explicit tolerances. Observations from one version and profile do not establish
complete Excel compatibility. XML fixture tests exercise the harness itself;
they are never labeled Microsoft observations.

## Observed on 2026-09-28

An Excel-created synthetic workbook in Microsoft Excel **16.113.2**, automatic
calculation, produced these actual live values:

| Formula | Excel result |
| --- | ---: |
| `=2+3*4` | 14 |
| `=IF(FALSE,1/0,42)` | 42 |
| `=SUM(1080,1000)` | 2080 |

Those values were emitted before cleanup. The same owned workbook was confirmed
closed without saving. This three-formula smoke check does not establish
workbook import/export, copied-reference, or validation conformance. Its exact
script hash is `a7987c0c9e67a2a44f9c6f0cb80b4e29c1d6ad144c3c19933119aa170870d856`.
The local receipt is retained outside the repository; raw Excel-generated files
are not suitable for publication without a separate metadata review.

File automation had inconsistent open behavior and a save timeout (`-1712`).
One later open/calculate/save/close command sequence completed, with Excel
reporting the 1900 date system, full precision, and automatic calculation, but
the resulting package lacked formula caches. The harness rejected it as evidence.
The complete six-output live scenario check subsequently encountered Excel
Apple-event parameter error `-50`; no scenario result is claimed from that run.
The generated-XLSX roundtrip, mixed-anchor copy, and live scenario acceptance
remain unverified. No global settings or user workbook contents were changed.
