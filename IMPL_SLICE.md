# v1 calc-binding — canonical model, then calculation

**Branch:** `impl/v1-calc-binding`  
**Base:** `main`

## Order

Downstream branches rebase after this slice merges. Do not invent a separate product tree for the model.

1. **Step 0, on this branch.** Versioned workbook model. Serialized schema is the source of truth ([Spec 1](docs/specs/red-flags.md#spec-1--canonical-intermediate-form), [Spec 4](docs/specs/red-flags.md#spec-4--model-versioning-from-day-one)). See [model versioning](docs/specs/model-versioning.md).
2. **This branch, after Step 0.** Bind the operating scenario (named assumptions → revenue, profit, break-even) to canonical cell identities and calculate it in Python and Rust.
3. **Next:** Excel open / edit / recalc / save / reimport (`impl/v1-export`).
4. **Then:** intake expansion (`impl/v1-intake`).
5. **Then:** agent-headless composition (`impl/v1-agent-headless`).

`impl/v1-export`, `impl/v1-intake`, and `impl/v1-agent-headless` should rebase onto this branch after it merges.

## Step 0

- Schema: `schemas/workbook-model.v1.schema.json`
- Python hydration: `workbook_forge.model` (import the module directly)
- Rust hydration: `workbook_forge::model` from the same JSON bytes. No Python call and no FFI object graph.
- Shared fixture: `fixtures/operating-scenario.workbook.json`

`schema_version` is the wire shape. `model_version` is the semantic interpretation. Missing or unsupported versions are errors.

## Calc-binding

The fixture's `bindings` name canonical cells (`Assumptions!B1`, `Forecast!F2`, and the rest). Both engines hydrate that document, calculate with their own formula evaluators, and keep `schema_version` and `model_version` on the serialized result. There is no adapter DTO and no parallel calculation document. Numeric equality is not enough: dependencies, Excel errors, and unsupported classifications have to match.

## Out of scope here

Excel Desktop export oracle (Spec 2), intake detectors (Spec 3), and agent-headless composition.

## Do not

- Do not rewrite history on `main` or force-push.
- Do not modify Codex session files under `~/.codex`.
- Keep shelf split: Forge owns compute + OOXML; cell-store owns the sealed event log (join later via FORGE_EDGE).

## Vision

Follow [docs/vision.md](docs/vision.md). Shared types and build rules there bind this slice to the others.


## Combined calc tip — 2026-09-28

Branch `agent/combine-calc-best-20260928` keeps the Grok/Cursor PR #2 tip as the
single schema SoT (`schemas/workbook-model.v1.schema.json`) and ports Mac/Codex
`impl/v1-calc-binding` multi-case golden coverage without a second schema path.

Kept from Grok: dual native hydrate, `schema_version`/`model_version`, provenance,
workbook diagnostics, `Formula.result`, empty Python/Rust semantic diff, docs order
(export → intake → agent-headless after this slice).

Taken from Mac: `fixtures/operating-scenario-cases.json` and the shared golden
input-case pytest (baseline / higher-price / price-equals-cost → `#N/A`), plus
Excel dimension maxima on the existing schema.

Dropped from Mac: `schemas/workbook-model-v1.schema.json` (second schema name),
array-of-bindings + constraints DTO, `canonical_calc` session/revision API,
intake bleed-in (`intake.py`, `test_intake.py`), and `tools/verify_canonical_calc.py`
tied to that alternate binding shape. Constraints/session can land later on this
schema if needed; they must not fork the serialized model.
