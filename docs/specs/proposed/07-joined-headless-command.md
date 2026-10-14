---
type: technical-specification
status: proposed — gated on 01
base: main c84ec07
summary: One thin headless command that runs intake → model → paired views → calculate → export → receipt, over library operations, after the Excel gate.
---

# 07 — Joined headless command

## Problem

Each stage exists separately (`workbook-forge intake`, `workbook-forge calculate`,
canonical export), but there is no single run that produces a model, views, a
calculation report, an exported `.xlsx` and a receipt from one input
fingerprint. Compiler-pipeline step 5 defines this and says it waits on the
Excel gate.

## Outcome

`workbook-forge run-pipeline plan.xlsx --inputs '{"unit_price":25}' --out-dir out/`
writes `model.json`, `views.md`, `cellmap.json`, `report.json`, `output.xlsx`
and `receipt.json` into a new directory, refuses rather than writing partial
output, and never changes the input file. (The command name is a proposal;
`run` is taken by the toolkit form.)

## Scope

- Python CLI first (`python/workbook_forge/cli.py`), thin over a library
  function `workbook_forge.pipeline.run(...)` in a new `pipeline.py`.
- Uses spec 05's session for inputs, `intake.py`, `views.py`,
  `canonical_xlsx.py`, and the calculation report.
- Print `model_version` in the command summary (vision.md, near-term step 4).
- Receipt: input fingerprint, `schema_version`, `model_version`, engine,
  stage outcomes, diagnostics with locations, output artifacts, semantic diff
  summary, and the Excel evidence level (from 01) as a field, not a claim.
- Rust exposure after the Python path is proven (red-flags build order 5).

## Non-goals

- Making this the default agent flow before the gate closes.
- MCP adapters.

## Design

- `--out-dir` must not exist or must be empty; write to a temp dir and rename
  at the end so a failure leaves nothing behind.
- Unsupported formulas end up in the report as `not_calculated` with reasons;
  export refuses if a required output was not calculated (same rule as the
  agent `export` operation).
- Duplicate keys in `--inputs` are refused like any CLI JSON (current policy,
  pending James's confirmation).

## Schema / model-version impact

None to the workbook model. Adds `schemas/compilation-receipt.v1.schema.json`
(compiler-pipeline: "versioned when the first integrated workflow is
implemented").

## Acceptance tests

```python
# python/tests/test_pipeline.py
def test_pipeline_writes_all_artifacts_from_one_fingerprint(tmp_path): ...
def test_pipeline_never_modifies_input(tmp_path): ...  # sha256 before == after
def test_pipeline_failure_leaves_no_out_dir_contents(tmp_path): ...
def test_pipeline_refuses_nonempty_out_dir(tmp_path): ...
def test_receipt_validates_against_compilation_receipt_schema(tmp_path): ...
def test_receipt_and_views_omit_private_path(tmp_path): ...
def test_unsupported_formula_is_not_calculated_and_export_refused_if_required(tmp_path): ...
def test_reimport_of_output_matches_report_outputs(tmp_path): ...
```

## Dependencies

- 01 (full gate, not only the scalar receipt: product-specifications.md plan
  item 5 depends on the full Excel gate). Hard dependency unless James decides otherwise.
- 05 (session). 06 if inputs should be type-checked.
- 02 only for the later Rust exposure.

## Open questions for James

- **Decision:** does the full Spec 2 gate have to close first, or only the
  scalar receipt? (Same question as 01.)
- **Decision:** confirm duplicate-key refusal (vs last-key-wins) for CLI and
  documents; PR #8 made it the behaviour, pending your OK.
