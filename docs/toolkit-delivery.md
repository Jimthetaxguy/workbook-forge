---
author: Codex
created: 2026-09-28
agent: codex/Codex
date: 2026-09-28
type: implementation-record
task: Deliver the programmable workbook toolkit
status: implementation-verified-excel-acceptance-outstanding
summary: Independent Python and Rust agent SDK operations verified by contract tests, installed packages and a live discovery-led workbook task; full Excel acceptance remains outstanding.
next_steps: [Complete live Excel acceptance, review and integrate the feature branch, expand agent tasks using concrete workbook cases]
remaining: [Full live Excel scenario and generated-file roundtrip acceptance, wider optional native-wheel platform coverage]
open_questions: []
---

# Workbook toolkit delivery

This living record tracks the approved first delivery. Storage, distributed
collaboration, general structural editing, and a full grid application remain
future work; the model is an in-process computation library, not a database.

The product direction is **an SDK for Excel in agent-ready formats**, as clarified
by James. Workbook primitives and independent language implementations provide
the foundation. The agent interface direction below defines how to expose those
capabilities without making unsupported Excel or agent-integration claims.

## Agent interface direction

Agents should be able to discover supported workbook operations, obtain enough
context for a task, execute typed changes or calculations, and return a useful
Excel artifact with an explanation of the result. Preserve formulas, references,
types, source locations and unsupported-feature evidence alongside values.
Application business meaning comes from explicit bindings and author metadata;
colors and cell position alone do not establish it.

The current foundation includes independent Python/Rust SDKs, versioned workbook
JSON, named input/output bindings, bounded atomic edits, inspection reports,
revisioned calculation reports, a JSON CLI and XLSX adapters. The agent layer now
implements the following contracts; see [agent protocol](agent-protocol.md) for
exact schemas, pagination, provenance and error semantics:

| Contract | Required evidence |
| --- | --- |
| Discoverable operations | Nine operations with versioned input/output schemas, side-effect classifications, examples and limits; shared schemas are checked across both packages |
| Focused workbook context | Sheet/range/output-dependency views, 100-cell maximum pages, revision-checked continuations and explicit diagnostic/dependency clipping |
| Explained results and changes | Source formulas, input bindings and cell provenance; imported caches remain separate; preview_inputs shows before/after results without mutation |
| SDK-backed agent tools | Independently implemented adapters and JSON-lines runners in both languages; Python callable dictionaries can be registered with agent frameworks; MCP remains future work |

The implemented adapters reuse each language's workbook engine and XLSX adapter.
The deterministic replay in examples/agent_scenario.py reproduces operation
results through the SDK. Agent reasoning is never substituted for spreadsheet
calculation. This script is labeled separately from live-agent evidence.

Acceptance: given the operating scenario and explicit bindings, an agent
discovers the supported inputs, changes unit price to 25, obtains revenue 9250
and profit 3290, explains their source formulas and revision, and exports the
editable workbook. The unsupported variant must produce a structured reason
when an output depends on unsupported content while retaining that content in
permitted exports. Repeat the operation sequence against both language engines.

This direction does not select a durable storage format, live-data architecture,
collaboration mechanism or full grid interface. Those retain their existing
evidence gates. Complete live Excel acceptance remains a separate requirement
from the agent-tool demonstration.

## Live agent acceptance

On 2026-09-28, an independent Codex consumer used the actual Python JSON-lines
runner with a synthetic imported workbook. It learned operations from discover
and bindings from describe, without reading implementation code, documentation
or the binding file. It chose ten requests to inspect the profit dependency
closure, preview price 25, apply the input at revision 0, calculate and explain
revision 1, and export a new workbook. The same ten requests succeeded against
the standalone Rust runner.

Both returned revenue **9250**, profit **3290**, and break-even units **1000/17**.
The explanation exposed the contributing cells and formulas; the old imported
profit cache of 1440 remained separate from the calculated 3290. Preview left
the original revision unchanged. Both exports reopened through the SDK with
matching supported content and the unrelated unsupported LET formula intact.
Raw package preservation is covered by separate tests, not this consumer trace.

Local evidence is retained in .verification/agent-sdk-live/consumer-report.md,
consumer-transcript.json and the two output subdirectories. This is one actual
agent acceptance exercise, not a general model benchmark. Opaque sheet IDs,
optional null cache fields, evaluation counts, diagnostic prose and ZIP byte
sizes differed; business results and operation semantics agreed.

## Formula-aware XML extraction

Both SDKs now expose namespace-aware XML parsing and structural-path primitives,
used by the existing workbook readers and the new extraction API. Ten catalogued
patterns expose cells, formulas, shared strings, names, tables, table columns and
formulas, validations, merged ranges and column metadata. Records retain package
part, XML path, worksheet/cell location, attributes and original text. Supported
formula syntax reuses the independent ASTs for references and function mappings
from the existing 521-function inventory and implementation status catalog.

Shared-formula expressions are reconstructed for inspection using their master
and copy offsets. Original text and caches remain separate; grouped calculation
and edit restrictions stay active. Invalid groups yield diagnostics rather than
guessed expressions. Import and extraction now consistently reject duplicate
primary scalar payloads and nested XML inside formula/value nodes. Exact paths
exclude extension decoys; invalid indices/scopes and ambiguous table ownership
are refused. See [extraction contract](extraction-patterns.md) and its linked
schema for limits and deliberate boundaries.

The final extraction checkpoint passes **640 Python tests and 78 Rust tests**,
including 82 actual Python/standalone-Rust contract checks and 39 focused Python
extraction tests. Rust 1.88, full formatting/check/Clippy, compilation, policy and
license gates pass. Pure and optional native wheels pass the expanded installed
smoke checks outside the checkout on Python 3.12/3.14. Extraction itself is
independent in each language; the native wheel's extraction API remains Python.
The packaged Rust extractor is exercised by an external consumer without Python.
No dependencies were added. The scenario produces matching records for 12
formulas and 3 validations; local evidence is retained in .verification/xml-engine
and .verification/xml-packages. No broader Excel compatibility is inferred.

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
- [x] Agent SDK: discovery, bounded context, preview/change/explanation/export
  operations in both languages, shared contract tests and live agent acceptance
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
| Agent adapters | `workbook_forge.agent.AgentWorkbook`, `workbook_forge::agent::AgentWorkbook` |
| Agent transports | `workbook-forge agent`, Rust `agent_workbook` example |
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
The clarified independent implementation checkpoint passed 375 Python tests and
62 Rust tests, including seven actual Rust-process/Python XLSX interchange checks.
The agent SDK checkpoint passes **512 Python tests and 69 Rust tests**. Its 91
shared agent contract tests exercise the actual standalone Rust process without
PyO3 and validate every operation's response against the discovery schemas.
All 69 Rust tests also pass with the declared minimum compiler, Rust 1.88.
The final gate ran with a fresh checkout-local Rust target directory after a
stale cached library caused an unresolved module error. The previous target
was preserved; package verification now uses a separate target directory.

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
catalogs, expressions, CLI, results, agent discovery/preview/edit/explain/export,
JSON-lines transport and generated XLSX reimport. All four installations pass.
The optional
binary wheel verified here is macOS arm64; other native platforms are unverified.
The packaged Rust crate passes the original independent generation workflow and
an external consumer that replays the live agent's ten requests without Python.
Its export reopens successfully through both the packaged Rust runner and the
installed pure Python package, retaining the unsupported formula. The crate
contains the agent source, embedded discovery schemas and runner example, with
no build directories. Wheels and source distributions also contain the required
agent module and catalog. Receipts are retained under
.verification/agent-sdk-packages; its rust-consumer-path.txt identifies the
external temporary consumer.
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

Official PyPI and crates.io registry checks were repeated at 2026-09-28 18:10 UTC
after the XML extraction implementation. All 15 packages in
`requirements-dev.lock` are current stable non-yanked releases. All 32 locked
Rust package/version pairs are current within their upstream compatibility lines;
31 are also the globally latest stable versions. Syn 2.0.119 remains required by
the latest pyo3-macros and pyo3-macros-backend 0.29.2; Syn 3.0.6 is already used
by the dependency branch that supports it. No version change was needed. The
exact response-derived receipt is .verification/xml-engine/dependency-freshness.json. Direct Rust dependencies
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

### 2026-09-28 — codex/Codex — formula-aware XML extraction

Implemented independent namespace/path parsing primitives and XLSX extraction
engines in Python and Rust, with catalog-derived formula mappings, shared-formula
inspection, bounded records and source provenance. Existing importers reuse the
new primitives. Independent review closed ambiguous scalar XML, negative-index,
invalid-scope and shared-range consistency defects. Full gates pass at 640 Python
and 78 Rust tests; installed resources and external consumers are verified.
The first delivery's Excel Desktop acceptance remains outstanding.

### 2026-09-28 — codex/Codex — independent agent SDK implementation

Added the nine-operation discovery contract, paged dependency context, calculated
provenance, input previews, atomic revision-checked mutations and constrained
exports to both independent language SDKs. Shared conformance tests cover schema
validity, error codes, pagination, request/response limits, imported-content
restrictions and preservation. Malformed/oversized JSON lines recover without
executing a discarded fragment. Full gates pass with 512 Python and 69 Rust
tests; fresh installed packages and the live discovery-led consumer pass.
No dependencies were added. The feature branch remains a draft delivery
candidate pending the separate live Excel edit/save/reimport acceptance.

### 2026-09-28 — codex/Codex — agent-ready Excel SDK framing

Updated the product purpose and vocabulary to reflect James's SDK direction.
Recorded the existing structured interfaces separately from the next operation
schema, focused-context, provenance and real-agent acceptance work. No new tool
server, operation schema or calculation behavior is claimed by this documentation
change. Independent Python and Rust implementations remain required.

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
