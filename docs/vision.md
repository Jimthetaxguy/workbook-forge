# Workbook Forge — North Star vision

**One sentence.** Workbook Forge is an Excel compiler that makes spreadsheet
logic portable: a structured model, Python and Rust code, and editable Excel
again — so business logic can leave the grid without losing its shape.

## What we are building

Excel holds real work: tax workpapers, forecasts, ops models. That work is hard
to reuse outside Excel. Agents and apps need the same structure and calculation
as software building blocks, then a clean path back into a workbook people can
open and edit.

Workbook Forge is that compiler. Under the hood it is modular. Outside it is
one useful install-and-run path for agents and developers.

## Three directions

1. **Excel → model.** Read a real workbook into one canonical, typed structure:
   cell identities, formulas, imported cached values, layout, cross-sheet
   links, and source evidence. A readable Markdown overview helps humans scan;
   the coordinate-preserving cell map is the source of record. The two views
   cross-check with evidence. Markdown is never the SoR, and unsupported bits
   are surfaced rather than guessed away.
2. **Model → software.** Bind that same model into Python, Rust, agent tools,
   and applications so formulas and structure become reusable building blocks
   instead of logic trapped in sheets.
3. **Model → Excel.** Emit an editable workbook again, with round-trip evidence
   that what left the model still opens and calculates in Excel.

## Typed composable pieces, no adapters

Core types (`Cell`, `Formula`, `Sheet`, `Workbook`, and the calc/export surfaces
that grow from them) are the single source of truth. Every stage — intake,
calc-binding, export, agent-headless — imports those types directly. No
translation layers between stages. No parallel “DTO” that drifts from the model.

## Headless agent path

Agents get a bounded, headless CLI and SDK surface: open a workbook, inspect the
model, run supported calculation, write Excel back. The agent does not need a
GUI. Contracts stay explicit; unsupported work is reported, not silently
skipped.

## Candidate formula recovery

When formulas are present, read and trace them. When a sheet is hard-coded
numbers, labels, and layout only, Forge may propose **candidate** formulas in a
separate copy, with uncertainty marked. Candidates never claim to be the
original author formulas. Recovery is optional and evidence-backed.

## Intake as one pass with many observers

Intake is one walk of the workbook. Alongside the core cell map, optional
**observers** run in parallel and do not own the pass:

- metadata probes
- pattern detection
- semantic tagging
- outlier analysis

Observers feed downstream routing (what to bind, what to flag, what to leave
alone). They are plugins. A slow or failing probe never blocks the core intake
result.

## Diffusion over linear streaming

Large workbooks are partitioned into independent regions where the dependency
graph allows it. Workers process regions in parallel and merge. Prefer this
diffusion shape over a single linear stream through every cell when regions do
not depend on each other.

## Repeated-formula collapse

Identical or template-shaped formulas across a range collapse to a template plus
a range (or run) before the graph materializes one node per cell. Graph builders
detect runs early so later stages stay cheap.

## Resilience

No observer blocks intake. Detectors are plugins with timeout and fallback.
Core types and the cell map still land when enrichment fails. Failures show up
in diagnostics; they do not erase the model.

## Shelf split (standing)

- **workbook-forge** — compute, typed model, conservative OOXML in and out.
- **cell-store** — sealed cell event log and sync. Join later at a defined edge.
- No fourth tree for the same job.

The shared model contract lives in Workbook Forge. Python and Rust implement it
independently and share meaning and fixtures; neither backend calls through the
other. The model represents workbook meaning for calculation and translation.
The sealed cell event log records immutable cell events over time. They are
separate responsibilities; a later `FORGE_EDGE` may connect them without
creating another model store or repository.

## How this shapes our work

Each vision point is a build rule. If a change fights a rule, stop and rename
the goal.

| Vision point | Build rule |
| --- | --- |
| Typed composable pieces, no adapters | Calc-binding, export, and agent-headless import `workbook_forge.model` (and later shared calc types) directly. Do not invent parallel DTOs or adapter modules between slices. |
| Excel → model is SoR | Intake writes the typed `Workbook`. Markdown overview is a derived scan layer with evidence links into the cell map. |
| Model → code | Calc-binding binds one supported formula path through Python and Rust against the same model types, with shared fixtures. |
| Model → Excel | Export round-trips the model to `.xlsx` and proves open + calculate with evidence; no “export-only” schema. |
| Headless agent path | Agent-headless exposes CLI/SDK ops over intake → model → calc → export; no GUI requirement in the happy path. |
| Candidate formula recovery | Hard-coded recovery writes candidates into a separate workbook or layer, tagged uncertain; never overwrite source formulas or claim recovery of originals. |
| Intake: one pass, many observers | Core intake returns the model even if every observer is off. Observers register as plugins; results attach as optional annotations. |
| Intake never blocks on metadata | Each probe has a timeout and a fallback (skip + diagnostic). A hung probe cannot stall `intake_workbook`. |
| Diffusion over linear streaming | Partition independent regions; run parallel workers; merge. Do not require full-sheet serial walks when the graph allows splits. |
| Repeated-formula collapse | Graph builders detect template + range runs **before** materializing per-cell nodes. |
| Resilience / plugin detectors | Detectors live behind a plugin interface; failure is local; diagnostics collect; core model remains valid. |
| Shelf split | Forge owns compute + OOXML; cell-store owns the sealed log. Do not grow a third product tree for the same responsibilities. |


## Red-flag specs

Call red flags are locked as build contracts in [docs/specs/red-flags.md](specs/red-flags.md): canonical intermediate form, behavioral parity for export, intake detector isolation, and model versioning from day one.

## Near-term order

1. Bind one existing calculation to explicit worksheet cells and run that same
   definition through both backends (`impl/v1-calc-binding`).
2. Complete the Excel open, edit, recalculate, save and reimport proof for that
   bound calculation (`impl/v1-export`). Package generation and SDK-only
   reimport are useful checks, but do not satisfy this proof.
3. Expand workbook intake using gaps and useful evidence exposed by the bound
   roundtrip (`impl/v1-intake`). The existing reader and cell map are the
   starting foundation; broader intake is the next product expansion.
4. Put the proven path behind the pre-wired headless agent surface
   (`impl/v1-agent-headless`), with CLI/SDK use and no GUI requirement.

Research and fixture design for intake can proceed alongside the first two
steps. Implementation priority stays: one calculation through both backends,
Excel round-trip evidence, then broader intake. Expand observers, diffusion,
and candidate recovery after that spine is green.
