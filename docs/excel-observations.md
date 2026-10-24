---
author: Codex
created: 2026-09-28
agent: codex/Codex
date: '2026-09-28'
type: verification-guide
status: partial
task: Independently observe bounded workbook behavior in Microsoft Excel
summary: Two 2026-09-28 Excel runs are unreceipted local notes. They grade no function excel_observed. The full red-flag gate remains blocked by unsupported worksheet spill placement.
---
# Excel observations

`tools/excel_oracle.py` generates a new public synthetic workbook and an evidence
receipt. Its default mode does not start Excel:

```sh
python tools/excel_oracle.py
python tools/excel_oracle.py --run-excel
python tools/excel_oracle.py --run-excel --live-scenario
```

Workbook generation and receipt writing use Python's standard library.
`--run-excel` additionally requires macOS and an installed Microsoft Excel with
working AppleScript automation. Run the commands from the repository root.
The live-scenario mode also needs the installed Python toolkit: it creates the actual
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

## SDK export round-trip

The `--roundtrip` mode consumes an SDK-generated `.xlsx` supplied by the caller.
It copies the source to a unique scratch directory and only edits that copy:

```sh
PYTHONPATH=python python tools/excel_oracle.py \
  --roundtrip path/to/sdk-generated.xlsx \
  --contract fixtures/excel-roundtrip-red-flags.contract.json \
  --run-excel --output-dir /tmp/workbook-forge-roundtrips
```

`--run-excel` is mandatory for this mode. The versioned contract declares the
model version, cell edits, formulas, expected result types and values, numeric
tolerances, volatile predicates, formula allowlists, and required fixture
classes. The harness checks formulas and exported package safety, opens only its
owned scratch copy, applies only declared input edits, calls Excel's full
dependency rebuild, saves and closes the copy, then reimports the saved workbook
with the toolkit importer `workbook_forge.xlsx.import_xlsx`
(`tools/excel_oracle.py`, `_model_view`). That importer is not canonical import
(`workbook_forge.canonical_xlsx.import_canonical`). The harness records formula changes separately
from typed cell results, compares expected outputs after reimport, and checks
that edited inputs survived. A cached value without a completed Excel rebuild,
save, and reimport cannot produce `observed`.

Excel's full dependency rebuild is application-wide. To keep that command
bounded, the script requires that no workbook is already open before it opens
its one scratch file. If Excel is already in use, or Apple-event permission is
missing, the receipt says `blocked`; it does not report an implementation
failure or parity. Iteration checks require matching settings to already be
enabled in Excel and in the workbook. The harness never changes global
calculation or iteration settings, executes macros, follows links, or fetches
external data. A timeout may leave the owned scratch copy open, which is
recorded; the script does not send cleanup commands after an uncertain result.
Some Excel versions return `missing value` for the global calculation and
iteration properties through AppleScript. Those fields are then recorded as
unavailable and unverified; they are not reported as unchanged. An iteration
fixture is blocked if its settings cannot be observed or do not match.

The machine-readable contract schema is
[`schemas/excel-roundtrip-contract.schema.json`](../schemas/excel-roundtrip-contract.schema.json),
and the red-flag fixture is
[`fixtures/excel-roundtrip-red-flags.contract.json`](../fixtures/excel-roundtrip-red-flags.contract.json).
Receipt statuses are `prepared` (preflight only), `observed` (Excel rebuild,
save, reimport, and every required assertion passed), `mismatch` (an observed
formula or behavior differed), `blocked` (automation or a required SDK
capability is unavailable), and `failed` (the harness or package could not be
read). `fixture_coverage` distinguishes classes actually observed from classes
that were not run or were blocked.

The corpus deliberately separates array calculation from spill placement.
`SUM(SEQUENCE(3))` checks a scalar reduction over an array result. It does not
prove that Excel writes the result into the neighboring worksheet cells. The
`array_spill` fixture explicitly names `B7:B9`, but current SDK export raises
`UnsupportedWorkbook` for worksheet array spill caches. The round-trip harness
therefore refuses that required check and records it as
`unsupported_capability`; it cannot mark the full red-flag contract observed.
Spill placement remains a product and acceptance gate until export can produce
and reimport those cells.

## Evidence meanings

- `documented` expectation: a proposed result supported by an explicit source.
- `independently_derived` expectation: a stated mathematical derivation or
  manually reasoned candidate behavior. It is not an Excel observation.
- `forge_profile` expectation: a local behavior choice, not Microsoft evidence.
- `excel_observed`: a typed cache read from the new workbook after the Excel
  commands completed. The receipt separately records comparison results.
- A receipt with status `observed` also carries an `excel_observation` block
  (saved workbook sha256, Excel version, `event_time` when Excel ran). That
  block is what a catalog `excel_observed` grade cites; if the saved workbook's
  hash changes the observation is stale. `python3 tools/excel_oracle.py
  --check-receipt <receipt.json>` reports `current`, `stale`, `missing` or
  `not_an_observation`. See [evidence grades](evidence-grades.md).
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

Excel may rewrite formulas while saving. The harness compares the entire formula
set, and only exact listed variants are allowed. In the operating scenario,
Excel 16.113.2 removed redundant single quotes around the simple sheet name
`Assumptions` from several formulas. That rewrite is recorded per cell; other
formula changes still fail the comparison.

## Unreceipted local note, 2026-09-28 — three-formula smoke

This section is an unreceipted local note. No receipt is committed. It grades
no function `excel_observed`.

The note records these live values from Microsoft Excel 16.113.2, with
automatic calculation, on an Excel-created synthetic workbook:

| Formula | Recorded result |
| --- | ---: |
| `=2+3*4` | 14 |
| `=IF(FALSE,1/0,42)` | 42 |
| `=SUM(1080,1000)` | 2080 |

The note says the values were emitted before cleanup. It says the owned
workbook was confirmed closed without saving. This three-formula smoke check
does not establish workbook import, export, copied-reference, or validation
conformance. The script hash in the note is
`a7987c0c9e67a2a44f9c6f0cb80b4e29c1d6ad144c3c19933119aa170870d856`.
The local receipt is outside the repository. Raw Excel-generated files are
not suitable for publication without a separate metadata review.

The note records inconsistent open behavior and a save timeout (`-1712`).
One later open, calculate, save, and close sequence completed. Excel reported
the 1900 date system, full precision, and automatic calculation. The resulting
package lacked formula caches. The harness rejected that package. The
six-output live scenario check then hit Apple-event parameter error `-50`.
No scenario result is claimed from that run. The generated XLSX round trip
was still unverified. The later SDK export note is below. No global settings
or user workbook contents were changed in these earlier attempts.

## Unreceipted local note, 2026-09-28 — SDK export round trip

This section is an unreceipted local note. No receipt is committed. It grades
no function `excel_observed`.

The note says the harness exported `operating_scenario()` with the Python SDK,
copied the generated workbook, and changed `Assumptions!B1` from 20 to 25. It
says Excel then ran a full dependency rebuild, saved and closed the copy, and
the harness reimported that file with the toolkit importer
`workbook_forge.xlsx.import_xlsx`. The note records Microsoft Excel 16.113.2
and says all twelve declared cell checks matched:

| Cell | Recorded result |
| --- | ---: |
| `Forecast!C2:C4` revenue | 2500, 3000, 3750 |
| `Forecast!D2:D4` variable costs | 800, 960, 1200 |
| `Forecast!E2:E4` profit | 700, 1040, 1550 |
| `Forecast!F2` total revenue | 9250 |
| `Forecast!F3` total profit | 3290 |
| `Forecast!F4` break-even units | 58.8235294117647 |

The note says the package retained all twelve formulas. It says Excel removed
redundant quotes around the simple sheet name `Assumptions` in nine formulas.
Each exact alternate spelling is listed for its own cell in the contract
allowlist. The note says no other formula diffs remained. It describes a local
receipt status of `observed`, with full rebuild, save, close, and reimport,
and an unchanged SDK source file. That receipt is outside the repository.
This note is not a finished observation. The note says global calculation and
iteration properties returned `missing value` through this Excel build's
AppleScript interface, and that those fields were marked unavailable and
unverified. The note does not prove those global settings stayed unchanged.

The complete red-flag contract produces a separate `blocked` receipt before
Excel opens. Its `array-spill-placement` check explicitly names `Red Flags!B7`
and expected spill cells `B7:B9`. SDK export currently raises
`UnsupportedWorkbook` for worksheet array spill caches, so that required class
is `blocked` with `blocker_kind=unsupported_capability`. Volatile, iteration,
scalar-array, and quirk classes stay `not_observed`. The twelve-cell note
grades no function `excel_observed`. It does not close the full export gate.

## Completing workbook acceptance

The outstanding acceptance check for the canonical model starts with
`fixtures/operating-scenario.workbook.json`. Open that file in Excel without repair, edit `Assumptions!B1` to 25,
recalculate, save, then reimport the saved file. Compare formulas, supported
outputs, and every required behavior class; a dynamic-array spill check must
inspect all cells in its declared spill range. Creating equivalent cells
directly in Excel cannot establish that the generated file survives this
roundtrip. `tools/canonical_excel_receipt.py` now exports
`fixtures/operating-scenario.workbook.json` and calls this harness. The harness
reimports Excel's saved file with the toolkit importer
`workbook_forge.xlsx.import_xlsx` (`tools/excel_oracle.py`, `_model_view`), not
canonical import (`workbook_forge.canonical_xlsx.import_canonical`). The SDK
note above is a separate unreceipted local note. The canonical package's Desktop
receipt is still pending on hosts without Excel. The Excel 16.113.4 run
described in [CONTEXT.md](../CONTEXT.md) and [run-history.md](run-history.md)
is uncommitted and unreceipted, with `spec2_passed` false. The scenario contract is
`fixtures/operating-scenario.excel-roundtrip.contract.json`. Its
`expected_formula` values are the authored fixture text. The nine period-cell
allowlist spellings are the exact strings Excel 16.113.2 wrote on the SDK
package. They are accepted if Desktop emits them again. A prepared run has an
empty formula-diff list.

```sh
python3 tools/canonical_excel_receipt.py \
  --fixture fixtures/operating-scenario.workbook.json \
  --output-dir receipts/canonical-operating-scenario

python3 tools/canonical_excel_receipt.py \
  --fixture fixtures/operating-scenario.workbook.json \
  --output-dir receipts/canonical-operating-scenario-excel \
  --excel
```

The second command is the Desktop oracle. It needs macOS and Excel. The red-flag
contract still blocks on `array_spill` before Excel opens, so a prepared or
scenario-only `observed` receipt does not pass Spec 2.

The [delivery record](toolkit-delivery.md) tracks that gate separately from SDK
regression and installed-package tests. A failed automation attempt leaves the gate
outstanding; matching Python and Rust results cannot substitute for it.

## Sharing observation evidence

Public evidence should contain synthetic inputs, formula text, expected and
observed results, Excel version, relevant calculation settings, and an outcome.
Keep machine paths, account names, unrelated workbook names and workbook author
metadata out of published receipts. Review any Excel-generated file's package
metadata before adding it to the repository. Preserve the original receipt
locally and publish a separately sanitized summary so the observation is still
traceable without exposing the machine that produced it.
