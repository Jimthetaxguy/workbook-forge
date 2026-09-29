# Workbook model changelog

`model_version` is the semantic generation. `schema_version` is the wire shape. See [model-versioning.md](model-versioning.md).

## 1 — 2026-09-29

- **Breaking:** no. First version.
- **schema_version:** 1
- **Summary:** Workbook, Sheet, Cell, Formula, error values, `Sheet!A1` dependencies, provenance, semantic cell bindings, and calculation diagnostics.
- **Migration:** none. Readers reject a missing `schema_version` or `model_version` instead of defaulting it. Unsupported versions fail with `unsupported_schema_version` or `unsupported_model_version`.
- **Calculation semantics:** engines recompute dependencies and formula results from formula text. Unsupported functions are classified and not evaluated. Excel errors are values on `Formula.result`. Cycles, parse failures, oversized ranges, and unknown sheets are diagnostics. Dependents of a failed formula are `blocked_dependency` and do not fall back to an imported cache.

- **Combine note (2026-09-28):** multi-case operating-scenario golden inputs live beside the shared fixture; they do not bump `model_version`. Dimension maxima were added to the v1 schema without a version bump (additive validation only).
