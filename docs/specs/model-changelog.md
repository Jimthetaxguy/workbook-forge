# Workbook model changelog

`model_version` is the semantic generation. `schema_version` is the wire shape. See [model-versioning.md](model-versioning.md).

## 1 — 2026-09-29

- **Breaking:** no. First version.
- **schema_version:** 1
- **Summary:** Workbook, Sheet, Cell, Formula, error values, `Sheet!A1` dependencies, provenance, semantic cell bindings, and calculation diagnostics.
- **Migration:** none. Readers reject a missing `schema_version` or `model_version` instead of defaulting it. Unsupported versions fail with `unsupported_schema_version` or `unsupported_model_version`.
- **Calculation semantics:** engines recompute dependencies and formula results from formula text. Unsupported functions are classified and not evaluated. Excel errors are values on `Formula.result`. Cycles, parse failures, oversized ranges, and unknown sheets are diagnostics. Dependents of a failed formula are `blocked_dependency` and do not fall back to an imported cache.

- **Defect note (2026-09-30):** three reader and writer defects were fixed without a version bump, since each brought the code to the rule already declared for version 1: a scalar formula whose value is a blank reference now has `Formula.result` 0 in both engines instead of `null`; every writer refuses a `schema_version` or `model_version` other than 1 before producing bytes; and `_xHHHH_` escapes in string parts are decoded on intake and written on export. A document calculated before this note may hold `null` where 0 is now written.
- **Combine note (2026-09-28):** multi-case operating-scenario golden inputs live beside the shared fixture; they do not bump `model_version`. Dimension maxima were added to the v1 schema without a version bump (additive validation only).
