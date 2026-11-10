---
type: technical-specification
status: proposed — reframed 2026-11-09 to the owner model under decision G-A (spec 09 section 3)
base: main c84ec07
summary: Add a revisioned calculation session over the owner workbook model in both engines, so set-input/preview/calculate carry model revisions like the agent session already does. Reframed under decision G-A (2026-11-09) from the canonical Workbook to the owner model, which grows from the toolkit shape.
---

# 05 — Revisioned calculation session on the owner model

Reframed on 2026-11-09: decision G-A chose the toolkit shape as the base of the owner
contract (spec 09 section 3), so this session operates on the owner model through spec
09 step 1, starting from the toolkit `Session` machinery rather than a second session
on the canonical path. The problem and the operations below are unchanged; where the
text says "canonical" it names the v1 documents the session must still accept through
spec 09 step 0.

## Problem

The JSON-lines agent ([agent-protocol.md](../../agent-protocol.md)) has
revisions, `expected_revision`, `revision_conflict`, `preview_inputs` and
`set_inputs`, but it runs on the older toolkit workbook form. The canonical
path calculates once and writes a report with no revision history.
CONTEXT.md: "Typed binding constraints and the calculation session with
revisions are not [ported]". The glossary already defines a **calculation
snapshot** (immutable model revision; stale work cannot replace current
results).

## Outcome

An agent can hydrate a canonical workbook, preview an input change, apply it
at an observed revision, calculate, and get a report that names the revision
it came from. A stale apply is refused. This is the session the joined
headless command (07) and future agent operations run on.

## Scope

- Python: a session in `python/workbook_forge/session.py` taking the owner model
  directly (Spec 1: no DTO in between), built from the toolkit `Session` machinery
  (spec 09 step 1); documents of either v1 shape enter through spec 09 step 0.
- Rust: the same session in `rust/src/session.rs`, taking the owner model.
- Operations: `revision`, `preview_inputs(values, expected_revision)`,
  `set_inputs(values, expected_revision)`, `calculate() -> report`.
- Inputs are addressed by `bindings.inputs` names only.

## Non-goals

- Moving the JSON-lines agent onto the canonical form (later, behind the same
  session).
- Durable sessions, audit storage, undo history.
- Typed binding validation (spec 06).

## Design

- Revision is an integer starting at 0, incremented by each successful
  `set_inputs`. `preview_inputs` never mutates and never writes files.
- Calculation runs on one committed snapshot. A failure publishes nothing.
- Error codes reuse the agent protocol's: `revision_conflict`,
  `invalid_input`, `invalid_reference`, `resource_limit`.
- Applied inputs become cell values with provenance `authored`; calculated
  formula results set `Formula.result` and the report marks them `calculated`.
  An imported cache is never promoted.

## Schema / model-version impact

- Workbook model: `revision` leaves the document and becomes session state (spec 09
  section 4; the change set is in spec 09 step 1).
- Calculation report: one `calculation-report.v2` merging the toolkit
  `CalculationReport` and the canonical v1 report, carrying `revision`,
  `base_revision` and the calculation context (spec 09 section 4). Decided with G-A
  on 2026-11-09; the v1 report schema stays readable until spec 09 step 6.

## Acceptance tests

```python
# python/tests/test_canonical_session.py
def test_new_session_starts_at_revision_zero(): ...
def test_preview_does_not_change_revision_or_values(): ...
def test_set_inputs_at_current_revision_increments_and_recalculates(): ...
def test_set_inputs_at_stale_revision_is_revision_conflict(): ...
def test_unknown_binding_name_is_invalid_input(): ...
def test_failed_calculation_publishes_no_partial_result(): ...
def test_report_carries_revision_and_imported_cache_not_promoted(): ...
```

```rust
// rust/tests/canonical_session.rs
#[test] fn preview_is_detached() {}
#[test] fn stale_revision_is_revision_conflict() {}
#[test] fn report_matches_python_on_operating_scenario_after_set_inputs() {}
```

The cross-engine comparison uses `fixtures/operating-scenario.workbook.json`
and a new shared input-change fixture.

## Dependencies

None to start. Spec 06 builds typed validation on top of it; spec 07 uses it.

## Open questions for James

Both answered by decision G-A (spec 09 section 3, 2026-11-09): the session is the
toolkit session over the owner model, not a second session on the canonical path; the
report is `calculation-report.v2`.
