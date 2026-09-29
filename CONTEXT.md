---
author: Codex
created: 2026-09-24
agent: codex/Codex
date: '2026-09-28T21:38:51-04:00'
type: project-context
task: Build an SDK for Excel in agent-ready formats using independent Python and Rust implementations
status: active
summary: Independent Python and Rust SDKs expose verified workbook workflows and native spreadsheet compositions; full Excel Desktop acceptance remains outstanding.
next_steps:
  - Complete the separate live Excel acceptance gate before integration; native primitives, review fixes and verification are tracked in docs/toolkit-delivery.md.
  - Define explicit bindings and independent transformations from native compositions to workbook expressions; see the proposed milestone in docs/toolkit-delivery.md.
  - Expand the verified agent operation contract against concrete workbook tasks; keep framework/MCP adapters thin and retain source/revision-aware results.
  - Use tools/excel_oracle.py to observe the generated scenario after an input edit and save; diagnose the current Excel automation failures.
  - Use observed Excel results to validate Workbook Forge profiles and update shared fixtures before expanding formula-family coverage.
  - After oracle coverage is reliable, prioritize the next formula slice by documented usage and dependency value.
remaining:
  - Workbook spill projection, general worksheet formula semantics beyond targeted scalar dependency closures, and broad Excel 365 coverage are staged, not complete.
  - "GitHub reports Jimthetaxguy/workbook-forge as public, verified 2026-09-28; historical private-release preparation notes in docs/run-history.md describe their original checkpoints."
open_questions: []
---
# Workbook Forge Context
## Purpose
Build an **SDK for Excel in agent-ready formats**: expose supported workbook data, formulas, dependencies, presentation and explicit business bindings as structured objects that agents can inspect, calculate, compose and deliver back as editable Excel workbooks. Independent Python and Rust implementations own these operations. The formula glossary aims to enumerate Excel 365 syntax and functions; evaluator and workbook capabilities make narrower, explicit claims.

**Workbook Forge** is the product name selected for public release on 2026-09-26. The Python distribution, import package, and Rust core crate are named `workbook_forge`. The canonical checkout tracks GitHub `main`; GitHub visibility is public as of the live 2026-09-28 check. The toolkit implementation is isolated on `agent/codex-workbook-toolkit` until integration.
## Terms
- **Spreadsheet primitive:** an identifiable operation with typed arguments, native implementations, explicit behavior limits and compatibility evidence. It can be used without a workbook.
- **Composition:** a structured calculation combining primitive calls, named inputs and operators while retaining operation identities for inspection. It can power scripts, agents or a later application interface.
- **Semantic profile:** the declared rules for coercion, blanks, errors and result types. An Excel function name identifies a correspondence; it does not prove complete compatibility.
- **XML structural pattern:** an exact namespace-qualified path through an OOXML part; same-named tags inside unrelated extensions do not match worksheet cells.
- **Extraction record:** a bounded read-only observation carrying package part, XML path, worksheet/cell location, original text and interpreted metadata. Formula function status comes from the existing catalogs.
- **Derived shared formula:** an inspection expression copied from a validated shared-group master; it does not enable calculation or editing of grouped cells.
- **Agent-ready format:** a structured representation with explicit types, identities, operations, revisions and diagnostics, preserving the supported workbook meaning needed for an agent task. JSON alone does not establish that contract.
- **Agent tool adapter:** a thin interface mapping tool calls to SDK operations. Python and Rust independently implement nine operations, discovery schemas, focused views and JSON-lines runners; neither adapter owns new calculation semantics. MCP remains future work.
- **Input preview:** a detached calculation of proposed inputs, with before/after outputs and cell changes; it never changes the owned session or writes files.
- **Calculation provenance:** source formulas, references, explicit bindings and model revision attached to a result; imported caches are never evidence that Forge calculated a value.
- **Workbook model:** sparse sheets, authored content, formulas, styles, and explicit input/output bindings, implemented independently in each language and separate from XML.
- **Canonical Workbook Model:** the versioned serialized contract in `schemas/workbook-model-v1.schema.json`, hydrated into `workbook_forge.model.Workbook` in Python and `workbook_forge::model::Workbook` in Rust. Those native types are the direct calc-binding input; do not introduce a competing workbook DTO. The existing toolkit session `WorkbookModel` remains a legacy engine shape until calc-binding retargets or refactors it.
- **Application binding:** a named input or output attached to an explicit cell; not an Excel defined-name expression.
- **Calculation snapshot:** an immutable model revision used by full or incremental calculation; stale work cannot replace current published results.
- **Preservation baseline:** immutable imported package content used to patch supported changes without rereading or rewriting the original file.
- **Python engine:** the hand-written Python expression, validation, graph-calculation and session implementation; default for Python APIs, with no native execution dependency.
- **Native backend:** optional Python interoperability bridge selecting the separately implemented Rust engine through PyO3. It is not the Python implementation.
- **Catalogued:** a formula feature or function has an entry with provenance and compatibility metadata.
- **Parsed:** the engine recognizes the construct and produces a typed representation.
- **Evaluated:** the engine calculates the construct for its documented input domain.
- **Conformance-tested:** shared cases verify the behavior in Python and Rust; separate Excel spot-check evidence is still needed for Microsoft compatibility claims.
- **Unsupported:** the evaluator refuses the operation with a typed reason; it does not approximate silently.
- **Table formula result range:** cells whose calculation logic is stored in a table part's calculated-column or totals-row metadata; workbook calculation refuses dependencies on these cells unless table semantics are explicitly supported.
- **Evaluator profile:** a deterministic implementation choice used where Excel documentation is incomplete or direct Excel evidence is absent; it must not be presented as verified Excel behavior.
- **Evaluator resource limits:** formulas are capped at 8,192 UTF-16 code units and 64 nested function calls, following documented Excel limits. Workbook Forge also caps parenthesis nesting at 96 and wildcard matching at 5,000,000 matching-state steps per evaluation; those two limits are local safety profiles.
- **Golden corpus:** language-neutral JSONL cases containing formula, workbook context, expected result or error, and compatibility profile.
- **ArrayValue:** a bounded, immutable rectangular formula result with explicit row and column shape; it does not place values into worksheet spill cells. Binary operators lift elementwise over equal shapes, with scalar-only broadcasting.
- **FILTER mask:** a one-dimensional row or column include vector whose length must match the selected source axis; two-dimensional masks are rejected by the current evaluator profile.
- **Binary comparison profile:** Binary operator errors propagate left to right, including non-finite numeric literal errors. ASCII text compares without case; if either text value contains non-ASCII characters, comparison is exact. Blank, Boolean, and numeric values compare after finite binary64 coercion; nonfinite values return `#NUM!`; mixed text/numeric equality is false, inequality is true, and relational comparison returns `#VALUE!`. These are Workbook Forge rules, not verified Excel collation or coercion behavior. Adjacent 16-digit literals are parity probes beyond Microsoft’s documented precision, not Excel result oracles.
- **FormulaResult / evaluate_result:** the shape-preserving result contract shared by Python and Rust; the scalar `evaluate` APIs keep a local `#VALUE!` boundary for top-level arrays and ranges.
## Boundaries
- Target: Excel for Microsoft 365 desktop, with availability/version metadata retained.
- Languages: independent hand-written Python and Rust implementations of formula primitives, workbook models, validation, dependency calculation, editing sessions and OOXML adapters. Shared fixtures compare behavior; neither engine calls the other. The optional native bridge is explicit interoperability.
- Primary workbook format: `.xlsx`; other formats require separately audited adapters.
- Third-party code and dependencies: permissive open-source licenses only, with no copyleft, so the project stays enterprise-friendly. The rule covers development and transitive dependencies; `README.md` lists the accepted licenses and `python/tests/test_project_policy.py` enforces them.
- Jev is a development-time reviewer only. It is not linked into runtime, and its results are advisory.
- Macros and external data are never executed or fetched.
## Current State
The [current-state findings](docs/toolkit-delivery.md#current-state-findings) separate delivered capabilities from evidence limits. The `impl/v1-intake` branch carries the first versioned canonical Workbook Model; its schema and shared fixture are under `schemas/` and `tests/fixtures/canonical/`. `intake_workbook(path)` emits canonical JSON bytes; `intake_workbook_model(path)` is the native Python convenience, and the CLI emits canonical JSON by default with `--summary` for a versioned overview. Native named-input compositions and cell-based workbook expressions remain separate authoring interfaces. Calc-binding must refactor or retarget session APIs to calculate against the canonical model directly, not translate it through another workbook DTO.
The native primitive interface adds nine functions and ten binary operators, typed native values, named-input composition and inspectable operation identities without requiring a workbook. Both packages provide the canonical catalog and expression schema. All three PR boundary findings are fixed; final independent review also closed a direct-call size-limit mismatch. Contributor evidence and the outstanding Excel acceptance gate are recorded in `docs/toolkit-delivery.md`.
Runs 1–24 are accepted. Coverage stands at 115 implemented functions, 85 detailed semantic specs, 110 source records, and 1,371 shared fixtures. Run 24 bounds formula size, nesting, and wildcard work. The toolkit adds independently implemented typed authoring, calculation sessions and Excel adapters in Python and Rust, installed examples, and reviewed parallel calculation. Both SDKs now expose nine schema-described agent operations and JSON-lines runners, verified through installed packages and a live agent task. Formula-aware XML extraction adds ten structural patterns, typed references and catalog mappings; the complete suite now passes 923 Python and 88 Rust tests. Three direct formula checks succeeded in Excel 16.113.2; the generated-file roundtrip and full scenario remain unverified because automation failed. The 521-entry source inventory remains broader than implementation coverage; 406 functions remain catalog-only. FILTER, SORT, and UNIQUE return bounded, shape-preserving arrays; worksheet spill projection remains unsupported. SORT/UNIQUE comparison, equality, coercion, and output precision include explicit Workbook Forge profiles. Do not describe the package as Excel-complete.
- The source-linked function inventory contains 521 records; it is a versioned discovery catalog, not an evaluator coverage claim.
- The Python and Rust evaluators implement the same bounded scalar, reference, operator, common-function, conditional-aggregation, and rectangular formula-result slice. Both load `fixtures/formula-cases.jsonl`. The toolkit adds reviewed Serde dependencies and a separate PyO3 bridge; exact license receipts cover both lockfiles.
- `catalog/formulas.json` records per-language status; a function is `conformance-tested` only when shared fixture coverage passes in both engines.
- The Python wheel installs the JSON catalogs under `share/workbook_forge/catalog`; the API resolves either installed data or the source-tree catalog. `catalog/open_source_patterns.json` records only permissively licensed (no copyleft) implementation references, license evidence, and adoption decisions; no third-party source code is copied.
## Where things are
- `docs/primitives.md`: native function calls, inspectable compositions, package discovery and explicit behavior limits.
- `docs/toolkit-delivery.md`: architecture, ownership, milestone checklist, acceptance model and implementation evidence.
- `docs/extraction-patterns.md`: independent XML parsing/extraction contract, limits, formula mappings and source provenance.
- `docs/agent-protocol.md`: the versioned operation, pagination, error, provenance and JSON-lines transport contract; catalog/agent-operations.json owns its discoverable schemas.
- `docs/excel-observations.md`: observation meanings, harness usage and actual Excel evidence.
- `docs/run-history.md`: dated project activity, superseded checkpoint decisions, Runs 1–24 and the pre-loop baseline. Historical counts and repository visibility describe their original checkpoints.
- `docs/behavior-profiles.md`: current behavior and profile notes by function family, plus workbook adapter details.
- `schemas/workbook-model-v1.schema.json` and `docs/specs/model-changelog.md`: canonical model bytes, field schema, and model-version history.
- `.autoresearch/state.json`: the authoritative accepted-run ledger. `.autoresearch/config.json` holds the loop criteria and `eval_command`.
- `_working-files/`: dated checkpoint and review notes.
- `_archive-2026-09-25-L1/`: a git-ignored archive of the pre-git backup copies (`*.bak-*`, `.autoresearch/_archive-*`, `.autoresearch/backups/`), with `MANIFEST.tsv` and `ROLLBACK.sh`.
## Working conventions
- Keep public examples and records portable: use repository-relative paths or documented environment variables, synthetic data, and project-focused decisions. Do not copy home-directory paths, personal conversations, private contact details or local tool credentials into tracked files.
- Preserve license and source attribution. Before publishing a privacy cleanup, inspect reachable Git history and commit metadata as well as current files; an ordinary cleanup commit does not erase earlier versions.
- Checkpoint accepted work with local git commits. The ignore rules exclude `*.bak-*`, `_archive-*/`, and `.autoresearch/backups/`, so ad-hoc backup copies are no longer needed.
- Append each run's summary to `docs/run-history.md`. Keep `README.md` and this file limited to the current state.
- Run the Rust gates with `CARGO_TARGET_DIR` inside the checkout, as `eval_command` does. A shared target directory can mix build artifacts between copies of the crate.
## Latest maintenance
### 2026-09-28 — codex/Codex — canonical JSON as intake output
- Made serialized canonical model bytes the default Python intake result and CLI output; retained an explicit native-model helper and versioned summary mode.
- Calc-binding and downstream callers should hydrate canonical bytes into their native model types before working with them.

### 2026-09-28 — codex/Codex — canonical workbook model v1
- Added the versioned Python/Rust model hydration contract and one shared serialization fixture on `impl/v1-intake`.
- Calc-binding still needs to retarget/refactor the existing session API to calculate from canonical model types directly; that integration is specified but not implemented in this slice.

### 2026-09-28 — codex/Codex — public documentation review
- Reviewed all project guides and historical notes, verified portable examples, and removed unnecessary personal context and machine locations.
- Recorded the contributor checks and separate published-history decision in [toolkit delivery](docs/toolkit-delivery.md#activity).
### 2026-09-28 — codex/Codex — native primitives and reviewed boundaries
- Added reusable calculations without workbooks, preserving the four product uses: extraction, software execution, programmatic Excel generation and a later application interface.
- Recorded independent contributor verification and reference-project analysis in [toolkit delivery](docs/toolkit-delivery.md). Known behavior differences remain explicit.
- Prior branch organization and implementation checkpoints remain in the delivery record and [project history](docs/run-history.md#project-activity).
