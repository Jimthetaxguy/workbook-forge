# v1 export — canonical calculation through Excel

**Branch:** `cursor/v1-export-canonical-receipt-bb6f`  
**Base:** `agent/combine-calc-best-20260928` @ `4d56af6`

This branch starts at the combined calc tip, not at bare `3cc1efd`. That tip keeps the Grok source of truth: `schemas/workbook-model.v1.schema.json` and `fixtures/operating-scenario.workbook.json`. It also keeps the Mac multi-case goldens in `fixtures/operating-scenario-cases.json`. Draft PR #3 is the calc candidate. PR #2 stays open.

The Excel harness is the Mac export slice `origin/impl/v1-export` at `bea3ee1` (`tools/excel_oracle.py` and the round-trip contract). `tools/canonical_excel_receipt.py` only exports the canonical fixture and calls that harness.

## What this slice proves

`fixtures/operating-scenario.workbook.json` is the canonical model. Python and Rust calculate those bytes. The Python OOXML writer exports that workbook, and import reads the package back into the same model types.

Excel Desktop is the recalc oracle (Spec 2). Export bytes, reimport, formula text, and engine parity do not pass Spec 2.

## Command

Pending receipt from a host without Excel Desktop:

```bash
python3 tools/canonical_excel_receipt.py \
  --fixture fixtures/operating-scenario.workbook.json \
  --output-dir receipts/canonical-operating-scenario
```

Desktop oracle, on a Mac with Excel, in a clean directory:

```bash
python3 tools/canonical_excel_receipt.py \
  --fixture fixtures/operating-scenario.workbook.json \
  --output-dir receipts/canonical-operating-scenario-excel \
  --excel
```

The no-Excel run marks the scenario contract `prepared` and Excel `pending`. It does not pass Spec 2.

## Array spill

v1 does not place spill cells. Canonical export and import fail closed with `array_spill_refused` and write no spill cells. The red-flag contract (`fixtures/excel-roundtrip-red-flags.contract.json`) names `Red Flags!B7` `=SEQUENCE(3)` and spill cells `B7:B9`. `run_roundtrip` blocks that class before Excel with `blocker_kind=unsupported_capability` and coverage `blocked`. The receipt gate is `not_passed`. `SUM(SEQUENCE(...))` does not count as spill placement.

## Out of scope

Agent-headless waits until the scenario round-trip status is `observed` and `spec2_passed` is true. Intake and broader formula coverage wait on gaps from real workbooks.

## Do not

- Do not rewrite history on `main` or force-push.
- Do not modify Codex session files under `~/.codex`.
- Do not merge this branch, PR #2, or PR #3 from here.
- Keep shelf split: Forge owns compute + OOXML; cell-store owns the sealed event log.

## Combined calc tip — 2026-09-28

Branch `agent/combine-calc-best-20260928` keeps the Grok/Cursor PR #2 tip as the
single schema SoT (`schemas/workbook-model.v1.schema.json`) and ports Mac/Codex
`impl/v1-calc-binding` multi-case golden coverage without a second schema path.

Kept from Grok: dual native hydrate, `schema_version`/`model_version`, provenance,
workbook diagnostics, `Formula.result`, empty Python/Rust semantic diff, docs order
(export → intake → agent-headless after this slice).

Taken from Mac: `fixtures/operating-scenario-cases.json` and the shared golden
input-case pytest (baseline / higher-price / price-equals-cost → `#N/A`), plus
Excel dimension maxima on the existing schema.

Dropped from Mac: `schemas/workbook-model-v1.schema.json` (second schema name),
array-of-bindings + constraints DTO, `canonical_calc` session/revision API,
intake bleed-in (`intake.py`, `test_intake.py`), and `tools/verify_canonical_calc.py`
tied to that alternate binding shape. Constraints/session can land later on this
schema if needed; they must not fork the serialized model.
