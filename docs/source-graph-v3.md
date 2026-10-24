# Source graph profile 3

This is an additive, bounded source compiler profile. Numeric-v1 and graph-v2
remain available; the v2 runtime templates are unchanged, and all 32 saved
stage-two generated artifacts still import. Workbook schema/model versions
are unchanged. This is compiler progress toward full custom coverage, not
complete Excel support.

## Public entry points

```sh
python tools/source_compiler.py compile input.xlsx bundle --language rust --profile graph-v3
python tools/source_compiler.py translate bundle/program.rs second.py --origin rust --destination python
python tools/source_compiler.py emit second.py bundle/manifest.json output.xlsx --language python --inputs "{\"'Inputs'!A1\":7}"
python tools/source_graph_benchmark.py .verification/source-v3-new --profile 3 --repeats 3
```

Library: `source_graph.compile_workbook(path, language, version=3)`,
`translate`, `execute`, `lower`, `emit`. Existing calls default to graph-v2.
Generated programs use only their language standard library and require no
Forge SDK, workbook, manifest, agent, UI or network service. The compiler driver
is Python and Rust emission requires the existing compiled Rust writer adapter.
Rust programs require rustc at build time; retained binary artifact paths must
be new. Both Rust runtime templates ship in the wheel.

## Semantics and addressing

Profile 3 supports multiple sheets with ordered identities, quoted names, doubled
apostrophes, exact Unicode names, ASCII case-insensitive lookup, empty sheets,
cross-sheet scalar/range references and mixed/absolute anchors. Source expressions
preserve reference spelling/anchors; export normalizes explicit sheet-name case
to the declared identity for SDK interoperability. Declarations and input overrides use canonical
quoted addresses such as `'O''Brien'!A1`. Formula context supplies unqualified
references. Sheet names containing `!` or `=` use hex-address framing on the
standalone typed wire, avoiding delimiter ambiguity. Arbitrary Unicode case-fold
compatibility has not been established. Control characters, unknown/external
sheets and 3D range endpoints are explicitly refused.

SUM, IF and IFERROR are source-compiled independently in Rust/Python. IF uses
lazy closures for both branches; IFERROR uses a lazy fallback. Non-selected
branches are validated and included in static dependencies but are not executed.
Cycles, including those in non-selected branches, remain an explicit refusal;
iterative calculation is not enabled. IF accepts two or three arguments; omitted
false results become Boolean FALSE. Omitted argument slots and array selections
remain backlog. Scalar branch results are values, so SUM applies its existing
direct-value coercion profile to selection results.

IFERROR catches typed Excel errors and preserves a selected fallback error. It
does not catch host exceptions/panics, resource limits, source diagnostics or
Rust Unsupported/Parse implementation failures. Both SDKs now propagate scalar
#NULL!/#SPILL! values and literals; this does not implement intersection or spill
placement. Source profile3 transports nine existing scalar error codes. Newer
Excel service/security/Python errors need explicit future contracts. The native
primitive API retains its separate seven-code input contract and explicit null-value
IFERROR behavior; worksheet empty-cell conversion does not redefine native null.

Microsoft documents IF selection and IFERROR's classic seven error codes. It
also states that selected empty cells in IFERROR become empty text; this stage
fixes that behavior in both SDKs and generated runtimes. See
[IF](https://support.microsoft.com/en-us/excel/functions/if-function) and
[IFERROR](https://support.microsoft.com/en-us/excel/functions/iferror-function).
Typed #CALC!/#SPILL! interception is an explicit local profile pending Excel
observation, not established by those classic-error examples. Source fixtures
include independent arithmetic and local-profile expectations.

## Execution and fidelity boundaries

Validated source bytes and the Rust executable are private snapshots. Rust
executes the private binary, not the retained caller-visible artifact; that
artifact is exclusively created and symlinks/existing files are refused. Changing
the retained file cannot replace the executed program. This is a restricted
compiler dialect, not a general sandbox for arbitrary Python/Rust programs.
Source import rejects scaffold edits and unknown constructors. Lexical conversion
preserves quoted strings, so Rust tokens in sheet names do not get rewritten.

Before OOXML import, compressed input and total declared expansion are checked
against the existing 1 MiB source-profile budget, duplicate ZIP parts are refused,
and the package is pinned. This bounds the profile before parser/allocation work.
Generated-source, input/output/cache/range payload, node/depth, cell and edge
budgets from profile 2 remain. Repeated range expansion is counted before
deduplicating dependencies; profile3 also bounds cumulative materialized
reference payloads, so multiple SUM arguments cannot bypass the per-value
budget. This is conservative execution accounting, not a whole-process memory
sandbox or a newly selected global memory setting. Larger packages require a later explicit resource
contract rather than silently dropping content.

All sheets pass the conservative package fidelity gate. Output is **fresh
workbook generation**, retaining representable sheet order/basic styles and
columns. Names/tables/shared formulas/arrays/spills/macros/unmodeled metadata
are refused. Preservation-aware patching remains the separate imported-baseline
IR lane; this profile does not claim opaque-part preservation. Manifests contain
no formula/cache fields and cannot restore old expressions over edited code.

The source route reader/parser/translator/lowering and checked SDK comparison
are Python. Generated execution is independently Rust or Python. Target export
recalculates and writes through that backend; Rust never calls Python from its
generated program. No source workflow is labeled end-to-end Rust-only. Native
Rust intake/evaluation/export and serialized-IR routes remain separate lanes.
Receipts retain artifact hashes and exact component provenance.

## Evidence and next boundary

`fixtures/source-graph.v3.json` and `python/tests/test_source_graph_v3.py` exercise
all four source routes, deliberate multiplier/default edits, changed inputs,
deleted original XLSX, poisoned formula caches, nine error values, cross-sheet
updates and both-backend output reimport. Additional tests execute lazy closures
with failure sentinels, preserve host failures, cover invalid source/package
limits, and replace the retained binary before private execution.

The function inventory now identifies 3 source functions separately from 115 SDK
runtime profiles and 521 historical built-in catalog rows. Inventory refresh and
target-build reconciliation remain required. Excel Desktop was not launched;
actual recalculation and no-repair opening remain unverified. Backend parity and
cached values are not an Excel oracle.

Next bounded milestone: compile comparison operators and common aggregate
functions (AVERAGE/MIN/MAX/COUNT), add explicit per-function coercion fixtures,
and extend the same modified-source/no-original gate. Then address names/tables
and preservation-aware source export using the existing immutable OOXML contract.
TS remains a transport-neutral client contract for independently adoptable engines.
