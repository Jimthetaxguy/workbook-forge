# Workbook Forge

**Workbook Forge is an SDK for Excel in formats agents can use.** Its goal is to
make supported workbook data, formulas, dependencies, presentation, and business
inputs available as structured objects that agents can inspect, calculate,
compose, and deliver back as editable Excel workbooks.

The SDK implements spreadsheet primitives and workbook calculation independently
in Python and Rust. Each language has its own formula evaluator, typed
expressions, workbook validation, dependency calculation, editing sessions, and
conservative `.xlsx` reader, generator, and patch writer.

The Python distribution, import package, and Rust crate use the consistent name `workbook_forge`.

The enhancement connects previously separate formula evaluators into complete
workbook workflows: build sheets and named inputs, calculate dependent outputs,
change inputs safely, and exchange supported workbook content with Excel.
The operating scenario demonstrates the behavior with public synthetic data:

| Named output | Unit price 20 | Unit price 25 |
| --- | ---: | ---: |
| Total revenue | 7,400 | 9,250 |
| Total profit | 1,440 | 3,290 |
| Break-even units per period | 83.3333 | 58.8235 |

Each language computes those results using its own code. Shared test data checks
agreement and established expected values; it does not replace either implementation.

## What agent-ready means

The product contract is to preserve workbook meaning across three surfaces:

| Surface | Purpose | Current status |
| --- | --- | --- |
| Python and Rust SDKs | Construct and execute workbook operations through typed primitives | Independent implementations for the supported profile |
| Structured interchange and reports | Give agents cell identities, formulas, named bindings, constraints, calculated values, revisions, and diagnostics | Versioned workbook JSON, inspection/calculation reports, and a JSON CLI exist |
| Editable Excel files | Exchange workbooks with people and existing Excel workflows | Bounded generation/import/export exist; complete live Excel roundtrip acceptance remains outstanding |

The agent SDK exposes nine schema-described operations through independent
Python and Rust adapters: discover, describe, read, calculate, explain,
preview_inputs, set_inputs, edit, and export. Focused views page through a sheet,
range, or output's dependency closure. Previews calculate before/after results
without mutating the session; edits and exports require an expected revision.
Explanations retain source formulas and distinguish calculated values from
imported caches. Application names such as `unit_price` remain explicit bindings
supplied by the author or caller.

Both implementations provide a JSON-lines tool transport, and Python exposes
the same operations as callable dictionaries for agent-framework integration.
An MCP server and automatic business meaning inference remain future work.

The core promise is a workbook's supported meaning and executable behavior,
with clear reports for features that can only be preserved. JSON, human-readable
views, and tool calls are interfaces to that meaning, not separate calculation
implementations. See the [agent interface direction](docs/toolkit-delivery.md#agent-interface-direction)
and the [versioned operation contract](docs/agent-protocol.md) for the interface
and its acceptance evidence.

## Agent SDK quick start

After installing the Python package, an agent host can register the schemas from
`operation_catalog()` and route chosen calls through one owned adapter:

```python
from workbook_forge.agent import AgentWorkbook, operation_catalog
from workbook_forge.toolkit import operating_scenario

tools = operation_catalog()["operations"]
workbook = AgentWorkbook(operating_scenario(), output_dir="exports")
context = workbook.call("read", {"output": "profit", "limit": 10})
proposal = {"values": {"unit_price": 25}, "expected_revision": 0}
preview = workbook.call("preview_inputs", proposal)
assert preview["ok"] and not preview["result"]["applied"]
changed = workbook.call("set_inputs", proposal)
assert changed["ok"]
result = workbook.call("explain", {"output": "profit", "expected_revision": 1})
assert result["ok"] and result["result"]["value"] == 3290
exported = workbook.call("export", {"filename": "scenario.xlsx", "expected_revision": 1})
assert exported["ok"]
```

For a process boundary, start either independent runner and write one JSON
request per line to stdin. Replies carry the call ID, revision, and either a
structured result or an error. EOF closes the transient session.

```sh
workbook-forge agent --scenario --output-dir exports-python
cargo run --manifest-path rust/Cargo.toml --example agent_workbook -- --scenario --output-dir exports-rust
```

```json
{"id":"step-1","operation":"discover","arguments":{}}
{"id":"step-2","operation":"describe","arguments":{}}
{"id":"step-3","operation":"read","arguments":{"output":"profit","limit":10}}
```

To load a workbook, replace `--scenario` with its path and add `--bindings` with
an explicit input/output JSON file. The host selects the source and export
directory; tool calls cannot read arbitrary paths. Imported agent edits allow
existing-cell value/formula changes, and reject unsupported changes before
committing. Requests are bounded to 1 MiB, responses to 256 KiB, and context
pages to 100 cells, with explicit continuation and truncation metadata.

`python examples/agent_scenario.py NEW_DIRECTORY` records a deterministic replay
and verifies the exported workbook by reimport. It is a reproducible SDK example,
not an LLM agent. Live-agent evidence is tracked separately in the delivery record.

## Programmable workbooks

The toolkit supports three connected workflows: extract a supported model from
Excel, build and execute a tool without Excel, and produce an editable workbook.
Python runs its own implementation by default and does not call Rust. The Rust
crate independently implements the same model and calculation contract. Shared
cases compare their behavior. Application input names bind to cells and are
distinct from Excel defined-name formulas.

Install the pure Python package with `pip install .`; no Rust toolchain or native
extension is required. Run the same model through Python or the command line:

```python
from workbook_forge.toolkit import operating_scenario
from workbook_forge.xlsx import export_xlsx, import_xlsx

model = operating_scenario()
model.set_inputs({"unit_price": 25})
result = model.calculate()
print(result["outputs"])  # revenue 9250, profit 3290, break_even_units 1000/17
export_xlsx(model, "scenario.xlsx", report=result)  # requires a new path

definition = model.to_dict()
restored = import_xlsx("scenario.xlsx", inputs=definition["inputs"], outputs=definition["outputs"])
assert restored.calculate()["outputs"] == result["outputs"]
```

```sh
workbook-forge scenario --inputs '{"unit_price":25}' --xlsx scenario.xlsx
workbook-forge inspect scenario.xlsx
workbook-forge capabilities
cargo run --manifest-path rust/Cargo.toml --example operating_scenario
cargo run --manifest-path rust/Cargo.toml --example xlsx_scenario -- generate rust-scenario.xlsx
cargo run --manifest-path rust/Cargo.toml --example xlsx_scenario -- edit rust-scenario.xlsx rust-updated.xlsx 25
```

Python typed expressions expose separate copy anchors and use the Python parser:

```python
from workbook_forge.expressions import CellReference, Expression

price = Expression.reference(CellReference(1, 2, "Assumptions", True, True))
quantity = Expression.reference(CellReference(2, 2))
formula = price * quantity
print(formula.copy(rows=1).formula)
```

`WorkbookModel` accepts a versioned interchange document or a sequence of sheet
names, explicit input/output bindings, and atomic cell edits. `to_dict()` returns
a detached snapshot. Calculation reports identify their model revision,
evaluated cells, diagnostics, and stale status. Incremental sessions operate in
process; there is no durable database or distributed synchronization promise.

An optional PyO3 bridge permits explicit comparison with the Rust implementation.
Install development requirements first, then build the bridge with
`WORKBOOK_FORGE_BUILD_NATIVE=1 pip install --no-build-isolation -e .`.
Select it with `operating_scenario(backend="rust")`,
`WorkbookModel(..., backend="rust")`, or `workbook-forge scenario --backend rust`.
Selecting an unavailable Rust bridge raises an explicit error; Python never
silently substitutes Rust for its own implementation.

Rust's `workbook_forge::xlsx` module provides its own import/export workflow and
uses only low-level ZIP/XML libraries. The Rust examples run without Python.
Fresh generation supports sparse authored models in either language. Rust's
preservation-aware imported editing currently requires existing cells; inserting
new cells into imported packages is explicitly rejected.

Imported packages retain an immutable baseline. Supported edits and results
are patched into a private reconstruction, preserving untouched part payloads.
Unsupported formulas outside the selected output closure remain preserved;
dependencies on unsupported content fail explicitly. Export refuses array spill
placement, stale results, and unsupported changes to imported structure/styles.
Input bindings are explicit application metadata and must be supplied again
when importing an XLSX. Nonpositive scenario contribution margin produces
`#N/A` for break-even units. Imported 1904-date workbooks preserve formula text
but cannot receive new formulas or calculated caches through this toolkit.

See [toolkit delivery](docs/toolkit-delivery.md), the
[capability map](catalog/workbook-capabilities.json), and
[Excel observations](docs/excel-observations.md) for supported boundaries and evidence.

The Microsoft function index currently contributes 521 named entries across its published categories. That inventory is a vocabulary and discovery aid, not a claim that every function has a complete behavioral specification or is supported by the evaluator. Availability labels are preserved when the source exposes them; desktop availability can vary by Excel build and rollout.

## Project map

- `catalog/function_inventory.json` — dated snapshot of function names, source categories, and linked provenance.
- `catalog/formulas.json` — implementation statuses, source links, and detailed semantic specs for the current evaluator slices.
- `catalog/open_source_patterns.json` — permissively licensed (no copyleft) architecture references, license evidence, adaptation decisions, and provenance; no source code is copied.
- `catalog/open-source-patterns.schema.json` — schema for the permitted-source pattern catalog.
- `catalog/formula-support.schema.json` — JSON Schema for the support catalog and semantic glossary.
- `fixtures/formula-cases.jsonl` — language-neutral expected outcomes loaded by both evaluators.
- `python/workbook_forge/` — independent standard-library formula and workbook engines, typed expressions, catalog API, and `.xlsx` adapter.
- `rust/` — standalone Rust formula evaluator, typed model, calculation sessions, independent `.xlsx` adapter, and examples.
- `native/` — optional PyO3 bridge to the Rust engine; pure Python does not require it.
- `catalog/workbook-capabilities.json` — primitive support across read, construct, calculate, transform, and export.
- `catalog/agent-operations.json` — versioned operation descriptions and request/response schemas; a checked identical copy is embedded in the Rust crate.
- `docs/agent-protocol.md` — agent operation semantics, provenance, pagination, errors, and transport limits.
- `docs/behavior-profiles.md` — behavior notes and Workbook Forge profiles by function family, plus workbook adapter limits.
- `docs/run-history.md` — what each autoresearch run added, with the counts at that time.
- `.autoresearch/` — loop configuration (`config.json`), the accepted-run ledger (`state.json`), and Jev advisory receipts.
- `_working-files/` — dated checkpoint and review notes.

## Status

Formula coverage retains the Run 24 checkpoint (commit `42126ef`); the workbook
toolkit adds model and adapter capabilities without expanding the function count.

| Measure | Count |
|---|---|
| Functions implemented and conformance-tested in both engines | 115 |
| Inventory functions that are still catalog-only | 406 of 521 |
| Shared fixture cases | 1,371 |
| Detailed semantic specs | 85 |
| Formula and compatibility source records | 110 |
| Direct Excel Desktop observations | 3 targeted formula checks; full workbook roundtrip outstanding |

The Python and Rust evaluators currently cover 115 functions, including scalar arithmetic, references, text and logical functions, the periodic financial functions FV, PV, PMT, NPER, IPMT, PPMT, CUMIPMT, CUMPRINC, SLN, SYD, DB, DDB, VDB, AMORLINC, and AMORDEGRC (deprecated legacy), dates, lookups, `TEXTBEFORE`/`TEXTAFTER`, error predicates (`ISNA`, `ISERR`, `ISERROR`), `NA`, `COUNTBLANK`, six conditional aggregations (`COUNTIF(S)`, `SUMIF(S)`, `AVERAGEIF(S)`), conditional extrema (`MINIFS`, `MAXIFS`), date/time functions (`HOUR`, `MINUTE`, `SECOND`, `TIME`, `WEEKDAY`, `WEEKNUM`, `ISOWEEKNUM`, `DAYS360`, `YEARFRAC`, `WORKDAY`, `NETWORKDAYS`, `WORKDAY.INTL`, `NETWORKDAYS.INTL`, `COUPDAYBS`, `COUPDAYS`, `COUPDAYSNC`, `COUPNCD`, `COUPNUM`, `COUPPCD`), and scalar rounding/remainder functions (`EVEN`, `ODD`, `INT`, `TRUNC`, `ROUND`, `ROUNDUP`, `ROUNDDOWN`, `MOD`, and `QUOTIENT`), plus parity predicates (`ISEVEN` and `ISODD`), plus factorial and combinatorics (`FACT`, `FACTDOUBLE`, `COMBIN`, `COMBINA`, `PERMUT`, and `PERMUTATIONA`), plus integer math (`GCD` and `LCM`), and bounded array generation with `SEQUENCE`, row/column ordering with `SORT`, and stable distinct-row/column selection with `UNIQUE`.

Catalog status means:

- `catalogued`: included in the source-derived inventory.
- `parsed`: syntax is recognized, but evaluation is incomplete.
- `evaluated`: implemented in the language engine, but lacks a shared golden fixture.
- `conformance-tested`: both engines pass at least one shared golden fixture for the feature/function.

`conformance-tested` means fixture parity only; it is not a claim of full Excel compatibility. `semantic_specs` records arguments, return shape, behavior, known implementation limits, and source IDs where that detail has been researched.

## Evaluator resource limits

Both evaluators cap formulas at 8,192 UTF-16 code units and function nesting at 64 levels, matching documented Excel limits. Workbook Forge also caps parenthesis nesting at 96 levels and wildcard matching at 5,000,000 matching-state steps per formula evaluation. The latter two are local safety profiles. Long unary, postfix, and left-associated arithmetic chains are processed iteratively. Wildcard literal comparisons use per-character Unicode lowercase expansion; locale-specific Excel collation remains unmodeled. These limits and profiles protect evaluator resources and keep the engines aligned; shared fixtures establish parity, not Excel compatibility.

## Python quick start

From the repository root, install the package with `python -m pip install .`, then:

```python
from workbook_forge import evaluate

result = evaluate("=IF(A1>2, SUM(A1:A3), 0)", {"A1": 3, "A2": 4, "A3": 5})
print(result)  # 12
```

For formulas that return rectangular arrays, use the shape-preserving API:

```python
from workbook_forge import ArrayValue, evaluate_result

result = evaluate_result("=SEQUENCE(2, 3, 10, 10)")
assert isinstance(result, ArrayValue)
print(result.shape, result.rows)  # (2, 3), ((10.0, 20.0, 30.0), (40.0, 50.0, 60.0))
```

FILTER works with a vertical row mask or horizontal column mask. Binary comparisons and arithmetic build elementwise masks, with scalar operands broadcast across the array shape:

```python
result = evaluate_result(
    '=FILTER(A1:B3,(B1:B3="apples")*(A1:A3="east"),"")',
    {
        "A1": "east", "B1": "apples",
        "A2": "west", "B2": "apples",
        "A3": "east", "B3": "pears",
    },
)
print(result.rows)  # (("east", "apples"),)
```

`evaluate_result` also materializes a top-level cell range as an `ArrayValue`. The scalar `evaluate` API returns `#VALUE!` for top-level arrays or ranges. FILTER, SORT, and UNIQUE return bounded, shape-preserving formula results; worksheet spill placement remains outside the evaluator.

To inspect the function glossary:

```python
from workbook_forge.catalog import function_status, implementation_patterns, lookup_function

print(lookup_function("XLOOKUP")["category"])
print(function_status("SUM", "python"))  # conformance-tested

patterns = implementation_patterns()
print(patterns["patterns"][0]["id"])
```

The `.xlsx` adapter uses only Python's standard library:

```python
from workbook_forge.workbook import Workbook

with Workbook.open("source.xlsx") as book:
    print(book.get("Sheet1", "A1"))
    book.set_value("Sheet1", "B1", 12)
    book.set_formula("Sheet1", "C1", "=SUM(A1:B1)")
    book.save_as("updated.xlsx")

with Workbook.open("source.xlsx") as book:
    book.calculate_cells_to("calculated.xlsx", ["Summary!B4"])
```

`calculate_cells_to` writes a new workbook after calculating the requested formula and dependencies. It refuses the operation if the dependency closure crosses the bounded evaluator's supported features.

Package limits, dependency-closure bounds, and the adapter's supported scope are described in [`docs/behavior-profiles.md`](docs/behavior-profiles.md#workbook-adapter).

## Behavior and history

[`docs/behavior-profiles.md`](docs/behavior-profiles.md) summarizes detailed behavior by function family, including every Workbook Forge profile that still needs an Excel check. [`docs/run-history.md`](docs/run-history.md) records what each run added.

## Verification

```sh
python3.13 -m pytest -q
python3.13 -m compileall -q python
(cd rust && cargo fmt --check && CARGO_TARGET_DIR="$PWD/target" cargo check --locked && CARGO_TARGET_DIR="$PWD/target" cargo test --locked && CARGO_TARGET_DIR="$PWD/target" cargo clippy --all-targets --locked -- -D warnings)
```

The Rust core uses Serde for the typed interchange model and quick-xml/zip for
low-level file decoding. The optional Python extension uses PyO3. The complete
Python runtime uses only the standard library. Exact dependency versions,
permissive licenses, and source checksums are
recorded in `catalog/dependency-licenses.json` and checked against both lockfiles.
The complete development gate compares both independent engines. Install the
verified development lock and explicitly build the optional bridge first:

```sh
python3.13 -m pip install -r requirements-dev.lock
WORKBOOK_FORGE_BUILD_NATIVE=1 python3.13 -m pip install --no-build-isolation -e .
python3.13 -m pytest -q
WORKBOOK_PYTHON=python3.13 bash tools/verify_toolkit.sh
```

The Python tests need the `test` extra in `pyproject.toml`, including build tools
for the dependency-license checks. Pure Python tests also run without the bridge;
only cross-engine comparisons are skipped then. `rust/Cargo.toml` declares Rust 1.88 as the
minimum supported version; the suite passes on 1.88.0 and 1.98.1. Build the optional
native extension when running comparisons between language engines.
Installed-wheel smoke checks must run outside the checkout:

```sh
python tools/verify_installed.py /new/output/directory --require-pure
```

For an optional native wheel, select `--backend rust` instead of `--require-pure`.

Dependency updates target current stable releases. Refresh the development lock
with `uv pip compile pyproject.toml --extra test --upgrade --generate-hashes -o requirements-dev.lock`,
refresh both Cargo lockfiles, review changed licenses, and rerun the complete gate.
The lockfiles identify tested versions; minimum Python/Rust language versions
describe compatibility and do not mean old dependency packages are selected.

## License and source policy

Original project code is MIT. Workbook Forge must stay usable in enterprise settings, so third-party components, whether used at runtime or only in development and including transitive dependencies, must carry a permissive open-source license with no copyleft terms. The accepted SPDX licenses are MIT, MIT-0, Apache-2.0, BSD-2-Clause, BSD-3-Clause, 0BSD, ISC, Zlib, PSF-2.0, Unicode-3.0, Unicode-DFS-2016, BSL-1.0, CC0-1.0, and Unlicense. A dual-licensed package qualifies when one of its options is on that list. Copyleft licenses (GPL, LGPL, AGPL, MPL, EPL, EUPL, CDDL) and source-available licenses (SSPL, BUSL) are excluded. `python/tests/test_project_policy.py` checks the installed Python dependency tree against this list, and it fails if a Rust crate is added without a license review. The original Python formula evaluator remains standard-library-only; the native workbook engine and bridge use the reviewed dependencies recorded with the catalog. Microsoft documentation is linked as provenance; the catalog records names and categories rather than copying function descriptions. Open-source implementation patterns are separately catalogued with license evidence and an explicit adopt/defer decision; no third-party implementation code is copied. The Python wheel installs catalog JSON into `share/workbook_forge/catalog` and the API reads the source-tree copy during development. Jev is an advisory development-time critic and is not part of either package runtime.
