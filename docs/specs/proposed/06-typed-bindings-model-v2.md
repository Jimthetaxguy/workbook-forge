---
type: technical-specification
status: proposed — approved 2026-11-09 under decision G-A with the amended binding shape (spec 09 section 3); code lands in spec 09 steps 2 and 6
base: main c84ec07
summary: Proposal for model/schema v2 bindings with value type, required flag and constraints, plus a v1→v2 migrator. James approved the version bump on 2026-11-09 under decision G-A, with the binding shape amended as spec 09 section 3 records; implementation is spec 09 steps 2 and 6.
---

# 06 — Typed bindings (model v2) — **decided 2026-11-09 under G-A**

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

## Scope (approved 2026-11-09 under G-A)

- `schemas/workbook-model.v2.schema.json`: each input binding is the toolkit's
  `{ "sheet", "address", "kind": "number|text|boolean" or absent, "required": bool (default true), "min", "max", "choices" }`,
  as decision G-A amended it (spec 09 section 3). An absent `kind` means untyped: any
  scalar is accepted; the formula-cell and blocked-cell refusals still apply.
  `choices` (1 to 32) is this proposal's `enum`. Not added: `date` (neither engine
  has a date value type), `max_length` (no consumer), and `shape`, which
  product-specifications.md (Spec 2) requires and which is deferred to the version
  that carries the result-range contract (spec 14), where a range binding gets its
  meaning. Outputs stay `{sheet, address}`; an output's type is observable in the
  report. The original proposal, kept for the record:
  `{ "cell": "Sheet!A1", "shape": "scalar|row|column|range", "type": "number|text|boolean|date", "required": bool, "constraints": {...} }`.
- Migrator v1 → v2 in both engines: a v1 binding becomes
  `{sheet, address, required: false}` with no `kind`, which is this proposal's
  `type: null, required: false` rule in the toolkit's vocabulary. `required: false`
  is a constant rule, not "true unless the bound cell is blank", so two identical v1
  documents migrate identically; an author wanting the primitives-layer rule sets
  `required: true` per binding. The rule lives in spec 09 step 0's `to_toolkit`,
  which becomes the step 6 migrator: a binding without a caller-supplied kind is
  carried untyped with `required: false` instead of dropped, while a caller-supplied
  kind keeps the default `required: true`, because supplying a kind is authoring.
  Readers keep v1 support through the migrator (Spec 4, item 3).
- Session (spec 05) validates inputs against bindings.
- Changelog row in `docs/specs/model-changelog.md` with two lineage lines, one for
  each v1 shape v2 absorbs; update `docs/specs/model-versioning.md` ("Version 1 is
  the only version…"). Both at spec 09 step 6.

## Non-goals

- Retired on 2026-11-09 by G-A: the non-goal against porting the toolkit's binding
  schema. Red-flag Spec 1 asks for one binding schema; Base B satisfies it by
  adoption, and the optional `kind` above is the one change to the toolkit's shape.
- Constraints that need formulas.

## Design notes

- Decided under G-A: both `schema_version` and `model_version` go to 2. Per
  model-versioning.md, a new required wire field is `schema_version`; new validation
  meaning is `model_version`.
- Writers must still refuse versions they do not write.
- Intake (`intake.py`) emits untyped v2 bindings; it never guesses types from
  labels or colours (compiler-pipeline stage 4).

## Schema / model-version impact

Breaking: new schema file, `schema_version` 2 and `model_version` 2, a
migrator, and a changelog entry. **Approved on 2026-11-09 under G-A; the bump lands
in spec 09's migration (steps 2 and 6), not before.**

## Acceptance tests (spec 09 step 2)

Names kept from the original proposal; under the amended shape `type` reads as `kind`
and `constraints` as `min`, `max` and `choices`.

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

All answered by decision G-A on 2026-11-09 (spec 09 section 3): model v2 approved,
both version fields to 2; `required` defaults to true for v2-written bindings,
matching the primitives layer, and `false` on migrated v1 bindings, because both
engines validate every binding against its bound cell on every input operation and a
migrated blank cell would otherwise refuse every input; the constraints are `min`,
`max` and `choices` (this proposal's `enum`), with `max_length` cut.
