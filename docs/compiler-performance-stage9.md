# Bounded phase performance and resilience, stage nine

This checkpoint measures the existing compiler/engine boundaries and changes one
production bottleneck: repeated static range expansion during source validation.
No function, schema, source dialect, runtime template or TypeScript API changes.

## Matched experiment

`tools/compiler_workloads.py` defines version-one small/medium chain, wide fan-out,
repeated-range and multi-sheet workloads. Sizes stop at 194 cells; dependency
chains at 64 formulas in the performance suite. Repetitions are sequential with
one first sample and three warmed samples, SDK worker count one. No cache flush,
thermal tuning, process inspection, dependency installation or client data.

`tools/compiler_phase_benchmark.py` records:

- Python library intake versus standalone native Rust intake (includes process
  startup); identical semantic fixture per language/backend.
- OOXML-to-source compile, source parse/validation, graph-only validation and
  lowering separately. Compiler driver/parsing/code generation remain Python
  for both emitted languages, not an end-to-end Rust-only lane.
- Generated source API's first validated build/execute; a separately timed rustc
  build; first and repeated prebuilt program **process** invocations. Prebuilt
  execution excludes SDK validation/build; it is not a persistent hot evaluator.
- Actual Python/Rust SDK session edit plus recalculation from a warmed baseline.
  Rust SDK calculation uses the existing native bridge, not Python evaluation.
- Actual fresh export. Python's API performs its existing recalculation/guards;
  Rust's adapter process includes export and its additional diagnostic calculation.
  These are operational export costs, not a pure ZIP-writer speed comparison.
- One checked generated-source-to-OOXML emission per lane, including its build,
  execution, SDK checks, writer and reimport verification. One sample does not
  establish an end-to-end speedup.

All values are checked against independent bounded arithmetic expectations.
Raw receipts retain every phase sample, workload sizes/hashes, executable/source
identity and provenance. All 16 before/after semantic fixture hashes and generated
source bytes match; OOXML ZIP-byte identity is not the comparison criterion.
Peak RSS is process-lifetime parent/largest-child usage including rustc, not
per-phase allocation, a memory reduction result or a hard memory sandbox.

## Selected optimization and correctness

Medium repeated-range compilation spent about half its Python-source compile
phase in graph validation. `tools/validation_probe.py` separately times and
profiles that validation, excluding profiling overhead from timed samples.

`python/workbook_forge/source_graph.py:validate` now retains range member addresses
only within one complete validation call, keyed by formula sheet context and
reference text. Sheet identities are fixed for that call. It never caches input
values, calculations, volatile/environment results or anything across programs.
Every occurrence still increments expansion accounting and contributes the same
dependency edges. Unique cached members are bounded by the existing accepted
expansion budget; no limit was raised. Generated source bytes are unchanged.

The exact same range expands once instead of 128 times in the target workload.
Forty-call validation medians moved from 3.223 to 0.556 ms. Matched medium range
compile medians moved from 6.946 to 4.050 ms for Python source and 7.609 to 4.515 ms
for Rust source. These are workload/phase-specific observations, not general
runtime, memory, overall compiler or full-product speedup claims. Other timings
include normal variation; Rust is not assumed faster in every measured phase.

## Seeded and boundary checks

`python/tests/test_compiler_seeded.py` uses seeds 7, 31 and 2026 to generate bounded
numeric inputs and formula combinations. Twelve actual routes exercise both
languages in all four directions. They change actual source expressions/defaults
and runtime inputs, make the original input path unavailable, and translate the
preceding artifact. A guarded metamorphic law adds zero only to a known-positive
numeric result, avoiding unsupported signed-zero/coercion assumptions. Typed
results/formulas are checked after export and reimport. Three fixture snapshots
and independent arithmetic expectations are retained in task-local evidence.

Regression checks prove per-sheet cache separation, no cross-call retention and
unchanged multiplicity accounting. Component boundaries check 255/256 aggregate
arguments, 32/33 expression levels, 96/97 formula dependency levels, 100,000 versus
100,500 expanded range references, cycles and exact 1 MiB/+1 source admission.
Both generated runtimes also execute an unprimed 96-formula chain. The source-byte
admission test uses padding and does not claim the padding is valid source;
static graph admission is separate from all later payload/package limits.

These checks are independent bounded arithmetic/regression evidence and backend
parity, not fresh Excel recalculation or complete Excel semantics. AVERAGE direct
coercion, locale/type/blank/precision gaps, full function coverage, metadata/UI
fidelity and environment bindings remain separate unfinished contracts. No
unbounded stress or broad engine rewrite was performed.
