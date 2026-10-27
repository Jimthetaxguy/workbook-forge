# Executable source profile 2

Status: implemented local compiler slice, not full Excel compatibility. This
extends numeric source profile 1 without changing canonical workbook schema or
model versions. The standalone compiler, embeddable SDKs and future TS/UI client
remain separate products sharing foundations.

## What executes

`workbook_forge.source_graph` is an importable compiler API. The repository CLI
`tools/source_compiler.py` selects it with `--profile graph-v2`:

```sh
python tools/source_compiler.py compile input.xlsx bundle --language rust --profile graph-v2
python tools/source_compiler.py translate bundle/program.rs translated.py --origin rust --destination python
python tools/source_compiler.py emit translated.py bundle/manifest.json output.xlsx --language python --inputs '{"A1":7}'
```

Rust emission additionally takes `--rust-binary` pointing to the independently
built `compiler_roundtrip` example. Output paths/bundle directories must be new.
No agent, UI, Tauri, FastAPI, provider service or original XLSX is needed after
compilation. Python hosts the converter; this does not make generated Rust use
Python. Both generated programs execute independent embedded implementations
using only their language standard libraries. Rust requires an existing rustc
toolchain to build. The runtime template is included in the Python wheel.

The same four source paths run against one versioned synthetic fixture:

- OOXML → executable Rust → OOXML
- OOXML → executable Python → OOXML
- OOXML → executable Rust → edited Rust artifact → executable Python → OOXML
- OOXML → executable Python → edited Python artifact → executable Rust → OOXML

Source expressions are real calls such as
`mul(reference(e, "$A1"), num(2.0))` and
`sum_values([range_ref(e, "$A$1:A6")])` (Rust uses `vec![...]`). Cell functions
resolve dependencies through a fresh runtime instance. They do not parse or
restore opaque original formula strings at execution time. Source import reads
the actual modified artifact, builds a bounded constructor AST and regenerates
the target program and worksheet formulas. Original formulas and caches are
absent from the metadata manifest. Edited defaults in source supersede the old
manifest values; explicit `--inputs` override those defaults.

Execution pins the validated source bytes before invoking Python/rustc. Export
uses the same pinned snapshot for execution and lowering. A concurrent change
to the caller's source path cannot replace the validated program. Regression
tests deliberately mutate that path before process creation for both languages
and both source profiles.

## Implemented surface

| Surface | Profile 2 behavior |
| --- | --- |
| Workbook shape | Multiple cells on one worksheet; basic representable styles/column widths; extra package parts and unmodeled layout are refused |
| Operators | Binary `+ - * /`, unary signs, finite binary64 values; nonfinite arithmetic returns typed `#NUM!` |
| References | A1, mixed/absolute anchors, exact same-sheet qualification, missing-cell reads; reverse lowering retains anchors |
| Ranges | Rectangular and reversed endpoints as SUM arguments; row-major traversal and bounded expansion |
| SUM | Numbers; direct numeric text/Boolean coercion; referenced text/Booleans/blanks ignored; first errors propagate |
| Scalars | Number, text, Boolean, explicit blank, typed error; numeric text stays text until the operation's coercion rules apply |
| Errors | Common scalar/literal codes `#DIV/0! #VALUE! #REF! #NAME? #NUM! #N/A #CALC!`; division/error propagation and literal round trips are tested |
| Dependencies | Derived afresh from modified source, including expanded ranges and nonformula leaves; runtime caches are per invocation |
| Cycles | Explicit `cycle` refusal; no invented Excel error code or silent iterative fallback |

`#NULL!` and `#SPILL!` remain explicit `unsupported_error` cases until the Rust
scalar representation supports them. A transported error does not imply spill
placement or the function that would produce it is implemented.

Absent cells and stored blanks read as zero when a formula returns their value.
An empty text value remains text on a direct reference; the existing arithmetic
profile coerces it to zero. Numeric text is coerced in arithmetic and direct SUM
arguments, but ignored in referenced SUM arguments. These distinctions have
independent expected fixtures; empty-text arithmetic remains a local profile
without a fresh Excel observation.

Worksheet SUM previously returned 15 for Microsoft's documented
`SUM("5",15,TRUE)=21` example. Both SDK formula engines now implement the
direct-versus-reference distinction. The separate nine-function primitive API
retains its documented legacy aggregate profile; callers must not conflate
those profiles. SUM accumulation is an explicit binary64 left fold in the
current shared profile, including a cancellation fixture derived from binary64
spacing, not from either engine. This is not an Excel precision-wide claim.
Primary coercion evidence:
[Microsoft SUM examples](https://support.microsoft.com/en-us/excel/use-the-sum-function-to-sum-numbers-in-a-range).

## Limits and refusal behavior

The source dialect requires the generated runtime/scaffold and canonical layout.
Supported expression/default edits use canonical constructors/literals. Arbitrary
Rust/Python programs, runtime edits, duplicate declarations, unknown constructors,
formula/cell-role changes and formulas/caches inserted into manifests are refused.
Blank inputs/references are supported; an explicit `blank()` expression has no
reversible formula literal in this dialect and is refused.

Existing workbook/range budgets are reused: at most 100,000 cells/dependency
edges, at most 100,000 cells in an expanded range. Source expressions have at
most 256 nodes/32 levels; formula dependency depth is at most 96. Source files,
typed input payload, runtime output, retained formula-value payload and an
individual range payload are each capped at 1 MiB. These are declared experimental
compiler-profile bounds, not Excel limits or a resolution of the separate proposed
global per-cell-memory policy. Logical payload accounting is not an RSS claim.
Rust/Python subprocesses have compilation/execution timeouts.

Typed values travel via bounded stdin or the standalone command's typed argument
form (`A1=n:7.0`). Text uses UTF-8 hex, keeping empty text, arbitrary text and
line breaks distinct from blank and protocol framing. The wire dialect is an
internal generated-program interface; it is not the planned TS client protocol.

Profile 2 refuses multiple sheets, external references, names, tables,
spill/CSE results, implicit intersection, omitted arguments, dates/date systems
beyond the conservative source package profile, and unmodeled metadata.
Multiple sheets are supported from profile 3 onward, including profile 6.
Profile 6 supports omitted arguments in XLOOKUP's optional positions. Current
compiler state is [source profile 6](source-graph-v6.md). Unknown parts can
still be retained through the separate baseline-preserving IR workflow. No
macro/provider execution is introduced.

## Coverage and verification

Profile2 compiles **SUM as one source function in each language**. The current
521-row pinned map additionally identifies the newer [profile3](source-graph-v3.md). It keeps **115 declared SDK runtime profiles** and Excel
observation as separate counts. A registry row is not a full argument-domain
proof. The pinned inventory still requires refresh against a target Excel build.

```sh
python -m pytest -q python/tests/test_source_graph.py python/tests/test_source_compiler.py python/tests/test_compiler_benchmark.py
python tools/compiler_coverage.py --check
python tools/source_graph_benchmark.py .verification/graph-source-run --repeats 3
```

`fixtures/source-graph.v2.json` contains independent arithmetic expectations,
Microsoft's SUM example, reference-sensitive coercion checks and profile/error
cases. The four routes edit a multiplier, edit a default, remove the original
workbook path, change inputs, execute both artifacts and reimport outputs with
both backends. Additional tests cover forward dependency edits, rectangles,
reversed ranges, typed input distinctions, common error literals, malformed
source/manifests, payload/depth limits and mutable-source pinning.

The benchmark compares identical graph-v2 workloads across routes. It separately
records generation, source translation, both build/execution stages and checked
export. Checked emission executes the target again; Rust builds and process
startup are included. Process-lifetime memory peaks include rustc. It does not
claim a speedup against numeric-v1's different two-cell workload, against an XML
editing library, or from the earlier canonical scheduler improvement.

Excel 16.114 was found installed during the local stage. A no-prompt automation
availability probe returned application-not-running; no app was launched or
permission/settings changed. Actual Excel no-repair opening/full recalculation
was not run. Independent expected cases and cross-backend agreement do not
replace that oracle.

This page describes source profile 2. Later profiles added scalar error
variants, multi-sheet addressing, and further source-call families, with
versioned runtime bindings and the same edited-source/no-original acceptance
gate. Current compiler state is [source profile 6](source-graph-v6.md). Shared
behavioral fixtures and the checked SDK comparison bound this profile, and the
embedded runtime stays explicitly versioned.

## Backend provenance and export contracts

The generated Rust program evaluates independently using only Rust's standard
library. The source compiler CLI is a Python driver: Python reads OOXML, parses
and translates the restricted source, lowers it to the existing toolkit model,
and calculates an independent SDK comparison during checked emission. The
selected writer then recalculates through its own backend before writing. Thus
the source workflow is not end-to-end Rust-only. Receipts identify reader,
evaluator, writer and orchestration separately and hash each transferred artifact.

The serialized-IR benchmark is a separate acceptance lane. Its Rust-to-Rust
components use Rust intake, evaluation and preservation-aware export, with
Python outside the native CLI for fixture creation and verification. Mixed routes
consume the preceding engine's serialized artifact. None of these IR routes
counts as generated-source translation.

Graph-v2 emits a fresh workbook under conservative fidelity guards. The existing
IR snapshot path instead patches an immutable imported OOXML baseline, retaining
opaque parts. Neither contract substitutes for the other. The graph manifest has
no formulas or caches and cannot restore an old expression over an edited source.

The separate canonical receipt harness uses `export_canonical()`, which performs
Python canonical calculation and Python OOXML writing. Rust there is an
independent comparison only. Its receipt now records that provenance. Receipt
output must be a new directory (existing files, directories and symlinks are
refused); all scenario/red-flag evidence is retained, with no recursive cleanup.
Non-destructive temporary-directory tests cover these guards.
