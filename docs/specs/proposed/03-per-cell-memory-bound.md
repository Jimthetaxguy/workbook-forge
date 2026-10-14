---
type: technical-specification
status: proposed
base: main c84ec07
summary: Add a documented per-cell and per-workbook memory budget to intake, hydration and calculation in both engines, refused with resource_limit.
---

# 03 — Per-cell memory bound

## Problem

Formula length (8,192 UTF-16 units), nesting (64 calls, 96 parentheses),
wildcard steps (5,000,000) and JSON depth (128 containers) are bounded. The
memory a single cell can hold is not. CONTEXT.md lists the per-cell memory
bound as a deferred review finding. A crafted workbook with a huge shared
string, a huge text result or a large `ArrayValue` can exhaust memory before
any existing limit fires.

## Outcome

An agent feeding an untrusted `.xlsx` or canonical JSON gets a typed
`resource_limit` refusal, with the cell location, instead of an out-of-memory
crash. Both engines refuse at the same boundary.

## Scope

- Limits (proposed values, local safety profiles, not Excel limits unless
  Microsoft documents them):
  - text value: 32,767 UTF-16 code units, the unit the primitives layer
    already uses (primitives.md), not characters;
  - `ArrayValue` result: a cell-count cap. The primitives layer already caps
    100,000 cumulative value elements and 1 MiB of JSON payload; reuse those
    numbers or state why the canonical path differs;
  - workbook-wide expanded cell budget at intake.
- Check points: intake (`python/workbook_forge/intake.py`, `rust/src/xlsx.rs`),
  canonical hydration (`model.py`, `rust/src/model.rs`), and calculation result
  construction (`python_engine.py`, `expressions.py`, Rust evaluator).

## Non-goals

- Process-level memory accounting or allocators.
- Changing the existing limits.

## Design

- One constants table per engine, cited in `docs/behavior-profiles.md` next to
  the existing evaluator resource limits, marked as Workbook Forge profiles
  where Excel has no documented number.
- Refusal: `resource_limit` with `sheet`, `address`, the limit name and the
  observed size. On the CLI this is the existing one-object stderr error with
  `code`. In the JSON-lines agent it is the existing `resource_limit` code.
- Check before allocating where the size is known (shared-string length from
  XML text length; array shape before materialising).

## Schema / model-version impact

None to the shape. Documents that used to load and now exceed a limit will be
refused. That is a behaviour change: note it in `docs/specs/model-changelog.md`
as non-breaking for the wire shape, and ask James whether it needs a
`model_version` bump (it does not change meaning of documents that load).

## Acceptance tests

```python
# python/tests/test_limit_boundaries.py
def test_text_cell_at_32767_utf16_units_loads(): ...  # include astral chars
def test_text_cell_at_32768_utf16_units_refused_resource_limit_with_location(): ...
def test_array_result_over_element_cap_refused_before_materialising(): ...
def test_intake_shared_string_over_limit_refused(tmp_path): ...
```

```rust
// rust/tests/limit_boundaries.rs
#[test] fn text_cell_at_limit_loads() {}
#[test] fn text_cell_over_limit_is_resource_limit() {}
#[test] fn array_result_over_element_cap_is_resource_limit() {}
```

Plus a shared boundary fixture in `fixtures/` read by both suites.

## Dependencies

None. Small and independent; good warm-up work.

## Open question for James

- Confirm the array element cap and the workbook-wide budget numbers. They are
  proposals.
