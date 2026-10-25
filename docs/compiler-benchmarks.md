# Compiler benchmark contract and full-coverage backlog

Status: tested typed-IR transfer and a small executable-source compiler slice;
full source coverage and UI integration are unfinished. Contract/fixture/mapping versions: 1. Full custom
Excel coverage is the target. Current refusals below describe implementation
backlog, not permanent product exclusions.

The later [source profile 2](source-graph-v2.md) adds multi-cell dependencies,
SUM/ranges, division and typed errors across all four source routes. Numeric
profile 1 below remains a backward-compatible checkpoint; the IR benchmark's
version 1 does not describe the newer source bundle version.

## Three independently usable products

| Product | Adoption boundary | Present foundation | Missing acceptance |
| --- | --- | --- | --- |
| Standalone compiler CLI/library | Workbook to executable Rust/Python to workbook, including source-language translation; no UI/server/agent required | OOXML adapters, typed models, intake CLI; older numeric-v1 lane; graph profiles 2-5 source-translating SUM, IF, IFERROR and AVERAGE across all four source routes; packaged `workbook-forge-compile` defaults to graph-v5 | General function coverage beyond those four profiles, and wider receipts |
| Embeddable runtime engine | Independent Rust crate or Python library with versioned values, errors, shapes, dependency edits and diagnostics | Native independent evaluators and toolkit sessions, JSON-lines operations | Full language coverage, canonical/toolkit convergence, wider fidelity and conformance evidence |
| TypeScript integration and optional spreadsheet UI | A typed client/runtime interface over either engine | Existing [operation contract](agent-protocol.md) with request IDs, revisions, pagination and limits, and the typed client in [typescript/](../typescript/README.md) | Transport adapters beyond the Node JSON-lines process, and equivalent visible/edit/export acceptance tests |

Generated code may depend on a versioned Forge runtime; its dependency contract
must be recorded. Standalone means independently usable, not zero dependencies.
Tauri can host a webview and native IPC. FastAPI can host a Python server API;
it does not itself host a webview. Neither belongs in compiler/runtime core.

The TS integration should reuse one protocol with local Tauri IPC or
HTTP/WebSocket transports. A socket means a pluggable interface, not a mandatory
network socket. Plan structured cell/range addressing, load, viewport reads,
batched edits/formulas, calculate, typed errors/arrays, dependency change sets,
expected revisions, cancellation, deterministic response ordering, request IDs,
diagnostics and save/export. Keep payload bounds and version negotiation explicit.
TS should not independently reinterpret formulas by accident. The same synthetic
workbook/edit sequence must produce equivalent visible results and persisted
OOXML through both backends. Layout, styles, merges, names and tables remain
separate fidelity acceptance dimensions.

## Scope and primary evidence

XML here means SpreadsheetML/OOXML inside XLSX packaging. Excel formulas are
arbitrary expressions: a function count is only one denominator. The versioned
[coverage mapping](../catalog/compiler-coverage.v1.json) includes **all 521 rows
of the pinned 2026-09-24 inventory**, including unimplemented functions, with
both runtime stages and the existing 21 syntax-feature rows. It is not a claim
that 521 is the complete current Excel surface. Its source snapshot is hashed,
and `inventory_refresh_required` remains true. The map records generic parse
probes separately from valid argument domains, executable semantics, shared
expected fixtures, preservation and generated-source translation.

The 115 implemented names in each language are declared implementation-profile
statuses in the existing function catalog, cross-checked against Rust's explicit
115-name runtime registry. All 115 have associated cases in the shared
fixture file. The available project suite executes those fixtures, but association
or registry membership is not a complete argument-domain proof and is not a
count of independently Excel-observed functions. Numeric source profile 1 has no
function-call code generation; source profile 2 now compiles SUM as one explicitly
bounded source function, separately from the 115 runtime profiles.

The [Microsoft alphabetical index](https://support.microsoft.com/en-us/excel/excel-functions-alphabetical)
applies across Microsoft 365 and several older releases; version markers do not
establish tenant/channel/build availability. The
[MS-XLSX archive](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-xlsx/2c5dee00-eff2-4b22-92b6-0738acd4475e)
lists revision 30.0 dated 2026-10-16. The
[formula grammar](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-xlsx/3d025add-118d-4413-9856-ab65712ec1b0)
and [extension-function list](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-xlsx/5d1b6d44-6fc1-4ecd-8fef-0b27406cc2bf)
must be reconciled with that revision and a target Excel build before calling
the language inventory current. The index, grammar and archive were inspected
on 2026-10-23; the pinned inventory has not been silently refreshed.

Built-ins are enumerable for a specified catalog revision. Arbitrary UDFs,
add-ins, cube/services, external workbooks and macros need explicit runtime
bindings and effect contracts rather than invented default values. These remain
in the full product target; no external provider is contacted by this slice.
Volatile time/random functions require injected clock/seed/recalculation policy.

Microsoft's [formula documentation](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/working-with-formulas)
identifies the stored cell value as a calculation cache. Cache contents and
Rust/Python agreement are not independent Excel oracles.

## Actual paths implemented

```
OOXML -> Rust import -> toolkit JSON IR -> Rust calculation/export -> OOXML
OOXML -> Python import -> toolkit JSON IR -> Python calculation/export -> OOXML
OOXML -> Rust import -> toolkit JSON IR -> Python calculation/export -> OOXML
OOXML -> Python import -> toolkit JSON IR -> Rust calculation/export -> OOXML
```

The middle artifact is the existing typed toolkit workbook schema version 1,
not generated source and not the distinct canonical workbook model v1.
Runtime-local sheet IDs are explicitly rebound only after matching sheet names
and order, as permitted by the existing operation contract. Formula expressions,
typed values and caches otherwise come from the source runtime's actual bytes.

The destination independently calculates edited IR with source XLSX unavailable
and all formula caches removed in the regression test. For fidelity-preserving
export it additionally needs the immutable original package as a preservation
baseline, because unknown parts are absent from this IR. The baseline is not
an executable-code substitute. Current `export_snapshot` APIs validate the
transferred model and reuse ordinary guarded export; they refuse cell deletion,
guard weakening and incompatible layout/style/validation edits. They leave the
owned import/session unchanged. Portable self-contained preservation packaging
and cryptographic binding of IR to its baseline are future work.

These toolkit IR routes are an older lane. Passing them alone cannot close the
code-translation acceptance gate. The IR receipt's `source_translation: not_implemented`
means that route does not perform source translation; it does not describe the
separate source compiler. Graph profiles 2-5 source-translate inventoried calls:
SUM on profile 2, IF and IFERROR on profile 3 (retained on profile 4), and
AVERAGE on profile 5. The packaged command `workbook-forge-compile` defaults to
graph-v5. See [source profile 5](source-graph-v5.md).

## Older lane: numeric-v1 executable-source slice

`tools/source_compiler.py` is the compatibility launcher. It defaults to
numeric-v1 and delegates to the packaged compiler module, with `compile`,
`translate` and `emit` commands. The version 1 source bundle supports one
numeric A1 input, a B1 expression, numeric literals, unary signs, and `+`,
`-`, `*`. Values are finite binary64 with magnitude at most 1e15; this is a local
numeric profile, not an Excel precision/conformance claim. This lane rejects
calls, division, powers, extra references, unsupported workbook shapes, metadata
outside its representable profile and edits outside the versioned source
scaffold. Those refusals belong to numeric-v1. Graph profiles 2-5 are the
later lane above: they add division, multi-cell references, and the four
inventoried calls.

All four **actual source** paths are exercised:

```
OOXML -> Rust source -> independently compiled/executed Rust -> OOXML
OOXML -> Python source -> independently executed Python -> OOXML
OOXML -> Rust source -> Python source -> independently executed Python -> OOXML
OOXML -> Python source -> Rust source -> independently compiled/executed Rust -> OOXML
```

The converter/source importer is currently Python-hosted on every source path.
That host choice is distinct from the generated program language. Generated Rust
uses Rust's standard library and `rustc`; generated Python uses Python's standard
library. Neither generated program calls Forge, Python-from-Rust, an LLM, a
server or a workbook reader. More complete generated programs may later need a
version-pinned Forge runtime; do not extend this zero-Forge-dependency claim to
future function implementations.

The source bundle carries a typed two-cell metadata manifest with **no original
formulas, formula caches or embedded XML**. The importer validates Excel syntax,
then accepts a restricted expression AST/scaffold, regenerates target code, and
lowers the edited target expression to a workbook. Rust output is emitted by the
independent Rust adapter; Python output uses the independent Python library.
Basic representable styles survive. Unrepresented package parts, names, merges,
row heights and style features are refused in this self-contained source subset;
the broader immutable-baseline IR path retains them. No baseline is used to
restore executable-source output.

The tests and `tools/source_benchmark.py` change `A1*2+3` in real generated source
to `a1*5.0+3.0`, make the original XLSX unavailable, execute the edited source and
translated source independently, and verify changed-input results of 13 at 2 and
38 at 7. Both runtime reimports verify output 38, edited formula, input and basic
style. The standalone CLI `emit` executes the supplied validated program and
checks its result against independent lowered workbook calculation before export.
Arbitrary programs outside the scaffold receive `unsupported_source`; metadata
outside the source profile receives `unsupported_fidelity`. There is no silent
embedded-original-workbook restoration. Source is bounded to 16 KiB, expression
to 8 KiB/256 nodes/32 levels, and the CLI manifest to 1 MiB.

```sh
.venv/bin/python tools/source_compiler.py compile input.xlsx bundle --language python
.venv/bin/python tools/source_compiler.py translate bundle/program.py generated.rs --origin python --destination rust
.venv/bin/python tools/source_compiler.py emit generated.rs bundle/manifest.json output.xlsx --language rust --input 7 --rust-binary "$PWD/rust/target/release/examples/compiler_roundtrip"
.venv/bin/python tools/source_benchmark.py .verification/source-run --repeats 3
.venv/bin/python -m pytest -q python/tests/test_source_compiler.py
```

Use a new bundle/output directory. `source_benchmark.py` authors its own synthetic
workbook. It records compilation, both independent executions, source translation
and workbook export separately on matched workloads; Rust compiler and process
startup cost is included. Memory high-water marks include `rustc` and are not
per-route runtime allocations. Excel Desktop opening/recalculation remains unrun.

## Reproduce

Use the repository's development environment and cached Rust dependencies:

```sh
CARGO_TARGET_DIR="$PWD/rust/target" cargo build --release --locked --offline --manifest-path rust/Cargo.toml --example compiler_roundtrip
.venv/bin/python tools/compiler_coverage.py --check
.venv/bin/python tools/compiler_benchmark.py .verification/compiler-run --repeats 5
.venv/bin/python -m pytest -q python/tests/test_compiler_benchmark.py
```

Choose a new output directory for each run; the harness refuses to reuse one.
The synthetic fixture has 13 stored cells and 8 formulas, five scalar types,
cross-sheet/absolute/range/absent references, lazy branching, text, a division
error and IFERROR. Every route changes input 2 to 7 and multiplication 2 to 3
inside the front-end artifact, poisons caches and checks independently derived
results (21 and 31 for the dependent arithmetic cells). Independent reimports
check formula/value/error caches. Metadata checks retain styles, a defined name,
a merged range and an opaque part, comparing XML structure rather than ZIP
identity. Full Excel no-repair opening and recalculation are not run.

One warmup precedes timing. Every route performs the same import, artifact edit,
serialization, target baseline import/calculation/export stages. Rust subprocess
startup is included; Python runs in the driver process. These are deployment-path
costs, not a pure Rust-versus-Python engine speed comparison. Validation reimports
are outside the timed region. Process-lifetime parent/child RSS high-water marks
are labeled explicitly and cannot rank per-route memory consumption.

A conventional `zipfile` + `ElementTree` baseline applies the same package edits
and preserves metadata while discarding stale formula caches. It cannot calculate
dependent values. Its time covers overlapping editing work only; do not compare
it as if it completed the full calculation task. Forge's demonstrated additional
capability is independent dependency calculation and typed error/result export,
not an asserted throughput advantage over every workbook library.

The canonical scheduler benchmark is separate from these toolkit/OOXML routes:

```sh
CARGO_TARGET_DIR="$PWD/rust/target" cargo test --release --locked --manifest-path rust/Cargo.toml scheduler_benchmark -- --ignored --nocapture
CARGO_TARGET_DIR="$PWD/rust/target" cargo run --release --locked --manifest-path rust/Cargo.toml --example benchmark_canonical
```

It removes repeated whole-graph scans with a reverse edge index, preserving lexical
order and cycle semantics. The reverse index adds O(formulas + edges) borrowed
storage. Do not extrapolate scheduler speedups to overall compilation; per-formula
whole-workbook environment construction remains a separate quadratic cost.

## Staged full-coverage backlog

1. **Expand compiler source contract:** extend the proven numeric subset to
   multi-cell/range dependencies, typed errors and lazy function calls; add portable
   AST/source maps and version-pinned runtime bindings, then exercise each family
   through the source-removal/edited-code gate. Keep library and packaged CLI
   entry points independently runnable. Arbitrary source programs remain a
   deliberately diagnosed import boundary, not an Excel-equivalence assumption.
2. **Language surface:** reconcile the 521-entry snapshot against current primary
   catalogs/specification revision, then implement the 406 currently unimplemented
   inventory entries by family with shared independent expected fixtures and
   backend-specific execution tests. Preserve separate parsed/preserved/translated/
   executable/reference/Excel-observed status for every function, never a count-only
   support claim. Audit operators, unions/intersections, names, structured/3D/external
   references, omitted arguments and scalar types alongside functions.
3. **Calculation semantics and bounds:** per-function coercion and lazy errors;
   absent/blank/zero/empty text/numeric text; serial 60 and 1900/1904 dates;
   shared-formula expansion; arrays/spills, `@` and `#`; LET/LAMBDA and modern
   functions; explicit cycles and iterative convergence policy. Profile cumulative
   graph/text/array/package budgets. Revision-aware caches must invalidate from
   full semantic dependencies, including environment bindings.
4. **Environment and fidelity:** clock/random providers, external data/UDF/add-in/
   service contracts, sandboxed effect boundaries, names/tables/validation/styles/
   merges/layout preservation and edit support. Extend the Excel Desktop evidence
   harness for no-repair opening and fresh results/shapes/errors, with build and
   calculation settings recorded. Unknown behavior remains unverified.
5. **Embeddable/TS product acceptance:** stabilize the client-facing protocol over
   existing independent sessions, cancellation and bounded viewport/change-set
   responses; thin native IPC and Python HTTP adapters; then one optional webview
   grid showing the same workbook/edit/export sequence on both backends. Avoid
   leaking engine-specific data structures into UI contracts.

These stages extend the existing compiler pipeline and product contracts; they
do not approve model-version changes, provider execution or a UI rewrite.
