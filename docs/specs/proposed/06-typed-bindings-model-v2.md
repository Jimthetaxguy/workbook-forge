---
type: technical-specification
status: proposed — needs James's approval before any code
base: main c84ec07
summary: Proposal for model/schema v2 bindings with value type, required flag and constraints, plus a v1→v2 migrator. Nothing here may be implemented until James approves the version bump.
---

# 06 — Typed bindings (model v2) — **James's decision**

## Problem

Version 1 `bindings.inputs` / `bindings.outputs` are name-to-cell maps. Spec 1
says a binding's value type, required status and declared constraints are
wanted, are not in v1, and that adding them changes the schema and needs a new
version. The toolkit form already has typed input bindings, so agents get
validation there and not on the canonical path.

## Outcome

An agent setting a canonical input gets `invalid_input` for a wrong type,
missing required value, or out-of-range value, before calculation, in both
engines, with the reason taken from the binding itself.

## Scope (if approved)

- `schemas/workbook-model.v2.schema.json`: each binding becomes
  `{ "cell": "Sheet!A1", "shape": "scalar|row|column|range", "type": "number|text|boolean|date", "required": bool, "constraints": {...} }`.
  product-specifications.md (Spec 2) requires shapes and supported range
  locations as well as types and constraints.
  Proposed constraints: `min`, `max`, `enum`, `max_length`. Outputs get `type`
  only.
- Migrator v1 → v2 in both engines: a v1 binding becomes
  `{cell, type: null, required: false, constraints: {}}`. Readers keep v1
  support through the migrator (Spec 4, item 3).
- Session (spec 05) validates inputs against bindings.
- Changelog row in `docs/specs/model-changelog.md`; update
  `docs/specs/model-versioning.md` ("Version 1 is the only version…").

## Non-goals

- Porting the toolkit's binding schema verbatim (Spec 1: slices must not
  invent another binding schema; there should be one).
- Constraints that need formulas.

## Design notes

- Decide whether this is a `schema_version` bump, a `model_version` bump, or
  both. Per model-versioning.md, a new required wire field is `schema_version`;
  new validation meaning is `model_version`. Likely both go to 2.
- Writers must still refuse versions they do not write.
- Intake (`intake.py`) emits untyped v2 bindings; it never guesses types from
  labels or colours (compiler-pipeline stage 4).

## Schema / model-version impact

Breaking: new schema file, `schema_version` 2 and/or `model_version` 2, a
migrator, and a changelog entry. **Do not bump without James's approval.**

## Acceptance tests (only after approval)

```python
# python/tests/test_canonical_model.py
def test_v1_document_migrates_to_v2_with_untyped_bindings(): ...
def test_v2_round_trip_preserves_binding_type_required_constraints(): ...
def test_writer_refuses_version_it_does_not_write(): ...
# python/tests/test_canonical_session.py
def test_wrong_type_input_is_invalid_input(): ...
def test_missing_required_input_is_invalid_input(): ...
def test_value_below_min_is_invalid_input_naming_constraint(): ...
```

```rust
#[test] fn v1_fixture_migrates_to_v2() {}
#[test] fn v2_fixture_round_trips_byte_stable() {}
#[test] fn constraint_violation_matches_python_code() {}
```

## Dependencies

05 (validation runs in the session). Should land before 07 if 07 is meant to
validate inputs.

## Open questions for James

- **Decision:** approve model v2 for typed bindings? Which version field(s)?
- Default for `required`: the primitives layer treats every declared input
  as required and refuses extras. Match that, or say why not.
- **Decision:** the constraint list (min, max, enum, max_length) — add or cut?
