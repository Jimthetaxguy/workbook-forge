# Fit-check: parse/schema/compile vs task correctness

- Date: 2026-11-24 (America/New_York)
- Handoff: 2026-11-23-grokky-nightly-audit-loop-158
- Paper: [arXiv 2611.27106](https://arxiv.org/abs/2611.27106) (Schema-Validated LLM Workflows)
- Repo: https://github.com/Jimthetaxguy/workbook-forge
- Ref: `main` at `0aebb42e77d5c2fa95e9760fe814b0733c1ec0fe` (matches expected `0aebb42e`)
- Clone: `/workspace/workbook-forge-build/fitcheck-clone` (shallow, box only; no push, PR, or merge)
- Verdict: **no fit** — no path was found that counts a successful parse, schema validation, or compile as a correct evaluation/task result. The tree already keeps those layers separate.

## arXiv numbers

The abstract page loaded. The same headline figures appear in §8 Results, not only the abstract:

- Confirmatory run `confirmatory-5400-v1` completed **5,400** unique rows (six conditions × five tasks × 100+50+50+50+50 instances × three nested replicates).
- Overall schema validity **0.684**; mean automated quality **0.638**.
- PydanticAI / LangGraph: 100% schema validity, quality 0.935–0.940; DSPy: 99.8% schema validity, quality 0.911 (the abstract’s 0.91–0.94 band).
- Table 9 (H1): many schema-valid rows still miss the 0.99 quality threshold — extraction 642 flags / 1,194 schema-valid rows; compliance QA 370 / 609. The paper’s line: “Schema acceptance therefore cannot be read as task success.”

## Findings (checked paths)

The catalogs encode a four-rung support ladder (`catalogued` → `parsed` → `evaluated` → `conformance-tested`) plus a separate evidence grade (`profile` / `dual_engine` / `expected_value` / `excel_observed`). `is_excel_compatible` is true only for a current Excel observation; dual-engine agreement and fixtures never make it true (`python/workbook_forge/catalog.py:178-192`, `224-228`; `catalog/formula-support.schema.json:214-220`, `341-348`).

On this SHA every one of the 171 `formulas.json` implementations is `conformance-tested` in both Python and Rust. The remaining inventory names stay `catalogued`. No live row is `parsed`-only.

1. **Compiler coverage does not treat parse as executable.** `tools/compiler_coverage.py:3-5` states that `NAME()` probes syntax only, never arity or semantics. `parsed_syntax_probe` is True for all 1,042 language rows (521 names × 2), including 700 `executable: planned` rows such as ACCRINT. `executable` is taken from catalog status, not from the probe (`tools/compiler_coverage.py:43-66`). Docs restated at `docs/compiler-benchmarks.md:45-47`.

2. **Evaluation refuses parse-only and catalog-only names.** After `analyze_formula` succeeds, Python still refuses functions whose status is not `evaluated` or `conformance-tested` (`python/workbook_forge/model.py:816-822`, `_supported` at `917-922`). Rust uses the 171-name `SUPPORTED_FUNCTIONS` registry (`rust/src/model.rs:621-626`), asserted equal to the catalog (`rust/src/toolkit.rs:3241`). Parse success is not enough to calculate.

3. **The capability matrix marks evaluate `supported` only when status is `conformance-tested`** (`tools/capability_matrix.py:52-58`, `294-298`). Corpus categories can be `supported` for inspect/preserve/export when a file did not refuse, but evaluate on those categories is explicitly `untested` (`332-336`). Browser-protocol evaluate is also `untested`. Evidence grade is recorded only on catalog-established evaluate rows.

4. **Shared fixtures and compiler benchmarks check values, not compile/parse alone.** All 2,627 lines of `fixtures/formula-cases.jsonl` carry an `expected` field. Source-compiler tests compile *and* execute (`python/tests/test_source_compiler.py:35-40`). `tools/compiler_benchmark.py:166-170,194` asserts independent arithmetic and records `semantic_checks: passed-independent-arithmetic-and-package-invariants` plus `excel_recalculation: not_run`.

5. **Agent and schema checks are contract tests, not task scores.** `python/tests/test_agent_contract.py:132-151` schema-validates every envelope and then requires `ok` plus result assertions. `examples/agent_scenario.py:1` is a deterministic SDK replay, “not an LLM.” There is no LLM eval harness that could score schema-valid output as task-correct. Intake/catalog `jsonschema.validate` calls check document shape.

6. **Extraction `status: "parsed"` is syntactic analysis, not a correctness score.** `python/workbook_forge/extraction.py:129-143` sets `parsed` when `analyze_formula` succeeds, including unknown names (`known: False`); syntax failure is `unsupported`. Tests and `tools/verify_installed.py` assert that analysis status, not calculated values.

7. **Web UI does not treat parse as “correct.”** The Functions panel lists `catalog/formulas.json` (today all `conformance-tested`) as “what Forge calculates” (`web/univer-spike/src/function-notes.ts:1-6`; `web/univer-spike/src/main.ts:177-187`). The status line’s “All changes calculated” (`328-337`) means the engine finished the pass; refused formulas go to a separate issues list (`201-232`). Evidence grade is not shown in the panel.

## Candidate follow-ups (not implemented)

1. Add a regression test that a `formulas.json` entry with status `parsed` is refused by `_supported` / `SUPPORTED_FUNCTIONS`, stays `untested` (not `supported`) in the capability matrix, and is not advertised in the web Functions panel as a function Forge calculates.
2. Align `_supported` (`model.py:922`, which also accepts `evaluated`) and `compiler_coverage.py:55` (also treats legacy `"implemented"`) with the matrix’s stricter `conformance-tested`-only rule, unless a mid-ladder function is intentionally reintroduced.
3. Show `status` and `evidence_grade` in the Functions panel so “calculates 171 of the inventoried functions” cannot be read as Excel-observed correctness.

