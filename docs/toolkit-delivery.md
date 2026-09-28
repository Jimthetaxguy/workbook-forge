---
author: Codex
created: 2026-09-28
agent: codex/Codex
date: 2026-09-28
type: implementation-record
task: Deliver the programmable workbook toolkit
status: implemented-with-excel-acceptance-outstanding
summary: Rust-owned workbook toolkit implemented and locally verified; full live Excel roundtrip acceptance remains outstanding.
next_steps: [Resolve Excel automation and observe the generated scenario after an input edit and save]
remaining: [Full live Excel scenario and generated-file roundtrip acceptance, wider platform wheel coverage]
open_questions: []
---

# Workbook toolkit delivery

This living record tracks the approved first delivery. Storage, distributed
collaboration, general structural editing, and a full grid application remain
future work; the model is an in-process computation library, not a database.

## Ownership and boundaries

Rust owns typed formula transformations, the sparse workbook model, validation,
calculation, and revisioned edit sessions. Python owns authoring convenience,
CLI presentation, and OOXML import/export. The original Python evaluator remains
independent; its public scalar and array APIs retain their existing behavior.

The native Python extension exchanges bounded model/report batches with Rust.
Calculation releases the interpreter and runs against a Rust-owned snapshot.
The standalone Rust library has no dependency on Python. Setuptools retains the
existing installed catalog layout; setuptools-rust builds the PyO3 extension.

Imported package content is an immutable baseline. Edits affect the native model;
preservation-aware export patches a private reconstruction of that baseline.
Imported formula caches are evidence about a file, never calculation inputs.
Application input names bind to explicit cells and do not introduce Excel
defined-name evaluation.

## Acceptance model

The operating scenario has Assumptions and Forecast sheets. Unit price is 20,
unit variable cost is 8, fixed cost per period is 1000, and period volumes are
100, 120, and 150. Independently derived totals are revenue 7400 and profit
1440; break-even volume is 1000 / (20 - 8). Changing price to 25 produces revenue
9250, profit 3290, and break-even volume 1000 / 17. A nonpositive contribution
margin returns `#N/A` because no finite break-even volume exists in this model.
Input/output bindings, copied formulas, formatting, and bounded validation travel
with the native model. XLSX imports require explicit reattachment of bindings.

`python examples/operating_scenario.py NEW_OUTPUT.xlsx --with-unsupported`
also generates a separate variant containing `LET(x,1,x)`, imports it, edits a
supported input, and exports a preservation-aware result. Its assertions check
that the formula survives unchanged and selecting it as an output produces a
diagnostic. It does not expand Forge's supported functions.

## Delivery checklist

- [x] A: capability map, synthetic cases, independent Excel observation path
- [x] B: typed primitives and minimal construct/calculate/export/reimport proof
- [x] C: full/incremental calculation, atomic edits and coherent snapshots
- [x] D implementation: extraction, fresh generation and preservation-aware export
- [ ] D acceptance: generated workbook opens without repair, edited input
  recalculates in Excel, saved file reimports with matching results
- [x] E: installed Python/Rust/CLI examples and measured parallel calculation
- [x] Existing regressions, policy/license checks and independent final review

The implementation is a reviewable delivery candidate. The complete milestone's
Excel acceptance criterion has not passed; three direct formula observations
are narrower evidence. See [Excel observations](excel-observations.md).

## Public surfaces and retained limits

| Surface | Entry point |
| --- | --- |
| Rust model, expression, session and calculation | `workbook_forge::toolkit` |
| Python native authoring | `workbook_forge.toolkit.WorkbookModel` |
| Python typed expression construction | `workbook_forge.expressions` |
| Excel extraction and production | `workbook_forge.xlsx.import_xlsx` / `export_xlsx` |
| CLI | `workbook-forge scenario`, `inspect`, `run`, `capabilities` |
| Capability report | `workbook_forge.catalog.workbook_capabilities()` |

The Python facade exchanges detached batches with Rust. This keeps one owner for
revisions and mutations without exposing XML internals to the engine. Legacy
Python imports still work when the extension is absent; new native APIs raise
`NativeUnavailableError` with installation guidance.

- A session applies a bounded batch atomically and rejects an expected-revision
  mismatch. Reports carry their calculation revision; obsolete work cannot
  publish into a newer session revision. Formula replacement updates dependency
  invalidation, and unchanged branches reuse results.
- Workbook formulas reuse the existing Excel-oriented evaluator. They do not
  become host-language arithmetic. Imported formula text remains available;
  unsupported syntax is rejected only when required by selected outputs.
- Arrays are bounded rectangular calculation results. Worksheet spill placement
  and export of array results remain unsupported.
- Import retains styles, name metadata, table boundaries, caches and opaque
  package payloads. A cache never substitutes for unsupported formula execution.
  Grouped/table formula result regions are protected from edits.
- Fresh macro-free transitional XLSX supports basic formats, font/fill/alignment,
  widths and custom stop validations. Text choices are case-sensitive checks,
  not a promise of a dropdown widget. Excel's behavior for pasting over validation
  is outside the standalone input-validation contract.
- Preserved imports reject structure/style changes and new binding constraints
  that differ from the retained validation. The 1904 date system remains
  preservation-only for formulas. Export writes a new path and validates caches
  against the current native calculation.
- Toolkit limits include 100,000 populated cells, bounded dependency expansion,
  100,000 cumulative materialized result elements and at most eight workers.
  Existing formula and OOXML package limits remain active. Serial calculation
  is the default; parallel execution is an explicit option.

## Verification record

Baseline: source main fd2c313, clean and aligned before implementation; Python
190 tests and Rust 31 tests passed during discovery. The final verification
entry point is `WORKBOOK_PYTHON=.venv/bin/python bash tools/verify_toolkit.sh`:
Python tests and compilation, Rust fmt/check/test/Clippy, native bridge
fmt/check/Clippy, catalog schemas, and exact-version dependency-license receipts.
The final integrated run passed **230 Python tests and 53 Rust tests**.
The Rust suite also passes on the declared minimum Rust 1.88.0.

Packaging checks build an sdist and an ABI3 wheel, install the wheel into fresh
Python 3.12.13 and 3.14.3 environments, and invoke `tools/verify_installed.py`
from outside the source checkout. Both verify the native extension, legacy APIs,
installed catalogs, expressions, CLI, and generated XLSX reimport. The platform
wheel verified here is macOS arm64; other platform binaries are not claimed.
The Rust crate is packaged locally and its scenario runs as a separate consumer
outside the checkout. No registry publication is part of this delivery.

Independent review led to regression coverage for unrelated oversized formulas,
blank worksheet results, preserved error codes, nonpositive break-even margin,
1904-date formula edits, and altered calculation-report caches. Incremental edit
sequences are compared with fresh full calculation, alongside targeted tests
for failed batches, concurrent snapshots and stale publication.

## Performance evidence

`cargo run --release --manifest-path rust/Cargo.toml --example calculation_benchmark`
compares one and four workers and asserts equal values and diagnostics. A local
release run recorded the following microseconds; these are observations from
one machine, not throughput guarantees.

| Workload | Formula evaluations | Serial µs | Four workers µs |
| --- | ---: | ---: | ---: |
| Sparse literals | 0 | 1,171 | 824 |
| Dense independent formulas | 1,000 | 2,350 | 2,183 |
| Chain | 999 | 2,359 | 2,320 |
| Branching | 999 | 2,217 | 3,169 |
| 64 independent array aggregations | 64 | 35,890 | 12,743 |

The expensive independent workload benefited by about 2.8×; cheap branching work
was slower. The benchmark process reported peak resident memory of 13,058,048
bytes. Serialized model/result sizes are emitted separately and are not memory
usage estimates. Optimize only after comparing on the target workload.

## Remaining acceptance and next dependency

The next required step is independent Excel observation of the actual generated
scenario: open without repair, edit price 20 → 25, calculate, save, and reimport.
The harness and synthetic cases are retained. Excel 16.113.2 produced three
live arithmetic/IF/SUM results; file-mode automation returned an unpopulated
cache set, and the complete live scenario encountered Apple-event error `-50`.
These failures do not count as a passing roundtrip.

After that acceptance gate, choose richer primitives from concrete workbook
cases. Persistence requires measured recovery/workload requirements first;
collaboration requires actual conflict cases. Neither has been selected here.

## Activity

### 2026-09-28 — codex/Codex

Implemented and integrated separate Rust core, Python/Excel adapter, observation,
and packaging lanes on `agent/codex-workbook-toolkit`. Independent reviews
covered calculation semantics and adapter preservation. Existing evaluator
fixtures remain regression gates. Live GitHub metadata confirmed public
visibility; no visibility change was made.
