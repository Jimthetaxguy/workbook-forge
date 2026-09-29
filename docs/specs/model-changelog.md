# Workbook Model Changelog

This log versions the meaning carried by serialized Workbook Forge models.
`schema_version` identifies the JSON shape; `model_version` identifies the
workbook model contract. Both are integer sequences and both are required.

| model_version | schema_version | Date | Change | Compatibility and migration |
| ---: | ---: | --- | --- | --- |
| 1 | 1 | 2026-09-28 | Initial canonical model for workbook metadata, named input/output bindings with explicit cells and constraints, ordered sheets, sparse cell maps, authored literal values, formulas, dependencies, optional imported formula-cache values, number formats, and dimensions. | Baseline version; no earlier serialized model exists. Formula cells use `value: null`; an observed non-blank source cache is stored only as `formula.cached_value`. Missing cache means no imported result was available. These caches are not engine calculations. Readers reject missing or unsupported version fields and unknown fields explicitly. |

## Reader policy

- Writers emit model version 1 using schema version 1.
- A future version change must add a changelog row and retain a reader or
  migrator for every prior version that remains supported.
- A reader that cannot interpret a version must report the received and
  supported versions. It must not default a missing version or silently drop
  fields.
