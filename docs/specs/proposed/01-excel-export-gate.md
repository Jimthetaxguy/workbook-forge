---
type: technical-specification
status: proposed
base: main c84ec07
summary: Record the first scalar Excel Desktop receipt for the canonical fixture, then add the Spec 2 coverage classes (spill, volatile, iterative, quirk) with oracle_optional and oracle_required runs.
---

# 01 — Excel export gate: scalar receipt, then Spec 2 classes

## Problem

The trust path in [compiler-pipeline.md](../compiler-pipeline.md#execution-order) is
**shared calculation report → scalar Excel observation → full Spec 2 evidence →
headless workflow**. The report exists. Committed receipts in
`receipts/canonical-operating-scenario/` are preflight only: those files record
that Excel was not invoked. The uncommitted Excel 16.113.4 run described in
[Completing workbook acceptance](../../excel-observations.md#completing-workbook-acceptance)
is separate: `spec2_passed` stayed false, and that receipt was not committed.
Phase A still means committing a path-sanitized receipt. [red-flags.md](../red-flags.md) lists as not built: an observed
round trip of the canonical fixture, the spill/volatile/iteration/quirk
classes, and the `oracle_optional` / `oracle_required` test runs.

## Outcome

- James (or an agent on his Mac) runs one command and commits an honest
  receipt showing Excel Desktop opened the canonical fixture, took an edited
  input, fully recalculated, saved, and was reimported.
- An agent can then tell which Spec 2 classes are observed, which are refused
  with a reason, and which are still open, from a receipt rather than prose.

## Scope

Phase A (scalar, needs a Mac with Excel):
- Run `python3 tools/canonical_excel_receipt.py --fixture fixtures/operating-scenario.workbook.json --output-dir receipts/canonical-operating-scenario-excel --excel`.
- Fix harness defects that stop it, in `tools/excel_oracle.py` /
  `tools/canonical_excel_receipt.py` only. Record settings the harness cannot
  observe (calculation mode, iteration settings) as unavailable, not assumed.
- Excel must have no workbook open before the run (the harness refuses
  otherwise and reports `blocked`). Never change global calculation or
  iteration settings, run macros, or send cleanup commands after an uncertain
  failure (excel-observations.md).
- The 2026-09-28 SDK export round trip is unreceipted narrative, not a finished
  observation. Phase A is the separate canonical-package receipt. Do not treat
  the SDK note as already observed.
- Commit the receipt directory and a short dated entry in
  `docs/excel-observations.md` and `docs/run-history.md`. Before committing,
  strip machine paths, account names and workbook author metadata, per
  "Sharing observation evidence" in excel-observations.md.

Phase B (Spec 2 classes):
- Add pytest marks `oracle_optional` and `oracle_required` in
  `pyproject.toml` (`[tool.pytest.ini_options] markers`). Optional runs skip
  with an explicit reason when Excel is absent; required runs fail.
- Add one fixture per required class under `fixtures/` (see acceptance tests).
- Spill: already refused. SDK export raises `UnsupportedWorkbook` and the
  harness records the class `blocked` with `blocker_kind=unsupported_capability`
  (excel-observations.md). Keep that; add tests if missing, not a new code.
  The gate stays open. Do not reduce an array to a scalar and call it covered.
- Volatile predicates, tolerances, formula allowlists and required classes
  already live in the round-trip contract
  (`schemas/excel-roundtrip-contract.schema.json`,
  `fixtures/excel-roundtrip-red-flags.contract.json`). Extend those; do not
  add a separate allowlist file. Fixture coverage already distinguishes
  observed / not_observed / blocked classes.

## Non-goals

- Implementing spill placement in the writer (separate spec once the refusal
  and destination-range contract are agreed).
- Claiming Excel compatibility for any Workbook Forge profile that this run
  did not observe.
- Any change to `schemas/workbook-model.v1.schema.json`.

## Design

- `tools/excel_oracle.py` stays the only process that drives Excel. Phase B
  adds a `--class` filter and a diff writer; it does not add a second driver.
- New test module `python/tests/test_excel_gate.py`. Each test loads a fixture,
  exports through the existing canonical export (`python/workbook_forge/canonical_xlsx.py`),
  calls the oracle, reimports with `python/workbook_forge/intake.py`, and
  compares against the fixture's expected values (from Microsoft examples or
  independent arithmetic, never from either engine).
- Rust export (`rust/src/xlsx.rs`) is driven through the same harness via the
  `rust/examples/canonical_calc.rs` path so both backends' files are observed.
- Receipts identify inputs by sha256 fingerprint. No absolute path appears in
  any receipt (same privacy rule as the calculation report).

## Schema / model-version impact

None for the workbook model. `model_version` stays 1. If the receipt needs a
schema, add `schemas/excel-receipt.v1.schema.json` as a separate document, or
extend `schemas/excel-roundtrip-contract.schema.json` if it already covers the
fields. Check that file before adding a new one.

## Acceptance tests

```python
# python/tests/test_excel_gate.py
@pytest.mark.oracle_optional
def test_scalar_receipt_records_excel_build_and_full_recalc(): ...
    # receipt["excel"]["build"] is a non-empty string; actions include
    # open, set_input, full_recalc, save, close, reimport, in that order.

@pytest.mark.oracle_optional
def test_scalar_receipt_outputs_match_fixture_expected(): ...

def test_oracle_optional_skips_with_reason_when_excel_missing(monkeypatch): ...
    # no Excel → pytest skip whose reason names "Excel Desktop not available".

def test_oracle_required_fails_when_excel_missing(pytester): ...

def test_spill_class_blocked_unsupported_capability(): ...
    # red-flag contract run: array_spill is blocked with
    # blocker_kind == "unsupported_capability"; no file is written.

@pytest.mark.oracle_optional
def test_volatile_cells_assert_type_and_recalc_not_value(): ...   # NOW, RAND

@pytest.mark.oracle_optional
def test_iterative_settings_round_trip_or_reported_unobservable(): ...

def test_harness_diff_fails_on_planted_mismatch(): ...
    # Spec 2 "done when": a planted wrong expected value makes the diff fail.

def test_receipt_contains_no_private_path(tmp_path): ...
    # input under tmp_path / "private-dir-xyz"; that name absent from receipt bytes.
```

## Dependencies

None. This is first because the compiler spec puts the joined headless path
(spec 07) behind it.

## Open questions for James

- **Decision:** after the scalar receipt, must all of Spec 2 close before the
  joined headless command (spec 07), or can 07 ship with the spill class open
  and refused? The compiler spec currently says the full gate comes first.
- Spill placement needs a declared result-range contract before the gate
  can close (product-specifications.md, plan item 3). Nobody has written it yet.
