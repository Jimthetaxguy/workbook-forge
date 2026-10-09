---
author: codex/Codex
created: 2026-09-28
agent: codex/Codex
date: '2026-10-01T19:56:55-04:00'
type: product-specification
task: Specify Workbook Forge user outcomes and executable engineering path
status: active-specification
summary: Defines the user outcomes and compiler contract for explainable workbook intake, shared calculations across Python and Rust, Excel output, headless use, formula hypotheses, and evidence-led coverage.
next_steps:
  - Finish one versioned calculation report for the operating scenario in both engines, then record the canonical scalar Excel open, edit, recalculate, save, and reimport cycle.
  - Close the full Excel export gate, including declared result ranges and required volatile, iterative, array/spill, and quirk cases, before joining the proven path behind the headless interface.
  - Prototype the paired overview and structural map in parallel, with privacy-safe source identity, then integrate them into the headless path.
remaining:
  - The compiler pipeline's shared report contract, full Excel acceptance evidence, privacy-safe source handling, and joined headless path are not complete.
  - Full dynamic-array spill, volatile, iterative and Excel-quirk acceptance, broader formula coverage, and hard-coded formula analysis remain evidence-gated.
open_questions:
  - Which real workbook task should drive intake priorities after the synthetic operating scenario?
  - Which OOXML structures must be preserved or cause a safe refusal in the first supported round trip?
  - Which evidence is useful enough to rank formula candidates without suggesting the original formula is known?
---

# Workbook Forge product specifications

This document turns the product direction into user outcomes, engineering
contracts, research questions and acceptance checks. It complements
[`CONTEXT.md`](../CONTEXT.md), which owns stable project vocabulary, and
[`toolkit-delivery.md`](toolkit-delivery.md), which owns implementation evidence
and the current integration milestone. It is a specification of the intended
system, not a claim that every described capability is implemented.

## Product outcome

Workbook Forge should make spreadsheet calculations understandable to software
and reusable outside Excel, while retaining a reliable path back to an editable
Excel workbook. A person should be able to provide a workbook and quickly get a
useful, inspectable account of its contents. An agent should be able to use that
account to answer questions or make bounded, reviewable changes. A developer
should be able to use the same calculation definitions in Python, Rust, or a
future application without reauthoring the calculation separately for every
surface.

The system is modular internally and pre-wired at its entry points. The default
path should work from a command line or agent tool without a graphical
interface. The same underlying library should also be usable from application
code. A spreadsheet-like interface is a possible client of the model, not a
prerequisite for using or validating the core.

The compiler design is shared across directions: Excel intake and supported
authored calculations become the same versioned workbook model; Python, Rust,
agents and applications use that model through stable operations; and the OOXML
writer emits editable Excel from it. “Code to code” means one supported,
structured calculation can run on different backends. It does not mean
arbitrary Python or Rust programs can be transpiled. The engineering stages,
evidence and implementation gaps are specified in
[`docs/specs/compiler-pipeline.md`](specs/compiler-pipeline.md).

## Shared requirements

These requirements apply to every specification below:

1. **Keep evidence attached.** Every extracted or calculated fact should point
   to its source: workbook, sheet, cell or package part, formula text, model
   revision, or explicit author binding, as applicable.
2. **Keep observation separate from interpretation.** A stored formula is an
   observation. A named role such as `revenue` is an explicit binding or an
   interpretation. A suggested formula for hard-coded values is a hypothesis.
   Reports must label these differently.
3. **Make partial understanding visible.** Report what was read, preserved,
   calculated, skipped or refused, with a reason. Do not silently flatten,
   approximate or drop unsupported workbook meaning.
4. **Keep calculation behavior explicit.** Python and Rust remain independently
   implemented. Shared schemas and fixtures compare their behavior; neither
   backend is treated as a wrapper around the other.
5. **Use examples as evidence, not as product ingredients by default.** Study
   outside tools for specific useful patterns. Adopt a dependency or copy an
   implementation only when a concrete requirement, license review and
   maintenance case support it.
6. **Treat Excel compatibility as an observed claim.** A generated ZIP package,
   matching Python/Rust output or successful reimport does not alone establish
   Excel Desktop behavior. Record the Excel version, action sequence and
   observed result for each direct compatibility claim.
7. **Route supported translations through the canonical model.** Excel input,
   structured authored calculations and every runtime or file target share one
   versioned meaning. Direct one-off converters must not create parallel
   semantics.
8. **Keep machine-local paths out of shared artifacts by default.** The
   resolved input path may exist in per-run context while a file is open, but
   canonical JSON omits it, and summaries, Markdown, agent responses and
   receipts use a content fingerprint and source coordinates. Older v1 model
   documents containing a path are redacted before they are returned to an
   agent. A safe display label is opt-in; the absolute path is not emitted.

## Compiler framing — one model, multiple routes

Workbook Forge is not a collection of pairwise translators. The canonical
workbook model is the intermediate description all supported routes share.
Excel and structured calculations are front ends; Python and Rust are
independent runtime targets; the OOXML writer is the Excel output target; and
Markdown is a derived context view. Agent operations and application code call
the same library behavior rather than reimplementing workbook semantics.

The detailed stage contracts, diagnostics and evidence requirements, paired
view loop, research questions, and execution order live in
[`docs/specs/compiler-pipeline.md`](specs/compiler-pipeline.md). This document
continues to define user outcomes and acceptance criteria.

## Product spec 1 — Fast, explainable workbook intake

### User outcome

Given an `.xlsx` file, an agent can quickly answer: what sheets and meaningful
regions appear to be present, where are formulas, which cells depend on which
inputs, which parts look like assumptions or outputs, and what could not be
understood? The agent can then inspect a narrow sheet, range or dependency path
without rereading an unbounded workbook dump.

### Required behavior

- Produce a compact first-pass inventory: workbook and sheet names, dimensions
  or populated regions, formula/value counts, hidden or merged areas where
  supported, tables/names/validations where supported, and extraction limits.
- Produce a structural map that retains coordinates and source locations for
  cell values, formulas, cached results, styles and supported workbook metadata.
- Produce a readable preview, initially Markdown or another text form, to give
  an agent quick context. The preview is an aid to navigation, not a replacement
  for the structural map or calculation source of truth.
- Allow targeted follow-up views for a sheet, rectangular range, formula, named
  output or dependency closure. Bound response sizes and provide continuation
  information when a view is paged.
- Make relationships discoverable in both directions: from an output to its
  formula inputs, and from an input or region to formulas that use it.
- Preserve the distinction between a formula's current expression, a cached
  value imported from Excel and a value calculated by Workbook Forge.
- Include unsupported-content and truncation diagnostics in the result so an
  agent can avoid presenting an incomplete view as complete.

### Paired-view interpretation loop

The readable preview and structural map should inform one another through
explicit, repeatable checks:

1. Generate the preview and the structural map from the same workbook revision.
2. Use the preview to identify possible semantic regions, such as assumptions,
   outputs, periods or units, even when the labels are incomplete.
3. Check those regions against coordinates, formulas, styles, tables, names and
   dependency links in the structural map.
4. When a structural pattern reveals a likely relationship the preview hides
   (for example, an unlabeled block feeding a forecast), add a sourced note or
   request a focused preview of that region.
5. Record the evidence and confidence of interpretations. Keep raw workbook
   facts available so a person or agent can disagree with the interpretation.

This loop is a proposal for progressively richer intake. It must not mutate the
source workbook or let a generated narrative overwrite the extracted facts.

### Acceptance checks

- A synthetic workbook with separated assumptions, calculations and outputs
  yields a quick inventory, a readable preview and a coordinate-preserving map.
- A formula-driven input block can be traced to dependent outputs even when its
  heading is missing or misleading; the report points to the exact evidence.
- The preview and map can be cross-checked without losing sheet/cell identity.
- A deliberately unsupported or oversized workbook produces a clear bounded
  refusal or completeness warning, not a success-shaped partial result.
- A focused request returns the relevant region and dependency evidence without
  requiring the agent to load every cell into its prompt.

### Research to do

- Compare how selected spreadsheet and document tools create fast previews,
  extract workbook structure and expose partial results. Record the exact
  pattern being studied, the source version/license and why it may or may not fit.
- Determine which OOXML parts carry useful layout and semantic clues beyond the
  existing ten extraction patterns, using small synthetic examples first.
- Explore compact workbook summaries that retain disconnected regions and
  whitespace boundaries. A simple left-to-right table conversion can falsely
  combine adjacent but semantically separate blocks.
- Establish measurable intake budgets: archive and XML limits already exist;
  measure report size and useful first-response context on representative
  synthetic workbooks before setting additional performance targets.

## Product spec 2 — One calculation across workbook, Python and Rust

### User outcome

A developer defines a calculation once, binds its named inputs and outputs to
workbook cells, runs it from Python or Rust, and emits an editable workbook. An
agent can inspect and change the same bound inputs through the headless
interface. The result includes enough provenance to explain how it was
produced.

### Required behavior

- Define an explicit versioned binding document connecting named inputs and
  outputs to sheet/cell or supported range locations, types, shapes and
  constraints. Do not infer these bindings silently from color, position or
  label text.
- Define a supported transformation between native compositions and workbook
  expressions. Keep operation identities and a mapping between named values and
  cell references.
- Implement the transformation independently in Python and Rust against the
  same versioned contract. Report unsupported operations and incompatible
  bindings before changing a workbook model.
- Preserve workbook source formulas, values, styles and unsupported content
  according to the existing adapter's conservative edit rules.
- Keep revisions and expected-revision checks on mutations. A failed bind,
  calculation or export must not leave a partially changed model or output.
- Allow each backend to be consumed as a library or invoked through its existing
  command/agent operation boundary. An application interface may sit above this
  contract later.

### First executable slice

Use the existing synthetic revenue/profit scenario. Bind `volumes` and
`unit_price` as inputs and revenue, profit and break-even as outputs, using
explicit worksheet locations. Run the same authored calculation in both
engines. Generate a workbook, edit `unit_price`, recalculate, export, and
reimport. Compare the resulting values, formula definitions, bindings and
supported structure against independently calculated expectations.

This slice connects existing primitives; it does not claim arbitrary source-code
translation. A native composition can be translated to a supported workbook
expression because both have a declared representation. Translating arbitrary
Python or Rust programs requires a separate language and safety design and is
not implied by this product contract.

### Acceptance checks

- Missing, duplicate, wrong-sheet, wrong-shape and wrong-type bindings are
  rejected with actionable locations and no partial mutation.
- One versioned definition yields the expected scenario outputs in independent
  Python and Rust executions.
- The exported workbook retains the declared bindings and supported formulas;
  reimport makes the same inputs and outputs addressable.
- A changed input produces the expected before/after result and a trace to the
  formula and source cells.
- Unsupported formula behavior is refused before export or called out as
  preserved-but-not-calculated; it is never silently replaced with an
  approximation.
- The same scenario is opened in Excel Desktop, edited, recalculated, saved and
  reimported. Record the Excel build, calculation settings, exact edits and
  observed values. Keep this evidence distinct from SDK-only checks.

### Research to do

- Decide how the binding format represents scalar, row, column and rectangular
  inputs/outputs, and how it evolves without changing calculation meaning.
- Identify OOXML and Excel behaviors that can invalidate a seemingly successful
  roundtrip: cached values, calculation-chain handling, formula prefixes,
  tables, defined names, validations, arrays and dynamic spill placement.
- Use focused Excel experiments to establish behavior rather than treating
  another engine's behavior as the compatibility oracle.
- Compare calculation/dependency architecture patterns in other repositories
  only where they answer a defined design question, such as bounded dependency
  closure, cached-result handling or separation of formula meaning from
  workbook layout.

## Product spec 3 — Headless, pre-wired agent and application use

### User outcome

An agent can install or invoke Workbook Forge with a workbook and a task, discover
what operations are available, gather a small relevant context, propose or apply
a bounded change, explain its result and export a workbook. A developer can
instead call the same operations from application code without adopting an
agent framework or building a user interface first.

### Required behavior

- Keep a convenient default entry path: install, point to a workbook, inspect or
  run a named workflow, and receive structured output with clear diagnostics.
- Keep the SDK usable as ordinary Python and Rust libraries. CLI, JSON-lines,
  agent-framework and future MCP adapters map to shared operation contracts;
  adapters do not create separate calculation semantics.
- Expose operation discovery, descriptions, limits, side effects, provenance,
  revision checks and stable typed errors.
- Constrain file access to caller-provided paths and exports to explicit output
  locations. Preview is non-mutating; edits and exports are explicit.
- Include small installed examples and reusable recipes that teach common
  tasks: profile a workbook, explain an output, compare a proposed input change,
  make a supported edit and export a copy.
- Keep future application shells (including a grid or domain-specific UI) as
  clients of the shared model and operation contracts, not alternative
  calculation engines.

### Acceptance checks

- A fresh package consumer outside the source checkout can discover operations,
  load the example, inspect a dependency path, preview an input and export a
  workbook.
- The same task produces contract-compatible results through Python and Rust
  entry points.
- A host can call the library without an agent framework or launch the
  headless runner without a UI.
- Operation descriptions identify mutating behavior and limits before a caller
  chooses to execute it.
- Example recipes state prerequisites, inputs, expected outputs and known
  unsupported cases.

### Research to do

- Learn which install-and-run defaults reduce setup for agents without hiding
  file selection, side effects or limits.
- Compare tool-adapter patterns in selected agent SDKs while keeping the core
  contract independent of any one framework.
- Validate package ergonomics with a clean Python consumer and a standalone
  Rust consumer, not only commands launched from the repository.
- Defer a GUI choice until real workbook tasks show which operations and
  explanations people need repeatedly.

## Product spec 4 — Recovering likely formulas from hard-coded workbooks

### User outcome

When a workbook contains numbers but no formulas, Workbook Forge can help an
analyst investigate how those numbers might relate. It may suggest candidate
equations, check candidates against stored values and show the evidence. It
cannot generally know which formula the original author used from results
alone.

### Separate modes

**Formula extraction** reports formulas actually stored in the workbook. It
preserves the original formula text, location, parsed references and relevant
calculation evidence. It must never describe an extracted formula as inferred.

**Formula hypothesis generation** operates on hard-coded values. It can use
explicit labels and units, repeated row/column structure, cross-sheet references
or similar number-flow clues to propose candidate relationships. Candidate
formula text must be kept separate from the source workbook until reviewed.

### Required behavior for a future hypothesis tool

- Build a graph of observed numeric cells and metadata relationships, preserving
  the direction and source of every proposed edge.
- Use labels, units, repeated structures, matching totals, period alignment and
  user-provided statements (for example, “A plus B plus C”) as candidate
  constraints rather than proof.
- Generate a bounded set of simple candidate expressions from a documented
  grammar. Report search limits and avoid unbounded symbolic search.
- Evaluate candidates on a separate model or copy. Compare predicted results
  with hard-coded values across more than one relevant case when available.
- Rank candidates using explicit evidence factors and show counterexamples,
  unmatched values, ambiguity and possible extra constants.
- Never overwrite hard-coded values or insert inferred formulas without a
  separate, explicit user action and an auditable diff.
- Say “candidate” or “possible relationship,” not “recovered original formula,”
  unless independent evidence establishes provenance.

### Research before implementation

- Test how often labels, units, number formats and repeated sections identify
  useful constraints on synthetic cases with known ground truth.
- Study reverse data-lineage and spreadsheet error-detection research for
  techniques that narrow a candidate search without pretending to establish
  author intent.
- Measure false-positive rates on deliberately ambiguous workbooks (for
  example, several equations that match the same totals) before designing a
  confidence score.
- Define a useful minimum evidence threshold with analyst feedback; a formula
  that matches one total may be too weak to show as a recommendation.
- Investigate obscured constants as a hypothesis class only when values across
  multiple periods or cases constrain them. A single workbook snapshot often
  cannot distinguish an embedded constant from an input or coincidence.

### Acceptance checks

- A known synthetic workbook with removed formulas yields the planted formula
  among candidates and links it to every supporting label, cell and comparison.
- An ambiguous workbook produces multiple candidates or an explicit low-evidence
  result rather than a confident single answer.
- A deliberately inconsistent workbook identifies failed comparisons and does
  not modify its source cells.
- Formula extraction and hypothesis generation use distinct report fields and
  language so consumers cannot confuse observed formulas with suggestions.

## Product spec 5 — Evidence-led formula and Excel behavior coverage

### Goal

Expand supported formula and workbook behavior to serve demonstrated tasks,
while keeping catalog size, parser recognition, calculation support and Excel
compatibility evidence distinct.

### Prioritization method

For every proposed formula family or workbook feature:

1. Name the user task it unblocks.
2. Identify a source workbook pattern or synthetic case that exercises it.
3. Specify semantics, supported input domain, errors, limits and Python/Rust
   behavior before adding implementation.
4. Add shared cases that distinguish plausible interpretations and failure
   boundaries.
5. Obtain direct Excel observations for claims that depend on Excel behavior.
6. Record whether the feature is catalogued, parsed, evaluated, conformance
   tested, Excel observed or explicitly unsupported.

Prioritize structural formulas and operations that unblock the binding,
inspection and intake flows. Candidate areas already surfaced for investigation
include structured table references and calculated-column formulas,
`SUMPRODUCT`, conditional aggregations with unequal range shapes, and
dynamic-array spill placement. They are research candidates, not automatically
the next implementation batch. The implementation and compatibility gaps for
each are recorded in the delivery evidence and capability catalog.

### Adoption ceiling to state plainly

The project must not imply that a broad source catalog means broad executable
support. In particular, a formula engine returning a shaped array is not the
same as placing those results into neighboring worksheet cells. Existing-cell
edit limits and unsupported spill behavior can block practical workflows even
when a formula name is recognized. Capability descriptions should state these
limits near the feature summary and link to exact status and evidence.

### Research to do

- Review supported workflow examples and workbook structures to rank formula
  gaps by task impact and dependency value.
- Compare Excel Desktop, OOXML, Python and Rust observations for each candidate;
  clearly label which system is the source of each observation.
- Study selected projects for the specific concept under review (for example,
  cached result handling, formula evaluation boundaries or ingestion reports).
  Record what was learned and the decision; do not import a whole tool because
  one pattern was useful.

## Executable plan and dependencies

| Order | Work item | Can start now? | Depends on | Completion evidence |
| --- | --- | --- | --- | --- |
| 1 | Complete the shared calculation result and provenance contract for the operating scenario | Yes | Existing canonical model and explicit bindings | Same canonical bytes produce expected Python and Rust results; report identifies backend, model revision, source cells and diagnostics |
| 2 | Record the canonical scalar Excel open/edit/full-recalc/save/reimport cycle | Yes; the harness exists | 1 and the current export path | Receipt records Excel build, edits, observed values, formula differences and unavailable settings honestly; explicitly marked as the first observation |
| 3 | Close the full Excel export gate, including declared spill placement | After the scalar observation | 2 and a defined result-range contract | Excel receipts cover the declared spill range, volatile, iterative and known-quirk cases with explicit tolerances; unsupported required cases keep the gate open |
| 4 | Prototype the paired overview, structural map and privacy-safe source handling | Yes, alongside 1–3 | Existing canonical intake | Synthetic cases show coordinate links and cross-view checks; serialized model, summary and scan omit a distinctive private input path while retaining fingerprint and cell evidence |
| 5 | Put intake → model → supported calculation → export behind one headless workflow | After 1–4 | Shared result contract, full Excel gate, paired report and privacy acceptance | A caller-selected workbook produces versioned model output, bounded context, diagnostics, calculation evidence and a new `.xlsx` without a GUI |
| 6 | Rank formula and workbook gaps by workflow value | Research can start now | Concrete workbook tasks and observed failures | A task-to-gap matrix ties each candidate to semantic rules, Python/Rust fixtures and Excel evidence needed |
| 7 | Prove Python/Rust application embedding | After the headless operations stabilize | 1 and 5 | Clean consumers call each native library without a UI, duplicated model or agent framework |
| 8 | Research hard-coded formula hypotheses | Research only for now | Paired intake signals and labeled ground-truth corpus | Candidate evidence, ambiguous cases, counterexamples and false-positive results; source workbook remains unchanged |

The first trust path is **shared calculation report → scalar Excel observation
→ full Spec 2 evidence → headless workflow**. Paired intake and formula-gap
research can advance alongside the Excel work, but the default agent path waits
until the workbook behavior is evidenced and its outputs use privacy-safe,
source-linked reports. Application reuse follows the same operation contract.

## What this specification does not claim

- It does not claim complete Excel 365 support or that every catalogued formula
  is executable.
- It does not claim that Python/Rust agreement proves Microsoft Excel parity.
- It does not claim that a readable conversion fully captures workbook meaning.
- It does not claim that hard-coded values reveal the one original formula.
- It does not require combining or depending on the reference projects reviewed.
- It does not specify a full spreadsheet grid, hosted service, durable shared
  database or collaboration system.
- It does not make arbitrary Python or Rust source transpilation part of the
  supported compiler contract.

## Update discipline

Keep this file focused on durable product and behavioral contracts. Add dated
observations, changing counts, run outcomes and task assignments to
[`toolkit-delivery.md`](toolkit-delivery.md) or
[`run-history.md`](run-history.md). When implementation changes a requirement,
update the relevant specification and link the evidence rather than silently
rewriting an aspiration as a delivered capability.
