# Workbook model versioning

The serialized document is the source of truth ([Spec 1](red-flags.md#spec-1--canonical-intermediate-form) and [Spec 4](red-flags.md#spec-4--model-versioning-from-day-one)). Python dataclasses and Rust structs are hydrations of `schemas/workbook-model.v1.schema.json`. They are not a second schema.

| Field | Meaning | Bump when |
| --- | --- | --- |
| `schema_version` | Wire shape: required fields, types, and names a reader must understand | A serialization change readers must understand |
| `model_version` | Semantic interpretation of a document that already fits the wire shape | Meaning, normalization, dependency, diagnostic, or provenance behavior changes |

Both fields are integers. Version 1 is the only version this tree reads or writes. In the canonical model, a missing version is a `missing_required_field` error and any other integer is `unsupported_schema_version` or `unsupported_model_version`. The older toolkit workbook form, read by `run`, `inspect`, `scenario` and `agent` and by `python_engine` and `toolkit.rs`, carries `schema_version` only; there a missing or unsupported version is the typed `schema_version` diagnostic. Neither form has a compatibility window that treats a missing version as v1.

Hydration checks the wire shape and rejects unknown fields. It does not calculate. Calculation is a later step on the hydrated workbook: each engine parses formulas with its own evaluator, writes `Formula.dependencies` and `Formula.result`, and records diagnostics. Imported `Cell.value` caches are not calculation results. A formula cell's calculated meaning is `Formula.result`.

`Formula.dependencies` are canonical `Sheet!A1` identities. Semantic bindings (`bindings.inputs` / `bindings.outputs`) name those same identities. They are not a parallel calculation document.

Provenance `origin` is `authored`, `imported`, or `calculated`. An imported cache is not evidence that Workbook Forge calculated the cell.

Changelog: [model-changelog.md](model-changelog.md).
