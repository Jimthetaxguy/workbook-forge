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

## Typed composable pieces, one model contract

The canonical model contract is the single source of truth. Each language
implements it with native typed structures. Intake, calc-binding, export and
agent tools use that contract directly. Thin transport or process adapters may
connect package and tool boundaries, but must not invent competing workbook
meaning or a parallel model that drifts from the contract.

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
| One typed model contract | Canonical JSON bytes are the source of truth. Python and Rust hydrate the same versioned workbook meaning independently and calculate against those native model types directly. Thin transport adapters may connect calls; no slice invents a competing workbook model or translation-only DTO. |
| Excel → model is SoR | Intake writes the typed `Workbook`. Markdown overview is a derived scan layer with evidence links into the cell map. |
| Model → code | Calc-binding binds one supported formula path through Python and Rust against the same model types, with shared fixtures. |
| Model → Excel | Export round-trips the model to `.xlsx` and proves open + calculate with evidence; no “export-only” schema. |
| Headless agent path | The Python CLI exposes the proven intake → model → calc → export path first, with no GUI requirement. Expose Rust to agents when the native Rust API offers the same operations. |
| Candidate formula recovery | Hard-coded recovery writes candidates into a separate workbook or layer, tagged uncertain; never overwrite source formulas or claim recovery of originals. |
| Intake: one pass, many observers | Core intake returns the model even if every observer is off. Observers register as plugins; results attach as optional annotations. |
| Intake never blocks on metadata | Each probe has a timeout and a fallback (skip + diagnostic). A hung probe cannot stall `intake_workbook`. |
| Diffusion over linear streaming | Partition independent regions; run parallel workers; merge. Do not require full-sheet serial walks when the graph allows splits. |
| Repeated-formula collapse | Graph builders detect template + range runs **before** materializing per-cell nodes. |
| Resilience / plugin detectors | Detectors live behind a plugin interface; failure is local; diagnostics collect; core model remains valid. |
| Shelf split | Forge owns compute + OOXML; cell-store owns the sealed log. Do not grow a third product tree for the same responsibilities. |


## Red-flag specs

Call red flags are locked as build contracts in [docs/specs/red-flags.md](specs/red-flags.md): canonical intermediate form, behavioral parity for export, intake detector isolation, and model versioning from day one.

## Near-term order: reduce risk in the compiler spine

1. **Canonical schema and `model_version` (`impl/v1-intake`, Specs 1 and 4).**
   The typed Python intake model and CLI already exist on that branch. Finish
   the canonical JSON Schema for `Cell`, `Formula`, `Sheet` and `Workbook`, add
   explicit named input/output bindings, `schema_version` and `model_version`,
   and prove Python and Rust can hydrate the same JSON fixture bytes and
   round-trip them without semantic drift. Keep the schema under `schemas/` and
   golden workbook fixtures under `tests/fixtures/canonical/`. This contract
   work can run while the calculation scenario is being prepared.
2. **One bound calculation (`impl/v1-calc-binding`).** Choose a trusted SDK
   calculation, bind its named inputs and outputs to explicit worksheet cells,
   then run the same canonical JSON document through Python and Rust with
   shared golden fixtures. Both sessions calculate directly against the
   schema-hydrated native workbook types; no adapter DTO or separately authored
   calculation.
3. **Export behavioral parity (`impl/v1-export`, Spec 2).** Write the bound
   model to `.xlsx`, open it in Excel or the documented oracle, edit an input,
   force full recalculation, save and reimport. Diff formulas and behavior as
   well as values. Include the required volatile, iterative, supported-array
   and known-quirk cases. Matching cached XML values or SDK-only reimport is
   not a pass for this gate.
4. **Headless path (`impl/v1-agent-headless`).** Put the proven intake →
   versioned model → calculation → export path behind the Python CLI/SDK first,
   without requiring a GUI. Include `model_version` in summaries. Expose Rust
   to agents once its native API supports the same operations. Keep this
   surface thin until steps 1–3 work.
5. **Broader intake (`impl/v1-intake`).** Use the roundtrip mismatches to decide
   which cells, relationships and unsupported features the cell map must
   capture next. Only after the spine is green, expand observers, diffusion,
   repeated-formula collapse and candidate formula recovery.

## v1 spine done

Call the v1 product spine complete only when all of these are evidenced:

- A serialized workbook model carries `schema_version` and `model_version`, and
  both language implementations hydrate it into their native typed structures.
- One explicitly bound calculation produces the same expected results in
  Python and Rust using shared fixtures.
- Its exported workbook survives open, edit, full recalculation, save and
  reimport, with a structured behavior/formula/value diff and recorded Excel or
  oracle version and settings.
- The Python headless CLI can run that path and prints the model version; Rust
  agent operations are exposed when the same native operations exist there.
- No red-flag spec has an open waiver.

Until those gates are met, keep impl branches separate from main. Do not spend
the critical path on a fourth tree, duplicate model store, adapter DTOs,
broad formula-family expansion, or detector tuning against a single golden
workbook. Detectors remain optional plugins with an off-switch and diverse
fixtures (Spec 3). The standing shelf boundary remains: Forge owns compute and
conservative OOXML; cell-store owns the sealed event log; connect them later
through `FORGE_EDGE` when that edge is real.
