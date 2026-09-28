---
author: Codex
created: 2026-09-28
agent: codex/Codex
date: 2026-09-28
type: implementation-record
task: Deliver the programmable workbook toolkit
status: implementation-verified-excel-acceptance-outstanding
summary: Independent hand-written Python and Rust workbook and XLSX implementations verified from installed packages, with refreshed stable dependencies.
next_steps: [Complete live Excel acceptance, review and integrate the feature branch]
remaining: [Full live Excel scenario and generated-file roundtrip acceptance, wider optional native-wheel platform coverage]
open_questions: []
---

# Workbook toolkit delivery

This living record tracks the approved first delivery. Storage, distributed
collaboration, general structural editing, and a full grid application remain
future work; the model is an in-process computation library, not a database.

## Ownership and boundaries

The clarified requirement is two full, hand-written language implementations.
Python and Rust independently implement typed formula transformations, sparse
workbook models, validation, dependency calculation and revisioned edit sessions.
The first implementation used a Rust-owned workbook layer with a Python wrapper;
that did not satisfy this clarification and has been replaced. Existing
independent formula evaluators and public scalar/array behavior remain intact.

Python defaults to its standard-library engine. The Rust library does not depend
on Python. An optional PyO3 extension selects Rust explicitly for interoperability
and comparisons; it is not the implementation of the Python toolkit. A standard
Python installation builds a pure wheel without invoking a Rust compiler.
Setuptools retains the installed catalog layout, with setuptools-rust used only
for the explicitly requested optional bridge.

Each language also implements its own XLSX adapter. Python uses its standard
library; Rust uses ZIP and XML decoding libraries with hand-written OOXML
interpretation, validation, generation and preservation logic. Neither language
calls the other to construct, calculate, import, edit or export a workbook.

Imported package content is an immutable baseline. Edits affect one engine's model;
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
with each language's model. XLSX imports require explicit reattachment of bindings.

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
| Independent Rust Excel extraction and production | `workbook_forge::xlsx::{import_xlsx, export_xlsx, ImportedWorkbook}` |
| Independent Python authoring and calculation | `workbook_forge.toolkit.WorkbookModel` (default `backend="python"`) |
| Python typed expression construction | `workbook_forge.expressions` |
| Independent Python Excel extraction and production | `workbook_forge.xlsx.import_xlsx` / `export_xlsx` |
| CLI | `workbook-forge scenario`, `inspect`, `run`, `capabilities` |
| Capability report | `workbook_forge.catalog.workbook_capabilities()` |

Each engine owns its revisions and mutations. Shared interchange data and
differential tests establish parity without reusing executable implementations.
Python operations work when native imports are blocked; explicitly selecting an
unavailable Rust bridge raises `NativeUnavailableError`.

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
  against the selected engine's current calculation.
- Rust imported edits require existing cells. New-cell insertion, imported
  style/layout/validation changes, and general structure edits are refused.
  Its file profile is UTF-8 transitional XLSX, excluding ZIP64, multivolume
  archives and directory entries. Both adapters preserve unsupported content
  within their accepted package profiles; their reports expose narrower limits.
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
The initial Rust-owned delivery passed 230 Python tests and 53 Rust tests.
The clarified independent implementation passes **375 Python tests and 62 Rust
tests**, including seven actual Rust-process/Python XLSX interchange checks.
Rust's complete suite also passes on the declared minimum Rust 1.88.0.

All 1,371 fixtures are exercised through each workbook engine: 1,368 retain
their expected results, while three oversized cases receive the documented
workbook resource-limit rejection in both engines. Existing direct evaluator
fixtures retain their outcomes. Native-import-blocked subprocess checks prove
Python authoring, calculation, expression copying and XLSX roundtrips work alone.

Packaging checks build a pure `py3-none-any` wheel with Rust absent from PATH,
an optional ABI3 bridge wheel, and a source distribution containing both
implementations. Fresh Python 3.12.13 and 3.14.3 environments run
`tools/verify_installed.py` outside the source checkout for both wheel variants.
The pure environments contain no extension. Checks cover legacy APIs, installed
catalogs, expressions, CLI, results and generated XLSX reimport. The optional
binary wheel verified here is macOS arm64; other native platforms are unverified.
The packaged Rust crate separately generates a workbook, edits price to 25,
exports it and inspects the result from an external consumer without Python.
No registry publication is part of this delivery.

Independent review led to regression coverage for unrelated oversized formulas,
blank worksheet results, preserved error codes, nonpositive break-even margin,
1904-date formula edits, and altered calculation-report caches. Incremental edit
sequences are compared with fresh full calculation, alongside targeted tests
for failed batches, concurrent snapshots and stale publication.
Additional independent review closed cell-shaped sheet-name parsing, phonetic
annotation extraction and incorrect selection of cell-shaped XML within opaque
extension content. Preservation tests check actual worksheet values and the
unchanged extension bytes separately.

## Dependency freshness

Official registry checks on 2026-09-28 confirmed all 15 packages in
`requirements-dev.lock` are current stable releases. Direct Rust dependencies
use Serde 1.0.229, serde_json 1.0.151, quick-xml 0.42.0 and zip 8.6.0; the optional
bridge uses PyO3 0.29.2. Python build/test dependencies include setuptools 84.0.0,
setuptools-rust 1.13.0 and pytest 9.1.1. Exact Cargo checksums and permissive-license
reviews cover 32 package/version pairs, including both upstream-required Syn
major versions. Updating a transitive crate across an incompatible major is not
forced. The Python runtime remains standard-library-only.

Version sources: [PyPI](https://pypi.org/project/pytest/),
[PyO3](https://docs.rs/pyo3/0.29.2/pyo3/),
[quick-xml](https://docs.rs/quick-xml/0.42.0/quick_xml/),
[zip](https://docs.rs/zip/8.6.0/zip/). Lockfiles record the tested versions;
minimum language versions describe compatibility.

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

The independent Python package has its own reproducible check:
`python examples/calculation_benchmark.py`. It asserts the expected evaluation
count as well as serial/parallel result equality. An installed Python 3.14.3 run
recorded these microseconds, including report conversion but excluding authoring:

| Workload | Formula evaluations | Serial µs | Four threads µs |
| --- | ---: | ---: | ---: |
| Sparse literals | 0 | 2,012 | 1,853 |
| Dense independent formulas | 1,000 | 21,058 | 19,156 |
| Chain | 999 | 18,516 | 18,743 |
| Branching | 999 | 18,000 | 18,718 |
| 64 independent array aggregations | 64 | 297,390 | 298,922 |

Peak process resident memory reached 38,780,928 bytes, including the interpreter
and earlier workloads. Threads showed no useful heavy-workload speedup on this
GIL-enabled build; serial remains the default. The Rust and Python timings use
different public-call overhead and are not a controlled language speed comparison.

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

### 2026-09-28 — codex/Codex — independent implementation correction

James clarified that each language must contain its own complete implementation.
Python's existing independent formula evaluator now has its own typed
expressions, graph calculation and sessions. Rust also has its own XLSX adapter.
Pure Python packaging is the default; native execution is explicit. Installed
pure/native Python packages and a separately packaged Rust consumer pass the
scenario checks. Registry checks refreshed the PyO3 family from
0.29.0 to 0.29.2 and confirmed all 15 locked Python development dependencies are
current stable releases. Old PyO3 license texts match the new versions byte for
byte. Declared package minimums and development lock now record the verified set.

### 2026-09-28 — codex/Codex

Implemented and integrated separate Rust core, Python/Excel adapter, observation,
and packaging lanes on `agent/codex-workbook-toolkit`. Independent reviews
covered calculation semantics and adapter preservation. Existing evaluator
fixtures remain regression gates. Live GitHub metadata confirmed public
visibility; no visibility change was made.
