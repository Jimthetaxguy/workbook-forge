# Cumulative compiler review brief (through source profile 5)

For a separate reviewer: assess the accumulated local patch, not another feature
implementation. Base commit: `6cc7b7625d89cbcb1fd0d1dfdf8eb4d8a66991a3`;
branch `agent/codex-dependency-optimization`, uncommitted/local-only. The task's
patch manifest records cumulative and stage-five delta hashes and exact paths.
No canonical checkout edits, pushes, merges, deployment or client workbook use.

## Interfaces and ownership

| Boundary | Exact path / entry | Review invariant |
| --- | --- | --- |
| Packaged compiler CLI | `python/workbook_forge/source_cli.py:main`; `pyproject.toml` | `workbook-forge-compile` and `python -m workbook_forge.source_cli` default to graph-v5; no repository scripts/UI/agent dependency |
| Compatibility launcher | `tools/source_compiler.py` | Legacy numeric-v1 default retained; implementation delegates to packaged module |
| Workbook/source compiler | `python/workbook_forge/source_graph.py:compile_workbook`, `translate`, `lower`, `execute`, `emit` | API default stays graph-v2; latest profile selected explicitly; source version is separate from workbook schema/model versions |
| Typed source AST | `python/workbook_forge/source_graph.py:Node`, `Program`, `validate`, `import_source` | Bounded constructors and exact scaffold; expressions/defaults authoritative; unknown edits/cycles rejected |
| OOXML generation intake | `python/workbook_forge/source_package.py:check_fidelity`; `source_graph.py:bounded_package`, `source_model` | Pinned bounded package, declared expansion/duplicate/metadata/relationship guards; not full XSD validation |
| Execution | `python/workbook_forge/source_process.py:run_validated_source` | Caller validates full scaffold first; source and Rust executable private/pinned; retained artifact exclusively created |
| Independent generated evaluators | `python/workbook_forge/source_runtime_v2.py/.rs` through `source_runtime_v5.py/.rs` | Frozen versioned stdlib templates; Rust never delegates evaluation to Python |
| Worksheet semantics | `python/workbook_forge/__init__.py`; `rust/src/lib.rs` | Independent typed/coercion implementations; native primitive legacy profile remains explicit |
| Existing workbook models | `python/workbook_forge/toolkit.py`, `python_engine.py`; `rust/src/toolkit.rs` | Source lowering uses existing toolkit interchange, not a third workbook format |
| Separate canonical path | `python/workbook_forge/model.py`, `canonical_xlsx.py`, `canonical_roundtrip.py`; `rust/src/model.rs` | Canonical export calculates/writes in Python; Rust is optional comparison. Receipt directory must be new, with no recursive cleanup |
| Fresh export / adapter | `source_graph.py:verified_export`; `rust/examples/compiler_roundtrip.rs:emit-fresh` | Writer output staged; reimported formulas, declarations and typed values/errors checked before exclusive publication |
| Preservation-aware export | `python/workbook_forge/xlsx.py:export_snapshot`; `rust/src/xlsx.rs` | Imported immutable baseline required; distinct from fresh source-generation contract |
| Coverage/evidence | `tools/compiler_coverage.py`, `tools/compiler_evidence.py`; `catalog/compiler-comparison-evidence.v1.json`, `average-evidence.v1.json` | Registry, source functions, grammar and independent evidence are separate counts |

## Review-critical invariants

1. All four source routes consume preceding **edited source**, with original XLSX
   unavailable and formula/cache-free manifests. Declaration identities/roles,
   sheet order and source version must match; metadata cannot restore formulas.
2. Generated execution, Python SDK comparison and selected writer recalculation
   are distinct. Post-writer reimport checks caches against freshly executed
   source and checks formula ASTs; caches are not an Excel oracle. Wrong writer
   output is refused before creating the user's file. Style fidelity remains
   fixture-covered, not a complete metadata equivalence proof.
3. Rust programs are stdlib-only. The compiler driver still requires Python for
   intake/translation/lowering/validation, so no source route is called end-to-end
   Rust-only. Rust export needs the separately built writer adapter supplied by
   `--rust-binary`; it is not silently bundled in a pure Python wheel.
4. Existing 1MiB source/payload/package budgets, 100,000 cells/edges/expanded
   ranges, 256 expression nodes/32 levels, 96 dependency depth and process
   timeouts apply. Emitted packages must also meet the source-package budget.
   These are profile limits, not a hard whole-process memory sandbox or Excel
   worksheet limits. Unsupported fidelity fails rather than promising preservation.
5. Independent behavioral specifications: `fixtures/comparison-cases.v1.json`
   (486 placements/operators), `fixtures/average-cases.v1.json`,
   `fixtures/source-graph.v2.json` through `source-graph.v5.json`, and shared
   `fixtures/formula-cases.jsonl`. Agreement is not independent Excel evidence.

## Unresolved policy/evidence (do not silently settle)

- AVERAGE direct logical/numeric-text counting has conflicting Microsoft pages.
  Selected counted policy is provisional; references ignore text/booleans/blanks
  and count zero. `catalog/average-evidence.v1.json` preserves both claims.
- Comparison audit: 72 primary-operator/arithmetic expectations, 168 MATCH-order
  inferences, 246 local-profile cases; **zero Excel observations**. Blank rules,
  locale collation, signed-zero/error edges and precision remain unobserved.
- AVERAGE uses decimal/exponent text with ASCII whitespace and binary64 left-fold
  accumulation; overflow/error order and computed scalar origins are profiles.
  Full Excel locale, numerical and reference-return semantics remain backlog.
- Native primitive scalar filtering/null/comparison behavior is deliberately
  distinct from worksheet semantics; see `test_primitives_contract.py`. Do not
  erase this distinction merely to increase agreement counts.
- Source function coverage is four partial profiles (SUM, IF, IFERROR, AVERAGE),
  six comparison operators and documented scalar/reference grammar; SDK catalog
  declarations are 115 names, historical inventory 521. Inventory refresh,
  target-build reconciliation, arrays/spills/names/tables/providers/UDFs/iteration
  and complete fidelity are unfinished. TS is a future transport-neutral client.
- No fresh Excel recalculation/no-repair opening. Seven original sandbox-only
  test failures remain excluded; the complete original gate is not green.

Read `docs/source-compiler-review-stage4.md` for reproduced prior findings and
`docs/source-graph-v5.md` for this stage's exact subset. Review priorities:
AVERAGE evidence conflict and coercion origins; source/manifest invariants;
private execution and staged publication; honest provenance and wheel isolation.

## Stage-six correction overlay

Read [source-compiler-review-stage6.md](source-compiler-review-stage6.md) before
retesting this checkpoint. Exact compact manifest encoding/budgets now live in
`source_contract.py:manifest_bytes`; style representability guards are stricter;
`workbook.py` retains negative-zero scalar sign; generated Rust SUM and the Rust
SDK use an explicit +0.0 left fold. Typed result verification remains strict.
Old Python/Rust exact-scaffold artifacts need regeneration after the template correction;
unary negation now inverts zero sign instead of subtracting from zero.
Independent review evidence itself was not edited.
