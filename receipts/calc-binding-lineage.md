# Calc-binding lineage receipt

The export branch starts at the combined calc tip, not at bare `3cc1efd`.

| Tip | SHA | Role |
| --- | --- | --- |
| Combine, `agent/combine-calc-best-20260928` (draft PR #3) | `4d56af6842e031022fd7067d67eb9be8675eb4d7` | Calc candidate this export is based on. Supersedes PR #2 as the candidate. PR #2 stays open. |
| Grok, `origin/cursor/canonical-model-calc-binding-404b` (PR #2) | `3cc1efdf8e13e7547c5fb659b619fc66d26e9b40` | Parent of the combine commit. Schema and fixture source of truth. |
| Mac, `origin/impl/v1-calc-binding` | `c69300ee5f60255cda7aba24efb56ca7018d17f2` | Sibling of `3cc1efd`. Multi-case goldens were ported; the rest of that model was not. |
| Export harness, `origin/impl/v1-export` | `bea3ee14533e1feb29c985ce9806837adb0a2012` | `excel_oracle` and the round-trip contract, replayed here. |

Combine decision detail is on PR #3 and in the combined-calc section of `IMPL_SLICE.md` at `4d56af6`. The named note `forge-combine-best-2026-09-28.md` is not a file in that tree.

Merge-base of the Grok tip, the Mac tip, and the export harness with `main` is `5698f5b0c365f0bfbbf81ac56c13b77c2c05981a`. `git merge-base --is-ancestor` fails in both directions between `3cc1efd` and `c69300e`. `4d56af6` is `3cc1efd` plus one commit.

## What each Step 0 tip keeps

Schema file:

- Grok: `schemas/workbook-model.v1.schema.json`
- Mac: `schemas/workbook-model-v1.schema.json`

Bindings:

- Grok: object `{inputs, outputs}` of `{sheet, address}`.
- Mac: array of `{direction, name, sheet, address, value_type, required, constraints}`.

Formula object:

- Grok required fields: `expression`, `dependencies`, `result`. `result` is the calculation. `cell.value` is the cache.
- Mac required fields: `expression`, `dependencies`. Optional `cached_value` is an observation, not a Forge result.

Fixture:

- Grok: `fixtures/operating-scenario.workbook.json`. Assumptions dimensions `[1, 3, 1, 3]` (8 cells). Forecast dimensions `[1, 5, 1, 10]` (30 cells). Scenario formulas plus boolean `C1`, a `#REF!` literal, `=1/0`, `=NOW()`, and parse probe `=1+`.
- Mac: `tests/fixtures/canonical/operating-scenario-v1.json`. Assumptions dimensions `[1, 3, 1, 2]` (6 cells). Forecast dimensions `[1, 4, 1, 7]` (27 cells). The twelve scenario formulas match the Grok authored text (`=B2*Assumptions!$B$1`, `=SUM(C2:C4)`, the break-even `IF`, and the sibling period formulas). The extra probes above are absent.

Calculation code:

- Grok: `python/workbook_forge/model.py` (`hydrate`, `calculate`) and `rust/src/model.rs`, with example `rust/examples/canonical_calc.rs`.
- Mac: `python/workbook_forge/canonical_calc.py`, `rust/src/canonical_calc.rs`, plus intake (`python/workbook_forge/intake.py`, `python/tests/test_intake.py`, `tools/verify_canonical_calc.py`).

## What this branch uses

Schema path: `schemas/workbook-model.v1.schema.json`. Fixture path: `fixtures/operating-scenario.workbook.json`. The combine commit also leaves `fixtures/operating-scenario-cases.json` and Excel dimension maxima on that schema. The Excel receipt uses those Grok engines. The round-trip harness (`tools/excel_oracle.py`, `schemas/excel-roundtrip-contract.schema.json`, `fixtures/excel-roundtrip-red-flags.contract.json`) is the Mac export slice at `bea3ee1`, replayed here. The canonical scenario contract is `fixtures/operating-scenario.excel-roundtrip.contract.json`.
