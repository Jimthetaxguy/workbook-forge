---
type: technical-reference
status: active
date: 2026-10-22
summary: First-class evidence grades per function and fixture case, Excel observations pinned to receipts, stale-receipt detection, and the opt-in strict export gate.
---

# Evidence grades

Workbook Forge holds four kinds of support evidence. They used to be one
notion in `catalog/formulas.json` (`status`, `semantic_specs`, `coverage`).
They are now separate, and only the last is evidence about Excel.

| Grade | Means | Evidence about Excel? |
| --- | --- | --- |
| `profile` | Workbook Forge declares the rule (`semantic_specs`, [behavior profiles](behavior-profiles.md)). | No |
| `dual_engine` | The independent Python and Rust engines agree on the shared fixtures. | **No.** Two engines agreeing is not evidence against Excel. |
| `expected_value` | A case matches a Microsoft example or independent arithmetic, never either engine. | Indirectly, for that case only |
| `excel_observed` | Excel Desktop produced the value, and a committed receipt pins it. | Yes, while the receipt is current |

## Catalog fields

Every entry in `catalog/formulas.json` `functions` now carries:

- `evidence_grade` (required): one of the four grades.
- `profile_class` (defaults to `unclassified`): `semantic` when a Workbook
  Forge profile rule for the function can change a calculated value,
  `cosmetic` when it cannot, `unclassified` until someone has reviewed it.
- `excel_observation`: required when, and only when, the grade is
  `excel_observed`. Fields: `receipt` and `workbook` (repository-relative
  paths), `receipt_sha256`, `workbook_sha256`, `excel_version`, and
  `event_time` (when Excel ran, ISO 8601 with a zone; not when the receipt was
  read).
- `excel_compatible` (optional): `true` is refused unless the grade is
  `excel_observed`. A `dual_engine` function cannot be marked Excel-compatible.

The schema (`catalog/formula-support.schema.json`), the Python loader
(`workbook_forge.catalog`, through `workbook_forge.evidence`) and the Rust
module (`workbook_forge::evidence`) enforce the same rules independently.

**Current values.** All 115 implemented functions are `conformance-tested` in
both engines, so each is graded `dual_engine` and `unclassified`. None is
`excel_observed`: no Excel receipt for a function family is committed (the
targeted observations in [excel-observations.md](excel-observations.md) have
no committed receipt). `catalog.is_excel_compatible(name)` is therefore false
for every function. Promoting a function to `expected_value` or
`excel_observed`, or classifying it `cosmetic` or `semantic`, is a reviewed
change, not something a tool infers.

The catalog grade is the function's *floor*: its weakest claim across its
cases. A function whose Microsoft example cases match but whose profile cases
have no independent source stays `dual_engine`.

## Per-case grades

A case in `fixtures/formula-cases.jsonl` may carry an optional record:

```json
{"id": "...", "formula": "...", "expected": {},
 "evidence": {"grade": "expected_value"}}
```

An `excel_observed` case also carries `excel_observation` with the fields
above. `workbook_forge.evidence.validate_case_evidence` validates it. No case
carries one yet; cases without it are covered by the dual-engine fixture run
only.

## Excel runs are observations with receipts

`tools/excel_oracle.py` stays the only process that drives Excel. When a run
ends with status `observed` (Excel ran, saved the owned workbook and every
check matched), the receipt now gains an `excel_observation` block:

```json
"excel_observation": {
  "kind": "excel_observed",
  "workbook": "roundtrip.xlsx",
  "workbook_sha256": "<sha256 of the workbook Excel saved>",
  "excel_version": "16.113.4",
  "event_time": "2026-10-14T18:00:03Z"
}
```

`event_time` is taken when the Excel automation returns. It is separate from
`recorded_at_utc`, which is when the harness started the receipt. Receipts
with any other status (`prepared`, `blocked`, `mismatch`, `failed`) get no
block and are refused by `observation_from_receipt` with
`not_an_observation`. The receipt `schema_version` is unchanged; the block is
additive.

`workbook_forge.evidence.observation_from_receipt(receipt, repo_root)` turns an
observed receipt into the catalog record, adding the receipt's own sha256.

## Stale receipts

An observation is pinned to bytes. `check_observation(observation, root)`
returns:

- `current`: the receipt and the workbook still hash to the recorded values;
- `stale`: either file changed since Excel observed it (for example the
  workbook was re-exported);
- `missing`: a file the observation names is gone.

Only a `current` observation counts. A stale one is reported, never silently
used. From the command line:

```bash
python3 tools/excel_oracle.py --check-receipt receipts/<dir>/receipt.json
# {"state": "stale", "status": "observed", "reasons": ["roundtrip.xlsx hash changed from ... to ..."]}
```

The command exits 0 only for `current`.

## Strict export gate (opt-in)

```python
from workbook_forge.canonical_xlsx import export_canonical, EvidenceGateRefused

export_canonical(workbook, "out.xlsx", strict_evidence=True)
```

With `strict_evidence=True`, export refuses before writing anything when a
formula cell calls a function that is:

- `semantic` or `unclassified` and has no `excel_observed` grade
  (`no_excel_observation`);
- `excel_observed` but its receipt is stale or missing
  (`excel_observation_stale`, `excel_observation_missing`);
- not in the implementation catalog at all (`uncatalogued`, for example `NOW`).

The error is `EvidenceGateRefused`, a `CanonicalPackageError` with code
`evidence_insufficient`; its `refusals` list one record per cell and function
(sheet, address, function, grade, profile class, reason). `cosmetic`
functions pass. A formula Forge cannot parse calculates nothing and is
exported as text without a cache, so no profile rule stands behind its value
and it is not refused.

**Why opt-in.** The cosmetic/semantic classification has not been done
(`profile_class` is `unclassified` everywhere), and `unclassified` is treated
like `semantic` because an unreviewed rule may change a value. Turned on by
default, the gate would refuse every workbook that calls a function. The
default (`strict_evidence=False`) keeps the existing export unchanged. Make it
the default once functions are classified and the first function-family
receipts are committed.

## Limits

- Grades are per function, not per profile rule. A function with one semantic
  rule is `semantic` as a whole until rules are graded individually.
- Operators (`+`, `&`, comparisons) carry profile rules too (see the binary
  comparison profile) but are not graded yet; the gate looks at functions only.
- The Rust crate validates the catalog records but does not hash receipts; it
  has no hashing dependency, and staleness is checked by the Python harness that
  writes the receipts. Rust export (`rust/src/xlsx.rs`) has no strict gate yet.
- Files and JSON only. If a cross-function evidence index is ever needed, rebuild
  it from the receipts as derived storage rather than adding a database.
