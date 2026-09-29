# Canonical Excel receipt

Fixture: `fixtures/operating-scenario.workbook.json`.
Harness: `tools/excel_oracle.py` round-trip (`excel_export_roundtrip_v1`).
The typed workbook model is the source of truth. This note is a scan of one run.

Spec 2 passed: `false`.
Spec 2 needs Excel Desktop full rebuild plus every required red-flag class. A prepared package check is not that. array_spill stays blocked until spill placement exists, so this receipt does not pass Spec 2.

## Package

- Exported xlsx: `receipts/canonical-operating-scenario/operating-scenario.exported.xlsx`
- Scenario contract: `fixtures/operating-scenario.excel-roundtrip.contract.json`
- Scenario harness receipt: `receipts/canonical-operating-scenario/scenario-roundtrip.json`
- Scenario status: `prepared`
- Excel Desktop: `pending`

Input change declared in the contract: `unit_price` at `Assumptions!B1`, 20 → 25.

Excel formula rewrites recorded by the harness on this run:

- None. Excel has not rewritten this package.

Structural reimport before Excel: match `true`. Formula rewrites: `[]`.

Excel was not invoked; no recalculation or compatibility claim is made

## Engine values

Before the input change, Python/Rust match: `true`.

| Output | Result |
| --- | --- |
| break_even_units | `83.33333333333333` |
| division_error | `{"error": "#DIV/0!", "message": null}` |
| parse_probe | `null` |
| profit | `1440` |
| revenue | `7400` |
| unsupported_probe | `null` |

After unit_price=25, engine preview only. Python/Rust match: `true`. Model calculation after unit_price=25. Not an Excel recalc.

| Output | Result |
| --- | --- |
| break_even_units | `58.8235294117647` |
| division_error | `{"error": "#DIV/0!", "message": null}` |
| parse_probe | `null` |
| profit | `3290` |
| revenue | `9250` |
| unsupported_probe | `null` |

## Array spill

Status: `blocked`. Gate: `not_passed`. Coverage: `blocked`.
The red-flag contract names Red Flags!B7 =SEQUENCE(3) and spill cells B7:B9. The round-trip harness blocks that class before Excel opens because export refuses worksheet array spill caches. Scalar SUM(SEQUENCE(...)) does not count as spill placement.
Red-flag harness receipt: `receipts/canonical-operating-scenario/red-flag-roundtrip.json`.

## Desktop command

```
python3 tools/canonical_excel_receipt.py --fixture fixtures/operating-scenario.workbook.json --output-dir receipts/canonical-operating-scenario-excel --excel
```

Headless agent composition waits until an Excel Desktop receipt exists.
